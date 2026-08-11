from fastapi import APIRouter, UploadFile, File

from app.schemas.ocr import OCRResponse
from app.services.ocr.ocr_service import process_ocr


router = APIRouter()


@router.post("/upload", response_model=OCRResponse)
async def upload(file: UploadFile = File(...)):
    return await process_ocr(file)