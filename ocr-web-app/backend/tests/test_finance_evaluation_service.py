from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.finance_evaluation_service import score_fields, verify_workbook  # noqa: E402


class FinanceEvaluationServiceTests(unittest.TestCase):
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

    def test_scores_receipt_items(self):
        score = score_fields(
            {"items": [{"name": "경유", "quantity": 48.936, "unit_price": 1410, "total_amount": 69000}]},
            {"items": [{"name": "경유", "quantity": 48.936, "unit_price": 1410, "total_amount": 69000}]},
        )
        self.assertEqual(score["evaluated_fields"], 4)
        self.assertEqual(score["correct_fields"], 4)
        self.assertTrue(score["complete_match"])


if __name__ == "__main__":
    unittest.main()
