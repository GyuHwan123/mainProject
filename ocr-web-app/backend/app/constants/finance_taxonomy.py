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
    "외식/식사",
    "카페/음료",
    "식품/장보기",
    "생활용품",
    "의류/패션",
    "취미/선물",
    "미용/뷰티",
    "도서",
    "전자제품/문구",
    "대중교통",
    "주유/차량",
    "의료",
    "문화",
    "레저/스포츠",
)

CATEGORY_TO_DOCUMENT_TYPE = {
    "외식/식사": "WELFARE_BENEFIT",
    "카페/음료": "WELFARE_BENEFIT",
    "식품/장보기": "WELFARE_BENEFIT",
    "생활용품": "PURCHASE_REQUEST",
    "의류/패션": "PURCHASE_REQUEST",
    "취미/선물": "PURCHASE_REQUEST",
    "미용/뷰티": "WELFARE_BENEFIT",
    "도서": "WELFARE_BENEFIT",
    "전자제품/문구": "PURCHASE_REQUEST",
    "대중교통": "TRAVEL_EXPENSE",
    "주유/차량": "TRAVEL_EXPENSE",
    "의료": "WELFARE_BENEFIT",
    "문화": "WELFARE_BENEFIT",
    "레저/스포츠": "WELFARE_BENEFIT",
}

# Compact accounting policies for the summary LLM. These define transaction
# boundaries without accumulating merchant-specific or receipt-specific examples.
CATEGORY_CLASSIFICATION_POLICIES = {
    "외식/식사": "식당에서 조리된 음식·식사·안주를 주문한 거래. 카페 음료와 포장 식품 장보기는 제외",
    "카페/음료": "카페·베이커리에서 커피·차·주스·디저트·음료를 주문한 거래",
    "식품/장보기": "마트·편의점·식품점에서 포장 식품·간식·음료·주류를 구매한 거래",
    "생활용품": "물티슈·세제·주방·욕실·청소·봉투 등 일상 소모품과 생활 잡화 구매",
    "의류/패션": "의류·신발·가방·패션 액세서리 구매 거래",
    "취미/선물": "공예·게임·꽃·식물·기념품·선물 등 취미 또는 선물 목적 상품 구매",
    "미용/뷰티": "헤어·네일 등 미용 서비스 또는 화장품·피부·모발 관리 제품 거래",
    "도서": "책·서적·출판물 구매 거래",
    "전자제품/문구": "전자기기·컴퓨터 주변기기·사무용품·문구 구매 거래",
    "대중교통": "택시·버스·철도·항공 등 승객 운송·승차권 거래",
    "주유/차량": "휘발유·경유·LPG 주유 또는 차량 정비·유지 거래",
    "의료": "진료·검사·치료·의약품 등 의료 목적 거래",
    "문화": "공연·영화·전시 등 문화 콘텐츠 이용 거래",
    "레저/스포츠": "골프·스포츠·여가 활동·숙박·리조트 이용 거래",
}

# Only unambiguous variants are accepted. The canonical labels above exactly
# match receipt_dataset_verified/receipts.json.
LEGACY_CATEGORY_ALIASES = {
    "교통비": "대중교통",
    "여비교통비": "대중교통",
    "차량유지비": "주유/차량",
    "도서인쇄비": "도서",
    "도서인쇄": "도서",
    "복리후생": "외식/식사",
    "복리후생비(간식)": "식품/장보기",
    "복리후생비(식대)": "외식/식사",
    "출장식비": "외식/식사",
    "출장식대": "외식/식사",
    "출장식사": "외식/식사",
    "회의비": "외식/식사",
    # Removed overlapping canonical labels remain safe input aliases.
    "교통": "대중교통",
    "주유/교통": "주유/차량",
    "미용": "미용/뷰티",
    "미용/생활": "미용/뷰티",
    "뷰티/쇼핑": "미용/뷰티",
    "전자제품": "전자제품/문구",
    "식비": "외식/식사",
    "식비/주류": "식품/장보기",
    # Historical overlapping food labels now share one canonical category.
    "식비/생활": "식품/장보기",
    "생활/식비": "식품/장보기",
    "식비/쇼핑": "식품/장보기",
    "식품/쇼핑": "식품/장보기",
    "생활/쇼핑": "생활용품",
    "의류/쇼핑": "의류/패션",
    "꽃/식물": "취미/선물",
    "취미/쇼핑": "취미/선물",
    "레저": "레저/스포츠",
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


def refine_expense_category(value: Any, evidence_text: Any = None) -> str | None:
    """Refine broad legacy labels only when receipt evidence supports a child category."""
    canonical = normalize_expense_category(value)
    if canonical is None:
        return None
    raw = compact_taxonomy_value(value)
    evidence = str(evidence_text or "").lower()
    if raw == compact_taxonomy_value("식비"):
        if re.search(r"마트|편의점|슈퍼|food\s*market|gs\s*25|cu\b|세븐일레븐|이마트|홈플러스|재사용.*봉투|종량제", evidence, re.IGNORECASE):
            return "식품/장보기"
        if re.search(r"카페|coffee|커피|공차|투썸|팀홀튼|베이커리|라떼|아메리카노|스무디|휘낭시에", evidence, re.IGNORECASE):
            return "카페/음료"
        return "외식/식사"
    if raw == compact_taxonomy_value("취미/쇼핑"):
        if re.search(r"유니클로|\bcos\b|의류|셔츠|가디건|저지|원피스|바지|재킷|신발|가방", evidence, re.IGNORECASE):
            return "의류/패션"
        if re.search(r"물티슈|세제|생활용품|생활잡화|종량제|재사용.*봉투|스펀지|주방|욕실|청소", evidence, re.IGNORECASE):
            return "생활용품"
        return "취미/선물"
    if raw == compact_taxonomy_value("교통") and re.search(
        r"주유소|유종|휘발유|경유|등유|lpg|유류|리터|\d+(?:\.\d+)?\s*[lℓ]", evidence, re.IGNORECASE,
    ):
        return "주유/차량"
    return canonical


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
