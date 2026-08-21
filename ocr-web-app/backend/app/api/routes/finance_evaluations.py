import json
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.auth import require_current_user
from app.api.routes.chatbot import generate
from app.api.routes.finance import _receipt_hints, _receipt_item_candidates
from app.core.config import settings
from app.models.user import User
from app.services.finance_evaluation_service import (
    CORE_FIELDS,
    estimate_ocr_impact,
    evaluate_models,
    normalize_ground_truth,
    score_fields,
    verify_workbook,
)
from app.services.supabase_service import supabase_service
from app.services import qwen_vl_service


router = APIRouter()


class FinanceEvaluationRequest(BaseModel):
    document_id: str
    ground_truth: dict[str, Any]
    model_names: list[str] = Field(min_length=1, max_length=4)


class FinanceEvaluationQuestionRequest(BaseModel):
    document_id: str
    question: str = Field(min_length=1, max_length=2000)
    model_names: list[str] = Field(min_length=1, max_length=4)


class FinanceRecordEvaluationRequest(BaseModel):
    document_id: str
    record_id: str
    ground_truth: dict[str, Any]
    latency_ms: int = Field(default=0, ge=0)


def require_developer(user: User = Depends(require_current_user)) -> User:
    if user.role not in {"DEVELOPER", "ADMIN"} and user.email != "developer@docunex.com":
        raise HTTPException(status_code=403, detail="개발자 권한이 필요합니다.")
    return user


async def _installed_ollama_models() -> list[str]:
    # Native execution defaults to local Ollama; Docker Compose supplies the
    # container address. Never mix model lists from different Ollama instances.
    base_url = settings.OLLAMA_BASE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
            response = await client.get("/api/tags")
            response.raise_for_status()
            return sorted({
                str(model.get("name") or model.get("model") or "").strip()
                for model in response.json().get("models", [])
                if isinstance(model, dict) and (model.get("name") or model.get("model"))
            })
    except (httpx.HTTPError, ValueError):
        pass
    raise HTTPException(status_code=503, detail="Ollama에서 설치된 모델 목록을 불러올 수 없습니다.")


@router.get("/models")
async def list_installed_ollama_models(
    _user: User = Depends(require_developer),
) -> dict[str, list[str]]:
    try:
        models = await _installed_ollama_models()
    except HTTPException:
        if not qwen_vl_service.is_configured():
            raise
        models = []
    if qwen_vl_service.is_configured():
        models.append(qwen_vl_service.model_name())
    return {"models": list(dict.fromkeys(models))}


@router.post("/record")
def evaluate_existing_finance_record(
    payload: FinanceRecordEvaluationRequest,
    user: User = Depends(require_developer),
) -> dict[str, Any]:
    """Score the exact record produced by automatic documentation.

    Unlike ``/run``, this endpoint does not invoke the LLM again, so the
    evaluation reflects the values that were actually written to Excel.
    """
    document = supabase_service.get_ocr_document(user.email, payload.document_id)
    record = next(
        (
            item for item in supabase_service.list_finance_records(user.email, limit=1000)
            if str(item.get("id")) == payload.record_id
        ),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="평가할 재무 기록을 찾을 수 없습니다.")

    # Duplicate detection can intentionally return an existing finance record
    # whose original document_id differs from the newly uploaded OCR document.
    # Ownership is already enforced by list_finance_records(user.email), so use
    # the requested record with the current upload's OCR evidence for scoring.
    text = (document.get("extracted_text") or "").strip()
    truth = normalize_ground_truth(payload.ground_truth)
    prediction = {field: record.get(field) for field in CORE_FIELDS}
    prediction["items"] = (record.get("structured_data") or {}).get("items") or []
    score = score_fields(prediction, truth)
    return {
        "document_id": payload.document_id,
        "document_name": document.get("file_name") or "receipt",
        "ocr_text": text,
        "ocr_pages": document.get("bounding_boxes") or [],
        "ground_truth": payload.ground_truth,
        "normalized_ground_truth": truth,
        "results": [{
            "model_name": record.get("model_name") or "unknown",
            "success": True,
            "latency_ms": payload.latency_ms,
            "system": {
                "prediction": prediction,
                "score": score,
                "ocr_impact": estimate_ocr_impact(text, truth, score),
                "workbook": verify_workbook(record),
            },
        }],
    }


def _evaluation_question_prompt(
    text: str,
    question: str,
    pages: list[dict[str, Any]] | None,
    filename: str,
) -> str:
    return f"""당신은 영수증 분석 도우미입니다. 아래 OCR 근거만 사용자의 질문에 한국어로 답하세요.
- OCR 원문에 없는 내용은 추측하지 마세요.
- 금액, 날짜, 상호, 품목은 OCR 표기를 가능한 그대로 사용하세요.
- 품목 행 후보를 하나씩 검토하고 합계·세금·결제 행은 상품에서 제외하세요.
- 금액 관계가 GROSS_MINUS_DISCOUNT_EQUALS_PAID이면 할인액을 부가세로 해석하지 마세요.
- 답을 찾을 수 없으면 OCR 원문에서 확인할 수 없다고 명확히 답하세요.

[코드 힌트]
{json.dumps(_receipt_hints(text, filename), ensure_ascii=False)}

[품목 행 후보]
{json.dumps(_receipt_item_candidates(pages), ensure_ascii=False)}

[OCR 원문]
{text[:6000]}

[공통 질문]
{question}
"""


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
    pages = document.get("bounding_boxes") or []
    prompt = _evaluation_question_prompt(text, payload.question, pages, document.get("file_name") or "receipt")

    async def ask_model(model_name: str) -> dict[str, Any]:
        started = perf_counter()
        try:
            if model_name == qwen_vl_service.model_name() and qwen_vl_service.is_configured():
                content, mime_type = supabase_service.download_document(document["file_url"])
                answer = await qwen_vl_service.generate_with_image(content, mime_type, document.get("file_name") or "receipt", prompt)
                if not isinstance(answer, str):
                    answer = str(answer)
            else:
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
    installed_models = await _installed_ollama_models()
    if qwen_vl_service.is_configured():
        installed_models.append(qwen_vl_service.model_name())
    unavailable = [name for name in model_names if name not in installed_models]
    if unavailable:
        raise HTTPException(
            status_code=422,
            detail=f"Ollama에 설치되지 않은 모델입니다: {', '.join(unavailable)}",
        )
    if not model_names:
        raise HTTPException(status_code=422, detail="평가할 Ollama 모델을 하나 이상 선택해 주세요.")
    qwen_input = None
    if qwen_vl_service.model_name() in model_names:
        qwen_input = supabase_service.download_document(document["file_url"])
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
            qwen_input=qwen_input,
            pages=document.get("bounding_boxes") or [],
        ),
    }
