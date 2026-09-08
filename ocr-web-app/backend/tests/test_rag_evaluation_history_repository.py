import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi import HTTPException

from app.services.supabase_rag_evaluation_repository import RagEvaluationMixin
from app.services.supabase_base import SupabaseBase


class Repository(RagEvaluationMixin, SupabaseBase):
    pass


class RagHistoryRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository()
        self.repository.url = "https://test.invalid"
        self.repository.get_public_user_id = Mock(return_value="owner-id")
        self.repository._service_headers = Mock(return_value={"apikey": "test-only"})

    def test_atomic_insert_ignores_duplicate_run_id_and_uses_authenticated_owner(self):
        with patch("app.services.supabase_rag_evaluation_repository.httpx.post", return_value=httpx.Response(201)) as post:
            self.repository.save_rag_evaluation_run("owner@example.com", {"id": "history-id", "run_id": "run-id", "user_id": "untrusted"})
        self.repository.get_public_user_id.assert_called_once_with("owner@example.com")
        request = post.call_args.kwargs
        self.assertEqual(request["params"], {"on_conflict": "run_id"})
        self.assertIn("resolution=ignore-duplicates", request["headers"]["Prefer"])
        self.assertEqual(request["json"]["user_id"], "owner-id")
        self.assertEqual(request["json"]["run_id"], "run-id")

    def test_failed_insert_is_reported_to_checkpoint_retry_layer(self):
        with patch("app.services.supabase_rag_evaluation_repository.httpx.post", return_value=httpx.Response(503, text="unavailable")):
            with self.assertRaises(HTTPException):
                self.repository.save_rag_evaluation_run("owner@example.com", {"id": "run-id"})

    def test_detail_query_always_filters_owner_and_run_id(self):
        with patch('app.services.supabase_rag_evaluation_repository.httpx.get', return_value=httpx.Response(200, json=[])) as get:
            self.assertIsNone(self.repository.get_rag_evaluation_run('owner@example.com', 'run-id'))
        self.assertEqual(get.call_args.kwargs['params']['user_id'], 'eq.owner-id')
        self.assertEqual(get.call_args.kwargs['params']['run_id'], 'eq.run-id')
