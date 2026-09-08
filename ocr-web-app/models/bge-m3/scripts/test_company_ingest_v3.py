"""Run: python -m unittest discover -s models/bge-m3/scripts -p test_company_ingest_v3.py"""
import ast
from copy import deepcopy
import json
from pathlib import Path
import unittest

import company_ingest_v3 as v3


META = {"doc_id": "TEST-001", "title": "테스트 규정", "owner": "테스트팀", "security": "사내공개",
        "version": "v1.0", "effective_date": "2026-07-01", "tags": [], "filename": "test.pdf"}


def text(value, index, page=1, size=10):
    return {"doc_id": META["doc_id"], "block_id": f"b{index}", "page_number": page,
            "block_type": "text", "text": value, "bbox": [10, index * 20, 500, index * 20 + 10],
            "font_size": size, "page_height": 1000, "table_index": None, "disposition": "content"}


def table(rows, index, page=1):
    return {"doc_id": META["doc_id"], "block_id": f"b{index}", "page_number": page,
            "block_type": "table", "text": "\n".join(v3._serialize_table_rows(rows)),
            "bbox": [10, index * 20, 500, index * 20 + 10], "page_height": 1000,
            "table_index": 0, "table_id": f"t{index}", "rows": rows, "headers": rows[0],
            "rendered_rows": v3._serialize_table_rows(rows), "disposition": "content"}


def chunk(blocks, maximum=600):
    result = []
    info = v3.classify_blocks(blocks, META)
    for unit in v3.build_units(blocks):
        renderer = v3.render_table if unit["chunk_type"] == "table" else v3.render_text
        result.extend(renderer(META, unit, info, min(380, maximum), maximum))
    for i, row in enumerate(result):
        row["chunk_index"] = i
    v3.validate(result, blocks, maximum)
    return result


class ChunkingUnitTests(unittest.TestCase):
    def test_existing_cell_and_row_serialization_preserved_without_model_imports(self):
        tree = ast.parse(Path(v3.__file__).with_name("company_ingest.py").read_text(encoding="utf-8"))
        namespace = {}
        for name in ["_clean_cell", "_serialize_table_rows"]:
            function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            exec(compile(ast.Module(body=[function], type_ignores=[]), "legacy-table-only", "exec"), namespace)
        for value in [None, " 긴\n 셀  값 ", 100]:
            self.assertEqual(v3._clean_cell(value), namespace["_clean_cell"](value))
        for rows in [[], [["항목"]], [["", "값"], ["항목", "100"]],
                     [["항목", "값"], ["항목", "값"], ["교통비", "100", "추가"]]]:
            self.assertEqual(v3._serialize_table_rows(rows), namespace["_serialize_table_rows"](rows))

    def test_cross_page_article_keeps_title_and_chapter_belongs_to_next_article(self):
        blocks = [text("제1장 총칙", 0), text("제1조 (목적)", 1), text("첫 내용.", 2),
                  text("제2장 근태", 3), text("제6조 (근로시간)", 4), text("다음 페이지 내용.", 5, page=2),
                  text("제7조 (외출)", 6, page=2), text("별도 내용.", 7, page=2)]
        result = chunk(blocks)
        self.assertEqual(len(result), 3)
        self.assertNotIn("제2장", result[0]["content"])
        self.assertEqual((result[1]["start_page"], result[1]["end_page"]), (1, 2))
        self.assertEqual(result[2]["start_page"], 2)

    def test_table_keeps_reading_order_and_article_continuation(self):
        blocks = [text("제1조 (비용)", 0), text("표 앞 내용.", 1),
                  table([["항목", "금액"], ["숙박비", "150,000원"]], 2), text("표 뒤 내용.", 3)]
        result = chunk(blocks)
        self.assertEqual([c["chunk_type"] for c in result], ["text", "table", "text"])
        self.assertTrue(all(c["article_title"] == "제1조 (비용)" for c in result))
        self.assertNotIn("표 앞 내용", result[1]["content"])

    def test_article_heading_followed_by_table_does_not_become_orphan_chunk(self):
        result = chunk([text("제1조 (한도)", 0), table([["항목", "값"], ["금액", "100"]], 1)])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["chunk_type"], "table")
        self.assertIn("제1조 (한도)", result[0]["content"])

    def test_inline_article_reference_is_not_an_article_boundary(self):
        result = chunk([text("제1조 (절차)", 0), text("이 절차는 제2조 (승인)에 따른다.", 1)])
        self.assertEqual(len(result), 1)

    def test_long_article_repeats_context_and_conserves_every_character(self):
        body = "\n\n".join(f"{i}. " + (f"항목{i}의 상세 조건을 준수한다. " * 20) for i in range(1, 5))
        result = chunk([text("제1장 총칙", 0), text("제1조 (긴 조항)", 1), text(body, 2)])
        self.assertGreater(len(result), 1)
        self.assertTrue(all(len(c["content"]) <= 600 for c in result))
        self.assertTrue(all("제1조 (긴 조항)" in c["content"] for c in result))
        self.assertEqual(v3.compact("".join(c["source_text"] for c in result)), v3.compact(body))

    def test_long_split_prefers_items_then_paragraphs_then_sentences(self):
        examples = ["1. " + "가" * 30 + "\n2. " + "나" * 30,
                    "가" * 30 + "\n\n" + "나" * 30,
                    "가" * 29 + ". " + "나" * 29 + "."]
        for sample in examples:
            spans = v3.split_spans(sample, 35, 45)
            self.assertEqual(len(spans), 2)
            self.assertTrue(sample[spans[0][0]:spans[0][1]].rstrip().endswith(("가", ".")))
            self.assertEqual("".join(sample[a:b] for a, b in spans), sample)

    def test_character_fallback_has_no_loss_or_overlap(self):
        sample = "긴문장" * 500
        spans = v3.split_spans(sample, 380, 600)
        self.assertTrue(all(b - a <= 600 for a, b in spans))
        self.assertEqual("".join(sample[a:b] for a, b in spans), sample)

    def test_long_cross_page_parts_keep_actual_pages_without_markers(self):
        blocks = [text("제1조 (긴 규정)", 0, page=3),
                  text("첫 페이지의 긴 내용이다. " * 60, 1, page=3),
                  text("다음 페이지의 별도 내용이다. " * 40, 2, page=4)]
        # Repeated sentences are valid content; use unique sequence labels to
        # distinguish otherwise identical full chunk texts in this fixture.
        blocks[1]["text"] = " ".join(f"{i}번째 첫 페이지 조건을 지킨다." for i in range(60))
        blocks[2]["text"] = " ".join(f"{i}번째 다음 페이지 조건을 지킨다." for i in range(40))
        result = chunk(blocks)
        self.assertGreater(len(result), 2)
        self.assertEqual(result[0]["start_page"], 3)
        self.assertEqual(result[-1]["start_page"], 4)
        self.assertTrue(all(c["start_page"] in (3, 4) and c["end_page"] in (3, 4) for c in result))
        self.assertTrue(all("[[PAGE:" not in c["content"] for c in result))

    def test_large_table_splits_at_rows_and_repeats_headers(self):
        rows = [["항목", "내용"]] + [[f"항목{i}", "내용" * 60] for i in range(10)]
        result = chunk([text("제2장 비용", 0), table(rows, 1)])
        self.assertGreater(len(result), 1)
        self.assertTrue(all(c["chunk_type"] == "table" and len(c["content"]) <= 600 for c in result))
        self.assertTrue(all("[표 헤더] 항목 | 내용" in c["content"] for c in result))
        self.assertEqual([r for c in result for r in c["table_rows"]], v3._serialize_table_rows(rows))

    def test_oversized_single_table_row_is_intact_and_flagged(self):
        row = "중요한내용" * 200
        result = chunk([table([["항목", "내용"], ["긴 행", row]], 0)])
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["oversized_atomic_row"])
        self.assertIn(row, result[0]["content"])

    def test_exact_notice_and_revision_preserved_in_audit_metadata(self):
        blocks = [text(v3.NOTICE[:-5], 0), text(v3.NOTICE[-5:], 1),
                  text("제1조 (목적)", 2), text("실제 내용.", 3), text("제정/개정 이력: v1.0 최초 제정", 4)]
        result = chunk(blocks)
        self.assertEqual(result[0]["document_info"]["notices"], [v3.NOTICE])
        self.assertEqual(len(result[0]["document_info"]["revision_history"]), 1)
        self.assertNotIn("가상기업", result[0]["content"])
        unknown = [text("※ 본 문서는 RAG/OCR/검색 관련 실제 추가 의무를 정한다.", 0)]
        self.assertIn("실제 추가 의무", chunk(unknown)[0]["content"])

    def test_validation_rejects_missing_rows_and_changed_content(self):
        blocks = [text("제1조 (목적)", 0), text("필수 내용.", 1),
                  table([["항목", "값"], ["A", "1"], ["B", "2"]], 2)]
        result = chunk(blocks)
        changed = deepcopy(result)
        changed[0]["text"] = changed[0]["content"] = changed[0]["content"].replace("필수", "변조")
        with self.assertRaises(ValueError):
            v3.validate(changed, blocks, 600)
        changed = deepcopy(result)
        changed[-1]["table_rows"].pop()
        with self.assertRaises(ValueError):
            v3.validate(changed, blocks, 600)


class GeneratedCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = v3.COMPANY_DIR / "processed_v3"
        cls.chunks = json.loads((folder / "company_chunks_v3.json").read_text(encoding="utf-8"))
        cls.blocks = json.loads((folder / "company_blocks_v3.json").read_text(encoding="utf-8"))
        cls.manifest = json.loads((folder / "company_ingest_manifest_v3.json").read_text(encoding="utf-8"))

    def test_full_corpus_coverage_and_protected_artifacts(self):
        self.assertTrue(all(v == 0 for v in v3.validate(self.chunks, self.blocks, 600).values()))
        self.assertEqual(v3.protected_hashes(), self.manifest["protected_artifact_sha256"])
        self.assertEqual(len({c["doc_id"] for c in self.chunks}), 18)
        for doc_id in {c["doc_id"] for c in self.chunks}:
            indexes = [c["chunk_index"] for c in self.chunks if c["doc_id"] == doc_id]
            self.assertEqual(indexes, list(range(len(indexes))))

    def test_hr_hours_attendance_and_cross_page_discipline(self):
        hr = [c for c in self.chunks if c["doc_id"] == "HR-001"]
        hours = next(c for c in hr if c["article_title"] == "제6조 (근로시간)")
        self.assertIn("09:00부터 18:00", hours["content"])
        self.assertNotIn("제7조", hours["content"])
        attendance = next(c for c in hr if c["article_title"] == "제7조 (지각·조퇴·외출)")
        self.assertIn("사후보고한다", attendance["content"])
        discipline = next(c for c in hr if c["article_title"] == "제18조 (징계사유)")
        self.assertEqual((discipline["start_page"], discipline["end_page"]), (1, 2))
        self.assertIn("무단결근", discipline["content"])
        self.assertNotIn("[표 행]", discipline["content"])

    def test_ga_table_stays_at_original_section_and_retains_lodging_row(self):
        ga = [c for c in self.chunks if c["doc_id"] == "GA-001"]
        expense = next(c for c in ga if c.get("table_headers") == ["항목", "일반직원", "팀장 이상", "비고"])
        self.assertEqual(expense["section_title"], "제2장 국내출장비")
        self.assertIsNone(expense["article_title"])
        self.assertIn("[표 행] 항목: 숙박비 | 일반직원: 1박 150,000원 한도 | 팀장 이상: 1박 180,000원 한도 | 비고: 세금·봉사료 포함", expense["content"])
        approval = next(c for c in ga if c["article_title"] == "제1조 (출장신청)")
        excess = next(c for c in ga if c["article_title"] == "제5조 (숙박비 초과)")
        self.assertLess(approval["chunk_index"], expense["chunk_index"])
        self.assertLess(expense["chunk_index"], excess["chunk_index"])


if __name__ == "__main__":
    unittest.main()
