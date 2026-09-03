from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from collections import Counter
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import HTTPException
import httpx

from app.core.config import settings
from app.services.supabase_service import supabase_service
from app.services.pii_service import redact_pages

EMBEDDING_MODEL = settings.RAG_EMBEDDING_MODEL
EMBEDDING_DIMENSIONS = settings.RAG_EMBEDDING_DIMENSIONS
RERANK_MODEL = settings.RAG_RERANK_MODEL
CHUNK_TARGET_CHARS = settings.RAG_CHUNK_TARGET_CHARS
TEXT_CHUNK_MAX_CHARS = settings.RAG_TEXT_CHUNK_MAX_CHARS
TEXT_CHUNK_OVERLAP_CHARS = settings.RAG_TEXT_CHUNK_OVERLAP_CHARS
DENSE_CANDIDATE_COUNT = settings.RAG_DENSE_CANDIDATE_COUNT
BM25_CANDIDATE_COUNT = settings.RAG_BM25_CANDIDATE_COUNT
QUERY_REWRITING_ENABLED = settings.RAG_QUERY_REWRITING
QUERY_REWRITE_MODEL = settings.RAG_QUERY_REWRITE_MODEL or settings.RAG_LLM_MODEL

_EVIDENCE_NORMALIZATION_VERSION = "facet-evidence-v1"
_EMBEDDING_CACHE_MAX_SIZE = 2048
_embedding_cache: OrderedDict[str, list[float]] = OrderedDict()
_embedding_cache_lock = Lock()


def can_access_company_rag(user_role: str, subscription_tier: str) -> bool:
    return True


@lru_cache(maxsize=1)
def _get_embedding_model() -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL)


@lru_cache(maxsize=1)
def _get_reranker() -> Any:
    import torch
    from FlagEmbedding import FlagReranker

    return FlagReranker(RERANK_MODEL, use_fp16=torch.cuda.is_available())


def _compute_rerank_scores_once(
    reranker: Any, sentence_pairs: list[list[str]], *, normalize: bool = True,
) -> list[float]:
    import numpy as np
    import torch
    from FlagEmbedding.utils.tokenizer_compat import prepare_for_model_compat

    sentence_pairs = reranker.get_detailed_inputs(sentence_pairs)
    batch_size = reranker.batch_size
    max_length = reranker.max_length
    query_max_length = reranker.query_max_length or max_length * 3 // 4
    device = reranker.target_devices[0]
    if device == "cpu":
        reranker.use_fp16 = False
    if reranker.use_fp16:
        reranker.model.half()
    reranker.model.to(device)
    reranker.model.eval()

    all_inputs = []
    for start_index in range(0, len(sentence_pairs), batch_size):
        sentence_batch = sentence_pairs[start_index:start_index + batch_size]
        query_inputs = reranker.tokenizer(
            [pair[0] for pair in sentence_batch], return_tensors=None,
            add_special_tokens=False, max_length=query_max_length, truncation=True,
        )["input_ids"]
        passage_inputs = reranker.tokenizer(
            [pair[1] for pair in sentence_batch], return_tensors=None,
            add_special_tokens=False, max_length=max_length, truncation=True,
        )["input_ids"]
        all_inputs.extend(
            prepare_for_model_compat(
                reranker.tokenizer, query_input, passage_input,
                truncation="only_second", max_length=max_length, padding=False,
            )
            for query_input, passage_input in zip(query_inputs, passage_inputs)
        )

    length_sorted_indices = np.argsort([-len(item["input_ids"]) for item in all_inputs])
    sorted_inputs = [all_inputs[index] for index in length_sorted_indices]

    while True:
        try:
            first_batch = reranker.tokenizer.pad(
                sorted_inputs[:min(len(sorted_inputs), batch_size)],
                padding=True, return_tensors="pt",
            ).to(device)
            with torch.no_grad():
                first_scores = reranker.model(
                    **first_batch, return_dict=True,
                ).logits.view(-1).float().cpu().numpy().tolist()
            break
        except (RuntimeError, torch.cuda.OutOfMemoryError):
            batch_size = batch_size * 3 // 4

    all_scores = first_scores
    for start_index in range(batch_size, len(sorted_inputs), batch_size):
        inputs = reranker.tokenizer.pad(
            sorted_inputs[start_index:start_index + batch_size],
            padding=True, return_tensors="pt",
        ).to(device)
        with torch.no_grad():
            scores = reranker.model(
                **inputs, return_dict=True,
            ).logits.view(-1).float()
        all_scores.extend(scores.cpu().numpy().tolist())

    restored_scores = [all_scores[index] for index in np.argsort(length_sorted_indices)]
    if normalize:
        restored_scores = [float(1 / (1 + np.exp(-score))) for score in restored_scores]
    return restored_scores


SECTION_TITLES = {
    "인적사항", "기본사항", "학력", "학력사항", "경력", "경력사항", "교육", "교육/연수",
    "교육및연수", "연수사항", "수상", "수상내용", "자격", "자격증", "자격사항",
    "보유기술", "기술사항", "자기소개", "자기소개서", "지원동기", "프로젝트", "프로젝트경험",
}

SECTION_KEYWORDS: dict[str, set[str]] = {
    "어디": {"인적사항", "주소", "거주지", "사는곳", "시", "구", "동"},
    "주소": {"인적사항", "주소", "거주지", "사는곳", "시", "구", "동"},
    "거주": {"인적사항", "주소", "거주지", "사는곳", "시", "구", "동"},
    "이름": {"인적사항", "성명", "이름"},
    "연락처": {"인적사항", "연락처", "휴대폰", "전화", "이메일", "e-mail", "mail"},
    "이메일": {"인적사항", "이메일", "e-mail", "mail", "@"},
    "나이": {"인적사항", "생년월일", "생년", "나이", "만"},
    "학력": {"학력", "학교", "학교명", "대학", "대학교", "전공", "학과", "학위", "졸업", "재학"},
    "경력": {"경력", "근무", "근무회사", "회사", "부서", "직위", "담당직무", "재직"},
    "교육": {"교육", "연수", "과정명", "교육기관", "훈련"},
    "자격": {"자격", "자격증", "면허", "취득일자", "발급처"},
    "수상": {"수상", "수상내용", "상훈", "표창"},
}

TITLE_QUERY = re.compile(r"(논문|문서|자료|보고서)?\s*(제목|논문명|문서명|자료명|보고서명)")


def _union_bbox(items: list[dict[str, Any]]) -> list[list[float]] | None:
    points = [point for item in items for point in (item.get("bbox") or []) if len(point) >= 2]
    if not points:
        return None
    xs, ys = [float(point[0]) for point in points], [float(point[1]) for point in points]
    return [[min(xs), min(ys)], [max(xs), max(ys)]]


def _item_rect(item: dict[str, Any]) -> tuple[float, float, float, float] | None:
    points = [point for point in (item.get("bbox") or []) if len(point) >= 2]
    if not points:
        return None
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _group_items_into_lines(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    positioned = [(item, _item_rect(item)) for item in items]
    positioned.sort(key=lambda value: ((value[1] or (0, 10**9, 0, 0))[1], (value[1] or (10**9, 0, 0, 0))[0]))
    lines: list[dict[str, Any]] = []
    for item, rect in positioned:
        if rect is None:
            lines.append({"items": [item], "center": 10**9 + len(lines), "height": 1.0})
            continue
        center = (rect[1] + rect[3]) / 2
        height = max(1.0, rect[3] - rect[1])
        line = next(
            (candidate for candidate in reversed(lines[-4:])
             if abs(candidate["center"] - center) <= max(5.0, min(candidate["height"], height) * 0.65)),
            None,
        )
        if line is None:
            lines.append({"items": [item], "center": center, "height": height})
        else:
            line["items"].append(item)
            count = len(line["items"])
            line["center"] = ((line["center"] * (count - 1)) + center) / count
            line["height"] = max(line["height"], height)
    for line in lines:
        line["items"].sort(key=lambda item: (_item_rect(item) or (10**9, 0, 0, 0))[0])
        line["text"] = " | ".join(str(item.get("text", "")).strip() for item in line["items"] if str(item.get("text", "")).strip())
    return [line for line in lines if line.get("text")]


def _is_section_heading(text: str) -> bool:
    compact = "".join(text.split()).replace("|", "").replace(":", "").strip("-·")
    normalized = re.sub(r"\s+", " ", text).strip(" |:-")
    return (
        compact in SECTION_TITLES
        or (len(compact) <= 12 and any(compact.startswith(title) for title in SECTION_TITLES))
        or normalized.casefold() in _COMMON_SECTION_HEADINGS
        or bool(re.match(r"^(?:제\s*\d+\s*[장절항]|\d+(?:\.\d+){0,3}[.)]?\s+\S)", normalized))
    )


_COMMON_SECTION_HEADINGS = {
    "초록", "요약", "개요", "서론", "배경", "목적", "연구 목적", "연구방법", "연구 방법",
    "방법", "실험", "실험 방법", "결과", "연구 결과", "고찰", "논의", "결론", "참고문헌",
    "부록", "적용 범위", "정의", "절차", "책임", "지원 자격", "평가 기준", "제출 서류",
    "학력", "경력", "프로젝트", "기술", "자격증", "교육", "수상", "자기소개",
    "abstract", "introduction", "background", "methods", "methodology", "results",
    "discussion", "conclusion", "references", "appendix",
}


def _section_heading_level(text: str, *, height: float = 0, median_height: float = 0) -> int | None:
    clean = re.sub(r"\s+", " ", text).strip(" |:-")
    compact = clean.casefold()
    if not clean or len(clean) > 100 or clean.count("|") >= 2:
        return None
    if re.match(r"^제\s*\d+\s*장\b", clean):
        return 1
    if re.match(r"^제\s*\d+\s*절\b", clean):
        return 2
    if re.match(r"^제\s*\d+\s*항\b", clean):
        return 3
    numbered = re.match(r"^(\d+(?:\.\d+){0,3})[.)]?\s+\S", clean)
    if numbered:
        return min(4, numbered.group(1).count(".") + 1)
    if compact in _COMMON_SECTION_HEADINGS or _is_section_heading(clean):
        return 2
    if re.fullmatch(r"[A-Z][A-Z\s-]{2,40}", clean) and any(character.isalpha() for character in clean):
        return 2
    # OCR does not expose font metadata, but bbox height reliably distinguishes
    # many short headings from body lines in scanned and native PDFs.
    if median_height and height >= median_height * 1.35 and len(clean) <= 40:
        digit_ratio = sum(character.isdigit() for character in clean) / max(len(clean), 1)
        if digit_ratio < 0.45 and not clean.endswith((".", "다", "요")):
            return 2
    return None


def _line_top(line: dict[str, Any]) -> float:
    rects = [rect for item in line.get("items", []) if (rect := _item_rect(item))]
    return min((rect[1] for rect in rects), default=0.0)


def _document_heading_markers(pages: list[dict[str, Any]], document_title: str) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    path: list[str] = []
    for page in pages:
        page_number = int(page.get("page") or 1)
        lines = _group_items_into_lines([
            item for item in page.get("items", []) if str(item.get("text", "")).strip()
        ])
        if not lines:
            lines = [
                {"text": text.strip(), "items": [], "height": 0, "plain_index": index}
                for index, text in enumerate(str(page.get("text") or "").splitlines())
                if text.strip()
            ]
        heights = [float(line.get("height") or 0) for line in lines if float(line.get("height") or 0) > 0]
        median_height = sorted(heights)[len(heights) // 2] if heights else 0
        for line in lines:
            text = str(line.get("text") or "").strip(" |")
            if not text or text == document_title:
                continue
            level = _section_heading_level(text, height=float(line.get("height") or 0), median_height=median_height)
            if level is None:
                continue
            path = path[:level - 1]
            path.append(text)
            markers.append({
                "page_number": page_number, "top": _line_top(line) if line.get("items") else float(line.get("plain_index") or 0),
                "section_title": text, "section_path": list(path), "heading_level": level,
            })
    return markers


def _apply_chunk_metadata(
    chunks: list[dict[str, Any]], pages: list[dict[str, Any]], document_title: str,
) -> None:
    markers = _document_heading_markers(pages, document_title)
    active: dict[str, Any] | None = None
    marker_index = 0
    for chunk in chunks:
        page_number = int(chunk.get("page_number") or 1)
        bbox = chunk.get("bbox") or []
        top = min((float(point[1]) for point in bbox if len(point) >= 2), default=float("inf"))
        while marker_index < len(markers):
            marker = markers[marker_index]
            if marker["page_number"] > page_number or (
                marker["page_number"] == page_number and marker["top"] > top
            ):
                break
            active = marker
            marker_index += 1
        if top == float("inf"):
            contained = [marker for marker in markers if marker["section_title"] in str(chunk.get("content") or "")]
            if contained:
                active = contained[-1]
        section_title = active["section_title"] if active else None
        section_path = active["section_path"] if active else []
        heading_level = active["heading_level"] if active else None
        chunk["document_title"] = document_title
        chunk["section_title"] = section_title
        chunk["section_path"] = section_path
        chunk["heading_level"] = heading_level
        metadata = [f"[문서 제목] {document_title}", f"[페이지] {page_number}"]
        if section_title:
            metadata.append(f"[섹션] {section_title}")
        if section_path:
            metadata.append("[장절항 경로] " + " > ".join(section_path))
        chunk["content"] = "\n".join([*metadata, str(chunk.get("content") or "")])


def _append_line_chunks(chunks: list[dict[str, Any]], page_number: int, lines: list[dict[str, Any]]) -> None:
    current: list[dict[str, Any]] = []
    section_heading = ""

    def flush() -> None:
        nonlocal current
        if not current:
            return
        content = "\n".join(line["text"] for line in current)
        chunks.append({
            "page_number": page_number,
            "content": content,
            "bbox": _union_bbox([item for line in current for item in line["items"]]),
        })
        current = []

    for line in lines:
        text = line["text"]
        if _is_section_heading(text):
            flush()
            section_heading = text
            current = [line]
            continue
        current_length = sum(len(value["text"]) + 1 for value in current)
        if current and current_length + len(text) > CHUNK_TARGET_CHARS:
            flush()
            if section_heading and section_heading != text:
                heading_item = {"text": f"[섹션] {section_heading}", "bbox": []}
                current = [{"text": f"[섹션] {section_heading}", "items": [heading_item]}]
        current.append(line)
    flush()


def _split_long_text(text: str) -> list[str]:
    if len(text) <= TEXT_CHUNK_MAX_CHARS:
        return [text.strip()]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + TEXT_CHUNK_MAX_CHARS
        content = text[start:end].strip()
        if content:
            chunks.append(content)
        if end >= len(text):
            break
        start = end - TEXT_CHUNK_OVERLAP_CHARS
    return chunks


def _append_article_chunks(chunks: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
    document_parts = [
        f"\n[[PAGE:{int(page.get('page') or 1)}]]\n{str(page.get('text') or '').strip()}"
        for page in pages
        if str(page.get("text") or "").strip()
    ]
    full_text = "\n".join(document_parts).replace("\r\n", "\n").replace("\r", "\n")
    parts = re.compile(r"(?=제\s*\d+\s*조\s*(?:\([^)]*\))?)").split(full_text)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        for section in _split_long_text(part):
            page_match = re.search(r"\[\[PAGE:(\d+)\]\]", section)
            page_number = int(page_match.group(1)) if page_match else 1
            content = re.sub(r"\[\[PAGE:\d+\]\]", "", section).strip()
            if content:
                chunks.append({"page_number": page_number, "content": content, "bbox": None})


def build_chunks(pages: list[dict[str, Any]], document_title: str | None = None) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    text_only_pages: list[dict[str, Any]] = []
    for page in pages:
        page_number = int(page.get("page") or 1)
        items = [item for item in page.get("items", []) if str(item.get("text", "")).strip()]
        tables = page.get("tables") or []
        for table in tables:
            rows = table.get("rows") or []
            if not rows:
                continue
            headers = [str(value or "").strip() for value in (table.get("columns") or rows[0])]
            column_count = int(table.get("column_count") or max((len(row) for row in rows), default=0))
            row_count = int(table.get("row_count") or len(rows))
            data_rows = rows[1:] if headers == [str(value or "").strip() for value in rows[0]] and len(rows) > 1 else rows
            table_lines = []
            table_bbox = table.get("bbox") or []
            table_xs = [float(point[0]) for point in table_bbox if len(point) >= 2]
            table_ys = [float(point[1]) for point in table_bbox if len(point) >= 2]
            table_rect = (min(table_xs), min(table_ys), max(table_xs), max(table_ys)) if table_xs and table_ys else None
            table_items = []
            for item in items:
                rect = _item_rect(item)
                if rect and table_rect:
                    center_x, center_y = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
                    if table_rect[0] <= center_x <= table_rect[2] and table_rect[1] <= center_y <= table_rect[3]:
                        table_items.append(item)
            header_items = [item for item in table_items if int(item.get("row") or 0) == 1]
            if column_count:
                header_descriptions = []
                for index in range(column_count):
                    header = headers[index] if index < len(headers) else ""
                    if header:
                        header_descriptions.append(f"{index + 1}열: {header}")
                    elif index == 0:
                        header_descriptions.append("1열: 헤더 없음(행 구분)")
                    else:
                        header_descriptions.append(f"{index + 1}열: 헤더 없음")
                table_lines.append({
                    "items": header_items,
                    "text": (
                        f"[표 크기] 헤더 포함 {row_count}행 × {column_count}열\n"
                        "[표 테이블 열 컬럼명] " + " | ".join(header_descriptions)
                    ),
                })
            for row_index, row in enumerate(data_rows, start=2 if data_rows is not rows else 1):
                fields = []
                for column_index, value in enumerate(row):
                    value = str(value or "").strip()
                    if not value:
                        continue
                    if column_index < len(headers) and headers[column_index]:
                        header = f"{column_index + 1}열({headers[column_index]})"
                    elif column_index == 0:
                        header = "1열(행 구분·헤더 없음)"
                    else:
                        header = f"{column_index + 1}열(헤더 없음)"
                    fields.append(f"{header}: {value}")
                if fields:
                    matching = [item for item in table_items if int(item.get("row") or 0) == row_index]
                    table_lines.append({"items": matching, "text": "[표 행] " + " | ".join(fields)})
            _append_line_chunks(chunks, page_number, table_lines)
            table_item_ids = {id(item) for item in table_items}
            items = [item for item in items if id(item) not in table_item_ids]
        if items:
            if page.get("rows") is not None or page.get("sheet_name") is not None:
                items_by_row: dict[int, list[dict[str, Any]]] = {}
                for item in items:
                    row_number = int(item.get("row") or 0)
                    items_by_row.setdefault(row_number, []).append(item)
                spreadsheet_lines = []
                for row_number in sorted(items_by_row):
                    row_items = sorted(items_by_row[row_number], key=lambda item: int(item.get("column") or 0))
                    spreadsheet_lines.append({
                        "items": row_items,
                        "text": " | ".join(str(item.get("text", "")).strip() for item in row_items),
                    })
                _append_line_chunks(chunks, page_number, spreadsheet_lines)
            else:
                _append_line_chunks(chunks, page_number, _group_items_into_lines(items))
        else:
            text_only_pages.append(page)
    _append_article_chunks(chunks, text_only_pages)
    resolved_title = str(document_title or "").strip()
    if not resolved_title:
        extracted = extract_document_title_with_layout(pages)
        resolved_title = extracted[0] if extracted else "제목 없음"
    _apply_chunk_metadata(chunks, pages, resolved_title)
    return chunks


def extract_document_title(pages: list[dict[str, Any]]) -> tuple[str, list[list[float]] | None] | None:
    if not pages:
        return None
    lines = _group_items_into_lines([
        item for item in pages[0].get("items", []) if str(item.get("text", "")).strip()
    ])
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, line in enumerate(lines[:30]):
        text = line["text"].strip(" |")
        compact = text.lower().replace(" ", "")
        if re.search(r"(abstract|초록)", compact):
            break
        if not (6 <= len(text) <= 180):
            continue
        if re.search(r"(https?://|doi|issn|journal|대학교|대학원|박사|석사|교수|제\s*\d+저자|e-mail|@)", text, re.I):
            continue
        if re.search(r"(^\s*\d{2,}\s*[|·]|[*]{1,}|(?:[^,]+,){2,})", text):
            continue
        if sum(character.isalpha() or "가" <= character <= "힣" for character in text) < 5:
            continue
        score = len(text) + (20 if index < 12 else 0) + (15 if any(mark in text for mark in (":", "-", "·")) else 0)
        candidates.append((score, line))
    if not candidates:
        return None
    _, best = max(candidates, key=lambda value: value[0])
    best_index = lines.index(best)
    selected = [best]
    if best_index + 1 < len(lines):
        following = lines[best_index + 1]
        following_text = following["text"].strip()
        if following_text.startswith((":", "-")) and len(following_text) <= 100:
            selected.append(following)
    title = " ".join(line["text"].strip(" |") for line in selected)
    return title, _union_bbox([item for line in selected for item in line["items"]])


def extract_document_title_with_layout(
    pages: list[dict[str, Any]],
) -> tuple[str, list[list[float]] | None] | None:
    """Prefer a prominent single title box over same-baseline callouts."""
    if not pages:
        return None
    items = [item for item in pages[0].get("items", []) if str(item.get("text", "")).strip()]
    candidates = [*_group_items_into_lines(items)]
    candidates.extend({"items": [item], "text": str(item.get("text") or "").strip()} for item in items)
    scored: list[tuple[float, dict[str, Any]]] = []
    for index, line in enumerate(candidates):
        text = str(line.get("text") or "").strip(" |")
        if not 6 <= len(text) <= 140 or text.count("|") >= 2:
            continue
        if re.search(r"(https?://|doi|issn|journal|e-mail|@)", text, re.I):
            continue
        if sum(character.isalpha() for character in text) < 4:
            continue
        bbox = _union_bbox(line.get("items") or [])
        if not bbox:
            continue
        height = float(bbox[1][1]) - float(bbox[0][1])
        top = float(bbox[0][1])
        digit_ratio = sum(character.isdigit() for character in text) / max(len(text), 1)
        score = height * 4 + min(len(text), 60) - top * .04 - text.count("|") * 80 - digit_ratio * 100
        if index < 20:
            score += 15
        scored.append((score, line))
    if not scored:
        return extract_document_title(pages)
    _, best = max(scored, key=lambda value: value[0])
    return str(best["text"]).strip(" |"), _union_bbox(best["items"])


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    try:
        model = await asyncio.to_thread(_get_embedding_model)
        encoded = await asyncio.to_thread(
            model.encode,
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        if encoded.ndim != 2 or encoded.shape != (len(texts), EMBEDDING_DIMENSIONS):
            raise ValueError(
                f"unexpected embedding shape: {encoded.shape}; "
                f"expected ({len(texts)}, {EMBEDDING_DIMENSIONS})"
            )
        return encoded.tolist()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Embedding 모델 {EMBEDDING_MODEL}을 로드하거나 실행할 수 없습니다.",
        ) from exc


def _embedding_cache_key(content: str) -> str:
    material = f"{EMBEDDING_MODEL}\0{_EVIDENCE_NORMALIZATION_VERSION}\0{content}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def _embed_texts_cached(texts: list[str]) -> tuple[list[list[float]], dict[str, int]]:
    if not texts:
        return [], {"hits": 0, "misses": 0}

    keys = [_embedding_cache_key(text) for text in texts]
    vectors: list[list[float] | None] = [None] * len(texts)
    missing_by_key: OrderedDict[str, str] = OrderedDict()
    hits = 0
    with _embedding_cache_lock:
        for index, (key, text) in enumerate(zip(keys, texts)):
            cached = _embedding_cache.get(key)
            if cached is None:
                missing_by_key.setdefault(key, text)
                continue
            _embedding_cache.move_to_end(key)
            vectors[index] = cached
            hits += 1

    missing_keys = list(missing_by_key)
    if missing_keys:
        embedded = await embed_texts(list(missing_by_key.values()))
        embedded_by_key = dict(zip(missing_keys, embedded))
        with _embedding_cache_lock:
            for key, vector in embedded_by_key.items():
                _embedding_cache[key] = vector
                _embedding_cache.move_to_end(key)
            while len(_embedding_cache) > _EMBEDDING_CACHE_MAX_SIZE:
                _embedding_cache.popitem(last=False)
        for index, key in enumerate(keys):
            if vectors[index] is None:
                vectors[index] = embedded_by_key[key]

    return [vector for vector in vectors if vector is not None], {
        "hits": hits,
        "misses": len(missing_keys),
    }


async def rerank_candidates(query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates or not RERANK_MODEL:
        return candidates
    try:
        reranker = await asyncio.to_thread(_get_reranker)
        pairs = [[query, str(candidate.get("content") or "")] for candidate in candidates]
        scores = await asyncio.to_thread(
            _compute_rerank_scores_once, reranker, pairs, normalize=True,
        )
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        if not isinstance(scores, (list, tuple)):
            scores = [scores]
        if len(scores) != len(candidates):
            raise ValueError(f"unexpected reranker score count: {len(scores)}")
        for candidate, score in zip(candidates, scores):
            candidate["rerank_score"] = float(score)
            candidate["similarity"] = float(score)
        return sorted(candidates, key=lambda candidate: candidate["rerank_score"], reverse=True)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Reranker 모델 {RERANK_MODEL}을 로드하거나 실행할 수 없습니다.",
        ) from exc


_BM25_K1 = 1.5
_BM25_B = 0.75
_BM25_PARTICLES = re.compile(
    r"(으로부터|에게서|으로|에서|까지|부터|처럼|보다|이나|이나마|은|는|이|가|을|를|의|와|과|도|에)$"
)


def _bm25_tokens(value: str) -> list[str]:
    """Tokenize Korean/English document text for deterministic BM25 scoring."""
    tokens = re.findall(r"[가-힣A-Za-z0-9]+", str(value or "").lower())
    normalized = []
    for token in tokens:
        stripped = _BM25_PARTICLES.sub("", token)
        if len(stripped) >= 2:
            normalized.append(stripped)
    return normalized


def bm25_candidates(
    query: str, chunks: list[dict[str, Any]], limit: int,
    *, k1: float = _BM25_K1, b: float = _BM25_B,
) -> list[dict[str, Any]]:
    """Rank chunks with Okapi BM25 and return only chunks having lexical evidence."""
    if not chunks or limit <= 0:
        return []
    query_terms = list(dict.fromkeys(_bm25_tokens(query)))
    if not query_terms:
        return []
    documents = [_bm25_tokens(str(chunk.get("content") or "")) for chunk in chunks]
    average_length = sum(len(document) for document in documents) / len(documents) or 1.0
    document_frequency = {
        term: sum(term in set(document) for document in documents)
        for term in query_terms
    }
    scored: list[dict[str, Any]] = []
    total_documents = len(documents)
    for chunk, document in zip(chunks, documents):
        frequencies = Counter(document)
        document_length = len(document)
        score = 0.0
        for term in query_terms:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            frequency_in_documents = document_frequency[term]
            inverse_document_frequency = math.log(
                1.0 + (total_documents - frequency_in_documents + 0.5)
                / (frequency_in_documents + 0.5)
            )
            denominator = frequency + k1 * (1.0 - b + b * document_length / average_length)
            score += inverse_document_frequency * frequency * (k1 + 1.0) / denominator
        if score <= 0:
            continue
        candidate = dict(chunk)
        candidate["bm25_score"] = float(score)
        candidate["retrieval_methods"] = ["bm25"]
        scored.append(candidate)
    scored.sort(key=lambda row: float(row["bm25_score"]), reverse=True)
    for rank, candidate in enumerate(scored[:limit], start=1):
        candidate["bm25_rank"] = rank
    return scored[:limit]


def merge_hybrid_candidates(
    dense: list[dict[str, Any]], lexical: list[dict[str, Any]],
    rewritten_dense: list[dict[str, Any]] | None = None,
    rewritten_lexical: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Deduplicate Dense and BM25 candidates before cross-encoder reranking."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    groups = (
        ("dense", "original", dense),
        ("bm25", "original", lexical),
        ("dense", "rewritten", rewritten_dense or []),
        ("bm25", "rewritten", rewritten_lexical or []),
    )
    for method, query_origin, candidates in groups:
        for rank, candidate in enumerate(candidates, start=1):
            key = str(candidate.get("id") or (
                candidate.get("rag_document_id"), candidate.get("chunk_index")
            ))
            if key not in merged:
                merged[key] = dict(candidate)
                merged[key]["retrieval_methods"] = []
                order.append(key)
            target = merged[key]
            methods = target.setdefault("retrieval_methods", [])
            if method not in methods:
                methods.append(method)
            query_origins = target.setdefault("retrieval_queries", [])
            if query_origin not in query_origins:
                query_origins.append(query_origin)
            if method == "dense":
                target[f"{query_origin}_dense_rank"] = rank
                target.setdefault("dense_rank", rank)
                target["vector_similarity"] = float(candidate.get("similarity") or 0)
            else:
                resolved_rank = int(candidate.get("bm25_rank") or rank)
                target[f"{query_origin}_bm25_rank"] = resolved_rank
                target.setdefault("bm25_rank", resolved_rank)
                target["bm25_score"] = float(candidate.get("bm25_score") or 0)
    return [merged[key] for key in order]


def _query_numbers(value: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,:]\d+)*(?:원|일|개월|시간|분|%|퍼센트)?", value))


async def rewrite_query(query: str) -> dict[str, Any]:
    """Rewrite a user question for retrieval, falling back safely to the original."""
    started = time.perf_counter()
    if not QUERY_REWRITING_ENABLED:
        return {"query": query, "status": "disabled", "latency_ms": 0}
    prompt = f"""당신은 한국어 사내문서 RAG 검색 질의 재작성기입니다.
사용자 질문을 답하지 말고 문서 검색에 적합한 한 문장으로만 재작성하세요.
규칙:
1. 숫자, 날짜, 시간, 금액, 비율, 부서명, 규정명, 고유명사를 그대로 보존합니다.
2. 원문에 없는 조건이나 사실을 추가하지 않습니다.
3. 부정 표현(안 됨, 없음, 금지, 불가)을 보존합니다.
4. 질문 의도를 유지하며 문서에 나올 핵심 용어를 사용합니다.
5. 120자 이내로 작성합니다.
JSON 형식 {{"query":"재작성 질의"}}만 출력하세요.

사용자 질문: {query}"""
    try:
        async with httpx.AsyncClient(
            base_url=settings.OLLAMA_BASE_URL.rstrip("/"),
            timeout=settings.RAG_QUERY_REWRITE_TIMEOUT_SECONDS,
        ) as client:
            response = await client.post("/api/generate", json={
                "model": QUERY_REWRITE_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "keep_alive": "30m",
                "options": {"temperature": 0, "num_predict": 80, "num_ctx": 2048},
            })
            response.raise_for_status()
        body = response.json()
        parsed = json.loads(str(body.get("response") or "{}"))
        rewritten = re.sub(r"\s+", " ", str(parsed.get("query") or "")).strip()[:120]
        if not rewritten:
            raise ValueError("empty rewritten query")
        original_numbers = _query_numbers(query)
        rewritten_numbers = _query_numbers(rewritten)
        if original_numbers != rewritten_numbers:
            raise ValueError("query rewrite changed protected numeric values")
        negative_markers = ("안 ", "않", "없", "못", "금지", "불가")
        if any(marker in query for marker in negative_markers) and not any(
            marker in rewritten for marker in negative_markers
        ):
            raise ValueError("query rewrite removed negation")
        status = "unchanged" if rewritten == query else "rewritten"
        return {
            "query": rewritten,
            "status": status,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "model": QUERY_REWRITE_MODEL,
        }
    except (httpx.HTTPError, ValueError, json.JSONDecodeError, TypeError):
        return {
            "query": query,
            "status": "fallback",
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "model": QUERY_REWRITE_MODEL,
        }


_EVIDENCE_STOP_WORDS = {
    "회사", "사내", "직원", "우리", "오늘", "무엇", "뭘", "몇", "어떻게", "얼마",
    "하나요", "인가요", "되나요", "있나요", "해요", "해야", "가능", "최대", "진행",
}
_EVIDENCE_INTERROGATIVES = {
    "언제", "어디", "누구", "왜", "무엇", "뭘", "어떻게", "몇", "얼마",
}
_EVIDENCE_STEM_ENDINGS = (
    "가능한가요", "하려면", "하나요", "해야", "해서",
)
_EVIDENCE_SUBJECT_NORMALIZATION = {
    "퇴사": "퇴직",
}
_EVIDENCE_PREDICATE_ENDINGS = (
    "하다", "한다", "하나요", "인가요", "되나요", "있나요", "해야", "해요", "해서",
    "하려면", "되면", "내야", "쉬게", "넘게", "주나요", "가능한가요", "서", "게", "면", "해",
)
_KOREAN_DURATION_NORMALIZATION = {
    "하루": "1일", "이틀": "2일", "사흘": "3일", "나흘": "4일",
    "한 달": "1개월", "두 달": "2개월", "세 달": "3개월",
}
_EVIDENCE_SEMANTIC_THRESHOLD = 0.55


def _normalize_evidence_text(value: str) -> str:
    normalized = str(value or "").lower()
    for source, replacement in _KOREAN_DURATION_NORMALIZATION.items():
        normalized = normalized.replace(source, replacement)

    def amount(match: re.Match[str]) -> str:
        return f"{int(float(match.group(1).replace(',', '')) * 10_000)}원"

    normalized = re.sub(r"(\d+(?:\.\d+)?)\s*만원", amount, normalized)
    normalized = re.sub(r"(\d[\d,]*)\s*원", lambda match: f"{match.group(1).replace(',', '')}원", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _evidence_sentences(candidates: list[dict[str, Any]]) -> list[str]:
    units: list[str] = []
    for candidate in candidates:
        content = _normalize_evidence_text(str(candidate.get("content") or ""))
        if not content:
            continue
        units.append(content)
        sentences = [
            sentence.strip()
            for sentence in re.split(r"[\n.!?]+", content)
            if len(sentence.strip()) >= 4
        ]
        units.extend(sentences[:2])
    return list(dict.fromkeys(units))


def _strip_korean_particle(token: str) -> str:
    return re.sub(r"(으로|에서|에게|한테|까지|부터|처럼|보다|이나|나|은|는|이|가|을|를|의|와|과|도|에)$", "", token)


def _normalize_evidence_token(raw_token: str) -> str:
    has_object_particle = bool(re.search(r"(을|를)$", raw_token))
    token = _strip_korean_particle(raw_token)
    for suffix in _EVIDENCE_STEM_ENDINGS:
        if not token.endswith(suffix):
            continue
        stem = token[:-len(suffix)]
        token = stem if len(stem) >= 2 else ""
        break
    if len(token) < 2 or token in _EVIDENCE_STOP_WORDS or token in _EVIDENCE_INTERROGATIVES:
        return ""
    if token.endswith(_EVIDENCE_PREDICATE_ENDINGS):
        if not (has_object_particle and token.endswith(("서", "게", "면", "해"))):
            return ""
    return token


def _extract_evidence_facets(query: str) -> dict[str, Any]:
    normalized = _normalize_evidence_text(query)
    raw_tokens = re.findall(r"\d+(?:원|일|개월|시간|퍼센트|%)?|[가-힣a-zA-Z]+", normalized)
    tokens = []
    for raw_token in raw_tokens:
        token = _normalize_evidence_token(raw_token)
        if not token:
            continue
        tokens.append(token)
    tokens = list(dict.fromkeys(tokens))
    conditions = re.findall(r"\d+(?:원|일|개월|시간|퍼센트|%)", normalized)
    strong_subjects = [
        _EVIDENCE_SUBJECT_NORMALIZATION.get(token, token) for token in tokens
        if len(token) >= 2 and not re.fullmatch(r"\d+(?:원|일|개월|시간|퍼센트|%)", token)
        and not token.endswith(("아서", "어서", "려고", "짜리"))
    ]
    return {
        "query": normalized,
        "tokens": tokens,
        "conditions": conditions,
        "strong_subjects": strong_subjects,
    }


def _quantity(value: str) -> tuple[float, str] | None:
    match = re.fullmatch(r"(\d+)(원|일|개월|시간|퍼센트|%)", value)
    if not match:
        return None
    unit = "퍼센트" if match.group(2) == "%" else match.group(2)
    return float(match.group(1)), unit


def _condition_supported(condition: str, evidence: str) -> bool:
    if condition in evidence:
        return True
    expected = _quantity(condition)
    if not expected:
        return False
    expected_number, expected_unit = expected
    comparisons = re.findall(
        r"(\d+)(원|일|개월|시간|퍼센트|%)(?:을|를|이|가|은|는)?\s*(초과|이상|이하|미만|한도)",
        evidence,
    )
    for raw_number, raw_unit, operator in comparisons:
        unit = "퍼센트" if raw_unit == "%" else raw_unit
        if unit != expected_unit:
            continue
        threshold = float(raw_number)
        if operator == "초과" and expected_number > threshold:
            return True
        if operator == "이상" and expected_number >= threshold:
            return True
        if operator == "이하" and expected_number <= threshold:
            return True
        if operator == "미만" and expected_number < threshold:
            return True
        if operator == "한도":
            return True
    values = [
        quantity for raw in re.findall(r"\d+(?:원|일|개월|시간|퍼센트|%)", evidence)
        if (quantity := _quantity(raw)) and quantity[1] == expected[1]
    ]
    numbers = sorted({number for number, _unit in values})
    return any(left <= expected[0] <= right for left, right in zip(numbers, numbers[1:]))


def _is_table_structure_query(query: str) -> bool:
    normalized = "".join(query.lower().split())
    return (
        any(term in normalized for term in ("표", "테이블"))
        and any(term in normalized for term in ("열", "컬럼", "행", "헤더"))
    )


async def _has_facet_evidence(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    facets: dict[str, Any] | None = None,
    facet_vectors: list[list[float]] | None = None,
) -> bool:
    if not candidates:
        return False
    facets = facets or _extract_evidence_facets(query)
    units = _evidence_sentences(candidates)
    if not units:
        return False
    combined_evidence = "\n".join(units)
    lexical_hits = [token for token in facets["tokens"] if token in combined_evidence]
    strong_subjects = facets["strong_subjects"]

    asks_for_table_structure = _is_table_structure_query(query)
    if asks_for_table_structure and "[표 테이블 열 컬럼명]" in combined_evidence:
        return True

    facet_texts = [facets["query"], *strong_subjects]
    if facet_vectors is None or len(facet_vectors) != len(facet_texts):
        facet_vectors, _ = await _embed_texts_cached(facet_texts)
    unit_vectors, _ = await _embed_texts_cached(units)
    query_scores = [
        sum(left * right for left, right in zip(facet_vectors[0], unit_vector))
        for unit_vector in unit_vectors
    ]
    if not query_scores or max(query_scores) < _EVIDENCE_SEMANTIC_THRESHOLD:
        return False

    if strong_subjects:
        subject_supported = any(
            subject in combined_evidence
            or max(
                sum(left * right for left, right in zip(subject_vector, unit_vector))
                for unit_vector in unit_vectors
            ) >= 0.60
            for subject, subject_vector in zip(strong_subjects, facet_vectors[1:])
        )
        if not subject_supported:
            return False

    conditions = facets["conditions"]
    if conditions:
        linked = False
        for unit, score in zip(units, query_scores):
            if score < _EVIDENCE_SEMANTIC_THRESHOLD:
                continue
            if all(_condition_supported(condition, unit) for condition in conditions):
                linked = True
                break
        if not linked:
            return False

    return bool(lexical_hits or conditions)


async def index_document(user_email: str, document_id: str) -> dict[str, Any]:
    document = supabase_service.get_ocr_document(user_email, document_id)
    try:
        pages = document.get("bounding_boxes") or []
        extracted_title = extract_document_title_with_layout(pages)
        document_title = extracted_title[0] if extracted_title else Path(document.get("file_name") or "document").stem
        chunks = build_chunks(redact_pages(pages), document_title=document_title)
        if not chunks:
            raise HTTPException(status_code=422, detail="RAG로 인덱싱할 추출 텍스트가 없습니다.")
        embeddings = await embed_texts([chunk["content"] for chunk in chunks])
        return supabase_service.replace_rag_index(
            user_email=user_email, document={**document, "rag_title": document_title}, chunks=chunks,
            embeddings=embeddings, embedding_model=EMBEDDING_MODEL,
        )
    except Exception as exc:
        try:
            supabase_service.mark_rag_failed(user_email, document_id, str(exc))
        except Exception:
            pass
        raise


async def search(
    user_email: str, query: str, rag_document_id: str | None, limit: int, *,
    user_role: str = "USER", subscription_tier: str = "PERSONAL",
) -> list[dict[str, Any]]:
    retrieval_started = time.perf_counter()
    stage_latency_ms = {
        "query_rewrite": 0.0, "embedding": 0.0, "dense": 0.0,
        "bm25": 0.0, "reranker": 0.0,
    }
    # A selected document already contains deterministic OCR table metadata.
    # Fetch it directly: vector similarity is not a reliable way to locate a
    # schema marker for short questions such as "what are all the columns?".
    if rag_document_id and _is_table_structure_query(query):
        document_chunks = supabase_service.list_rag_chunks(user_email, rag_document_id)
        schema_candidates = [
            row for row in document_chunks
            if "[표 테이블 열 컬럼명]" in str(row.get("content") or "")
        ]
        if schema_candidates:
            for row in schema_candidates:
                row["similarity"] = 1.0
                row["vector_similarity"] = 1.0
            return schema_candidates[:limit]

    rewrite_result = (
        {"query": query, "status": "structural_bypass", "latency_ms": 0}
        if _is_table_structure_query(query)
        else await rewrite_query(query)
    )
    stage_latency_ms["query_rewrite"] = float(rewrite_result.get("latency_ms") or 0)
    rewritten_query = str(rewrite_result.get("query") or query)
    use_rewritten_query = rewritten_query != query

    facets = _extract_evidence_facets(query)
    strong_subjects = facets["strong_subjects"]
    facet_texts = [facets["query"], *strong_subjects]
    query_texts = [query, *facet_texts]
    if use_rewritten_query:
        query_texts.append(rewritten_query)
    stage_started = time.perf_counter()
    query_vectors, _ = await _embed_texts_cached(query_texts)
    stage_latency_ms["embedding"] = (time.perf_counter() - stage_started) * 1000
    embedding = query_vectors[0]
    facet_vectors = query_vectors[1:1 + len(facet_texts)]
    rewritten_embedding = query_vectors[-1] if use_rewritten_query else None
    stage_started = time.perf_counter()
    dense_candidates = supabase_service.search_rag_chunks(
        user_email, embedding, rag_document_id, DENSE_CANDIDATE_COUNT,
        include_company_documents=can_access_company_rag(user_role, subscription_tier),
    )
    dense_original_elapsed = (time.perf_counter() - stage_started) * 1000
    stage_started = time.perf_counter()
    lexical_chunks = supabase_service.list_accessible_rag_chunks(
        user_email, rag_document_id,
        include_company_documents=can_access_company_rag(user_role, subscription_tier),
    )
    lexical_candidates = bm25_candidates(query, lexical_chunks, BM25_CANDIDATE_COUNT)
    bm25_original_elapsed = (time.perf_counter() - stage_started) * 1000
    stage_started = time.perf_counter()
    rewritten_dense_candidates = (
        supabase_service.search_rag_chunks(
            user_email, rewritten_embedding, rag_document_id, DENSE_CANDIDATE_COUNT,
            include_company_documents=can_access_company_rag(user_role, subscription_tier),
        )
        if rewritten_embedding is not None else []
    )
    stage_latency_ms["dense"] = dense_original_elapsed + (time.perf_counter() - stage_started) * 1000
    stage_started = time.perf_counter()
    rewritten_lexical_candidates = (
        bm25_candidates(rewritten_query, lexical_chunks, BM25_CANDIDATE_COUNT)
        if use_rewritten_query else []
    )
    stage_latency_ms["bm25"] = bm25_original_elapsed + (time.perf_counter() - stage_started) * 1000
    candidates = merge_hybrid_candidates(
        dense_candidates, lexical_candidates,
        rewritten_dense_candidates, rewritten_lexical_candidates,
    )
    compact_query = "".join(query.lower().split())
    requested_sections = [keywords for name, keywords in SECTION_KEYWORDS.items() if name in compact_query]
    query_terms = {
        token for token in query.lower().replace("?", " ").replace(".", " ").split()
        if len(token) >= 2 and token not in {"어떻게", "알려줘", "알려주세요", "무엇", "뭐야", "지원자", "지원자의"}
    }
    for row in candidates:
        content = str(row.get("content", "")).lower()
        section_hits = sum(1 for keywords in requested_sections for keyword in keywords if keyword in content)
        term_hits = sum(1 for term in query_terms if term.rstrip("은는이가을를의") in content)
        lexical_boost = min(0.45, section_hits * 0.07 + term_hits * 0.04)
        row["vector_similarity"] = float(row.get("similarity") or 0)
        row["similarity"] = min(1.0, row["vector_similarity"] + lexical_boost)
    candidates.sort(key=lambda row: float(row.get("similarity") or 0), reverse=True)

    # Table row/column/header questions are answered from deterministic OCR
    # metadata.  Running the large CPU reranker here can exceed the browser's
    # request timeout even though the exact schema is already in a chunk.
    if _is_table_structure_query(query):
        schema_candidates = [
            row for row in candidates
            if "[표 테이블 열 컬럼명]" in str(row.get("content") or "")
        ]
        if schema_candidates:
            return schema_candidates[:limit]

    stage_started = time.perf_counter()
    candidates = await rerank_candidates(query, candidates)
    stage_latency_ms["reranker"] = (time.perf_counter() - stage_started) * 1000
    count_query = re.search(r"(?:몇\s*(?:문제|문항)|(?:문제|문항)\s*수|총\s*문제)", query)
    if rag_document_id and count_query:
        all_chunks = supabase_service.list_rag_chunks(user_email, rag_document_id)
        numbered: list[tuple[int, dict[str, Any]]] = []
        for chunk in all_chunks:
            content = str(chunk.get("content") or "")
            numbers: list[int] = []
            for start, end in re.findall(r"(\d{1,2})\s*[~～\-–]\s*(\d{1,2})", content):
                numbers.extend((int(start), int(end)))
            numbers.extend(
                int(value)
                for value in re.findall(r"(?:^|\s)(\d{1,2})\s*(?:번|[.)])", content, flags=re.MULTILINE)
            )
            valid = [value for value in numbers if 1 <= value <= 100]
            if valid:
                numbered.append((max(valid), chunk))
        if numbered:
            highest, evidence = max(numbered, key=lambda item: item[0])
            candidates.insert(0, {
                "id": f"question-count-{rag_document_id}",
                "document_id": evidence["document_id"],
                "rag_document_id": rag_document_id,
                "chunk_index": evidence.get("chunk_index", -1),
                "page_number": evidence.get("page_number", 1),
                "content": (
                    f"[문서 전체 집계] 모든 페이지에서 확인한 가장 큰 문제 번호는 {highest}번입니다. "
                    f"문제 번호가 1번부터 연속된 시험지라면 총 {highest}문제입니다. "
                    f"마지막 문제 번호 근거: {str(evidence.get('content') or '')[:500]}"
                ),
                "bbox": evidence.get("bbox"),
                "similarity": 1.0,
                "vector_similarity": 1.0,
                "source": evidence.get("source", "문서"),
            })
    if rag_document_id and TITLE_QUERY.search(query.replace("?", "")):
        rag_document = next((item for item in supabase_service.list_rag_documents(user_email) if item.get("id") == rag_document_id), None)
        if rag_document:
            document = supabase_service.get_ocr_document(user_email, rag_document["document_id"])
            extracted = extract_document_title_with_layout(document.get("bounding_boxes") or [])
            if extracted:
                title, bbox = extracted
                candidates.insert(0, {
                    "id": f"title-{document['id']}", "document_id": document["id"],
                    "rag_document_id": rag_document_id, "chunk_index": -1, "page_number": 1,
                    "content": f"[문서 제목] {title}", "bbox": bbox, "similarity": 1.0,
                    "vector_similarity": 1.0, "source": document["file_name"],
                })
    for candidate in candidates:
        candidate["original_query"] = query
        candidate["rewritten_query"] = rewritten_query if use_rewritten_query else None
        candidate["query_rewrite_status"] = rewrite_result.get("status")
        candidate["query_rewrite_latency_ms"] = int(rewrite_result.get("latency_ms") or 0)
        candidate["query_rewrite_model"] = rewrite_result.get("model")
        candidate["retrieval_latency_ms"] = {
            **{name: round(value, 2) for name, value in stage_latency_ms.items()},
            "total": round((time.perf_counter() - retrieval_started) * 1000, 2),
        }
    candidates = candidates[:limit]
    return candidates if await _has_facet_evidence(
        query, candidates, facets=facets, facet_vectors=facet_vectors,
    ) else []


rag_service = type("RagService", (), {"index_document": staticmethod(index_document), "search": staticmethod(search)})()
