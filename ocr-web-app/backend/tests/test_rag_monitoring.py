import unittest
from datetime import date
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import rag_monitoring as routes
from app.api.routes.auth import require_current_user
from app.models.user import User
from app.services.supabase_base import SupabaseBase
from app.services.supabase_rag_evaluation_repository import RagEvaluationMixin


class RagMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(routes.router, prefix="/rag")
        self.user = User(id="owner", name="Owner", email="owner@example.com", role="DEVELOPER")
        self.app.dependency_overrides[require_current_user] = lambda: self.user
        self.client = TestClient(self.app)

    def test_daily_kst_boundaries_run_averages_and_missing_day(self):
        rows = [
            {"id": "b", "evaluated_at": "2026-09-07T00:00:00Z", "question_count": 200, "completed_count": 200, "answer_accuracy": 1, "hallucination_rate": 0, "summary_metrics": {"top_k": 4}},
            {"id": "a", "evaluated_at": "2026-09-06T15:00:00Z", "question_count": 100, "completed_count": 100, "answer_accuracy": 0, "hallucination_rate": 1, "summary_metrics": {"top_k": 3}},
        ]
        with patch.object(routes.supabase_service, "list_rag_evaluation_runs", return_value=rows) as query:
            response = self.client.get('/rag/evaluation/monitoring?start_date=2026-09-06&end_date=2026-09-07')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        query.assert_called_once_with("owner@example.com", "2026-09-06T00:00:00+09:00", "2026-09-08T00:00:00+09:00")
        self.assertEqual(data["summary"]["total"], 300)
        self.assertEqual(data["summary"]["answer_accuracy"], .5)
        self.assertIsNone(data["summary"]["top_k"])
        self.assertIsNone(data["daily"][0]["answer_accuracy"])
        self.assertEqual(data["daily"][1]["run_count"], 2)
        self.assertEqual([row["id"] for row in data["recent_runs"]], ["b", "a"])

    def test_empty_history_does_not_fabricate_zero_scores(self):
        with patch.object(routes.supabase_service, "list_rag_evaluation_runs", return_value=[]):
            data = self.client.get('/rag/evaluation/monitoring?start_date=2026-09-07&end_date=2026-09-07').json()
        self.assertEqual(data["summary"]["total"], 0)
        self.assertIsNone(data["summary"]["answer_accuracy"])
        self.assertEqual(data["recent_runs"], [])

    def test_date_validation_and_role_guard_do_not_query_storage(self):
        with patch.object(routes.supabase_service, "list_rag_evaluation_runs") as query:
            for start, end in [("2026-09-07", "2026-09-06"), ("2020-01-01", "2026-09-07"), ("invalid", "2026-09-07")]:
                self.assertEqual(self.client.get(f'/rag/evaluation/monitoring?start_date={start}&end_date={end}').status_code, 422)
            self.user.role = "USER"
            self.assertEqual(self.client.get('/rag/evaluation/monitoring?start_date=2026-09-07&end_date=2026-09-07').status_code, 403)
            query.assert_not_called()

    def test_recent_limit_does_not_limit_summary(self):
        row = {"evaluated_at": "2026-09-07T00:00:00Z", "question_count": 1, "completed_count": 1, "answer_accuracy": 0}
        with patch.object(routes.supabase_service, "list_rag_evaluation_runs", return_value=[{**row, "id": str(i)} for i in range(60)]):
            data = routes.rag_monitoring(date(2026, 9, 7), date(2026, 9, 7), self.user)
        self.assertEqual(len(data["recent_runs"]), 50)
        self.assertEqual(data["summary"]["run_count"], 60)
        self.assertEqual(data["summary"]["answer_accuracy"], 0)

    def test_repository_pagination_filters_owner_and_both_date_bounds(self):
        class Repository(RagEvaluationMixin, SupabaseBase):
            pass
        repository = Repository()
        repository.get_public_user_id = Mock(return_value="owner-id")
        repository._service_headers = Mock(return_value={"apikey": "test"})
        with patch('app.services.supabase_rag_evaluation_repository.httpx.get', side_effect=[httpx.Response(200, json=[{"id": "a"}]), httpx.Response(200, json=[{"id": "b"}]), httpx.Response(200, json=[])]) as get:
            rows = repository.list_rag_evaluation_runs("owner@example.com", "start", "end")
        self.assertEqual(len(rows), 2)
        params = get.call_args_list[0].kwargs["params"]
        self.assertIn(("user_id", "eq.owner-id"), params)
        self.assertIn(("evaluated_at", "gte.start"), params)
        self.assertIn(("evaluated_at", "lt.end"), params)
        self.assertIn(("offset", "2"), get.call_args_list[2].kwargs["params"])
