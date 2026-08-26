from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any


ANALYSIS_VERSION = "receipt-error-analysis-v1"
NUMBER_FIELDS = {"quantity", "unit_price", "total_amount", "supply_amount", "tax_amount"}
SUMMARY_ITEM_PATTERN = re.compile(
    r"합계|소계|결제|승인|공급가액|부가세|세액|할인|쿠폰|적립금|거스름돈|총\s*구매",
    re.IGNORECASE,
)
DISCOUNT_PATTERN = re.compile(r"할인|쿠폰|적립금", re.IGNORECASE)
TOTAL_PATTERN = re.compile(r"합계|소계|결제|승인|총\s*구매|공급가액|부가세|세액", re.IGNORECASE)
FIELD_LABEL_PATTERNS = {
    "total_amount": re.compile(r"합계|결제|승인|받을\s*금액|총\s*구매", re.IGNORECASE),
    "supply_amount": re.compile(r"공급가액|공급액", re.IGNORECASE),
    "tax_amount": re.compile(r"부가세|세액", re.IGNORECASE),
    "discount_amount": re.compile(r"할인|쿠폰", re.IGNORECASE),
    "total_quantity": re.compile(r"총\s*수량", re.IGNORECASE),
}


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        cleaned = re.sub(r"[^0-9.+-]", "", str(value).replace(",", ""))
        return round(float(cleaned), 2) if cleaned else None
    except (TypeError, ValueError):
        return None


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())


def _similar(left: Any, right: Any) -> float:
    a, b = _compact(left), _compact(right)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _value_equal(field: str, left: Any, right: Any) -> bool:
    if field in NUMBER_FIELDS:
        a, b = _number(left), _number(right)
        return a is not None and b is not None and abs(a - b) < 0.01
    return _similar(left, right) >= 0.72


def _text_contains(text: str, field: str, value: Any) -> bool:
    if value is None or value == "":
        return False
    if field in NUMBER_FIELDS:
        target = _number(value)
        if target is None:
            return False
        values = [_number(token) for token in re.findall(r"\d[\d,.]*", text)]
        return any(number is not None and abs(number - target) < 0.01 for number in values)
    compact = _compact(value)
    return bool(compact) and compact in _compact(text)


def _candidate_as_item(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": candidate.get("name_candidate"),
        "quantity": candidate.get("quantity_candidate"),
        "unit_price": candidate.get("unit_price_candidate"),
        "total_amount": candidate.get("amount_candidate"),
    }


def _best_match(item: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[int | None, float]:
    best_index, best_score = None, 0.0
    for index, row in enumerate(rows):
        name_score = _similar(item.get("name"), row.get("name"))
        numeric_matches = sum(
            _value_equal(field, item.get(field), row.get(field))
            for field in ("quantity", "unit_price", "total_amount")
            if item.get(field) is not None and row.get(field) is not None
        )
        score = name_score * 3 + numeric_matches
        if score > best_score:
            best_index, best_score = index, score
    return best_index, best_score


def _tag(
    category: str,
    code: str,
    *,
    scope: str = "sample",
    field: str | None = None,
    confidence: float = 1.0,
    message: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    confidence = round(max(0.0, min(float(confidence), 1.0)), 2)
    decision = "AUTO" if confidence >= 0.9 else "NEEDS_REVIEW" if confidence >= 0.6 else "UNKNOWN"
    return {
        "category": category if decision != "UNKNOWN" else "UNKNOWN",
        "code": code if decision != "UNKNOWN" else "UNKNOWN",
        "scope": scope,
        "field": field,
        "confidence": confidence,
        "decision": decision,
        "message": message,
        "evidence": evidence or {},
    }


def _deduplicate(tags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[tuple[Any, ...], dict[str, Any]] = {}
    for tag in tags:
        key = (tag["category"], tag["code"], tag["scope"], tag.get("field"))
        if key not in selected or tag["confidence"] > selected[key]["confidence"]:
            selected[key] = tag
    return list(selected.values())


def _evaluation_failed(ground_truth: dict[str, Any], prediction: dict[str, Any]) -> bool:
    for field, expected in ground_truth.items():
        if field == "items":
            continue
        if not _value_equal(field, expected, prediction.get(field)):
            return True
    truth_items = [row for row in ground_truth.get("items") or [] if isinstance(row, dict)]
    final_items = [row for row in prediction.get("items") or [] if isinstance(row, dict)]
    if len(truth_items) != len(final_items):
        return True
    for truth_item in truth_items:
        index, score = _best_match(truth_item, final_items)
        if index is None or score < 2.4:
            return True
        actual = final_items[index]
        if any(not _value_equal(field, expected, actual.get(field)) for field, expected in truth_item.items()):
            return True
    return False


def analyze_finance_evaluation_failure(
    *,
    ocr_text: str,
    ground_truth: dict[str, Any],
    prediction: dict[str, Any],
    pipeline_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attribute extraction failures using preserved evidence without changing scores."""
    trace = pipeline_trace or {}
    candidates = [_candidate_as_item(row) for row in trace.get("item_candidates") or [] if isinstance(row, dict)]
    candidate_raw = [row for row in trace.get("item_candidates") or [] if isinstance(row, dict)]
    model_items = [row for row in trace.get("model_items") or [] if isinstance(row, dict)]
    final_items = [row for row in prediction.get("items") or [] if isinstance(row, dict)]
    truth_items = [row for row in ground_truth.get("items") or [] if isinstance(row, dict)]
    tags: list[dict[str, Any]] = []
    if not _evaluation_failed(ground_truth, prediction):
        return {
            "analysis_version": ANALYSIS_VERSION,
            "status": "SUCCESS",
            "primary_category": None,
            "needs_review": False,
            "error_tags": [],
            "category_counts": {},
        }

    # Summary OCR evidence: absence alone is uncertain because OCR can split tokens.
    for field, expected in ground_truth.items():
        if field == "items" or expected in (None, "", []):
            continue
        actual = prediction.get(field)
        if _value_equal(field, expected, actual):
            continue
        if not _text_contains(ocr_text, field, expected):
            number_with_label = field in NUMBER_FIELDS and FIELD_LABEL_PATTERNS.get(field, re.compile(r"$^" )).search(ocr_text)
            tags.append(_tag(
                "OCR_ERROR", "OCR_NUMBER_ERROR" if number_with_label else "OCR_TEXT_MISSING",
                field=field, confidence=0.9 if number_with_label else 0.72,
                message="정답 숫자가 OCR 라벨 주변에서 정확히 인식되지 않았습니다." if number_with_label else "정답 값이 OCR 원문에서 확인되지 않습니다.",
                evidence={"expected": expected, "actual": actual},
            ))
        elif field in NUMBER_FIELDS:
            tags.append(_tag(
                "LLM_ERROR", "LLM_CHANGED_CORRECT_CANDIDATE", field=field, confidence=0.91,
                message="정답 숫자는 OCR에 있지만 최종 예측값이 다릅니다.",
                evidence={"expected": expected, "actual": actual},
            ))

    matched_model_indexes: set[int] = set()
    matched_final_indexes: set[int] = set()
    for truth_index, truth_item in enumerate(truth_items):
        scope = f"items[{truth_index}]"
        candidate_index, candidate_score = _best_match(truth_item, candidates)
        model_index, model_score = _best_match(truth_item, model_items)
        final_index, final_score = _best_match(truth_item, final_items)
        candidate = candidates[candidate_index] if candidate_index is not None and candidate_score >= 2.4 else None
        model_item = model_items[model_index] if model_index is not None and model_score >= 2.4 else None
        final_item = final_items[final_index] if final_index is not None and final_score >= 2.4 else None
        if model_item is not None:
            matched_model_indexes.add(model_index)
        if final_item is not None:
            matched_final_indexes.add(final_index)

        name_in_ocr = _text_contains(ocr_text, "name", truth_item.get("name"))
        for field in ("quantity", "unit_price", "total_amount"):
            if field in truth_item and name_in_ocr and not _text_contains(ocr_text, field, truth_item.get(field)):
                tags.append(_tag(
                    "OCR_ERROR", "OCR_NUMBER_ERROR", scope=scope, field=field, confidence=0.9,
                    message="품목명은 OCR에 있지만 정답 숫자가 OCR 원문에 없습니다.",
                    evidence={"truth_item": truth_item},
                ))
        if candidate is None:
            confidence = 0.94 if name_in_ocr else 0.5
            tags.append(_tag(
                "CANDIDATE_ERROR", "ITEM_MISSING", scope=scope, confidence=confidence,
                message="정답 품목에 대응하는 코드 후보가 생성되지 않았습니다.",
                evidence={"truth_item": truth_item, "name_found_in_ocr": name_in_ocr},
            ))
        elif model_item is None:
            tags.append(_tag(
                "LLM_ERROR", "ITEM_MISSING", scope=scope, confidence=0.96,
                message="정답과 일치하는 코드 후보가 있지만 LLM 품목 결과에서 누락됐습니다.",
                evidence={"truth_item": truth_item, "candidate_index": candidate_index, "candidate": candidate},
            ))

        comparison_source = model_item or final_item
        if comparison_source is not None:
            for field, code in (
                ("quantity", "QUANTITY_ERROR"),
                ("unit_price", "UNIT_PRICE_ERROR"),
                ("total_amount", "ITEM_AMOUNT_ERROR"),
            ):
                if field not in truth_item or _value_equal(field, truth_item.get(field), comparison_source.get(field)):
                    continue
                category = "LLM_ERROR"
                confidence = 0.94 if candidate and _value_equal(field, truth_item.get(field), candidate.get(field)) else 0.7
                tags.append(_tag(
                    category, code, scope=scope, field=field, confidence=confidence,
                    message=f"품목의 {field} 값이 정답과 다릅니다.",
                    evidence={"expected": truth_item.get(field), "actual": comparison_source.get(field), "candidate": candidate},
                ))

        if candidate and model_item:
            changed = [
                field for field in ("name", "quantity", "unit_price", "total_amount")
                if _value_equal(field, truth_item.get(field), candidate.get(field))
                and not _value_equal(field, candidate.get(field), model_item.get(field))
            ]
            if changed:
                tags.append(_tag(
                    "LLM_ERROR", "LLM_CHANGED_CORRECT_CANDIDATE", scope=scope, confidence=0.97,
                    message="LLM이 정답과 일치하던 코드 후보 값을 변경했습니다.",
                    evidence={"changed_fields": changed, "candidate": candidate, "model_item": model_item},
                ))

    for index, item in enumerate(model_items):
        if index in matched_model_indexes:
            continue
        name = str(item.get("name") or "")
        if DISCOUNT_PATTERN.search(name):
            code = "DISCOUNT_AS_ITEM"
        elif TOTAL_PATTERN.search(name):
            code = "TOTAL_ROW_AS_ITEM"
        elif not any(_text_contains(ocr_text, field, item.get(field)) for field in ("name", "total_amount")):
            code = "LLM_HALLUCINATION"
        else:
            code = "EXTRA_ITEM"
        tags.append(_tag(
            "LLM_ERROR", code, scope=f"model_items[{index}]", confidence=0.94,
            message="정답 품목과 매칭되지 않는 LLM 품목이 있습니다.", evidence={"model_item": item},
        ))

    model_name_counts = Counter(_compact(item.get("name")) for item in model_items if _compact(item.get("name")))
    for name, count in model_name_counts.items():
        if count > 1:
            tags.append(_tag(
                "LLM_ERROR", "DUPLICATE_ITEM", scope="items", confidence=0.91,
                message="동일한 품목명이 LLM 결과에 중복됐습니다.", evidence={"normalized_name": name, "count": count},
            ))

    for index, raw_candidate in enumerate(candidate_raw):
        uncertainties = set(raw_candidate.get("uncertainty") or [])
        if raw_candidate.get("source") == "unresolved_title" or "row_split" in uncertainties:
            tags.append(_tag(
                "CANDIDATE_ERROR", "ITEM_ROW_SPLIT_ERROR", scope=f"candidates[{index}]", confidence=0.68,
                message="품목 행이 여러 OCR 행으로 분리됐을 가능성이 있습니다.", evidence={"candidate": raw_candidate},
            ))
        if raw_candidate.get("column_resolution") in (None, "ambiguous") or any("column" in value for value in uncertainties):
            numeric_count = sum(raw_candidate.get(key) is not None for key in ("quantity_candidate", "unit_price_candidate", "amount_candidate"))
            if numeric_count >= 2:
                tags.append(_tag(
                    "CANDIDATE_ERROR", "COLUMN_RESOLUTION_ERROR", scope=f"candidates[{index}]", confidence=0.63,
                    message="후보 숫자 열의 역할 판별이 불확실합니다.", evidence={"candidate": raw_candidate},
                ))

    item_sum = sum(_number(item.get("total_amount")) or 0 for item in final_items)
    total = _number(prediction.get("total_amount"))
    discount = _number(prediction.get("discount_amount")) or 0
    if final_items and total is not None and abs(item_sum - total) >= 0.01 and abs(item_sum - discount - total) >= 0.01:
        tags.append(_tag(
            "VALIDATION_ERROR", "ITEM_SUM_MISMATCH", confidence=1.0,
            message="최종 품목 금액 합계가 결제액과 일치하지 않습니다.",
            evidence={"item_sum": item_sum, "discount_amount": discount, "total_amount": total},
        ))

    supply, tax = _number(prediction.get("supply_amount")), _number(prediction.get("tax_amount"))
    if supply is not None and tax is not None and total is not None and any((supply, tax)) and abs(supply + tax - total) >= 0.01:
        tags.append(_tag(
            "VALIDATION_ERROR", "SUPPLY_TAX_MISMATCH", confidence=1.0,
            message="공급가액과 부가세의 합이 최종 결제액과 일치하지 않습니다.",
            evidence={"supply_amount": supply, "tax_amount": tax, "total_amount": total},
        ))

    hints = trace.get("deterministic_hints") or {}
    stated_count = _number(hints.get("stated_item_count"))
    stated_quantity = _number(hints.get("stated_total_quantity"))
    final_quantity = sum(_number(item.get("quantity")) or 0 for item in final_items)
    inconsistent = (
        stated_count is not None and int(stated_count) != len(final_items)
    ) or (
        stated_quantity is not None and abs(stated_quantity - final_quantity) >= 0.01
    )
    if inconsistent:
        tags.append(_tag(
            "VALIDATION_ERROR", "SUMMARY_ITEM_INCONSISTENCY", confidence=1.0,
            message="영수증 summary의 품목 수 또는 총수량이 최종 items와 일치하지 않습니다.",
            evidence={"stated_item_count": stated_count, "actual_item_count": len(final_items), "stated_total_quantity": stated_quantity, "actual_total_quantity": final_quantity},
        ))

    tags = _deduplicate(tags)
    if not tags:
        tags = [_tag(
            "UNKNOWN", "UNKNOWN", confidence=0.0,
            message="현재 자동 근거만으로 실패 원인을 특정할 수 없습니다.",
        )]
    category_counts = Counter(tag["category"] for tag in tags)
    primary_category = max(category_counts, key=category_counts.get) if category_counts else None
    return {
        "analysis_version": ANALYSIS_VERSION,
        "status": "FAILED" if tags else "SUCCESS",
        "primary_category": primary_category,
        "needs_review": any(tag["decision"] != "AUTO" for tag in tags),
        "error_tags": tags,
        "category_counts": dict(category_counts),
    }
