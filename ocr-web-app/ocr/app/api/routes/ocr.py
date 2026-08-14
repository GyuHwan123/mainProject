import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile

from app.schemas.ocr import OCRResponse
from app.services.ocr.ocr_service import process_ocr
from app.services.docx_service import convert_docx_to_pdf_bytes
from app.services.spreadsheet_service import extract_spreadsheet
from app.services.receipt_evaluate_service import evaluate_receipt


router = APIRouter()


@router.post("/upload", response_model=OCRResponse)
async def upload(
    file: UploadFile = File(...),
    ground_truth_json: str | None = Form(default=None),
    processing_mode: str = Query(default="document", pattern="^(document|receipt)$"),
):
    result = await process_ocr(file, processing_mode=processing_mode)
    if ground_truth_json:
        try:
            result.evaluation = evaluate_receipt(result, ground_truth_json)
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="정답 JSON 형식이 올바르지 않습니다.") from exc
    return result


@router.post("/docx-preview")
async def docx_preview(file: UploadFile = File(...)):
    if Path(file.filename or "").suffix.lower() != ".docx":
        raise HTTPException(status_code=400, detail="DOCX 파일만 미리보기할 수 있습니다.")

    with NamedTemporaryFile(delete=False, suffix=".docx") as temp:
        temp.write(await file.read())
        temp_path = Path(temp.name)

    try:
        return Response(
            content=convert_docx_to_pdf_bytes(temp_path),
            media_type="application/pdf",
        )
    finally:
        temp_path.unlink(missing_ok=True)


@router.post("/spreadsheet-preview", response_model=OCRResponse)
async def spreadsheet_preview(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".xlsx", ".xlsm"}:
        raise HTTPException(status_code=400, detail="XLSX 또는 XLSM 파일만 미리보기할 수 있습니다.")

    with NamedTemporaryFile(delete=False, suffix=suffix) as temp:
        temp.write(await file.read())
        temp_path = Path(temp.name)

    try:
        return OCRResponse(
            filename=file.filename or "spreadsheet.xlsx",
            content_type="spreadsheet",
            pages=extract_spreadsheet(temp_path),
        )
    finally:
        temp_path.unlink(missing_ok=True)
