import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.api.routes.chatbot import ChatMessage, _ask_chatbot, _table_structure_answer


CONTEXT = """[근거 1 · 표 이미지.jpg · 1페이지 · Chunk 1] [문서 제목] 표1. 연구수행 결과표
[표 크기] 헤더 포함 8행 × 4열
[표 테이블 열 컬럼명] 1열: 헤더 없음(행 구분) | 2열: 평균 | 3열: 표준편차 | 4열: 평균
[표 행] 1열(행 구분·헤더 없음): 실험1 | 2열(평균): 77.40

[근거 2 · 표 이미지.jpg · 1페이지 · Chunk 2] 다른 근거"""


class TableStructureAnswerTests(unittest.TestCase):
    def test_returns_all_columns_and_dimensions(self):
        answer = _table_structure_answer("테이블의 컬럼명을 다 알려주세요", CONTEXT)
        self.assertIn("8행 × 4열", answer)
        self.assertIn("1열: 헤더 없음(행 구분)", answer)
        self.assertIn("2열: 평균", answer)
        self.assertIn("3열: 표준편차", answer)
        self.assertIn("4열: 평균", answer)
        self.assertTrue(answer.endswith("[근거 1]"))

    def test_does_not_intercept_regular_table_value_question(self):
        self.assertIsNone(_table_structure_answer("실험1의 평균은 얼마인가요?", CONTEXT))


class TableStructureChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_skips_llm_for_explicit_schema_question(self):
        payload = ChatMessage(message="이 표는 몇 행 몇 열인가요?", context=CONTEXT)
        with patch("app.api.routes.chatbot.generate", new_callable=AsyncMock) as generate:
            reply = await _ask_chatbot(payload, Mock())
        self.assertEqual(reply.model, "table-metadata")
        self.assertIn("8행 × 4열", reply.reply)
        generate.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
