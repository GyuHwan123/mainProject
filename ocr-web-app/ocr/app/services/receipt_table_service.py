from __future__ import annotations

import re
from statistics import median

from app.schemas.ocr import OCRItem, OCRTable


NUMBER_PATTERN = re.compile(r"(?:\d[\d,.]*|[₩￦])")


def _box(item: OCRItem) -> tuple[float, float, float, float]:
    return item.bbox[0][0], item.bbox[0][1], item.bbox[1][0], item.bbox[1][1]


def _group_lines(items: list[OCRItem]) -> list[list[OCRItem]]:
    ordered = sorted(items, key=lambda item: ((_box(item)[1] + _box(item)[3]) / 2, _box(item)[0]))
    lines: list[list[OCRItem]] = []
    for item in ordered:
        _, y1, _, y2 = _box(item)
        center = (y1 + y2) / 2
        height = max(y2 - y1, 1)
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


def detect_receipt_tables(items: list[OCRItem]) -> list[OCRTable]:
    """Build conservative, borderless table hints from receipt OCR geometry.

    This is intentionally called only by receipt-mode image OCR. It does not
    rewrite OCR text; an uncertain layout therefore cannot damage the canonical
    extraction used by normal image documents.
    """
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
        if right - left < typical_height * 5:
            continue
        candidates.append((index, line))

    groups: list[list[list[OCRItem]]] = []
    previous_candidate_index: int | None = None
    for line_index, line in candidates:
        if groups and previous_candidate_index is not None:
            previous = groups[-1][-1]
            previous_bottom = max(_box(item)[3] for item in previous)
            current_top = min(_box(item)[1] for item in line)
            if line_index - previous_candidate_index <= 2 and current_top - previous_bottom <= typical_height * 3.5:
                groups[-1].append(line)
                previous_candidate_index = line_index
                continue
        groups.append([line])
        previous_candidate_index = line_index

    tables: list[OCRTable] = []
    for group in groups:
        if len(group) < 2:
            continue

        anchors: list[float] = []
        tolerance = typical_height * 2.2
        for line in group:
            for item in line:
                x1, _, _, _ = _box(item)
                anchor = next((value for value in anchors if abs(value - x1) <= tolerance), None)
                if anchor is None:
                    anchors.append(x1)
        anchors.sort()
        if len(anchors) < 2 or len(anchors) > 8:
            continue

        rows: list[list[str]] = []
        confidences: list[float] = []
        for line in group:
            row = [""] * len(anchors)
            for item in line:
                x1, _, _, _ = _box(item)
                column = min(range(len(anchors)), key=lambda idx: abs(anchors[idx] - x1))
                row[column] = " ".join(value for value in (row[column], item.text) if value)
                confidences.append(item.confidence)
            rows.append(row)

        x1 = min(_box(item)[0] for line in group for item in line)
        y1 = min(_box(item)[1] for line in group for item in line)
        x2 = max(_box(item)[2] for line in group for item in line)
        y2 = max(_box(item)[3] for line in group for item in line)
        tables.append(OCRTable(
            bbox=[[round(x1), round(y1)], [round(x2), round(y2)]],
            confidence=round(sum(confidences) / len(confidences), 4),
            rows=rows,
        ))
    return tables
