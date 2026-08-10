from pydantic import BaseModel


class OCRResult(BaseModel):
    filename: str
    text: str
