"""Minimal one-call receipt pipeline.

The model extracts one compact JSON object.  Code validates the answer and
decides PASS/REVIEW; it deliberately does not repair model output with OCR
candidate graphs, retries, or merchant-specific recovery rules.
"""
from __future__ import annotations

import json
import logging
import re
from time import perf_counter
from typing import Any

from app.api.routes.chatbot import generate
from app.constants.finance_taxonomy import (
    ALLOWED_EXPENSE_CATEGORIES,
    CATEGORY_TO_DOCUMENT_TYPE,
    refine_expense_category,
)
from app.core.config import settings
from app.services.finance_normalization import normalize_date


FINANCE_PROMPT_VERSION = "receipt-simple-v1-one-call"
RECEIPTS_MODEL_NAME = settings.RECEIPTS_LLM_MODEL
EXPENSE_CATEGORIES = ALLOWED_EXPENSE_CATEGORIES
RECEIPT_LLM_TIMEOUT_SECONDS = 240
RECEIPT_LLM_NUM_PREDICT = 600
MAX_OCR_PROMPT_CHARS = 8000
logger = logging.getLogger(__name__)

_MONEY_RE = re.compile(r"(?<!\d)(?:\d{1,3}(?:[,.]\d{3})+|\d{3,8})(?!\d)")
_CARD_NUMBER_LABEL_PATTERN = re.compile(
    r"(?:\uce74\ub4dc\s*(?:\ubc88\ud638|no\.?)|card\s*(?:number|no\.?|#))\s*[:\uff1a]?\s*"
    r"([0-9xX*\u2217][0-9xX*\u2217\t -]{10,30}[0-9xX*\u2217])",
    re.IGNORECASE,
)
_REMOVE_LINE_RE = re.compile(
    r"https?://|www\.|고객센터|교환.*환불|개인정보|약관|QR\s*코드|"
    r"영수증을.*보관|감사합니다",
    re.IGNORECASE,
)


def _normalize_expense_category(value: Any, evidence_text: Any = None) -> str | None:
    return refine_expense_category(value, evidence_text)


def _receipt_number(value: str) -> int:
    value = re.sub(r"\s+", "", str(value or "").strip())
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", value):
        return int(re.sub(r"[.,]", "", value))
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else 0


def _ground_masked_card_number(model_value: Any, text: str) -> tuple[str | None, dict[str, Any]]:
    """Accept only one explicitly labelled and masked card number from OCR."""
    candidates: list[str] = []
    for match in _CARD_NUMBER_LABEL_PATTERN.finditer(text or ""):
        candidate = re.sub(r"[\t ]+", "", match.group(1)).strip("-")
        slots = re.sub(r"[^0-9xX*\u2217]", "", candidate)
        if 12 <= len(slots) <= 19 and len(re.findall(r"\d", slots)) >= 4 and re.search(r"[xX*\u2217]", slots):
            candidates.append(candidate)
    unique = list(dict.fromkeys(candidates))
    accepted = unique[0] if len(unique) == 1 else None
    return accepted, {
        "policy": "explicit_label_and_mask_required",
        "accepted": accepted is not None,
        "ocr_candidates": unique,
        "model_value_rejected": bool(model_value) and str(model_value).strip() != accepted,
        "reason": "unique_labeled_masked_ocr_candidate" if accepted else "ambiguous_labeled_masked_candidates" if unique else "missing_explicit_label_or_mask",
    }


def _simple_ocr_lines(text: str) -> list[dict[str, str]]:
    """Keep ordered, unique OCR lines and discard only obvious boilerplate."""
    rows: list[str] = []
    seen: set[str] = set()
    for raw_line in str(text or "").splitlines():
        line = " ".join(raw_line.strip().split())
        compact = re.sub(r"\s+", "", line).lower()
        if not compact or compact in seen:
            continue
        seen.add(compact)
        if _REMOVE_LINE_RE.search(line) and not _MONEY_RE.search(line):
            continue
        rows.append(line)
    return [{"id": f"L{index:03d}", "text": line} for index, line in enumerate(rows, 1)]


def _bounded_ocr_text(text: str) -> tuple[str, dict[str, Any]]:
    lines = _simple_ocr_lines(text)
    rendered = [f"{line['id']} | {line['text']}" for line in lines]
    original_chars = sum(len(line) + 1 for line in rendered)
    if original_chars <= MAX_OCR_PROMPT_CHARS:
        selected = rendered
    else:
        # Preserve the header, settlement tail, and every amount-bearing line.
        priority = set(range(min(25, len(rendered))))
        priority.update(range(max(0, len(rendered) - 35), len(rendered)))
        priority.update(index for index, line in enumerate(rendered) if _MONEY_RE.search(line))
        selected, used = [], 0
        for index, line in enumerate(rendered):
            if index not in priority:
                continue
            if used + len(line) + 1 > MAX_OCR_PROMPT_CHARS:
                break
            selected.append(line)
            used += len(line) + 1
    prompt_text = "\n".join(selected)
    return prompt_text, {
        "raw_line_count": len(str(text or "").splitlines()),
        "clean_line_count": len(lines),
        "prompt_line_count": len(selected),
        "prompt_chars": len(prompt_text),
        "truncated": len(selected) < len(rendered),
    }


def _preflight_review_reasons(text: str) -> list[str]:
    clean = " ".join(str(text or "").split())
    reasons = []
    if len(clean) < 40:
        reasons.append("OCR_TEXT_TOO_SHORT")
    if not _MONEY_RE.search(clean):
        reasons.append("NO_MONEY_EVIDENCE")
    if len(clean) > 20000:
        reasons.append("OCR_TEXT_TOO_DENSE")
    return reasons


def _simple_receipt_prompt(text: str, filename: str) -> tuple[str, dict[str, Any]]:
    ocr_text, diagnostics = _bounded_ocr_text(text)
    category_lines = "\n".join(f"- {value}" for value in EXPENSE_CATEGORIES)
    prompt = f"""다음 OCR은 한국 영수증입니다. OCR에 실제로 보이는 값만 사용해 JSON 객체 하나로 반환하세요.
설명, 마크다운, 코드 블록은 출력하지 마세요. 알 수 없는 값은 null, 품목 근거가 없으면 items는 []입니다.

반환 키:
merchant, transaction_date, expense_category, supply_amount, tax_amount,
discount_amount, total_amount, payment_method, items

items의 키:
name, quantity, unit_price, total_amount

규칙:
1. transaction_date는 YYYY-MM-DD입니다.
2. 금액과 수량은 숫자입니다. 할인·쿠폰·소계·부가세·결제 행을 품목으로 만들지 마세요.
3. total_amount는 실제 최종 결제·승인·받을 금액입니다.
4. 일반적인 상품 행이 없는 택시·승차권·미용·서비스 승인전표는 items=[]를 허용합니다.
5. expense_category는 아래 목록에서만 고릅니다.
{category_lines}

[파일명]
{filename}

[OCR 행]
{ocr_text}
"""
    return prompt, diagnostics


def _generation_metrics(response: Any) -> dict[str, Any]:
    metrics = getattr(response, "ollama_metrics", None)
    if not isinstance(metrics, dict):
        return {}
    return {
        "prompt_eval_count": int(metrics.get("prompt_eval_count") or 0),
        "eval_count": int(metrics.get("eval_count") or 0),
        "done_reason": str(metrics.get("done_reason") or ""),
        "total_duration_ms": round(float(metrics.get("total_duration") or 0) / 1_000_000, 2),
        "prompt_eval_duration_ms": round(float(metrics.get("prompt_eval_duration") or 0) / 1_000_000, 2),
        "eval_duration_ms": round(float(metrics.get("eval_duration") or 0) / 1_000_000, 2),
    }


async def _generate_receipt_json(*args: Any, **kwargs: Any) -> str:
    import sys
    route_module = sys.modules.get("app.api.routes.finance")
    generator = getattr(route_module, "generate", generate) if route_module else generate
    return await generator(*args, **kwargs)


def _as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _clean_model_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items = []
    for raw in value[:50]:
        if not isinstance(raw, dict):
            continue
        name = " ".join(str(raw.get("name") or "").split())[:300]
        if not name:
            continue
        items.append({
            "name": name,
            "quantity": _as_number(raw.get("quantity")),
            "unit_price": _as_number(raw.get("unit_price")),
            "total_amount": _as_number(raw.get("total_amount")),
        })
    return items


def _amount_is_grounded(value: Any, text: str) -> bool:
    number = _as_number(value)
    if number is None:
        return False
    observed = {_receipt_number(token) for token in _MONEY_RE.findall(text)}
    return any(abs(float(number) - float(candidate)) < .01 for candidate in observed)


def _simple_validation(result: dict[str, Any], text: str) -> dict[str, Any]:
    reasons: list[str] = []
    required = ("merchant", "transaction_date", "total_amount", "expense_category")
    missing = [field for field in required if result.get(field) in (None, "", [])]
    if len(missing) >= 2:
        reasons.append("MULTIPLE_REQUIRED_FIELDS_MISSING")
    for field in missing:
        reasons.append(f"MISSING_{field.upper()}")

    category = _normalize_expense_category(result.get("expense_category"), text)
    if not category:
        reasons.append("INVALID_EXPENSE_CATEGORY")
    result["expense_category"] = category

    raw_transaction_date = str(result.get("transaction_date") or "").strip() or None
    transaction_date = normalize_date(raw_transaction_date)
    if raw_transaction_date and not transaction_date:
        reasons.append("INVALID_TRANSACTION_DATE")
    result["transaction_date"] = transaction_date

    for field in ("supply_amount", "tax_amount", "discount_amount", "total_amount"):
        result[field] = _as_number(result.get(field))
    result["items"] = _clean_model_items(result.get("items"))

    total = _as_number(result.get("total_amount"))
    if total is not None and not _amount_is_grounded(total, text):
        reasons.append("TOTAL_AMOUNT_NOT_IN_OCR")
    supply, tax, discount = (
        _as_number(result.get("supply_amount")),
        _as_number(result.get("tax_amount")),
        _as_number(result.get("discount_amount")) or 0,
    )
    if supply is not None and tax is not None and total is not None:
        if abs(float(supply) + float(tax) - float(discount) - float(total)) > 1:
            reasons.append("AMOUNT_RELATION_MISMATCH")
    item_amounts = [_as_number(item.get("total_amount")) for item in result["items"]]
    if result["items"] and all(value is not None for value in item_amounts) and total is not None:
        item_sum = sum(float(value) for value in item_amounts if value is not None)
        if abs(item_sum - float(discount) - float(total)) > 1 and abs(item_sum - float(total)) > 1:
            reasons.append("ITEM_SUM_MISMATCH")
    for item in result["items"]:
        quantity = _as_number(item.get("quantity"))
        unit_price = _as_number(item.get("unit_price"))
        item_total = _as_number(item.get("total_amount"))
        if quantity is not None and unit_price is not None and item_total is not None:
            if abs(float(quantity) * float(unit_price) - float(item_total)) > 1:
                reasons.append("ITEM_ARITHMETIC_MISMATCH")
                break

    reasons = list(dict.fromkeys(reasons))
    return {
        "decision": "REVIEW" if reasons else "PASS",
        "reasons": reasons,
        "missing_fields": missing,
        "checks": {
            "json_schema": "PASS",
            "total_grounded": bool(total is not None and _amount_is_grounded(total, text)),
            "amount_relation": "AMOUNT_RELATION_MISMATCH" not in reasons,
            "item_sum": "ITEM_SUM_MISMATCH" not in reasons,
            "item_arithmetic": "ITEM_ARITHMETIC_MISMATCH" not in reasons,
        },
    }


async def _classify_receipt_with_model(
    text: str,
    filename: str,
    model_name: str,
    pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    preflight_reasons = _preflight_review_reasons(text)
    if preflight_reasons:
        return {
            "merchant": None, "transaction_date": None, "expense_category": None,
            "supply_amount": None, "tax_amount": None, "discount_amount": None,
            "total_amount": None, "payment_method": None, "items": [],
            "automation_validation": {"decision": "REVIEW", "reasons": preflight_reasons, "checks": {}},
            "llm_trace": {
                "model_name": model_name, "prompt_version": FINANCE_PROMPT_VERSION,
                "call_count": 0, "call_status": "skipped_preflight_review",
                "input_chars": 0, "output_chars": 0, "latency_ms": 0, "ollama": {},
                "raw_output": None,
            },
            "_model_name": model_name,
        }

    prompt, input_diagnostics = _simple_receipt_prompt(text, filename)
    started = perf_counter()
    raw = await _generate_receipt_json(
        prompt,
        json_format=True,
        num_predict=RECEIPT_LLM_NUM_PREDICT,
        model_name=model_name,
        request_timeout_seconds=RECEIPT_LLM_TIMEOUT_SECONDS,
    )
    latency_ms = round((perf_counter() - started) * 1000)
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("receipt JSON object expected")
    parsed["automation_validation"] = _simple_validation(parsed, text)
    parsed["llm_trace"] = {
        "model_name": model_name,
        "prompt_version": FINANCE_PROMPT_VERSION,
        "call_count": 1,
        "call_status": "success",
        "input_chars": len(prompt),
        "output_chars": len(raw),
        "latency_ms": latency_ms,
        "input_diagnostics": input_diagnostics,
        "ollama": _generation_metrics(raw),
        "raw_output": json.loads(json.dumps(parsed, ensure_ascii=False)),
        "response_text": str(raw),
    }
    parsed["_model_name"] = model_name
    return parsed


async def _classify_receipt(
    text: str,
    filename: str,
    pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        return await _classify_receipt_with_model(text, filename, RECEIPTS_MODEL_NAME, pages)
    except Exception as exc:
        logger.warning("Simple receipt extraction failed: filename=%s error=%s", filename, type(exc).__name__)
        return {
            "merchant": None, "transaction_date": None, "expense_category": None,
            "supply_amount": None, "tax_amount": None, "discount_amount": None,
            "total_amount": None, "payment_method": None, "items": [],
            "automation_validation": {
                "decision": "REVIEW",
                "reasons": ["LLM_CALL_FAILED", type(exc).__name__],
                "checks": {},
            },
            "llm_trace": {
                "model_name": RECEIPTS_MODEL_NAME,
                "prompt_version": FINANCE_PROMPT_VERSION,
                "call_count": 1,
                "call_status": "failed",
                "raw_output": None,
            },
            "_model_name": "rules-fallback",
        }


def _normalize(result: dict[str, Any], filename: str, text: str) -> dict[str, Any]:
    """Minimal normalization: format values, validate, and never auto-repair."""
    validation = result.get("automation_validation")
    if not isinstance(validation, dict):
        validation = _simple_validation(result, text)
        result["automation_validation"] = validation
    category = _normalize_expense_category(result.get("expense_category"), text)
    document_type = CATEGORY_TO_DOCUMENT_TYPE.get(category)
    items = _clean_model_items(result.get("items"))
    total_quantity = None
    if items and all(item.get("quantity") is not None for item in items):
        total_quantity = sum(float(item["quantity"]) for item in items)
        if total_quantity.is_integer():
            total_quantity = int(total_quantity)
    grounded_card, card_evidence = _ground_masked_card_number(None, text)
    result.update({
        "doc_type": document_type,
        "document_type": document_type,
        "expense_category": category,
        "items": items,
        "total_quantity": total_quantity,
        "card_number": grounded_card,
        "card_number_evidence": card_evidence,
        "source_filename": filename,
        "needs_review": validation.get("decision") != "PASS",
        "review_reasons": validation.get("reasons") or [],
    })
    merchant = " ".join(str(result.get("merchant") or "").split())[:300] or None
    payment_method = str(result.get("payment_method") or "").strip() or None
    if payment_method and "카드" in payment_method:
        payment_method = "카드"
    elif payment_method and "현금" in payment_method:
        payment_method = "현금"
    structured = result
    return {
        "document_type": document_type,
        "expense_category": category,
        "merchant": merchant,
        "transaction_date": result.get("transaction_date"),
        "supply_amount": _as_number(result.get("supply_amount")) or 0,
        "tax_amount": _as_number(result.get("tax_amount")) or 0,
        "total_amount": _as_number(result.get("total_amount")) or 0,
        "payment_method": payment_method,
        "description": merchant,
        "structured_data": structured,
        "model_name": str(result.get("_model_name") or RECEIPTS_MODEL_NAME),
        "status": "REVIEW",
    }


__all__ = [name for name in globals() if not name.startswith("__")]
