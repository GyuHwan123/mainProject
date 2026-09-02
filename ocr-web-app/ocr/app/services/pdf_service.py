import math
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable

import fitz

from app.schemas.ocr import OCRItem, OCRPage, OCRTable


HEADER_FOOTER_MARGIN_RATIO = 0.10


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


def _normalized_repeated_text(text: str) -> str:
    """Normalize marginal text while allowing page numbers to vary."""
    return " ".join("#" if token.isdigit() else token.casefold() for token in text.split())


def _remove_repeated_headers_and_footers(pages: list[OCRPage], page_heights: list[float]) -> None:
    """Remove text repeated in the top/bottom margin on most document pages."""
    if len(pages) < 2:
        return

    occurrences: dict[str, set[int]] = {}
    for page_index, (page, height) in enumerate(zip(pages, page_heights)):
        for item in page.items:
            ys = [point[1] for point in item.bbox]
            if not ys:
                continue
            top, bottom = min(ys), max(ys)
            if top > height * HEADER_FOOTER_MARGIN_RATIO and bottom < height * (1 - HEADER_FOOTER_MARGIN_RATIO):
                continue
            key = _normalized_repeated_text(item.text.strip())
            if key:
                occurrences.setdefault(key, set()).add(page_index)

    minimum_pages = max(2, math.ceil(len(pages) * 0.6))
    repeated = {key for key, indexes in occurrences.items() if len(indexes) >= minimum_pages}
    if not repeated:
        return

    for page, height in zip(pages, page_heights):
        kept = []
        for item in page.items:
            ys = [point[1] for point in item.bbox]
            in_margin = bool(ys) and (min(ys) <= height * HEADER_FOOTER_MARGIN_RATIO or max(ys) >= height * (1 - HEADER_FOOTER_MARGIN_RATIO))
            if in_margin and _normalized_repeated_text(item.text.strip()) in repeated:
                continue
            kept.append(item)
        page.items = kept
        page.text = "\n".join(item.text for item in kept).strip()


def _point_in_rect(item: OCRItem, rect: tuple[float, float, float, float]) -> bool:
    xs = [point[0] for point in item.bbox]
    ys = [point[1] for point in item.bbox]
    if not xs or not ys:
        return False
    center_x, center_y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    return rect[0] <= center_x <= rect[2] and rect[1] <= center_y <= rect[3]


def _extract_tables(page) -> tuple[list[OCRTable], list[OCRItem], list[tuple[float, float, float, float]]]:
    """Extract native PDF tables as explicit rows, columns and cell items."""
    tables: list[OCRTable] = []
    cell_items: list[OCRItem] = []
    table_rects: list[tuple[float, float, float, float]] = []
    try:
        found = page.find_tables()
    except (AttributeError, TypeError, ValueError):
        return tables, cell_items, table_rects

    for detected in getattr(found, "tables", []):
        raw_rows = detected.extract() or []
        rows = [[str(cell or "").strip() for cell in row] for row in raw_rows]
        rows = [row for row in rows if any(row)]
        rect = tuple(float(value) for value in detected.bbox)
        bbox = _rect_bbox(rect, page.rect.width, page.rect.height)
        if not rows or not bbox:
            continue
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        columns = rows[0] if len(rows) > 1 and any(rows[0]) else None
        tables.append(OCRTable(
            bbox=bbox, confidence=1.0, rows=rows, columns=columns,
            row_count=len(rows), column_count=width,
        ))
        table_rects.append(rect)

        # PyMuPDF exposes exact cell rectangles for native tables. Preserve
        # those coordinates so RAG evidence can highlight a specific cell.
        cells = list(getattr(detected, "cells", []) or [])
        for row_index, row in enumerate(rows, start=1):
            for column_index, value in enumerate(row, start=1):
                if not value:
                    continue
                flat_index = (row_index - 1) * width + column_index - 1
                cell_rect = tuple(cells[flat_index]) if flat_index < len(cells) and cells[flat_index] else rect
                cell_bbox = _rect_bbox(cell_rect, page.rect.width, page.rect.height) or bbox
                cell_items.append(OCRItem(
                    text=value, confidence=1.0, bbox=cell_bbox,
                    cell=f"R{row_index}C{column_index}", row=row_index, column=column_index,
                ))
    return tables, cell_items, table_rects


def _native_page_items(page) -> tuple[list[OCRItem], list[OCRTable]]:
    tables, cell_items, table_rects = _extract_tables(page)
    items: list[OCRItem] = []
    for word in page.get_text("words"):
        if len(word) < 5:
            continue
        text = str(word[4]).strip()
        bbox = _rect_bbox(tuple(word[:4]), page.rect.width, page.rect.height)
        candidate = OCRItem(text=text, confidence=1.0, bbox=bbox) if text and bbox else None
        if candidate and not any(_point_in_rect(candidate, rect) for rect in table_rects):
            items.append(candidate)
    return items + cell_items, tables


def _reading_order(item: OCRItem) -> tuple[int, int]:
    return min(point[1] for point in item.bbox), min(point[0] for point in item.bbox)


def _sort_document_items(items: list[OCRItem]) -> None:
    """Order a conventional two-column document left column before right."""
    if len(items) < 10:
        items.sort(key=_reading_order)
        return
    min_x = min(point[0] for item in items for point in item.bbox)
    max_x = max(point[0] for item in items for point in item.bbox)
    midpoint = (min_x + max_x) / 2
    gutter = max((max_x - min_x) * 0.025, 8)
    left = [item for item in items if max(point[0] for point in item.bbox) < midpoint - gutter]
    right = [item for item in items if min(point[0] for point in item.bbox) > midpoint + gutter]
    if len(left) < 4 or len(right) < 4:
        items.sort(key=_reading_order)
        return
    spanning = [item for item in items if item not in left and item not in right]
    column_top = min(_reading_order(item)[0] for item in left + right)
    header = [item for item in spanning if _reading_order(item)[0] < column_top]
    body_spanning = [item for item in spanning if item not in header]
    items[:] = (
        sorted(header, key=_reading_order)
        + sorted(left, key=_reading_order)
        + sorted(right, key=_reading_order)
        + sorted(body_spanning, key=_reading_order)
    )


def extract_pdf_text(
    file_path: Path,
    *,
    document_layout: bool = True,
) -> list[OCRPage]:

    pages = []
    page_heights: list[float] = []

    document = fitz.open(file_path)

    try:
        for page_index, page in enumerate(document):
            if not document_layout:
                pages.append(OCRPage(page=page_index + 1, text=page.get_text("text").strip(), items=[]))
                continue
            items, tables = _native_page_items(page)
            _sort_document_items(items)

            pages.append(
                OCRPage(
                    page=page_index + 1,
                    text="\n".join(item.text for item in items).strip(),
                    items=items,
                    tables=tables or None,
                )
            )
            page_heights.append(page.rect.height)

    finally:
        document.close()

    if document_layout:
        _remove_repeated_headers_and_footers(pages, page_heights)
    return pages


def extract_pdf_text_and_images(
    file_path: Path,
    ocr_runner: Callable[[Path], list[OCRPage]],
    *,
    document_layout: bool = True,
) -> list[OCRPage]:
    """
    텍스트와 이미지가 함께 있는 PDF를 처리한다.

    PDF native text는 PyMuPDF로 추출하고,
    PDF 내부 이미지는 추출한 뒤 PaddleOCR을 실행한다.

    이미지 OCR 결과의 bbox를 PDF 페이지 좌표로 변환한 뒤
    native text와 함께 위치 기준으로 정렬한다.
    """

    pages = []
    page_heights: list[float] = []

    document = fitz.open(file_path)

    try:

        for page_number, page in enumerate(
            document,
            start=1,
        ):

            if document_layout:
                page_items, page_tables = _native_page_items(page)
            else:
                page_items = []
                page_tables = []
                for word in page.get_text("words"):
                    if len(word) < 5:
                        continue
                    text = str(word[4]).strip()
                    bbox = _rect_bbox(tuple(word[:4]), page.rect.width, page.rect.height)
                    if text and bbox:
                        page_items.append(OCRItem(text=text, confidence=1.0, bbox=bbox))

            # Word boxes are materially tighter than line boxes for converted
            # DOCX files, especially for tables, tab stops and mixed fonts.
            # The trailing tuple fields are block, line and word indices.
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

            if document_layout:
                _sort_document_items(page_items)
            else:
                page_items.sort(key=_reading_order)

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
                    tables=page_tables or None,
                )
            )
            page_heights.append(page.rect.height)

        if document_layout:
            _remove_repeated_headers_and_footers(pages, page_heights)
        return pages

    finally:
        document.close()
