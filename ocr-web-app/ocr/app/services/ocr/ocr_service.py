import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import UploadFile
from paddleocr import PaddleOCR

from app.schemas.ocr import OCRPage, OCRResponse
from app.services.file_classifier import (
    FileContentType,
    classify_file,
)
from app.services.pdf_service import extract_pdf_text
from app.services.docx_service import extract_docx_text
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
) -> list[OCRPage]:
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

        if content_type == FileContentType.TEXT_ONLY:

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

                text = extract_docx_text(
                    temp_path
                )

                pages = [
                    build_text_page(text)
                ]

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
                "→ PaddleOCR을 실행합니다."
            )

            pages = run_paddle_ocr(
                temp_path
            )

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
                    "→ PDF 텍스트 + 이미지 처리를 준비합니다."
                )

                # 현재 단계에서는 기존 OCR 사용
                pages = run_paddle_ocr(
                    temp_path
                )

            elif file_extension == ".docx":

                print(
                    "→ DOCX 텍스트 + 이미지 처리를 준비합니다."
                )

                # 현재 단계에서는 DOCX 전체 텍스트를 우선 추출
                text = extract_docx_text(
                    temp_path
                )

                pages = [
                    build_text_page(text)
                ]

                # TODO:
                # DOCX 내부 이미지 추출 후
                # PaddleOCR 연결

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