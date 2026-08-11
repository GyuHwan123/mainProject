from pathlib import Path
from tempfile import TemporaryDirectory

from docx import Document
from docx2pdf import convert

from app.schemas.ocr import OCRPage
from app.services.pdf_service import extract_pdf_text_and_images


def extract_docx_text(
    file_path: Path,
) -> str:
    """
    DOCX에서 텍스트를 추출한다.

    추출 대상:
    - 일반 문단
    - 표
    """

    document = Document(
        str(file_path)
    )

    texts: list[str] = []

    # ---------------------------------
    # 일반 문단
    # ---------------------------------

    for paragraph in document.paragraphs:

        text = paragraph.text.strip()

        if text:
            texts.append(text)

    # ---------------------------------
    # 표
    # ---------------------------------

    for table in document.tables:

        for row in table.rows:

            row_texts = []

            for cell in row.cells:

                text = cell.text.strip()

                if text:
                    row_texts.append(text)

            if row_texts:

                texts.append(
                    " | ".join(row_texts)
                )

    return "\n".join(texts)

def extract_docx_text_and_images(
    file_path: Path,
    ocr_runner,
) -> list[OCRPage]:
    """
    DOCX의 텍스트와 이미지를 함께 처리한다.

    Microsoft Word를 이용해 DOCX를 PDF로 변환한 뒤,
    기존 PDF mixed-content 처리 로직을 재사용한다.
    """

    with TemporaryDirectory() as temp_dir:

        temp_path = Path(temp_dir)

        pdf_path = (
            temp_path
            / f"{file_path.stem}.pdf"
        )

        # ---------------------------------
        # DOCX → PDF
        # ---------------------------------

        convert(
            str(file_path),
            str(pdf_path),
        )

        # ---------------------------------
        # PDF mixed 처리
        # ---------------------------------

        return extract_pdf_text_and_images(
            pdf_path,
            ocr_runner,
        )