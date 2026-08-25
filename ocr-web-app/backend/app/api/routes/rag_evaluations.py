from __future__ import annotations

import math
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.api.routes.auth import require_current_user
from app.api.routes.chatbot import ChatMessage, ask_chatbot
from app.core.config import settings
from app.models.user import User
from app.services.rag_service import embed_texts, rag_service
from app.services.supabase_service import supabase_service

router = APIRouter()


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
    retrieval_scores: dict[str, list[float]] = {"hit": [], "recall": [], "mrr": [], "ndcg": []}
    answer_accuracies: list[float] = []
    citation_accuracies: list[float] = []
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
            expected_vector, answer_vector = await embed_texts([case.expected_answer, answer])
            answer_score = sum(left * right for left, right in zip(expected_vector, answer_vector))
            answer_correct = answer_score >= answer_threshold
            answer_accuracies.append(float(answer_correct))
        else:
            rejection_scores.append(float(rejected))

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
            "citation_accuracy": citation_accuracy,
            "rejected": rejected,
            "sources": sources,
        })

    return {
        "dataset_name": dataset.dataset_name,
        "summary": {
            "total": len(dataset.cases),
            "retrieval_evaluated": len(retrieval_scores["hit"]),
            "top_k": top_k,
            "answer_threshold": answer_threshold,
            "hit_at_k": _mean(retrieval_scores["hit"]),
            "recall_at_k": _mean(retrieval_scores["recall"]),
            "mrr": _mean(retrieval_scores["mrr"]),
            "ndcg_at_k": _mean(retrieval_scores["ndcg"]),
            "answer_accuracy": _mean(answer_accuracies),
            "citation_accuracy": _mean(citation_accuracies),
            "unanswerable_rejection_rate": _mean(rejection_scores),
        },
        "cases": case_results,
    }
