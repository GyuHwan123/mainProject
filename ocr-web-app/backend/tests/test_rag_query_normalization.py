import unittest
from unittest.mock import AsyncMock, patch

from app.services import rag_service as rag


PAIRS = [
    ("근무시간알려줘", "근무시간 알려줘", "근무시간"),
    ("근무시간알려주세요", "근무시간 알려주세요", "근무시간"),
    ("근무시간이뭐야", "근무시간이 뭐야", "근무시간"),
    ("출장비알려줘", "출장비 알려줘", "출장비"),
    ("연차휴가가뭐야", "연차휴가가 뭐야", "연차휴가"),
]


async def positive_semantics(texts):
    # Isolate lexical/condition decisions while passing semantic thresholds.
    return [[1.0, 0.0] for _ in texts], {}


class QueryNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pairs_have_identical_facets_and_gate_decisions(self):
        with patch.object(rag, "_embed_texts_cached", side_effect=positive_semantics):
            for attached, spaced, subject in PAIRS:
                with self.subTest(query=attached):
                    facets = rag._extract_evidence_facets(attached)
                    self.assertEqual(facets, rag._extract_evidence_facets(spaced))
                    self.assertEqual(facets["tokens"], [subject])
                    self.assertEqual(facets["strong_subjects"], [subject])
                    for content, expected in [(f"{subject} 관련 규정입니다.", True),
                                              ("정보보안 비밀번호 규정입니다.", False)]:
                        for query in (attached, spaced):
                            self.assertEqual(await rag._has_facet_evidence(
                                query, [{"content": content}]), expected)

    def test_terminal_endings_and_core_nouns(self):
        for ending in ("알려줘", "알려주세요", "알려줄래", "뭐야", "무엇이야",
                       "인가요", "이뭐야", "이뭔가요", "어떻게해", "어떻게하나요"):
            with self.subTest(ending=ending):
                self.assertEqual(rag._extract_evidence_facets("근무시간" + ending + "?")["tokens"],
                                 ["근무시간"])
        for noun in ("근무시간", "재택근무", "출장비"):
            self.assertEqual(rag._normalize_query_question_ending(noun), noun)
            self.assertEqual(rag._extract_evidence_facets(noun + "알려줘")["tokens"], [noun])
        for text in ("연차휴가", "병가", "한도", "알려줘 문구가 포함된 문서", "한달최대재택근무며칠이야"):
            self.assertEqual(rag._normalize_query_question_ending(text), text)
        self.assertEqual(rag._normalize_evidence_text("근무시간알려줘"), "근무시간알려줘")

    async def test_normal_questions_and_numeric_conditions(self):
        cases = [
            ("근무시간 알려줘", ["근무시간"], [], "근무시간은 09:00부터 18:00까지이다."),
            ("출장 숙박비 한도 알려줘", ["출장", "숙박비"], [], "출장 숙박비 한도는 150000원이다."),
            ("병가 3일이면 증빙이 필요한가요?", ["증빙", "필요한가요"], ["3일"], "병가 3일이면 증빙이 필요하다."),
            ("350만원 외주 구매 절차 알려줘", ["외주", "구매", "절차"], ["3500000원"], "350만원 외주 구매 절차는 결재 후 진행한다."),
        ]
        with patch.object(rag, "_embed_texts_cached", side_effect=positive_semantics):
            for query, subjects, conditions, content in cases:
                with self.subTest(query=query):
                    self.assertEqual(rag._normalize_query_question_ending(query), query)
                    facets = rag._extract_evidence_facets(query)
                    self.assertEqual(facets["strong_subjects"], subjects)
                    self.assertEqual(facets["conditions"], conditions)
                    self.assertTrue(await rag._has_facet_evidence(query, [{"content": content}]))
            self.assertFalse(await rag._has_facet_evidence(
                "병가 3일이면 증빙이 필요한가요?", [{"content": "병가 증빙 제출 규정이다."}]))
        for query, conditions in [("병가3일이면증빙필요해?", ["3일"]),
                                  ("350만원외주구매알려줘", ["3500000원"]),
                                  ("한달최대재택근무며칠이야", [])]:
            self.assertEqual(rag._extract_evidence_facets(query)["conditions"], conditions)

    async def test_search_normalizes_inputs_but_preserves_original_query(self):
        async def unchanged(query):
            return {"query": query, "status": "unchanged"}

        for query in PAIRS[0][:2]:
            with (
                patch.object(rag, "rewrite_query", side_effect=unchanged) as rewrite,
                patch.object(rag, "_embed_texts_cached", side_effect=positive_semantics) as embed,
                patch.object(rag.supabase_service, "search_rag_chunks", return_value=[
                    {"id": "hours", "content": "근무시간은 09:00부터 18:00까지이다.", "similarity": 0.9}]),
                patch.object(rag.supabase_service, "list_accessible_rag_chunks", return_value=[]),
                patch.object(rag, "bm25_candidates", return_value=[]) as bm25,
                patch.object(rag, "rerank_candidates", new_callable=AsyncMock,
                             side_effect=lambda original, candidates: candidates) as rerank,
            ):
                results = await rag.search("test@example.com", query, None, 4)
                self.assertEqual(results[0]["original_query"], query)
                rewrite.assert_awaited_once_with(query)
                self.assertEqual(rerank.await_args.args[0], query)
                self.assertEqual(embed.await_args_list[0].args[0][0], "근무시간 알려줘")
                self.assertEqual(bm25.call_args.args[0], "근무시간 알려줘")


if __name__ == "__main__":
    unittest.main()
