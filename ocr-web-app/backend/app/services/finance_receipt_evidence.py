from __future__ import annotations

import json
import hashlib
import logging
import re
from datetime import date, datetime, timezone
from io import BytesIO
from time import perf_counter
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.routes.auth import require_current_user
from app.api.routes.chatbot import generate
from app.constants.finance_taxonomy import (
    ALLOWED_DOCUMENT_TYPES,
    ALLOWED_EXPENSE_CATEGORIES,
    CATEGORY_TO_DOCUMENT_TYPE,
    normalize_expense_category,
    validate_classification,
)
from app.core.config import settings
from app.models.user import User
from app.services.finance_workbook_service import build_finance_workbook
from app.services.supabase_service import supabase_service

router = APIRouter()
logger = logging.getLogger(__name__)
DocumentType = Literal["EXPENSE_REPORT", "TRAVEL_EXPENSE", "PURCHASE_REQUEST", "WELFARE_BENEFIT"]
EXPENSE_CATEGORIES = ALLOWED_EXPENSE_CATEGORIES
FINANCE_PROMPT_VERSION = "receipt-v11-beauty-service-guidance"
RECEIPTS_MODEL_NAME = settings.RECEIPTS_LLM_MODEL

_ITEM_COLUMN_LABEL = r"(?:상품\s*코드|상품\s*명|품\s*명|품목\s*명|단가|수량|금액|합계금액)"
_ITEM_COLUMN_HEADER = re.compile(rf"(?:{_ITEM_COLUMN_LABEL}[\s|:/·-]*){{2,}}", re.IGNORECASE)


ALCOHOL_EVIDENCE_PATTERN = re.compile(
    r"소주|맥주|생맥|와인|위스키|보드카|막걸리|사케|청주|양주|하이볼|칵테일|"
    r"주류|알코올|alcohol|beer|wine|whisk(?:e)?y|vodka|sake|cocktail",
    re.IGNORECASE,
)
NON_ALCOHOL_PATTERN = re.compile(r"무\s*알코올|무\s*알콜|논\s*알코올|논\s*알콜|non[-\s]?alcohol", re.IGNORECASE)


def _has_alcohol_evidence(text: Any) -> bool:
    evidence = NON_ALCOHOL_PATTERN.sub("", str(text or ""))
    return ALCOHOL_EVIDENCE_PATTERN.search(evidence) is not None


def _normalize_expense_category(value: Any, evidence_text: Any = None) -> str | None:
    """Normalize to the single receipt_dataset_verified taxonomy."""
    normalized = normalize_expense_category(value)
    if normalized is None:
        return None
    if normalized == "식비/주류" and not _has_alcohol_evidence(evidence_text):
        return "식비"
    return normalized


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
    source_file_name: str | None = Field(default=None, max_length=500)
    save_to_archive: bool = True


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
    document_type: DocumentType | None
    expense_category: str | None
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


def _quantity_number(value: Any) -> float:
    """Parse quantities without treating a period as a thousands separator."""
    if isinstance(value, (int, float)):
        return max(float(value), 0)
    normalized = re.sub(r"\s+", "", str(value or "")).replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", normalized)
    if not match:
        return 0
    try:
        return max(float(match.group(0)), 0)
    except ValueError:
        return 0


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
    date_value_pattern = (
        r"(?<!\d)(20\d{2}|\d{2})\s*(?:년\s*|[-./]\s*)"
        r"(\d{1,2})\s*(?:월\s*|[-./]\s*)(\d{1,2})\s*일?"
        r"(?:\s*\([^)]*\))?(?:\s*(?:오전|오후)?\s*\d{1,2}:\d{2}(?::\d{2})?)?"
    )

    # Prefer the meaning of a nearby label over the visual length of a date.
    # For example, ``거래일시: 18/01/10`` is stronger evidence than an
    # unrelated four-digit ``품질검사일자: 2017-11-09`` elsewhere.
    labeled_date_match = None
    for label_pattern in (
        r"(?:거래|판매|구매|결제)\s*(?:일시|일자|일)",
        r"승인\s*(?:일시|일자|일)",
        r"발행\s*(?:일시|일자|일)",
    ):
        labeled_date_match = re.search(
            rf"(?:{label_pattern})\s*[:：]?\s*{date_value_pattern}",
            text,
            re.IGNORECASE,
        )
        if labeled_date_match:
            break

    generic_date_text = "\n".join(
        line for line in text.splitlines()
        if not re.search(r"품질\s*검사|제조\s*(?:일|일자)|유효\s*(?:기간|일자)|만료\s*(?:일|일자)", line, re.IGNORECASE)
    )
    date_match = labeled_date_match or re.search(date_value_pattern, generic_date_text, re.IGNORECASE)
    transaction_date = None
    if date_match:
        try:
            raw_year = int(date_match.group(1))
            transaction_date = date(
                raw_year if raw_year >= 100 else 2000 + raw_year,
                int(date_match.group(2)),
                int(date_match.group(3)),
            ).isoformat()
        except ValueError:
            pass
    if transaction_date is None and not labeled_date_match:
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
    # Never let a won amount span OCR lines.  ``\s`` used to join fragments
    # such as a phone/card number on one line with an amount on the next and
    # could produce a plausible but enormous total.
    won_amounts = [
        _receipt_number(token)
        for token in re.findall(r"(?<!\d)(\d[\d.,\t ]{0,15})[\t ]*원", text)
    ]
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
        """Read a labelled amount without ever crossing an OCR line."""
        lines = [line.strip() for line in text.splitlines()]
        label_re = re.compile(rf"(?:{labels})", re.IGNORECASE)
        amount_token = r"([0-9]{1,3}(?:[ \t]*[.,][ \t]*[0-9]{3})+|[0-9]{3,8})"
        money_only = re.compile(rf"^[ \t]*{amount_token}[ \t]*원?[ \t]*[:：]?[ \t]*$", re.IGNORECASE)
        for index, line in enumerate(lines):
            label_match = label_re.search(line)
            if not label_match:
                continue
            if re.search(r"(?:과세|면세|세금)\s*합계", line, re.IGNORECASE):
                continue
            # Prefer a value on the same line and only after the matched label.
            same_line = re.search(
                rf"^[ \t]*[:：]?[ \t]*(?:금액[ \t]*)?{amount_token}[ \t]*원?",
                line[label_match.end():],
                re.IGNORECASE,
            )
            if same_line:
                value = _receipt_number(same_line.group(1))
                if value >= 100:
                    return value
            # OCR frequently places the amount immediately above or below its
            # label. Accept only an otherwise money-only adjacent line.
            for adjacent in (index - 1, index + 1):
                if 0 <= adjacent < len(lines):
                    adjacent_match = money_only.fullmatch(lines[adjacent])
                    if adjacent_match:
                        value = _receipt_number(adjacent_match.group(1))
                        if value >= 100:
                            return value
        return None

    labeled_final = labeled_amount(
        r"최종\s*결제(?:\s*금액)?|실\s*결제(?:\s*금액)?|받을\s*금액|"
        r"승인(?:\s*금액|\s*액)|결제(?:\s*금액|\s*액)|청구(?:\s*금액|\s*액)|"
        r"신용\s*판매(?:\s*금액|\s*액)|현금\s*결제(?:\s*금액)?|카드\s*결제(?:\s*금액)?|"
        r"합계\s*(?:금액|급액)?|총\s*액"
    )
    labeled_discount = labeled_amount(r"할인(?:\s*금액|\s*액)?|쿠폰(?:\s*할인)?")
    labeled_gross = labeled_amount(r"정가|판매\s*금액|할인\s*전(?:\s*금액)?|상품\s*합계|총\s*상품\s*금액")
    # Re-evaluate the core financial labels with encoding-stable patterns.
    # Explicit paid totals outrank arithmetic triples, while product subtotals
    # must not be mistaken for the final amount.
    labeled_final = labeled_amount(
        r"\uCD5C\uC885\s*\uACB0\uC81C(?:\s*\uAE08\uC561)?|"
        r"\uCD1D\s*\uACB0\uC81C(?:\s*\uAE08\uC561)?|"
        r"\uBC1B\uC744\s*\uAE08\uC561|\uC2B9\uC778(?:\s*\uAE08\uC561|\s*\uC561)?|"
        r"\uCCAD\uAD6C(?:\s*\uAE08\uC561|\s*\uC561)?|"
        r"\uC2E0\uC6A9\s*\uD310\uB9E4(?:\s*\uAE08\uC561|\s*\uC561)?|"
        r"\uD604\uAE08\s*\uACB0\uC81C(?:\s*\uAE08\uC561)?|"
        r"\uCE74\uB4DC\s*\uACB0\uC81C(?:\s*\uAE08\uC561)?|"
        r"(?<!\uC0C1\uD488)(?<!\uBB3C\uD488)\uD569\uACC4(?:\s*\uAE08\uC561)?|\uCD1D\s*\uC561"
    )
    labeled_discount = labeled_amount(
        r"\uD560\uC778(?:\s*\uAE08\uC561|\s*\uC561)?|\uCFE0\uD3F0(?:\s*\uD560\uC778)?"
    ) or labeled_discount
    labeled_gross = labeled_amount(
        r"\uC815\uAC00|\uD310\uB9E4\s*\uAE08\uC561|\uD560\uC778\s*\uC804(?:\s*\uAE08\uC561)?|"
        r"\uC0C1\uD488\s*\uD569\uACC4|\uCD1D\s*\uC0C1\uD488\s*\uAE08\uC561"
    ) or labeled_gross
    if labeled_final:
        total = labeled_final
    total_amount_source = "labeled_final" if labeled_final else "arithmetic" if triples else "won_amount" if won_amounts else None
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
    document_type_source = None
    document_type_confidence = None
    expense_category = None
    if any(keyword in name for keyword in ("출장", "여비", "교통", "숙박", "ktx", "srt", "택시")):
        document_type = "TRAVEL_EXPENSE"
        if any(keyword in name for keyword in ("출장", "여비")):
            document_type_source = "FILENAME_BUSINESS_CONTEXT"
            document_type_confidence = 0.9
        else:
            document_type_source = "FILENAME_RECEIPT_CONTEXT"
            document_type_confidence = 0.65
        if any(keyword in name for keyword in ("식비", "식대", "음료", "카페")):
            expense_category = "식비"
        elif not any(keyword in name for keyword in ("숙박", "호텔", "모텔")):
            expense_category = "교통"
    elif any(keyword in name for keyword in ("복지", "도서", "교육", "병원", "검진", "경조")):
        document_type = "WELFARE_BENEFIT"
        document_type_source = "FILENAME_BUSINESS_CONTEXT"
        document_type_confidence = 0.85
    elif any(keyword in name for keyword in ("구매", "견적", "비품", "장비", "소프트웨어", "라이선스")):
        document_type = "PURCHASE_REQUEST"
        document_type_source = "FILENAME_BUSINESS_CONTEXT"
        document_type_confidence = 0.85

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
    if not item_count_match:
        # Some OCR engines flatten the two summary columns vertically as
        # ``총품목수 총수량 2 2`` and therefore lose the printed slash. Accept
        # only two nearby small integers after both exact labels; a monetary
        # token (comma/period/currency suffix) is deliberately not allowed.
        item_count_match = re.search(
            r"총\s*품목(?:\s*수)?\s+총\s*수량[^\d]{0,30}"
            r"(\d{1,3})(?:\s*개)?\s+(\d{1,3})(?:\s*개)?(?!\d|\s*[,.원₩])",
            text,
        )
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

    # Transport tickets commonly express quantity as ``총매수: 2매`` rather
    # than a retail-style ``총품목/총수량`` pair. This establishes the total
    # quantity only; it does not prove how many distinct item rows exist.
    if stated_total_quantity is None:
        ticket_count_match = re.search(
            r"총\s*매수\s*[:：]?\s*(\d{1,3})\s*매(?:\s|$)",
            text,
            re.IGNORECASE,
        )
        if ticket_count_match:
            stated_total_quantity = int(ticket_count_match.group(1))
    if stated_total_quantity is None:
        passenger_counts = [
            int(match.group(1))
            for match in re.finditer(
                r"(?:어른|성인|어린이|소아|유아|청소년)\s*[:：]?\s*(\d{1,3})\s*(?:매|명)(?=\s|[,/|·]|$)",
                text,
                re.IGNORECASE,
            )
        ]
        passenger_total = sum(passenger_counts)
        if passenger_counts and passenger_total > 0:
            stated_total_quantity = passenger_total

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
        "total_amount_source": total_amount_source,
        "discount_amount": labeled_discount,
        "amount_relation": amount_relation,
        "document_type": document_type,
        "document_type_source": document_type_source,
        "document_type_confidence": document_type_confidence,
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
        is_host_facility = any(host in compact_merchant for host in host_facilities)
        if not merchant or is_host_facility:
            return canonical
    merchant = re.sub(r"\s*\((?:과세|면세)\)\s*$", "", merchant, flags=re.IGNORECASE).strip()
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
        "card_number", "description", "receipt_summary", "items", "needs_review",
        "classification_review_reason", "card_number_evidence",
    )
    return {
        key: json.loads(json.dumps(result.get(key), ensure_ascii=False))
        for key in summary_fields
        if key in result
    }


_CARD_NUMBER_LABEL_PATTERN = re.compile(
    r"(?:카드\s*(?:번호|no\.?)|card\s*(?:number|no\.?|#))\s*[:：]?\s*"
    r"([0-9xX*＊•●][0-9xX*＊•●\t -]{10,30}[0-9xX*＊•●])",
    re.IGNORECASE,
)


def _ground_masked_card_number(model_value: Any, text: str) -> tuple[str | None, dict[str, Any]]:
    """Accept a card number only when OCR has both an explicit label and masking."""
    candidates: list[str] = []
    for match in _CARD_NUMBER_LABEL_PATTERN.finditer(text or ""):
        candidate = re.sub(r"[\t ]+", "", match.group(1)).strip("-")
        slots = re.sub(r"[^0-9xX*＊•●]", "", candidate)
        digit_count = len(re.findall(r"\d", slots))
        mask_count = len(re.findall(r"[xX*＊•●]", slots))
        if 12 <= len(slots) <= 19 and digit_count >= 4 and mask_count >= 1:
            candidates.append(candidate)

    unique_candidates = list(dict.fromkeys(candidates))
    accepted = unique_candidates[0] if len(unique_candidates) == 1 else None
    diagnostic = {
        "policy": "explicit_label_and_mask_required",
        "accepted": accepted is not None,
        "ocr_candidates": unique_candidates,
        "model_value_rejected": bool(model_value) and str(model_value).strip() != accepted,
        "reason": (
            "unique_labeled_masked_ocr_candidate"
            if accepted
            else "ambiguous_labeled_masked_candidates"
            if unique_candidates
            else "missing_explicit_label_or_mask"
        ),
    }
    return accepted, diagnostic


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
        "할인액", "할인판매", "할인적용액", "할인전주유단가", "쿠폰", "자동쿠폰",
        "적립금", "캐시백", "자동캐시백", "거스름돈", "카드번호", "사업자번호",
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

    rejected_adjustment_items = [
        item for item in items
        if isinstance(item, dict)
        and re.search(
            r"할인\s*(?:판매|적용액|전\s*(?:주유)?\s*단가)|(?:자동\s*)?(?:캐시백|쿠폰)",
            str(item.get("name") or ""),
            re.IGNORECASE,
        )
    ]
    items = [
        item for item in items
        if is_real_item(item) and item not in rejected_adjustment_items
    ]
    item_diagnostics = result.get("item_extraction_diagnostics")
    if rejected_adjustment_items:
        if not isinstance(item_diagnostics, dict):
            item_diagnostics = {}
            result["item_extraction_diagnostics"] = item_diagnostics
        item_diagnostics["rejected_adjustment_items"] = json.loads(
            json.dumps(rejected_adjustment_items, ensure_ascii=False)
        )
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
                if field == "quantity":
                    item[field] = _quantity_number(raw_value)
                else:
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
            "unit": candidate.get("unit"),
            "unit_price": _clean_number(candidate.get("unit_price_candidate")),
            "total_amount": _clean_number(candidate.get("amount_candidate")),
            "candidate_type": candidate.get("candidate_type"),
            "arithmetic_tolerance": candidate.get("arithmetic_tolerance"),
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
    finalized_items = result["items"]
    stated_total_quantity = hints.get("stated_total_quantity")
    if stated_total_quantity is not None:
        result["total_quantity"] = stated_total_quantity
    elif finalized_items and all(item.get("quantity") is not None for item in finalized_items):
        # The item pass can resolve every quantity even when the receipt does
        # not print a separate total-quantity summary (common for services).
        result["total_quantity"] = sum(
            _clean_number(item.get("quantity")) for item in finalized_items
        )
    if stated_item_count:
        receipt_summary.update({
            "stated_item_count": int(stated_item_count),
            "stated_total_quantity": hints.get("stated_total_quantity"),
            "stated_total_amount": hints.get("stated_total_amount"),
        })
        result["receipt_summary"] = receipt_summary
    model_doc_type = result.get("doc_type") or result.get("document_type")
    candidate_category = _normalize_expense_category(
        result.get("expense_category") or hints.get("expense_category"), text,
    )
    document_type, expense_category, needs_review, review_reason = validate_classification(
        model_doc_type,
        candidate_category,
        result.get("needs_review", False),
        deterministic_doc_type=hints.get("document_type"),
        deterministic_source=hints.get("document_type_source"),
    )
    category_document_type = CATEGORY_TO_DOCUMENT_TYPE.get(expense_category)
    result["classification_decision"] = {
        "expense_category": expense_category,
        "category_document_type": category_document_type,
        "model_document_type": str(model_doc_type or "").strip().upper() or None,
        "deterministic_document_type": hints.get("document_type"),
        "deterministic_source": hints.get("document_type_source"),
        "deterministic_confidence": hints.get("document_type_confidence"),
        "selected_document_type": document_type,
        "status": "CONFLICT" if review_reason == "category_document_type_conflict" else "REVIEW_REQUIRED" if needs_review else "AGREED",
        "reason": review_reason,
    }
    def prefer_evidenced_model_amount(field: str) -> float:
        model_value = _clean_number(result.get(field))
        hint_value = _clean_number(hints.get(field))
        # An explicitly labelled final/approved/paid amount is stronger than
        # an arbitrary OCR number selected by the model (often an item price).
        if field == "total_amount" and hints.get("total_amount_source") == "labeled_final" and hint_value >= 100:
            return hint_value
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
    grounded_card_number, card_number_evidence = _ground_masked_card_number(
        result.get("card_number"), text,
    )
    result["card_number"] = grounded_card_number
    result["card_number_evidence"] = card_number_evidence
    result["doc_type"] = document_type
    result["expense_category"] = expense_category
    result["needs_review"] = needs_review
    if review_reason:
        result["classification_review_reason"] = review_reason
    else:
        result.pop("classification_review_reason", None)
    normalized_record = {
        "document_type": document_type,
        "expense_category": expense_category,
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

__all__ = [name for name in globals() if not name.startswith("__")]
