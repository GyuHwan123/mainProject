from app.schemas.ocr import OCRItem, OCRPage
from app.services.postprocess_service import normalize_ocr_text


def sort_text_lines(texts, scores, boxes):
    """
    OCR 결과를
    위쪽 → 아래쪽,
    같은 줄에서는 왼쪽 → 오른쪽
    순서로 정렬한다.
    """

    elements = []

    for text, score, box in zip(texts, scores, boxes):
        text = normalize_ocr_text(text)
        if not text:
            continue

        x1, y1, x2, y2 = map(int, box)

        height = y2 - y1
        center_y = (y1 + y2) / 2

        elements.append(
            {
                "text": text,
                "score": float(score),
                "box": [x1, y1, x2, y2],
                "center_y": center_y,
                "height": height,
            }
        )

    page_width = (
        max((item["box"][2] for item in elements), default=0)
        - min((item["box"][0] for item in elements), default=0)
    )
    page_left = min((item["box"][0] for item in elements), default=0)
    midpoint = page_left + page_width / 2
    center_band = page_width * 0.006
    clear_left = [item for item in elements if item["box"][2] < midpoint - center_band]
    clear_right = [item for item in elements if item["box"][0] > midpoint + center_band]
    has_two_columns = len(clear_left) >= 5 and len(clear_right) >= 5

    if has_two_columns:
        for item in elements:
            x1, _, x2, _ = item["box"]
            if x1 < midpoint - center_band and x2 > midpoint + center_band:
                item["column"] = "spanning"
            else:
                item["column"] = "left" if (x1 + x2) / 2 < midpoint else "right"

    # 위 → 아래
    elements.sort(
        key=lambda item: item["center_y"]
    )

    lines = []

    for item in elements:
        matched_line = None

        for line in lines:
            if has_two_columns and line[0].get("column") != item.get("column"):
                continue

            line_center_y = sum(
                element["center_y"]
                for element in line
            ) / len(line)

            line_height = sum(
                element["height"]
                for element in line
            ) / len(line)

            threshold = max(
                10,
                line_height * 0.5,
            )

            line_left = min(element["box"][0] for element in line)
            line_right = max(element["box"][2] for element in line)
            horizontal_gap = max(
                line_left - item["box"][2],
                item["box"][0] - line_right,
                0,
            )
            max_word_gap = max(24, line_height * 3.5, page_width * 0.055)

            if abs(
                item["center_y"] - line_center_y
            ) <= threshold and horizontal_gap <= max_word_gap:
                matched_line = line
                break

        if matched_line is None:
            lines.append([item])
        else:
            matched_line.append(item)

    # 같은 줄 → 왼쪽 → 오른쪽
    for line in lines:
        line.sort(
            key=lambda item: item["box"][0]
        )

    by_top = lambda line: min(item["center_y"] for item in line)
    lines.sort(key=by_top)

    # Two-column documents are read down the full left column first, then the
    # right column. Center-spanning titles and tables keep their page order.
    if len(lines) >= 4:
        min_x = min(item["box"][0] for line in lines for item in line)
        max_x = max(item["box"][2] for line in lines for item in line)
        midpoint = (min_x + max_x) / 2
        gutter = max((max_x - min_x) * 0.04, 10)
        left = [line for line in lines if max(item["box"][2] for item in line) < midpoint + gutter]
        right = [line for line in lines if min(item["box"][0] for item in line) > midpoint - gutter]

        if len(left) >= 2 and len(right) >= 2:
            spanning = [line for line in lines if line not in left and line not in right]
            column_top = min(by_top(line) for line in left + right)
            header = [line for line in spanning if by_top(line) < column_top]
            body_spanning = [line for line in spanning if line not in header]
            return sorted(header, key=by_top) + sorted(left, key=by_top) + sorted(right, key=by_top) + sorted(body_spanning, key=by_top)

    return lines


def build_ocr_page(
    data: dict,
    page_number: int,
) -> OCRPage:

    texts = data.get("rec_texts", [])
    scores = data.get("rec_scores", [])
    boxes = data.get("rec_boxes", [])

    lines = sort_text_lines(
        texts,
        scores,
        boxes,
    )

    items = []

    for line in lines:

        for item in line:

            items.append(
                OCRItem(
                    text=item["text"],
                    confidence=item["score"],
                    bbox=[
                        [
                            item["box"][0],
                            item["box"][1],
                        ],
                        [
                            item["box"][2],
                            item["box"][3],
                        ],
                    ],
                )
            )

    page_lines = []

    for line in lines:

        line_text = " ".join(
            item["text"]
            for item in line
        )

        page_lines.append(
            line_text
        )

    page_text = "\n".join(
        page_lines
    )

    return OCRPage(
        page=page_number,
        text=page_text,
        items=items,
    )
