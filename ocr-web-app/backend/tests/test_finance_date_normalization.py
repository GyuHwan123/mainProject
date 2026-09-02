from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.finance_evaluation_scoring import normalize_ground_truth, score_fields
from app.services.finance_normalization import normalize_date
from app.services.finance_receipt_simple import _simple_validation


class FinanceDateNormalizationTests(unittest.TestCase):
    def test_normalizes_unambiguous_year_first_formats(self):
        equivalents = (
            "2024/12/11",
            "2024-12-11",
            "2024.12.11",
            "20241211",
            "2024년 12월 11일",
            "2024-12-11 14:30:20",
            "24/12/11",
            "12/11/2024",
        )
        self.assertEqual({normalize_date(value) for value in equivalents}, {"2024-12-11"})

    def test_rejects_ambiguous_or_invalid_dates(self):
        for value in ("2024-13-11", "20240230", "not-a-date"):
            self.assertIsNone(normalize_date(value))

    def test_pipeline_canonicalizes_model_date(self):
        result = {
            "merchant": "테스트 상점",
            "transaction_date": "20241211",
            "expense_category": "식품/장보기",
            "total_amount": 12000,
            "items": [],
        }
        validation = _simple_validation(result, "테스트 상점 2024/12/11 결제금액 12,000")
        self.assertEqual(result["transaction_date"], "2024-12-11")
        self.assertNotIn("INVALID_TRANSACTION_DATE", validation["reasons"])

    def test_evaluation_compares_equivalent_date_formats(self):
        score = score_fields(
            {"transaction_date": "20241211"},
            {"transaction_date": "2024/12/11"},
        )
        self.assertTrue(score["fields"]["transaction_date"]["correct"])
        self.assertEqual(normalize_ground_truth({"transaction_date": "2024년 12월 11일"})["transaction_date"], "2024-12-11")


if __name__ == "__main__":
    unittest.main()
