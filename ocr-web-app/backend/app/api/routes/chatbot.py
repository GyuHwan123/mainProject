import logging
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
logger = logging.getLogger(__name__)


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


async def generate(
    prompt: str,
    *,
    json_format: bool = False,
    num_predict: int = 600,
    model_name: str | None = None,
) -> str:
    effective_model = model_name or MODEL_NAME
    payload: dict[str, Any] = {
        "model": effective_model,
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
    if json_format:
        payload["format"] = "json"

    base_url = settings.OLLAMA_BASE_URL.rstrip("/")
    logger.warning("Ollama model call: model=%s json_format=%s", effective_model, json_format)
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=120) as client:
            response = await client.post("/api/generate", json=payload)
            response.raise_for_status()
            answer = response.json().get("response", "").strip()
            logger.warning("Ollama raw response: model=%s response=%s", effective_model, answer)
            if not answer:
                raise ValueError("empty model response")
            return answer
    except (httpx.HTTPError, ValueError) as exc:
        last_error = exc
    raise HTTPException(
        status_code=503,
        detail=f"Ollama 모델 {payload['model']}에 연결할 수 없습니다. {base_url}의 실행 상태와 모델 설치 여부를 확인해 주세요.",
    ) from last_error


@router.post("/ask", response_model=ChatReply)
async def ask_chatbot(payload: ChatMessage, _user: User = Depends(require_current_user)) -> ChatReply:
    if is_sensitive_query(payload.message):
        return ChatReply(reply=PRIVACY_RESPONSE, model="privacy-policy")
    context = (payload.context or "문서 근거가 제공되지 않았습니다.")[:6000]
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
- [민감정보 보호]로 표시된 값은 절대 유추하거나 복원하지 말고, 개인정보 보호로 제공할 수 없다고 답하세요.
- 답변에 사용한 근거 번호를 문장 끝에 [근거 1] 형식으로 표시하세요.

[최근 대화]
{history or '이전 대화 없음'}

[문서 근거]
{context}

[질문]
{payload.message}

[답변]"""
    return ChatReply(reply=await generate(prompt))


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
        "query_rewriting": False,
        "prompt_version": settings.RAG_PROMPT_VERSION,
        "top_k": settings.RAG_TOP_K,
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
