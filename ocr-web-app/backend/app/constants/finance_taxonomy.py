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
    "식비",
    "레저",
    "의료",
    "문화",
)

CATEGORY_TO_DOCUMENT_TYPE = {
    "취미/쇼핑": "PURCHASE_REQUEST",
    "미용": "WELFARE_BENEFIT",
    "도서": "WELFARE_BENEFIT",
    "전자제품/문구": "PURCHASE_REQUEST",
    "교통": "TRAVEL_EXPENSE",
    "식비": "WELFARE_BENEFIT",
    "레저": "WELFARE_BENEFIT",
    "의료": "WELFARE_BENEFIT",
    "문화": "WELFARE_BENEFIT",
}

# Compact accounting policies for the summary LLM. These define transaction
# boundaries without accumulating merchant-specific or receipt-specific examples.
CATEGORY_CLASSIFICATION_POLICIES = {
    "취미/쇼핑": "취미·의류·선물·일반 쇼핑 거래. 다른 전용 카테고리의 대상이 명확하면 제외",
    "미용": "외모·모발·피부·손발 관리 거래. 관련 상품만 보이면 상호 업종만으로 확정하지 않음",
    "도서": "책·서적·출판물 구매 거래",
    "전자제품/문구": "전자기기·컴퓨터 주변기기·사무용품·문구 구매 거래",
    "교통": "승객 운송·승차 또는 차량 연료·유지 거래",
    "식비": "식사·식품·간식·음료·주류 구매 거래",
    "레저": "스포츠·여가 활동·숙박 이용 거래",
    "의료": "진료·검사·치료·의약품 등 의료 목적 거래",
    "문화": "공연·영화·전시 등 문화 콘텐츠 이용 거래",
}

# Only unambiguous variants are accepted. The canonical labels above exactly
# match receipt_dataset_verified/receipts.json.
LEGACY_CATEGORY_ALIASES = {
    "교통비": "교통",
    "여비교통비": "교통",
    "차량유지비": "교통",
    "도서인쇄비": "도서",
    "도서인쇄": "도서",
    "복리후생": "식비",
    "복리후생비(간식)": "식비",
    "복리후생비(식대)": "식비",
    "출장식비": "식비",
    "출장식대": "식비",
    "출장식사": "식비",
    "회의비": "식비",
    # Removed overlapping canonical labels remain safe input aliases.
    "주유/교통": "교통",
    "미용/생활": "미용",
    "전자제품": "전자제품/문구",
    "식비/주류": "식비",
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
    *,
    deterministic_doc_type: Any = None,
    deterministic_source: Any = None,
    allow_explicit_document_type: bool = False,
) -> tuple[str | None, str | None, bool, str | None]:
    """Resolve category-first classification and surface conflicting signals."""
    normalized_doc_type = str(doc_type or "").strip().upper()
    if normalized_doc_type not in ALLOWED_DOCUMENT_TYPES:
        normalized_doc_type = None
    normalized_deterministic = str(deterministic_doc_type or "").strip().upper()
    if normalized_deterministic not in ALLOWED_DOCUMENT_TYPES:
        normalized_deterministic = None
    category = normalize_expense_category(expense_category)

    # A user-reviewed pair is an explicit workflow decision. Receipt categories
    # describe what was purchased, while document types can additionally encode
    # business context (for example, food purchased during a trip).
    if allow_explicit_document_type and normalized_doc_type and category:
        return normalized_doc_type, category, False, None

    if bool(needs_review) and category is None:
        return None, None, True, "model_requested_review"
    if category is None:
        document_type = normalized_deterministic or normalized_doc_type
        return document_type, None, True, "invalid_expense_category"

    category_document_type = CATEGORY_TO_DOCUMENT_TYPE[category]
    if bool(needs_review):
        document_type = normalized_deterministic or category_document_type
        return document_type, category, True, "model_requested_review"

    signals = [value for value in (normalized_doc_type, normalized_deterministic) if value]
    if any(value != category_document_type for value in signals):
        # Strong filename business context can select the working document, but
        # the category mismatch remains visible and must be reviewed.
        document_type = (
            normalized_deterministic
            if deterministic_source == "FILENAME_BUSINESS_CONTEXT" and normalized_deterministic
            else category_document_type
        )
        return document_type, category, True, "category_document_type_conflict"

    if not normalized_doc_type and not normalized_deterministic:
        return category_document_type, category, False, "document_type_derived_from_category"

    return category_document_type, category, False, None


if set(CATEGORY_TO_DOCUMENT_TYPE) != set(ALLOWED_EXPENSE_CATEGORIES):
    raise RuntimeError("Every canonical expense category must have one document type")
if set(CATEGORY_CLASSIFICATION_POLICIES) != set(ALLOWED_EXPENSE_CATEGORIES):
    raise RuntimeError("Every canonical expense category must have one classification policy")
if not set(CATEGORY_TO_DOCUMENT_TYPE.values()).issubset(ALLOWED_DOCUMENT_TYPES):
    raise RuntimeError("Category mapping contains an unknown document type")
