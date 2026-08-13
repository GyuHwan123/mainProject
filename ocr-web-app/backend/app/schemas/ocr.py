from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OCRItem(BaseModel):
    text: str
    confidence: float
    bbox: list[list[float]]
    cell: str | None = None
    row: int | None = None
    column: int | None = None


class OCRPage(BaseModel):
    page: int
    text: str
    items: list[OCRItem]
    sheet_name: str | None = None
    rows: list[list[str]] | None = None


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
