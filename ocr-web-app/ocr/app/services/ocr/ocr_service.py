import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import UploadFile
from paddleocr import PaddleOCR

from app.schemas.ocr import OCRResponse
from app.services.file_classifier import (
    FileContentType,
    classify_file,
)
from app.services.pdf_service import extract_pdf_text
from app.services.ocr.ocr_parser import build_ocr_page


ocr = PaddleOCR(
    lang="korean",
    use_doc_orientation_classify=True,
    use_doc_unwarping=True,
    use_textline_orientation=True,

    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="korean_PP-OCRv5_mobile_rec",

    enable_mkldnn=False,
)


def run_paddle_ocr(
    file_path: Path,
):
    """
    PaddleOCR을 실행하고
    OCRPage 목록을 반환한다.
    """

    pages = []

    results = ocr.predict(
        str(file_path)
    )

    for result in results:

        raw_data = result.json

        if isinstance(
            raw_data,
            str,
        ):
            raw_data = json.loads(
                raw_data
            )

        data = raw_data.get(
            "res",
            raw_data,
        )

        page_index = data.get(
            "page_index"
        )

        if page_index is None:
            page_number = len(pages) + 1
        else:
            page_number = page_index + 1

        page = build_ocr_page(
            data,
            page_number,
        )

        pages.append(page)

    return pages


async def process_ocr(
    file: UploadFile,
) -> OCRResponse:

    suffix = (
        Path(file.filename or "")
        .suffix
        .lower()
        or ".tmp"
    )

    # ---------------------------------
    # 임시 파일 저장
    # ---------------------------------

    with NamedTemporaryFile(
        delete=False,
        suffix=suffix,
    ) as temp:

        temp.write(
            await file.read()
        )

        temp_path = Path(temp.name)

    try:

        # ---------------------------------
        # 파일 유형 판단
        # ---------------------------------

        content_type = classify_file(
            temp_path
        )

        print(
            f"\n파일: {file.filename}"
        )

        print(
            f"파일 내용 유형: "
            f"{content_type.value}"
        )

        # ---------------------------------
        # TEXT_ONLY
        # ---------------------------------

        if content_type == FileContentType.TEXT_ONLY:

            print(
                "→ 텍스트를 직접 추출합니다."
            )

            pages = extract_pdf_text(
                temp_path
            )

        # ---------------------------------
        # IMAGE_ONLY
        # ---------------------------------

        elif content_type == FileContentType.IMAGE_ONLY:

            print(
                "→ PaddleOCR을 실행합니다."
            )

            pages = run_paddle_ocr(
                temp_path
            )

        # ---------------------------------
        # TEXT_AND_IMAGE
        # ---------------------------------

        elif (
            content_type
            == FileContentType.TEXT_AND_IMAGE
        ):

            print(
                "→ 텍스트 추출 + OCR을 "
                "실행합니다."
            )

            # 현재는 1차 구현.
            # 추후 PDF 내부 이미지 영역만
            # OCR하도록 개선.

            pages = run_paddle_ocr(
                temp_path
            )

        # ---------------------------------
        # UNKNOWN
        # ---------------------------------

        else:

            print(
                "→ 지원하지 않는 파일입니다."
            )

            pages = []

        # ---------------------------------
        # 응답
        # ---------------------------------

        return OCRResponse(
            filename=file.filename or "unknown",
            content_type=content_type.value,
            pages=pages,
        )

    finally:

        temp_path.unlink(
            missing_ok=True
        )