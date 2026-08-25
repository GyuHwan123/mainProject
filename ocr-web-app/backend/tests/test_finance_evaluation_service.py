from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.finance_evaluation_service import estimate_ocr_impact, normalize_ground_truth, score_fields, verify_workbook  # noqa: E402


class FinanceEvaluationServiceTests(unittest.TestCase):
    def test_treats_haircut_and_beauty_service_as_same_item(self):
        score = score_fields(
            {"items": [{"name": "헤어컷", "quantity": 1, "unit_price": 140000, "total_amount": 140000}]},
            {"items": [{"name": "미용 서비스", "quantity": 1, "unit_price": 140000, "total_amount": 140000}]},
        )

        self.assertTrue(score["fields"]["items"]["items"][0]["fields"]["name"]["correct"])

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

    def test_normalizes_korean_ground_truth_for_selection_rubric(self):
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
        self.assertEqual(truth["total_quantity"], 7)
        self.assertEqual(truth["expense_category"], "취미/쇼핑")
        self.assertIsNone(truth["discount_amount"])
        self.assertIsNone(truth["card_number"])
        self.assertEqual(truth["items"][1], {
            "name": "알파카 실", "unit_price": 12600, "quantity": 6, "total_amount": 75600,
        })
        self.assertNotIn("document_type", truth)

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
            "expense_category": "취미/쇼핑",
            "merchant": "테스트 상점",
            "transaction_date": "2025-12-14",
            "total_amount": 6000,
            "payment_method": "현금",
            "items": [{"name": "상품", "unit_price": 6000.0, "quantity": 1.0, "total_amount": 6000}],
        }, truth)

        self.assertEqual(score["evaluated_fields"], 11)
        self.assertEqual(score["correct_fields"], 11)
        self.assertNotIn("document_type", score["fields"])
        self.assertIn("expense_category", score["fields"])

    def test_calculates_test01_test20_weighted_selection_rubric(self):
        truth = normalize_ground_truth({
            "가게명": "테스트 상점", "구매일자": "2025-10-03", "총 물품 수량": 1,
            "총 결제액": 6000, "카테고리": "식비", "결제방식": "카드", "카드번호": None,
            "구매물품": [{"상품명": "국수", "단가": 6000, "수량": 1, "금액": 6000}],
        })
        prediction = {
            "merchant": "테스트 상점", "transaction_date": "2025-10-03", "total_quantity": 1,
            "discount_amount": None, "total_amount": 6000, "expense_category": "식비",
            "payment_method": "신한카드", "card_number": None,
            "items": [{"name": "국수", "unit_price": 6000, "quantity": 1, "total_amount": 6000}],
        }
        raw = {
            "image": "test01.jpg", "merchant": "테스트 상점", "transaction_date": "2025-10-03",
            "items": prediction["items"], "total_quantity": 1, "total_amount": 6000,
            "expense_category": "식비", "payment_method": "신한카드", "card_number": None,
        }

        rubric = score_fields(prediction, truth, raw)["selection_rubric"]

        self.assertEqual(rubric["extraction_score"], 95)
        self.assertEqual(rubric["schema_rate"], 1)
        self.assertTrue(rubric["total_amount_correct"])

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
        self.assertEqual(result["preview"]["headers"][0:4], ["영수증 ID", "품목 순번", "결제일시", "상호명(가맹점)"])
        self.assertEqual(result["preview"]["rows"][0][3], "테스트카페")
        self.assertIn("경비지출결의서", result["sheet_previews"])
        self.assertIn("영수증요약", result["sheet_previews"])
        self.assertEqual(result["sheet_previews"]["영수증요약"]["headers"][0], "영수증 ID")

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

    def test_treats_card_and_credit_card_as_same_payment_method(self):
        score = score_fields(
            {"payment_method": "신용카드"},
            {"payment_method": "카드"},
        )

        self.assertEqual(score["correct_fields"], 1)
        self.assertTrue(score["complete_match"])

    def test_treats_card_issuer_name_and_card_as_same_payment_method(self):
        score = score_fields(
            {"payment_method": "신한카드"},
            {"payment_method": "카드"},
        )

        self.assertEqual(score["correct_fields"], 1)
        self.assertTrue(score["complete_match"])

    def test_keeps_issuer_check_card_distinct_from_credit_card(self):
        score = score_fields(
            {"payment_method": "신한체크카드"},
            {"payment_method": "카드"},
        )

        self.assertEqual(score["correct_fields"], 0)
        self.assertFalse(score["complete_match"])

    def test_treats_aladin_used_bookstore_name_as_same_merchant(self):
        score = score_fields(
            {"merchant": "알라딘 합정점"},
            {"merchant": "알라딘 중고서점 합정점"},
        )

        self.assertEqual(score["correct_fields"], 1)
        self.assertTrue(score["complete_match"])

    def test_does_not_ignore_aladin_branch_name(self):
        score = score_fields(
            {"merchant": "알라딘 강남점"},
            {"merchant": "알라딘 중고서점 합정점"},
        )

        self.assertEqual(score["correct_fields"], 0)
        self.assertFalse(score["complete_match"])

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

    def test_matches_multilingual_item_name_aliases(self):
        score = score_fields(
            {
                "items": [{
                    "name": "HAIR SALON",
                    "quantity": 1,
                    "unit_price": 140000,
                    "total_amount": 140000,
                }],
            },
            {
                "items": [{
                    "name": "미용 서비스",
                    "quantity": 1,
                    "unit_price": 140000,
                    "total_amount": 140000,
                }],
            },
        )

        self.assertTrue(score["fields"]["items"]["items"][0]["fields"]["name"]["correct"])
        self.assertTrue(score["complete_match"])

    def test_does_not_match_unlisted_cross_language_item_names(self):
        score = score_fields(
            {"items": [{"name": "NAIL SALON"}]},
            {"items": [{"name": "미용 서비스"}]},
        )

        self.assertFalse(score["fields"]["items"]["items"][0]["fields"]["name"]["correct"])

    def test_allows_minor_merchant_ocr_typo_and_corporate_notation(self):
        score = score_fields(
            {"merchant": "주)바늘이야기-법업스토어"},
            {"merchant": "(주)바늘이야기-팝업스토어"},
        )

        self.assertTrue(score["complete_match"])

    def test_matches_item_across_word_order_bilingual_and_descriptor_differences(self):
        score = score_fields(
            {"items": [{"name": "알파카 페루 베텔린 스카프(Brushed Alpaca Peru)"}]},
            {"items": [{"name": "[DIY] 브러쉬드 알파카 페루 베텔린 스카프 (도안)"}]},
        )

        self.assertTrue(score["fields"]["items"]["items"][0]["fields"]["name"]["correct"])

    def test_keeps_different_numeric_product_variants_strict(self):
        score = score_fields(
            {"items": [{"name": "케이블 2m"}]},
            {"items": [{"name": "케이블 3m"}]},
        )

        self.assertFalse(score["fields"]["items"]["items"][0]["fields"]["name"]["correct"])


if __name__ == "__main__":
    unittest.main()
