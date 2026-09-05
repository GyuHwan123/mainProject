"""Minimal one-call receipt pipeline.

The model extracts one compact JSON object from OCR text. Code validates the
answer and decides PASS/REVIEW without another model call.
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
    CATEGORY_CLASSIFICATION_POLICIES,
    CATEGORY_DECISION_RULES,
    refine_expense_category,
)
from app.core.config import settings
from app.services.finance_normalization import normalize_date
from app.services.receipt_item_grounding import ground_items
from app.services.receipt_document_classifier import classify_document_type


FINANCE_PROMPT_VERSION = "receipt-simple-v1.3-compact-category-decision-rules"
RECEIPT_PIPELINE_VERSION = "receipt-simple-v3.2-document-classifier"
RECEIPTS_MODEL_NAME = settings.RECEIPTS_LLM_MODEL
EXPENSE_CATEGORIES = ALLOWED_EXPENSE_CATEGORIES
RECEIPT_LLM_TIMEOUT_SECONDS = settings.RECEIPTS_LLM_TIMEOUT_SECONDS
RECEIPT_LLM_NUM_PREDICT = 800
RECEIPT_LLM_KEEP_ALIVE = settings.RECEIPTS_LLM_KEEP_ALIVE
RECEIPT_LLM_NUM_CTX = settings.RECEIPTS_LLM_NUM_CTX
MAX_OCR_PROMPT_CHARS = 8000
AMOUNT_ROUNDING_TOLERANCE = 10
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


def _simple_receipt_prompt(
    text: str, filename: str, pages: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    ocr_text, diagnostics = _bounded_ocr_text(text)
    # Pages are used only after generation; the prompt remains text-only.
    amount_evidence = _extract_amount_evidence(text)
    diagnostics["amount_evidence"] = amount_evidence
    category_lines = "\n".join(
        f"- {value}: {CATEGORY_CLASSIFICATION_POLICIES[value]}" for value in EXPENSE_CATEGORIES
    )
    compact_amounts = {key: value for key, value in amount_evidence.items() if key not in {"labels", "resolution_context"}}
    prompt = f"""한국 영수증 OCR을 JSON 객체 하나로 구조화하세요. 설명·마크다운 없이 간결하게 출력하세요.
OCR에 직접 나타나야 하는 추출값이 없으면 null, 품목 근거가 없으면 items=[]입니다.
반환 키: merchant, transaction_date, expense_category, supply_amount, tax_amount, discount_amount, total_amount, items
items의 키: name, quantity, unit_price, total_amount
규칙:
- 날짜는 YYYY-MM-DD, 금액·수량은 숫자.
- 할인·쿠폰·소계·세금·결제 행은 품목에서 제외. 쇼핑백·포장비·배달비 등 유상 거래는 포함.
- total_amount는 최종 결제·승인 금액. 공급액·세액은 아래 금액 근거 우선, 근거 없는 값은 추정하지 마세요.
- 할인 전 세금 요약은 결제액과 달라도 다시 계산하지 마세요.
- expense_category는 추출값이 아니라 분류값입니다. 상호·품목·서비스 근거가 하나라도 있으면 14개 중 하나를 선택하고, 거래 성격을 판단할 근거가 전혀 없을 때만 null.
- 카테고리 판정: {CATEGORY_DECISION_RULES}
- 광고·환불 안내의 브랜드·상품은 분류 근거에서 제외하세요.
[카테고리 기준]
{category_lines}
[규칙 기반 금액 근거]
{json.dumps(compact_amounts, ensure_ascii=False, separators=(",", ":"))}
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
        "load_duration_ms": round(float(metrics.get("load_duration") or 0) / 1_000_000, 2),
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


def _labeled_amount(text: str, label_pattern: str) -> int | None:
    """Return an amount only when its label and value share one OCR line."""
    pattern = re.compile(
        rf"{label_pattern}[()\[\]:：]*(-?\d{{1,3}}(?:[,.]\d{{3}})+|-?\d{{1,8}})(?:원)?(?![\d*xX])",
        re.IGNORECASE,
    )
    lines = [re.sub(r"\s+", "", raw_line) for raw_line in str(text or "").splitlines()]
    for compact in lines:
        match = pattern.search(compact)
        if match:
            return _receipt_number(match.group(1)) * (-1 if match.group(1).startswith("-") else 1)
    return None


def _extract_amount_evidence(text: str, *, include_context: bool = False) -> dict[str, Any]:
    compact_text = "\n".join(re.sub(r"\s+", "", line) for line in str(text or "").splitlines())
    taxable_pattern = r"(?<!부가세)과세(?:물품|상품)?(?:가액|금액|합계|매출|액)"
    exempt_pattern = r"면세(?:물품|상품)?(?:가액|금액|합계|매출|액)"
    tax_pattern = r"(?:부가가치세(?!법)|부가세(?:액|포함)?(?!과세|면세)|(?<!과)세액|VAT)"
    tax_amount = _labeled_amount(text, tax_pattern)
    total_amount = _labeled_amount(text, r"(?:(?:총)?결제(?:금액|요금|액)|승인금액|받을금액|구매금액)")
    if total_amount is None:
        tax_included_pair = re.search(
            r"\[금액\][:：]?(\d{1,3}(?:[,.]\d{3})+|\d{1,8})(?:원)?.*?"
            r"(?:부가세(?:액|포함)?|VAT)[()\[\]:：]*(\d{1,3}(?:[,.]\d{3})+|\d{1,8})(?:원)?",
            compact_text,
            re.IGNORECASE,
        )
        if tax_included_pair:
            total_amount = _receipt_number(tax_included_pair.group(1))
            tax_amount = _receipt_number(tax_included_pair.group(2))
    evidence = {
        "supply_amount": _labeled_amount(text, r"공급(?:가액|액)"),
        "taxable_supply_amount": _labeled_amount(text, taxable_pattern),
        "tax_exempt_amount": _labeled_amount(text, exempt_pattern),
        "tax_amount": tax_amount,
        "total_amount": total_amount,
        "discount_amount": _labeled_amount(text, r"(?:총할인(?:금액|액)?|할인금액)"),
        "rounding_adjustment": _labeled_amount(text, r"(?:절사금액|절삭금액|반올림)"),
        "labels": {
            "supply": bool(re.search(r"공급(?:가액|액)", compact_text, re.IGNORECASE)),
            "taxable_supply": bool(re.search(taxable_pattern, compact_text, re.IGNORECASE)),
            "tax_exempt": bool(re.search(exempt_pattern, compact_text, re.IGNORECASE)),
            "tax": bool(re.search(tax_pattern, compact_text, re.IGNORECASE)),
            "total": bool(re.search(r"(?:(?:총)?결제(?:금액|요금|액)|승인금액|받을금액|구매금액)", compact_text, re.IGNORECASE)),
        },
    }
    if include_context:
        patterns = {
            "supply_amount": r"공급(?:가액|액)",
            "taxable_supply_amount": taxable_pattern,
            "tax_exempt_amount": exempt_pattern,
            "tax_amount": tax_pattern,
            "total_amount": r"(?:(?:최종)?카드(?:결제|승인)(?:금액|액)?|(?:최종|총)?결제(?:금액|요금|액)?|승인금액|받을금액|구매금액|총액|합계금액)",
        }
        # Select a final payment section only with an explicit payment anchor.
        rows = compact_text.splitlines()
        anchors = [i for i, row in enumerate(rows) if re.search(r"최종카드|최종결제|카드전표|신용카드매출전표", row)]
        final_text = "\n".join(rows[anchors[0]:]) if anchors else ""
        final_total = _labeled_amount(final_text, patterns["total_amount"])
        final_tax = _labeled_amount(final_text, tax_pattern)
        final_selected = final_total is not None
        scope = final_text if final_selected else compact_text
        conflicts = []
        for field, pattern in patterns.items():
            values = []
            token_pattern = re.compile(rf"(?:{pattern})[()\[\]:：]*(-?\d{{1,3}}(?:[,.]\d{{3}})+|-?\d{{1,8}})(?:원)?(?![\d*xX])", re.I)
            for match in token_pattern.finditer(scope):
                values.append(_receipt_number(match.group(1)) * (-1 if match.group(1).startswith('-') else 1))
            unique = list(dict.fromkeys(values))
            if len(unique) > 1:
                conflicts.append(field)
            if final_selected or unique:
                evidence[field] = unique[0] if len(unique) == 1 else None
        evidence["resolution_context"] = {
            "conflicts": conflicts,
            "final_payment_selected": final_selected,
            "final_payment_tax": final_tax if final_selected else None,
        }
        if final_selected:
            evidence["labels"] = {
                key: bool(re.search(patterns[field], scope, re.I))
                for key, field in (("supply", "supply_amount"), ("taxable_supply", "taxable_supply_amount"),
                                   ("tax_exempt", "tax_exempt_amount"), ("tax", "tax_amount"), ("total", "total_amount"))
            }
    return evidence



def _reconcile_amounts(result: dict[str, Any], text: str) -> dict[str, Any]:
    """Resolve taxes from OCR first, then guarded deterministic arithmetic."""
    evidence = _extract_amount_evidence(text, include_context=True)
    context = evidence["resolution_context"]
    compact = re.sub(r"\s+", "", text).upper()
    explicit_supply = evidence["supply_amount"]
    taxable = evidence["taxable_supply_amount"]
    exempt = evidence["tax_exempt_amount"]
    explicit_tax = evidence["tax_amount"]
    labels = evidence["labels"]
    reasons: list[str] = []
    trace: dict[str, Any] = {
        "policy": "explicit_ocr_then_components_then_guarded_arithmetic",
        "explicit": evidence, "changes": [], "review_reason": reasons,
        "tax_treatment": "UNKNOWN", "supply_source": "UNKNOWN", "tax_source": "UNKNOWN",
    }
    if evidence["total_amount"] is not None:
        result["total_amount"] = evidence["total_amount"]
        trace["changes"].append("total_from_explicit_ocr")
    total = _as_number(result.get("total_amount"))
    total_confirmed = total is not None and total >= 0 and (
        evidence["total_amount"] is not None or {_receipt_number(v) for v in _MONEY_RE.findall(text)} == {total}
    ) and "total_amount" not in context["conflicts"]
    mixed = ((taxable is not None and taxable > 0 and exempt is not None and exempt > 0)
             or bool(re.search(r"과세.{0,8}면세|면세.{0,8}과세", compact)))
    if taxable is not None and exempt is not None:
        mixed = taxable > 0 and exempt > 0
    incomplete = ((labels["taxable_supply"] and taxable is None)
                  or (labels["tax_exempt"] and exempt is None))
    extras = bool(re.search(r"교육세|봉사료|관광진흥기금|기금|수수료", compact))
    discount = bool(re.search(r"할인|쿠폰", compact))
    ambiguous_bus = bool(re.search(r"시외[/·ㆍ,또는]+고속|고속[/·ㆍ,또는]+시외", compact))
    taxable_transport = bool(re.search(r"택시|(?<![A-Z])(?:KTX|SRT)(?![A-Z])|고속철도|시외(?:우등|고급)고속|고속버스|우등고속|항공(?:기|권|운임)|전세버스", compact))
    exempt_only = bool(re.search(r"(?:도서|책|면세상품)(?:만구매|단독|만결제)", compact))
    exempt_only = exempt_only or (exempt is not None and exempt == total and not (taxable or explicit_tax))
    exempt_only = exempt_only or bool(re.search(r"전액면세|면세전용|도서.*면세|면세.*도서", compact))
    if re.search(r"문구|볼펜|노트|완구|음료|커피|잡화", compact):
        exempt_only = False
    treatment = "UNKNOWN"
    if not mixed and not incomplete:
        if exempt_only and not (explicit_tax or taxable or taxable_transport):
            treatment = "EXEMPT"
        elif (explicit_tax is not None and explicit_tax > 0) or (taxable is not None and taxable > 0) or (taxable_transport and not ambiguous_bus):
            treatment = "TAXABLE"
    trace["tax_treatment"] = treatment
    result["supply_amount"] = explicit_supply
    result["tax_amount"] = explicit_tax
    if explicit_supply is not None:
        trace["supply_source"] = "EXPLICIT_OCR"
        trace["changes"].append("supply_from_explicit_ocr")
    if explicit_tax is not None:
        trace["tax_source"] = "EXPLICIT_OCR"
        trace["changes"].append("tax_from_explicit_ocr")
    if explicit_supply is None and taxable is not None and not incomplete:
        result["supply_amount"] = taxable + (exempt or 0)
        trace["supply_source"] = "TAXABLE_PLUS_EXEMPT_OCR"
        trace["changes"].append("supply_from_taxable_plus_exempt_ocr")
    elif explicit_supply is None and exempt is not None and not labels["taxable_supply"]:
        result["supply_amount"] = exempt
        trace["supply_source"] = "EXPLICIT_OCR"
    if context["conflicts"]:
        reasons.append("OCR_AMOUNT_CONFLICT")
    if not total_confirmed:
        reasons.append("TOTAL_AMOUNT_UNCONFIRMED")
    if extras:
        reasons.append("ADDITIONAL_TAX_COMPONENTS")
    if incomplete or (mixed and (taxable is None or exempt is None)):
        reasons.append("MIXED_TAX_COMPONENTS_UNRESOLVED")
    # A discount summary is insufficient to infer the final taxable base.
    discount_blocked = discount and context["final_payment_tax"] is None
    if discount_blocked:
        reasons.append("DISCOUNT_TAX_BASIS_UNCLEAR")
    guarded = total_confirmed and not (extras or incomplete or context["conflicts"] or discount_blocked)
    if guarded and not mixed:
        if treatment == "TAXABLE" and result["supply_amount"] is None:
            if explicit_tax is not None and 0 <= explicit_tax <= total:
                result["supply_amount"] = total - explicit_tax
                trace["supply_source"] = "DERIVED_TAXABLE_TOTAL"
                trace["changes"].append("supply_from_guarded_arithmetic")
            elif explicit_tax is None and not labels["tax"] and not re.search(r"VAT별도|부가세별도|세금별도", compact):
                result["tax_amount"] = round(total / 11)
                result["supply_amount"] = total - result["tax_amount"]
                trace["supply_source"] = trace["tax_source"] = "DERIVED_TAXABLE_TOTAL"
                trace["changes"].append("tax_and_supply_from_taxable_total")
        elif treatment == "EXEMPT":
            if result["supply_amount"] is None:
                result["supply_amount"] = total
                trace["supply_source"] = "DERIVED_EXEMPT_TOTAL"
                trace["changes"].append("supply_from_exempt_total")
            if explicit_tax is None:
                result["tax_amount"] = 0
                trace["tax_source"] = "EXEMPT_ZERO"
                trace["changes"].append("tax_zero_from_exempt_only_ocr")
    if treatment == "UNKNOWN" and not (result["supply_amount"] is not None and explicit_tax is not None):
        reasons.append("TAX_TREATMENT_UNKNOWN")
    if result["supply_amount"] is None or result["tax_amount"] is None:
        reasons.append("TAX_AMOUNTS_UNRESOLVED")
    if explicit_tax is not None and not any(evidence[f] is not None for f in ("total_amount", "supply_amount", "taxable_supply_amount", "tax_exempt_amount")):
        trace["rejected_tax_amount"] = {"value": explicit_tax, "reason": "missing_total_or_supply_cross_check"}
        reasons.append("TAX_EVIDENCE_UNCORROBORATED")
    supply, tax = result["supply_amount"], result["tax_amount"]
    if total is not None and supply is not None and tax is not None:
        if supply < 0 or tax < 0 or abs(supply + tax - total) > AMOUNT_ROUNDING_TOLERANCE:
            reasons.append("AMOUNT_RELATION_MISMATCH")
    result["amount_resolution"] = trace
    return trace



def _simple_validation(result: dict[str, Any], text: str) -> dict[str, Any]:
    reasons: list[str] = []
    warnings: list[dict[str, Any]] = []
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
    supply, tax, raw_discount = (
        _as_number(result.get("supply_amount")),
        _as_number(result.get("tax_amount")),
        _as_number(result.get("discount_amount")),
    )
    # Receipts and models may represent a discount as either 1,200 or -1,200.
    # Validation treats both as a 1,200 reduction without rewriting the OCR value.
    discount = abs(float(raw_discount)) if raw_discount is not None else 0.0
    evidence = _extract_amount_evidence(text)
    labels = evidence["labels"]
    if (labels["supply"] or labels["taxable_supply"] or labels["tax_exempt"]) and supply is None:
        reasons.append("SUPPLY_AMOUNT_UNRESOLVED")
    if labels["tax"] and tax is None:
        reasons.append("TAX_AMOUNT_UNRESOLVED")
    amount_resolution = result.get("amount_resolution")
    if isinstance(amount_resolution, dict):
        reasons.extend(amount_resolution.get("review_reason", []))
    if isinstance(amount_resolution, dict) and amount_resolution.get("rejected_tax_amount"):
        reasons.append("TAX_EVIDENCE_UNCORROBORATED")
    if supply is not None and tax is not None and total is not None:
        tax_summary = float(supply) + float(tax)
        direct_delta = abs(tax_summary - float(total))
        discounted_delta = abs(tax_summary - discount - float(total)) if discount else None
        if direct_delta <= AMOUNT_ROUNDING_TOLERANCE:
            amount_relation_basis = "post_discount_tax_summary"
        elif discounted_delta is not None and discounted_delta <= AMOUNT_ROUNDING_TOLERANCE:
            amount_relation_basis = "pre_discount_tax_summary"
        else:
            amount_relation_basis = "explicit_ocr_mismatch" if (
                evidence.get("tax_amount") is not None
                and any(evidence.get(field) is not None for field in (
                    "supply_amount", "taxable_supply_amount", "tax_exempt_amount"
                ))
            ) else "unresolved_mismatch"
            reasons.append("AMOUNT_RELATION_MISMATCH")
    else:
        # A missing supply or tax value is valid partial information. There is no
        # three-way relation to check and the missing side must not be inferred.
        amount_relation_basis = "not_checkable_partial_amounts"
    item_amounts = [_as_number(item.get("total_amount")) for item in result["items"]]
    if result["items"] and all(value is not None for value in item_amounts) and total is not None:
        item_sum = sum(float(value) for value in item_amounts if value is not None)
        if (
            abs(item_sum - discount - float(total)) > AMOUNT_ROUNDING_TOLERANCE
            and abs(item_sum - float(total)) > AMOUNT_ROUNDING_TOLERANCE
        ):
            reasons.append("ITEM_SUM_MISMATCH")
    for item_index, item in enumerate(result["items"]):
        quantity = _as_number(item.get("quantity"))
        unit_price = _as_number(item.get("unit_price"))
        item_total = _as_number(item.get("total_amount"))
        if quantity is not None and unit_price is not None and item_total is not None:
            calculated = float(quantity) * float(unit_price)
            if abs(calculated - float(item_total)) > 1:
                # Coupons and bundle/member discounts commonly make the displayed
                # unit price differ from the charged line amount. Preserve every
                # extracted value and expose only a non-blocking diagnostic.
                warnings.append({
                    "code": "ITEM_AMOUNT_RELATION_WARNING",
                    "item_index": item_index,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "item_total_amount": item_total,
                    "calculated_amount": int(calculated) if calculated.is_integer() else calculated,
                })

    reasons = list(dict.fromkeys(reasons))
    return {
        "decision": "REVIEW" if reasons else "PASS",
        "reasons": reasons,
        "warnings": warnings,
        "missing_fields": missing,
        "checks": {
            "json_schema": "PASS",
            "total_grounded": bool(total is not None and _amount_is_grounded(total, text)),
            "amount_relation": "AMOUNT_RELATION_MISMATCH" not in reasons,
            "amount_relation_basis": amount_relation_basis,
            "item_sum": "ITEM_SUM_MISMATCH" not in reasons,
            "item_arithmetic": True,
            "item_amount_relation_warning": bool(warnings),
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
                "pipeline_version": RECEIPT_PIPELINE_VERSION,
                "model_name": model_name, "prompt_version": FINANCE_PROMPT_VERSION,
                "call_count": 0, "call_status": "skipped_preflight_review",
                "input_chars": 0, "output_chars": 0, "latency_ms": 0, "ollama": {},
                "raw_output": None,
            },
            "_model_name": model_name,
        }

    prompt, input_diagnostics = _simple_receipt_prompt(text, filename, pages)
    started = perf_counter()
    raw = await _generate_receipt_json(
        prompt,
        json_format=True,
        num_predict=RECEIPT_LLM_NUM_PREDICT,
        model_name=model_name,
        request_timeout_seconds=RECEIPT_LLM_TIMEOUT_SECONDS,
        keep_alive=RECEIPT_LLM_KEEP_ALIVE,
        num_ctx=RECEIPT_LLM_NUM_CTX,
    )
    latency_ms = round((perf_counter() - started) * 1000)
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("receipt JSON object expected")
    _reconcile_amounts(parsed, text)
    parsed["payment_method"], parsed["payment_method_evidence"] = _payment_from_ocr(text)
    parsed["item_grounding"] = ground_items(parsed.get("items"), text, pages)
    parsed["automation_validation"] = _simple_validation(parsed, text)
    parsed["llm_trace"] = {
        "pipeline_version": RECEIPT_PIPELINE_VERSION,
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
                "pipeline_version": RECEIPT_PIPELINE_VERSION,
                "model_name": RECEIPTS_MODEL_NAME,
                "prompt_version": FINANCE_PROMPT_VERSION,
                "call_count": 1,
                "call_status": "failed",
                "raw_output": None,
            },
            "_model_name": "rules-fallback",
        }


def _payment_from_ocr(text: str) -> tuple[str | None, dict[str, Any]]:
    """Use transaction evidence only; model guesses never fill missing evidence."""
    evidence: dict[str, list[str]] = {"카드": [], "현금": []}
    for raw in str(text or "").splitlines():
        line = re.sub(r"\s+", "", raw).lower()
        if not line or re.search(r"환불|반품|교환|혜택|프로모션|적립|할인행사|가능|안내|미발급|발급불가|발급대상", line):
            continue
        if re.search(r"(?:카드|현금|cash)(?:결제|금액)?[:：]?(?:0|0\.00)원?$", line):
            continue
        card = bool(re.search(r"신용카드|신용[.·]?승인|체크카드|카드(?:결제|승인|번호)|(?:신한|현대|삼성|롯데|국민|하나|우리|농협|비씨|bc)카드|credit|debit", line))
        card = card or bool(re.fullmatch(r"카드(?:[:：]?[0-9,]+원?)?", line))
        cash = bool(re.search(r"현금영수증|현금(?:결제|수납)|(?<![a-z])cash(?![a-z])", line))
        cash = cash or bool(re.fullmatch(r"현금(?:[:：]?[0-9,]+원?)?", line))
        if card:
            evidence["카드"].append(raw.strip())
        if cash:
            evidence["현금"].append(raw.strip())
    matches = [method for method, rows in evidence.items() if rows]
    return (matches[0] if len(matches) == 1 else None), {
        "policy": "ocr_card_cash_or_null",
        "reason": "unique_evidence" if len(matches) == 1 else "conflicting_evidence" if matches else "missing_evidence",
        "evidence": evidence,
    }


def _normalize(result: dict[str, Any], filename: str, text: str) -> dict[str, Any]:
    """Minimal normalization: format values, validate, and never auto-repair."""
    validation = result.get("automation_validation")
    if not isinstance(validation, dict):
        validation = _simple_validation(result, text)
        result["automation_validation"] = validation
    category = _normalize_expense_category(result.get("expense_category"), text)
    items = _clean_model_items(result.get("items"))
    classification = classify_document_type({
        "expense_category": category,
        "merchant": result.get("merchant"),
        "items": items,
        "transaction_description": result.get("transaction_description"),
    })
    document_type = classification["selected_document_type"]
    result["classification_decision"] = classification
    # Preserve extraction failures and append routing review reasons before DB save.
    if classification["status"] == "REVIEW":
        validation["decision"] = "REVIEW"
        validation["reasons"] = list(dict.fromkeys([
            *(validation.get("reasons") or []), *classification["reasons"],
        ]))
    validation.setdefault("checks", {})["document_classification"] = classification["status"]
    total_quantity = None
    if items and all(item.get("quantity") is not None for item in items):
        total_quantity = sum(float(item["quantity"]) for item in items)
        if total_quantity.is_integer():
            total_quantity = int(total_quantity)
    grounded_card, card_evidence = _ground_masked_card_number(None, text)
    payment_method, payment_evidence = _payment_from_ocr(text)
    result["payment_method_evidence"] = payment_evidence
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
        "payment_method": payment_method,
    })
    merchant = " ".join(str(result.get("merchant") or "").split())[:300] or None
    structured = result
    return {
        "document_type": document_type,
        "expense_category": category,
        "merchant": merchant,
        "transaction_date": result.get("transaction_date"),
        "supply_amount": _as_number(result.get("supply_amount")),
        "tax_amount": _as_number(result.get("tax_amount")),
        "total_amount": _as_number(result.get("total_amount")) or 0,
        "payment_method": payment_method,
        "description": merchant,
        "structured_data": structured,
        "model_name": str(result.get("_model_name") or RECEIPTS_MODEL_NAME),
        "status": "REVIEW",
    }


__all__ = [name for name in globals() if not name.startswith("__")]
