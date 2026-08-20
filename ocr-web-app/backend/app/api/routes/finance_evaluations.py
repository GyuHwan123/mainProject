from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.auth import require_current_user
from app.api.routes.chatbot import generate
from app.models.user import User
from app.services.finance_evaluation_service import evaluate_models, normalize_ground_truth
from app.services.supabase_service import supabase_service


router = APIRouter()


class FinanceEvaluationRequest(BaseModel):
    document_id: str
    ground_truth: dict[str, Any]
    model_names: list[str] = Field(min_length=2, max_length=4)


class FinanceEvaluationQuestionRequest(BaseModel):
    document_id: str
    question: str = Field(min_length=1, max_length=2000)
    model_names: list[str] = Field(min_length=1, max_length=4)


def require_developer(user: User = Depends(require_current_user)) -> User:
    if user.role not in {"DEVELOPER", "ADMIN"} and user.email != "developer@docunex.com":
        raise HTTPException(status_code=403, detail="개발자 권한이 필요합니다.")
    return user


@router.post("/ask")
async def ask_evaluated_models(
    payload: FinanceEvaluationQuestionRequest,
    user: User = Depends(require_developer),
) -> dict[str, Any]:
    document = supabase_service.get_ocr_document(user.email, payload.document_id)
    text = (document.get("extracted_text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="질문할 OCR 텍스트가 없습니다.")
    model_names = list(dict.fromkeys(name.strip() for name in payload.model_names if name.strip()))
    prompt = f"""당신은 영수증 분석 도우미입니다. 아래 OCR 원문만 근거로 사용자의 질문에 한국어로 답하세요.
- OCR 원문에 없는 내용은 추측하지 마세요.
- 금액, 날짜, 상호, 품목은 OCR 표기를 가능한 그대로 사용하세요.
- 답을 찾을 수 없으면 OCR 원문에서 확인할 수 없다고 명확히 답하세요.

[OCR 원문]
{text[:12000]}

[공통 질문]
{payload.question}
"""

    async def ask_model(model_name: str) -> dict[str, Any]:
        started = perf_counter()
        try:
            answer = await generate(prompt, model_name=model_name, num_predict=700)
            return {"model_name": model_name, "success": True, "answer": answer, "latency_ms": round((perf_counter() - started) * 1000)}
        except Exception as exc:
            return {"model_name": model_name, "success": False, "answer": "", "error": str(exc), "latency_ms": round((perf_counter() - started) * 1000)}

    answers = []
    for model_name in model_names:
        answers.append(await ask_model(model_name))
    return {
        "document_id": payload.document_id,
        "question": payload.question,
        "answers": answers,
    }


@router.post("/run")
async def run_finance_evaluation(
    payload: FinanceEvaluationRequest,
    user: User = Depends(require_developer),
) -> dict[str, Any]:
    document = supabase_service.get_ocr_document(user.email, payload.document_id)
    text = (document.get("extracted_text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="평가할 OCR 텍스트가 없습니다.")
    model_names = list(dict.fromkeys(name.strip() for name in payload.model_names if name.strip()))
    if len(model_names) < 2:
        raise HTTPException(status_code=422, detail="서로 다른 모델을 두 개 이상 입력해 주세요.")
    return {
        "document_id": payload.document_id,
        "document_name": document.get("file_name") or "receipt",
        "ocr_text": text,
        "ocr_pages": document.get("bounding_boxes") or [],
        "ground_truth": payload.ground_truth,
        "normalized_ground_truth": normalize_ground_truth(payload.ground_truth),
        "results": await evaluate_models(
            text=text,
            filename=document.get("file_name") or "receipt",
            truth=payload.ground_truth,
            model_names=model_names,
        ),
    }
