import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.api.routes.chatbot import (
    ChatMessage,
    _ask_chatbot,
    _document_title_answer,
    _labeled_fact_answer,
    _table_structure_answer,
)


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

    def test_answers_regular_table_value_question_from_the_matching_row(self):
        answer = _table_structure_answer("실험1의 평균은 얼마인가요?", CONTEXT)
        self.assertIn("77.40", answer)
        self.assertTrue(answer.endswith("[근거 1]"))

    def test_recognizes_column_composition_without_explicit_table_word(self):
        answer = _table_structure_answer("열 구성이 어떻게 되나요?", CONTEXT)
        self.assertIn("1열: 헤더 없음(행 구분)", answer)
        self.assertIn("4열: 평균", answer)


class DocumentTitleAnswerTests(unittest.TestCase):
    def test_returns_filename_fallback_title_from_metadata(self):
        context = "[근거 1 · 분기 보고서.pdf · 1페이지 · Chunk 1] [문서 제목] 분기 보고서\n본문"
        answer = _document_title_answer("이 문서 제목은 무엇인가요?", context)
        self.assertEqual(answer, "문서 제목은 분기 보고서입니다. [근거 1]")


class LabeledFactAnswerTests(unittest.TestCase):
    context = "[근거 1 · 회의 계획.pdf · 1페이지 · Chunk 1] 최종 점검 회의\n회의 시간: 14:00 / 담당 부서: 경영지원팀"

    def test_meeting_time_paraphrases_have_the_same_answer(self):
        expected = "회의 시간은 14:00입니다. [근거 1]"
        self.assertEqual(_labeled_fact_answer("최종 점검 회의는 몇 시에 진행해?", self.context), expected)
        self.assertEqual(_labeled_fact_answer("최종 점검 회의는 언제야?", self.context), expected)

    def test_department_paraphrases_have_the_same_answer(self):
        expected = "담당 부서는 경영지원팀입니다. [근거 1]"
        self.assertEqual(_labeled_fact_answer("담당 부서는 어디야?", self.context), expected)
        self.assertEqual(_labeled_fact_answer("담당 부서는?", self.context), expected)


class TableStructureChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_skips_llm_for_explicit_schema_question(self):
        payload = ChatMessage(message="이 표는 몇 행 몇 열인가요?", context=CONTEXT)
        with patch("app.api.routes.chatbot.generate", new_callable=AsyncMock) as generate:
            reply = await _ask_chatbot(payload, Mock())
        self.assertEqual(reply.model, "table-metadata")
        self.assertIn("8행 × 4열", reply.reply)
        generate.assert_not_awaited()

    async def test_skips_llm_for_filename_fallback_title(self):
        payload = ChatMessage(
            message="이 문서 제목은 무엇인가요?",
            context="[근거 1 · 제목없는파일.pdf · 1페이지 · Chunk 1] [문서 제목] 제목없는파일\n본문",
        )
        with patch("app.api.routes.chatbot.generate", new_callable=AsyncMock) as generate:
            reply = await _ask_chatbot(payload, Mock())
        self.assertEqual(reply.model, "document-metadata")
        self.assertEqual(reply.reply, "문서 제목은 제목없는파일입니다. [근거 1]")
        generate.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
