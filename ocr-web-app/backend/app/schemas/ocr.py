from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OCRItem(BaseModel):
    text: str
    confidence: float
    bbox: list[list[float]]
    cell: str | None = None
    row: int | None = None
    column: int | None = None


class OCRTable(BaseModel):
    bbox: list[list[float]]
    confidence: float
    rows: list[list[str]]
    columns: list[str] | None = None


class OCRRegion(BaseModel):
    type: str
    bbox: list[list[float]]
    confidence: float


class OCRPage(BaseModel):
    page: int
    text: str
    items: list[OCRItem]
    sheet_name: str | None = None
    rows: list[list[str]] | None = None
    tables: list[OCRTable] | None = None
    regions: list[OCRRegion] | None = None


class OCRResponse(BaseModel):
    document_id: str | None = None
    filename: str
    content_type: str
    pages: list[OCRPage]
    processing_mode: str = "document"
    preprocessing: dict | None = None
    timings: dict[str, float] | None = None
    preprocessed_image: str | None = None
    evaluation: dict | None = None


class DocumentHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    file_name: str
    file_url: str
    status: str
    created_at: datetime
