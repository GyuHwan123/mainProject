from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.schemas.ocr import OCRItem, OCRPage, OCRTable


@dataclass(frozen=True)
class _Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


def _item_rect(item: OCRItem) -> _Rect:
    xs = [int(point[0]) for point in item.bbox]
    ys = [int(point[1]) for point in item.bbox]
    return _Rect(min(xs), min(ys), max(xs), max(ys))


def _cluster_positions(values: list[int], tolerance: int) -> list[int]:
    if not values:
        return []
    groups: list[list[int]] = []
    for value in sorted(values):
        if not groups or value - groups[-1][-1] > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [round(sum(group) / len(group)) for group in groups]


def _line_positions(mask: np.ndarray, *, vertical: bool) -> list[int]:
    projection = np.count_nonzero(mask, axis=0 if vertical else 1)
    required = (mask.shape[0] if vertical else mask.shape[1]) * 0.18
    return _cluster_positions(np.flatnonzero(projection >= required).tolist(), tolerance=4)


def _grid_boundaries(image: np.ndarray) -> tuple[list[int], list[int]] | None:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    height, width = binary.shape[:2]
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, width // 30), 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, height // 30)))
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
    xs = _line_positions(vertical, vertical=True)
    ys = _line_positions(horizontal, vertical=False)
    if len(xs) < 2 or len(ys) < 2:
        return None
    return xs, ys


def _horizontal_table_rect(image: np.ndarray) -> _Rect | None:
    """Locate a table that has horizontal rules but no vertical borders."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    height, width = binary.shape[:2]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, width // 18), 1))
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    contours = cv2.findContours(horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    lines = []
    for contour in contours:
        x, y, line_width, line_height = cv2.boundingRect(contour)
        if line_width >= width * 0.18 and line_height <= max(8, height * 0.012):
            lines.append(_Rect(x, y, x + line_width, y + line_height))
    if len(lines) < 3:
        return None

    # Explanatory arrows and underlines tend to be isolated. A table has at
    # least three rules with a substantially overlapping horizontal span.
    best: list[_Rect] = []
    for seed in lines:
        group = []
        for line in lines:
            overlap = max(0, min(seed.right, line.right) - max(seed.left, line.left))
            if overlap >= min(seed.right - seed.left, line.right - line.left) * 0.7:
                group.append(line)
        if len(group) > len(best):
            best = group
    if len(best) < 3:
        return None
    best.sort(key=lambda line: line.top)
    median_gap = float(np.median([right.top - left.top for left, right in zip(best, best[1:])]))
    padding = max(8, round(median_gap * 0.8)) if median_gap > 0 else 12
    # In common report tables the header labels sit just above the first long
    # horizontal rule.  A symmetric sub-row padding drops those labels and
    # makes the first data row look like the header.  Include nearly two row
    # heights above the rule; paragraph/title items remain excluded later
    # because a structured row must occupy at least two column anchors.
    top_padding = max(padding, round(median_gap * 1.8)) if median_gap > 0 else 24
    return _Rect(
        max(0, min(line.left for line in best) - padding),
        max(0, best[0].top - top_padding),
        min(width, max(line.right for line in best) + padding),
        min(height, best[-1].bottom + padding),
    )


def _items_in_rect(items: list[OCRItem], rect: _Rect) -> list[OCRItem]:
    return [
        item for item in items
        if rect.left <= _item_rect(item).center_x <= rect.right
        and rect.top <= _item_rect(item).center_y <= rect.bottom
    ]


def _build_grid_table(items: list[OCRItem], image: np.ndarray) -> OCRTable | None:
    boundaries = _grid_boundaries(image)
    if not boundaries:
        horizontal_rect = _horizontal_table_rect(image)
        if horizontal_rect is None:
            return None
        return _build_borderless_table(
            _items_in_rect(items, horizontal_rect),
            bbox_override=horizontal_rect,
            confidence=0.82,
        )
    xs, ys = boundaries
    table_rect = _Rect(xs[0], ys[0], xs[-1], ys[-1])
    contained = _items_in_rect(items, table_rect)
    if len(contained) < 4:
        return None

    rows = [["" for _ in range(len(xs) - 1)] for _ in range(len(ys) - 1)]
    assigned = 0
    for item in contained:
        rect = _item_rect(item)
        row = next((index for index in range(len(ys) - 1) if ys[index] <= rect.center_y <= ys[index + 1]), None)
        column = next((index for index in range(len(xs) - 1) if xs[index] <= rect.center_x <= xs[index + 1]), None)
        if row is None or column is None:
            continue
        rows[row][column] = " ".join(part for part in (rows[row][column], item.text.strip()) if part)
        item.row, item.column, item.cell = row + 1, column + 1, f"R{row + 1}C{column + 1}"
        assigned += 1
    rows = [row for row in rows if any(row)]
    if assigned < 4 or len(rows) < 2:
        return None
    return OCRTable(
        bbox=[[table_rect.left, table_rect.top], [table_rect.right, table_rect.top],
              [table_rect.right, table_rect.bottom], [table_rect.left, table_rect.bottom]],
        confidence=0.9,
        rows=rows,
        columns=rows[0] if len(rows) > 1 else None,
        row_count=len(rows),
        column_count=max((len(row) for row in rows), default=0),
    )


def _group_rows(items: list[OCRItem]) -> list[list[OCRItem]]:
    ordered = sorted(items, key=lambda item: (_item_rect(item).center_y, _item_rect(item).left))
    heights = [max(1, _item_rect(item).bottom - _item_rect(item).top) for item in ordered]
    tolerance = max(6, int(np.median(heights) * 0.65)) if heights else 6
    rows: list[list[OCRItem]] = []
    for item in ordered:
        center_y = _item_rect(item).center_y
        target = next((row for row in reversed(rows[-3:]) if abs(np.mean([_item_rect(value).center_y for value in row]) - center_y) <= tolerance), None)
        if target is None:
            rows.append([item])
        else:
            target.append(item)
    for row in rows:
        row.sort(key=lambda item: _item_rect(item).left)
    return rows


def _build_borderless_table(
    items: list[OCRItem],
    *,
    bbox_override: _Rect | None = None,
    confidence: float = 0.65,
) -> OCRTable | None:
    """Infer conservative borderless tables from repeated OCR column anchors."""
    rows = [row for row in _group_rows(items) if len(row) >= 2]
    if len(rows) < 3:
        return None
    heights = [max(1, _item_rect(item).bottom - _item_rect(item).top) for row in rows for item in row]
    tolerance = max(12, int(np.median(heights) * 1.8))
    candidate_anchors = _cluster_positions([round(_item_rect(item).center_x) for row in rows for item in row], tolerance)
    # Keep only columns repeated in at least three rows. This discards diagram
    # labels and callout text surrounding an otherwise regular table.
    anchors = [
        anchor for anchor in candidate_anchors
        if sum(any(abs(_item_rect(item).center_x - anchor) <= tolerance for item in row) for row in rows) >= 3
    ]
    # Notes on the right can align with several data rows while still being
    # separated from the real table by a distinctly larger column gap.
    if len(anchors) >= 3:
        gaps = [right - left for left, right in zip(anchors, anchors[1:])]
        typical_gap = float(np.median(gaps))
        anchor_groups: list[list[int]] = [[anchors[0]]]
        for anchor, gap in zip(anchors[1:], gaps):
            if typical_gap > 0 and gap > typical_gap * 1.5:
                anchor_groups.append([anchor])
            else:
                anchor_groups[-1].append(anchor)
        viable_groups = [group for group in anchor_groups if len(group) >= 2]
        if viable_groups:
            anchors = max(
                viable_groups,
                key=lambda group: sum(
                    any(abs(_item_rect(item).center_x - anchor) <= tolerance for item in row)
                    for anchor in group for row in rows
                ),
            )
    if not 2 <= len(anchors) <= 12:
        return None

    structured: list[tuple[list[OCRItem], list[str]]] = []
    for row in rows:
        cells = ["" for _ in anchors]
        used: set[int] = set()
        aligned_items: list[OCRItem] = []
        for item in row:
            column = min(range(len(anchors)), key=lambda index: abs(anchors[index] - _item_rect(item).center_x))
            if abs(anchors[column] - _item_rect(item).center_x) > tolerance:
                continue
            cells[column] = " ".join(part for part in (cells[column], item.text.strip()) if part)
            used.add(column)
            aligned_items.append(item)
        if len(used) >= 2:
            structured.append((aligned_items, cells))
    if len(structured) < 3:
        return None
    covered_columns = {index for _, cells in structured for index, value in enumerate(cells) if value}
    if len(covered_columns) < 2:
        return None

    selected_items = [item for row, _ in structured for item in row]
    for row_index, (row, cells) in enumerate(structured, start=1):
        for item in row:
            column = min(range(len(anchors)), key=lambda index: abs(anchors[index] - _item_rect(item).center_x))
            item.row, item.column, item.cell = row_index, column + 1, f"R{row_index}C{column + 1}"
    rects = [_item_rect(item) for item in selected_items]
    rows_output = [cells for _, cells in structured]
    bounds = bbox_override or _Rect(
        min(rect.left for rect in rects), min(rect.top for rect in rects),
        max(rect.right for rect in rects), max(rect.bottom for rect in rects),
    )
    return OCRTable(
        bbox=[[bounds.left, bounds.top], [bounds.right, bounds.top],
              [bounds.right, bounds.bottom], [bounds.left, bounds.bottom]],
        confidence=confidence,
        rows=rows_output,
        columns=rows_output[0] if len(rows_output) > 1 else None,
        row_count=len(rows_output),
        column_count=max((len(row) for row in rows_output), default=0),
    )


def enhance_document_tables(page: OCRPage, image: np.ndarray | None = None) -> None:
    """Add table structure to document OCR without changing recognized text."""
    if page.tables or len(page.items) < 4:
        return
    table = _build_grid_table(page.items, image) if image is not None else None
    if table is None:
        table = _build_borderless_table(page.items)
    if table is not None:
        page.tables = [table]
