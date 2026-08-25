from __future__ import annotations

import re
from statistics import median

from app.schemas.ocr import OCRItem, OCRRegion, OCRTable


NUMBER_PATTERN = re.compile(r"\d[\d,.]*")
SUMMARY_PATTERN = re.compile(
    r"(?:합계|소계|결제|승인|공급가액|부가세|할인|쿠폰|적립|"
    r"거스름|카드\s*번호|사업자\s*번호|총\s*품목|총\s*수량|총\s*구매)", re.IGNORECASE,
)
HEADER_PATTERN = re.compile(r"^(?:품명|상품명|수량|단가|금액|합계)(?:\s|$)", re.IGNORECASE)
ITEM_HEADER_PATTERN = re.compile(r"(?:품명|상품명|상품|품목)", re.IGNORECASE)
QUANTITY_HEADER_PATTERN = re.compile(r"(?:수량|qty)", re.IGNORECASE)
UNIT_PRICE_HEADER_PATTERN = re.compile(r"(?:단가|판매가)", re.IGNORECASE)
AMOUNT_HEADER_PATTERN = re.compile(r"(?:금액|합계)", re.IGNORECASE)


def _box(item: OCRItem) -> tuple[float, float, float, float]:
    return item.bbox[0][0], item.bbox[0][1], item.bbox[1][0], item.bbox[1][1]


def _group_lines(items: list[OCRItem]) -> list[list[OCRItem]]:
    ordered = sorted(items, key=lambda item: ((_box(item)[1] + _box(item)[3]) / 2, _box(item)[0]))
    lines: list[list[OCRItem]] = []
    for item in ordered:
        _, y1, _, y2 = _box(item)
        center, height = (y1 + y2) / 2, max(y2 - y1, 1)
        match = None
        for line in reversed(lines[-3:]):
            centers = [(_box(value)[1] + _box(value)[3]) / 2 for value in line]
            heights = [max(_box(value)[3] - _box(value)[1], 1) for value in line]
            if abs(center - sum(centers) / len(centers)) <= max(height, median(heights)) * 0.55:
                match = line
                break
        if match is None:
            lines.append([item])
        else:
            match.append(item)
    for line in lines:
        line.sort(key=lambda item: _box(item)[0])
    return lines


def _anchor_x(item: OCRItem) -> float:
    x1, _, x2, _ = _box(item)
    compact = item.text.replace(" ", "")
    return x2 if NUMBER_PATTERN.fullmatch(compact) else x1


def _continuation_text(line: list[OCRItem]) -> str | None:
    text = " ".join(item.text.strip() for item in line if item.text.strip()).strip()
    if not text or SUMMARY_PATTERN.search(text) or HEADER_PATTERN.search(text):
        return None
    return text if re.search(r"[A-Za-z가-힣]", text) else None


def _line_text(line: list[OCRItem]) -> str:
    return " ".join(item.text.strip() for item in line if item.text.strip()).strip()


def _item_region(lines: list[list[OCRItem]], typical_height: float) -> tuple[list[tuple[int, list[OCRItem]]], dict[str, float], int | None]:
    """Separate the item body from merchant/header and payment/summary areas."""
    header_index = None
    header_columns: dict[str, float] = {}
    for index, line in enumerate(lines):
        text = _line_text(line)
        has_name = bool(ITEM_HEADER_PATTERN.search(text))
        has_numeric_header = sum(bool(pattern.search(text)) for pattern in (
            QUANTITY_HEADER_PATTERN, UNIT_PRICE_HEADER_PATTERN, AMOUNT_HEADER_PATTERN,
        ))
        if has_name and has_numeric_header:
            header_index = index
            for item in line:
                center = (_box(item)[0] + _box(item)[2]) / 2
                compact = item.text.replace(" ", "")
                if ITEM_HEADER_PATTERN.search(compact):
                    header_columns.setdefault("name", center)
                elif QUANTITY_HEADER_PATTERN.search(compact):
                    header_columns.setdefault("quantity", center)
                elif UNIT_PRICE_HEADER_PATTERN.search(compact):
                    header_columns.setdefault("unit_price", center)
                elif AMOUNT_HEADER_PATTERN.search(compact):
                    header_columns.setdefault("amount", center)
            break

    start = header_index + 1 if header_index is not None else 0
    end = len(lines)
    consecutive_summary = 0
    for index in range(start, len(lines)):
        text = _line_text(lines[index])
        is_summary = bool(SUMMARY_PATTERN.search(text))
        consecutive_summary = consecutive_summary + 1 if is_summary else 0
        # One explicit total/payment line is already a reliable end marker.
        if is_summary and (re.search(r"합계|결제|승인|총\s*품목|총\s*수량|총\s*구매", text) or consecutive_summary >= 2):
            end = index
            break

    region = [(index, line) for index, line in enumerate(lines[start:end], start=start)]
    # If a detected header is followed by an implausibly large blank gap, do
    # not let unrelated lower-page content become an item row.
    if region and header_index is not None:
        header_bottom = max(_box(item)[3] for item in lines[header_index])
        first_top = min(_box(item)[1] for item in region[0][1])
        if first_top - header_bottom > typical_height * 8:
            return [], header_columns, header_index
    return region, header_columns, header_index


def detect_receipt_regions(items: list[OCRItem]) -> list[OCRRegion]:
    """Expose item boundaries so downstream code does not rescan the page."""
    if not items:
        return []
    lines = _group_lines(items)
    typical_height = median(max(_box(item)[3] - _box(item)[1], 1) for item in items)
    region, header_columns, header_index = _item_region(lines, typical_height)
    if not region:
        return []
    region_items = [item for _, line in region for item in line]
    if header_index is not None:
        region_items.extend(lines[header_index])
    x1 = min(_box(item)[0] for item in region_items)
    y1 = min(_box(item)[1] for item in region_items)
    x2 = max(_box(item)[2] for item in region_items)
    y2 = max(_box(item)[3] for item in region_items)
    confidence = .95 if len(header_columns) >= 2 else .7
    return [OCRRegion(type="items", bbox=[[round(x1), round(y1)], [round(x2), round(y2)]], confidence=confidence)]


def detect_receipt_tables(items: list[OCRItem]) -> list[OCRTable]:
    """Build borderless item rows without rewriting canonical OCR text."""
    if len(items) < 4:
        return []

    lines = _group_lines(items)
    heights = [max(_box(item)[3] - _box(item)[1], 1) for item in items]
    typical_height = median(heights)
    region, header_columns, _ = _item_region(lines, typical_height)
    candidates: list[tuple[int, list[OCRItem]]] = []
    for index, line in region:
        if len(line) < 2 or not any(NUMBER_PATTERN.search(item.text) for item in line):
            continue
        left = min(_box(item)[0] for item in line)
        right = max(_box(item)[2] for item in line)
        if right - left >= typical_height * 5:
            candidates.append((index, line))

    groups: list[list[tuple[int, list[OCRItem]]]] = []
    for line_index, line in candidates:
        if groups:
            previous_index, previous = groups[-1][-1]
            previous_bottom = max(_box(item)[3] for item in previous)
            current_top = min(_box(item)[1] for item in line)
            if line_index - previous_index <= 4 and current_top - previous_bottom <= typical_height * 5.5:
                groups[-1].append((line_index, line))
                continue
        groups.append([(line_index, line)])

    tables: list[OCRTable] = []
    for group in groups:
        # A header-bounded region can legitimately contain one purchased item.
        # Without a header, retain the old two-row minimum to avoid turning a
        # merchant key/value line into a table.
        if len(group) < 2 and len(header_columns) < 2:
            continue
        anchors: list[float] = []
        tolerance = typical_height * 2.2
        columns: list[str] | None = None
        if len(header_columns) >= 2:
            ordered_columns = sorted(header_columns.items(), key=lambda pair: pair[1])
            columns = [key for key, _ in ordered_columns]
            anchors = [position for _, position in ordered_columns]
        else:
            for _, line in group:
                for item in line:
                    position = _anchor_x(item)
                    if not any(abs(value - position) <= tolerance for value in anchors):
                        anchors.append(position)
            anchors.sort()
        if len(anchors) < 2 or len(anchors) > 8:
            continue

        rows: list[list[str]] = []
        confidences: list[float] = []
        supplemental_items: list[OCRItem] = []
        previous_index = max(-1, group[0][0] - 4)
        for group_index, (line_index, line) in enumerate(group):
            row = [""] * len(anchors)
            for item in line:
                position = _anchor_x(item)
                column = min(range(len(anchors)), key=lambda idx: abs(anchors[idx] - position))
                row[column] = " ".join(value for value in (row[column], item.text) if value)
                confidences.append(item.confidence)
            continuations = [
                text for preceding in lines[max(previous_index + 1, line_index - 3):line_index]
                if (text := _continuation_text(preceding))
            ]
            if continuations:
                row[0] = " ".join([*continuations, row[0]]).strip()
            # Receipts often print a member/discount price in parentheses on a
            # short line immediately below the primary price. Preserve it in
            # the same item block instead of dropping it for having one box.
            next_index = group[group_index + 1][0] if group_index + 1 < len(group) else min(len(lines), line_index + 3)
            trailing = []
            for following in lines[line_index + 1:next_index]:
                text = _line_text(following)
                if re.fullmatch(r"[\(（]\s*\d[\d,.]*\s*[\)）]", text):
                    trailing.append(text)
                    supplemental_items.extend(following)
                    confidences.extend(item.confidence for item in following)
            if trailing:
                row[-1] = " ".join([row[-1], *trailing]).strip()
            rows.append(row)
            previous_index = line_index

        table_items = [item for _, line in group for item in line] + supplemental_items
        x1 = min(_box(item)[0] for item in table_items)
        y1 = min(_box(item)[1] for item in table_items)
        x2 = max(_box(item)[2] for item in table_items)
        y2 = max(_box(item)[3] for item in table_items)
        tables.append(OCRTable(
            bbox=[[round(x1), round(y1)], [round(x2), round(y2)]],
            confidence=round(sum(confidences) / len(confidences), 4), rows=rows, columns=columns,
        ))
    return tables
