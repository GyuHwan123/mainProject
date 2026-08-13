from pydantic import BaseModel


class OCRItem(BaseModel):
    text: str
    confidence: float
    bbox: list[list[int]]
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
    filename: str
    content_type: str
    pages: list[OCRPage]
