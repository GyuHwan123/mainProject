from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel

router = APIRouter()


class OCRResponse(BaseModel):
    filename: str
    text: str
    confidence: float
    language: str = "ko"


@router.post("/upload", response_model=OCRResponse)
async def upload_image(file: UploadFile = File(...)) -> OCRResponse:
    content = (
        "프로젝트 4인 발표\n"
        "- OCR/LLM/Vector Embedding\n"
        "- 정확도 96.4%\n"
        "- 재현율 93.2%"
    )
    return OCRResponse(
        filename=file.filename or "unknown.pdf",
        text=content,
        confidence=0.964,
    )


@router.get("/history")
def ocr_history() -> list[dict[str, str | float]]:
    return [
        {"filename": "프로젝트_발표자료.pdf", "confidence": 0.964, "status": "completed"},
        {"filename": "영수증_0527.pdf", "confidence": 0.941, "status": "completed"},
    ]
