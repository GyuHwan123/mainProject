from __future__ import annotations

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
NUMBER_FIELDS = {"supply_amount", "tax_amount", "total_amount"}


def _canonical(field: str, value: Any) -> Any:
    if field in NUMBER_FIELDS:
        try:
            return round(float(value or 0), 2)
        except (TypeError, ValueError):
            return 0.0
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def score_fields(prediction: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    details = {}
    evaluated = 0
    correct = 0
    for field in CORE_FIELDS:
        if field not in truth:
            continue
        expected = _canonical(field, truth.get(field))
        actual = _canonical(field, prediction.get(field))
        matched = actual == expected
        details[field] = {"expected": truth.get(field), "actual": prediction.get(field), "correct": matched}
        evaluated += 1
        correct += int(matched)
    expected_items = truth.get("items")
    if isinstance(expected_items, list):
        predicted_items = prediction.get("items") if isinstance(prediction.get("items"), list) else []
        item_fields = ("name", "quantity", "unit_price", "total_amount")
        item_details = []
        for index, expected_item in enumerate(expected_items):
            actual_item = predicted_items[index] if index < len(predicted_items) and isinstance(predicted_items[index], dict) else {}
            comparisons = {}
            for field in item_fields:
                if field not in expected_item:
                    continue
                expected = _canonical(field if field != "unit_price" else "total_amount", expected_item.get(field))
                actual = _canonical(field if field != "unit_price" else "total_amount", actual_item.get(field))
                matched = actual == expected
                comparisons[field] = {"expected": expected_item.get(field), "actual": actual_item.get(field), "correct": matched}
                evaluated += 1
                correct += int(matched)
            item_details.append({"index": index, "fields": comparisons})
        details["items"] = {"expected_count": len(expected_items), "actual_count": len(predicted_items), "items": item_details}
    return {
        "correct_fields": correct,
        "evaluated_fields": evaluated,
        "field_accuracy": correct / evaluated if evaluated else 0,
        "complete_match": bool(evaluated) and correct == evaluated,
        "fields": details,
    }


def verify_workbook(record: dict[str, Any]) -> dict[str, Any]:
    try:
        workbook = load_workbook(BytesIO(build_finance_workbook([record])), data_only=False)
        expected_sheet = SHEET_NAMES.get(record.get("document_type"))
        return {
            "success": expected_sheet in workbook.sheetnames and workbook.active.title == expected_sheet,
            "active_sheet": workbook.active.title,
            "expected_sheet": expected_sheet,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


async def evaluate_models(
    *, text: str, filename: str, truth: dict[str, Any], model_names: list[str],
) -> list[dict[str, Any]]:
    results = []
    for model_name in model_names:
        started = perf_counter()
        try:
            pure = await _classify_receipt_with_model(text, filename, model_name)
            latency_ms = round((perf_counter() - started) * 1000)
            system = _normalize(dict(pure), filename, text)
            system_prediction = {field: system.get(field) for field in CORE_FIELDS}
            system_prediction["items"] = (system.get("structured_data") or {}).get("items") or []
            pure_score = score_fields(pure, truth)
            system_score = score_fields(system_prediction, truth)
            results.append({
                "model_name": model_name,
                "success": True,
                "latency_ms": latency_ms,
                "pure": {"prediction": pure, "score": pure_score},
                "system": {
                    "prediction": system_prediction,
                    "score": system_score,
                    "workbook": verify_workbook(system),
                },
            })
        except Exception as exc:
            results.append({
                "model_name": model_name,
                "success": False,
                "latency_ms": round((perf_counter() - started) * 1000),
                "error": str(exc),
                "pure": {"prediction": {}, "score": score_fields({}, truth)},
                "system": {"prediction": {}, "score": score_fields({}, truth), "workbook": {"success": False}},
            })
    return results
