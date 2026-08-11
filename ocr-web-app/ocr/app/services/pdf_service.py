from pathlib import Path

import fitz

from app.schemas.ocr import OCRPage


def extract_pdf_text(
    file_path: Path,
) -> list[OCRPage]:

    pages = []

    document = fitz.open(file_path)

    try:
        for page_index, page in enumerate(document):

            text = page.get_text("text")

            pages.append(
                OCRPage(
                    page=page_index + 1,
                    text=text.strip(),
                    items=[],
                )
            )

    finally:
        document.close()

    return pages