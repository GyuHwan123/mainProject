from __future__ import annotations

import base64
import hashlib
import html
import json
import math
import re
from threading import Lock
from typing import Any

import httpx
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.api.routes.auth import require_current_user
from app.api.routes.chatbot import ChatMessage, ask_chatbot
from app.core.config import settings
from app.models.user import User
from app.services.rag_service import embed_texts, rag_service
from app.services.supabase_service import COMPANY_RAG_DOCUMENT_IDS, supabase_service

router = APIRouter()
_latest_evaluations: dict[str, dict[str, Any]] = {}
_umap_cache: dict[str, Any] = {}
_umap_cache_lock = Lock()
_UMAP_COLORS = {"HR": "#2563eb", "GA": "#16a34a", "IS": "#9333ea", "SH": "#ea580c", "ER": "#dc2626"}


class RagEvaluationCase(BaseModel):
    question_id: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=4_000)
    question_type: str | None = None
    difficulty: str | None = None
    expected_documents: list[str]
    expected_document_titles: list[str] = Field(default_factory=list)
    expected_sections: list[str] = Field(default_factory=list)
    expected_answer: str
    answerable: bool


class RagEvaluationDataset(BaseModel):
    dataset_name: str = Field(min_length=1, max_length=200)
    question_count: int = Field(ge=1, le=500)
    cases: list[RagEvaluationCase] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_case_count(self) -> "RagEvaluationDataset":
        if self.question_count != len(self.cases):
            raise ValueError("question_count와 cases 개수가 일치해야 합니다.")
        return self


def require_developer(user: User = Depends(require_current_user)) -> User:
    if user.role not in {"DEVELOPER", "ADMIN"} and user.email != "developer@docunex.com":
        raise HTTPException(status_code=403, detail="개발자 권한이 필요합니다.")
    return user


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _ndcg(retrieved: list[str], expected: set[str], k: int) -> float:
    # Binary document relevance: relevant documents receive gain 1, all others 0.
    dcg = sum(1.0 / math.log2(rank + 2) for rank, doc_id in enumerate(retrieved[:k]) if doc_id in expected)
    ideal_count = min(len(expected), k)
    idcg = sum(1.0 / math.log2(rank + 2) for rank in range(ideal_count))
    return dcg / idcg if idcg else 0.0


def _is_rejection(answer: str) -> bool:
    compact = re.sub(r"\s+", "", answer.lower())
    return any(phrase in compact for phrase in (
        "확인할수없", "찾을수없", "근거가없", "정보가없", "제공되지않",
        "알수없", "답변할수없", "문서에없",
    ))


def _catalog_maps() -> tuple[dict[str, str], dict[str, str]]:
    filename_to_id: dict[str, str] = {}
    title_to_id: dict[str, str] = {}
    for document in supabase_service.list_rag_document_catalog():
        doc_id = str(document.get("doc_id") or "")
        if not re.fullmatch(r"[A-Z]{2,10}-\d{2,10}", doc_id):
            continue
        filename_to_id[str(document.get("filename") or "").casefold()] = doc_id
        title_to_id[str(document.get("title") or "").casefold()] = doc_id
    return filename_to_id, title_to_id


def _retrieved_document_ids(
    sources: list[dict[str, Any]], filename_to_id: dict[str, str], title_to_id: dict[str, str],
) -> list[str]:
    resolved = []
    for source in sources:
        source_name = str(source.get("source") or "")
        resolved.append(
            filename_to_id.get(source_name.casefold())
            or title_to_id.get(source_name.casefold())
            or str(source.get("document_id") or "")
        )
    return _unique(resolved)


@router.post("/evaluate")
async def evaluate_rag(
    dataset: RagEvaluationDataset,
    user: User = Depends(require_developer),
) -> dict[str, Any]:
    top_k = settings.RAG_TOP_K
    answer_threshold = settings.RAG_EVALUATION_ANSWER_THRESHOLD
    filename_to_id, title_to_id = _catalog_maps()
    retrieval_scores: dict[str, list[float]] = {
        "hit": [], "hit_at_1": [], "hit_at_3": [], "hit_at_5": [],
        "recall": [], "mrr": [], "ndcg": [],
    }
    answer_accuracies: list[float] = []
    citation_accuracies: list[float] = []
    faithfulness_scores: list[float] = []
    rejection_scores: list[float] = []
    case_results: list[dict[str, Any]] = []

    for case in dataset.cases:
        sources = await rag_service.search(user.email, case.question, None, top_k)
        retrieved_documents = _retrieved_document_ids(sources, filename_to_id, title_to_id)
        expected = set(case.expected_documents)
        matched = expected.intersection(retrieved_documents)
        hit = bool(matched)
        recall = len(matched) / len(expected) if expected else 0.0
        first_rank = next((rank for rank, doc_id in enumerate(retrieved_documents, 1) if doc_id in expected), None)
        reciprocal_rank = 1.0 / first_rank if first_rank else 0.0
        ndcg = _ndcg(retrieved_documents, expected, top_k)
        if expected:
            retrieval_scores["hit"].append(float(hit))
            retrieval_scores["hit_at_1"].append(float(bool(expected.intersection(retrieved_documents[:1]))))
            retrieval_scores["hit_at_3"].append(float(bool(expected.intersection(retrieved_documents[:3]))))
            retrieval_scores["hit_at_5"].append(float(bool(expected.intersection(retrieved_documents[:5]))))
            retrieval_scores["recall"].append(recall)
            retrieval_scores["mrr"].append(reciprocal_rank)
            retrieval_scores["ndcg"].append(ndcg)
        citation_accuracy = len(matched) / len(set(retrieved_documents)) if retrieved_documents else 0.0
        citation_accuracies.append(citation_accuracy)

        context = "\n\n".join(
            f"[근거 {index + 1} · {source.get('source', '문서')} · "
            f"{source.get('page_number', 1)}페이지 · Chunk {source.get('chunk_index', 0) + 1}] "
            f"{source.get('content', '')}"
            for index, source in enumerate(sources)
        )
        reply = await ask_chatbot(ChatMessage(message=case.question, context=context, history=[]), user)
        answer = reply.reply
        rejected = _is_rejection(answer)
        answer_score: float | None = None
        answer_correct: bool | None = None
        if case.answerable:
            expected_vector, answer_vector, context_vector = await embed_texts([
                case.expected_answer, answer, context or "제공된 문서 근거가 없습니다.",
            ])
            answer_score = sum(left * right for left, right in zip(expected_vector, answer_vector))
            answer_correct = answer_score >= answer_threshold
            answer_accuracies.append(float(answer_correct))
            faithfulness_score = sum(left * right for left, right in zip(answer_vector, context_vector))
        else:
            rejection_scores.append(float(rejected))
            if rejected:
                faithfulness_score = 1.0
            elif context:
                answer_vector, context_vector = await embed_texts([answer, context])
                faithfulness_score = sum(left * right for left, right in zip(answer_vector, context_vector))
            else:
                faithfulness_score = 0.0
        faithfulness_score = max(0.0, min(1.0, faithfulness_score))
        faithfulness_scores.append(faithfulness_score)

        case_results.append({
            "question_id": case.question_id,
            "question": case.question,
            "question_type": case.question_type,
            "difficulty": case.difficulty,
            "answerable": case.answerable,
            "expected_documents": case.expected_documents,
            "retrieved_documents": retrieved_documents,
            "answer": answer,
            "expected_answer": case.expected_answer,
            "hit": hit,
            "recall": recall,
            "reciprocal_rank": reciprocal_rank,
            "ndcg_at_k": ndcg,
            "answer_score": answer_score,
            "answer_correct": answer_correct,
            "faithfulness": faithfulness_score,
            "hallucination_score": 1.0 - faithfulness_score,
            "citation_accuracy": citation_accuracy,
            "rejected": rejected,
            "sources": sources,
        })

    result = {
        "dataset_name": dataset.dataset_name,
        "summary": {
            "total": len(dataset.cases),
            "retrieval_evaluated": len(retrieval_scores["hit"]),
            "top_k": top_k,
            "answer_threshold": answer_threshold,
            "hit_at_k": _mean(retrieval_scores["hit"]),
            "hit_at_1": _mean(retrieval_scores["hit_at_1"]),
            "hit_at_3": _mean(retrieval_scores["hit_at_3"]),
            "hit_at_5": _mean(retrieval_scores["hit_at_5"]),
            "recall_at_k": _mean(retrieval_scores["recall"]),
            "mrr": _mean(retrieval_scores["mrr"]),
            "ndcg_at_k": _mean(retrieval_scores["ndcg"]),
            "answer_accuracy": _mean(answer_accuracies),
            "citation_accuracy": _mean(citation_accuracies),
            "context_precision": _mean(citation_accuracies),
            "faithfulness": _mean(faithfulness_scores),
            "hallucination_rate": _mean([1.0 - score for score in faithfulness_scores]),
            "faithfulness_method": "BGE-M3 cosine(answer, context); grounded rejection=1.0",
            "unanswerable_rejection_rate": _mean(rejection_scores),
        },
        "cases": case_results,
    }
    _latest_evaluations[user.email] = result
    return result


@router.get("/evaluate/latest")
def latest_rag_evaluation(user: User = Depends(require_developer)) -> dict[str, Any]:
    result = _latest_evaluations.get(user.email)
    if not result:
        raise HTTPException(status_code=404, detail="현재 Backend 프로세스에 저장된 RAG 평가 결과가 없습니다.")
    return result


@router.get("/evaluation/umap")
def rag_evaluation_umap(user: User = Depends(require_developer)) -> dict[str, Any]:
    document_response = httpx.get(
        f"{supabase_service.url}/rest/v1/rag_documents",
        params={
            "select": "id,doc_id,filename",
            "doc_id": f"in.({','.join(COMPANY_RAG_DOCUMENT_IDS)})",
            "order": "doc_id.asc",
        },
        headers=supabase_service._service_headers(), timeout=30,
    )
    supabase_service._raise_for_supabase(document_response, "기업 RAG 문서 조회 실패")
    documents = document_response.json()
    if len(documents) != len(COMPANY_RAG_DOCUMENT_IDS):
        raise HTTPException(
            status_code=503,
            detail=f"기업문서가 {len(documents)}개 조회되었습니다. 정확히 {len(COMPANY_RAG_DOCUMENT_IDS)}개가 필요합니다.",
        )
    document_by_id = {row["id"]: row for row in documents}
    chunk_response = httpx.get(
        f"{supabase_service.url}/rest/v1/rag_chunks",
        params={
            "select": "id,document_id,chunk_index,embedding",
            "document_id": f"in.({','.join(document_by_id)})",
            "order": "document_id.asc,chunk_index.asc",
            "limit": "5000",
        },
        headers=supabase_service._service_headers(), timeout=60,
    )
    supabase_service._raise_for_supabase(chunk_response, "기업 RAG chunk 조회 실패")
    chunks = chunk_response.json()
    if len(chunks) < 3:
        raise HTTPException(status_code=503, detail="UMAP 생성에는 기업 RAG chunk가 3개 이상 필요합니다.")

    vectors: list[np.ndarray] = []
    point_metadata: list[dict[str, Any]] = []
    signature_rows = []
    for chunk in chunks:
        document = document_by_id.get(chunk.get("document_id"))
        if not document:
            continue
        raw_embedding = chunk.get("embedding")
        vector = (
            np.fromstring(raw_embedding.strip("[]"), sep=",", dtype=np.float32)
            if isinstance(raw_embedding, str)
            else np.asarray(raw_embedding, dtype=np.float32)
        )
        if vector.shape != (1024,) or not np.isfinite(vector).all():
            raise HTTPException(
                status_code=503,
                detail=f"chunk {chunk.get('id')} embedding이 유효한 1024차원 벡터가 아닙니다.",
            )
        metadata = {
            "doc_id": document["doc_id"],
            "filename": document.get("filename") or document["doc_id"],
            "chunk_index": int(chunk.get("chunk_index") or 0),
        }
        vectors.append(vector)
        point_metadata.append(metadata)
        signature_rows.append((chunk.get("id"), metadata["doc_id"], metadata["chunk_index"], raw_embedding))
    matrix = np.vstack(vectors)
    signature = hashlib.sha256(
        json.dumps(signature_rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    with _umap_cache_lock:
        if _umap_cache.get("signature") == signature:
            return {**_umap_cache["result"], "cache_hit": True}

        try:
            import umap

            coordinates = umap.UMAP(
                n_components=2,
                n_neighbors=min(15, len(matrix) - 1),
                min_dist=0.1,
                metric="cosine",
                random_state=42,
            ).fit_transform(matrix)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="현재 corpus UMAP을 생성할 수 없습니다.") from exc

        width, height, padding = 960, 540, 54
        minimum = coordinates.min(axis=0)
        span = np.maximum(coordinates.max(axis=0) - minimum, 1e-6)
        scaled = (coordinates - minimum) / span
        plotted = np.column_stack((
            padding + scaled[:, 0] * (width - padding * 2),
            height - padding - scaled[:, 1] * (height - padding * 2),
        ))
        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
            '<rect width="100%" height="100%" fill="#ffffff"/>',
            '<text x="54" y="28" fill="#17304c" font-family="Arial,sans-serif" font-size="16" font-weight="700">Current Corporate RAG Corpus · BGE-M3 UMAP</text>',
        ]
        for (x_value, y_value), metadata in zip(plotted, point_metadata):
            group = metadata["doc_id"].split("-", 1)[0]
            tooltip = html.escape(
                f'{metadata["doc_id"]} · {metadata["filename"]} · chunk {metadata["chunk_index"]}'
            )
            svg_parts.append(
                f'<circle cx="{x_value:.2f}" cy="{y_value:.2f}" r="6" fill="{_UMAP_COLORS.get(group, "#64748b")}" fill-opacity="0.78" stroke="#ffffff" stroke-width="1"><title>{tooltip}</title></circle>'
            )
        legend_x = width - 250
        for index, group in enumerate(_UMAP_COLORS):
            x_value = legend_x + index * 48
            svg_parts.extend((
                f'<circle cx="{x_value}" cy="{height - 18}" r="5" fill="{_UMAP_COLORS[group]}"/>',
                f'<text x="{x_value + 8}" y="{height - 14}" fill="#52647b" font-family="Arial,sans-serif" font-size="10">{group}</text>',
            ))
        svg_parts.append("</svg>")
        svg = "".join(svg_parts)
        result = {
            "image_data_url": f"data:image/svg+xml;base64,{base64.b64encode(svg.encode('utf-8')).decode('ascii')}",
            "document_count": len(documents),
            "chunk_count": len(chunks),
            "input_shape": [int(matrix.shape[0]), int(matrix.shape[1])],
            "output_shape": [int(coordinates.shape[0]), int(coordinates.shape[1])],
            "cache_key": signature,
            "cache_hit": False,
            "groups": list(_UMAP_COLORS),
        }
        _umap_cache.clear()
        _umap_cache.update({"signature": signature, "result": result})
        return result
