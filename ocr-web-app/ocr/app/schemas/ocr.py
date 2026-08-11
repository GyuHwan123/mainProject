from pydantic import BaseModel


class OCRItem(BaseModel):
    text: str
    confidence: float
    bbox: list[list[int]]


class OCRPage(BaseModel):
    page: int
    text: str
    items: list[OCRItem]


class OCRResponse(BaseModel):
    filename: str
    content_type: str
    pages: list[OCRPage]