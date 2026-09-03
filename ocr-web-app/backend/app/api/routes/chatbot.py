import logging
import os
import re
from datetime import datetime, timezone
from itertools import count
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes.auth import require_current_user
from app.core.config import settings
from app.models.user import User
from app.services.supabase_service import supabase_service
from app.services.pii_service import PRIVACY_RESPONSE, is_sensitive_query

router = APIRouter()
MODEL_NAME = settings.RAG_LLM_MODEL
GROUNDED_REJECTION_RESPONSE = "제공된 문서에서는 질문에 대한 충분한 근거를 확인할 수 없습니다."
logger = logging.getLogger(__name__)
_ollama_call_sequence = count(1)


class GeneratedText(str):
    """Text-compatible Ollama response carrying optional runtime metrics."""

    ollama_metrics: dict[str, Any]

    def __new__(cls, value: str, metrics: dict[str, Any] | None = None) -> "GeneratedText":
        instance = super().__new__(cls, value)
        instance.ollama_metrics = metrics or {}
        return instance


def _ollama_metrics(body: dict[str, Any]) -> dict[str, Any]:
    """Keep only stable Ollama timing/token fields; durations are nanoseconds."""
    integer_fields = (
        "total_duration",
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
    )
    metrics = {field: int(body.get(field) or 0) for field in integer_fields}
    metrics["done_reason"] = str(body.get("done_reason") or "")
    return metrics


class ChatMessage(BaseModel):
    message: str
    context: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list, max_length=12)


class ChatReply(BaseModel):
    reply: str
    model: str = MODEL_NAME


class ChatSessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    document_id: str | None = None


class ChatMessageCreate(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=50_000)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    model_name: str | None = None


class StoredChatSession(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    title: str


class StoredChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    session_id: str
    role: str
    content: str
    sources: list[dict[str, Any]] = Field(default_factory=list)


class KnowledgeScrapCreate(BaseModel):
    question: str = Field(min_length=1, max_length=10_000)
    answer: str = Field(min_length=1, max_length=50_000)
    document_id: str | None = None
    document_name: str | None = None
    source_count: int = Field(default=0, ge=0)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    model_name: str | None = None


class KnowledgeScrap(KnowledgeScrapCreate):
    model_config = ConfigDict(extra="allow")
    id: str


def _table_structure_answer(message: str, context: str) -> str | None:
    """Answer explicit table-schema questions from deterministic RAG metadata."""
    question = re.sub(r"\s+", " ", str(message or "")).strip()
    asks_columns = bool(re.search(
        r"(컬럼\s*명|열\s*(?:이름|명)|헤더|(?:컬럼|열).*(?:알려|무엇|뭐|전부|모두))",
        question,
    ))
    asks_size = bool(re.search(r"(몇\s*행|몇\s*열|몇\s*컬럼|행.*열|열.*행|표\s*크기)", question))
    if not asks_columns and not asks_size:
        return None

    blocks = re.findall(
        r"\[근거\s+(\d+)[^\]]*\]\s*(.*?)(?=\n\n\[근거\s+\d+|\Z)",
        str(context or ""),
        flags=re.S,
    )
    for evidence_number, content in blocks:
        size_match = re.search(
            r"\[표 크기\]\s*(?:헤더 포함\s*)?(\d+)행\s*[×xX*]\s*(\d+)열",
            content,
        )
        columns_match = re.search(r"\[표 테이블 열 컬럼명\]\s*([^\n]+)", content)
        if asks_columns and not columns_match:
            continue
        if asks_size and not size_match:
            continue

        sentences = []
        if size_match:
            sentences.append(
                f"이 표는 헤더를 포함해 {int(size_match.group(1))}행 × {int(size_match.group(2))}열입니다."
            )
        if columns_match:
            columns = [value.strip() for value in columns_match.group(1).split("|") if value.strip()]
            if columns:
                sentences.append("컬럼은 " + ", ".join(columns) + "입니다.")
        if sentences:
            return " ".join(sentences) + f" [근거 {evidence_number}]"
    return None


async def generate(
    prompt: str,
    *,
    json_format: bool = False,
    num_predict: int = 600,
    model_name: str | None = None,
    question: str | None = None,
    request_timeout_seconds: float = 120,
    keep_alive: str | int = "30m",
    num_ctx: int = 8192,
) -> str:
    effective_model = model_name or MODEL_NAME
    payload: dict[str, Any] = {
        "model": effective_model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": keep_alive,
        "options": {
            "temperature": 0.05,
            "num_predict": num_predict,
            "num_ctx": num_ctx,
            "repeat_penalty": 1.08,
        },
    }
    if json_format:
        payload["format"] = "json"

    base_url = settings.OLLAMA_BASE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=request_timeout_seconds) as client:
            # Diagnostic block: remove after runtime duplicate-call verification.
            sequence = next(_ollama_call_sequence)
            question_preview = " ".join((question or "").split())[:40]
            if sequence == 1:
                root_logger = logging.getLogger()
                logger.warning(
                    "[OLLAMA_LOGGING] pid=%s module_handlers=%s root_handlers=%s propagate=%s",
                    os.getpid(), len(logger.handlers), len(root_logger.handlers), logger.propagate,
                )
            logger.warning(
                '[OLLAMA_CALL] time=%s pid=%s seq=%s question="%s" model=%s',
                datetime.now(timezone.utc).isoformat(), os.getpid(), sequence,
                question_preview.replace('"', "'"), effective_model,
            )
            response = await client.post("/api/generate", json=payload)
            response.raise_for_status()
            body = response.json()
            answer = str(body.get("response") or "").strip()
            logger.warning("Ollama raw response: model=%s response=%s", effective_model, answer)
            if not answer:
                raise ValueError("empty model response")
            return GeneratedText(answer, _ollama_metrics(body))
    except (httpx.HTTPError, ValueError) as exc:
        last_error = exc
    raise HTTPException(
        status_code=503,
        detail=f"Ollama 모델 {payload['model']}에 연결할 수 없습니다. {base_url}의 실행 상태와 모델 설치 여부를 확인해 주세요.",
    ) from last_error


async def generate_with_metadata(
    prompt: str,
    *,
    model_name: str,
    num_predict: int = 600,
    question: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "temperature": 0.05,
            "num_predict": num_predict,
            "num_ctx": 8192,
            "repeat_penalty": 1.08,
        },
    }
    base_url = settings.OLLAMA_BASE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=120) as client:
            sequence = next(_ollama_call_sequence)
            question_preview = " ".join((question or "").split())[:40]
            logger.warning(
                '[OLLAMA_EVALUATION_CALL] time=%s pid=%s seq=%s question="%s" model=%s',
                datetime.now(timezone.utc).isoformat(), os.getpid(), sequence,
                question_preview.replace('"', "'"), model_name,
            )
            response = await client.post("/api/generate", json=payload)
            response.raise_for_status()
            body = response.json()
            answer = str(body.get("response") or "").strip()
            if not answer:
                raise ValueError("empty model response")
            return {"response": answer, **_ollama_metrics(body)}
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama 모델 {model_name}에 연결할 수 없습니다.",
        ) from exc


async def _ask_chatbot(
    payload: ChatMessage,
    _user: User,
    *,
    evaluation_model: str | None = None,
    evaluation_metadata: dict[str, Any] | None = None,
) -> ChatReply:
    if is_sensitive_query(payload.message):
        return ChatReply(reply=PRIVACY_RESPONSE, model="privacy-policy")
    if not payload.context or not payload.context.strip():
        return ChatReply(reply=GROUNDED_REJECTION_RESPONSE, model="grounded-rejection")
    context = payload.context[:6000]
    table_answer = _table_structure_answer(payload.message, context)
    if table_answer:
        return ChatReply(reply=table_answer, model="table-metadata")
    history = "\n".join(
        f"{'사용자' if item.get('role') == 'user' else 'AI'}: {str(item.get('content', ''))[:800]}"
        for item in payload.history[-8:]
        if item.get("role") in {"user", "assistant"} and item.get("content")
    )[:4000]
    prompt = f"""당신은 정확한 문서 질의응답 도우미입니다. 아래 문서 근거만 사용해 한국어로 답하세요.
- 질문에 대한 직접적인 답을 첫 문장에 쓰세요.
- 저자, 사람, 기관, 날짜, 수치가 근거에 있으면 생략하지 말고 원문 그대로 쓰세요.
- 대화 기록은 대명사와 후속 질문을 이해하는 용도로만 사용하세요.
- 문서 근거에 없는 내용은 추측하지 마세요.
- 근거에 없는 사실을 반대 사실로 추론하지 마세요. 지원 근거가 없다고 해서 지원하지 않는다고 답할 수는 없습니다.
- 질문에 대한 직접적인 근거가 없으면 "제공된 문서에서는 질문에 대한 충분한 근거를 확인할 수 없습니다."라고만 답하고 근거를 인용하지 마세요.
- [민감정보 보호]로 표시된 값은 절대 유추하거나 복원하지 말고, 개인정보 보호로 제공할 수 없다고 답하세요.
- 답변에 사용한 근거 번호를 문장 끝에 [근거 1] 형식으로 표시하세요.

[최근 대화]
{history or '이전 대화 없음'}

[문서 근거]
{context}

[질문]
{payload.message}

[답변]"""
    if evaluation_model:
        generated = await generate_with_metadata(
            prompt, model_name=evaluation_model, question=payload.message,
        )
        if evaluation_metadata is not None:
            evaluation_metadata.update(generated)
        return ChatReply(reply=generated["response"], model=evaluation_model)
    return ChatReply(reply=await generate(prompt, question=payload.message))


@router.post("/ask", response_model=ChatReply)
async def ask_chatbot(payload: ChatMessage, user: User = Depends(require_current_user)) -> ChatReply:
    return await _ask_chatbot(payload, user)


@router.get("/sessions", response_model=list[StoredChatSession])
def list_sessions(user: User = Depends(require_current_user)) -> list[dict[str, Any]]:
    return supabase_service.list_chat_sessions(user.email)


@router.post("/sessions", response_model=StoredChatSession)
def create_session(payload: ChatSessionCreate, user: User = Depends(require_current_user)) -> dict[str, Any]:
    return supabase_service.create_chat_session(user.email, payload.title.strip(), payload.document_id)


@router.get("/sessions/{session_id}/messages", response_model=list[StoredChatMessage])
def list_messages(session_id: str, user: User = Depends(require_current_user)) -> list[dict[str, Any]]:
    return supabase_service.list_chat_messages(user.email, session_id)


@router.post("/sessions/{session_id}/messages", response_model=StoredChatMessage)
def create_message(session_id: str, payload: ChatMessageCreate, user: User = Depends(require_current_user)) -> dict[str, Any]:
    return supabase_service.save_chat_message(
        user_email=user.email, session_id=session_id, role=payload.role,
        content=payload.content, sources=payload.sources, model_name=payload.model_name,
    )


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, user: User = Depends(require_current_user)) -> None:
    supabase_service.delete_chat_session(user.email, session_id)


@router.get("/scraps", response_model=list[KnowledgeScrap])
def list_scraps(user: User = Depends(require_current_user)) -> list[dict[str, Any]]:
    return supabase_service.list_knowledge_scraps(user.email)


@router.post("/scraps", response_model=KnowledgeScrap)
def create_scrap(payload: KnowledgeScrapCreate, user: User = Depends(require_current_user)) -> dict[str, Any]:
    return supabase_service.create_knowledge_scrap(user.email, payload.model_dump())


@router.delete("/scraps/{scrap_id}", status_code=204)
def delete_scrap(scrap_id: str, user: User = Depends(require_current_user)) -> None:
    supabase_service.delete_knowledge_scrap(user.email, scrap_id)


@router.get("/status")
async def chatbot_status() -> dict[str, Any]:
    configuration = {
        "model": MODEL_NAME,
        "embedding_model": settings.RAG_EMBEDDING_MODEL,
        "embedding_dimensions": settings.RAG_EMBEDDING_DIMENSIONS,
        "rerank_model": settings.RAG_RERANK_MODEL or None,
        "retrieval_method": "dense_bm25_hybrid",
        "dense_candidate_count": settings.RAG_DENSE_CANDIDATE_COUNT,
        "bm25_candidate_count": settings.RAG_BM25_CANDIDATE_COUNT,
        "query_rewriting": settings.RAG_QUERY_REWRITING,
        "query_rewrite_model": settings.RAG_QUERY_REWRITE_MODEL or settings.RAG_LLM_MODEL,
        "prompt_version": settings.RAG_PROMPT_VERSION,
        "top_k": settings.RAG_TOP_K,
        "answerability_threshold": settings.RAG_ANSWERABILITY_THRESHOLD,
        "chunk_target_chars": settings.RAG_CHUNK_TARGET_CHARS,
    }
    base_url = settings.OLLAMA_BASE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=5) as client:
            response = await client.get("/api/tags")
            response.raise_for_status()
            models = [model.get("name", "") for model in response.json().get("models", [])]
        if any(name.startswith(MODEL_NAME) for name in models):
            return {"ready": True, **configuration}
    except httpx.HTTPError:
        pass
    return {"ready": False, **configuration}
