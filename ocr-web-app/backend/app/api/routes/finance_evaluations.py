from collections import Counter
from datetime import date, datetime, time, timedelta
from math import ceil
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.auth import require_current_user
from app.api.routes.finance import _receipt_item_candidates
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
from app.services.finance_error_analysis_service import analyze_finance_evaluation_failure
from app.services.supabase_service import supabase_service


router = APIRouter()
KST = ZoneInfo("Asia/Seoul")


def _prediction_from_finance_record(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebuild the scored schema from persisted root and structured fields."""
    structured = record.get("structured_data") or {}
    prediction = {
        field: record.get(field) if record.get(field) is not None else structured.get(field)
        for field in CORE_FIELDS
    }
    prediction["items"] = structured.get("items") or []
    summary = structured.get("receipt_summary") if isinstance(structured.get("receipt_summary"), dict) else {}
    if prediction.get("total_quantity") is None:
        prediction["total_quantity"] = summary.get("stated_total_quantity")
    return prediction, structured


def _ocr_structure_diagnostics(pages: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Expose the same compact OCR evidence that is passed to the receipt LLM."""
    page_list = pages or []
    candidates = _receipt_item_candidates(page_list)
    table_rows = sum(
        len(table.get("rows") or [])
        for page in page_list
        for table in (page.get("tables") or [])
    )
    return {
        "candidates": candidates,
        "summary": {
            "pages": len(page_list),
            "ocr_boxes": sum(len(page.get("items") or []) for page in page_list),
            "tables": sum(len(page.get("tables") or []) for page in page_list),
            "table_rows": table_rows,
            "item_regions": sum(
                1 for page in page_list for region in (page.get("regions") or [])
                if region.get("type") == "items"
            ),
            "item_candidates": len(candidates),
            "uncertain_candidates": sum(1 for candidate in candidates if candidate.get("uncertainty")),
        },
    }


def _pipeline_trace(structured: dict[str, Any]) -> dict[str, Any]:
    diagnostics = structured.get("item_extraction_diagnostics") or {}
    return {
        "llm": structured.get("llm_trace") or {},
        "semantic_evidence": structured.get("semantic_evidence") or {},
        "validator": structured.get("validator_trace") or {},
        "deterministic_hints": structured.get("deterministic_hints") or {},
        "item_candidates": diagnostics.get("candidates") or [],
        "model_items": diagnostics.get("model_items") or [],
        "resolved_items": diagnostics.get("resolved_items") or [],
    }


def _raw_prediction_from_trace(
    pipeline_trace: dict[str, Any], prediction: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild legacy raw output without nesting ``items_raw.items`` twice."""
    llm_trace = pipeline_trace.get("llm") or {}
    summary_raw = llm_trace.get("summary_raw") or {}
    items_raw = llm_trace.get("items_raw")
    if isinstance(items_raw, dict):
        raw_items = items_raw.get("items") or []
    elif isinstance(items_raw, list):
        raw_items = items_raw
    else:
        raw_items = prediction.get("items") or []
    return {
        **(summary_raw if isinstance(summary_raw, dict) else {}),
        "items": raw_items if isinstance(raw_items, list) else [],
    }



class FinanceEvaluationRequest(BaseModel):
    document_id: str
    ground_truth: dict[str, Any]
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
    model_name: str | None = Field(default=None, max_length=200)
    total_items: int = Field(default=0, ge=0, le=10000)
    evaluation_mode: Literal["SINGLE", "BULK"] = "SINGLE"


def require_developer(user: User = Depends(require_current_user)) -> User:
    if user.role not in {"DEVELOPER", "ADMIN"} and user.email != "developer@docunex.com":
        raise HTTPException(status_code=403, detail="개발자 권한이 필요합니다.")
    return user


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _local_date(row: dict[str, Any], key: str) -> str:
    value = str(row.get(key) or "")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(KST).date().isoformat()
    except ValueError:
        return value[:10]


def _monitoring_metrics(evaluations: list[dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in evaluations if row.get("status") == "COMPLETED"]
    amount_scores = [
        float(bool(detail.get("correct")))
        for row in completed
        if isinstance((detail := (row.get("field_scores") or {}).get("total_amount")), dict)
    ]
    item_count = len(items) or len(evaluations)
    ocr_failures = sum(row.get("error_stage") == "OCR" for row in items)
    return {
        "field_accuracy": _average([float(row.get("field_accuracy") or 0) for row in completed]),
        "amount_accuracy": _average(amount_scores),
        "perfect_receipt_rate": _average([float(bool(row.get("complete_match"))) for row in completed]),
        "processing_success_rate": len(completed) / item_count if item_count else None,
        "average_latency_ms": _average([float(row.get("latency_ms") or 0) for row in completed]),
        "ocr_success_rate": (item_count - ocr_failures) / item_count if item_count else None,
        "total_count": item_count,
    }


def _monitoring_details(evaluations: list[dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in evaluations if row.get("status") == "COMPLETED"]
    errors: Counter[str] = Counter()
    fields: dict[str, list[bool]] = {}

    def add_field(name: str, detail: Any) -> None:
        if isinstance(detail, dict) and detail.get("correct") is not None:
            fields.setdefault(name, []).append(bool(detail["correct"]))

    for row in evaluations:
        for tag in row.get("error_tags") or []:
            if isinstance(tag, dict):
                errors[str(tag.get("category") or "UNKNOWN")] += 1
        for field, detail in (row.get("field_scores") or {}).items():
            if field != "items":
                add_field(field, detail)
                continue
            if isinstance(detail, dict):
                if detail.get("count_correct") is not None:
                    fields.setdefault("items.count", []).append(bool(detail["count_correct"]))
                for scored_item in detail.get("items") or []:
                    for item_field, item_detail in (scored_item.get("fields") or {}).items():
                        add_field(f"items.{item_field}", item_detail)
    for item in items:
        if item.get("status") == "FAILED" and item.get("error_stage"):
            stage = str(item["error_stage"])
            category = "OCR_ERROR" if stage == "OCR" else "LLM_ERROR" if stage == "DOCUMENTATION" else "PIPELINE_ERROR"
            errors[category] += 1

    total_errors = sum(errors.values())
    latencies = sorted(float(row.get("latency_ms") or 0) for row in completed)
    p95_latency = latencies[max(ceil(len(latencies) * .95) - 1, 0)] if latencies else None
    timeout_count = sum("timeout" in str(row.get("error_message") or "").lower() for row in evaluations + items)
    llm_json_failures = sum(
        any(
            isinstance(tag, dict) and tag.get("category") == "LLM_ERROR" and "JSON" in str(tag.get("code") or "").upper()
            for tag in row.get("error_tags") or []
        )
        for row in evaluations
    )
    return {
        "error_distribution": [
            {"category": category, "count": count, "rate": count / total_errors if total_errors else 0}
            for category, count in errors.most_common()
        ],
        "total_errors": total_errors,
        "field_accuracy": [
            {"field": field, "accuracy": sum(values) / len(values), "count": len(values)}
            for field, values in sorted(fields.items(), key=lambda item: (-sum(item[1]) / len(item[1]), item[0]))
        ],
        "system": {
            "average_latency_ms": _average(latencies),
            "p95_latency_ms": p95_latency,
            "timeout_count": timeout_count,
            "ocr_failure_count": sum(item.get("error_stage") == "OCR" for item in items),
            "llm_json_failure_count": llm_json_failures,
            "total_count": len(items) or len(evaluations),
        },
    }


@router.get("/monitoring")
def get_finance_monitoring(
    start_date: date,
    end_date: date,
    model_name: str | None = None,
    user: User = Depends(require_developer),
) -> dict[str, Any]:
    if end_date < start_date:
        raise HTTPException(status_code=422, detail="종료일은 시작일보다 빠를 수 없습니다.")
    if (end_date - start_date).days > 365:
        raise HTTPException(status_code=422, detail="조회 기간은 최대 366일까지 선택할 수 있습니다.")
    start_at = datetime.combine(start_date, time.min, KST).isoformat()
    end_exclusive = datetime.combine(end_date + timedelta(days=1), time.min, KST).isoformat()
    data = supabase_service.list_finance_monitoring_data(
        user.email, start_at=start_at, end_at=end_exclusive, model_name=model_name,
    )
    evaluations = data["evaluations"]
    items = data["items"]
    details = _monitoring_details(evaluations, items)
    period_days = (end_date - start_date).days + 1
    previous_end_date = start_date - timedelta(days=1)
    previous_start_date = previous_end_date - timedelta(days=period_days - 1)
    previous_data = supabase_service.list_finance_monitoring_data(
        user.email,
        start_at=datetime.combine(previous_start_date, time.min, KST).isoformat(),
        end_at=datetime.combine(start_date, time.min, KST).isoformat(),
        model_name=model_name,
    )

    daily = []
    cursor = start_date
    while cursor <= end_date:
        day = cursor.isoformat()
        day_evaluations = [row for row in evaluations if _local_date(row, "evaluated_at") == day]
        day_items = [row for row in items if _local_date(row, "started_at") == day]
        daily.append({"date": day, **_monitoring_metrics(day_evaluations, day_items)})
        cursor += timedelta(days=1)
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "model_name": model_name,
        "summary": _monitoring_metrics(evaluations, items),
        "details": details,
        "comparison": {
            "start_date": previous_start_date.isoformat(),
            "end_date": previous_end_date.isoformat(),
            "summary": _monitoring_metrics(previous_data["evaluations"], previous_data["items"]),
            "details": _monitoring_details(previous_data["evaluations"], previous_data["items"]),
        },
        "recent_runs": data.get("batches", []),
        "daily": daily,
    }


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
    prediction, structured = _prediction_from_finance_record(record)
    score = score_fields(prediction, truth, structured, document.get("bounding_boxes") or [])
    pipeline_trace = _pipeline_trace(structured)
    error_analysis = analyze_finance_evaluation_failure(
        ocr_text=text,
        ground_truth=truth,
        prediction=prediction,
        pipeline_trace=pipeline_trace,
    )
    response = {
        "document_id": payload.document_id,
        "document_name": document.get("file_name") or "receipt",
        "ocr_text": text,
        "ocr_pages": document.get("bounding_boxes") or [],
        "ocr_diagnostics": _ocr_structure_diagnostics(document.get("bounding_boxes") or []),
        "ground_truth": payload.ground_truth,
        "normalized_ground_truth": truth,
        "results": [{
            "model_name": record.get("model_name") or "unknown",
            "success": True,
            "latency_ms": payload.latency_ms,
            "system": {
                "prediction": prediction,
                "pipeline_trace": pipeline_trace,
                "error_analysis": error_analysis,
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
        model_name=payload.model_name or settings.RECEIPTS_LLM_MODEL or "final-service",
        total_items=payload.total_items,
        evaluation_mode=payload.evaluation_mode,
    )


@router.get("/batches")
def list_finance_evaluation_batches(
    user: User = Depends(require_developer),
) -> list[dict[str, Any]]:
    return supabase_service.list_finance_evaluation_batches(user.email)


@router.post("/batches/{batch_id}/finalize")
def finalize_finance_evaluation_batch(
    batch_id: str,
    user: User = Depends(require_developer),
) -> dict[str, Any]:
    return supabase_service.finalize_finance_evaluation_batch(user.email, batch_id)


@router.get("/runs")
def list_saved_finance_evaluations(
    evaluation_mode: Literal["SINGLE", "BULK"] | None = None,
    batch_id: str | None = None,
    limit: int = 30,
    user: User = Depends(require_developer),
) -> list[dict[str, Any]]:
    runs = []
    for row in supabase_service.list_finance_record_evaluations(
        user.email,
        limit=limit,
        evaluation_mode=evaluation_mode,
        batch_id=batch_id,
    ):
        item = row.get("finance_evaluation_items") or {}
        batch = row.get("finance_evaluation_batches") or {}
        document = row.get("ocr_documents") or {}
        if isinstance(item, list):
            item = item[0] if item else {}
        if isinstance(batch, list):
            batch = batch[0] if batch else {}
        if isinstance(document, list):
            document = document[0] if document else {}
        row_mode = str(batch.get("evaluation_mode") or "SINGLE").upper()
        if evaluation_mode and row_mode != evaluation_mode:
            continue
        prediction = row.get("prediction") or {}
        normalized_truth = row.get("normalized_ground_truth") or row.get("ground_truth") or {}
        pipeline_trace = row.get("pipeline_trace") or {}
        stored_rubric = row.get("selection_rubric") or {}
        replay_rubric = stored_rubric
        if not replay_rubric:
            raw_prediction = _raw_prediction_from_trace(pipeline_trace, prediction)
            replay_rubric = score_fields(
                prediction, normalized_truth, raw_prediction,
                document.get("bounding_boxes") or [],
            ).get("selection_rubric") or {}
        runs.append({
            "document_id": row["document_id"],
            "document_name": document.get("file_name") or "receipt",
            "ocr_text": document.get("extracted_text") or "",
            "ocr_pages": document.get("bounding_boxes") or [],
            "ocr_diagnostics": _ocr_structure_diagnostics(document.get("bounding_boxes") or []),
            "ground_truth": row.get("ground_truth") or {},
            "normalized_ground_truth": normalized_truth,
            "evaluated_at": row.get("evaluated_at"),
            "batch_id": row.get("batch_id"),
            "evaluation_id": row.get("id"),
            "record_id": row.get("finance_record_id"),
            "dataset_name": batch.get("dataset_name"),
            "evaluation_mode": row_mode,
            "dataset_index": item.get("dataset_index", 0),
            "matched_image": item.get("source_file_name") or document.get("file_name"),
            "results": [{
                "model_name": row.get("model_name") or "unknown",
                "success": row.get("status") == "COMPLETED",
                "latency_ms": row.get("latency_ms") or 0,
                "error": row.get("error_message"),
                "system": {
                    "prediction": prediction,
                    "pipeline_trace": pipeline_trace,
                    "error_analysis": row.get("error_analysis") or {},
                    "score": {
                        "fields": row.get("field_scores") or {},
                        "correct_fields": row.get("correct_fields") or 0,
                        "evaluated_fields": row.get("evaluated_fields") or 0,
                        "field_accuracy": row.get("field_accuracy") or 0,
                        "complete_match": bool(row.get("complete_match")),
                        "selection_rubric": replay_rubric,
                    },
                    "ocr_impact": row.get("ocr_impact") or {},
                    "workbook": row.get("workbook_result") or {},
                },
            }],
        })
    return runs


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
    logger.warning("Finance evaluation request models: %s", model_names)
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
        "ocr_diagnostics": _ocr_structure_diagnostics(document.get("bounding_boxes") or []),
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
