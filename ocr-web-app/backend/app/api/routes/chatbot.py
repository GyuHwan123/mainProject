import logging
import os
import re
from datetime import datetime, timezone
from itertools import count
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes.auth import require_current_user
from app.core.config import settings
from app.models.user import User
from app.services.supabase_service import supabase_service
from app.services.pii_service import PRIVACY_RESPONSE, is_sensitive_query
from app.services.email_service import email_service
from app.services.knowledge_report_service import build_knowledge_report

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


class KnowledgeScrapEmailRequest(BaseModel):
    recipient: str | None = Field(default=None, max_length=320)
    recipient_user_id: str | None = None
    scrap_ids: list[str] = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(default="", max_length=2000)


class KnowledgeScrapPdfRequest(BaseModel):
    scrap_ids: list[str] = Field(min_length=1, max_length=100)


def _table_structure_answer(message: str, context: str) -> str | None:
    """Answer explicit table-schema questions from deterministic RAG metadata."""
    question = re.sub(r"\s+", " ", str(message or "")).strip()
    asks_columns = bool(re.search(
        r"(컬럼\s*명|열\s*(?:이름|명|구성)|헤더|(?:컬럼|열).*(?:알려|무엇|뭐|전부|모두|어떻게|구성))",
        question,
    ))
    asks_size = bool(re.search(r"(몇\s*행|몇\s*열|몇\s*컬럼|행.*열|열.*행|표\s*크기)", question))
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
        if not asks_columns and not asks_size and columns_match:
            headers = [value.strip() for value in columns_match.group(1).split("|")]
            header_names = [re.sub(r"^\d+열:\s*", "", value) for value in headers]
            requested_column = next((index for index, name in enumerate(header_names) if name and name in question), None)
            for row in re.findall(r"\[표 행\]\s*([^\n]+)", content):
                cells = [re.sub(r"^\d+열(?:\([^)]*\))?:\s*", "", value.strip()) for value in row.split("|")]
                if requested_column is not None and requested_column < len(cells) and any(
                    value and value in question for index, value in enumerate(cells) if index != requested_column
                ):
                    return f"{header_names[requested_column]}은(는) {cells[requested_column]}입니다. [근거 {evidence_number}]"
            continue
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


def _document_title_answer(message: str, context: str) -> str | None:
    """Return indexed title metadata without asking the LLM to reinterpret it."""
    question = re.sub(r"\s+", " ", str(message or "")).strip()
    if not re.search(r"(?:논문|문서|자료|보고서)?\s*(?:제목|논문명|문서명|자료명|보고서명)", question):
        return None
    match = re.search(
        r"\[근거\s+(\d+)[^\]]*\]\s*.*?\[문서 제목\]\s*([^\n]+)",
        str(context or ""),
        flags=re.S,
    )
    if not match:
        return None
    title = match.group(2).strip(" |")
    if not title:
        return None
    return f"문서 제목은 {title}입니다. [근거 {match.group(1)}]"


def _labeled_fact_answer(message: str, context: str) -> str | None:
    """Answer common labelled RAG facts consistently across paraphrases."""
    question = re.sub(r"\s+", " ", str(message or "")).strip()
    blocks = re.findall(
        r"\[근거\s+(\d+)[^\]]*\]\s*(.*?)(?=\n\n\[근거\s+\d+|\Z)",
        str(context or ""),
        flags=re.S,
    )
    asks_meeting_time = "회의" in question and bool(re.search(r"몇\s*시|언제|시간", question))
    asks_department = "부서" in question and bool(re.search(r"담당|어디", question))
    for evidence_number, content in blocks:
        if asks_meeting_time:
            time_match = re.search(
                r"(?:회의\s*)?(?:시간|일시)\s*(?:은|는|이|가|[:：])?\s*"
                r"((?:오전|오후)?\s*\d{1,2}(?::\d{2}|\s*시(?:\s*\d{1,2}\s*분)?))",
                content,
            )
            if time_match:
                return f"회의 시간은 {time_match.group(1).strip()}입니다. [근거 {evidence_number}]"
        if asks_department:
            department_match = re.search(
                r"담당\s*부서\s*(?:은|는|이|가|[:：])?\s*([^\n/|,;]+)",
                content,
            )
            if department_match:
                department = department_match.group(1).strip(" .")
                if department:
                    return f"담당 부서는 {department}입니다. [근거 {evidence_number}]"
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
    title_answer = _document_title_answer(payload.message, context)
    if title_answer:
        return ChatReply(reply=title_answer, model="document-metadata")
    fact_answer = _labeled_fact_answer(payload.message, context)
    if fact_answer:
        return ChatReply(reply=fact_answer, model="document-metadata")
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


@router.post("/scraps/email")
def email_scraps(payload: KnowledgeScrapEmailRequest, user: User = Depends(require_current_user)) -> dict[str, int | str]:
    recipient = (payload.recipient or "").strip().lower()
    if payload.recipient_user_id:
        recipient_user = supabase_service.get_user_by_id(payload.recipient_user_id)
        if not recipient_user or not recipient_user.get("is_active", True) or recipient_user.get("subscription_tier") != "ENTERPRISE":
            raise HTTPException(status_code=404, detail="전송할 기업 사용자를 찾을 수 없습니다.")
        recipient = str(recipient_user["email"]).lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", recipient):
        raise HTTPException(status_code=422, detail="올바른 이메일 주소를 입력해 주세요.")
    selected_ids = set(payload.scrap_ids)
    scraps = [item for item in supabase_service.list_knowledge_scraps(user.email) if str(item.get("id")) in selected_ids]
    if not scraps:
        raise HTTPException(status_code=400, detail="전송할 지식 바구니 내용이 없습니다.")
    try:
        pdf_content = build_knowledge_report(scraps, author_name=user.name, author_email=user.email)
        email_service.send_knowledge_scraps(recipient=recipient, sender_email=user.email, subject=payload.subject.strip(), note=payload.message.strip(), scraps=scraps, pdf_content=pdf_content)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="이메일 발송 설정을 확인해 주세요.") from exc
    except Exception as exc:
        logger.exception("Knowledge scrapbook email failed")
        raise HTTPException(status_code=502, detail="이메일을 전송하지 못했습니다.") from exc
    return {"message": "이메일을 전송했습니다.", "sent_count": len(scraps)}


@router.post("/scraps/pdf")
def download_scraps_pdf(payload: KnowledgeScrapPdfRequest, user: User = Depends(require_current_user)) -> Response:
    selected_ids = set(payload.scrap_ids)
    scraps = [item for item in supabase_service.list_knowledge_scraps(user.email) if str(item.get("id")) in selected_ids]
    if not scraps:
        raise HTTPException(status_code=400, detail="PDF로 변환할 지식 바구니 항목이 없습니다.")
    content = build_knowledge_report(scraps, author_name=user.name, author_email=user.email)
    return Response(content=content, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=DocAI_knowledge_report.pdf"})


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
