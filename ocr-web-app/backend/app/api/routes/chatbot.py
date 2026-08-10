from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class ChatMessage(BaseModel):
    message: str


class ChatReply(BaseModel):
    reply: str
    model: str = "gemma2:2b"


@router.post("/ask", response_model=ChatReply)
def ask_chatbot(payload: ChatMessage) -> ChatReply:
    return ChatReply(
        reply=(
            "이 문서는 OCR 결과를 기반으로 요약하면, "
            "핵심은 OCR, LLM, Vector Embedding 기반 문서 처리 흐름입니다."
        )
    )


@router.get("/status")
def chatbot_status() -> dict[str, str]:
    return {"message": "Ollama Gemma2:2b 연결 준비 완료"}
