import unittest
from unittest.mock import AsyncMock, patch

from app.services.rag_service import bm25_candidates, merge_hybrid_candidates, search


class Bm25RankingTests(unittest.TestCase):
    def test_prefers_exact_rare_terms(self):
        chunks = [
            {"id": "wrong", "content": "국내 출장 신청과 승인 절차를 설명한다."},
            {"id": "right", "content": "일반직원 국내 출장 숙박비 한도는 150000원이다."},
            {"id": "noise", "content": "인사평가와 근무시간에 관한 규정이다."},
        ]

        ranked = bm25_candidates("일반직원 국내 출장 숙박비 한도", chunks, 3)

        self.assertEqual(ranked[0]["id"], "right")
        self.assertGreater(ranked[0]["bm25_score"], ranked[1]["bm25_score"])

    def test_merges_duplicate_dense_and_bm25_chunks(self):
        dense = [{"id": "same", "content": "정답", "similarity": 0.8}]
        lexical = [{"id": "same", "content": "정답", "bm25_score": 3.2, "bm25_rank": 1}]

        merged = merge_hybrid_candidates(dense, lexical)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["retrieval_methods"], ["dense", "bm25"])
        self.assertEqual(merged[0]["dense_rank"], 1)
        self.assertEqual(merged[0]["bm25_rank"], 1)

    def test_merges_original_and_rewritten_query_candidates(self):
        merged = merge_hybrid_candidates(
            [{"id": "original", "content": "근무시간", "similarity": 0.8}],
            [],
            [{"id": "rewritten", "content": "근로시간", "similarity": 0.7}],
            [{"id": "original", "content": "근무시간", "bm25_score": 2.0}],
        )

        self.assertEqual({row["id"] for row in merged}, {"original", "rewritten"})
        original = next(row for row in merged if row["id"] == "original")
        self.assertEqual(original["retrieval_queries"], ["original", "rewritten"])


class HybridSearchFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_dense_and_bm25_candidates_are_both_sent_to_reranker(self):
        dense = [{"id": "dense", "content": "의미 기반 후보", "similarity": 0.8}]
        lexical = [{"id": "lexical", "content": "숙박비 한도 150000원"}]

        async def keep_candidates(_query, candidates):
            return candidates

        with (
            patch(
                "app.services.rag_service.rewrite_query", new_callable=AsyncMock,
                return_value={"query": "숙박비 한도", "status": "unchanged", "latency_ms": 1},
            ),
            patch(
                "app.services.rag_service._embed_texts_cached", new_callable=AsyncMock,
                return_value=([[0.1], [0.1]], {"hits": 0, "misses": 2}),
            ),
            patch(
                "app.services.rag_service.supabase_service.search_rag_chunks",
                return_value=dense,
            ),
            patch(
                "app.services.rag_service.supabase_service.list_accessible_rag_chunks",
                return_value=lexical,
            ),
            patch(
                "app.services.rag_service.rerank_candidates",
                new_callable=AsyncMock, side_effect=keep_candidates,
            ) as rerank,
            patch(
                "app.services.rag_service._has_facet_evidence",
                new_callable=AsyncMock, return_value=True,
            ),
        ):
            results = await search(
                "user@example.com", "숙박비 한도", None, 4,
                user_role="DEVELOPER", subscription_tier="PERSONAL",
            )

        reranked = rerank.await_args.args[1]
        self.assertEqual({row["id"] for row in reranked}, {"dense", "lexical"})
        self.assertEqual({row["id"] for row in results}, {"dense", "lexical"})


if __name__ == "__main__":
    unittest.main()
