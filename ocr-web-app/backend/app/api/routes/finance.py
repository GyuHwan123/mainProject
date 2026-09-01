"""Finance HTTP endpoints; receipt processing lives in the service layer."""

import asyncio

from app.services.finance_receipt_pipeline import *


RECEIPT_CLASSIFICATION_BUDGET_SECONDS = 1560
_receipt_classification_lock = asyncio.Lock()


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
        normalized["duplicate_of_record_id"] = duplicate_record["id"]
        normalized["structured_data"]["duplicate_detection"] = {
            "is_duplicate": True,
            "previous_record_id": duplicate_record["id"],
            "message": "동일 영수증의 이전 분석 기록이 있으며 현재 모델로 새 기록을 생성했습니다.",
        }
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
    if payload.save_to_archive:
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
    for record in supabase_service.list_finance_records(user.email):
        data = record.get("structured_data") or {}
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
    for item in archive:
        record = item.get("finance_records") or {}
        if isinstance(record, list):
            record = record[0] if record else {}
        if record:
            item["expense_category"] = record.get("expense_category")
            item["merchant"] = record.get("merchant")
            item["transaction_date"] = record.get("transaction_date")
            item["total_amount"] = record.get("total_amount") or 0
        document = item.get("ocr_documents") or {}
        if isinstance(document, list):
            document = document[0] if document else {}
        storage_path = item.get("source_storage_path") or document.get("file_url")
        if not item.get("source_file_name") and document.get("file_name"):
            item["source_file_name"] = document["file_name"]
        item["image_url"] = supabase_service.create_document_signed_url(storage_path) if storage_path else None
    return archive


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
    document_type, expense_category, needs_review, reason = validate_classification(
        values["document_type"], values["expense_category"],
        allow_explicit_document_type=True,
    )
    if needs_review:
        raise HTTPException(status_code=422, detail=f"유효하지 않은 비용 분류입니다: {reason}")
    values["document_type"] = document_type
    values["expense_category"] = expense_category
    if not values["total_amount"]:
        values["total_amount"] = values["supply_amount"] + values["tax_amount"]
    current = next(
        (item for item in supabase_service.list_finance_records(user.email, limit=1000) if item.get("id") == record_id),
        None,
    )
    if current:
        structured_data = dict(current.get("structured_data") or {})
        previous_decision = dict(structured_data.get("classification_decision") or {})
        structured_data["expense_category"] = expense_category
        structured_data["doc_type"] = document_type
        structured_data["needs_review"] = False
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


@router.post("/records/{record_id}/submit", response_model=FinanceRecord)
def submit_to_finance(record_id: str, user: User = Depends(require_current_user)) -> dict[str, Any]:
    record = next((item for item in supabase_service.list_finance_records(user.email, limit=1000) if item.get("id") == record_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="재무 기록을 찾을 수 없습니다.")
    if record.get("status") != "CONFIRMED":
        raise HTTPException(status_code=422, detail="사용자가 최종 확정한 문서만 재무팀에 보낼 수 있습니다.")
    structured_data = dict(record.get("structured_data") or {})
    workflow = dict(structured_data.get("finance_workflow") or {})
    workflow.update({
        "finance_team_status": "확인 필요",
        "submitted_at": workflow.get("submitted_at") or datetime.now(timezone.utc).isoformat(),
        "finance_confirmed_at": None,
        "document_filename": workflow.get("document_filename") or f"finance-receipt-{record_id}.xlsx",
    })
    structured_data["finance_workflow"] = workflow
    return supabase_service.update_finance_record(user.email, record_id, {"structured_data": structured_data})


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
    content = build_finance_workbook(records, author={"name": user.name, "email": user.email})
    filename = f"finance-receipts-{date.today().isoformat()}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export")
def export_records(user: User = Depends(require_current_user)) -> StreamingResponse:
    records = [record for record in supabase_service.list_finance_records(user.email, limit=1000) if record.get("status") == "CONFIRMED"]
    if not records:
        raise HTTPException(status_code=422, detail="확정된 재무 문서가 없습니다. 내용을 검토하고 확정해 주세요.")
    content = build_finance_workbook(records, author={"name": user.name, "email": user.email})
    filename = f"finance-receipts-{date.today().isoformat()}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
