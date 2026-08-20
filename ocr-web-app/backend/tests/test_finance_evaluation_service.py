from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.finance_evaluation_service import estimate_ocr_impact, normalize_ground_truth, score_fields, verify_workbook  # noqa: E402


class FinanceEvaluationServiceTests(unittest.TestCase):
    def test_estimates_ocr_and_llm_error_causes_per_field(self):
        truth = {"merchant": "테스트 상점", "total_amount": 81300, "payment_method": "현금"}
        score = score_fields(
            {"merchant": "다른 상점", "total_amount": 81300, "payment_method": "현금"},
            truth,
        )
        impact = estimate_ocr_impact("테스트상점 합계 81,300원 신용카드", truth, score)
        statuses = {item["field"]: item["status"] for item in impact["fields"]}

        self.assertEqual(statuses["merchant"], "LIKELY_LLM_ERROR")
        self.assertEqual(statuses["total_amount"], "SUCCESS")
        self.assertEqual(statuses["payment_method"], "LLM_RECOVERY")
        self.assertEqual(impact["counts"]["LIKELY_LLM_ERROR"], 1)

    def test_normalizes_korean_ground_truth_without_category_classification(self):
        truth = normalize_ground_truth({
            "image": "receipt_001.jpg",
            "가게명": "(주)바늘이야기-팝업스토어",
            "구매일자": "2025-12-14 19:19:46",
            "구매물품": [
                {"상품명": "스카프 도안", "단가": 6000, "수량": 1, "금액": 6000},
                {"상품명": "알파카 실", "단가": 12600, "수량": 6, "금액": 75600},
            ],
            "총 물품 수량": 7,
            "총 결제액": 81300,
            "카테고리": "취미/쇼핑",
            "결제방식": "현금",
            "카드번호": None,
        })

        self.assertEqual(truth["merchant"], "(주)바늘이야기-팝업스토어")
        self.assertEqual(truth["transaction_date"], "2025-12-14")
        self.assertEqual(truth["total_amount"], 81300)
        self.assertEqual(truth["payment_method"], "현금")
        self.assertEqual(truth["items"][1], {
            "name": "알파카 실", "unit_price": 12600, "quantity": 6, "total_amount": 75600,
        })
        self.assertNotIn("document_type", truth)
        self.assertNotIn("expense_category", truth)

    def test_scores_model_schema_against_normalized_korean_truth(self):
        truth = normalize_ground_truth({
            "가게명": "테스트 상점",
            "구매일자": "2025-12-14 19:19:46",
            "총 결제액": 6000,
            "결제방식": "현금",
            "구매물품": [{"상품명": "상품", "단가": 6000, "수량": 1, "금액": 6000}],
            "카테고리": "취미/쇼핑",
        })
        score = score_fields({
            "document_type": "PURCHASE_REQUEST",
            "expense_category": "비품",
            "merchant": "테스트 상점",
            "transaction_date": "2025-12-14",
            "total_amount": 6000,
            "payment_method": "현금",
            "items": [{"name": "상품", "unit_price": 6000.0, "quantity": 1.0, "total_amount": 6000}],
        }, truth)

        self.assertEqual(score["evaluated_fields"], 9)
        self.assertEqual(score["correct_fields"], 9)
        self.assertNotIn("document_type", score["fields"])
        self.assertNotIn("expense_category", score["fields"])

    def test_scores_only_truth_fields_and_normalizes_text(self):
        score = score_fields(
            {"document_type": "expense_report", "merchant": "  테스트  카페 ", "total_amount": 11000},
            {"document_type": "EXPENSE_REPORT", "merchant": "테스트 카페", "total_amount": 11000},
        )
        self.assertEqual(score["evaluated_fields"], 3)
        self.assertEqual(score["correct_fields"], 3)
        self.assertTrue(score["complete_match"])

    def test_verifies_expected_finance_sheet(self):
        result = verify_workbook({
            "document_type": "EXPENSE_REPORT", "expense_category": "회의비", "merchant": "테스트카페",
            "transaction_date": "2026-08-19", "supply_amount": 10000, "tax_amount": 1000,
            "total_amount": 11000, "payment_method": "법인카드", "description": "미팅", "structured_data": {},
        })
        self.assertTrue(result["success"])
        self.assertEqual(result["active_sheet"], "경비지출결의서")
        self.assertEqual(result["preview"]["headers"][0:3], ["No", "결제일시", "상호명(가맹점)"])
        self.assertEqual(result["preview"]["rows"][0][2], "테스트카페")

    def test_scores_receipt_items(self):
        score = score_fields(
            {"items": [{"name": "경유", "quantity": 48.936, "unit_price": 1410, "total_amount": 69000}]},
            {"items": [{"name": "경유", "quantity": 48.936, "unit_price": 1410, "total_amount": 69000}]},
        )
        self.assertEqual(score["evaluated_fields"], 5)
        self.assertEqual(score["correct_fields"], 5)
        self.assertTrue(score["complete_match"])

    def test_normalizes_payment_date_merchant_and_formatted_numbers(self):
        score = score_fields(
            {
                "merchant": "바늘이야기",
                "transaction_date": "2025/12/14",
                "total_amount": "81,300원",
                "payment_method": "CASH",
            },
            {
                "merchant": "(주) 바늘이야기",
                "transaction_date": "2025-12-14",
                "total_amount": 81300,
                "payment_method": "현금",
            },
        )

        self.assertEqual(score["correct_fields"], 4)
        self.assertTrue(score["complete_match"])

    def test_matches_items_independent_of_order_and_allows_similar_names(self):
        score = score_fields(
            {"items": [
                {"name": "알파카 실 50g", "quantity": 6, "unit_price": 12600, "total_amount": 75600},
                {"name": "스카프 도안", "quantity": 1, "unit_price": 6000, "total_amount": 6000},
            ]},
            {"items": [
                {"name": "스카프 도안 상품", "quantity": 1, "unit_price": 6000, "total_amount": 6000},
                {"name": "알파카 실", "quantity": 6, "unit_price": 12600, "total_amount": 75600},
            ]},
        )

        self.assertEqual(score["correct_fields"], 9)
        self.assertEqual(score["fields"]["items"]["items"][0]["matched_actual_index"], 1)
        self.assertTrue(score["complete_match"])

    def test_penalizes_extra_predicted_items_with_item_count_field(self):
        score = score_fields(
            {"items": [
                {"name": "택시 이용", "quantity": 1, "unit_price": 96200, "total_amount": 96200},
                {"name": "잘못 추가된 품목", "quantity": 1, "unit_price": 100, "total_amount": 100},
            ]},
            {"items": [{"name": "택시 이용", "quantity": 1, "unit_price": 96200, "total_amount": 96200}]},
        )

        self.assertEqual(score["correct_fields"], 4)
        self.assertEqual(score["evaluated_fields"], 5)
        self.assertEqual(score["fields"]["items"]["false_positive_count"], 1)
        self.assertFalse(score["complete_match"])


    def test_korean_date_is_recognized_as_ocr_evidence(self):
        truth = {"transaction_date": "2016-09-18"}
        score = score_fields({"transaction_date": "2023-09-18"}, truth)
        impact = estimate_ocr_impact(
            "[등록]2016년09월 18일(일)12:55 POSNo.01", truth, score,
        )

        self.assertTrue(impact["fields"][0]["ocr_evidence_found"])
        self.assertEqual(impact["fields"][0]["status"], "LIKELY_LLM_ERROR")


if __name__ == "__main__":
    unittest.main()
