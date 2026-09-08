"""Finance HTTP endpoints; receipt processing lives in the service layer."""

import asyncio
from pydantic import BaseModel, Field
from app.services.finance_email_review import create_review, activate_review, read_review, confirm_review
from threading import Lock
from datetime import date, datetime, timezone
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import load_workbook

from app.api.routes.auth import require_current_user
from app.api.routes.chatbot import generate
from app.constants.finance_taxonomy import (
    ALLOWED_DOCUMENT_TYPES,
    ALLOWED_EXPENSE_CATEGORIES,
    CATEGORY_TO_DOCUMENT_TYPE,
    validate_classification,
)
from app.core.config import settings
from app.models.finance_receipt import FinanceClassifyRequest, FinanceExportRequest, FinanceRecord, FinanceRecordUpdate
from app.models.user import User
from app.services.finance_receipt_identity import legacy_receipt_key, receipt_fingerprint, receipt_hints, receipt_identity_key
from app.services.finance_receipt_simple import (
    FINANCE_PROMPT_VERSION,
    RECEIPTS_MODEL_NAME,
    EXPENSE_CATEGORIES,
    _bounded_ocr_text,
    _classify_receipt,
    _classify_receipt_with_model,
    _normalize,
    _normalize_expense_category,
    _preflight_review_reasons,
    _simple_receipt_prompt,
)
from app.services.email_service import email_service
from app.services.finance_workbook_service import build_finance_workbook, SHEET_NAMES, SUMMARY_SHEET_NAME
from app.services.supabase_service import supabase_service


router = APIRouter()

# Local aliases keep the route implementation readable without leaking service internals.
_receipt_fingerprint = receipt_fingerprint
_receipt_hints = receipt_hints
_receipt_identity_key = receipt_identity_key
_legacy_receipt_key = legacy_receipt_key


RECEIPT_CLASSIFICATION_BUDGET_SECONDS = settings.RECEIPTS_CLASSIFICATION_BUDGET_SECONDS
_receipt_classification_lock = asyncio.Lock()


def _duplicate_finance_response(existing: dict[str, Any]) -> dict[str, Any]:
    """Return the existing row while telling the client why it was reused."""
    result = dict(existing)
    structured_data = dict(existing.get("structured_data") or {})
    structured_data["duplicate_detection"] = {
        "is_duplicate": True,
        "previous_record_id": existing["id"],
        "message": "동일 영수증의 기존 분석 기록을 반환했습니다. 새 재무 기록은 생성하지 않았습니다.",
    }
    result["structured_data"] = structured_data
    result["duplicate_of_record_id"] = existing["id"]
    return result


async def _classify_receipt_serialized(
    text: str, filename: str, pages: list[dict[str, Any]],
) -> dict[str, Any]:
    async with _receipt_classification_lock:
        return await _classify_receipt(text, filename, pages)

@router.post("/records/classify", response_model=FinanceRecord)
async def classify_and_save(payload: FinanceClassifyRequest, user: User = Depends(require_current_user)) -> dict[str, Any]:
    if not RECEIPTS_MODEL_NAME.strip():
        raise HTTPException(
            status_code=503,
            detail="영수증 LLM 모델이 설정되지 않았습니다. .env에 RECEIPTS_LLM_MODEL을 설정해 주세요.",
        )
    document = supabase_service.get_ocr_document(user.email, payload.document_id)
    extracted_text = (document.get("extracted_text") or "").strip()
    if not extracted_text:
        raise HTTPException(status_code=422, detail="분류할 OCR 텍스트가 없습니다.")
    existing_records = supabase_service.list_finance_records(user.email, limit=1000)
    hints = _receipt_hints(extracted_text, document.get("file_name") or "receipt")
    fingerprint = _receipt_fingerprint(extracted_text)
    identity_key = _receipt_identity_key(extracted_text, hints)
    duplicate_record = None
    for existing in existing_records:
        if str(existing.get("document_id")) == payload.document_id:
            continue
        data = existing.get("structured_data") or {}
        if data.get("receipt_fingerprint") == fingerprint or (identity_key and data.get("receipt_identity_key") == identity_key):
            duplicate_record = existing
            break

    # Fingerprint/identity matching does not need the LLM. More importantly,
    # returning here guarantees that a duplicate upload cannot create a second
    # finance row merely because it has a different OCR document id.
    if duplicate_record is not None:
        return _duplicate_finance_response(duplicate_record)

    try:
        classified = await asyncio.wait_for(
            _classify_receipt_serialized(
                extracted_text,
                document.get("file_name") or "receipt",
                document.get("bounding_boxes") or [],
            ),
            timeout=RECEIPT_CLASSIFICATION_BUDGET_SECONDS,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                f"영수증 분류가 안전 처리 시간 {RECEIPT_CLASSIFICATION_BUDGET_SECONDS}초를 "
                "초과해 중단되었습니다. 다음 영수증은 이전 Ollama 작업 종료 후 처리할 수 있습니다."
            ),
        ) from exc
    normalized = _normalize(classified, document.get("file_name") or "receipt", extracted_text)
    normalized["structured_data"]["receipt_fingerprint"] = fingerprint
    normalized["structured_data"]["receipt_identity_key"] = identity_key
    candidate = {**normalized, "structured_data": normalized["structured_data"]}
    candidate_legacy_key = _legacy_receipt_key(candidate)
    if candidate_legacy_key and duplicate_record is None:
        for existing in existing_records:
            if str(existing.get("document_id")) == payload.document_id:
                continue
            if _legacy_receipt_key(existing) == candidate_legacy_key:
                duplicate_record = existing
                break
    if duplicate_record is not None:
        # Older rows may not have fingerprints and can only be identified after
        # normalization. Reuse them as well instead of persisting a new row.
        return _duplicate_finance_response(duplicate_record)
    else:
        normalized["duplicate_of_record_id"] = None
        normalized["structured_data"].pop("duplicate_detection", None)
    normalized["prompt_version"] = FINANCE_PROMPT_VERSION
    normalized["processed_at"] = datetime.now(timezone.utc).isoformat()
    finance_record = supabase_service.save_finance_record(
        user_email=user.email,
        document_id=payload.document_id,
        payload=normalized,
    )
    # A duplicate analysis may have a new finance_record_id, but it must not
    # create another archive card for the same physical receipt.
    if payload.save_to_archive and duplicate_record is None:
        supabase_service.save_receipt_archive(
            user_email=user.email,
            document_id=payload.document_id,
            finance_record=finance_record,
            source_file_name=payload.source_file_name or document.get("file_name") or "receipt",
            source_storage_path=document.get("file_url") or "",
        )
    return finance_record


@router.get("/records", response_model=list[FinanceRecord])
def list_records(user: User = Depends(require_current_user)) -> list[dict[str, Any]]:
    unique_records = []
    seen = set()
    for record in supabase_service.list_finance_records(user.email, limit=None):
        data = record.get("structured_data") or {}
        if record.get("status") != "CONFIRMED" or not data.get("excel_saved_at"):
            continue
        duplicate_key = data.get("receipt_identity_key") or data.get("receipt_fingerprint") or _legacy_receipt_key(record)
        if duplicate_key and duplicate_key in seen:
            continue
        if duplicate_key:
            seen.add(duplicate_key)
        unique_records.append(record)
    return unique_records


@router.get("/taxonomy")
def get_finance_taxonomy(user: User = Depends(require_current_user)) -> dict[str, Any]:
    return {
        "document_types": list(ALLOWED_DOCUMENT_TYPES),
        "expense_categories": list(ALLOWED_EXPENSE_CATEGORIES),
        "category_to_document_type": CATEGORY_TO_DOCUMENT_TYPE,
    }


@router.get("/receipt-archive")
def receipt_archive(category: str | None = None, user: User = Depends(require_current_user)) -> list[dict[str, Any]]:
    if category and category != "UNCLASSIFIED" and category not in ALLOWED_EXPENSE_CATEGORIES:
        raise HTTPException(status_code=422, detail="지원하지 않는 영수증 카테고리입니다.")
    archive = supabase_service.list_receipt_archive(user.email, category=category)
    unique_archive = []
    seen_receipts = set()
    for item in archive:
        record = item.get("finance_records") or {}
        if isinstance(record, list):
            record = record[0] if record else {}
        if record:
            item["expense_category"] = record.get("expense_category")
            item["merchant"] = record.get("merchant")
            item["transaction_date"] = record.get("transaction_date")
            item["total_amount"] = record.get("total_amount") or 0
        structured_data = record.get("structured_data") or {}
        duplicate_key = (
            item.get("receipt_fingerprint")
            or structured_data.get("receipt_identity_key")
            or structured_data.get("receipt_fingerprint")
            or _legacy_receipt_key(record)
        )
        if duplicate_key and duplicate_key in seen_receipts:
            continue
        if duplicate_key:
            seen_receipts.add(duplicate_key)
        document = item.get("ocr_documents") or {}
        if isinstance(document, list):
            document = document[0] if document else {}
        storage_path = item.get("source_storage_path") or document.get("file_url")
        if not item.get("source_file_name") and document.get("file_name"):
            item["source_file_name"] = document["file_name"]
        item["image_url"] = supabase_service.create_document_signed_url(storage_path) if storage_path else None
        unique_archive.append(item)
    return unique_archive


@router.delete("/receipt-archive/{archive_id}")
def delete_receipt_archive_item(archive_id: str, user: User = Depends(require_current_user)) -> dict[str, Any]:
    deleted_count = supabase_service.soft_delete_receipt_archive(user.email, archive_id)
    if not deleted_count:
        raise HTTPException(status_code=404, detail="삭제할 영수증 보관 기록을 찾을 수 없습니다.")
    return {"deleted": deleted_count}


@router.delete("/receipt-archive")
def delete_all_receipt_archive(user: User = Depends(require_current_user)) -> dict[str, Any]:
    return {"deleted": supabase_service.soft_delete_receipt_archive(user.email)}


@router.get("/history")
def finance_history(user: User = Depends(require_current_user)) -> list[dict[str, Any]]:
    history = []
    for record in supabase_service.list_finance_records(user.email, limit=1000):
        workflow = (record.get("structured_data") or {}).get("finance_workflow") or {}
        if not workflow.get("submitted_at"):
            continue
        history.append({
            "id": record.get("id"),
            "document_type": record.get("document_type"),
            "expense_category": record.get("expense_category"),
            "merchant": record.get("merchant"),
            "total_amount": record.get("total_amount"),
            "document_filename": workflow.get("document_filename") or f"finance-receipt-{record.get('id')}.xlsx",
            "finance_team_status": workflow.get("finance_team_status") or "확인 필요",
            "submitted_at": workflow.get("submitted_at"),
            "finance_confirmed_at": workflow.get("finance_confirmed_at"),
        })
    return history


@router.patch("/records/{record_id}", response_model=FinanceRecord)
def update_record(record_id: str, payload: FinanceRecordUpdate, user: User = Depends(require_current_user)) -> dict[str, Any]:
    values = payload.model_dump(mode="json")
    items = values.pop("items", None)
    document_type, expense_category, needs_review, reason = validate_classification(
        values["document_type"], values["expense_category"],
        allow_explicit_document_type=True,
    )
    if needs_review:
        raise HTTPException(status_code=422, detail=f"유효하지 않은 비용 분류입니다: {reason}")
    values["document_type"] = document_type
    values["expense_category"] = expense_category
    if (
        not values["total_amount"]
        and values["supply_amount"] is not None
        and values["tax_amount"] is not None
    ):
        values["total_amount"] = values["supply_amount"] + values["tax_amount"]
    current = next(
        (item for item in supabase_service.list_finance_records(user.email, limit=1000) if item.get("id") == record_id),
        None,
    )
    if current:
        structured_data = dict(current.get("structured_data") or {})
        previous_decision = dict(structured_data.get("classification_decision") or {})
        structured_data["expense_category"] = expense_category
        structured_data["category_confirmation"] = {
            "confirmed_category": expense_category,
            "confirmed_by": user.email,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        }
        structured_data["doc_type"] = document_type
        if items is not None:
            structured_data["items"] = items
        structured_data["needs_review"] = False
        if values["status"] == "CONFIRMED":
            structured_data["excel_saved_at"] = datetime.now(timezone.utc).isoformat()
        else:
            structured_data.pop("excel_saved_at", None)
            structured_data.pop("finance_workflow", None)
        structured_data.pop("classification_review_reason", None)
        structured_data["classification_decision"] = {
            **previous_decision,
            "expense_category": expense_category,
            "selected_document_type": document_type,
            "status": "USER_CONFIRMED",
            "reason": None,
        }
        values["structured_data"] = structured_data
    return supabase_service.update_finance_record(user.email, record_id, values)


_finance_submission_lock = Lock()


def _email_finance_records(records: list[dict[str, Any]], user: User) -> list[dict[str, Any]]:
    if not records:
        return []
    filename = f"finance-receipts-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.xlsx"
    content = build_finance_workbook(records, author={"name": user.name, "email": user.email})
    review_id, review_url = create_review(records, user)
    try:
        email_service.send_finance_records(author_email=user.email, record_count=len(records), content=content, filename=filename, review_url=review_url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="이메일 발송을 확인하지 못했습니다. 기록은 비우지 않았습니다. 수신함을 확인한 후 다시 시도해 주세요.") from exc
    submitted = []
    submitted_at = datetime.now(timezone.utc).isoformat()
    try:
        for record in records:
            structured_data = dict(record.get("structured_data") or {})
            previous_workflow = structured_data.get("finance_workflow") or {}
            if previous_workflow.get("submitted_at"):
                structured_data["finance_workflow"] = {
                    **previous_workflow,
                    "last_resent_at": submitted_at,
                    "last_resent_filename": filename,
                }
                submitted.append(supabase_service.update_finance_record(user.email, record["id"], {"structured_data": structured_data}))
                continue
            structured_data["finance_workflow"] = {
                **(structured_data.get("finance_workflow") or {}),
                "finance_team_status": "확인 필요",
                "submitted_at": submitted_at,
                "email_sent_at": submitted_at,
                "email_recipient": "docai0914@gmail.com",
                "finance_confirmed_at": None,
                "document_filename": filename,
            }
            submitted.append(supabase_service.update_finance_record(user.email, record["id"], {"structured_data": structured_data}))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="이메일은 발송됐지만 전송 기록 저장에 실패했습니다. 중복 발송을 피하려면 다시 보내지 말고 관리자에게 문의해 주세요.") from exc
    activate_review(review_id)
    return submitted


class EmailReviewRequest(BaseModel):
    token: str = Field(min_length=40, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


@router.post('/email-review/inspect')
def inspect_email_review(payload: EmailReviewRequest):
    return read_review(payload.token)


@router.post('/email-review/confirm')
def complete_email_review(payload: EmailReviewRequest):
    return confirm_review(payload.token)


@router.post("/records/submit-all", response_model=list[FinanceRecord])
def submit_all_to_finance(user: User = Depends(require_current_user), include_previous: bool = False) -> list[dict[str, Any]]:
    with _finance_submission_lock:
        pending = [record for record in list_records(user)
                   if include_previous or not ((record.get("structured_data") or {}).get("finance_workflow") or {}).get("submitted_at")]
        return _email_finance_records(pending, user)


@router.post("/records/preview")
def preview_records(payload: FinanceExportRequest, user: User = Depends(require_current_user)) -> dict[str, Any]:
    available = {record["id"]: record for record in list_records(user)}
    ids = list(dict.fromkeys(payload.record_ids))
    if any(record_id not in available for record_id in ids):
        raise HTTPException(status_code=422, detail="최종 확정하여 Excel에 저장한 기록만 미리 볼 수 있습니다.")
    content = build_finance_workbook([available[record_id] for record_id in ids], author={"name": user.name, "email": user.email})
    workbook = load_workbook(BytesIO(content))
    try:
        selected_types = {available[record_id].get("document_type") for record_id in ids}
        preview_names = [name for kind, name in SHEET_NAMES.items() if kind in selected_types]
        preview_names.append(SUMMARY_SHEET_NAME)
        return {"sheets": [{"name": name, "rows": list(workbook[name].values)} for name in preview_names if name in workbook.sheetnames]}
    finally:
        workbook.close()


@router.post("/records/soft-delete")
def soft_delete_sent_records(payload: FinanceExportRequest, user: User = Depends(require_current_user)) -> dict[str, int]:
    ids = list(dict.fromkeys(payload.record_ids))
    available = {record["id"]: record for record in supabase_service.list_finance_records(user.email, limit=None)}
    if any(record_id not in available for record_id in ids):
        raise HTTPException(status_code=404, detail="재무 기록을 찾을 수 없습니다.")
    if any(not ((available[record_id].get("structured_data") or {}).get("finance_workflow") or {}).get("submitted_at") for record_id in ids):
        raise HTTPException(status_code=422, detail="재무팀 발송 완료 기록만 삭제할 수 있습니다.")
    return {"deleted_count": supabase_service.soft_delete_finance_records(user.email, ids)}


@router.post("/records/{record_id}/submit", response_model=FinanceRecord)
def submit_to_finance(record_id: str, user: User = Depends(require_current_user)) -> dict[str, Any]:
    record = next((item for item in supabase_service.list_finance_records(user.email, limit=1000) if item.get("id") == record_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="재무 기록을 찾을 수 없습니다.")
    if record.get("status") != "CONFIRMED" or not (record.get("structured_data") or {}).get("excel_saved_at"):
        raise HTTPException(status_code=422, detail="사용자가 최종 확정한 문서만 재무팀에 보낼 수 있습니다.")
    with _finance_submission_lock:
        pending = next((item for item in list_records(user) if item["id"] == record_id
                        and not ((item.get("structured_data") or {}).get("finance_workflow") or {}).get("submitted_at")), None)
        if pending is None:
            return record
        return _email_finance_records([pending], user)[0]


@router.post("/records/{record_id}/finance-confirm", response_model=FinanceRecord)
def confirm_by_finance(record_id: str, user: User = Depends(require_current_user)) -> dict[str, Any]:
    if user.role not in {"ADMIN", "DEVELOPER"}:
        raise HTTPException(status_code=403, detail="재무팀 확인 권한이 없습니다.")
    record = next((item for item in supabase_service.list_finance_records(user.email, limit=1000) if item.get("id") == record_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="재무 기록을 찾을 수 없습니다.")
    structured_data = dict(record.get("structured_data") or {})
    workflow = dict(structured_data.get("finance_workflow") or {})
    if not workflow.get("submitted_at"):
        raise HTTPException(status_code=422, detail="아직 재무팀에 제출되지 않은 문서입니다.")
    workflow.update({"finance_team_status": "확인", "finance_confirmed_at": datetime.now(timezone.utc).isoformat()})
    structured_data["finance_workflow"] = workflow
    return supabase_service.update_finance_record(user.email, record_id, {"structured_data": structured_data})


@router.get("/records/{record_id}/export")
def export_record(record_id: str, user: User = Depends(require_current_user)) -> StreamingResponse:
    record = next((item for item in supabase_service.list_finance_records(user.email, limit=1000) if item.get("id") == record_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="재무 기록을 찾을 수 없습니다.")
    if record.get("status") != "CONFIRMED" or not (record.get("structured_data") or {}).get("excel_saved_at"):
        raise HTTPException(status_code=422, detail="최종 확정하여 Excel에 저장한 기록만 다운로드할 수 있습니다.")
    content = build_finance_workbook([record], author={"name": user.name, "email": user.email})
    filename = f"finance-receipt-{record_id}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/records/export")
def export_selected_records(payload: FinanceExportRequest, user: User = Depends(require_current_user)) -> StreamingResponse:
    requested_ids = list(dict.fromkeys(payload.record_ids))
    records_by_id = {
        record.get("id"): record
        for record in supabase_service.list_finance_records(user.email, limit=1000)
        if record.get("id") in requested_ids
    }
    records = [records_by_id[record_id] for record_id in requested_ids if record_id in records_by_id]
    if len(records) != len(requested_ids):
        raise HTTPException(status_code=404, detail="일부 재무 기록을 찾을 수 없습니다.")
    if any(record.get("status") != "CONFIRMED" or not (record.get("structured_data") or {}).get("excel_saved_at") for record in records):
        raise HTTPException(status_code=422, detail="최종 확정하여 Excel에 저장한 기록만 다운로드할 수 있습니다.")
    content = build_finance_workbook(records, author={"name": user.name, "email": user.email})
    filename = f"finance-receipts-{date.today().isoformat()}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export")
def export_records(user: User = Depends(require_current_user)) -> StreamingResponse:
    records = [record for record in supabase_service.list_finance_records(user.email, limit=1000) if record.get("status") == "CONFIRMED" and (record.get("structured_data") or {}).get("excel_saved_at")]
    if not records:
        raise HTTPException(status_code=422, detail="확정된 재무 문서가 없습니다. 내용을 검토하고 확정해 주세요.")
    content = build_finance_workbook(records, author={"name": user.name, "email": user.email})
    filename = f"finance-receipts-{date.today().isoformat()}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
