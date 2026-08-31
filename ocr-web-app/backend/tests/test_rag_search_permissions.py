import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.services.rag_service import can_access_company_rag
from app.services.supabase_service import COMPANY_RAG_DOCUMENT_IDS, SupabaseService


class RagSearchPermissionTests(unittest.TestCase):
    def test_legacy_supabase_module_exports_company_rag_document_ids(self):
        self.assertIn("HR-001", COMPANY_RAG_DOCUMENT_IDS)

    def setUp(self) -> None:
        self.service = SupabaseService()
        self.service.url = "https://example.supabase.co"
        self.service.service_role_key = "test-secret"
        self.personal_document = {
            "id": "11111111-1111-1111-1111-111111111111",
            "doc_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "owner": "user@example.com",
            "filename": "personal.pdf",
        }
        self.company_document = {
            "id": "22222222-2222-2222-2222-222222222222",
            "doc_id": "HR-001",
            "owner": "인사팀",
            "filename": "company.pdf",
        }

    def _response(self, rows):
        response = Mock(status_code=200, text="")
        response.json.return_value = rows
        return response

    @patch("app.services.supabase_service.httpx.post")
    @patch("app.services.supabase_service.httpx.get")
    def test_enterprise_search_passes_company_and_personal_ids_to_rpc(self, mock_get, mock_post):
        self.service.list_rag_documents = Mock(return_value=[self.personal_document.copy()])
        mock_get.return_value = self._response([self.company_document.copy()])
        mock_post.return_value = self._response([])

        self.service.search_rag_chunks(
            "user@example.com", [0.1], None, 4, include_company_documents=True,
        )

        payload = mock_post.call_args.kwargs["json"]
        self.assertCountEqual(payload["allowed_document_ids"], [
            self.personal_document["id"], self.company_document["id"],
        ])
        self.assertEqual(payload["match_count"], 4)

    @patch("app.services.supabase_service.httpx.post")
    @patch("app.services.supabase_service.httpx.get")
    def test_personal_search_passes_only_personal_ids_to_rpc(self, mock_get, mock_post):
        self.service.list_rag_documents = Mock(return_value=[self.personal_document.copy()])
        mock_post.return_value = self._response([])

        self.service.search_rag_chunks(
            "user@example.com", [0.1], None, 4, include_company_documents=False,
        )

        mock_get.assert_not_called()
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["allowed_document_ids"], [self.personal_document["id"]])

    @patch("app.services.supabase_service.httpx.post")
    @patch("app.services.supabase_service.httpx.get")
    def test_selected_document_restricts_rpc_to_accessible_selection(self, mock_get, mock_post):
        self.service.list_rag_documents = Mock(return_value=[self.personal_document.copy()])
        mock_get.return_value = self._response([self.company_document.copy()])
        mock_post.return_value = self._response([])

        self.service.search_rag_chunks(
            "user@example.com", [0.1], self.company_document["id"], 4,
            include_company_documents=True,
        )

        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["allowed_document_ids"], [self.company_document["id"]])

    @patch("app.services.supabase_service.httpx.post")
    def test_inaccessible_selected_document_is_rejected_before_rpc(self, mock_post):
        self.service.list_rag_documents = Mock(return_value=[self.personal_document.copy()])

        with self.assertRaises(HTTPException) as context:
            self.service.search_rag_chunks(
                "user@example.com", [0.1], self.company_document["id"], 4,
                include_company_documents=False,
            )

        self.assertEqual(context.exception.status_code, 404)
        mock_post.assert_not_called()

    def test_role_and_subscription_policy(self):
        self.assertTrue(can_access_company_rag("DEVELOPER", "PERSONAL"))
        self.assertTrue(can_access_company_rag("USER", "ENTERPRISE"))
        self.assertFalse(can_access_company_rag("USER", "PERSONAL"))


if __name__ == "__main__":
    unittest.main()
