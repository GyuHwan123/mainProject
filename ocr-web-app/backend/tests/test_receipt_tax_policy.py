"""Run the production pure postprocessors without importing server integrations."""
import ast
from pathlib import Path
import re
import sys
import unittest
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.constants.finance_taxonomy import refine_expense_category
from app.services.finance_normalization import normalize_date

SOURCE = ROOT / 'app/services/finance_receipt_simple.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
FUNCTIONS = {'_receipt_number', '_as_number', '_labeled_amount', '_extract_amount_evidence',
             '_reconcile_amounts', '_simple_validation', '_amount_is_grounded',
             '_normalize_expense_category', '_clean_model_items'}
NAMESPACE = dict(re=re, Any=Any, normalize_date=normalize_date,
                 refine_expense_category=refine_expense_category)
NODES = [node for node in TREE.body if
         isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS or
         isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and
         t.id in {'_MONEY_RE', 'AMOUNT_ROUNDING_TOLERANCE'} for t in node.targets)]
exec(compile(ast.Module(body=NODES, type_ignores=[]), str(SOURCE), 'exec'), NAMESPACE)


class ReceiptTaxPolicyTests(unittest.TestCase):
    def resolve(self, text, total=None):
        result = dict(merchant='테스트', transaction_date='2026-09-05',
                      expense_category='대중교통', total_amount=total,
                      supply_amount=999, tax_amount=99, items=[])
        NAMESPACE['_reconcile_amounts'](result, text)
        result['validation'] = NAMESPACE['_simple_validation'](result, text)
        return result

    def test_required_cases(self):
        cases = [
            ('공급가액 10,001\nVAT 999\n결제금액 11,000', 10001, 999),
            ('도서 단독 구매\n결제금액 12,000', 12000, 0),
            ('도서 면세\n결제금액 12,000', 12000, 0),
            ('개인택시\n결제금액 96,200', 87455, 8745),
            ('KTX\n결제금액 50,800', 46182, 4618),
            ('시외/고속버스\n결제금액 20,000', None, None),
            ('시외버스 승차권\n결제금액 20,000', None, None),
            ('버스\n결제금액 20,000', None, None),
            ('골프장 교육세 기금 포함\n결제금액 110,000', None, None),
            ('과세 + 면세 혼합\n결제금액 11,000', None, None),
            ('과세물품가액 10,000\n면세금액 2,000\nVAT 1,000\n결제금액 13,000', 12000, 1000),
            ('골프장\n공급액 90,000\nVAT 9,000\n교육세 11,000\n결제금액 110,000', 90000, 9000),
        ]
        for text, supply, tax in cases:
            with self.subTest(text=text):
                result = self.resolve(text)
                self.assertEqual((result['supply_amount'], result['tax_amount']), (supply, tax))
                if supply is None:
                    self.assertEqual(result['validation']['decision'], 'REVIEW')

    def test_final_card_overrides_pre_discount_summary(self):
        for before, total, vat, supply in [(12000, 10800, 981, 9819), (11700, 10530, 957, 9573)]:
            with self.subTest(total=total):
                text = (f'상품 합계 {before}\n공급가액 10,000\nVAT 1,000\n할인 {before-total}\n'
                        f'최종 카드결제 {total}\n카드전표 VAT {vat}')
                result = self.resolve(text)
                self.assertEqual((result['total_amount'], result['supply_amount'], result['tax_amount']), (total, supply, vat))
                self.assertEqual(result['amount_resolution']['review_reason'], [])

    def test_guards(self):
        for text in [
            'KTX\n결제금액 50,800\n결제금액 45,000',
            '개인택시\n할인 1,000\n최종 카드결제 10,000',
            'KTX\n별도 수수료 500\n결제금액 11,000',
            '과세물품가액 10,000\n면세금액\nVAT 1,000\n결제금액 12,000',
            '일반 상점\n결제금액 11,000',
            'KTX\n부가세 별도\n결제금액 11,000',
        ]:
            with self.subTest(text=text):
                result = self.resolve(text)
                self.assertIsNone(result['supply_amount'])
                self.assertEqual(result['validation']['decision'], 'REVIEW')

    def test_conflicting_explicit_vat(self):
        result = self.resolve('공급가액 10,000\nVAT 1,000\nVAT 2,000\n결제금액 11,000')
        self.assertEqual(result['supply_amount'], 10000)
        self.assertIsNone(result['tax_amount'])
        self.assertIn('OCR_AMOUNT_CONFLICT', result['amount_resolution']['review_reason'])

    def test_small_mismatch_preserves_explicit(self):
        result = self.resolve('공급가액 10,000\nVAT 999\n결제금액 11,000')
        self.assertEqual((result['supply_amount'], result['tax_amount']), (10000, 999))
        self.assertEqual(result['amount_resolution']['review_reason'], [])

    def test_large_mismatch_preserves_explicit_and_reviews(self):
        result = self.resolve('공급가액 10,000\nVAT 1,000\n결제금액 15,000')
        self.assertEqual((result['supply_amount'], result['tax_amount']), (10000, 1000))
        self.assertIn('AMOUNT_RELATION_MISMATCH', result['amount_resolution']['review_reason'])

    def test_prompt_excludes_context_by_default(self):
        self.assertNotIn('resolution_context', NAMESPACE['_extract_amount_evidence']('KTX 50,800'))

    def test_transport_evidence_and_no_category_fallback(self):
        for name in ['일반택시', '택시', 'SRT', '고속철도', '시외우등고속', '시외고급고속', '고속버스', '항공권', '전세버스']:
            with self.subTest(name=name):
                result = self.resolve(name + '\n결제금액 11,000')
                self.assertEqual((result['supply_amount'], result['tax_amount']), (10000, 1000))
                self.assertEqual(result['validation']['decision'], 'PASS')
        result = self.resolve('승차권\n결제금액 11,000')
        self.assertEqual(result['amount_resolution']['tax_treatment'], 'UNKNOWN')

    def test_book_and_stationery_is_not_exempt_only(self):
        result = self.resolve('도서 면세\n볼펜 1,000\n결제금액 11,000')
        self.assertIsNone(result['tax_amount'])

    def test_model_total_must_be_unambiguous(self):
        result = self.resolve('KTX\n요금 50,800\n다른 금액 40,000', total=50800)
        self.assertIsNone(result['tax_amount'])
        result = self.resolve('KTX 50,800', total=50800)
        self.assertEqual(result['tax_amount'], 4618)

    def test_missing_final_vat_does_not_reuse_old_summary(self):
        result = self.resolve('공급가액 10,000\nVAT 1,000\n할인 1,000\n최종 카드결제 10,000')
        self.assertIsNone(result['supply_amount'])
        self.assertIsNone(result['tax_amount'])

    def test_conflicting_supply_and_final_totals(self):
        result = self.resolve('공급액 10,000\n공급액 11,000\nVAT 1,000\n결제금액 11,000')
        self.assertIsNone(result['supply_amount'])
        self.assertIn('OCR_AMOUNT_CONFLICT', result['amount_resolution']['review_reason'])
        result = self.resolve('최종 카드결제 11,000\n최종 카드결제 12,000\nVAT 1,000')
        self.assertIsNone(result['supply_amount'])

    def test_zero_exempt_component_allows_taxable_classification(self):
        result = self.resolve('과세물품가액 10,000\n면세금액 0\nVAT 1,000\n결제금액 11,000')
        self.assertEqual(result['amount_resolution']['tax_treatment'], 'TAXABLE')


if __name__ == '__main__':
    unittest.main()
