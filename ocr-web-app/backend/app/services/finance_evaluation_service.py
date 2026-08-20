from __future__ import annotations

import re
from difflib import SequenceMatcher
from io import BytesIO
from time import perf_counter
from typing import Any

from openpyxl import load_workbook

from app.api.routes.finance import _classify_receipt_with_model, _normalize
from app.services.finance_workbook_service import SHEET_NAMES, build_finance_workbook


CORE_FIELDS = (
    "document_type", "expense_category", "merchant", "transaction_date",
    "supply_amount", "tax_amount", "total_amount", "payment_method",
)
NUMBER_FIELDS = {"supply_amount", "tax_amount", "total_amount", "quantity", "unit_price"}

# Explicit receipt-domain aliases keep evaluation deterministic while allowing
# equivalent item names written in different languages. Keys are compacted by
# removing spaces and punctuation before lookup; values are stable concepts.
ITEM_NAME_ALIASES = {
    "hairsalon": "beauty_service",
    "beautysalon": "beauty_service",
    "hairdressingservice": "beauty_service",
    "미용서비스": "beauty_service",
    "미용실": "beauty_service",
    "barbershop": "barber_service",
    "barberservice": "barber_service",
    "이발서비스": "barber_service",
    "이발소": "barber_service",
}


def normalize_ground_truth(truth: dict[str, Any]) -> dict[str, Any]:
    """Convert Korean receipt labels to the English schema used by the model.

    Classification labels are deliberately not inferred from ``카테고리``.  An
    explicitly supplied English ``document_type``/``expense_category`` remains
    supported for existing evaluation payloads.
    """
    normalized = {
        field: truth[field]
        for field in CORE_FIELDS
        if field in truth
    }

    korean_to_english = {
        "가게명": "merchant",
        "총 결제액": "total_amount",
        "결제방식": "payment_method",
    }
    for korean_key, english_key in korean_to_english.items():
        if english_key not in normalized and korean_key in truth:
            normalized[english_key] = truth[korean_key]

    raw_date = truth.get("transaction_date", truth.get("구매일자"))
    if raw_date is not None:
        date_text = str(raw_date).strip()
        normalized["transaction_date"] = date_text[:10] if date_text else None

    source_items = truth.get("items")
    if not isinstance(source_items, list):
        source_items = truth.get("구매물품")
    if isinstance(source_items, list):
        normalized["items"] = []
        for source_item in source_items:
            if not isinstance(source_item, dict):
                continue
            item = {}
            aliases = {
                "name": "상품명",
                "quantity": "수량",
                "unit_price": "단가",
                "total_amount": "금액",
            }
            for english_key, korean_key in aliases.items():
                if english_key in source_item:
                    item[english_key] = source_item[english_key]
                elif korean_key in source_item:
                    item[english_key] = source_item[korean_key]
            normalized["items"].append(item)

    return normalized


def _canonical(field: str, value: Any) -> Any:
    if field in NUMBER_FIELDS:
        try:
            cleaned = re.sub(r"[^0-9.+-]", "", str(value or 0).replace(",", ""))
            return round(float(cleaned or 0), 2)
        except (TypeError, ValueError):
            return 0.0
    if value is None:
        return ""
    text = " ".join(str(value).strip().lower().split())
    if field == "name":
        compact = re.sub(r"[^0-9a-z가-힣]", "", text)
        return ITEM_NAME_ALIASES.get(compact, text)
    if field == "transaction_date":
        parts = re.findall(r"\d+", text)[:3]
        if len(parts) == 3:
            return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    if field == "payment_method":
        compact = re.sub(r"[^0-9a-z가-힣]", "", text)
        aliases = {
            "cash": ("cash", "현금", "현금결제"),
            "credit_card": ("creditcard", "card", "신용카드", "카드결제", "법인카드"),
            "debit_card": ("debitcard", "checkcard", "체크카드"),
            "bank_transfer": ("banktransfer", "transfer", "계좌이체", "이체"),
        }
        for standard, values in aliases.items():
            if compact in values:
                return standard
    if field == "merchant":
        text = re.sub(r"(?:주식회사|\(주\)|㈜)", "", text)
        return re.sub(r"[^0-9a-z가-힣]", "", text)
    return text


def _values_match(field: str, expected_value: Any, actual_value: Any) -> bool:
    expected = _canonical(field, expected_value)
    actual = _canonical(field, actual_value)
    if expected == actual:
        return True
    if field != "name" or not expected or not actual:
        return False
    expected_compact = re.sub(r"[^0-9a-z가-힣]", "", str(expected))
    actual_compact = re.sub(r"[^0-9a-z가-힣]", "", str(actual))
    if min(len(expected_compact), len(actual_compact)) >= 2 and (
        expected_compact in actual_compact or actual_compact in expected_compact
    ):
        return True
    return SequenceMatcher(None, expected_compact, actual_compact).ratio() >= 0.75


def _match_items(expected_items: list[dict[str, Any]], predicted_items: list[dict[str, Any]]) -> dict[int, int]:
    candidates = []
    fields = ("name", "quantity", "unit_price", "total_amount")
    for expected_index, expected_item in enumerate(expected_items):
        for actual_index, actual_item in enumerate(predicted_items):
            score = sum(
                (3 if field == "name" else 1)
                for field in fields
                if field in expected_item and _values_match(field, expected_item.get(field), actual_item.get(field))
            )
            candidates.append((score, expected_index, actual_index))
    matches = {}
    used_actual = set()
    for _, expected_index, actual_index in sorted(candidates, reverse=True):
        if expected_index not in matches and actual_index not in used_actual:
            matches[expected_index] = actual_index
            used_actual.add(actual_index)
    return matches


def score_fields(prediction: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    details = {}
    evaluated = 0
    correct = 0
    for field in CORE_FIELDS:
        if field not in truth:
            continue
        matched = _values_match(field, truth.get(field), prediction.get(field))
        details[field] = {"expected": truth.get(field), "actual": prediction.get(field), "correct": matched}
        evaluated += 1
        correct += int(matched)
    expected_items = truth.get("items")
    if isinstance(expected_items, list):
        predicted_items = [item for item in (prediction.get("items") or []) if isinstance(item, dict)] if isinstance(prediction.get("items"), list) else []
        item_fields = ("name", "quantity", "unit_price", "total_amount")
        item_details = []
        matches = _match_items(expected_items, predicted_items)
        count_matched = len(expected_items) == len(predicted_items)
        evaluated += 1
        correct += int(count_matched)
        for index, expected_item in enumerate(expected_items):
            actual_index = matches.get(index)
            actual_item = predicted_items[actual_index] if actual_index is not None else {}
            comparisons = {}
            for field in item_fields:
                if field not in expected_item:
                    continue
                matched = _values_match(field, expected_item.get(field), actual_item.get(field))
                comparisons[field] = {"expected": expected_item.get(field), "actual": actual_item.get(field), "correct": matched}
                evaluated += 1
                correct += int(matched)
            item_details.append({"index": index, "matched_actual_index": actual_index, "fields": comparisons})
        details["items"] = {
            "expected_count": len(expected_items),
            "actual_count": len(predicted_items),
            "count_correct": count_matched,
            "false_positive_count": max(0, len(predicted_items) - len(expected_items)),
            "items": item_details,
        }
    return {
        "correct_fields": correct,
        "evaluated_fields": evaluated,
        "field_accuracy": correct / evaluated if evaluated else 0,
        "complete_match": bool(evaluated) and correct == evaluated,
        "fields": details,
    }


def _ocr_contains(text: str, field: str, value: Any) -> bool:
    if value is None or value == "":
        return False
    if field == "transaction_date":
        parts = re.findall(r"\d+", str(value))[:3]
        if len(parts) != 3:
            return False
        year, month, day = (int(part) for part in parts)
        date_pattern = (
            rf"(?<!\d){year:04d}\s*(?:년\s*|[-./]\s*)"
            rf"0?{month}\s*(?:월\s*|[-./]\s*)0?{day}\s*일?(?!\d)"
        )
        return re.search(date_pattern, text) is not None
    if field in NUMBER_FIELDS:
        expected_digits = re.sub(r"\D", "", str(value))
        return bool(expected_digits) and expected_digits in re.sub(r"\D", "", text)
    expected = re.sub(r"[^0-9a-z가-힣]", "", str(value).lower())
    source = re.sub(r"[^0-9a-z가-힣]", "", text.lower())
    return bool(expected) and expected in source


def estimate_ocr_impact(text: str, truth: dict[str, Any], score: dict[str, Any]) -> dict[str, Any]:
    """Estimate whether each extraction error likely began in OCR or the LLM.

    This is evidence-based attribution, not a CER/word-error-rate measurement.
    """
    fields = []

    def append(field: str, label: str, expected: Any, correct: bool) -> None:
        evidence_found = _ocr_contains(text, field, expected)
        if evidence_found and correct:
            status = "SUCCESS"
        elif evidence_found:
            status = "LIKELY_LLM_ERROR"
        elif correct:
            status = "LLM_RECOVERY"
        else:
            status = "LIKELY_OCR_ERROR"
        fields.append({
            "field": label,
            "expected": expected,
            "ocr_evidence_found": evidence_found,
            "llm_correct": bool(correct),
            "status": status,
        })

    score_fields_detail = score.get("fields") or {}
    for field, detail in score_fields_detail.items():
        if field == "items" or not isinstance(detail, dict):
            continue
        append(field, field, detail.get("expected"), bool(detail.get("correct")))

    items_detail = score_fields_detail.get("items") or {}
    for item in items_detail.get("items") or []:
        index = int(item.get("index") or 0)
        for field, detail in (item.get("fields") or {}).items():
            append(field, f"items[{index}].{field}", detail.get("expected"), bool(detail.get("correct")))

    counts = {status: sum(item["status"] == status for item in fields) for status in (
        "SUCCESS", "LIKELY_OCR_ERROR", "LIKELY_LLM_ERROR", "LLM_RECOVERY",
    )}
    evaluated = len(fields)
    return {
        "method": "FIELD_EVIDENCE_ESTIMATE",
        "notice": "OCR 원문에 정답 필드가 존재하는지 기반으로 한 원인 추정이며 CER/F1 점수가 아닙니다.",
        "evaluated_fields": evaluated,
        "ocr_evidence_rate": sum(item["ocr_evidence_found"] for item in fields) / evaluated if evaluated else 0,
        "counts": counts,
        "fields": fields,
    }


def verify_workbook(record: dict[str, Any]) -> dict[str, Any]:
    try:
        workbook = load_workbook(BytesIO(build_finance_workbook([record])), data_only=False)
        expected_sheet = SHEET_NAMES.get(record.get("document_type"))
        sheet = workbook[expected_sheet] if expected_sheet in workbook.sheetnames else workbook.active
        headers = [sheet.cell(11, column).value for column in range(1, 9)]
        rows = [
            [sheet.cell(row, column).value for column in range(1, 9)]
            for row in range(12, max(12, sheet.max_row))
        ]
        return {
            "success": expected_sheet in workbook.sheetnames and workbook.active.title == expected_sheet,
            "active_sheet": workbook.active.title,
            "expected_sheet": expected_sheet,
            "preview": {"headers": headers, "rows": rows},
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


async def evaluate_models(
    *, text: str, filename: str, truth: dict[str, Any], model_names: list[str],
) -> list[dict[str, Any]]:
    truth = normalize_ground_truth(truth)
    results = []
    for model_name in model_names:
        started = perf_counter()
        try:
            pure = await _classify_receipt_with_model(text, filename, model_name)
            latency_ms = round((perf_counter() - started) * 1000)
            system = _normalize(dict(pure), filename, text)
            system_prediction = {field: system.get(field) for field in CORE_FIELDS}
            system_prediction["items"] = (system.get("structured_data") or {}).get("items") or []
            system_score = score_fields(system_prediction, truth)
            results.append({
                "model_name": model_name,
                "success": True,
                "latency_ms": latency_ms,
                "system": {
                    "prediction": system_prediction,
                    "score": system_score,
                    "ocr_impact": estimate_ocr_impact(text, truth, system_score),
                    "workbook": verify_workbook(system),
                },
            })
        except Exception as exc:
            results.append({
                "model_name": model_name,
                "success": False,
                "latency_ms": round((perf_counter() - started) * 1000),
                "error": str(exc),
                "system": {"prediction": {}, "score": score_fields({}, truth), "workbook": {"success": False}},
            })
    return results
