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
    "교통비",
    "도서인쇄비",
    "복리후생비(간식)",
    "복리후생비(식대)",
    "비품비",
    "소모품비",
    "여비교통비",
    "운반비",
    "인쇄비",
    "지급수수료",
    "차량유지비",
    "출장숙박비",
    "출장식비",
    "통신비",
    "회의비",
)

CATEGORY_TO_DOCUMENT_TYPE = {
    "교통비": "EXPENSE_REPORT",
    "도서인쇄비": "EXPENSE_REPORT",
    "복리후생비(간식)": "WELFARE_BENEFIT",
    "복리후생비(식대)": "WELFARE_BENEFIT",
    "비품비": "PURCHASE_REQUEST",
    "소모품비": "PURCHASE_REQUEST",
    "여비교통비": "TRAVEL_EXPENSE",
    "운반비": "EXPENSE_REPORT",
    "인쇄비": "EXPENSE_REPORT",
    "지급수수료": "EXPENSE_REPORT",
    "차량유지비": "EXPENSE_REPORT",
    "출장숙박비": "TRAVEL_EXPENSE",
    "출장식비": "TRAVEL_EXPENSE",
    "통신비": "EXPENSE_REPORT",
    "회의비": "EXPENSE_REPORT",
}

# Only unambiguous spelling/label variants are accepted. Broad legacy labels
# such as 기타, 식비, 숙박비, 복리후생 and 취미/쇼핑 intentionally remain
# unmapped because choosing a canonical category would require guessing.
LEGACY_CATEGORY_ALIASES = {
    "도서인쇄": "도서인쇄비",
    "비품": "비품비",
    "소모품": "소모품비",
    "사무용품": "소모품비",
    "여비교통": "여비교통비",
    "출장숙박": "출장숙박비",
    "출장식대": "출장식비",
    "출장식사": "출장식비",
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
    if bool(needs_review):
        return None, None, True, "model_requested_review"

    category = normalize_expense_category(expense_category)
    if category is None:
        return None, None, True, "invalid_expense_category"

    normalized_doc_type = str(doc_type or "").strip().upper()
    if normalized_doc_type not in ALLOWED_DOCUMENT_TYPES:
        return None, None, True, "invalid_document_type"

    expected_doc_type = CATEGORY_TO_DOCUMENT_TYPE[category]
    if normalized_doc_type != expected_doc_type:
        return None, None, True, "category_document_type_mismatch"

    return normalized_doc_type, category, False, None


if set(CATEGORY_TO_DOCUMENT_TYPE) != set(ALLOWED_EXPENSE_CATEGORIES):
    raise RuntimeError("Every canonical expense category must have one document type")
if not set(CATEGORY_TO_DOCUMENT_TYPE.values()).issubset(ALLOWED_DOCUMENT_TYPES):
    raise RuntimeError("Category mapping contains an unknown document type")
