from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.auth import require_current_user
from app.models.user import User
from app.services.finance_evaluation_service import evaluate_models
from app.services.supabase_service import supabase_service


router = APIRouter()


class FinanceEvaluationRequest(BaseModel):
    document_id: str
    ground_truth: dict[str, Any]
    model_names: list[str] = Field(min_length=2, max_length=4)


def require_developer(user: User = Depends(require_current_user)) -> User:
    if user.role not in {"DEVELOPER", "ADMIN"} and user.email != "developer@docunex.com":
        raise HTTPException(status_code=403, detail="개발자 권한이 필요합니다.")
    return user


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
        "ground_truth": payload.ground_truth,
        "results": await evaluate_models(
            text=text,
            filename=document.get("file_name") or "receipt",
            truth=payload.ground_truth,
            model_names=model_names,
        ),
    }
