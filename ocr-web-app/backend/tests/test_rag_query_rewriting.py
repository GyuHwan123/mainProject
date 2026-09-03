import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.services.rag_service import rewrite_query


class QueryRewriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_numeric_values(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"response": '{"query":"취업규칙 기본 근무시간 09:00부터 18:00"}'}
        client = AsyncMock()
        client.post.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client

        with (
            patch("app.services.rag_service.QUERY_REWRITING_ENABLED", True),
            patch("app.services.rag_service.httpx.AsyncClient", return_value=context),
        ):
            result = await rewrite_query("기본 근무시간은 09:00부터 18:00까지인가요?")

        self.assertEqual(result["status"], "rewritten")
        self.assertIn("09:00", result["query"])
        self.assertIn("18:00", result["query"])

    async def test_falls_back_when_model_invents_number(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"response": '{"query":"숙박비 한도 200000원"}'}
        client = AsyncMock()
        client.post.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client

        with (
            patch("app.services.rag_service.QUERY_REWRITING_ENABLED", True),
            patch("app.services.rag_service.httpx.AsyncClient", return_value=context),
        ):
            result = await rewrite_query("숙박비 한도는 얼마인가요?")

        self.assertEqual(result["status"], "fallback")
        self.assertEqual(result["query"], "숙박비 한도는 얼마인가요?")

    async def test_disabled_mode_does_not_call_ollama(self):
        with (
            patch("app.services.rag_service.QUERY_REWRITING_ENABLED", False),
            patch("app.services.rag_service.httpx.AsyncClient") as client,
        ):
            result = await rewrite_query("근무시간은 언제인가요?")

        self.assertEqual(result["status"], "disabled")
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
