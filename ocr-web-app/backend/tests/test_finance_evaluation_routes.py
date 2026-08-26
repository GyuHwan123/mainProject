import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes.finance_evaluations import _prediction_from_finance_record  # noqa: E402


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

if __name__ == "__main__":
    unittest.main()
