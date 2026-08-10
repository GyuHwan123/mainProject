class OCRService:
    """Adapter point for the selected OCR engine."""

    async def extract_text(self, content: bytes) -> str:
        raise NotImplementedError("Configure an OCR engine before using this service.")
