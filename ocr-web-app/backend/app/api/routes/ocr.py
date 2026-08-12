import json
from io import BytesIO

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.api.routes.auth import require_current_user
from app.core.config import settings
from app.models.user import User
from app.schemas.ocr import DocumentHistoryItem, OCRResponse
from app.services.supabase_service import supabase_service

router = APIRouter()
MAX_FILE_SIZE = 50 * 1024 * 1024


def save_result(*, user: User, file: UploadFile, content: bytes, result: OCRResponse) -> OCRResponse:
    document = supabase_service.save_ocr_document(
        user_email=user.email,
        filename=file.filename or result.filename,
        mime_type=file.content_type or "application/octet-stream",
        content=content,
        content_type=result.content_type,
        pages=[page.model_dump() for page in result.pages],
    )
    result.document_id = document["id"]
    return result


@router.post("/archive", response_model=OCRResponse)
async def archive_extracted_file(
    file: UploadFile = File(...),
    result_json: str = Form(...),
    user: User = Depends(require_current_user),
) -> OCRResponse:
    try:
        result = OCRResponse.model_validate(json.loads(result_json))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="잘못된 추출 결과입니다.") from exc
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="파일은 최대 50MB까지 저장할 수 있습니다.")
    return save_result(user=user, file=file, content=content, result=result)


@router.post("/upload", response_model=OCRResponse)
async def upload_file(
    file: UploadFile = File(...),
    user: User = Depends(require_current_user),
) -> OCRResponse:
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="파일은 최대 50MB까지 저장할 수 있습니다.")

    filename = file.filename or "upload"
    mime_type = file.content_type or "application/octet-stream"
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{settings.OCR_BASE_URL.rstrip('/')}/upload",
                files={"file": (filename, content, mime_type)},
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=exc.response.text or "OCR processing failed") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="OCR service is unavailable") from exc

    result = OCRResponse.model_validate(response.json())
    return save_result(user=user, file=file, content=content, result=result)


@router.get("/history", response_model=list[DocumentHistoryItem])
def ocr_history(user: User = Depends(require_current_user)) -> list[DocumentHistoryItem]:
    return [DocumentHistoryItem.model_validate(item) for item in supabase_service.list_ocr_documents(user.email)]


@router.get("/documents/{document_id}", response_model=OCRResponse)
def get_document(document_id: str, user: User = Depends(require_current_user)) -> OCRResponse:
    document = supabase_service.get_ocr_document(user.email, document_id)
    pages = document.get("bounding_boxes") or []
    return OCRResponse(
        document_id=document["id"],
        filename=document["file_name"],
        content_type="stored_document",
        pages=pages,
    )


@router.get("/documents/{document_id}/file")
def get_document_file(document_id: str, user: User = Depends(require_current_user)) -> StreamingResponse:
    document = supabase_service.get_ocr_document(user.email, document_id)
    content, mime_type = supabase_service.download_document(document["file_url"])
    return StreamingResponse(BytesIO(content), media_type=mime_type)
