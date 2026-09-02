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

        self.assertEqual(FINANCE_PROMPT_VERSION, "receipt-simple-v1-one-call")
        self.assertIn("merchant, transaction_date, expense_category", prompt)
        self.assertIn("items의 키", prompt)
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


if __name__ == "__main__":
    unittest.main()
