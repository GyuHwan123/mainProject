import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter()


class ChatMessage(BaseModel):
    message: str
    context: str | None = None


class ChatReply(BaseModel):
    reply: str
    model: str = "gemma2:2b"


@router.post("/ask", response_model=ChatReply)
async def ask_chatbot(payload: ChatMessage) -> ChatReply:
    context = (payload.context or "문서 근거가 제공되지 않았습니다.")[:6000]
    prompt = f"""당신은 문서 질의응답 도우미입니다. 아래 문서 근거만 사용해 한국어로 답하세요.
근거에 답이 없으면 추측하지 말고 '제공된 문서에서 확인할 수 없습니다'라고 말하세요.
답변 끝에는 사용한 [파일명 / Chunk 번호]를 출처로 표시하세요.

[문서 근거]
{context}

[질문]
{payload.message}

[답변]"""
    try:
        async with httpx.AsyncClient(base_url=settings.OLLAMA_BASE_URL, timeout=90) as client:
            response = await client.post(
                "/api/generate",
                json={"model": "gemma2:2b", "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            answer = response.json().get("response", "").strip()
            if not answer:
                raise ValueError("empty model response")
            return ChatReply(reply=answer)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="AI 모델 서버에 연결할 수 없습니다.") from exc


@router.get("/status")
def chatbot_status() -> dict[str, str]:
    return {"message": "Ollama Gemma2:2b 연결 준비 완료"}
