"""Compact CORD/JSON evaluator adapted from the receipt prototype."""

import json
import re
from collections import Counter
from typing import Any

from app.schemas.ocr import OCRResponse


def _clean(value: str) -> str:
    return re.sub(r"[^\w]", "", value.casefold(), flags=re.UNICODE)


def _distance(reference: str, hypothesis: str) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, expected in enumerate(reference, 1):
        current = [row]
        for column, actual in enumerate(hypothesis, 1):
            current.append(min(previous[column] + 1, current[-1] + 1,
                               previous[column - 1] + (expected != actual)))
        previous = current
    return previous[-1]


def _words(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get("valid_line"), list):
        return [word for line in value["valid_line"] if isinstance(line, dict)
                for word in line.get("words", []) if isinstance(word, dict) and isinstance(word.get("text"), str)]
    if isinstance(value, dict):
        result = []
        for key, child in value.items():
            if key.casefold() not in {"bbox", "box", "quad", "polygon", "points", "meta", "image_size"}:
                result.extend(_words(child))
        return result
    if isinstance(value, list):
        return [word for child in value for word in _words(child)]
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return [{"text": str(value)}]
    return []


def _box(quad: Any) -> list[float] | None:
    if isinstance(quad, dict):
        points = [(quad.get(f"x{i}"), quad.get(f"y{i}")) for i in range(1, 5)]
        if all(isinstance(number, (int, float)) for point in points for number in point):
            xs, ys = zip(*points)
            return [min(xs), min(ys), max(xs), max(ys)]
    return None


def _coverage(reference: list[float], detected: list[float]) -> float:
    left, top = max(reference[0], detected[0]), max(reference[1], detected[1])
    right, bottom = min(reference[2], detected[2]), min(reference[3], detected[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    area = max(0, reference[2] - reference[0]) * max(0, reference[3] - reference[1])
    return intersection / area if area else 0.0


def evaluate_receipt(ocr_result: OCRResponse, ground_truth: str) -> dict[str, Any]:
    parsed = json.loads(ground_truth)
    expected_words = _words(parsed.get("gt_parse", parsed) if isinstance(parsed, dict) else parsed)
    detected = [(item.text, [float(item.bbox[0][0]), float(item.bbox[0][1]),
                             float(item.bbox[1][0]), float(item.bbox[1][1])])
                for page in ocr_result.pages for item in page.items]
    expected = [_clean(word["text"]) for word in expected_words if _clean(word["text"])]
    actual = [_clean(text) for text, _ in detected if _clean(text)]
    matched = sum((Counter(expected) & Counter(actual)).values())
    similarities = []
    missing = []
    for word, cleaned in zip(expected_words, expected):
        candidates = [max(0.0, 1 - _distance(cleaned, candidate) / max(len(cleaned), len(candidate), 1))
                      for candidate in actual]
        best = max(candidates, default=0.0)
        similarities.append(best)
        if best < 1:
            missing.append(word["text"])
    coverages = []
    for word in expected_words:
        reference = _box(word.get("quad"))
        if not reference:
            continue
        candidates = [box for text, box in detected if _clean(word["text"]) in _clean(text)]
        coverages.append(max((_coverage(reference, box) for box in candidates), default=0.0))
    return {
        "summary": {
            "character_accuracy": round(sum(similarities) / max(len(similarities), 1) * 100, 2),
            "word_accuracy": round(matched / max(len(expected), 1) * 100, 2),
            "matched_words": matched,
            "expected_words": len(expected),
            "detected_words": len(actual),
        },
        "text": {"missing_words": missing[:10]},
        "position": {
            "available": bool(coverages),
            "mean_box_coverage": round(sum(coverages) / len(coverages), 4) if coverages else 0.0,
            "coverage_0_5_accuracy": round(sum(value >= .5 for value in coverages) / len(coverages), 4) if coverages else 0.0,
        },
    }
