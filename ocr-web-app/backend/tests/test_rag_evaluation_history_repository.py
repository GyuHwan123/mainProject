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

    def test_atomic_insert_ignores_duplicate_id_and_uses_authenticated_owner(self):
        with patch("app.services.supabase_rag_evaluation_repository.httpx.post", return_value=httpx.Response(201)) as post:
            self.repository.save_rag_evaluation_run("owner@example.com", {"id": "run-id", "user_id": "untrusted"})
        self.repository.get_public_user_id.assert_called_once_with("owner@example.com")
        request = post.call_args.kwargs
        self.assertEqual(request["params"], {"on_conflict": "id"})
        self.assertIn("resolution=ignore-duplicates", request["headers"]["Prefer"])
        self.assertEqual(request["json"]["user_id"], "owner-id")

    def test_failed_insert_is_reported_to_checkpoint_retry_layer(self):
        with patch("app.services.supabase_rag_evaluation_repository.httpx.post", return_value=httpx.Response(503, text="unavailable")):
            with self.assertRaises(HTTPException):
                self.repository.save_rag_evaluation_run("owner@example.com", {"id": "run-id"})
