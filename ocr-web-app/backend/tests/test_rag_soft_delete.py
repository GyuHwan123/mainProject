import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.services.supabase_service import SupabaseService


def response(rows, status_code=200):
    result = Mock(status_code=status_code, text="")
    result.json.return_value = rows
    return result


class RagSoftDeleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SupabaseService()
        self.service.url = "https://example.supabase.co"
        self.service.service_role_key = "test-secret"

    @patch("app.services.supabase_service.httpx.delete")
    @patch("app.services.supabase_service.httpx.patch")
    @patch("app.services.supabase_service.httpx.get")
    def test_delete_rag_document_sets_deleted_at_without_deleting_rows(
        self, mock_get, mock_patch, mock_delete,
    ):
        mock_get.return_value = response([{"id": "rag-id"}])
        mock_patch.return_value = response([{"id": "rag-id", "deleted_at": "set"}])

        self.service.delete_rag_document("user@example.com", "rag-id")

        mock_delete.assert_not_called()
        self.assertIsNotNone(mock_patch.call_args.kwargs["json"]["deleted_at"])
        self.assertEqual(mock_patch.call_args.kwargs["params"]["deleted_at"], "is.null")

    @patch("app.services.supabase_service.httpx.get")
    def test_list_rag_documents_excludes_deleted_documents(self, mock_get):
        mock_get.return_value = response([])

        self.assertEqual(self.service.list_rag_documents("user@example.com"), [])

        self.assertEqual(mock_get.call_args.kwargs["params"]["deleted_at"], "is.null")

    @patch("app.services.supabase_service.httpx.get")
    def test_deleted_rag_document_is_blocked_from_direct_access(self, mock_get):
        mock_get.return_value = response([])

        with self.assertRaises(HTTPException) as raised:
            self.service.get_accessible_rag_document("user@example.com", "rag-id")

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(mock_get.call_args.kwargs["params"]["deleted_at"], "is.null")

    @patch("app.services.supabase_service.httpx.delete")
    @patch("app.services.supabase_service.httpx.post")
    def test_reupload_restores_soft_deleted_document(self, mock_post, mock_delete):
        mock_post.side_effect = [
            response([{"id": "rag-id", "doc_id": "ocr-id", "filename": "sample.pdf"}]),
            response([]),
        ]
        mock_delete.return_value = response([])

        result = self.service.replace_rag_index(
            user_email="user@example.com",
            document={"id": "ocr-id", "file_name": "sample.pdf"},
            chunks=[{"page_number": 1, "content": "content"}],
            embeddings=[[0.1, 0.2]],
            embedding_model="BAAI/bge-m3",
        )

        self.assertIsNone(mock_post.call_args_list[0].kwargs["json"]["deleted_at"])
        self.assertEqual(result["id"], "rag-id")


class ChatSoftDeleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SupabaseService()
        self.service.url = "https://example.supabase.co"
        self.service.service_role_key = "test-secret"

    @patch("app.services.supabase_service.httpx.delete")
    @patch("app.services.supabase_service.httpx.patch")
    @patch("app.services.supabase_service.httpx.get")
    def test_chat_session_soft_delete_remains_enabled(self, mock_get, mock_patch, mock_delete):
        mock_get.side_effect = [response([{"id": "user-id"}]), response([{"id": "session-id", "user_id": "user-id"}])]
        mock_patch.return_value = response([{"id": "session-id", "deleted_at": "set"}])

        self.service.delete_chat_session("user@example.com", "session-id")

        mock_delete.assert_not_called()
        self.assertIsNotNone(mock_patch.call_args.kwargs["json"]["deleted_at"])
        self.assertEqual(mock_get.call_args_list[1].kwargs["params"]["deleted_at"], "is.null")


if __name__ == "__main__":
    unittest.main()
