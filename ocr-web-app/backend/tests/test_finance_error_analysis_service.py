import unittest

from app.services.finance_error_analysis_service import analyze_finance_evaluation_failure


class FinanceErrorAnalysisServiceTests(unittest.TestCase):
    def test_returns_no_tags_for_success(self):
        result = analyze_finance_evaluation_failure(
            ocr_text="노트 1 5,000 5,000 결제금액 5,000",
            ground_truth={"total_amount": 5000, "items": [{"name": "노트", "quantity": 1, "unit_price": 5000, "total_amount": 5000}]},
            prediction={"total_amount": 5000, "items": [{"name": "노트", "quantity": 1, "unit_price": 5000, "total_amount": 5000}]},
            pipeline_trace={},
        )

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["error_tags"], [])

    def test_tags_llm_item_error_when_correct_candidate_was_changed(self):
        result = analyze_finance_evaluation_failure(
            ocr_text="노트 2 1,000 2,000 결제금액 2,000",
            ground_truth={"total_amount": 2000, "items": [{"name": "노트", "quantity": 2, "unit_price": 1000, "total_amount": 2000}]},
            prediction={"total_amount": 2000, "items": [{"name": "노트", "quantity": 1, "unit_price": 1000, "total_amount": 1000}]},
            pipeline_trace={
                "item_candidates": [{"name_candidate": "노트", "quantity_candidate": 2, "unit_price_candidate": 1000, "amount_candidate": 2000}],
                "model_items": [{"name": "노트", "quantity": 1, "unit_price": 1000, "total_amount": 1000}],
            },
        )

        codes = {(tag["category"], tag["code"]) for tag in result["error_tags"]}
        self.assertIn(("LLM_ERROR", "QUANTITY_ERROR"), codes)
        self.assertIn(("LLM_ERROR", "LLM_CHANGED_CORRECT_CANDIDATE"), codes)
        self.assertIn(("VALIDATION_ERROR", "ITEM_SUM_MISMATCH"), codes)

    def test_supports_multiple_tags_and_review_state(self):
        result = analyze_finance_evaluation_failure(
            ocr_text="노트 결제금액 4,000",
            ground_truth={"total_amount": 5000, "items": [{"name": "노트", "quantity": 2, "unit_price": 2500, "total_amount": 5000}]},
            prediction={"total_amount": 4000, "supply_amount": 3500, "tax_amount": 400, "items": []},
            pipeline_trace={"item_candidates": []},
        )

        codes = {tag["code"] for tag in result["error_tags"]}
        self.assertIn("OCR_NUMBER_ERROR", codes)
        self.assertIn("ITEM_MISSING", codes)
        self.assertIn("SUPPLY_TAX_MISMATCH", codes)
        self.assertFalse(result["needs_review"])

    def test_leaves_uncertain_attribution_for_review(self):
        result = analyze_finance_evaluation_failure(
            ocr_text="식별할 수 없는 영수증",
            ground_truth={"items": [{"name": "노트", "quantity": 1, "total_amount": 5000}]},
            prediction={"items": []},
            pipeline_trace={"item_candidates": []},
        )

        self.assertTrue(result["needs_review"])
        self.assertIn("UNKNOWN", {tag["category"] for tag in result["error_tags"]})


if __name__ == "__main__":
    unittest.main()
