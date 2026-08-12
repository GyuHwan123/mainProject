import json
import re
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.auth import require_current_user
from app.core.config import settings
from app.models.user import User

router = APIRouter()
MODEL_NAME = "gemma2:2b"
MAX_CONTEXT_LENGTH = 12_000
LOCAL_OLLAMA_URL = "http://127.0.0.1:11434"


class ChatMessage(BaseModel):
    message: str
    context: str | None = None


class ChatReply(BaseModel):
    reply: str
    model: str = MODEL_NAME


class TransformRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CONTEXT_LENGTH)
    mode: Literal["structured", "table"]


class TransformReply(BaseModel):
    mode: Literal["structured", "table"]
    result: dict[str, Any]
    model: str = MODEL_NAME


async def generate(prompt: str, *, json_format: bool = False) -> str:
    payload: dict[str, Any] = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    if json_format:
        payload["format"] = "json"

    urls = list(dict.fromkeys([settings.OLLAMA_BASE_URL.rstrip("/"), LOCAL_OLLAMA_URL]))
    last_error: Exception | None = None
    for base_url in urls:
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=120) as client:
                response = await client.post("/api/generate", json=payload)
                response.raise_for_status()
                answer = response.json().get("response", "").strip()
                if not answer:
                    raise ValueError("empty model response")
                return answer
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
    raise HTTPException(
        status_code=503,
        detail="Gemma2 모델에 연결할 수 없습니다. Ollama 실행 상태와 gemma2:2b 설치 여부를 확인해 주세요.",
    ) from last_error


def parse_json_response(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise HTTPException(status_code=502, detail="AI가 구조화된 결과를 반환하지 못했습니다.")
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="AI 응답을 해석할 수 없습니다. 다시 시도해 주세요.") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=502, detail="AI 응답 형식이 올바르지 않습니다.")
    return value


@router.post("/ask", response_model=ChatReply)
async def ask_chatbot(payload: ChatMessage) -> ChatReply:
    context = (payload.context or "문서 근거가 제공되지 않았습니다.")[:6000]
    prompt = f"""당신은 문서 질의응답 도우미입니다. 아래 문서 근거만 사용해 한국어로 답하세요.
근거가 없으면 추측하지 말고 '제공된 문서에서 확인할 수 없습니다'라고 답하세요.

[문서 근거]
{context}

[질문]
{payload.message}

[답변]"""
    return ChatReply(reply=await generate(prompt))


@router.post("/transform", response_model=TransformReply)
async def transform_document(
    payload: TransformRequest,
    _user: User = Depends(require_current_user),
) -> TransformReply:
    source = payload.text[:MAX_CONTEXT_LENGTH]
    if payload.mode == "structured":
        instruction = """문서의 원래 언어를 유지하며 내용을 구조화하세요.
반드시 다음 JSON 형식만 반환하세요:
{"title":"문서 제목", "summary":"핵심 요약", "sections":[{"heading":"항목 제목", "content":"항목 내용"}]}
원문에 없는 사실은 만들지 마세요. sections는 중요한 순서대로 구성하세요."""
    else:
        instruction = """문서에서 표로 표현할 수 있는 사실과 관계를 찾아 표로 정리하세요.
반드시 다음 JSON 형식만 반환하세요:
{"title":"표 제목", "columns":["열1", "열2"], "rows":[["값1", "값2"]], "note":"필요한 설명"}
각 행의 값 개수는 columns 개수와 같아야 합니다. 표로 만들 근거가 부족하면 columns와 rows를 빈 배열로 반환하세요.
원문에 없는 사실은 만들지 마세요."""

    raw = await generate(f"{instruction}\n\n[원문]\n{source}", json_format=True)
    return TransformReply(mode=payload.mode, result=parse_json_response(raw))


@router.get("/status")
async def chatbot_status() -> dict[str, Any]:
    urls = list(dict.fromkeys([settings.OLLAMA_BASE_URL.rstrip("/"), LOCAL_OLLAMA_URL]))
    for base_url in urls:
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=5) as client:
                response = await client.get("/api/tags")
                response.raise_for_status()
                models = [model.get("name", "") for model in response.json().get("models", [])]
            if any(name.startswith(MODEL_NAME) for name in models):
                return {"ready": True, "model": MODEL_NAME}
        except httpx.HTTPError:
            continue
    return {"ready": False, "model": MODEL_NAME}
