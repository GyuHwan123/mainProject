import unittest
from unittest.mock import AsyncMock, patch

from app.services.rag_service import (
    _has_facet_evidence,
    _is_table_structure_query,
    build_chunks,
    extract_document_title_with_layout,
    search,
)


class RagTableChunkingTests(unittest.TestCase):
    def test_preserves_pdf_table_headers_and_row_values(self):
        page = {
            "page": 1,
            "text": "",
            "tables": [{
                "bbox": [[0, 0], [200, 0], [200, 100], [0, 100]],
                "columns": ["품목", "금액"],
                "rows": [["품목", "금액"], ["사과", "1000"], ["배", "2000"]],
            }],
            "items": [
                {"text": "사과", "bbox": [[0, 20], [50, 20], [50, 30], [0, 30]], "cell": "R2C1", "row": 2, "column": 1},
                {"text": "1000", "bbox": [[100, 20], [150, 20], [150, 30], [100, 30]], "cell": "R2C2", "row": 2, "column": 2},
                {"text": "배", "bbox": [[0, 40], [50, 40], [50, 50], [0, 50]], "cell": "R3C1", "row": 3, "column": 1},
                {"text": "2000", "bbox": [[100, 40], [150, 40], [150, 50], [100, 50]], "cell": "R3C2", "row": 3, "column": 2},
            ],
        }

        chunks = build_chunks([page])

        self.assertEqual(len(chunks), 1)
        self.assertIn("[표 크기] 헤더 포함 3행 × 2열", chunks[0]["content"])
        self.assertIn("[표 테이블 열 컬럼명] 1열: 품목 | 2열: 금액", chunks[0]["content"])
        self.assertIn("1열(품목): 사과 | 2열(금액): 1000", chunks[0]["content"])
        self.assertIn("1열(품목): 배 | 2열(금액): 2000", chunks[0]["content"])
        self.assertEqual(chunks[0]["bbox"], [[0.0, 20.0], [150.0, 50.0]])

    def test_labels_blank_first_header_as_row_dimension(self):
        page = {
            "page": 1,
            "tables": [{
                "bbox": [[0, 0], [400, 0], [400, 200], [0, 200]],
                "rows": [["", "평균", "표준편차", "평균"], ["실험1", "77.40", "8.55", "92.19"]],
                "columns": ["", "평균", "표준편차", "평균"],
                "row_count": 2,
                "column_count": 4,
            }],
            "items": [
                {"text": "실험1", "bbox": [[0, 40], [60, 40], [60, 60], [0, 60]], "cell": "R2C1", "row": 2, "column": 1},
                {"text": "77.40", "bbox": [[100, 40], [150, 40], [150, 60], [100, 60]], "cell": "R2C2", "row": 2, "column": 2},
            ],
        }
        content = build_chunks([page], document_title="연구수행 결과표")[0]["content"]
        self.assertIn("헤더 포함 2행 × 4열", content)
        self.assertIn("1열: 헤더 없음(행 구분)", content)
        self.assertIn("2열: 평균 | 3열: 표준편차 | 4열: 평균", content)
        self.assertNotIn("[표 테이블 열 컬럼명] 열 1", content)

    def test_adds_document_section_and_hierarchical_path_metadata(self):
        def row(text, y, height=12):
            return {"text": text, "bbox": [[10, y], [180, y], [180, y + height], [10, y + height]]}

        pages = [{
            "page": 1,
            "text": "",
            "items": [
                row("인사 운영 규정", 10, 20),
                row("제2장 평가 운영", 50, 18),
                row("제3절 평가 기준", 80, 16),
                row("평가 항목별 배점은 별표와 같다.", 110),
            ],
        }]

        chunks = build_chunks(pages, document_title="인사 운영 규정")

        body_chunk = chunks[-1]
        self.assertEqual(body_chunk["document_title"], "인사 운영 규정")
        self.assertEqual(body_chunk["section_title"], "제3절 평가 기준")
        self.assertEqual(body_chunk["section_path"], ["제2장 평가 운영", "제3절 평가 기준"])
        self.assertIn("[장절항 경로] 제2장 평가 운영 > 제3절 평가 기준", body_chunk["content"])

    def test_prefers_single_title_box_over_same_baseline_callout(self):
        pages = [{"page": 1, "items": [
            {"text": "표1. 연구수행 결과표", "bbox": [[100, 100], [350, 100], [350, 145], [100, 145]]},
            {"text": "테두리 굵기 안내", "bbox": [[650, 105], [850, 105], [850, 135], [650, 135]]},
            {"text": "실험1", "bbox": [[100, 200], [160, 200], [160, 225], [100, 225]]},
            {"text": "77.40", "bbox": [[250, 200], [310, 200], [310, 225], [250, 225]]},
        ]}]
        title, _ = extract_document_title_with_layout(pages)
        self.assertEqual(title, "표1. 연구수행 결과표")


class RagStructuralEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_detects_table_structure_questions(self):
        self.assertTrue(_is_table_structure_query("테이블의 컬럼명을 다 알려주세요"))
        self.assertTrue(_is_table_structure_query("표가 몇 행 몇 열인가요?"))
        self.assertFalse(_is_table_structure_query("실험1의 평균은 얼마인가요?"))

    async def test_accepts_explicit_table_schema_without_semantic_model_gate(self):
        candidates = [{"content": "[표 테이블 열 컬럼명] 항목 | 평균 | 표준편차"}]
        with patch("app.services.rag_service._embed_texts_cached", new_callable=AsyncMock) as embed:
            accepted = await _has_facet_evidence("테이블의 컬럼명을 다 알려주세요", candidates)
        self.assertTrue(accepted)
        embed.assert_not_awaited()

    async def test_table_schema_search_skips_cpu_reranker(self):
        candidate = {
            "content": "[표 크기] 헤더 포함 8행 × 4열\n[표 테이블 열 컬럼명] 1열: 행 구분 | 2열: 평균",
            "similarity": 0.7,
        }
        with (
            patch("app.services.rag_service._embed_texts_cached", new_callable=AsyncMock) as embed,
            patch(
                "app.services.rag_service.supabase_service.list_rag_chunks",
                return_value=[candidate],
            ),
            patch("app.services.rag_service.rerank_candidates", new_callable=AsyncMock) as rerank,
        ):
            results = await search("user@example.com", "테이블의 컬럼명을 다 알려주세요", "doc-1", 4)

        self.assertEqual(results, [candidate])
        embed.assert_not_awaited()
        rerank.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
