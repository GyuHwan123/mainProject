from __future__ import annotations

import re
from statistics import median

from app.schemas.ocr import OCRItem, OCRTable


NUMBER_PATTERN = re.compile(r"\d[\d,.]*")
SUMMARY_PATTERN = re.compile(
    r"(?:합계|소계|결제|승인|공급가액|부가세|할인|쿠폰|적립|"
    r"거스름|카드\s*번호|사업자\s*번호|총\s*품목|총\s*수량|총\s*구매)", re.IGNORECASE,
)
HEADER_PATTERN = re.compile(r"^(?:품명|상품명|수량|단가|금액|합계)(?:\s|$)", re.IGNORECASE)


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


def detect_receipt_tables(items: list[OCRItem]) -> list[OCRTable]:
    """Build borderless item rows without rewriting canonical OCR text."""
    if len(items) < 4:
        return []

    lines = _group_lines(items)
    heights = [max(_box(item)[3] - _box(item)[1], 1) for item in items]
    typical_height = median(heights)
    candidates: list[tuple[int, list[OCRItem]]] = []
    for index, line in enumerate(lines):
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
        if len(group) < 2:
            continue
        anchors: list[float] = []
        tolerance = typical_height * 2.2
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
        previous_index = max(-1, group[0][0] - 4)
        for line_index, line in group:
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
            rows.append(row)
            previous_index = line_index

        table_items = [item for _, line in group for item in line]
        x1 = min(_box(item)[0] for item in table_items)
        y1 = min(_box(item)[1] for item in table_items)
        x2 = max(_box(item)[2] for item in table_items)
        y2 = max(_box(item)[3] for item in table_items)
        tables.append(OCRTable(
            bbox=[[round(x1), round(y1)], [round(x2), round(y2)]],
            confidence=round(sum(confidences) / len(confidences), 4), rows=rows,
        ))
    return tables
