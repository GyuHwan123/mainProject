import unittest

from app.services.finance_error_analysis_service import analyze_finance_evaluation_failure


class FinanceErrorAnalysisServiceTests(unittest.TestCase):
    def test_tags_semantically_equivalent_values_as_normalization_errors(self):
        result = analyze_finance_evaluation_failure(
            ocr_text="한국철도공사 KTX 125 일반실 승차권",
            ground_truth={
                "merchant": "한국철도공사",
                "expense_category": "교통",
                "items": [{"name": "KTX 125 일반실 승차권"}],
            },
            prediction={
                "merchant": "KORAIL",
                "expense_category": "교통비",
                "items": [{"name": "KTX125 일반실 1호차입석"}],
            },
            pipeline_trace={},
        )

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["category_counts"], {"NORMALIZATION_ERROR": 3})
        self.assertEqual({tag["code"] for tag in result["error_tags"]}, {"SEMANTIC_EQUIVALENCE"})

    def test_classifies_receipt_failures_without_unknown_or_unclassified_fields(self):
        truth = {
            "merchant": "늘좋은주유소", "transaction_date": "2018-01-10",
            "expense_category": "주유/교통", "total_quantity": 48.936,
            "items": [{"name": "유류", "quantity": 48.936, "unit_price": 1410, "total_amount": 69000}],
        }
        prediction = {
            "merchant": "늘좋은주유소", "transaction_date": "2017-11-09",
            "expense_category": "기타", "total_quantity": None,
            "items": [{"name": "NS-OIL", "quantity": 2, "unit_price": 10000, "total_amount": 22000}],
        }
        result = analyze_finance_evaluation_failure(
            ocr_text="2017년11월09일 품질검사 거래일시: 18/01/10 주유소 유종:경유 단가:1410원 48.936L",
            ground_truth=truth,
            prediction=prediction,
            pipeline_trace={
                "item_candidates": [{"name_candidate": "NS-OIL", "quantity_candidate": 2, "unit_price_candidate": 10, "amount_candidate": 20}],
                "model_items": prediction["items"],
                "validator": {"input": prediction, "output": prediction},
            },
        )

        tags = result["error_tags"]
        self.assertNotIn("UNKNOWN", {tag["category"] for tag in tags})
        self.assertIn("CATEGORY_INFERENCE_ERROR", {tag["code"] for tag in tags})
        self.assertIn("TRANSACTION_DATE_SELECTION_ERROR", {tag["code"] for tag in tags})
        self.assertIn("VALUE_CANDIDATE_MISSING", {tag["code"] for tag in tags})
        self.assertIn("ITEM_CANDIDATE_SELECTION_ERROR", {tag["code"] for tag in tags})

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
        self.assertIn("ITEM_TEXT_MISSING", codes)
        self.assertIn("SUPPLY_TAX_MISMATCH", codes)
        self.assertTrue(result["needs_review"])

    def test_leaves_uncertain_attribution_for_review(self):
        result = analyze_finance_evaluation_failure(
            ocr_text="식별할 수 없는 영수증",
            ground_truth={"items": [{"name": "노트", "quantity": 1, "total_amount": 5000}]},
            prediction={"items": []},
            pipeline_trace={"item_candidates": []},
        )

        self.assertTrue(result["needs_review"])
        self.assertIn("OCR_ERROR", {tag["category"] for tag in result["error_tags"]})

    def test_tags_summary_amount_selected_from_an_item(self):
        result = analyze_finance_evaluation_failure(
            ocr_text="샤프 8,300\n최종 결제금액 56,300",
            ground_truth={"total_amount": 56300, "items": [{"name": "샤프", "total_amount": 8300}]},
            prediction={"total_amount": 8300, "items": [{"name": "샤프", "total_amount": 8300}]},
            pipeline_trace={
                "llm": {"summary_raw": {"total_amount": 8300}},
                "deterministic_hints": {"total_amount": 56300, "total_amount_source": "labeled_final"},
                "model_items": [{"name": "샤프", "total_amount": 8300}],
            },
        )

        self.assertIn("SUMMARY_AMOUNT_SELECTION_ERROR", {tag["code"] for tag in result["error_tags"]})

    def test_tags_digit_read_as_similar_letter_as_ocr_error(self):
        result = analyze_finance_evaluation_failure(
            ocr_text="[샌디스크] Z71/16G 1 10,000",
            ground_truth={"items": [{"name": "[샌디스크] 271/16G", "quantity": 1, "total_amount": 10000}]},
            prediction={"items": [{"name": "[샌디스크] Z71/16G", "quantity": 1, "total_amount": 10000}]},
            pipeline_trace={
                "item_candidates": [{"name_candidate": "[샌디스크] Z71/16G", "quantity_candidate": 1, "amount_candidate": 10000}],
                "model_items": [{"name": "[샌디스크] Z71/16G", "quantity": 1, "total_amount": 10000}],
            },
        )

        tags = {(tag["category"], tag["code"], tag["field"]) for tag in result["error_tags"]}
        self.assertIn(("OCR_ERROR", "OCR_CHARACTER_CONFUSION", "name"), tags)

    def test_tags_similar_letter_read_as_digit_as_ocr_error(self):
        result = analyze_finance_evaluation_failure(
            ocr_text="[샌디스크] 271/16G 1 10,000",
            ground_truth={"items": [{"name": "[샌디스크] Z71/16G", "quantity": 1, "total_amount": 10000}]},
            prediction={"items": [{"name": "[샌디스크] 271/16G", "quantity": 1, "total_amount": 10000}]},
            pipeline_trace={
                "item_candidates": [{"name_candidate": "[샌디스크] 271/16G", "quantity_candidate": 1, "amount_candidate": 10000}],
                "model_items": [{"name": "[샌디스크] 271/16G", "quantity": 1, "total_amount": 10000}],
            },
        )

        tags = {(tag["category"], tag["code"], tag["field"]) for tag in result["error_tags"]}
        self.assertIn(("OCR_ERROR", "OCR_CHARACTER_CONFUSION", "name"), tags)

    def test_tags_lost_quantity_decimal_separator_as_normalization_error(self):
        result = analyze_finance_evaluation_failure(
            ocr_text="보통 휘발유 1,429 X 20.994 30,000",
            ground_truth={"items": [{"name": "보통 휘발유", "quantity": 20.994, "unit_price": 1429, "total_amount": 30000}]},
            prediction={"items": [{"name": "보통 휘발유", "quantity": 20994, "unit_price": 1429, "total_amount": 30000}]},
            pipeline_trace={
                "item_candidates": [{"name_candidate": "보통 휘발유", "quantity_candidate": 20994, "unit_price_candidate": 1429, "amount_candidate": 30000}],
                "model_items": [{"name": "보통 휘발유", "quantity": 20994, "unit_price": 1429, "total_amount": 30000}],
            },
        )

        tags = {(tag["category"], tag["code"], tag["field"]) for tag in result["error_tags"]}
        self.assertIn(("NORMALIZATION_ERROR", "DECIMAL_SEPARATOR_LOST", "quantity"), tags)
        self.assertNotIn(("LLM_ERROR", "QUANTITY_ERROR", "quantity"), tags)

    def test_tags_partial_merchant_from_complete_ocr_text_as_llm_error(self):
        result = analyze_finance_evaluation_failure(
            ocr_text="CJ올리브영 청라커낼웨이점",
            ground_truth={"merchant": "CJ올리브영 청라커낼웨이점"},
            prediction={"merchant": "CJ올리브영"},
            pipeline_trace={},
        )

        tags = {(tag["category"], tag["code"]) for tag in result["error_tags"]}
        self.assertIn(("LLM_ERROR", "MERCHANT_DETAIL_DROPPED"), tags)

    def test_tags_validator_changes_and_item_mutations(self):
        result = analyze_finance_evaluation_failure(
            ocr_text="노트 1 5,000\n결제금액 5,000",
            ground_truth={"total_amount": 5000, "items": [{"name": "노트", "quantity": 1, "total_amount": 5000}]},
            prediction={"total_amount": 3000, "items": [{"name": "가짜품목", "quantity": 1, "total_amount": 3000}]},
            pipeline_trace={
                "llm": {"summary_raw": {"total_amount": 5000}},
                "validator": {"input": {"total_amount": 5000}, "output": {"total_amount": 3000}},
                "model_items": [{"name": "노트", "quantity": 1, "total_amount": 5000}],
            },
        )

        codes = {tag["code"] for tag in result["error_tags"]}
        self.assertIn("VALIDATOR_CHANGED_CORRECT_VALUE", codes)
        self.assertIn("VALIDATOR_DROPPED_CORRECT_ITEM", codes)
        self.assertIn("VALIDATOR_ADDED_UNSUPPORTED_ITEM", codes)


if __name__ == "__main__":
    unittest.main()
