from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from app.services.finance_normalization import normalization_equivalent, semantic_normalized_value


ANALYSIS_VERSION = "receipt-error-analysis-v3"
NUMBER_FIELDS = {"quantity", "unit_price", "total_amount", "supply_amount", "tax_amount", "total_quantity", "discount_amount"}
SUMMARY_ITEM_PATTERN = re.compile(
    r"합계|소계|결제|승인|공급가액|부가세|세액|할인|쿠폰|적립금|거스름돈|총\s*구매",
    re.IGNORECASE,
)
DISCOUNT_PATTERN = re.compile(r"할인|쿠폰|적립금|캐시백", re.IGNORECASE)
TOTAL_PATTERN = re.compile(r"합계|소계|결제|승인|총\s*구매|공급가액|부가세|세액", re.IGNORECASE)
FIELD_LABEL_PATTERNS = {
    "total_amount": re.compile(r"합계|결제|승인|받을\s*금액|총\s*구매", re.IGNORECASE),
    "supply_amount": re.compile(r"공급가액|공급액", re.IGNORECASE),
    "tax_amount": re.compile(r"부가세|세액", re.IGNORECASE),
    "discount_amount": re.compile(r"할인|쿠폰", re.IGNORECASE),
    "total_quantity": re.compile(r"총\s*(?:수량|매수)", re.IGNORECASE),
}
CATEGORY_EVIDENCE_PATTERNS = {
    "외식/식사": re.compile(r"식대|식사|음식|메뉴|식당|주문|포케|떡볶이|고기|파스타", re.IGNORECASE),
    "카페/음료": re.compile(r"카페|음료|커피|라떼|아메리카노|스무디|주스|공차|투썸|베이커리", re.IGNORECASE),
    "식품/장보기": re.compile(r"식품|마트|편의점|슈퍼|장보기|과자|라면|생수|봉투", re.IGNORECASE),
    "생활용품": re.compile(r"생활|물티슈|세제|주방|욕실|청소|종량제|스펀지", re.IGNORECASE),
    "의류/패션": re.compile(r"의류|패션|셔츠|가디건|저지|원피스|바지|재킷|신발|가방", re.IGNORECASE),
    "취미/선물": re.compile(r"취미|선물|꽃|식물|공예|게임|소품", re.IGNORECASE),
    "미용/뷰티": re.compile(r"미용|헤어|네일|뷰티|화장품|립|스킨|트리트먼트", re.IGNORECASE),
    "도서": re.compile(r"도서|서적|책|문고", re.IGNORECASE),
    "전자제품/문구": re.compile(r"전자|가전|컴퓨터|휴대폰|케이블|문구|사무용품|노트|펜", re.IGNORECASE),
    "대중교통": re.compile(r"버스|택시|철도|korail|ktx|승차권|운임", re.IGNORECASE),
    "주유/차량": re.compile(r"주유소|유종|휘발유|경유|주유|정비|oil|lpg", re.IGNORECASE),
    "의료": re.compile(r"병원|의원|약국|의료|진료", re.IGNORECASE),
    "문화": re.compile(r"문화|공연|영화|전시", re.IGNORECASE),
    "레저/스포츠": re.compile(r"레저|여가|스포츠|골프|숙박|호텔|리조트", re.IGNORECASE),
}
OCR_DIGIT_CONFUSIONS = str.maketrans({
    "o": "0", "O": "0",
    "i": "1", "I": "1", "l": "1", "L": "1",
    "z": "2", "Z": "2",
    "s": "5", "S": "5",
    "b": "8", "B": "8",
})


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        cleaned = re.sub(r"[^0-9.+-]", "", str(value).replace(",", ""))
        return round(float(cleaned), 2) if cleaned else None
    except (TypeError, ValueError):
        return None


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())


def _similar(left: Any, right: Any) -> float:
    a, b = _compact(left), _compact(right)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _ocr_digit_confusion(expected: Any, observed: Any) -> bool:
    expected_compact = _compact(expected)
    observed_compact = _compact(observed)
    if not expected_compact or not observed_compact or expected_compact == observed_compact:
        return False
    if not any(character.isdigit() for character in expected_compact):
        return False
    return (
        observed_compact.translate(OCR_DIGIT_CONFUSIONS)
        == expected_compact.translate(OCR_DIGIT_CONFUSIONS)
    )


def _quantity_decimal_separator_lost(expected: Any, observed: Any) -> bool:
    def quantity_value(value: Any) -> float | None:
        try:
            cleaned = re.sub(r"[^0-9.+-]", "", str(value).replace(",", ""))
            return float(cleaned) if cleaned else None
        except (TypeError, ValueError):
            return None

    expected_number = quantity_value(expected)
    observed_number = quantity_value(observed)
    if expected_number is None or observed_number is None or expected_number <= 0 or observed_number <= 0:
        return False
    return (
        abs(expected_number * 1000 - observed_number) < 0.01
        or abs(observed_number * 1000 - expected_number) < 0.01
    )


def _value_equal(field: str, left: Any, right: Any) -> bool:
    if field in NUMBER_FIELDS:
        a, b = _number(left), _number(right)
        return a is not None and b is not None and abs(a - b) < 0.01
    if field == "transaction_date":
        left_parts = re.findall(r"\d+", str(left or ""))[:3]
        right_parts = re.findall(r"\d+", str(right or ""))[:3]
        return len(left_parts) == 3 and len(right_parts) == 3 and tuple(map(int, left_parts)) == tuple(map(int, right_parts))
    if field in {"expense_category", "merchant", "name"} and normalization_equivalent(field, left, right):
        return True
    if field == "merchant":
        return False
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


def _date_in_text(text: str, value: Any) -> bool:
    parts = re.findall(r"\d+", str(value or ""))[:3]
    if len(parts) != 3:
        return False
    year, month, day = (int(part) for part in parts)
    candidates = re.findall(
        r"(?<!\d)(\d{2,4})\s*(?:년\s*|[-./]\s*)(\d{1,2})\s*(?:월\s*|[-./]\s*)(\d{1,2})\s*일?(?!\d)",
        text,
    )
    return any(
        (int(candidate_year) == year or int(candidate_year) == year % 100)
        and int(candidate_month) == month and int(candidate_day) == day
        for candidate_year, candidate_month, candidate_day in candidates
    )


def _category_evidence_found(text: str, expected: Any) -> bool:
    expected_text = semantic_normalized_value("expense_category", expected)
    return any(
        semantic_normalized_value("expense_category", label) == expected_text and pattern.search(text)
        for label, pattern in CATEGORY_EVIDENCE_PATTERNS.items()
    )


def _best_match(item: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[int | None, float]:
    best_index, best_score = None, 0.0
    for index, row in enumerate(rows):
        name_score = 1.0 if normalization_equivalent("name", item.get("name"), row.get("name")) else _similar(item.get("name"), row.get("name"))
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
    llm_summary = (trace.get("llm") or {}).get("raw_output") or {}
    if not isinstance(llm_summary, dict):
        llm_summary = {}
    model_items = [row for row in llm_summary.get("items") or [] if isinstance(row, dict)]
    final_items = [row for row in prediction.get("items") or [] if isinstance(row, dict)]
    truth_items = [row for row in ground_truth.get("items") or [] if isinstance(row, dict)]
    tags: list[dict[str, Any]] = []
    for field, expected in ground_truth.items():
        if field == "items":
            continue
        actual = prediction.get(field)
        if (
            field == "total_quantity"
            and _quantity_decimal_separator_lost(expected, actual)
            and _text_contains(ocr_text, field, expected)
        ):
            tags.append(_tag(
                "NORMALIZATION_ERROR", "DECIMAL_SEPARATOR_LOST", field=field, confidence=0.99,
                message="OCR의 총수량 소수점이 숫자 정규화 과정에서 제거됐습니다.",
                evidence={"expected": expected, "actual": actual},
            ))
        elif field not in NUMBER_FIELDS and expected != actual and normalization_equivalent(field, expected, actual):
            tags.append(_tag(
                "NORMALIZATION_ERROR", "SEMANTIC_EQUIVALENCE", field=field, confidence=1.0,
                message="표현은 다르지만 정규화하면 같은 의미인 값입니다.",
                evidence={
                    "expected": expected, "actual": actual,
                    "normalized": semantic_normalized_value(field, expected),
                },
            ))
    for index, truth_item in enumerate(truth_items):
        actual_index, _ = _best_match(truth_item, final_items)
        if actual_index is None:
            continue
        actual_item = final_items[actual_index]
        for field, expected in truth_item.items():
            actual = actual_item.get(field)
            if (
                field == "quantity"
                and _quantity_decimal_separator_lost(expected, actual)
                and _text_contains(ocr_text, field, expected)
            ):
                tags.append(_tag(
                    "NORMALIZATION_ERROR", "DECIMAL_SEPARATOR_LOST", scope=f"items[{index}]", field=field, confidence=0.99,
                    message="OCR의 수량 소수점이 숫자 정규화 과정에서 제거됐습니다.",
                    evidence={"expected": expected, "actual": actual},
                ))
            elif field not in NUMBER_FIELDS and expected != actual and normalization_equivalent(field, expected, actual):
                tags.append(_tag(
                    "NORMALIZATION_ERROR", "SEMANTIC_EQUIVALENCE", scope=f"items[{index}]", field=field, confidence=1.0,
                    message="품목 표현은 다르지만 정규화하면 같은 의미인 값입니다.",
                    evidence={
                        "expected": expected, "actual": actual,
                        "normalized": semantic_normalized_value(field, expected),
                    },
                ))
            elif field == "name" and _ocr_digit_confusion(expected, actual) and _text_contains(ocr_text, "name", actual):
                tags.append(_tag(
                    "OCR_ERROR", "OCR_CHARACTER_CONFUSION", scope=f"items[{index}]", field=field, confidence=0.98,
                    message="품목명의 숫자가 OCR에서 모양이 비슷한 문자로 잘못 인식됐습니다.",
                    evidence={"expected": expected, "actual": actual},
                ))
    has_attributed_failure = any(tag["category"] != "NORMALIZATION_ERROR" for tag in tags)
    if not _evaluation_failed(ground_truth, prediction) and not has_attributed_failure:
        category_counts = Counter(tag["category"] for tag in tags)
        return {
            "analysis_version": ANALYSIS_VERSION,
            "status": "SUCCESS",
            "primary_category": "NORMALIZATION_ERROR" if tags else None,
            "needs_review": False,
            "error_tags": tags,
            "category_counts": dict(category_counts),
        }

    # Summary OCR evidence: absence alone is uncertain because OCR can split tokens.
    for field, expected in ground_truth.items():
        if field == "items" or expected in (None, "", []):
            continue
        actual = prediction.get(field)
        if _value_equal(field, expected, actual):
            continue
        if (
            field == "total_quantity"
            and _quantity_decimal_separator_lost(expected, actual)
            and _text_contains(ocr_text, field, expected)
        ):
            continue
        if field == "expense_category" and _category_evidence_found(ocr_text, expected):
            tags.append(_tag(
                "LLM_ERROR", "CATEGORY_INFERENCE_ERROR", field=field, confidence=0.96,
                message="OCR에 카테고리 근거가 있지만 최종 카테고리를 다르게 추론했습니다.",
                evidence={"expected": expected, "actual": actual},
            ))
            continue
        if field == "transaction_date" and _date_in_text(ocr_text, expected) and _date_in_text(ocr_text, actual):
            tags.append(_tag(
                "LLM_ERROR", "TRANSACTION_DATE_SELECTION_ERROR", field=field, confidence=0.98,
                message="OCR에 여러 날짜가 있으며 LLM이 거래일이 아닌 날짜를 선택했습니다.",
                evidence={"expected": expected, "actual": actual},
            ))
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
            llm_value = llm_summary.get(field)
            if _empty(actual) and _empty(llm_value):
                tags.append(_tag(
                    "LLM_ERROR", "VALUE_MISSING", field=field, confidence=0.94,
                    message="정답 숫자는 OCR에 있지만 LLM 구조화 결과에서 누락됐습니다.",
                    evidence={"expected": expected, "actual": actual},
                ))
            elif _value_equal(field, expected, llm_value) and not _value_equal(field, expected, actual):
                tags.append(_tag(
                    "VALIDATION_ERROR", "POSTPROCESSING_CHANGED_CORRECT_VALUE", field=field, confidence=0.99,
                    message="LLM 원본의 정확한 값이 후처리 과정에서 변경됐습니다.",
                    evidence={"expected": expected, "llm_raw": llm_value, "final": actual},
                ))
            elif field == "total_amount" and any(
                _value_equal("total_amount", actual, item.get("total_amount"))
                for item in model_items
            ):
                tags.append(_tag(
                    "LLM_ERROR", "SUMMARY_AMOUNT_SELECTION_ERROR", field=field, confidence=0.98,
                    message="총 결제액 대신 개별 품목 금액을 summary 합계로 선택했습니다.",
                    evidence={"expected": expected, "actual": actual, "llm_raw": llm_value},
                ))
            else:
                tags.append(_tag(
                    "LLM_ERROR", "NUMERIC_VALUE_ERROR", field=field, confidence=0.91,
                    message="정답 숫자는 OCR에 있지만 LLM이 다른 숫자를 구조화했습니다.",
                    evidence={"expected": expected, "actual": actual},
                ))
        elif field == "merchant":
            tags.append(_tag(
                "LLM_ERROR", "MERCHANT_DETAIL_DROPPED", field=field, confidence=0.98,
                message="OCR에 전체 상호가 있지만 모델이 상호 또는 지점 정보 일부만 추출했습니다.",
                evidence={"expected": expected, "actual": actual},
            ))

    matched_model_indexes: set[int] = set()
    matched_final_indexes: set[int] = set()
    for truth_index, truth_item in enumerate(truth_items):
        scope = f"items[{truth_index}]"
        model_index, model_score = _best_match(truth_item, model_items)
        final_index, final_score = _best_match(truth_item, final_items)
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
        if model_item is None and name_in_ocr:
            tags.append(_tag(
                "LLM_ERROR", "ITEM_MISSING", scope=scope, confidence=0.96,
                message="OCR에 정답 품목이 있지만 LLM 구조화 결과에서 누락됐습니다.",
                evidence={"truth_item": truth_item},
            ))
        elif model_item is None:
            tags.append(_tag(
                "OCR_ERROR", "ITEM_TEXT_MISSING", scope=scope, confidence=0.72,
                message="OCR 결과에서 정답 품목을 확인하지 못했습니다.",
                evidence={"truth_item": truth_item, "name_found_in_ocr": name_in_ocr},
            ))

        comparison_source = model_item or final_item
        if comparison_source is not None:
            expected_name = truth_item.get("name")
            observed_name = comparison_source.get("name")
            if (
                not _value_equal("name", expected_name, observed_name)
                and _ocr_digit_confusion(expected_name, observed_name)
                and _text_contains(ocr_text, "name", observed_name)
            ):
                tags.append(_tag(
                    "OCR_ERROR", "OCR_CHARACTER_CONFUSION", scope=scope, field="name", confidence=0.98,
                    message="품목명의 숫자가 OCR에서 모양이 비슷한 문자로 잘못 인식됐습니다.",
                    evidence={"expected": expected_name, "actual": observed_name},
                ))
            for field, code in (
                ("quantity", "QUANTITY_ERROR"),
                ("unit_price", "UNIT_PRICE_ERROR"),
                ("total_amount", "ITEM_AMOUNT_ERROR"),
            ):
                if field not in truth_item or _value_equal(field, truth_item.get(field), comparison_source.get(field)):
                    continue
                if (
                    field == "quantity"
                    and _quantity_decimal_separator_lost(truth_item.get(field), comparison_source.get(field))
                    and _text_contains(ocr_text, field, truth_item.get(field))
                ):
                    continue
                tags.append(_tag(
                    "LLM_ERROR", code, scope=scope, field=field, confidence=0.9,
                    message=f"품목의 {field} 값이 정답과 다릅니다.",
                    evidence={"expected": truth_item.get(field), "actual": comparison_source.get(field)},
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

    # Compare the one-call LLM output with the final normalized record.
    for truth_index, truth_item in enumerate(truth_items):
        raw_index, raw_score = _best_match(truth_item, model_items)
        final_index, final_score = _best_match(truth_item, final_items)
        if raw_index is not None and raw_score >= 2.4 and (final_index is None or final_score < 2.4):
            tags.append(_tag(
                "VALIDATION_ERROR", "POSTPROCESSING_DROPPED_CORRECT_ITEM", scope=f"items[{truth_index}]", confidence=0.98,
                message="LLM 원본에 있던 정답 품목이 후처리 결과에서 제거됐습니다.",
                evidence={"truth_item": truth_item, "model_item": model_items[raw_index]},
            ))
    for final_index, final_item in enumerate(final_items):
        raw_index, raw_score = _best_match(final_item, model_items)
        truth_index, truth_score = _best_match(final_item, truth_items)
        if (raw_index is None or raw_score < 2.4) and (truth_index is None or truth_score < 2.4):
            tags.append(_tag(
                "VALIDATION_ERROR", "POSTPROCESSING_ADDED_UNSUPPORTED_ITEM", scope=f"final_items[{final_index}]", confidence=0.96,
                message="후처리가 LLM 원본과 정답에 없던 품목을 최종 결과에 추가했습니다.",
                evidence={"final_item": final_item},
            ))

    model_name_counts = Counter(_compact(item.get("name")) for item in model_items if _compact(item.get("name")))
    for name, count in model_name_counts.items():
        if count > 1:
            tags.append(_tag(
                "LLM_ERROR", "DUPLICATE_ITEM", scope="items", confidence=0.91,
                message="동일한 품목명이 LLM 결과에 중복됐습니다.", evidence={"normalized_name": name, "count": count},
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
    if supply is not None and tax is not None and total is not None and any((supply, tax)) and abs(supply + tax - total) > 10:
        tags.append(_tag(
            "VALIDATION_ERROR", "SUPPLY_TAX_MISMATCH", confidence=1.0,
            message="공급가액과 부가세의 합이 최종 결제액과 일치하지 않습니다.",
            evidence={"supply_amount": supply, "tax_amount": tax, "total_amount": total},
        ))

    # Every mismatched field must have at least one pipeline-responsibility tag.
    # This prevents UI rows from ending as "분류 없음" even when a specialized
    # heuristic has not been added yet.
    for field, expected in ground_truth.items():
        if field == "items" or _value_equal(field, expected, prediction.get(field)):
            continue
        if not any(tag.get("scope") == "sample" and tag.get("field") == field for tag in tags):
            tags.append(_tag(
                "LLM_ERROR", "FINAL_VALUE_MISMATCH", field=field, confidence=0.9,
                message="최종 예측값이 정답과 다르며 이전 단계에서 더 구체적인 원인이 확인되지 않았습니다.",
                evidence={"expected": expected, "actual": prediction.get(field)},
            ))
    for truth_index, truth_item in enumerate(truth_items):
        scope = f"items[{truth_index}]"
        actual_index, _ = _best_match(truth_item, final_items)
        actual_item = final_items[actual_index] if actual_index is not None else {}
        for field, expected in truth_item.items():
            if _value_equal(field, expected, actual_item.get(field)):
                continue
            if not any(tag.get("scope") == scope and (not tag.get("field") or tag.get("field") == field) for tag in tags):
                tags.append(_tag(
                    "LLM_ERROR", "ITEM_FIELD_MISMATCH", scope=scope, field=field, confidence=0.9,
                    message="LLM이 구조화한 품목 필드가 정답과 다릅니다.",
                    evidence={"expected": expected, "actual": actual_item.get(field)},
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
