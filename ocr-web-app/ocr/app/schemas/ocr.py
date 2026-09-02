from pydantic import BaseModel


class OCRItem(BaseModel):
    text: str
    confidence: float
    bbox: list[list[int]]
    cell: str | None = None
    row: int | None = None
    column: int | None = None


class OCRTable(BaseModel):
    bbox: list[list[int]]
    confidence: float
    rows: list[list[str]]
    columns: list[str] | None = None
    row_count: int | None = None
    column_count: int | None = None


class OCRRegion(BaseModel):
    type: str
    bbox: list[list[int]]
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
    filename: str
    content_type: str
    pages: list[OCRPage]
    processing_mode: str = "document"
    preprocessing: dict | None = None
    timings: dict[str, float] | None = None
    preprocessed_image: str | None = None
    evaluation: dict | None = None
