import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from app.services import document_summary_service
from app.services.supabase_service import SupabaseService


class DocumentSummaryServiceTests(unittest.IsolatedAsyncioTestCase):
    @patch.object(document_summary_service, "_summarize_chunks", new_callable=AsyncMock)
    @patch.object(document_summary_service, "supabase_service")
    async def test_cached_summary_skips_chunks_llm_and_write(self, mock_supabase, mock_summarize):
        mock_supabase.get_accessible_rag_document.return_value = {"summary": "  저장된 요약  "}

        result = await document_summary_service.get_or_create_document_summary(
            "user@example.com", "rag-id", user_role="USER", subscription_tier="PERSONAL",
        )

        self.assertEqual(result, {"document_id": "rag-id", "summary": "저장된 요약", "cached": True})
        mock_supabase.list_all_rag_chunks.assert_not_called()
        mock_summarize.assert_not_awaited()
        mock_supabase.save_rag_document_summary.assert_not_called()

    @patch.object(document_summary_service, "_summarize_chunks", new_callable=AsyncMock)
    @patch.object(document_summary_service, "supabase_service")
    async def test_missing_summary_reads_all_chunks_generates_and_saves(self, mock_supabase, mock_summarize):
        mock_supabase.get_accessible_rag_document.return_value = {"summary": None}
        chunks = [{"chunk_index": 0, "content": "첫 번째"}, {"chunk_index": 1, "content": "두 번째"}]
        mock_supabase.list_all_rag_chunks.return_value = chunks
        mock_summarize.return_value = "생성된 요약"

        result = await document_summary_service.get_or_create_document_summary(
            "user@example.com", "rag-id", user_role="DEVELOPER", subscription_tier="PERSONAL",
        )

        self.assertEqual(result["summary"], "생성된 요약")
        self.assertFalse(result["cached"])
        mock_summarize.assert_awaited_once_with(chunks)
        mock_supabase.save_rag_document_summary.assert_called_once_with("rag-id", "생성된 요약")

    @patch.object(document_summary_service, "_summarize_chunks", new_callable=AsyncMock)
    @patch.object(document_summary_service, "supabase_service")
    async def test_force_regenerate_bypasses_cache_and_overwrites_after_success(self, mock_supabase, mock_summarize):
        mock_supabase.get_accessible_rag_document.return_value = {"summary": "기존 요약"}
        chunks = [{"chunk_index": 0, "content": "전체 문서 내용"}]
        mock_supabase.list_all_rag_chunks.return_value = chunks
        mock_summarize.return_value = "새 요약"

        result = await document_summary_service.get_or_create_document_summary(
            "user@example.com", "rag-id", user_role="USER", subscription_tier="PERSONAL",
            force_regenerate=True,
        )

        self.assertEqual(result, {"document_id": "rag-id", "summary": "새 요약", "cached": False})
        mock_summarize.assert_awaited_once_with(chunks)
        mock_supabase.save_rag_document_summary.assert_called_once_with("rag-id", "새 요약")

    @patch.object(document_summary_service, "_summarize_chunks", new_callable=AsyncMock)
    @patch.object(document_summary_service, "supabase_service")
    async def test_generation_failure_does_not_write_summary(self, mock_supabase, mock_summarize):
        mock_supabase.get_accessible_rag_document.return_value = {"summary": "기존 정상 요약"}
        mock_supabase.list_all_rag_chunks.return_value = [{"content": "내용"}]
        mock_summarize.side_effect = HTTPException(status_code=503, detail="문서 요약에 실패했습니다.")

        with self.assertRaises(HTTPException):
            await document_summary_service.get_or_create_document_summary(
                "user@example.com", "rag-id", user_role="USER", subscription_tier="ENTERPRISE",
                force_regenerate=True,
            )

        mock_supabase.save_rag_document_summary.assert_not_called()

    @patch.object(document_summary_service, "_generate_summary", new_callable=AsyncMock)
    async def test_long_document_uses_multiple_hierarchical_calls(self, mock_generate):
        mock_generate.return_value = "부분 요약"
        chunks = [{"content": "가" * 4_000}, {"content": "나" * 4_000}]

        result = await document_summary_service._summarize_chunks(chunks)

        self.assertEqual(result, "부분 요약")
        self.assertGreater(mock_generate.await_count, 2)


class DocumentSummaryPermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SupabaseService()
        self.service.url = "https://example.supabase.co"
        self.service.service_role_key = "test-secret"

    @staticmethod
    def _response(document):
        response = Mock(status_code=200, text="")
        response.json.return_value = [document]
        return response

    @patch("app.services.supabase_service.httpx.get")
    def test_owner_can_access_personal_document(self, mock_get):
        mock_get.return_value = self._response({
            "id": "rag-id", "doc_id": "private-id", "owner": "user@example.com", "summary": None,
        })

        document = self.service.get_accessible_rag_document("user@example.com", "rag-id")

        self.assertEqual(document["id"], "rag-id")

    @patch("app.services.supabase_service.httpx.get")
    def test_enterprise_or_developer_scope_can_access_company_document(self, mock_get):
        mock_get.return_value = self._response({
            "id": "rag-id", "doc_id": "HR-001", "owner": "전사공통", "summary": None,
        })

        document = self.service.get_accessible_rag_document(
            "user@example.com", "rag-id", include_company_documents=True,
        )

        self.assertEqual(document["doc_id"], "HR-001")

    @patch("app.services.supabase_service.httpx.get")
    def test_personal_and_other_owner_cannot_access_document(self, mock_get):
        mock_get.return_value = self._response({
            "id": "rag-id", "doc_id": "HR-001", "owner": "other@example.com", "summary": "비공개",
        })

        with self.assertRaises(HTTPException) as context:
            self.service.get_accessible_rag_document("user@example.com", "rag-id")

        self.assertEqual(context.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
