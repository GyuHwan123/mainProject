import json
import numpy as np
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import UploadFile
from paddleocr import PaddleOCR

from app.schemas.ocr import OCRPage, OCRResponse
from app.services.file_classifier import (
    FileContentType,
    classify_file,
)
from app.services.pdf_service import extract_pdf_text, extract_pdf_text_and_images
from app.services.docx_service import extract_docx_text, extract_docx_text_and_images
from app.services.ocr.ocr_parser import build_ocr_page
from app.services.preprocess_service import preprocess_image
from app.services.spreadsheet_service import extract_spreadsheet


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
    source: Path | np.ndarray,
) -> list[OCRPage]:
    """
    PaddleOCR을 실행하고
    OCRPage 목록을 반환한다.
    """
    
    pages = []

    if isinstance(source, Path):
        ocr_input = str(source)
    else:
        ocr_input = source

    results = ocr.predict(
        ocr_input
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


def build_text_page(
    text: str,
    page_number: int = 1,
) -> OCRPage:
    """
    OCR을 사용하지 않고 직접 추출한 텍스트를
    OCRPage 형태로 변환한다.
    """

    return OCRPage(
        page=page_number,
        text=text.strip(),
        items=[],
    )

def print_final_result(
    pages: list[OCRPage],
) -> None:
    """
    OCR/텍스트 추출이 완료된 최종 결과를
    페이지별로 출력한다.

    파일 형식이나 추출 방식과 관계없이
    OCRPage.text만 출력한다.
    """

    print(
        "\n"
        + "=" * 70
    )

    print(
        "최종 문서 추출 결과"
    )

    print(
        "=" * 70
    )

    for page in pages:

        print(
            f"\n[페이지 {page.page}]"
        )

        print(
            "-" * 70
        )

        print(
            page.text
        )

        print(
            "-" * 70
        )

    print(
        "\n"
        + "=" * 70
    )


def extract_txt_text(
    file_path: Path,
) -> list[OCRPage]:
    """
    TXT / MD / CSV 파일의 텍스트를 직접 읽는다.
    """

    text = file_path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    return [
        build_text_page(text)
    ]


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

        temp_path = Path(
            temp.name
        )

    try:

        # ---------------------------------
        # 파일 콘텐츠 유형 판단
        # ---------------------------------

        content_type = classify_file(
            temp_path
        )

        # ---------------------------------
        # 파일 형식 확인
        # ---------------------------------

        file_extension = (
            temp_path.suffix.lower()
        )

        print(
            f"\n파일: {file.filename}"
        )

        print(
            f"파일 형식: "
            f"{file_extension}"
        )

        print(
            f"파일 내용 유형: "
            f"{content_type.value}"
        )

        # =================================
        # TEXT_ONLY
        # =================================

        if content_type == FileContentType.SPREADSHEET:

            pages = extract_spreadsheet(temp_path)

        elif content_type == FileContentType.TEXT_ONLY:

            print(
                "→ 텍스트를 직접 추출합니다."
            )

            # -----------------------------
            # PDF
            # -----------------------------

            if file_extension == ".pdf":

                pages = extract_pdf_text(
                    temp_path
                )

            # -----------------------------
            # DOCX
            # -----------------------------

            elif file_extension == ".docx":
                pages = extract_docx_text_and_images(
                    temp_path,
                    run_paddle_ocr,
                )

            # -----------------------------
            # 일반 텍스트
            # -----------------------------

            elif file_extension in {
                ".txt",
                ".md",
                ".csv",
            }:

                pages = extract_txt_text(
                    temp_path
                )

            else:

                print(
                    "→ 텍스트 추출기를 찾을 수 없습니다."
                )

                pages = []

        # =================================
        # IMAGE_ONLY
        # =================================

        elif content_type == FileContentType.IMAGE_ONLY:

            print(
                "→ 이미지 전처리 후 PaddleOCR을 실행합니다."
            )

            if file_extension == ".pdf":
                pages = run_paddle_ocr(temp_path)
            elif file_extension == ".docx":
                pages = extract_docx_text_and_images(
                    temp_path,
                    run_paddle_ocr,
                )
            else:
                processed_image = preprocess_image(temp_path)
                pages = run_paddle_ocr(processed_image)

        # =================================
        # TEXT_AND_IMAGE
        # =================================

        elif (
            content_type
            == FileContentType.TEXT_AND_IMAGE
        ):

            print(
                "→ 텍스트 추출 + OCR이 필요한 파일입니다."
            )

            # ---------------------------------
            # 현재 1차 구현
            #
            # 이미지 영역을 별도로 추출하는
            # 로직은 아직 추가하지 않았으므로
            # 현재는 기존 OCR 방식으로 처리.
            #
            # 추후:
            #
            # PDF  → pdf_service
            #        + PDF 이미지 추출
            #        + PaddleOCR
            #
            # DOCX → docx_service
            #        + DOCX 이미지 추출
            #        + PaddleOCR
            # ---------------------------------

            if file_extension == ".pdf":

                print(
                    "→ PDF 텍스트 + 이미지 OCR을 실행합니다."
                )

                # 현재 단계에서는 기존 OCR 사용
                pages = extract_pdf_text_and_images(
                    temp_path,
                    run_paddle_ocr,
                )

            elif file_extension == ".docx":

                print(
                    "→ DOCX 텍스트 + 이미지 OCR을 실행합니다."
                )

                pages = extract_docx_text_and_images(
                    temp_path,
                    run_paddle_ocr,
                )

            else:

                pages = []

        # =================================
        # UNKNOWN
        # =================================

        else:

            print(
                "→ 지원하지 않는 파일입니다."
            )

            pages = []

        # ---------------------------------
        # 최종 결과 출력
        # ---------------------------------

        # Do not print extracted document contents. Besides leaking user data,
        # Windows CP949 consoles can fail on OCR output containing Unicode.
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
