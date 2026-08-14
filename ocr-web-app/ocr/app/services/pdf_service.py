import math
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable

import fitz

from app.schemas.ocr import OCRItem, OCRPage


def _rect_bbox(
    rect: tuple[float, float, float, float],
    page_width: float,
    page_height: float,
) -> list[list[int]] | None:
    """Return a non-empty, page-clipped bbox without cutting glyph edges."""
    x0, y0, x1, y1 = rect
    if not all(math.isfinite(value) for value in rect):
        return None

    left = max(0, min(math.floor(x0), math.ceil(page_width)))
    top = max(0, min(math.floor(y0), math.ceil(page_height)))
    right = max(0, min(math.ceil(x1), math.ceil(page_width)))
    bottom = max(0, min(math.ceil(y1), math.ceil(page_height)))
    if right <= left or bottom <= top:
        return None

    return [[left, top], [right, top], [right, bottom], [left, bottom]]


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


def extract_pdf_text_and_images(
    file_path: Path,
    ocr_runner: Callable[[Path], list[OCRPage]],
) -> list[OCRPage]:
    """
    텍스트와 이미지가 함께 있는 PDF를 처리한다.

    PDF native text는 PyMuPDF로 추출하고,
    PDF 내부 이미지는 추출한 뒤 PaddleOCR을 실행한다.

    이미지 OCR 결과의 bbox를 PDF 페이지 좌표로 변환한 뒤
    native text와 함께 위치 기준으로 정렬한다.
    """

    pages = []

    document = fitz.open(file_path)

    try:

        for page_number, page in enumerate(
            document,
            start=1,
        ):

            page_items: list[OCRItem] = []

            # Word boxes are materially tighter than line boxes for converted
            # DOCX files, especially for tables, tab stops and mixed fonts.
            # The trailing tuple fields are block, line and word indices.
            for word in page.get_text("words"):
                if len(word) < 5:
                    continue
                text = str(word[4]).strip()
                bbox = _rect_bbox(
                    tuple(word[:4]),
                    page.rect.width,
                    page.rect.height,
                )
                if text and bbox:
                    page_items.append(
                        OCRItem(text=text, confidence=1.0, bbox=bbox)
                    )

            blocks = page.get_text(
                "dict"
            ).get(
                "blocks",
                []
            )

            for block in blocks:

                block_type = block.get("type")

                # =================================
                # TEXT
                # =================================

                if block_type == 0:
                    # Native text was extracted above with precise word boxes.
                    continue

                # =================================
                # IMAGE
                # =================================

                elif block_type == 1:

                    image_bytes = block.get(
                        "image"
                    )

                    if not image_bytes:
                        continue

                    image_bbox = block.get(
                        "bbox",
                        (0, 0, 0, 0),
                    )

                    image_x0, image_y0, image_x1, image_y1 = (
                        image_bbox
                    )

                    image_ext = block.get(
                        "ext",
                        "png",
                    )

                    # ---------------------------------
                    # 이미지 크기
                    # ---------------------------------

                    pixmap = fitz.Pixmap(
                        image_bytes
                    )

                    image_width = pixmap.width
                    image_height = pixmap.height

                    if (
                        image_width <= 0
                        or image_height <= 0
                    ):
                        continue

                    # ---------------------------------
                    # 임시 이미지 파일
                    # ---------------------------------

                    with NamedTemporaryFile(
                        delete=False,
                        suffix=f".{image_ext}",
                    ) as temp:

                        temp.write(
                            image_bytes
                        )

                        image_path = Path(
                            temp.name
                        )

                    try:

                        # ---------------------------------
                        # 이미지 OCR
                        # ---------------------------------

                        ocr_pages = ocr_runner(
                            image_path
                        )

                        # ---------------------------------
                        # 이미지 내부 OCR 결과
                        # → PDF 좌표로 변환
                        # ---------------------------------

                        for ocr_page in ocr_pages:

                            for ocr_item in ocr_page.items:

                                local_bbox = ocr_item.bbox

                                local_xs = [
                                    point[0]
                                    for point in local_bbox
                                ]

                                local_ys = [
                                    point[1]
                                    for point in local_bbox
                                ]

                                local_x0 = min(
                                    local_xs
                                )
                                local_y0 = min(
                                    local_ys
                                )
                                local_x1 = max(
                                    local_xs
                                )
                                local_y1 = max(
                                    local_ys
                                )

                                # -----------------------------
                                # 이미지 → PDF 좌표 변환
                                # -----------------------------

                                scale_x = (
                                    image_x1 - image_x0
                                ) / image_width

                                scale_y = (
                                    image_y1 - image_y0
                                ) / image_height

                                pdf_x0 = (
                                    image_x0
                                    + local_x0 * scale_x
                                )

                                pdf_y0 = (
                                    image_y0
                                    + local_y0 * scale_y
                                )

                                pdf_x1 = (
                                    image_x0
                                    + local_x1 * scale_x
                                )

                                pdf_y1 = (
                                    image_y0
                                    + local_y1 * scale_y
                                )

                                mapped_bbox = _rect_bbox(
                                    (pdf_x0, pdf_y0, pdf_x1, pdf_y1),
                                    page.rect.width,
                                    page.rect.height,
                                )
                                if not mapped_bbox:
                                    continue

                                page_items.append(
                                    OCRItem(
                                        text=ocr_item.text,
                                        confidence=ocr_item.confidence,
                                        bbox=mapped_bbox,
                                    )
                                )

                    finally:

                        image_path.unlink(
                            missing_ok=True
                        )

            # =================================
            # Reading Order
            # =================================

            page_items.sort(
                key=lambda item: (
                    min(
                        point[1]
                        for point in item.bbox
                    ),
                    min(
                        point[0]
                        for point in item.bbox
                    ),
                )
            )

            # =================================
            # 최종 페이지 텍스트
            # =================================

            page_text = "\n".join(
                item.text
                for item in page_items
            )

            pages.append(
                OCRPage(
                    page=page_number,
                    text=page_text.strip(),
                    items=page_items,
                )
            )

        return pages

    finally:
        document.close()
