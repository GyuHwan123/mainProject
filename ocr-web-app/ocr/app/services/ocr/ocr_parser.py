from app.schemas.ocr import OCRItem, OCRPage


def sort_text_lines(texts, scores, boxes):
    """
    OCR 결과를
    위쪽 → 아래쪽,
    같은 줄에서는 왼쪽 → 오른쪽
    순서로 정렬한다.
    """

    elements = []

    for text, score, box in zip(texts, scores, boxes):
        if not text or not text.strip():
            continue

        x1, y1, x2, y2 = map(int, box)

        height = y2 - y1
        center_y = (y1 + y2) / 2

        elements.append(
            {
                "text": text.strip(),
                "score": float(score),
                "box": [x1, y1, x2, y2],
                "center_y": center_y,
                "height": height,
            }
        )

    # 위 → 아래
    elements.sort(
        key=lambda item: item["center_y"]
    )

    lines = []

    for item in elements:
        matched_line = None

        for line in lines:
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

            if abs(
                item["center_y"] - line_center_y
            ) <= threshold:
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

    # 줄 → 위 → 아래
    lines.sort(
        key=lambda line: min(
            item["center_y"]
            for item in line
        )
    )

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

    # confidence 0.5 이하만 출력
    low_confidence_items = [
        item
        for line in lines
        for item in line
        if item["score"] <= 0.5
    ]

    if low_confidence_items:

        print(
            f"\n[페이지 {page_number}] "
            "confidence 0.5 이하"
        )

        for item in low_confidence_items:

            print(
                f"텍스트: {item['text']!r}, "
                f"confidence: "
                f"{item['score']:.3f}"
            )

    return OCRPage(
        page=page_number,
        text=page_text,
        items=items,
    )