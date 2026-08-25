import json
from time import perf_counter
from typing import Any, Literal

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
    batch_id: str | None = None
    dataset_name: str | None = None
    dataset_index: int = Field(default=0, ge=0)
    source_file_name: str | None = None


class FinanceEvaluationBatchRequest(BaseModel):
    batch_name: str = Field(min_length=1, max_length=200)
    dataset_name: str | None = None
    model_name: str = Field(min_length=1, max_length=200)
    total_items: int = Field(default=0, ge=0, le=10000)
    evaluation_mode: Literal["SINGLE", "BULK"] = "SINGLE"


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
) -> dict[str, Any]:
    models = await _installed_ollama_models()
    configured_model = settings.RECEIPTS_LLM_MODEL.strip()
    warning = None
    if not configured_model:
        warning = "영수증 LLM 모델이 설정되지 않았습니다. .env에 RECEIPTS_LLM_MODEL을 설정해 주세요."
    elif configured_model not in models:
        warning = f"설정된 영수증 LLM 모델 '{configured_model}'이 Ollama에 설치되어 있지 않습니다."
    return {
        "models": models,
        "default_model": configured_model or None,
        "warning": warning,
    }


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
    response = {
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
    stored = supabase_service.save_finance_record_evaluation(
        user_email=user.email,
        document=document,
        record=record,
        ground_truth=payload.ground_truth,
        normalized_ground_truth=truth,
        result=response["results"][0],
        dataset_index=payload.dataset_index,
        dataset_name=payload.dataset_name,
        source_file_name=payload.source_file_name or document.get("file_name"),
        batch_id=payload.batch_id,
    )
    response["batch_id"] = stored["batch"]["id"]
    response["evaluation_id"] = stored["evaluation"]["id"]
    return response


@router.post("/batches")
def create_finance_evaluation_batch(
    payload: FinanceEvaluationBatchRequest,
    user: User = Depends(require_developer),
) -> dict[str, Any]:
    return supabase_service.create_finance_evaluation_batch(
        user_email=user.email,
        batch_name=payload.batch_name,
        dataset_name=payload.dataset_name,
        model_name=payload.model_name,
        total_items=payload.total_items,
        evaluation_mode=payload.evaluation_mode,
    )


@router.post("/batches/{batch_id}/finalize")
def finalize_finance_evaluation_batch(
    batch_id: str,
    user: User = Depends(require_developer),
) -> dict[str, Any]:
    return supabase_service.finalize_finance_evaluation_batch(user.email, batch_id)


@router.get("/runs")
def list_saved_finance_evaluations(
    user: User = Depends(require_developer),
) -> list[dict[str, Any]]:
    runs = []
    for row in supabase_service.list_finance_record_evaluations(user.email):
        document = supabase_service.get_ocr_document(user.email, row["document_id"])
        item = row.get("finance_evaluation_items") or {}
        batch = row.get("finance_evaluation_batches") or {}
        runs.append({
            "document_id": row["document_id"],
            "document_name": document.get("file_name") or "receipt",
            "ocr_text": document.get("extracted_text") or "",
            "ocr_pages": document.get("bounding_boxes") or [],
            "ground_truth": row.get("ground_truth") or {},
            "normalized_ground_truth": row.get("normalized_ground_truth") or {},
            "evaluated_at": row.get("evaluated_at"),
            "batch_id": row.get("batch_id"),
            "evaluation_id": row.get("id"),
            "dataset_name": batch.get("dataset_name"),
            "dataset_index": item.get("dataset_index", 0),
            "matched_image": item.get("source_file_name") or document.get("file_name"),
            "results": [{
                "model_name": row.get("model_name") or "unknown",
                "success": row.get("status") == "COMPLETED",
                "latency_ms": row.get("latency_ms") or 0,
                "error": row.get("error_message"),
                "system": {
                    "prediction": row.get("prediction") or {},
                    "score": {
                        "fields": row.get("field_scores") or {},
                        "correct_fields": row.get("correct_fields") or 0,
                        "evaluated_fields": row.get("evaluated_fields") or 0,
                        "field_accuracy": row.get("field_accuracy") or 0,
                        "complete_match": bool(row.get("complete_match")),
                    },
                    "ocr_impact": row.get("ocr_impact") or {},
                    "workbook": row.get("workbook_result") or {},
                },
            }],
        })
    return runs


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
    unavailable = [name for name in model_names if name not in installed_models]
    if unavailable:
        raise HTTPException(
            status_code=422,
            detail=f"Ollama에 설치되지 않은 모델입니다: {', '.join(unavailable)}",
        )
    if not model_names:
        raise HTTPException(status_code=422, detail="평가할 Ollama 모델을 하나 이상 선택해 주세요.")
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
            pages=document.get("bounding_boxes") or [],
        ),
    }
