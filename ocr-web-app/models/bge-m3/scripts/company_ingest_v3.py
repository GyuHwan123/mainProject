"""Local-only company PDF chunking. Never loads an embedding model or a DB client.

Run with PyMuPDF==1.28.2 and pypdf==6.0.0. Outputs are confined to processed_v3.
The source blocks and validation manifest are retained for a reproducible audit.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import pickle
import re
import statistics
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
COMPANY_DIR = BASE_DIR / "data" / "company_documents"
TARGET_CHARS = 380
MAX_CHARS = 600
ARTICLE = re.compile(r"^제\s*\d+\s*조(?:의\s*\d+)?(?:\s*\([^)]*\)|\s+.+)?$")
SECTION = re.compile(r"^제\s*\d+\s*([장절])(?:\s|$)")
NUMBERED_SECTION = re.compile(r"^\d+(?:\.\d+)*[.)]\s+\S")
NOTICE = "※ 본 문서는 RAG/OCR/검색 성능평가를 위해 작성한 가상기업 테스트 문서입니다. 실제 회사 규정이나 법률 자문으로 사용하지 마십시오."


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# Preserve the v1 table cell cleanup and header/value rendering verbatim in
# behavior, without importing company_ingest (which imports torch/model code).
def _clean_cell(value: object) -> str:
    return " ".join(str(value or "").split())


def _serialize_table_rows(rows: list[list[str]]) -> list[str]:
    if len(rows) < 2 or not any(rows[0]):
        return []
    headers, rendered = rows[0], []
    for row in rows[1:]:
        if row == headers:
            continue
        fields = []
        for index, value in enumerate(row):
            if value:
                header = headers[index] if index < len(headers) else ""
                fields.append(f"{header or f'열 {index + 1}'}: {value}")
        if fields:
            rendered.append("[표 행] " + " | ".join(fields))
    return rendered


def union_rect(rects: list[list[float]]) -> list[float] | None:
    if not rects:
        return None
    return [min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects)]


def bbox_points(rect: list[float] | None) -> list[list[float]] | None:
    return [[rect[0], rect[1]], [rect[2], rect[3]]] if rect else None


def extract_blocks(pdf_path: Path, metadata: dict) -> tuple[list[dict], list[dict]]:
    import pymupdf
    from pypdf import PdfReader

    blocks, page_audits = [], []
    reader = PdfReader(pdf_path)
    with pymupdf.open(pdf_path) as document:
        for page_number, page in enumerate(document, 1):
            tables = sorted(page.find_tables().tables, key=lambda t: (t.bbox[1], t.bbox[0]))
            table_chars: list[list[str]] = [[] for _ in tables]
            page_blocks, all_chars = [], []
            for raw_block in page.get_text("rawdict")["blocks"]:
                if raw_block["type"] != 0:
                    raise ValueError(f"{metadata['doc_id']} p{page_number}: image block requires OCR; refusing silent omission")
                for line in raw_block["lines"]:
                    kept = []
                    for span in line["spans"]:
                        for char in span["chars"]:
                            all_chars.append(char["c"])
                            rect = char["bbox"]
                            cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
                            owners = [i for i, table in enumerate(tables) if table.bbox[0] <= cx <= table.bbox[2] and table.bbox[1] <= cy <= table.bbox[3]]
                            if len(owners) > 1:
                                raise ValueError("Overlapping detected tables; ambiguous character ownership")
                            if owners:
                                table_chars[owners[0]].append(char["c"])
                            else:
                                kept.append(char)
                    text = "".join(c["c"] for c in kept).strip()
                    if text:
                        page_blocks.append({"block_type": "text", "text": text,
                                            "bbox": union_rect([list(c["bbox"]) for c in kept]),
                                            "font_size": max(s["size"] for s in line["spans"]),
                                            "table_index": None})
            for table_index, table in enumerate(tables):
                rows = [[_clean_cell(c) for c in row] for row in table.extract() or []]
                rows = [row for row in rows if any(row)]
                rendered = _serialize_table_rows(rows)
                if not rendered:
                    raise ValueError(f"{metadata['doc_id']} p{page_number}: unsupported headerless/empty table")
                # Spatial removal must account for every table character once.
                if Counter(compact("".join(table_chars[table_index]))) != Counter(compact("".join(c for row in rows for c in row))):
                    raise ValueError(f"{metadata['doc_id']} p{page_number}: table cell coverage mismatch")
                page_blocks.append({"block_type": "table", "text": "\n".join(rendered),
                                    "bbox": list(table.bbox), "table_index": table_index,
                                    "table_id": f"{metadata['doc_id']}:p{page_number}:t{table_index}",
                                    "headers": rows[0], "rows": rows, "rendered_rows": rendered,
                                    "raw_text": "".join(table_chars[table_index])})
            # Independent extractor coverage: order can differ; characters cannot.
            pdf_text = reader.pages[page_number - 1].extract_text() or ""
            original = Counter(compact(pdf_text))
            extracted = Counter(compact("".join(all_chars)))
            assigned = Counter(compact("".join(b.get("raw_text", b["text"]) for b in page_blocks)))
            if original != extracted or extracted != assigned:
                raise ValueError(f"{metadata['doc_id']} p{page_number}: PDF character coverage mismatch")
            if not original:
                raise ValueError(f"{metadata['doc_id']} p{page_number}: empty/native-text-free PDF page")
            for index, block in enumerate(sorted(page_blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))):
                block.update(doc_id=metadata["doc_id"], page_number=page_number,
                             block_id=f"{metadata['doc_id']}:p{page_number}:b{index}",
                             page_height=float(page.rect.height), disposition="content")
                blocks.append(block)
            page_audits.append({"page_number": page_number, "non_whitespace_characters": sum(original.values()),
                                "pypdf_vs_pymupdf_equal": True, "spatial_assignment_equal": True,
                                "table_count": len(tables)})
    return blocks, page_audits


def classify_blocks(blocks: list[dict], metadata: dict) -> dict:
    """Only exact known boilerplate is excluded from content; retain it in audit."""
    info: dict[str, Any] = {"revision_history": [], "source_headers": [], "notices": []}
    for block in blocks:
        if block["block_type"] != "text":
            continue
        text = block["text"]
        reason = None
        if text == metadata["title"]:
            reason = "document_title_in_metadata"
        elif "문서번호 " + metadata["doc_id"] in text and "버전 " + metadata["version"] in text:
            reason = "document_header_in_metadata"
            info["source_headers"].append(text)
        elif text.startswith("제정/개정 이력:"):
            reason = "revision_history_in_metadata"
            info["revision_history"].append(text)
        elif block["bbox"][1] >= block["page_height"] * .9 and (
            text == "네오웍스테크(주)" or re.fullmatch(r"\d+\s*/\s*\d*", text)
        ):
            reason = "repeated_footer"
        if reason:
            block.update(disposition="metadata", exclusion_reason=reason)
    # Consume only the complete known notice, not everything after a loose prefix.
    for start, block in enumerate(blocks):
        if block["block_type"] != "text" or not block["text"].startswith("※ 본 문서는 RAG/OCR/검색"):
            continue
        candidates, text = [], ""
        for candidate in blocks[start:start + 5]:
            if candidate["block_type"] != "text" or candidate["page_number"] != block["page_number"]:
                break
            candidates.append(candidate)
            text += candidate["text"]
            if compact(text) == compact(NOTICE):
                for item in candidates:
                    item.update(disposition="metadata", exclusion_reason="corpus_notice")
                info["notices"].append(NOTICE)
                break
    return info


def heading_kind(block: dict) -> str | None:
    text = block["text"]
    if ARTICLE.fullmatch(text):
        return "article"
    match = SECTION.match(text)
    if match:
        return "chapter" if match.group(1) == "장" else "section"
    # Numbered lists stay body text; this corpus uses 13pt section headings.
    if NUMBERED_SECTION.match(text) and block.get("font_size", 0) >= 11:
        return "numbered_section"
    if re.match(r"^(?:\[?표\s*\d+|별표\s*\d+)", text):
        return "table_caption"
    return None


def build_units(blocks: list[dict]) -> list[dict]:
    units, body = [], []
    section_blocks: list[dict] = []
    article = None
    caption = None
    anchor = None

    def flush() -> None:
        nonlocal body, anchor
        if body:
            units.append({"chunk_type": "text", "body_blocks": body,
                          "section_blocks": list(section_blocks), "article_block": article,
                          "anchor": anchor})
            body, anchor = [], None

    for block in blocks:
        if block["disposition"] == "metadata":
            continue
        if block["block_type"] == "table":
            flush()
            units.append({"chunk_type": "table", "table_block": block,
                          "section_blocks": list(section_blocks), "article_block": article,
                          "caption_block": caption})
            caption = None
            # An article heading followed immediately by a table belongs to that
            # table; do not make an orphan heading-only text chunk.
            anchor = None
            continue
        kind = heading_kind(block)
        if kind:
            flush()
            block["disposition"] = "context"
            if kind == "article":
                article, anchor = block, block
            elif kind == "table_caption":
                caption = block
            else:
                article = None
                if kind == "section":
                    section_blocks = [b for b in section_blocks if heading_kind(b) == "chapter"] + [block]
                else:
                    section_blocks = [block]
                anchor = block
            continue
        body.append(block)
    flush()
    # Context-only sections must not silently disappear (e.g. empty article).
    referenced = {b["block_id"] for u in units for b in [*u["section_blocks"], u.get("article_block"), u.get("caption_block")] if b}
    orphaned = [b["block_id"] for b in blocks if b["disposition"] == "context" and b["block_id"] not in referenced]
    if orphaned:
        raise ValueError(f"Heading with no body/table: {orphaned}")
    return units


def split_spans(text: str, target: int, maximum: int) -> list[tuple[int, int]]:
    """Split only long units, using item > paragraph > sentence > character.

    Spans partition the source string exactly; there is no cross-unit overlap.
    """
    if not 0 < target <= maximum:
        raise ValueError("Invalid split budget (context prefix may exceed maximum)")
    patterns = [r"(?m)^(?=[①-⑳•●▪]|(?:\d+[.)]|[가-하][.)])\s)",
                r"\n\s*\n", r"(?<=[.!?。！？])\s+"]

    def atoms(start: int, end: int, level: int) -> list[tuple[int, int]]:
        if end - start <= maximum:
            return [(start, end)]
        if level == len(patterns):
            return [(i, min(i + target, end)) for i in range(start, end, target)]
        cuts = [start] + [start + m.end() for m in re.finditer(patterns[level], text[start:end]) if 0 < m.end() < end - start] + [end]
        return [span for a, b in zip(cuts, cuts[1:]) for span in atoms(a, b, level + 1)]

    if len(text) <= maximum:
        return [(0, len(text))]
    packed: list[tuple[int, int]] = []
    for start, end in atoms(0, len(text), 0):
        if packed and end - packed[-1][0] <= target:
            packed[-1] = (packed[-1][0], end)
        else:
            packed.append((start, end))
    return packed


def prefix_lines(metadata: dict, unit: dict) -> list[str]:
    return [f"[문서] {metadata['title']}", *[b["text"] for b in unit["section_blocks"]],
            *([unit["article_block"]["text"]] if unit.get("article_block") else [])]


def make_chunk(metadata: dict, unit: dict, content: str, sources: list[dict], info: dict) -> dict:
    pages = [s["page_number"] for s in sources]
    if not pages:
        raise ValueError("Chunk without source page")
    first = min(pages)
    rect = union_rect([s["bbox"] for s in sources if s["page_number"] == first and s.get("bbox")])
    context = [*unit["section_blocks"], *([unit["article_block"]] if unit.get("article_block") else [])]
    result = dict(metadata)
    result.update(text=content, content=content, page=first, page_number=first, start_page=first,
                  end_page=max(pages), chunk_type=unit["chunk_type"], bbox=bbox_points(rect),
                  section_title=unit["section_blocks"][-1]["text"] if unit["section_blocks"] else None,
                  section_path=[b["text"] for b in unit["section_blocks"]],
                  article_title=unit["article_block"]["text"] if unit.get("article_block") else None,
                  section=unit["article_block"]["text"] if unit.get("article_block") else None,
                  document_title=metadata["title"], document_info=info,
                  source_spans=sources, context_block_ids=[b["block_id"] for b in context])
    return result


def source_ref(block: dict, **extra: Any) -> dict:
    return {"block_id": block["block_id"], "page_number": block["page_number"], "bbox": block["bbox"], **extra}


def render_text(metadata: dict, unit: dict, info: dict, target: int, maximum: int) -> list[dict]:
    text, locations = "", []
    previous = None
    for block in unit["body_blocks"]:
        separator = ""
        if previous:
            gap = block["bbox"][1] - previous["bbox"][1]
            separator = "\n\n" if block["page_number"] == previous["page_number"] and gap > previous.get("font_size", 10) * 2.1 else "\n"
        text += separator
        start = len(text)
        text += block["text"]
        locations.append((start, len(text), block))
        previous = block
    prefix = "\n".join(prefix_lines(metadata, unit)) + "\n"
    capacity = maximum - len(prefix)
    spans = split_spans(text, min(max(1, target - len(prefix)), capacity), capacity)
    chunks = []
    for part, (start, end) in enumerate(spans):
        if not text[start:end].strip():
            continue
        sources = [source_ref(b, start_char=max(a, start) - a, end_char=min(z, end) - a)
                   for a, z, b in locations if a < end and z > start]
        if part == 0 and unit.get("anchor"):
            sources.insert(0, source_ref(unit["anchor"], context_anchor=True))
        chunk = make_chunk(metadata, unit, prefix + text[start:end].strip(), sources, info)
        chunk.update(part_index=part, part_count=len(spans), source_text=text[start:end].strip())
        chunks.append(chunk)
    if compact("".join(c["source_text"] for c in chunks)) != compact(text):
        raise ValueError("Long article split changed source text")
    return chunks


def render_table(metadata: dict, unit: dict, info: dict, target: int, maximum: int) -> list[dict]:
    table = unit["table_block"]
    is_info = table["headers"] == ["구분", "내용"] and any(row[0] == "시행일" for row in table["rows"][1:])
    caption = unit.get("caption_block")
    table_title = caption["text"] if caption else "문서 정보" if is_info else (unit["section_blocks"][-1]["text"] if unit["section_blocks"] else "표")
    headers = [h or f"열 {i + 1}" for i, h in enumerate(table["headers"])]
    prefix = "\n".join([*prefix_lines(metadata, unit), f"[표 제목] {table_title}", "[표 헤더] " + " | ".join(headers)]) + "\n"
    if len(prefix) >= maximum:
        raise ValueError("Table context/header exceeds maximum; manual review required")
    rows = table["rendered_rows"]
    groups: list[list[int]] = []
    if len(prefix) + len("\n".join(rows)) <= maximum:
        groups = [list(range(len(rows)))]
    else:
        for i, row in enumerate(rows):
            if not groups or len(prefix) + len("\n".join(rows[j] for j in [*groups[-1], i])) > target:
                groups.append([i])
            else:
                groups[-1].append(i)
    chunks = []
    for part, indices in enumerate(groups):
        content = prefix + "\n".join(rows[i] for i in indices)
        sources = [source_ref(table, row_indices=indices)]
        chunk = make_chunk(metadata, unit, content, sources, info)
        chunk.update(table_id=table["table_id"], table_index=table["table_index"], table_title=table_title,
                     table_headers=headers, table_row_indices=indices, table_rows=[rows[i] for i in indices],
                     part_index=part, part_count=len(groups), oversized_atomic_row=len(content) > maximum)
        if caption:
            chunk["context_block_ids"].append(caption["block_id"])
        chunks.append(chunk)
    return chunks


def length_stats(chunks: list[dict]) -> dict:
    lengths = [len(c["text"]) for c in chunks]
    typed = all("chunk_type" in c for c in chunks)
    return {"document_count": len({c["doc_id"] for c in chunks}), "chunk_count": len(chunks),
            "text_chunk_count": sum(c.get("chunk_type") == "text" for c in chunks) if typed else None,
            "table_chunk_count": sum(c.get("chunk_type") == "table" for c in chunks) if typed else None,
            "min_length": min(lengths), "max_length": max(lengths),
            "mean_length": statistics.mean(lengths), "median_length": statistics.median(lengths),
            **{f"over_{n}": sum(x > n for x in lengths) for n in (380, 500, 600)}}


def validate(chunks: list[dict], blocks: list[dict], maximum: int) -> dict:
    by_id = {b["block_id"]: b for b in blocks}
    # Distinguish article headings from inline references to other articles.
    mixed = sum(len({compact(line) for line in c["content"].splitlines() if ARTICLE.fullmatch(line)}) > 1 for c in chunks if c["chunk_type"] == "text")
    broken_rows = sum([line for line in c["content"].splitlines() if line.startswith("[표 행]")] != c.get("table_rows", []) for c in chunks if c["chunk_type"] == "table")
    missing_headers = sum(not c.get("table_headers") or "[표 헤더] " not in c["content"] for c in chunks if c["chunk_type"] == "table")
    empty = sum(not c["content"].strip() for c in chunks)
    duplicates = len(chunks) - len({(c["doc_id"], c["chunk_type"], c["content"]) for c in chunks})
    coverage_errors, table_errors = [], []
    contexts = {i for c in chunks for i in c["context_block_ids"]}
    for block in blocks:
        bid = block["block_id"]
        if block["disposition"] == "metadata":
            continue  # Exact source retained in blocks artifact / document_info.
        if block["disposition"] == "context":
            if bid not in contexts:
                coverage_errors.append(bid)
        elif block["block_type"] == "text":
            spans = sorted((s["start_char"], s["end_char"]) for c in chunks for s in c["source_spans"] if s["block_id"] == bid and "start_char" in s)
            cursor = 0
            for start, end in spans:
                if start != cursor:
                    coverage_errors.append(bid)
                cursor = end
            if cursor != len(block["text"]):
                coverage_errors.append(bid)
            rendered = "".join(block["text"][a:z] for a, z in spans)
            if rendered != block["text"]:
                coverage_errors.append(bid)
        else:
            related = [c for c in chunks if c.get("table_id") == block["table_id"]]
            rows = [r for c in related for r in c["table_rows"]]
            indexes = [i for c in related for i in c["table_row_indices"]]
            if rows != block["rendered_rows"] or indexes != list(range(len(rows))):
                table_errors.append(bid)
    bad_pages = sum(c["start_page"] != min(s["page_number"] for s in c["source_spans"]) or c["end_page"] != max(s["page_number"] for s in c["source_spans"]) for c in chunks)
    content_errors = 0
    for c in chunks:
        if c["text"] != c["content"]:
            content_errors += 1
        if c["chunk_type"] == "text":
            source = "".join(by_id[s["block_id"]]["text"][s["start_char"]:s["end_char"]]
                             for s in c["source_spans"] if "start_char" in s)
            expected_prefix = "\n".join([f"[문서] {c['document_title']}", *c["section_path"],
                                         *([c["article_title"]] if c["article_title"] else [])]) + "\n"
            if compact(source) != compact(c["source_text"]) or c["content"] != expected_prefix + c["source_text"]:
                content_errors += 1
        for s in c["source_spans"]:
            if s["page_number"] != by_id[s["block_id"]]["page_number"]:
                bad_pages += 1
    validation = {"mixed_article_text_chunks": mixed, "broken_table_row_chunks": broken_rows,
                  "table_chunks_without_headers": missing_headers, "empty_chunks": empty,
                  "duplicate_chunks": duplicates, "source_coverage_errors": len(coverage_errors),
                  "table_row_coverage_errors": len(table_errors), "page_metadata_errors": bad_pages,
                  "rendered_content_errors": content_errors,
                  "text_chunks_over_max": sum(len(c["content"]) > maximum for c in chunks if c["chunk_type"] == "text"),
                  "table_rows_in_text_chunks": sum("[표 행]" in c["content"] for c in chunks if c["chunk_type"] == "text")}
    if any(validation.values()):
        raise ValueError("Structure validation failed: " + json.dumps(validation))
    validation["oversized_atomic_table_chunks"] = sum(c.get("oversized_atomic_row", False) for c in chunks)
    return validation


def protected_hashes() -> dict:
    folders = [COMPANY_DIR / name for name in ("processed", "processed_v2", "embedding_variants")]
    folders += [BASE_DIR / "finetuned", BASE_DIR / "model_checkpoints"]
    return {str(p.relative_to(BASE_DIR)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in folders for p in sorted(folder.rglob("*")) if p.is_file()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=TARGET_CHARS)
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS)
    args = parser.parse_args()
    if not 0 < args.target <= args.max_chars:
        parser.error("0 < target <= max-chars is required")
    before = protected_hashes()
    catalog = json.loads((COMPANY_DIR / "metadata/document_catalog.json").read_text(encoding="utf-8"))
    pdfs = sorted((COMPANY_DIR / "documents").glob("*.pdf"))
    metadata_map = {d["filename"]: d for d in catalog["documents"]}
    if len(pdfs) != 18 or len(metadata_map) != 18 or {p.name for p in pdfs} != set(metadata_map):
        raise ValueError("Exactly the catalog's 18 PDFs are required")
    chunks, all_blocks, documents = [], [], []
    for pdf in pdfs:
        metadata = metadata_map[pdf.name]
        blocks, pages = extract_blocks(pdf, metadata)
        info = classify_blocks(blocks, metadata)
        local = []
        for unit in build_units(blocks):
            renderer = render_table if unit["chunk_type"] == "table" else render_text
            local.extend(renderer(metadata, unit, info, args.target, args.max_chars))
        for index, chunk in enumerate(local):
            chunk["chunk_index"] = index
        if not local:
            raise ValueError(f"Empty document: {metadata['doc_id']}")
        documents.append({"doc_id": metadata["doc_id"], "title": metadata["title"],
                          "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                          "stats": length_stats(local), "page_audits": pages, "document_info": info})
        chunks.extend(local)
        all_blocks.extend(blocks)
    validation = validate(chunks, all_blocks, args.max_chars)
    if protected_hashes() != before:
        raise ValueError("Protected artifacts changed during local chunking")
    comparison = {}
    for label, path in [("current_37", "processed/company_chunks.json"), ("v2_99", "processed_v2/company_chunks_v2.json")]:
        comparison[label] = length_stats(json.loads((COMPANY_DIR / path).read_text(encoding="utf-8")))
    comparison["v3"] = length_stats(chunks)
    import pymupdf
    import pypdf
    manifest = {"chunking_version": "v3", "embedding_generated": False,
                "parameters": {"target_chars": args.target, "max_chars": args.max_chars, "overlap_chars": 0,
                               "length_includes_context": True, "oversized_table_row_policy": "preserve_and_flag"},
                "extractors": {"pymupdf": pymupdf.VersionBind, "pypdf": pypdf.__version__},
                "stats": length_stats(chunks), "documents": documents, "validation": validation,
                "source_block_count": len(all_blocks),
                "source_page_count": sum(len(d["page_audits"]) for d in documents),
                "source_non_whitespace_characters": sum(p["non_whitespace_characters"] for d in documents for p in d["page_audits"]),
                "source_table_count": sum(b["block_type"] == "table" for b in all_blocks),
                "source_table_row_count": sum(len(b.get("rendered_rows", [])) for b in all_blocks),
                "metadata_block_count": sum(b["disposition"] == "metadata" for b in all_blocks),
                "comparison": comparison, "protected_artifacts_unchanged": True,
                "protected_artifact_sha256": before,
                "chunk_fingerprint_sha256": fingerprint([{k: c[k] for k in ("doc_id", "chunk_index", "page", "text")} for c in chunks]),
                "embedding_input": "company_chunks_v3.pkl", "embedding_text_field": "text"}
    output = COMPANY_DIR / "processed_v3"
    output.mkdir(parents=True, exist_ok=True)
    for name, value in [("company_chunks_v3.json", chunks), ("company_blocks_v3.json", all_blocks),
                        ("company_ingest_manifest_v3.json", manifest)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (output / "company_chunks_v3.pkl").open("wb") as file:
        pickle.dump(chunks, file)
    print(json.dumps({"stats": manifest["stats"], "validation": validation,
                      "per_document": [{"doc_id": d["doc_id"], **d["stats"]} for d in documents]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
