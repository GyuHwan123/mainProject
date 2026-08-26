import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes.finance_evaluations import _evaluation_question_prompt, _prediction_from_finance_record  # noqa: E402


class FinanceEvaluationRouteTests(unittest.TestCase):
    def test_record_prediction_preserves_structured_schema_and_quantity(self):
        prediction, raw = _prediction_from_finance_record({
            "merchant": "문구점",
            "total_amount": 5000,
            "structured_data": {
                "source_filename": "receipt.jpg",
                "merchant": "문구점",
                "items": [{"name": "노트", "quantity": 1, "unit_price": 5000, "total_amount": 5000}],
                "receipt_summary": {"stated_total_quantity": 1},
            },
        })

        self.assertEqual(prediction["total_quantity"], 1)
        self.assertEqual(prediction["items"][0]["name"], "노트")
        self.assertEqual(raw["source_filename"], "receipt.jpg")

    def test_question_prompt_receives_tables_candidates_and_amount_relation(self):
        text = "상품합계 3,900원\n할인금액 780원\n최종 결제금액 3,120원\n볼펜 1 3,900"
        pages = [{
            "page": 1,
            "text": text,
            "tables": [{"confidence": .95, "rows": [["볼펜", "1", "3,900"]]}],
        }]

        prompt = _evaluation_question_prompt(text, "무엇을 샀어?", pages, "receipt.jpg")

        self.assertIn("GROSS_MINUS_DISCOUNT_EQUALS_PAID", prompt)
        self.assertIn("품목 행 후보", prompt)
        self.assertIn("볼펜", prompt)


if __name__ == "__main__":
    unittest.main()
