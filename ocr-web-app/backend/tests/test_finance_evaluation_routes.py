import sys
import threading
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes.finance_evaluations import (  # noqa: E402
    get_finance_monitoring,
    _pipeline_trace,
    _prediction_from_finance_record,
    _raw_prediction_from_trace,
)
from app.services.finance_pipeline import FINANCE_PIPELINE_VERSION  # noqa: E402


class FinanceEvaluationRouteTests(unittest.TestCase):
    def test_current_and_comparison_periods_load_concurrently(self):
        barrier = threading.Barrier(2)
        ranges = []

        def load(_email, *, start_at, end_at, model_name=None):
            ranges.append((start_at, end_at, model_name))
            barrier.wait(timeout=2)
            return {"evaluations": [], "items": [], "batches": []}

        with patch(
            "app.api.routes.finance_evaluations.supabase_service.list_finance_monitoring_data",
            side_effect=load,
        ):
            result = get_finance_monitoring(
                date(2026, 9, 1), date(2026, 9, 7),
                user=SimpleNamespace(email="developer@example.com"),
            )

        self.assertEqual(len(ranges), 2)
        self.assertEqual(result["summary"]["total_count"], 0)

    def test_current_semantic_receipt_pipeline_is_v2_5(self):
        self.assertEqual(FINANCE_PIPELINE_VERSION, "v2.5")

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

    def test_pipeline_trace_collects_simple_one_call_diagnostics(self):
        trace = _pipeline_trace({
            "llm_trace": {"raw_output": {"merchant": "문구점"}, "call_count": 1},
            "automation_validation": {"decision": "PASS", "reasons": []},
        })

        self.assertEqual(trace["llm"]["raw_output"]["merchant"], "문구점")
        self.assertEqual(trace["llm"]["call_count"], 1)
        self.assertEqual(trace["validation"]["decision"], "PASS")
        self.assertNotIn("item_candidates", trace)

    def test_raw_prediction_uses_single_model_output(self):
        raw = _raw_prediction_from_trace({
            "llm": {
                "raw_output": {"merchant": "store", "items": [{"name": "item"}]},
            },
        }, {"items": [{"name": "fallback"}]})

        self.assertEqual(raw["merchant"], "store")
        self.assertIsInstance(raw["items"], list)
        self.assertEqual(raw["items"][0]["name"], "item")


if __name__ == "__main__":
    unittest.main()
