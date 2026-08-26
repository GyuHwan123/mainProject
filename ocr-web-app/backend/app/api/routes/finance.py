from __future__ import annotations

import json
import hashlib
import logging
import re
from datetime import date, datetime, timezone
from io import BytesIO
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.routes.auth import require_current_user
from app.api.routes.chatbot import generate
from app.core.config import settings
from app.models.user import User
from app.services.finance_workbook_service import build_finance_workbook
from app.services.supabase_service import supabase_service

router = APIRouter()
logger = logging.getLogger(__name__)
DocumentType = Literal["EXPENSE_REPORT", "TRAVEL_EXPENSE", "PURCHASE_REQUEST", "WELFARE_BENEFIT"]
FINANCE_PROMPT_VERSION = "receipt-v3-evidence-and-item-semantics"
RECEIPTS_MODEL_NAME = settings.RECEIPTS_LLM_MODEL

_ITEM_COLUMN_LABEL = r"(?:상품\s*코드|상품\s*명|품\s*명|품목\s*명|단가|수량|금액|합계금액)"
_ITEM_COLUMN_HEADER = re.compile(rf"(?:{_ITEM_COLUMN_LABEL}[\s|:/·-]*){{2,}}", re.IGNORECASE)


def _clean_item_name_evidence(value: Any) -> tuple[str, list[str]]:
    """Remove only recognizable metadata/header prefixes from an item name."""
    original = " ".join(str(value or "").strip().split())
    cleaned = original
    reasons: list[str] = []

    headers = list(_ITEM_COLUMN_HEADER.finditer(cleaned))
    if headers:
        suffix = cleaned[headers[-1].end():].strip(" |:/·-")
        if re.search(r"[A-Za-z가-힣]", suffix):
            cleaned = suffix
            reasons.append("embedded_item_header_removed")

    metadata_prefix = re.compile(
        r"^(?:(?:\[?\s*판매\s*(?:일시|일자|번호|매)?\s*\]?|거래\s*(?:일시|번호)|"
        r"포스\s*(?:번호)?|pos\s*(?:no\.?)?)\s*[:#]?\s*"
        r"(?:20\d{2}[-./]\d{1,2}[-./]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?|"
        r"[0-9A-Za-z-]{3,})\s*)+",
        re.IGNORECASE,
    )
    without_metadata = metadata_prefix.sub("", cleaned).strip(" |:/·-")
    if without_metadata and without_metadata != cleaned:
        cleaned = without_metadata
        reasons.append("transaction_metadata_removed")

    return cleaned, reasons


def _structure_item_name(value: Any) -> dict[str, Any]:
    """Split a displayed item name into canonical name and typed metadata."""
    name = " ".join(str(value or "").strip().split())
    aliases: list[str] = []
    specifications: list[str] = []
    options: list[str] = []

    # A parenthesized English rendering after a Korean product name is an
    # alias, not an additional part of the canonical product name.
    if re.search(r"[가-힣]", name):
        def remove_english_alias(match: re.Match[str]) -> str:
            content = match.group(1).strip()
            if re.fullmatch(r"[A-Za-z][A-Za-z .&'/-]*", content):
                aliases.append(content)
                return ""
            return match.group(0)
        name = re.sub(r"\(([^()]*)\)", remove_english_alias, name)

    # SKU/color selections belong in specification, while size expressions
    # such as 1볼/50g remain part of the evaluated product name.
    def remove_option(match: re.Match[str]) -> str:
        options.append(match.group(1).strip())
        return ""
    name = re.sub(
        # A 3+ digit identifier followed by an option description is an SKU/
        # colour selection. Keep short quantity specifications such as
        # ``1볼/50g`` in the evaluated display name.
        r"\(\s*(\d{3,8}(?:\s+|[-_/])[^()]*)\s*\)",
        remove_option,
        name,
        flags=re.IGNORECASE,
    )
    canonical_name = " ".join(name.split())
    return {
        "canonical_name": canonical_name,
        "aliases": aliases,
        "specifications": specifications,
        "options": options,
    }


def _separate_item_name_metadata(value: Any) -> tuple[str, list[str]]:
    """Backward-compatible wrapper used by existing callers and tests."""
    structured = _structure_item_name(value)
    separated = [
        *structured["aliases"],
        *structured["specifications"],
        *structured["options"],
    ]
    return structured["canonical_name"], separated


class FinanceClassifyRequest(BaseModel):
    document_id: str


class FinanceExportRequest(BaseModel):
    record_ids: list[str] = Field(min_length=1, max_length=200)


class FinanceRecordUpdate(BaseModel):
    document_type: DocumentType
    expense_category: str = Field(min_length=1, max_length=100)
    merchant: str | None = Field(default=None, max_length=200)
    transaction_date: date | None = None
    supply_amount: float = Field(default=0, ge=0)
    tax_amount: float = Field(default=0, ge=0)
    total_amount: float = Field(default=0, ge=0)
    payment_method: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    status: Literal["REVIEW", "CONFIRMED"] = "CONFIRMED"


class FinanceRecord(BaseModel):
    id: str
    document_id: str
    document_type: DocumentType
    expense_category: str
    merchant: str | None = None
    transaction_date: date | None = None
    supply_amount: float = 0
    tax_amount: float = 0
    total_amount: float = 0
    payment_method: str | None = None
    description: str | None = None
    structured_data: dict[str, Any] = Field(default_factory=dict)
    model_name: str
    prompt_version: str | None = None
    duplicate_of_record_id: str | None = None
    processed_at: datetime | None = None
    status: str
    created_at: datetime


def _clean_number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(float(value), 0)
    text = "".join(character for character in str(value or "") if character.isdigit() or character in ".-")
    try:
        return max(float(text), 0)
    except ValueError:
        return 0


def _receipt_number(value: str) -> int:
    value = re.sub(r"\s+", "", value.strip())
    if not value:
        return 0
    # Korean receipts commonly use both commas and periods as thousands separators.
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", value):
        return int(re.sub(r"[.,]", "", value))
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else 0


def _receipt_fingerprint(text: str) -> str:
    canonical = re.sub(r"[^0-9a-z가-힣]", "", text.lower())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _receipt_identity_key(text: str, hints: dict[str, Any]) -> str | None:
    reference = re.search(r"(?:승인\s*번호|거래\s*번호|주문\s*번호)\s*[:：]?\s*([0-9A-Za-z*-]{4,})", text, re.IGNORECASE)
    if not reference:
        return None
    reference_value = re.sub(r"[^0-9A-Za-z]", "", reference.group(1)).lower()
    if len(reference_value) < 4:
        return None
    raw = f"{reference_value}|{hints.get('transaction_date') or ''}|{hints.get('total_amount') or 0}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _legacy_receipt_key(record: dict[str, Any]) -> str | None:
    data = record.get("structured_data") or {}
    filename = re.sub(r"\s*\(\d+\)(?=\.[^.]+$)", "", str(data.get("source_filename") or "").lower())
    filename = re.sub(r"[^0-9a-z가-힣.]", "", filename)
    transaction_date = str(record.get("transaction_date") or "")
    total = round(float(record.get("total_amount") or 0), 2)
    supply = round(float(record.get("supply_amount") or 0), 2)
    tax = round(float(record.get("tax_amount") or 0), 2)
    if not filename or not transaction_date or total <= 0:
        return None
    return f"{filename}|{transaction_date}|{supply}|{tax}|{total}"


def _mark_duplicate(record: dict[str, Any]) -> dict[str, Any]:
    duplicate = dict(record)
    structured_data = dict(duplicate.get("structured_data") or {})
    structured_data["duplicate_detection"] = {"is_duplicate": True, "message": "이미 문서화된 영수증입니다."}
    duplicate["structured_data"] = structured_data
    return duplicate


def _receipt_hints(text: str, filename: str) -> dict[str, Any]:
    # OCR often removes the space between date and time (2025-10-0516:50).
    # The separators make the date boundary unambiguous, so parse the optional
    # attached time instead of rejecting a digit immediately after the day.
    # Accept numeric dates and Korean receipt notation such as
    # ``2016년09월 18일(일)12:55``. This deterministic hint overrides any
    # conflicting date generated by the LLM in ``_normalize``.
    date_match = re.search(
        r"(?<!\d)(20\d{2})\s*(?:년\s*|[-./]\s*)(\d{1,2})\s*(?:월\s*|[-./]\s*)(\d{1,2})\s*일?(?:\s*\([^)]*\))?(?:\s*\d{1,2}:\d{2})?",
        text,
    )
    transaction_date = None
    if date_match:
        try:
            transaction_date = date(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))).isoformat()
        except ValueError:
            pass
    if transaction_date is None:
        short_date_match = re.search(
            r"(?<!\d)(\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})"
            r"(?:\s*(?:오전|오후)?\s*\d{1,2}:\d{2}(?::\d{2})?)?(?!\d)",
            text,
        )
        if short_date_match:
            try:
                transaction_date = date(
                    2000 + int(short_date_match.group(1)),
                    int(short_date_match.group(2)),
                    int(short_date_match.group(3)),
                ).isoformat()
            except ValueError:
                pass

    amount_tokens = re.findall(r"(?<!\d)(\d{1,3}(?:[.,]\d{3})+|\d{3,8})(?!\d)", text)
    amounts = sorted({amount for token in amount_tokens if 100 <= (amount := _receipt_number(token)) <= 100_000_000})
    won_amounts = [_receipt_number(token) for token in re.findall(r"(\d[\d.,\s]{0,15})\s*원", text)]
    won_amounts = [amount for amount in won_amounts if 100 <= amount <= 100_000_000]
    triples: list[tuple[int, int, int]] = []
    for total in sorted(set(won_amounts + amounts), reverse=True):
        for supply in amounts:
            for tax in amounts:
                if supply >= tax and supply + tax == total:
                    triples.append((supply, tax, total))
    supply = tax = total = 0
    if triples:
        supply, tax, total = max(triples, key=lambda triple: triple[2])
    elif won_amounts:
        total = max(won_amounts)

    def labeled_amount(labels: str) -> int | None:
        match = re.search(
            rf"(?:{labels})\s*[:：]?\s*(?:금액\s*)?([0-9]{{1,3}}(?:\s*[.,]\s*[0-9]{{3}})+|[0-9]{{3,8}})\s*원?",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        value = _receipt_number(match.group(1))
        return value if value >= 100 else None

    labeled_final = labeled_amount(
        r"최종\s*결제(?:\s*금액)?|실\s*결제(?:\s*금액)?|받을\s*금액|"
        r"승인(?:\s*금액|\s*액)|결제(?:\s*금액|\s*액)|청구(?:\s*금액|\s*액)|"
        r"신용\s*판매(?:\s*금액|\s*액)|현금\s*결제(?:\s*금액)?|카드\s*결제(?:\s*금액)?"
    )
    labeled_discount = labeled_amount(r"할인(?:\s*금액|\s*액)?|쿠폰(?:\s*할인)?")
    labeled_gross = labeled_amount(r"정가|할인\s*전(?:\s*금액)?|상품\s*합계|총\s*상품\s*금액")
    if labeled_final:
        total = labeled_final
    amount_relation = None
    if labeled_discount and labeled_final and labeled_gross and labeled_gross - labeled_discount == labeled_final:
        amount_relation = {
            "type": "GROSS_MINUS_DISCOUNT_EQUALS_PAID",
            "gross_amount": labeled_gross,
            "discount_amount": labeled_discount,
            "paid_amount": labeled_final,
        }

    name = filename.lower()
    document_type = None
    expense_category = None
    if any(keyword in name for keyword in ("출장", "여비", "교통", "숙박", "ktx", "srt", "택시")):
        document_type = "TRAVEL_EXPENSE"
        if any(keyword in name for keyword in ("식비", "식대", "음료", "카페")):
            expense_category = "일비/식대"
        elif "숙박" in name:
            expense_category = "숙박비"
        else:
            expense_category = "교통비"
    elif any(keyword in name for keyword in ("복지", "도서", "교육", "병원", "검진", "경조")):
        document_type = "WELFARE_BENEFIT"
    elif any(keyword in name for keyword in ("구매", "견적", "비품", "장비", "소프트웨어", "라이선스")):
        document_type = "PURCHASE_REQUEST"

    stated_item_count = None
    stated_total_quantity = None
    stated_total_amount = None
    item_count_match = re.search(
        r"총\s*품목(?:\s*수)?\s*[/／]\s*총\s*수량[^\d]{0,80}(\d{1,3})\s*개?\s*[/／]\s*(\d{1,3})\s*개?",
        text,
    )
    count_value_first = False
    if not item_count_match:
        item_count_match = re.search(
            r"(\d{1,3})\s*개?\s*[/／]\s*(\d{1,3})\s*개?[^\d]{0,80}총\s*품목(?:\s*수)?\s*[/／]\s*총\s*수량",
            text,
        )
        count_value_first = item_count_match is not None
    if item_count_match:
        stated_item_count = int(item_count_match.group(1))
        stated_total_quantity = int(item_count_match.group(2))

        # Many receipts print the purchase total immediately after the
        # ``item count / total quantity`` pair. Treat it as a deterministic
        # summary value only when a money-shaped token follows nearby.
        summary_tail = text[item_count_match.end():item_count_match.end() + 80] if not count_value_first else text[item_count_match.start():item_count_match.start() + 120]
        stated_amount_match = re.search(r"(?<!\d)(\d{1,3}(?:[.,]\d{3})+|\d{3,8})(?!\d)", summary_tail)
        if stated_amount_match:
            candidate = _receipt_number(stated_amount_match.group(1))
            if candidate >= 100:
                stated_total_amount = candidate

    payment_method = None
    payment_method_rejected_by_policy = False
    card_transaction_pattern = (
        r"카드\s*(?:결제|승인|매출표)|신용\s*카드|체크\s*카드|"
        r"신용\s*(?:승인|송인|매출표)"
    )
    if re.search(card_transaction_pattern, text, re.IGNORECASE):
        payment_method = "카드"
    elif re.search(r"현금\s*(?:결제|영수증)|현금영수증", text, re.IGNORECASE):
        payment_method = "현금"

    # Preserve the existing broad card detection, then suppress it only when
    # every card mention belongs to refund/exchange/cancellation instructions.
    policy_context = re.compile(r"환불|환급|취소|교환|반품|지참|소요|최대\s*\d+\s*일", re.IGNORECASE)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    policy_card_found = any(policy_context.search(line) and re.search(r"카드", line, re.IGNORECASE) for line in lines)
    transactional_card_found = any(
        not policy_context.search(line)
        and re.search(
            r"카드\s*(?:결제|승인)(?:액|금액|번호)?|카드\s*매출표|"
            r"신용\s*(?:판매액|승인|송인|매출표)",
            line,
            re.IGNORECASE,
        )
        for line in lines
    )
    if payment_method == "카드" and policy_card_found and not transactional_card_found:
        payment_method = None
        payment_method_rejected_by_policy = True

    return {
        "transaction_date": transaction_date,
        "supply_amount": supply,
        "tax_amount": tax,
        "total_amount": total,
        "discount_amount": labeled_discount,
        "amount_relation": amount_relation,
        "document_type": document_type,
        "expense_category": expense_category,
        "stated_item_count": stated_item_count,
        "stated_total_quantity": stated_total_quantity,
        "stated_total_amount": stated_total_amount,
        "payment_method": payment_method,
        "payment_method_rejected_by_policy": payment_method_rejected_by_policy,
    }


def _normalize_merchant(value: Any, text: str) -> str | None:
    """Correct only high-confidence tenant/facility merchant confusion.

    OCR table flattening can place a mall name or domain next to a tenant
    brand. Keep normal model judgments intact and apply aliases only when the
    merchant is missing, is the same brand with a tax suffix, or is a known
    host-facility name.
    """
    merchant = str(value or "").strip()
    compact_merchant = re.sub(r"[^0-9a-z가-힣]", "", merchant.lower())
    compact_text = re.sub(r"[^0-9a-z가-힣]", "", text.lower())
    tenant_aliases = {
        "유니클로": ("유니클로", "uniqlo"),
    }
    host_facilities = {
        "starfield", "starfiled", "스타필드",
        "starfieldcoex", "starfiledcoex", "스타필드코엑스",
    }

    for canonical, aliases in tenant_aliases.items():
        if not any(alias in compact_text for alias in aliases):
            continue
        is_same_tenant = any(alias in compact_merchant for alias in aliases)
        is_host_facility = any(host in compact_merchant for host in host_facilities)
        if not merchant or is_same_tenant or is_host_facility:
            return canonical
    return merchant[:200] or None


def _normalize_payment_method(value: Any, evidenced_value: Any, rejected_by_policy: bool = False) -> str | None:
    """Prefer an explicit OCR payment label over missing/conflicting LLM text."""
    model_value = str(value or "").strip()
    hint_value = str(evidenced_value or "").strip()
    placeholders = {"-", "--", "없음", "미확인", "확인필요", "null", "none", "n/a", "na"}
    if model_value.lower().replace(" ", "") in placeholders:
        model_value = ""

    if rejected_by_policy:
        return None

    if hint_value == "현금":
        return "현금"
    if hint_value == "카드":
        # Preserve useful issuer/card-type detail only when it agrees with the
        # OCR evidence that this was a card payment.
        if re.search(r"카드|card", model_value, re.IGNORECASE):
            return model_value[:100]
        return "카드"
    return model_value[:100] or None


def _validator_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    """Capture values observed by validation without changing validation behavior."""
    summary_fields = (
        "doc_type", "document_type", "expense_category", "merchant", "transaction_date",
        "supply_amount", "tax_amount", "discount_amount", "total_amount", "payment_method",
        "card_number", "description", "receipt_summary", "items",
    )
    return {
        key: json.loads(json.dumps(result.get(key), ensure_ascii=False))
        for key in summary_fields
        if key in result
    }


def _validator_trace(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(set(before) | set(after))
    changes = [
        {"field": key, "before": before.get(key), "after": after.get(key)}
        for key in keys
        if before.get(key) != after.get(key)
    ]
    return {
        "validator": "finance._normalize",
        "version": FINANCE_PROMPT_VERSION,
        "input": before,
        "output": after,
        "changed": bool(changes),
        "changes": changes,
    }


def _normalize(result: dict[str, Any], filename: str, text: str) -> dict[str, Any]:
    validator_input = _validator_snapshot(result)
    hints = _receipt_hints(text, filename)
    receipt_summary = result.get("receipt_summary") if isinstance(result.get("receipt_summary"), dict) else {}
    stated_item_count = hints.get("stated_item_count") or _clean_number(receipt_summary.get("stated_item_count"))
    items = result.get("items") if isinstance(result.get("items"), list) else []
    non_item_labels = {
        "카드결제액", "카드승인금액", "승인금액", "결제금액", "최종결제금액",
        "받을금액", "총결제액", "총구매금액", "결제수단", "승인번호", "현금영수증",
        "상품합계", "소계", "합계", "공급가액", "부가세", "부가세액", "할인금액",
        "할인액", "쿠폰", "적립금", "거스름돈", "카드번호", "사업자번호",
    }

    def is_real_item(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        name = re.sub(r"[^0-9a-z가-힣]", "", str(item.get("name") or "").lower())
        if not name or any(name == label or name.startswith(label) for label in non_item_labels):
            return False
        raw_name = str(item.get("name") or "").strip()
        if re.fullmatch(r"(?:https?://|www\.)\S+", raw_name, re.IGNORECASE):
            return False
        if re.fullmatch(r"(?:현대\s*HDS|CAT\s*ID|승인\s*번호|X)", raw_name, re.IGNORECASE):
            return False
        return True

    items = [item for item in items if is_real_item(item)]
    item_diagnostics = result.get("item_extraction_diagnostics")
    is_card_sales_slip = bool(re.search(
        r"카드\s*매출표|신용\s*(?:승인|송인|매출표)",
        text,
        re.IGNORECASE,
    ))
    has_item_diagnostics = isinstance(item_diagnostics, dict)
    evidenced_candidates = item_diagnostics.get("candidates") if has_item_diagnostics else None
    # A card sales slip contains only the transaction amount/tax/approval
    # details. If the OCR structure found no item row, any model-created item
    # is unsupported and must not be written to the workbook.
    if is_card_sales_slip and has_item_diagnostics and not evidenced_candidates:
        if items:
            item_diagnostics["rejected_model_items"] = json.loads(json.dumps(items, ensure_ascii=False))
        item_diagnostics["items_rejected_reason"] = "card_sales_slip_without_ocr_item_candidates"
        items = []
    numeric_item_fields = ("quantity", "unit_price", "supply_amount", "tax_amount", "total_amount")
    for item in items:
        raw_name = str(item.get("name") or "").strip()
        cleaned_name, cleanup_reasons = _clean_item_name_evidence(raw_name)
        name_parts = _structure_item_name(cleaned_name)
        item["name"] = name_parts["canonical_name"]
        separated_name_metadata = [
            *name_parts["aliases"], *name_parts["specifications"], *name_parts["options"],
        ]
        if cleanup_reasons or separated_name_metadata:
            item["raw_name"] = raw_name
        if cleanup_reasons:
            item["name_cleanup"] = cleanup_reasons
        if separated_name_metadata:
            existing_specification = str(item.get("specification") or "").strip()
            item["specification"] = " · ".join(filter(None, [existing_specification, *separated_name_metadata]))
            item["name_metadata_separated"] = separated_name_metadata
        if name_parts["aliases"]:
            item["aliases"] = name_parts["aliases"]
        if name_parts["options"]:
            item["options"] = name_parts["options"]
        for field in numeric_item_fields:
            if item.get(field) is not None:
                raw_value = str(item[field]).strip()
                item[field] = (
                    float(_receipt_number(raw_value))
                    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw_value)
                    else _clean_number(item[field])
                )
        quantity = _clean_number(item.get("quantity"))
        item_total = _clean_number(item.get("total_amount"))
        if not _clean_number(item.get("unit_price")) and quantity > 0 and item_total > 0:
            item["unit_price"] = item_total / quantity
            item["note"] = " · ".join(filter(None, [str(item.get("note") or "").strip(), "품목금액÷수량으로 단가 복원"]))

    candidate_rows = (
        result.get("item_extraction_diagnostics", {}).get("candidates", [])
        if isinstance(result.get("item_extraction_diagnostics"), dict)
        else []
    )
    recoverable_candidates = [
        candidate for candidate in candidate_rows
        if isinstance(candidate, dict)
        and candidate.get("name_candidate")
        and _clean_number(candidate.get("quantity_candidate")) > 0
        and _clean_number(candidate.get("unit_price_candidate")) > 0
        and _clean_number(candidate.get("amount_candidate")) > 0
    ]
    evidenced_total = _clean_number(result.get("total_amount")) or _clean_number(hints.get("total_amount"))
    model_item_total = sum(_clean_number(item.get("total_amount")) for item in items)
    candidate_item_total = sum(_clean_number(candidate.get("amount_candidate")) for candidate in recoverable_candidates)
    if (
        evidenced_total > 0
        and recoverable_candidates
        and abs(candidate_item_total - evidenced_total) < 0.01
        and abs(model_item_total - evidenced_total) >= 0.01
    ):
        diagnostics = result.setdefault("item_extraction_diagnostics", {})
        diagnostics["rejected_model_items_after_total_validation"] = json.loads(json.dumps(items, ensure_ascii=False))
        diagnostics["items_resolution"] = "ocr_candidates_match_receipt_total"
        items = [{
            "name": str(candidate["name_candidate"]),
            "quantity": _clean_number(candidate.get("quantity_candidate")),
            "unit_price": _clean_number(candidate.get("unit_price_candidate")),
            "total_amount": _clean_number(candidate.get("amount_candidate")),
            "product_code": candidate.get("product_code"),
            "discount_amount": _clean_number(candidate.get("discount_amount_candidate")) or None,
            "note": "OCR 품목 후보 합계와 영수증 총액 일치로 복원",
            "item_resolution": "ocr_candidates_match_receipt_total",
        } for candidate in recoverable_candidates]

    # Narrow recovery for the known tenant receipt layout. In this layout OCR
    # exposes ``유니클로(과세/면세)`` beside the product header, while small
    # models sometimes consume the token only as merchant and return no item.
    # Recover it only for an empty, single-item-compatible result with a
    # positive evidenced receipt total; never invent additional item rows.
    uniqlo_item_match = re.search(r"유니클로\s*\(\s*(과세|면세)\s*\)", text)
    recovered_total = _clean_number(result.get("total_amount")) or _clean_number(hints.get("total_amount"))
    if (
        not items
        and uniqlo_item_match
        and (not stated_item_count or int(stated_item_count) == 1)
        and recovered_total >= 100
    ):
        item_name = f"유니클로({uniqlo_item_match.group(1)})"
        items.append({
            "name": item_name,
            "quantity": 1.0,
            "unit_price": recovered_total,
            "total_amount": recovered_total,
            "note": "OCR 근거 기반 단일 품목 복원",
        })
    result["items"] = items
    # Discount is a high-risk hallucination. Besides requiring an explicit OCR
    # label, verify it against the item gross and paid total whenever both are
    # available. Flattened OCR can otherwise associate a discount label with a
    # distant phone/order number on the page.
    evidenced_discount = hints.get("discount_amount")
    candidate_rows = (
        result.get("item_extraction_diagnostics", {}).get("candidates", [])
        if isinstance(result.get("item_extraction_diagnostics"), dict)
        else []
    )
    candidate_amounts = [
        _clean_number(candidate.get("amount_candidate"))
        for candidate in candidate_rows if isinstance(candidate, dict)
    ]
    candidate_gross = sum(value for value in candidate_amounts if value > 0)
    paid_total = _clean_number(result.get("total_amount")) or _clean_number(hints.get("total_amount"))
    expected_discount = candidate_gross - paid_total if candidate_gross >= paid_total > 0 else None
    discount_rejection = None
    if evidenced_discount is not None and expected_discount is not None:
        if expected_discount <= 0 or abs(_clean_number(evidenced_discount) - expected_discount) > 0.01:
            discount_rejection = "inconsistent_with_item_gross_and_paid_total"
            evidenced_discount = None
    result["discount_amount"] = evidenced_discount
    result["financial_evidence_diagnostics"] = {
        "candidate_gross_amount": candidate_gross or None,
        "paid_amount": paid_total or None,
        "expected_discount_amount": expected_discount if expected_discount and expected_discount > 0 else None,
        "accepted_discount_amount": evidenced_discount,
        "discount_rejection": discount_rejection,
    }
    # A lone count of 1 is too easy to confuse with nearby quantity/spec text
    # such as ``1볼/50g``. Only shorten multi-item output when a count of two
    # or more is supported by the receipt summary.
    if stated_item_count >= 2 and len(items) > int(stated_item_count):
        result["items"] = items[:int(stated_item_count)]
    if stated_item_count:
        receipt_summary.update({
            "stated_item_count": int(stated_item_count),
            "stated_total_quantity": hints.get("stated_total_quantity"),
            "stated_total_amount": hints.get("stated_total_amount"),
        })
        result["receipt_summary"] = receipt_summary
    allowed = {"EXPENSE_REPORT", "TRAVEL_EXPENSE", "PURCHASE_REQUEST", "WELFARE_BENEFIT"}
    document_type = str(
        hints.get("document_type")
        or result.get("doc_type")
        or result.get("document_type")
        or "EXPENSE_REPORT"
    ).upper()
    if document_type not in allowed:
        document_type = "EXPENSE_REPORT"
    def prefer_evidenced_model_amount(field: str) -> float:
        model_value = _clean_number(result.get(field))
        hint_value = _clean_number(hints.get(field))
        # Arithmetic hints can accidentally combine item subtotals. Preserve a
        # plausible model value when that exact amount is present in OCR; use
        # the hint only to recover missing, implausibly scaled, or absent data.
        ocr_values = {
            _receipt_number(token)
            for token in re.findall(r"(?<!\d)(\d{1,3}(?:[.,]\d{3})+|\d{3,8})(?!\d)", text)
        }
        if model_value >= 100 and any(abs(model_value - value) < 0.01 for value in ocr_values):
            return model_value
        return hint_value or model_value

    supply = prefer_evidenced_model_amount("supply_amount")
    tax = prefer_evidenced_model_amount("tax_amount")
    total = prefer_evidenced_model_amount("total_amount") or supply + tax
    relation = hints.get("amount_relation")
    if isinstance(relation, dict) and relation.get("type") == "GROSS_MINUS_DISCOUNT_EQUALS_PAID":
        total = _clean_number(relation.get("paid_amount"))
        discount = _clean_number(relation.get("discount_amount"))
        if tax == discount and not re.search(r"부가\s*세|세액", text):
            tax = 0
        if supply and supply + tax != total and supply == total:
            tax = 0
    transaction_date = hints.get("transaction_date") or str(result.get("transaction_date") or "").strip() or None
    if transaction_date:
        try:
            date.fromisoformat(transaction_date)
        except ValueError:
            transaction_date = None
    result["source_filename"] = filename
    result["deterministic_hints"] = hints
    normalized_record = {
        "document_type": document_type,
        "expense_category": str(hints.get("expense_category") or result.get("expense_category") or "기타").strip()[:100],
        "merchant": _normalize_merchant(result.get("merchant"), text),
        "transaction_date": transaction_date,
        "supply_amount": supply,
        "tax_amount": tax,
        "total_amount": total,
        "payment_method": _normalize_payment_method(
            result.get("payment_method"),
            hints.get("payment_method"),
            bool(hints.get("payment_method_rejected_by_policy")),
        ),
        "description": str(result.get("description") or "").strip()[:1000] or None,
        "structured_data": result,
        "model_name": str(result.get("_model_name") or RECEIPTS_MODEL_NAME),
        "status": "REVIEW",
    }
    validator_output = _validator_snapshot(result)
    validator_output.update({
        key: normalized_record[key]
        for key in (
            "document_type", "expense_category", "merchant", "transaction_date",
            "supply_amount", "tax_amount", "total_amount", "payment_method", "description",
        )
    })
    result["validator_trace"] = _validator_trace(validator_input, validator_output)
    return normalized_record


def _receipt_table_hint(pages: list[dict[str, Any]] | None) -> str:
    tables = []
    for page in pages or []:
        for table_index, table in enumerate(page.get("tables") or [], start=1):
            if table.get("rows"):
                tables.append({
                    "page": page.get("page"),
                    "table": table_index,
                    "confidence": table.get("confidence"),
                    "rows": table["rows"],
                })
    return json.dumps(tables, ensure_ascii=False) if tables else "없음"


def _receipt_item_candidates(pages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Create compact item candidates without assuming physical column order."""
    summary_labels = re.compile(
        r"(?:합계|소계|결제|승인|공급가액|부가세|할인|쿠폰|적립|거스름|카드번호|사업자번호|판매번호|거래번호|주문번호|영수증번호|총품목|총수량)",
        re.IGNORECASE,
    )
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    non_item_row = re.compile(
        r"^(?:X|현대\s*HDS|CAT\s*ID|승인\s*번호|수\s*량|총\s*수량|계|합계|총합계)$",
        re.IGNORECASE,
    )

    def append_structured_candidate(candidate: dict[str, Any]) -> None:
        raw = " | ".join(str(value) for value in candidate.get("raw_cells", []) if value)
        dedupe_key = re.sub(r"[^0-9A-Za-z가-힣]", "", raw).lower()
        if not raw or dedupe_key in seen:
            return
        candidates.append(candidate)
        seen.add(dedupe_key)

    def discounted_item_pair(
        row: list[Any], next_row: list[Any], page_number: Any,
    ) -> dict[str, Any] | None:
        """Merge a retail price row with its following SKU/discount row."""
        first = " ".join(str(cell or "").strip() for cell in row if str(cell or "").strip())
        second = " ".join(str(cell or "").strip() for cell in next_row if str(cell or "").strip())
        if not first or not re.search(r"할인", second):
            return None
        first_amounts = [_receipt_number(value) for value in re.findall(r"\d{1,3}(?:[.,]\d{3})+|\d{3,8}", first)]
        signed_second = [
            int(re.sub(r"[,.]", "", value))
            for value in re.findall(r"-?\d{1,3}(?:[.,]\d{3})+|-?\d{3,8}", second)
        ]
        negative = [abs(value) for value in signed_second if value < 0]
        positive_money = [value for value in signed_second if value >= 100]
        if not first_amounts or not negative or not positive_money:
            return None
        unit_price = first_amounts[-1]
        final_amount = positive_money[-1]
        if unit_price - negative[-1] != final_amount:
            return None

        raw_name = str(row[0] or "").strip()
        # Metadata can be joined to the first product by borderless-table line
        # grouping. The actual product is the final textual fragment.
        raw_name = re.sub(
            r"^.*(?:시간\s*:\s*(?:오전|오후)?\s*\d{1,2}:\d{2}|P[O0]S\s*번호\s*:\s*\S+)\s*",
            "",
            raw_name,
            flags=re.IGNORECASE,
        ).strip()
        if len(raw_name) > 50:
            fragments = re.findall(r"[A-Za-z가-힣][A-Za-z가-힣 ]{1,30}", raw_name)
            raw_name = fragments[-1].strip() if fragments else raw_name
        option_source = str(next_row[0] or "").strip()
        sku_match = re.match(r"(\d{6,})(.*)", option_source)
        product_code = sku_match.group(1) if sku_match else None
        option = (sku_match.group(2) if sku_match else option_source)
        option = re.split(r"\s*할인", option, maxsplit=1)[0].strip()
        name = " ".join(value for value in (raw_name, option) if value)
        if not re.search(r"[A-Za-z가-힣]", name):
            return None
        candidate = {
            "page": page_number,
            "source": "discounted_item_block",
            "raw_cells": [first, second],
            "name_candidate": name,
            "quantity_candidate": 1,
            "unit_price_candidate": unit_price,
            "amount_candidate": final_amount,
            "discount_amount_candidate": negative[-1],
            "column_resolution": "discount_arithmetic",
        }
        if product_code:
            candidate["product_code"] = product_code
        return candidate

    def single_amount_item(row: list[Any], page_number: Any) -> dict[str, Any] | None:
        cells = [str(cell or "").strip() for cell in row]
        name = cells[0] if cells else ""
        raw = " | ".join(cell for cell in cells if cell)
        if not name or summary_labels.search(raw) or non_item_row.fullmatch(re.sub(r"\s+", "", name)):
            return None
        if re.search(r"(?:직원|매장|영수증|날짜|시간|POS|CATID|승인|현대HDS|^X$)", name, re.IGNORECASE):
            return None
        numbers = re.findall(r"(?<!\d)\d{1,3}(?:[.,]\d{3})+|(?<!\d)\d{1,8}(?!\d)", " | ".join(cells[1:]))
        if len(numbers) != 1 or not re.search(r"[A-Za-z가-힣]", name):
            return None
        amount = _receipt_number(numbers[0])
        if amount <= 0:
            return None
        return {
            "page": page_number,
            "source": "single_amount_item_row",
            "raw_cells": [cell for cell in cells if cell],
            "name_candidate": name,
            "quantity_candidate": 1,
            "unit_price_candidate": amount,
            "amount_candidate": amount,
            "column_resolution": "single_amount_default_quantity",
        }

    def add_candidate(cells: list[str], page_number: Any, source: str, columns: list[str] | None = None) -> None:
        # Keep empty cells until column roles are resolved. Removing them shifts
        # every value after a missed cell into the wrong semantic column.
        aligned_cells = [str(cell or "").strip() for cell in cells]
        raw_first_cell = aligned_cells[0] if aligned_cells else ""
        cleanup_reasons: list[str] = []
        if aligned_cells:
            aligned_cells[0] = re.sub(
                r"^(?:(?:판매번호|포스번호|거래번호|주문번호|영수증번호)\s*[:#]?\s*[0-9A-Za-z-]+\s*)+",
                "",
                aligned_cells[0],
                flags=re.IGNORECASE,
            ).strip()
            # Receipt line numbers and U-prefixed inventory identifiers are
            # metadata, not product names. Preserve the code separately.
            aligned_cells[0] = re.sub(r"^\s*\d{1,3}\s+(?=\D)", "", aligned_cells[0]).strip()
            aligned_cells[0], cleanup_reasons = _clean_item_name_evidence(aligned_cells[0])
        product_code = None
        if aligned_cells:
            code_match = re.search(r"(?<![0-9A-Za-z])(U\d{6,})(?!\d)", aligned_cells[0], re.IGNORECASE)
            if code_match:
                product_code = code_match.group(1).upper()
                aligned_cells[0] = " ".join(
                    value for value in (aligned_cells[0][:code_match.start()].strip(), aligned_cells[0][code_match.end():].strip())
                    if value
                )
        display_cells = [cell for cell in aligned_cells if cell]
        raw = " | ".join(display_cells)
        dedupe_key = re.sub(r"[^0-9A-Za-z가-힣]", "", raw).lower()
        if (
            not raw or dedupe_key in seen or summary_labels.search(raw)
            or non_item_row.fullmatch(re.sub(r"\s+", "", aligned_cells[0] if aligned_cells else ""))
        ):
            return
        if not re.search(r"[A-Za-z가-힣]", raw):
            return
        numeric_raw = " | ".join(aligned_cells[1:]) if len(aligned_cells) > 1 else raw
        numbers = re.findall(r"(?<!\d)\d{1,3}(?:[.,]\d{3})+|(?<!\d)\d{1,8}(?!\d)", numeric_raw)
        if len(numbers) < 2:
            return

        first_number = re.search(r"\d", raw)
        if len(aligned_cells) > 1 and aligned_cells[0] and re.search(r"[A-Za-z가-힣]", aligned_cells[0]):
            name = aligned_cells[0]
        else:
            name = raw[:first_number.start()].strip(" |:-") if first_number else ""
        parsed = [_receipt_number(value) for value in numbers]
        parenthesized = [
            _receipt_number(value)
            for value in re.findall(r"[\(（]\s*(\d{1,3}(?:[.,]\d{3})+|\d{3,8})\s*[\)）]", numeric_raw)
        ]
        primary_parsed = list(parsed)
        for alternate in parenthesized:
            try:
                primary_parsed.remove(alternate)
            except ValueError:
                pass
        candidate: dict[str, Any] = {
            "page": page_number,
            "source": source,
            "raw_cells": display_cells,
            "name_candidate": name or None,
            "amount_candidate": primary_parsed[-1] if primary_parsed else parsed[-1],
        }
        if name:
            name_parts = _structure_item_name(name)
            candidate["name_candidate"] = name_parts["canonical_name"]
            if name_parts["aliases"]:
                candidate["alias_candidates"] = name_parts["aliases"]
            if name_parts["specifications"]:
                candidate["specification_candidates"] = name_parts["specifications"]
            if name_parts["options"]:
                candidate["option_candidates"] = name_parts["options"]
        if cleanup_reasons:
            candidate["raw_name_candidate"] = raw_first_cell
            candidate["name_cleanup"] = cleanup_reasons
        if product_code:
            candidate["product_code"] = product_code
        if parenthesized:
            candidate["alternate_price_candidates"] = parenthesized
            candidate["candidate_type"] = "incomplete_item"
            candidate["uncertainty"] = ["parenthesized_price_role"]
        resolved_by_header = False
        if columns and len(columns) == len(aligned_cells):
            values_by_role = {role: aligned_cells[index] for index, role in enumerate(columns) if aligned_cells[index]}
            for role, target in (("quantity", "quantity_candidate"), ("unit_price", "unit_price_candidate"), ("amount", "amount_candidate")):
                value = values_by_role.get(role)
                if value and re.search(r"\d", value):
                    value_numbers = re.findall(r"\d{1,3}(?:[.,]\d{3})+|\d{1,8}", value)
                    if value_numbers:
                        amount_index = 0 if parenthesized else -1
                        numeric_value = _receipt_number(value_numbers[amount_index] if role == "amount" else value_numbers[0])
                        # A money-sized value in the quantity column is evidence
                        # of a missed/shifted cell, not a quantity of thousands.
                        if role != "quantity" or 0 < numeric_value <= 999:
                            candidate[target] = numeric_value
            resolved_by_header = all(candidate.get(field) is not None for field in (
                "quantity_candidate", "unit_price_candidate", "amount_candidate",
            ))
        if len(primary_parsed) >= 3 and not resolved_by_header:
            first, second, amount = primary_parsed[-3], primary_parsed[-2], primary_parsed[-1]
            # Compare both possible quantity/unit-price assignments. Arithmetic
            # plus a receipt-sized quantity is stronger than physical order.
            options = [(first, second), (second, first)]
            valid = [(quantity, price) for quantity, price in options if 0 < quantity <= 999 and price >= 1 and quantity * price == amount]
            if valid:
                quantity, price = min(valid, key=lambda pair: pair[0])
                candidate.update(quantity_candidate=quantity, unit_price_candidate=price, column_resolution="arithmetic")
            else:
                plausible = [(quantity, price) for quantity, price in options if 0 < quantity <= 100 and price >= 100]
                if plausible:
                    quantity, price = min(plausible, key=lambda pair: pair[0])
                    candidate.update(quantity_candidate=quantity, unit_price_candidate=price, column_resolution="plausibility")
                else:
                    candidate["unresolved_numeric_cells"] = primary_parsed[-3:]
        elif len(primary_parsed) >= 2 and primary_parsed[0] <= 100:
            quantity, price = primary_parsed[0], primary_parsed[1]
            candidate.update(
                quantity_candidate=quantity,
                unit_price_candidate=price,
                amount_candidate=quantity * price,
                column_resolution="item_block",
            )
        elif primary_parsed and primary_parsed[0] <= 100:
            candidate["quantity_candidate"] = primary_parsed[0]
        if resolved_by_header:
            candidate["column_resolution"] = "header"
        if (
            not candidate.get("quantity_candidate")
            and candidate.get("unit_price_candidate")
            and candidate.get("amount_candidate")
            and candidate["unit_price_candidate"] == candidate["amount_candidate"]
        ):
            candidate["quantity_candidate"] = 1
            candidate["quantity_resolution"] = "unit_price_equals_amount"
        candidates.append(candidate)
        seen.add(dedupe_key)

    for page in pages or []:
        page_number = page.get("page")
        tables = page.get("tables") or []
        for table in tables:
            pending_title: str | None = None
            table_rows = table.get("rows") or []
            skip_rows: set[int] = set()
            for row_index, row in enumerate(table_rows):
                if row_index in skip_rows:
                    continue
                if row_index + 1 < len(table_rows):
                    paired = discounted_item_pair(row, table_rows[row_index + 1], page_number)
                    if paired:
                        append_structured_candidate(paired)
                        skip_rows.add(row_index + 1)
                        continue
                single = single_amount_item(row, page_number)
                if single:
                    append_structured_candidate(single)
                    continue
                aligned_row = [str(cell or "").strip() for cell in row]
                first_cell = aligned_row[0] if aligned_row else ""
                compact_first = re.sub(r"\s+", "", first_cell)
                if re.search(r"^(?:수량|총수량|계|합계|총합계|면세상품|과세상품|부가세|결제금액)", compact_first):
                    break
                other_cells = [cell for cell in aligned_row[1:] if cell]
                title_text = re.sub(r"^\s*\d{1,3}\s+(?=\D)", "", first_cell).strip()
                is_code_row = re.fullmatch(r"U\d{6,}", first_cell, re.IGNORECASE) is not None
                is_title_only = bool(re.search(r"[A-Za-z가-힣]", title_text)) and not other_cells and not is_code_row
                if is_title_only:
                    pending_title = title_text
                    continue
                if pending_title and is_code_row:
                    aligned_row[0] = f"{pending_title} {first_cell}"
                    pending_title = None
                elif pending_title:
                    # Do not silently discard an unresolved title when the
                    # next row is not the expected code/price continuation.
                    add_candidate([pending_title], page_number, "unresolved_title", None)
                    pending_title = None
                add_candidate(aligned_row, page_number, "table", table.get("columns"))
            if pending_title:
                add_candidate([pending_title], page_number, "unresolved_title", None)
        # A valid table is authoritative. Rescanning page.text would re-add
        # payment, approval, and receipt-number lines as fake products.
        if not tables:
            item_regions = [region for region in page.get("regions") or [] if region.get("type") == "items"]
            page_items = page.get("items") or []
            if item_regions and page_items:
                for region in item_regions:
                    (rx1, ry1), (rx2, ry2) = region.get("bbox") or [[0, 0], [0, 0]]
                    selected = []
                    for item in page_items:
                        (x1, y1), (x2, y2) = item.get("bbox") or [[0, 0], [0, 0]]
                        center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
                        if rx1 <= center_x <= rx2 and ry1 <= center_y <= ry2:
                            selected.append(item)
                    selected.sort(key=lambda item: ((item["bbox"][0][1] + item["bbox"][1][1]) / 2, item["bbox"][0][0]))
                    heights = [max(item["bbox"][1][1] - item["bbox"][0][1], 1) for item in selected]
                    # A conservative threshold prevents adjacent compact
                    # receipt rows from being merged into one product line.
                    tolerance = (sorted(heights)[len(heights) // 2] if heights else 10) * .4
                    lines: list[list[dict[str, Any]]] = []
                    for item in selected:
                        center_y = (item["bbox"][0][1] + item["bbox"][1][1]) / 2
                        line = next((line for line in reversed(lines[-3:]) if abs(center_y - sum((entry["bbox"][0][1] + entry["bbox"][1][1]) / 2 for entry in line) / len(line)) <= tolerance), None)
                        if line is None:
                            lines.append([item])
                        else:
                            line.append(item)
                    for line in lines:
                        line.sort(key=lambda item: item["bbox"][0][0])
                        add_candidate([item.get("text", "") for item in line], page_number, "item_region")
            elif not item_regions:
                for line in str(page.get("text") or "").splitlines():
                    add_candidate([line], page_number, "ocr_line_unscoped")
    candidates = candidates[:40]
    combined_text = "\n".join(str(page.get("text") or "") for page in pages or [])
    summary = _receipt_hints(combined_text, "receipt")
    stated_count = summary.get("stated_item_count")
    stated_quantity = summary.get("stated_total_quantity")
    if stated_count and stated_quantity and len(candidates) == int(stated_count):
        known = [candidate.get("quantity_candidate") for candidate in candidates]
        missing = [index for index, value in enumerate(known) if not value or not 0 < float(value) <= 999]
        if len(missing) == 1:
            remainder = int(stated_quantity) - sum(int(value) for value in known if value and 0 < float(value) <= 999)
            if 0 < remainder <= 999:
                candidate = candidates[missing[0]]
                candidate["quantity_candidate"] = remainder
                candidate["quantity_resolution"] = "receipt_total_remainder"
                candidate.setdefault("uncertainty", []).append("quantity_recovered_from_total")
                amount = candidate.get("amount_candidate")
                if not candidate.get("unit_price_candidate") and amount and int(amount) % remainder == 0:
                    candidate["unit_price_candidate"] = int(amount) // remainder
                    candidate["unit_price_resolution"] = "amount_divided_by_quantity"
    return candidates


def _reconcile_items_with_candidates(
    items: list[dict[str, Any]], candidates: list[dict[str, Any]], stated_count: int | None,
) -> list[dict[str, Any]]:
    """Ground item names in a uniquely matching OCR amount/quantity row."""
    resolved = [dict(item) for item in items if isinstance(item, dict)]
    used_candidates: set[int] = set()

    for item in resolved:
        item_amount = _clean_number(item.get("total_amount"))
        item_unit_price = _clean_number(item.get("unit_price"))
        item_quantity = _clean_number(item.get("quantity"))
        matches: list[tuple[int, int]] = []
        for index, candidate in enumerate(candidates):
            if index in used_candidates or not candidate.get("name_candidate"):
                continue
            score = 0
            candidate_amount = _clean_number(candidate.get("amount_candidate"))
            candidate_unit_price = _clean_number(candidate.get("unit_price_candidate"))
            candidate_quantity = _clean_number(candidate.get("quantity_candidate"))
            if item_amount and candidate_amount and item_amount == candidate_amount:
                score += 4
            if item_unit_price and candidate_unit_price and item_unit_price == candidate_unit_price:
                score += 2
            if item_quantity and candidate_quantity and item_quantity == candidate_quantity:
                score += 1
            if score >= 4:
                matches.append((score, index))
        if not matches:
            continue
        best_score = max(score for score, _ in matches)
        best = [index for score, index in matches if score == best_score]
        if len(best) != 1:
            continue
        candidate_index = best[0]
        candidate = candidates[candidate_index]
        candidate_name = str(candidate.get("name_candidate") or "").strip()
        model_name = str(item.get("name") or "").strip()
        if candidate_name and candidate_name != model_name:
            item["raw_model_name"] = model_name or None
            item["name"] = candidate_name
            item["name_resolution"] = "unique_ocr_amount_match"
        candidate_metadata = [
            *candidate.get("alias_candidates", []),
            *candidate.get("specification_candidates", []),
            *candidate.get("option_candidates", []),
        ]
        if candidate_metadata:
            existing_specification = str(item.get("specification") or "").strip()
            item["specification"] = " · ".join(filter(None, [existing_specification, *candidate_metadata]))
        if candidate.get("alias_candidates"):
            item["aliases"] = candidate["alias_candidates"]
        if candidate.get("option_candidates"):
            item["options"] = candidate["option_candidates"]
        used_candidates.add(candidate_index)

    # Recover a completely missed item pass only when the receipt-declared row
    # count exactly agrees with fully evidenced OCR candidates.
    if not resolved and stated_count and len(candidates) == int(stated_count):
        recoverable = [candidate for candidate in candidates if candidate.get("name_candidate") and candidate.get("amount_candidate")]
        if len(recoverable) == int(stated_count):
            resolved = [{
                "name": candidate["name_candidate"],
                "quantity": candidate.get("quantity_candidate"),
                "unit_price": candidate.get("unit_price_candidate"),
                "total_amount": candidate.get("amount_candidate"),
                "specification": " · ".join([
                    *candidate.get("alias_candidates", []),
                    *candidate.get("specification_candidates", []),
                    *candidate.get("option_candidates", []),
                ]) or None,
                "aliases": candidate.get("alias_candidates") or None,
                "options": candidate.get("option_candidates") or None,
                "name_resolution": "receipt_count_confirmed_ocr_recovery",
            } for candidate in recoverable]

    return resolved


def _receipt_prompt(text: str, filename: str, pages: list[dict[str, Any]] | None = None) -> str:
    hints = _receipt_hints(text, filename)
    return f"""OCR 영수증의 요약 정보만 JSON 객체 하나로 반환하세요. items는 추출하지 마세요.
OCR에 없는 값은 추측하지 말고 null로 작성하세요.

doc_type은 다음 중 하나입니다.
- EXPENSE_REPORT: 일반 경비
- TRAVEL_EXPENSE: 출장·교통·숙박
- PURCHASE_REQUEST: 물품·장비·소프트웨어 구매
- WELFARE_BENEFIT: 도서·교육·의료·복리후생

반환 키: image, doc_type, expense_category, merchant, transaction_date, supply_amount,
tax_amount, discount_amount, total_amount, payment_method, card_number, description.

판단 규칙:
1. OCR에 직접 근거가 있는 값만 작성하고, 불명확한 값은 null로 둡니다. 날짜는 YYYY-MM-DD, 금액과 수량은 숫자로 작성합니다.
2. 상호는 실제 판매 주체를 선택합니다. 쇼핑몰·건물·지점 안내·URL은 입점 장소일 수 있으므로, 영수증을 발행하고 상품을 판매한 입점 매장명을 우선합니다. 예를 들어 OCR에 `유니클로`와 `Starfield` 또는 `starfield.co.kr`가 함께 있으면 merchant는 쇼핑몰인 Starfield가 아니라 입점 매장인 `유니클로`입니다. 브랜드명 뒤의 `(과세)`·`(면세)`는 세금 구분이므로 상호에서 제거합니다.
3. 실제 결제된 상품 행만 items로 만듭니다. 먼저 `상품명 | 수량 | 단가 | 금액`의 대응을 확인한 뒤 출력하며, 상품 행이 명확하면 items를 비워 두지 않습니다. `총품목/총수량`의 총품목 수 N은 서로 다른 상품 행의 수이므로, 표시가 명확하면 실제 상품을 N개 찾아야 합니다.
4. 새 상품명에 별도의 수량·단가·금액이 붙으면 독립 품목입니다. 한글명과 영문명이 이어져도 가격 묶음이 하나일 때만 같은 품목이며, `DIY`, `도안`, 괄호 표기라는 이유만으로 다른 유료 상품을 규격에 합치지 않습니다. 상호와 품목명이 같아도 각각 근거가 있으면 merchant와 items 양쪽에 모두 작성합니다. 예를 들어 상호가 `유니클로`이고 상품 행이 `유니클로(과세) 1 60,000`이면 merchant는 `유니클로`, items에는 `유니클로(과세)` 1개를 작성합니다.
5. 상품명처럼 보여도 결제·승인·합계·할인·세금·안내 영역의 문구는 품목이 아닙니다. 반대로 별도 청구된 배송비·봉투값은 품목으로 둘 수 있습니다.
6. total_amount는 `최종 결제금액`, `받을 금액`, `승인금액`, `총구매금액`처럼 최종 지불액을 뜻하는 명시적 라벨을 우선합니다. 상품합계·소계나 공급가액+세금 계산은 검산용이며, 할인·쿠폰 때문에 다르면 명시된 최종 금액을 선택합니다.
7. 아래 코드 힌트는 후보일 뿐입니다. OCR의 명시적 라벨 및 문맥과 충돌하면 OCR 판단을 우선합니다.

[파일명]
{filename}

[코드 확인값]
{json.dumps(hints, ensure_ascii=False)}

[OCR 텍스트]
{text[:6000]}
"""


def _receipt_items_prompt(text: str, pages: list[dict[str, Any]] | None = None) -> str:
    candidates = _receipt_item_candidates(pages)
    summary = _receipt_hints(text, "receipt")
    stated_count = summary.get("stated_item_count")
    stated_quantity = summary.get("stated_total_quantity")
    # Always include a compact OCR excerpt. Candidate generation is heuristic;
    # hiding the source text when even one bad candidate exists prevents the
    # model from recovering item rows that the table parser missed.
    evidence = json.dumps({
        "candidates": candidates,
        "ocr_text": text[:3500],
    }, ensure_ascii=False, separators=(",", ":"))
    return f"""영수증의 실제 구매 품목만 JSON으로 반환하세요.
형식: {{"items":[{{"name":...,"specification":...,"quantity":...,"unit":...,"unit_price":...,"supply_amount":...,"tax_amount":...,"total_amount":...,"note":...}}]}}

규칙:
1. 근거 행 하나는 원칙적으로 품목 하나입니다. 확인되는 품목을 누락하지 마세요.
2. 합계·소계·공급가액·부가세·할인·결제·승인·안내 행은 품목이 아닙니다.
3. 후보의 name/quantity/unit_price/amount는 코드 추정값입니다. raw_cells가 다르면 raw_cells를 우선합니다.
4. 확인할 수 없는 필드는 null로 두고 상품 자체가 확인되면 품목을 삭제하지 마세요.
5. 영수증에 명시된 품목 수: {stated_count if stated_count is not None else '미확인'}
6. 열 순서는 영수증마다 다릅니다. raw_cells의 순서를 수량-단가로 고정하지 말고 column_resolution과 산술 관계를 확인하세요.
7. 판매번호·거래번호·주문번호·승인번호·사업자번호는 품목명이 아닙니다.
8. candidate_type이 incomplete_item이어도 상품명과 주가격이 있으면 품목을 삭제하지 말고 불명확한 필드만 null로 두세요.
9. alternate_price_candidates는 괄호로 표시된 회원가·할인가·참고가격 후보입니다. 명확한 라벨이 없으면 주가격을 대체하지 마세요.
10. 영수증에 명시된 총수량: {stated_quantity if stated_quantity is not None else '미확인'}. quantity_resolution이 receipt_total_remainder이면 다른 품목 수량과 총수량으로 복원한 값입니다.
11. product_code는 재고·상품 식별 코드이며 품목명이 아닙니다. name_candidate를 품목명으로 사용하고 코드는 specification 또는 note에만 보존하세요.
12. raw_name_candidate와 name_cleanup이 있으면 거래일시·POS·판매번호·상품 열 제목을 제거한 name_candidate를 사용하세요. 상품명에 날짜, POS 번호, `상품코드/단가/수량/금액` 헤더를 포함하지 마세요.
13. alias_candidates는 다른 언어로 반복 표기된 같은 상품명, specification_candidates는 중량·크기·묶음 규격, option_candidates는 SKU·색상 옵션입니다. 이 값들은 name에 다시 합치지 말고 specification 또는 note에 보존하세요.
14. candidates가 OCR 원문과 충돌하거나 품목을 누락하면 ocr_text를 사용해 복원하세요. 정가 다음 행에 SKU·색상·할인액·할인 후 금액이 이어지면 같은 품목입니다.

[품목 근거]
{evidence}
"""


async def _classify_receipt_with_model(
    text: str,
    filename: str,
    model_name: str,
    pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    raw = await generate(_receipt_prompt(text, filename, pages), json_format=True, num_predict=1200, model_name=model_name)
    result = json.loads(raw)
    if not isinstance(result, dict):
        logger.error("Receipt JSON parsing failed: model=%s filename=%s reason=object_expected raw_response=%s", model_name, filename, raw)
        raise ValueError("object expected")
    result["llm_trace"] = {
        "model_name": model_name,
        "prompt_version": FINANCE_PROMPT_VERSION,
        "summary_raw": json.loads(json.dumps(result, ensure_ascii=False)),
        "summary_response_text": raw,
    }
    result.pop("items", None)
    candidates = _receipt_item_candidates(pages)
    try:
        items_raw = await generate(
            _receipt_items_prompt(text, pages),
            json_format=True,
            num_predict=900,
            model_name=model_name,
        )
        items_result = json.loads(items_raw)
        model_items = items_result.get("items") if isinstance(items_result, dict) and isinstance(items_result.get("items"), list) else []
        result["llm_trace"]["items_raw"] = json.loads(json.dumps(items_result, ensure_ascii=False))
        result["llm_trace"]["items_response_text"] = items_raw
        model_items_snapshot = json.loads(json.dumps(model_items, ensure_ascii=False))
        stated_count = _receipt_hints(text, filename).get("stated_item_count")
        result["items"] = _reconcile_items_with_candidates(model_items, candidates, stated_count)
        result["item_extraction_diagnostics"] = {
            "candidates": candidates,
            # Preserve the pre-normalization model payload for error tracing.
            "model_items": model_items_snapshot,
            "resolved_items": json.loads(json.dumps(result["items"], ensure_ascii=False)),
        }
    except Exception:
        # Metadata remains useful when the isolated item pass fails.
        result["items"] = []
        result["item_extraction_diagnostics"] = {
            "candidates": candidates,
            "model_items": [],
            "failed": True,
        }
        result["llm_trace"].setdefault("items_raw", None)
        result["llm_trace"].setdefault("items_response_text", None)
    return result


async def _classify_receipt(
    text: str,
    filename: str,
    pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    hints = _receipt_hints(text, filename)
    try:
        return await _classify_receipt_with_model(text, filename, RECEIPTS_MODEL_NAME, pages)
    except Exception:
        # OCR 결과는 LLM 장애와 무관하게 재무 양식에 먼저 저장합니다.
        # 학습 모델이 준비되면 같은 검토 화면에서 분류값을 보완할 수 있습니다.
        return {
            "document_type": hints.get("document_type") or "EXPENSE_REPORT",
            "expense_category": hints.get("expense_category") or "확인 필요",
            "transaction_date": hints.get("transaction_date"),
            "supply_amount": hints.get("supply_amount") or 0,
            "tax_amount": hints.get("tax_amount") or 0,
            "total_amount": hints.get("total_amount") or 0,
            "description": "LLM 분류 전 OCR 자동 입력",
            "items": [],
            "_model_name": "rules-fallback",
        }


@router.post("/records/classify", response_model=FinanceRecord)
async def classify_and_save(payload: FinanceClassifyRequest, user: User = Depends(require_current_user)) -> dict[str, Any]:
    if not RECEIPTS_MODEL_NAME.strip():
        raise HTTPException(
            status_code=503,
            detail="영수증 LLM 모델이 설정되지 않았습니다. .env에 RECEIPTS_LLM_MODEL을 설정해 주세요.",
        )
    document = supabase_service.get_ocr_document(user.email, payload.document_id)
    extracted_text = (document.get("extracted_text") or "").strip()
    if not extracted_text:
        raise HTTPException(status_code=422, detail="분류할 OCR 텍스트가 없습니다.")
    existing_records = supabase_service.list_finance_records(user.email, limit=1000)
    hints = _receipt_hints(extracted_text, document.get("file_name") or "receipt")
    fingerprint = _receipt_fingerprint(extracted_text)
    identity_key = _receipt_identity_key(extracted_text, hints)
    duplicate_record = None
    for existing in existing_records:
        if str(existing.get("document_id")) == payload.document_id:
            continue
        data = existing.get("structured_data") or {}
        if data.get("receipt_fingerprint") == fingerprint or (identity_key and data.get("receipt_identity_key") == identity_key):
            duplicate_record = existing
            break

    classified = await _classify_receipt(
        extracted_text,
        document.get("file_name") or "receipt",
        document.get("bounding_boxes") or [],
    )
    normalized = _normalize(classified, document.get("file_name") or "receipt", extracted_text)
    normalized["structured_data"]["receipt_fingerprint"] = fingerprint
    normalized["structured_data"]["receipt_identity_key"] = identity_key
    candidate = {**normalized, "structured_data": normalized["structured_data"]}
    candidate_legacy_key = _legacy_receipt_key(candidate)
    if candidate_legacy_key and duplicate_record is None:
        for existing in existing_records:
            if str(existing.get("document_id")) == payload.document_id:
                continue
            if _legacy_receipt_key(existing) == candidate_legacy_key:
                duplicate_record = existing
                break
    if duplicate_record is not None:
        normalized["duplicate_of_record_id"] = duplicate_record["id"]
        normalized["structured_data"]["duplicate_detection"] = {
            "is_duplicate": True,
            "previous_record_id": duplicate_record["id"],
            "message": "동일 영수증의 이전 분석 기록이 있으며 현재 모델로 새 기록을 생성했습니다.",
        }
    else:
        normalized["duplicate_of_record_id"] = None
        normalized["structured_data"].pop("duplicate_detection", None)
    normalized["prompt_version"] = FINANCE_PROMPT_VERSION
    normalized["processed_at"] = datetime.now(timezone.utc).isoformat()
    return supabase_service.save_finance_record(
        user_email=user.email,
        document_id=payload.document_id,
        payload=normalized,
    )


@router.get("/records", response_model=list[FinanceRecord])
def list_records(user: User = Depends(require_current_user)) -> list[dict[str, Any]]:
    unique_records = []
    seen = set()
    for record in supabase_service.list_finance_records(user.email):
        data = record.get("structured_data") or {}
        duplicate_key = data.get("receipt_identity_key") or data.get("receipt_fingerprint") or _legacy_receipt_key(record)
        if duplicate_key and duplicate_key in seen:
            continue
        if duplicate_key:
            seen.add(duplicate_key)
        unique_records.append(record)
    return unique_records


@router.get("/history")
def finance_history(user: User = Depends(require_current_user)) -> list[dict[str, Any]]:
    history = []
    for record in supabase_service.list_finance_records(user.email, limit=1000):
        workflow = (record.get("structured_data") or {}).get("finance_workflow") or {}
        if not workflow.get("submitted_at"):
            continue
        history.append({
            "id": record.get("id"),
            "document_type": record.get("document_type"),
            "expense_category": record.get("expense_category"),
            "merchant": record.get("merchant"),
            "total_amount": record.get("total_amount"),
            "document_filename": workflow.get("document_filename") or f"finance-receipt-{record.get('id')}.xlsx",
            "finance_team_status": workflow.get("finance_team_status") or "확인 필요",
            "submitted_at": workflow.get("submitted_at"),
            "finance_confirmed_at": workflow.get("finance_confirmed_at"),
        })
    return history


@router.patch("/records/{record_id}", response_model=FinanceRecord)
def update_record(record_id: str, payload: FinanceRecordUpdate, user: User = Depends(require_current_user)) -> dict[str, Any]:
    values = payload.model_dump(mode="json")
    if not values["total_amount"]:
        values["total_amount"] = values["supply_amount"] + values["tax_amount"]
    return supabase_service.update_finance_record(user.email, record_id, values)


@router.post("/records/{record_id}/submit", response_model=FinanceRecord)
def submit_to_finance(record_id: str, user: User = Depends(require_current_user)) -> dict[str, Any]:
    record = next((item for item in supabase_service.list_finance_records(user.email, limit=1000) if item.get("id") == record_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="재무 기록을 찾을 수 없습니다.")
    if record.get("status") != "CONFIRMED":
        raise HTTPException(status_code=422, detail="사용자가 최종 확정한 문서만 재무팀에 보낼 수 있습니다.")
    structured_data = dict(record.get("structured_data") or {})
    workflow = dict(structured_data.get("finance_workflow") or {})
    workflow.update({
        "finance_team_status": "확인 필요",
        "submitted_at": workflow.get("submitted_at") or datetime.now(timezone.utc).isoformat(),
        "finance_confirmed_at": None,
        "document_filename": workflow.get("document_filename") or f"finance-receipt-{record_id}.xlsx",
    })
    structured_data["finance_workflow"] = workflow
    return supabase_service.update_finance_record(user.email, record_id, {"structured_data": structured_data})


@router.post("/records/{record_id}/finance-confirm", response_model=FinanceRecord)
def confirm_by_finance(record_id: str, user: User = Depends(require_current_user)) -> dict[str, Any]:
    if user.role not in {"ADMIN", "DEVELOPER"}:
        raise HTTPException(status_code=403, detail="재무팀 확인 권한이 없습니다.")
    record = next((item for item in supabase_service.list_finance_records(user.email, limit=1000) if item.get("id") == record_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="재무 기록을 찾을 수 없습니다.")
    structured_data = dict(record.get("structured_data") or {})
    workflow = dict(structured_data.get("finance_workflow") or {})
    if not workflow.get("submitted_at"):
        raise HTTPException(status_code=422, detail="아직 재무팀에 제출되지 않은 문서입니다.")
    workflow.update({"finance_team_status": "확인", "finance_confirmed_at": datetime.now(timezone.utc).isoformat()})
    structured_data["finance_workflow"] = workflow
    return supabase_service.update_finance_record(user.email, record_id, {"structured_data": structured_data})


@router.get("/records/{record_id}/export")
def export_record(record_id: str, user: User = Depends(require_current_user)) -> StreamingResponse:
    record = next((item for item in supabase_service.list_finance_records(user.email, limit=1000) if item.get("id") == record_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="재무 기록을 찾을 수 없습니다.")
    content = build_finance_workbook([record], author={"name": user.name, "email": user.email})
    filename = f"finance-receipt-{record_id}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/records/export")
def export_selected_records(payload: FinanceExportRequest, user: User = Depends(require_current_user)) -> StreamingResponse:
    requested_ids = list(dict.fromkeys(payload.record_ids))
    records_by_id = {
        record.get("id"): record
        for record in supabase_service.list_finance_records(user.email, limit=1000)
        if record.get("id") in requested_ids
    }
    records = [records_by_id[record_id] for record_id in requested_ids if record_id in records_by_id]
    if len(records) != len(requested_ids):
        raise HTTPException(status_code=404, detail="일부 재무 기록을 찾을 수 없습니다.")
    content = build_finance_workbook(records, author={"name": user.name, "email": user.email})
    filename = f"finance-receipts-{date.today().isoformat()}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export")
def export_records(user: User = Depends(require_current_user)) -> StreamingResponse:
    records = [record for record in supabase_service.list_finance_records(user.email, limit=1000) if record.get("status") == "CONFIRMED"]
    if not records:
        raise HTTPException(status_code=422, detail="확정된 재무 문서가 없습니다. 내용을 검토하고 확정해 주세요.")
    content = build_finance_workbook(records, author={"name": user.name, "email": user.email})
    filename = f"finance-receipts-{date.today().isoformat()}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
