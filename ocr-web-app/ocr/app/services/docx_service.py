from pathlib import Path

from docx import Document


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