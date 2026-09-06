"""Deterministic regressions: no OCR service or LLM calls required."""
import unittest

from test_receipt_item_grounding import cell, coffee, header, model_item, page_of
from test_receipt_tax_policy import NAMESPACE
from app.services.receipt_item_grounding import ground_items


class GlobalReceiptTests(unittest.TestCase):
    def test_v3_total_support_and_transport_guard(self):
        for total, supply, tax in ((77700, 70636, 7064), (190000, 172727, 17273)):
            result = {'total_amount': total}
            NAMESPACE['_reconcile_amounts'](result, f'거래 금액 {total}\n다른 숫자 3000')
            self.assertEqual((result['supply_amount'], result['tax_amount']), (supply, tax))
            self.assertEqual(result['amount_resolution']['total_confidence'], 'CONFIRMED_TOTAL')
            self.assertIn('OCR_AMOUNT_CANDIDATE', result['amount_resolution']['total_evidence'])
        result = self.resolve('택시 미터요금\n결제금액 5300')
        self.assertIsNone(result['tax_amount'])
        self.assertEqual(result['amount_resolution']['tax_treatment'], 'TRANSPORT_SPECIAL')
        result = {'total_amount': 77700}
        NAMESPACE['_reconcile_amounts'](result, '금액 77700\nVAT 7064\n다른 숫자 3000')
        self.assertEqual(result['supply_amount'], 70636)

    def test_global_repair_with_single_ocr_price(self):
        text, page = page_of([cell('아메리카노', 0, 70), cell('13900', 420, 70)])
        items = [model_item(q=2, u=13900, a=27800)]
        trace = ground_items(items, text, [page], receipt_total=13900)
        self.assertEqual(items[0]['quantity'], 1)
        self.assertEqual(items[0]['total_amount'], 13900)
        self.assertEqual(trace['changed_items'], 1)

    def test_global_repair_abstains_for_multiple_solutions(self):
        text, page = page_of([cell('아메리카노', 0, 70), cell('13900', 420, 70)],
                             [cell('카페라떼', 0, 110), cell('13900', 420, 110)])
        items = [model_item(q=2, u=13900, a=27800), model_item('카페라떼', 2, 13900, 27800)]
        trace = ground_items(items, text, [page], receipt_total=41700)
        self.assertEqual([i['quantity'] for i in items], [2, 2])
        self.assertEqual(trace['changed_items'], 0)

    def test_total_conflict_and_missing_ocr_still_block(self):
        for text in ('일반 거래', '결제금액 77700\n결제금액 190000',
                     '금액 77700\n할인 1000', '금액 77700\n과세 면세 혼합'):
            result = {'total_amount': 77700, 'items': [{'total_amount': 77700}]}
            NAMESPACE['_reconcile_amounts'](result, text)
            self.assertIsNone(result['tax_amount'])

    def test_three_paid_parents_with_repeated_unindented_options(self):
        rows, items = [], []
        for i in range(3):
            y = 70 + i * 400
            name = f'버거세트{i}'
            rows.append(coffee(y, name=name, q='1', u='9000', a='9000'))
            items.append(model_item(name, 1, 9000, 9000))
            for j, child in enumerate(('콜라', '후렌치후라이', 'DrinkSwap')):
                rows.append([cell(child, 0, y + 120 + j * 70)])
                items.append(model_item(child))
        text, page = page_of(*rows)
        trace = ground_items(items, text, [page])
        self.assertEqual(len(items), 3)
        self.assertEqual(trace['hierarchical_collapsed_count'], 9)

    def validate(self, items, total, discount=None, grounding=None):
        result = dict(merchant='식당', transaction_date='2026-09-05', expense_category='외식',
                      total_amount=total, items=items, discount_amount=discount,
                      item_grounding=grounding or {})
        return NAMESPACE['_simple_validation'](result, f'결제금액 {total}')

    def resolve(self, text):
        result = {}
        NAMESPACE['_reconcile_amounts'](result, text)
        return result

    def test_coherent_double_quantity_is_reviewed_without_bbox(self):
        result = self.validate([model_item(q=2, u=13900, a=27800)], 13900)
        self.assertIn('ITEM_SUM_TOTAL_MISMATCH', result['reasons'])
        self.assertFalse(result['checks']['global_item_consistency_pass'])
        self.assertEqual(result['checks']['item_sum_vs_total']['quantity_correction_candidates'][0]['quantity'], 1)

    def test_coherent_double_quantity_is_corrected_from_bbox(self):
        text, page = page_of(header(), coffee(q='1', u='13900', a='13900'))
        items = [model_item(q=2, u=13900, a=27800)]
        ground_items(items, text, [page], receipt_total=13900)
        self.assertEqual(items[0]['quantity'], 1)
        self.assertEqual(items[0]['total_amount'], 13900)

    def test_summary_bbox_is_reserved_and_copied_amount_reviewed(self):
        text, page = page_of([cell('아메리카노', 0, 70)],
                             [cell('총액', 0, 95), cell('13900', 420, 95)])
        items = [model_item(q=1, u=13900, a=13900)]
        trace = ground_items(items, text, [page], receipt_total=13900)
        self.assertEqual(trace['reserved_summary_amount_count'], 1)
        self.assertEqual(trace['changed_items'], 0)
        self.assertIn('SUMMARY_AMOUNT_USED_AS_ITEM', trace['review_reasons'])
        self.assertIn('SUMMARY_AMOUNT_USED_AS_ITEM', self.validate(items, 13900, grounding=trace)['reasons'])

    def test_explicit_tax_alone_is_preserved(self):
        result = self.resolve('부가세 1,000')
        self.assertEqual(result['tax_amount'], 1000)
        self.assertEqual(result['amount_resolution']['tax_source'], 'EXPLICIT_OCR')
        self.assertIsNone(result['supply_amount'])

    def test_explicit_total_derives_missing_side(self):
        for label in ('공급가액 10,000', 'VAT 1,000'):
            result = self.resolve(label + '\n결제금액 11,000')
            self.assertEqual((result['supply_amount'], result['tax_amount']), (10000, 1000))

    def test_dining_guard_requires_ocr_context(self):
        result = self.resolve('일반 음식점\n주문 비빔밥\n결제금액 11,000')
        self.assertEqual((result['supply_amount'], result['tax_amount']), (10000, 1000))
        for prefix in ('시내버스 승차권', '음식점 주문\n면세',
                       '음식점 주문\n봉사료', '음식점 주문\n할인 1000'):
            with self.subTest(prefix=prefix):
                self.assertIsNone(self.resolve(prefix + '\n결제금액 11,000')['tax_amount'])

    def test_hierarchical_unpriced_children_collapse_but_paid_option_stays(self):
        text, page = page_of(coffee(name='버거세트', q='1', u='9000', a='9000'),
                             [cell('콜라', 40, 100)], [cell('DrinkSwap', 40, 125)],
                             [cell('후렌치후라이', 40, 150)],
                             coffee(190, name='추가토핑', q='1', u='1000', a='1000'))
        items = [model_item('버거세트', 1, 9000, 9000), model_item('콜라'),
                 model_item('DrinkSwap'), model_item('후렌치후라이'), model_item('추가토핑', 1, 1000, 1000)]
        trace = ground_items(items, text, [page])
        self.assertEqual(trace['item_layout_type'], 'HIERARCHICAL')
        self.assertEqual(trace['hierarchical_collapsed_count'], 3)
        self.assertEqual([i['name'] for i in items], ['버거세트', '추가토핑'])

    def test_headerless_table_adds_ocr_items_and_overrides_model_numbers(self):
        text, page = page_of(coffee(), coffee(110, name='녹차라떼'))
        items = [model_item(q=1, u=100, a=100)]
        trace = ground_items(items, text, [page])
        self.assertTrue(trace['headerless_table_detected'])
        self.assertEqual(trace['item_layout_type'], 'COLUMN_TABLE')
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['total_amount'], 9000)

    def test_discount_and_partial_sum(self):
        for discount in (1000, -1000):
            result = self.validate([model_item(q=1, u=10000, a=10000)], 9000, discount)
            self.assertTrue(result['checks']['global_item_consistency_pass'])
        result = self.validate([model_item(q=1, u=10000, a=10000), model_item(a=None)], 9000)
        self.assertIn('ITEM_SUM_TOTAL_MISMATCH', result['reasons'])

    def test_reused_numeric_bbox_is_reviewed(self):
        text, page = page_of(header(), coffee())
        trace = ground_items([model_item(), model_item('아메리카노.')], text, [page])
        self.assertTrue(trace['reused_numeric_bbox_detected'])
        self.assertIn('NUMERIC_BBOX_REUSED', trace['review_reasons'])

    def test_complete_table_uses_ocr_rows_as_item_inventory(self):
        text, page = page_of(header(), coffee(), coffee(110, name='녹차라떼'))
        items = [model_item('존재하지않는상품')]
        trace = ground_items(items, text, [page])
        self.assertEqual([i['name'] for i in items], ['아메리카노', '녹차라떼'])
        self.assertEqual(trace['removed_items'][0]['reason'], 'not_in_complete_ocr_table')

    def test_headerless_misaligned_columns_require_review(self):
        shifted = coffee(110, name='녹차라떼')
        shifted[1] = cell('2', 250, 110)
        text, page = page_of(coffee(), shifted)
        trace = ground_items([model_item()], text, [page])
        self.assertFalse(trace['headerless_table_detected'])
        self.assertIn('HEADERLESS_TABLE_LOW_CONFIDENCE', trace['review_reasons'])

    def test_all_missing_item_amounts_are_not_global_pass(self):
        result = self.validate([model_item(a=None)], 13900)
        self.assertIsNone(result['checks']['global_item_consistency_pass'])
        self.assertIn('ITEM_TOTALS_INCOMPLETE', result['reasons'])


if __name__ == '__main__':
    unittest.main()
