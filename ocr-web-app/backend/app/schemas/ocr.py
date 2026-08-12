from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OCRItem(BaseModel):
    text: str
    confidence: float
    bbox: list[list[float]]


class OCRPage(BaseModel):
    page: int
    text: str
    items: list[OCRItem]


class OCRResponse(BaseModel):
    document_id: str | None = None
    filename: str
    content_type: str
    pages: list[OCRPage]


class DocumentHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    file_name: str
    file_url: str
    status: str
    created_at: datetime
