from __future__ import annotations

import re
from typing import Any


ALLOWED_DOCUMENT_TYPES = (
    "EXPENSE_REPORT",
    "PURCHASE_REQUEST",
    "TRAVEL_EXPENSE",
    "WELFARE_BENEFIT",
)

ALLOWED_EXPENSE_CATEGORIES = (
    "취미/쇼핑",
    "미용",
    "도서",
    "전자제품/문구",
    "교통",
    "주유/교통",
    "미용/생활",
    "식비",
    "레저",
    "전자제품",
    "식비/주류",
    "의료",
    "문화",
)

CATEGORY_TO_DOCUMENT_TYPE = {
    "취미/쇼핑": "PURCHASE_REQUEST",
    "미용": "WELFARE_BENEFIT",
    "도서": "WELFARE_BENEFIT",
    "전자제품/문구": "PURCHASE_REQUEST",
    "교통": "EXPENSE_REPORT",
    "주유/교통": "EXPENSE_REPORT",
    "미용/생활": "WELFARE_BENEFIT",
    "식비": "WELFARE_BENEFIT",
    "레저": "WELFARE_BENEFIT",
    "전자제품": "PURCHASE_REQUEST",
    "식비/주류": "WELFARE_BENEFIT",
    "의료": "WELFARE_BENEFIT",
    "문화": "WELFARE_BENEFIT",
}

# Only unambiguous variants are accepted. The canonical labels above exactly
# match receipt_dataset_verified/receipts.json.
LEGACY_CATEGORY_ALIASES = {
    "교통비": "교통",
    "여비교통비": "교통",
    "차량유지비": "주유/교통",
    "도서인쇄비": "도서",
    "도서인쇄": "도서",
    "복리후생": "식비",
    "복리후생비(간식)": "식비",
    "복리후생비(식대)": "식비",
    "출장식비": "식비",
    "출장식대": "식비",
    "출장식사": "식비",
    "회의비": "식비",
    # Historical overlapping food labels now share one canonical category.
    "식비/생활": "식비",
    "생활/식비": "식비",
    "식비/쇼핑": "식비",
    "비품비": "전자제품/문구",
    "소모품비": "전자제품/문구",
    "비품": "전자제품/문구",
    "소모품": "전자제품/문구",
    "사무용품": "전자제품/문구",
}


def compact_taxonomy_value(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())


_CATEGORY_BY_COMPACT = {
    compact_taxonomy_value(category): category
    for category in ALLOWED_EXPENSE_CATEGORIES
}
_ALIASES_BY_COMPACT = {
    compact_taxonomy_value(alias): category
    for alias, category in LEGACY_CATEGORY_ALIASES.items()
}


def normalize_expense_category(value: Any) -> str | None:
    """Return a canonical category, or None when normalization would guess."""
    compact = compact_taxonomy_value(value)
    if not compact:
        return None
    return _CATEGORY_BY_COMPACT.get(compact) or _ALIASES_BY_COMPACT.get(compact)


def validate_classification(
    doc_type: Any,
    expense_category: Any,
    needs_review: Any = False,
) -> tuple[str | None, str | None, bool, str | None]:
    """Validate the SFT classification contract without silently correcting it."""
    normalized_doc_type = str(doc_type or "").strip().upper()
    category = normalize_expense_category(expense_category)
    if bool(needs_review) and category is None:
        return None, None, True, "model_requested_review"
    if category is None:
        document_type = normalized_doc_type if normalized_doc_type in ALLOWED_DOCUMENT_TYPES else None
        return document_type, None, True, "invalid_expense_category"

    if bool(needs_review):
        document_type = normalized_doc_type if normalized_doc_type in ALLOWED_DOCUMENT_TYPES else CATEGORY_TO_DOCUMENT_TYPE[category]
        return document_type, category, True, "model_requested_review"
    if normalized_doc_type not in ALLOWED_DOCUMENT_TYPES:
        return CATEGORY_TO_DOCUMENT_TYPE[category], category, False, "document_type_derived_from_category"

    return normalized_doc_type, category, False, None


if set(CATEGORY_TO_DOCUMENT_TYPE) != set(ALLOWED_EXPENSE_CATEGORIES):
    raise RuntimeError("Every canonical expense category must have one document type")
if not set(CATEGORY_TO_DOCUMENT_TYPE.values()).issubset(ALLOWED_DOCUMENT_TYPES):
    raise RuntimeError("Category mapping contains an unknown document type")
