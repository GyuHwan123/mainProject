from pathlib import Path
import json
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes.finance import (  # noqa: E402
    EXPENSE_CATEGORIES,
    FINANCE_PROMPT_VERSION,
    _bounded_ocr_text,
    _classify_receipt_with_model,
    _normalize,
    _preflight_review_reasons,
    _simple_receipt_prompt,
)
from app.services.finance_receipt_simple import _reconcile_amounts  # noqa: E402
from app.services.finance_receipt_simple import _simple_validation  # noqa: E402


SAMPLE_OCR = """GS25 청주테크노점
판매일자 2026-02-20 19:15:14
저당바닐라초코 1 3,300
M&M'S 2 1,800 3,600
공급가액 6,273
부가세 627
결제금액 6,900
신용카드
"""


class FinanceClassificationTests(unittest.IsolatedAsyncioTestCase):
    def test_simple_v1_prompt_requests_all_fields_once(self):
        prompt, diagnostics = _simple_receipt_prompt(SAMPLE_OCR, "receipt.jpg")

        self.assertEqual(FINANCE_PROMPT_VERSION, "receipt-simple-v1.1-one-call-amount-evidence")
        self.assertIn("merchant, transaction_date, expense_category", prompt)
        self.assertIn("items의 키", prompt)
        self.assertIn("과세상품의 세전 금액과 면세상품금액을 합한 전체 공급액", prompt)
        self.assertIn("면세상품만 있으면 supply_amount는 면세상품금액이고 tax_amount는 0", prompt)
        self.assertIn("supply_amount=38,100", prompt)
        self.assertIn("쇼핑백·포장비·배달비", prompt)
        self.assertIn("할인 전 세금 요약", prompt)
        self.assertTrue(all(category in prompt for category in EXPENSE_CATEGORIES))
        self.assertNotIn("semantic_evidence", prompt)
        self.assertNotIn("candidate", prompt)
        self.assertGreater(diagnostics["prompt_line_count"], 0)

    def test_prompt_input_is_bounded(self):
        text = "\n".join(f"상품{i} 1 {i + 1000:,}" for i in range(2000))
        bounded, diagnostics = _bounded_ocr_text(text)

        self.assertLessEqual(len(bounded), 8000)
        self.assertTrue(diagnostics["truncated"])

    def test_preflight_skips_unreadable_ocr(self):
        self.assertIn("OCR_TEXT_TOO_SHORT", _preflight_review_reasons("흐림"))
        self.assertIn("NO_MONEY_EVIDENCE", _preflight_review_reasons("흐림"))

    async def test_model_is_called_exactly_once(self):
        model_result = {
            "merchant": "GS25 청주테크노점",
            "transaction_date": "2026-02-20",
            "expense_category": "식품/장보기",
            "supply_amount": 6273,
            "tax_amount": 627,
            "discount_amount": 0,
            "total_amount": 6900,
            "payment_method": "카드",
            "items": [
                {"name": "저당바닐라초코", "quantity": 1, "unit_price": 3300, "total_amount": 3300},
                {"name": "M&M'S", "quantity": 2, "unit_price": 1800, "total_amount": 3600},
            ],
        }
        generator = AsyncMock(return_value=json.dumps(model_result, ensure_ascii=False))
        with patch("app.api.routes.finance.generate", generator):
            result = await _classify_receipt_with_model(SAMPLE_OCR, "receipt.jpg", "gemma3:4b")

        self.assertEqual(generator.await_count, 1)
        call_options = generator.await_args.kwargs
        self.assertEqual(call_options["keep_alive"], "0s")
        self.assertEqual(call_options["num_ctx"], 4096)
        self.assertEqual(call_options["request_timeout_seconds"], 600)
        self.assertEqual(result["llm_trace"]["call_count"], 1)
        self.assertEqual(result["llm_trace"]["call_status"], "success")
        self.assertEqual(result["automation_validation"]["decision"], "PASS")

    async def test_preflight_review_uses_zero_model_calls(self):
        generator = AsyncMock()
        with patch("app.api.routes.finance.generate", generator):
            result = await _classify_receipt_with_model("흐림", "receipt.jpg", "gemma3:4b")

        self.assertEqual(generator.await_count, 0)
        self.assertEqual(result["llm_trace"]["call_count"], 0)
        self.assertEqual(result["automation_validation"]["decision"], "REVIEW")

    def test_normalization_does_not_repair_wrong_amount(self):
        result = {
            "merchant": "GS25 청주테크노점",
            "transaction_date": "2026-02-20",
            "expense_category": "식품/장보기",
            "supply_amount": 6273,
            "tax_amount": 627,
            "discount_amount": 0,
            "total_amount": 9999,
            "payment_method": "카드",
            "items": [],
        }
        normalized = _normalize(result, "receipt.jpg", SAMPLE_OCR)

        self.assertEqual(normalized["total_amount"], 9999)
        validation = normalized["structured_data"]["automation_validation"]
        self.assertEqual(validation["decision"], "REVIEW")
        self.assertIn("TOTAL_AMOUNT_NOT_IN_OCR", validation["reasons"])

    def test_normalization_limits_payment_method_to_supported_values(self):
        base = {
            "merchant": "가맹점", "transaction_date": "2026-02-20", "expense_category": "식품/장보기",
            "supply_amount": 1000, "tax_amount": 100, "total_amount": 1100, "items": [],
        }
        card = _normalize({**base, "payment_method": "신한 체크카드"}, "card.jpg", "가맹점 결제금액 1,100 신한 체크카드")
        cash = _normalize({**base, "payment_method": "CASH"}, "cash.jpg", "가맹점 결제금액 1,100 현금영수증")
        unknown = _normalize({**base, "payment_method": "계좌이체"}, "transfer.jpg", "가맹점 결제금액 1,100 계좌이체")
        missing = _normalize({**base, "payment_method": None}, "missing.jpg", "가맹점 결제금액 1,100")

        self.assertEqual(card["payment_method"], "카드")
        self.assertEqual(card["structured_data"]["payment_method"], "카드")
        self.assertEqual(cash["payment_method"], "현금")
        self.assertEqual(unknown["payment_method"], "기타")
        self.assertIsNone(missing["payment_method"])

    def test_normalization_preserves_unknown_supply_and_tax_as_null(self):
        result = {
            "merchant": "법인택시",
            "transaction_date": "2020-05-16",
            "expense_category": "대중교통",
            "supply_amount": None,
            "tax_amount": None,
            "discount_amount": None,
            "total_amount": 5300,
            "payment_method": "카드",
            "items": [],
        }

        normalized = _normalize(result, "taxi.jpg", "결제요금 5,300원")

        self.assertIsNone(normalized["supply_amount"])
        self.assertIsNone(normalized["tax_amount"])
        self.assertIsNone(normalized["structured_data"]["supply_amount"])
        self.assertIsNone(normalized["structured_data"]["tax_amount"])

    def test_reconcile_combines_taxable_and_exempt_supply(self):
        result = {"supply_amount": 36212, "tax_amount": 9999, "total_amount": 55813}
        text = """면세물품가액 7,280
과세물품가액 36,212
부가세 3,621
절사금액 -3
결제금액 47,110"""

        trace = _reconcile_amounts(result, text)

        self.assertEqual(result["supply_amount"], 43492)
        self.assertEqual(result["tax_amount"], 3621)
        self.assertEqual(result["total_amount"], 47110)
        self.assertIn("supply_from_taxable_plus_exempt_ocr", trace["changes"])

    def test_reconcile_repairs_ungrounded_supply_only_without_adjustments(self):
        result = {"supply_amount": 30000, "tax_amount": 2727, "total_amount": 30000}

        trace = _reconcile_amounts(result, "부가세액 2,727\n결제금액 30,000")

        self.assertEqual(result["supply_amount"], 27273)
        self.assertIn("supply_from_guarded_arithmetic", trace["changes"])

    def test_reconcile_uses_explicit_tax_and_total_despite_discount(self):
        result = {"supply_amount": 10000, "tax_amount": 1000, "total_amount": 15000}

        trace = _reconcile_amounts(result, "부가세 1,000\n할인 2,000\n결제금액 15,000")

        self.assertEqual(result["supply_amount"], 14000)
        self.assertIn("supply_from_guarded_arithmetic", trace["changes"])

    def test_reconcile_does_not_confuse_taxable_amount_with_vat(self):
        result = {"supply_amount": None, "tax_amount": None, "total_amount": None}

        _reconcile_amounts(result, "과세액 21,091\n부가세액 2,109\n결제금액 23,200")

        self.assertEqual(result["supply_amount"], 21091)
        self.assertEqual(result["tax_amount"], 2109)
        self.assertEqual(result["total_amount"], 23200)

    def test_reconcile_recognizes_parenthesized_tax_included(self):
        result = {"supply_amount": None, "tax_amount": None, "total_amount": None}

        _reconcile_amounts(result, "결제금액 23,200\n(부가세포함) (2,109)\n할인금액 3,100")

        self.assertEqual(result["supply_amount"], 21091)
        self.assertEqual(result["tax_amount"], 2109)
        self.assertEqual(result["total_amount"], 23200)

    def test_reconcile_does_not_force_ambiguous_next_line_amounts(self):
        result = {"supply_amount": None, "tax_amount": None, "total_amount": None}

        _reconcile_amounts(result, "공급가액\n21,091\n부가세\n2,109\n결제액\n23,200")

        self.assertIsNone(result["supply_amount"])
        self.assertIsNone(result["tax_amount"])
        self.assertIsNone(result["total_amount"])

    def test_reconcile_does_not_assume_missing_exempt_value_is_zero(self):
        result = {"supply_amount": None, "tax_amount": 1000, "total_amount": 12000}

        trace = _reconcile_amounts(result, "과세물품가액 10,000\n면세물품가액\n부가세 1,000\n결제금액 12,000")

        self.assertIsNone(result["supply_amount"])
        self.assertNotIn("supply_from_taxable_plus_exempt_ocr", trace["changes"])

    def test_taxable_amount_is_not_reused_as_payment_total(self):
        result = {"supply_amount": None, "tax_amount": None, "total_amount": None}

        _reconcile_amounts(result, "과세금액 27,273 부가세액 2,727")

        self.assertEqual(result["supply_amount"], 27273)
        self.assertEqual(result["tax_amount"], 2727)
        self.assertIsNone(result["total_amount"])

    def test_masked_card_number_is_not_used_as_payment_total(self):
        result = {"supply_amount": None, "tax_amount": None, "total_amount": None}

        _reconcile_amounts(result, "카드결제액 65562082520*")

        self.assertIsNone(result["total_amount"])

    def test_uncorroborated_tax_evidence_requires_review(self):
        result = {
            "merchant": "가맹점", "transaction_date": "2025-10-01", "expense_category": "식품/장보기",
            "supply_amount": None, "tax_amount": 17300, "discount_amount": None,
            "total_amount": 17300, "payment_method": "카드", "items": [],
        }
        text = "매입사명 삼성카드 17,300원 부가세 17,300원"
        _reconcile_amounts(result, text)

        validation = _simple_validation(result, text)

        self.assertIn("TAX_EVIDENCE_UNCORROBORATED", validation["reasons"])

    def test_validation_allows_small_rounding_difference_after_discount(self):
        result = {
            "merchant": "NC신구로점",
            "transaction_date": "2026-08-10",
            "expense_category": "식품/장보기",
            "supply_amount": 27772,
            "tax_amount": 1860,
            "discount_amount": 6380,
            "total_amount": 29630,
            "payment_method": "카드",
            "items": [{"name": "상품 합계", "quantity": 1, "unit_price": 36012, "total_amount": 36012}],
        }

        validation = _simple_validation(result, "총합계액 36,012\n총할인액 -6,380\n절사금액 -2\n결제액 29,630")

        self.assertNotIn("AMOUNT_RELATION_MISMATCH", validation["reasons"])
        self.assertNotIn("ITEM_SUM_MISMATCH", validation["reasons"])

    def test_validation_accepts_explicit_pre_discount_tax_summary(self):
        result = {
            "merchant": "공차 선릉중앙점",
            "transaction_date": "2025-10-01",
            "expense_category": "식품/장보기",
            "supply_amount": 10910,
            "tax_amount": 1090,
            "discount_amount": 1200,
            "total_amount": 10800,
            "payment_method": "카드",
            "items": [],
        }
        text = "공급가액 10,910\n부가세 1,090\n할인금액 1,200\n결제금액 10,800"

        validation = _simple_validation(result, text)

        self.assertNotIn("AMOUNT_RELATION_MISMATCH", validation["reasons"])
        self.assertEqual(validation["checks"]["amount_relation_basis"], "pre_discount_tax_summary")

    def test_validation_accepts_negative_discount_sign(self):
        result = {
            "merchant": "공차 선릉중앙점",
            "transaction_date": "2025-10-01",
            "expense_category": "식품/장보기",
            "supply_amount": 10910,
            "tax_amount": 1090,
            "discount_amount": -1200,
            "total_amount": 10800,
            "payment_method": "카드",
            "items": [],
        }

        validation = _simple_validation(
            result,
            "공급가액 10,910\n부가세 1,090\n할인금액 -1,200\n결제금액 10,800",
        )

        self.assertNotIn("AMOUNT_RELATION_MISMATCH", validation["reasons"])
        self.assertEqual(validation["checks"]["amount_relation_basis"], "pre_discount_tax_summary")

    def test_validation_skips_amount_relation_for_partial_tax_information(self):
        result = {
            "merchant": "팀홀튼",
            "transaction_date": "2026-02-23",
            "expense_category": "식품/장보기",
            "supply_amount": None,
            "tax_amount": 1027,
            "discount_amount": None,
            "total_amount": 11300,
            "payment_method": "카드",
            "items": [],
        }

        validation = _simple_validation(result, "부가세포함 (1,027)\n결제금액 11,300")

        self.assertNotIn("AMOUNT_RELATION_MISMATCH", validation["reasons"])
        self.assertEqual(validation["checks"]["amount_relation_basis"], "not_checkable_partial_amounts")

    def test_validation_keeps_review_for_unexplained_explicit_mismatch(self):
        result = {
            "merchant": "가맹점",
            "transaction_date": "2025-10-01",
            "expense_category": "식품/장보기",
            "supply_amount": 10000,
            "tax_amount": 1000,
            "discount_amount": 500,
            "total_amount": 9000,
            "payment_method": "카드",
            "items": [],
        }
        text = "공급가액 10,000\n부가세 1,000\n할인금액 500\n결제금액 9,000"

        validation = _simple_validation(result, text)

        self.assertIn("AMOUNT_RELATION_MISMATCH", validation["reasons"])
        self.assertEqual(validation["checks"]["amount_relation_basis"], "explicit_ocr_mismatch")

    def test_item_amount_relation_mismatch_is_non_blocking_warning(self):
        item = {"name": "행사상품", "quantity": 2, "unit_price": 1000, "total_amount": 1500}
        result = {
            "merchant": "할인마트",
            "transaction_date": "2025-10-01",
            "expense_category": "식품/장보기",
            "supply_amount": 1364,
            "tax_amount": 136,
            "discount_amount": None,
            "total_amount": 1500,
            "payment_method": "카드",
            "items": [item.copy()],
        }
        text = "할인마트 2025-10-01\n행사상품 2 1,000 1,500\n공급가액 1,364\n부가세 136\n결제금액 1,500"

        validation = _simple_validation(result, text)

        self.assertEqual(validation["decision"], "PASS")
        self.assertNotIn("ITEM_ARITHMETIC_MISMATCH", validation["reasons"])
        self.assertTrue(validation["checks"]["item_amount_relation_warning"])
        self.assertEqual(validation["warnings"][0]["code"], "ITEM_AMOUNT_RELATION_WARNING")
        self.assertEqual(result["items"][0], item)


if __name__ == "__main__":
    unittest.main()
