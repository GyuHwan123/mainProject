from __future__ import annotations

import asyncio
import re
from functools import lru_cache
from typing import Any

from fastapi import HTTPException

from app.core.config import settings
from app.services.supabase_service import supabase_service
from app.services.pii_service import redact_pages

EMBEDDING_MODEL = settings.RAG_EMBEDDING_MODEL
EMBEDDING_DIMENSIONS = settings.RAG_EMBEDDING_DIMENSIONS
RERANK_MODEL = settings.RAG_RERANK_MODEL
CHUNK_TARGET_CHARS = settings.RAG_CHUNK_TARGET_CHARS
TEXT_CHUNK_MAX_CHARS = settings.RAG_TEXT_CHUNK_MAX_CHARS
TEXT_CHUNK_OVERLAP_CHARS = settings.RAG_TEXT_CHUNK_OVERLAP_CHARS


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
    return compact in SECTION_TITLES or (len(compact) <= 12 and any(compact.startswith(title) for title in SECTION_TITLES))


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


def build_chunks(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    text_only_pages: list[dict[str, Any]] = []
    for page in pages:
        page_number = int(page.get("page") or 1)
        items = [item for item in page.get("items", []) if str(item.get("text", "")).strip()]
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


async def index_document(user_email: str, document_id: str) -> dict[str, Any]:
    document = supabase_service.get_ocr_document(user_email, document_id)
    try:
        chunks = build_chunks(redact_pages(document.get("bounding_boxes") or []))
        if not chunks:
            raise HTTPException(status_code=422, detail="RAG로 인덱싱할 추출 텍스트가 없습니다.")
        embeddings = await embed_texts([chunk["content"] for chunk in chunks])
        return supabase_service.replace_rag_index(
            user_email=user_email, document=document, chunks=chunks,
            embeddings=embeddings, embedding_model=EMBEDDING_MODEL,
        )
    except Exception as exc:
        try:
            supabase_service.mark_rag_failed(user_email, document_id, str(exc))
        except Exception:
            pass
        raise


async def search(user_email: str, query: str, rag_document_id: str | None, limit: int) -> list[dict[str, Any]]:
    embedding = (await embed_texts([query]))[0]
    candidates = supabase_service.search_rag_chunks(
        user_email, embedding, rag_document_id, 4,
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
    candidates = await rerank_candidates(query, candidates)
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
            extracted = extract_document_title(document.get("bounding_boxes") or [])
            if extracted:
                title, bbox = extracted
                candidates.insert(0, {
                    "id": f"title-{document['id']}", "document_id": document["id"],
                    "rag_document_id": rag_document_id, "chunk_index": -1, "page_number": 1,
                    "content": f"[문서 제목] {title}", "bbox": bbox, "similarity": 1.0,
                    "vector_similarity": 1.0, "source": document["file_name"],
                })
    return candidates[:limit]


rag_service = type("RagService", (), {"index_document": staticmethod(index_document), "search": staticmethod(search)})()
