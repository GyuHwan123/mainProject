from __future__ import annotations

import re
from typing import Any


ALLOWED_DOCUMENT_TYPES = (
    "EXPENSE_REPORT", "PURCHASE_REQUEST", "TRAVEL_EXPENSE", "WELFARE_BENEFIT",
)

ALLOWED_EXPENSE_CATEGORIES = (
    "외식/식사", "카페/음료", "식품/장보기", "생활용품", "의류/패션",
    "취미/선물", "미용/뷰티", "도서", "전자제품/문구", "대중교통",
    "주유/차량", "의료", "문화", "레저/스포츠",
)

CATEGORY_TO_DOCUMENT_TYPE = {
    "외식/식사": "WELFARE_BENEFIT", "카페/음료": "WELFARE_BENEFIT",
    "식품/장보기": "WELFARE_BENEFIT", "생활용품": "PURCHASE_REQUEST",
    "의류/패션": "PURCHASE_REQUEST", "취미/선물": "PURCHASE_REQUEST",
    "미용/뷰티": "WELFARE_BENEFIT", "도서": "WELFARE_BENEFIT",
    "전자제품/문구": "PURCHASE_REQUEST", "대중교통": "TRAVEL_EXPENSE",
    "주유/차량": "TRAVEL_EXPENSE", "의료": "WELFARE_BENEFIT",
    "문화": "WELFARE_BENEFIT", "레저/스포츠": "WELFARE_BENEFIT",
}

CATEGORY_DECISION_RULES = (
    "실제 구매 품목·서비스를 상호명보다 우선한다. "
    "장소보다 거래 대상의 성격을 우선한다. "
    "외식/식사와 식품/장보기가 모두 가능하면 주문금액·주문내역·테이블·메뉴명·POS 또는 "
    "POS와 단가·수량·금액 표 구조가 있으면 외식/식사를 우선한다. "
    "마트·슈퍼·편의점·식자재·상품코드·바코드 중심 거래는 식품/장보기를 우선한다. "
    "카페 상호 또는 커피·차·스무디·디저트 주문 근거가 있으면 카페/음료를 우선한다. "
    "근거가 있으면 가장 구체적인 카테고리 하나를 선택한다."
)

CATEGORY_CLASSIFICATION_POLICIES = {
    "외식/식사": "식당·주점의 조리 음식·식사·안주. 주문금액·테이블·메뉴명·POS와 단가·수량·금액 표 구조는 강한 근거. 카페 음료와 마트·편의점 포장식품 제외",
    "카페/음료": "카페·베이커리의 커피·차·주스·음료·디저트 주문. 마트·편의점 포장음료 제외",
    "식품/장보기": "마트·편의점·슈퍼·식품점의 포장식품·간식·음료·주류·식재료. 음식점 POS의 조리메뉴 주문 제외",
    "생활용품": "세제·물티슈·주방·욕실·청소·봉투 등 생활 소모품·잡화",
    "의류/패션": "의류·신발·가방·패션 액세서리",
    "취미/선물": "공예·게임·꽃·식물·기념품·선물용 상품",
    "미용/뷰티": "헤어·네일 등 미용 서비스와 화장품·피부·모발 관리 제품",
    "도서": "책·서적·출판물",
    "전자제품/문구": "전자기기·컴퓨터 주변기기·사무용품·문구",
    "대중교통": "택시·버스·철도·항공 등 승객 운송·승차권",
    "주유/차량": "휘발유·경유·LPG 주유와 차량 정비·유지",
    "의료": "진료·검사·치료·약국·의약품",
    "문화": "영화·공연·전시 등 문화 콘텐츠 이용",
    "레저/스포츠": "골프·운동시설·스포츠·숙박·리조트·여가 활동",
}

LEGACY_CATEGORY_ALIASES = {
    "교통비": "대중교통", "여비교통비": "대중교통", "차량유지비": "주유/차량",
    "도서인쇄비": "도서", "도서인쇄": "도서", "복리후생": "외식/식사",
    "복리후생비(간식)": "식품/장보기", "복리후생비(식대)": "외식/식사",
    "출장식비": "외식/식사", "출장식대": "외식/식사", "출장식사": "외식/식사",
    "회의비": "외식/식사", "교통": "대중교통", "주유/교통": "주유/차량",
    "미용": "미용/뷰티", "미용/생활": "미용/뷰티", "뷰티/쇼핑": "미용/뷰티",
    "전자제품": "전자제품/문구", "식비": "외식/식사", "식비/주류": "식품/장보기",
    "식비/생활": "식품/장보기", "생활/식비": "식품/장보기", "식비/쇼핑": "식품/장보기",
    "식품/쇼핑": "식품/장보기", "생활/쇼핑": "생활용품", "의류/쇼핑": "의류/패션",
    "꽃/식물": "취미/선물", "취미/쇼핑": "취미/선물", "레저": "레저/스포츠",
    "비품비": "전자제품/문구", "소모품비": "전자제품/문구", "비품": "전자제품/문구",
    "소모품": "전자제품/문구", "사무용품": "전자제품/문구",
}


def compact_taxonomy_value(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())


_CATEGORY_BY_COMPACT = {compact_taxonomy_value(c): c for c in ALLOWED_EXPENSE_CATEGORIES}
_ALIASES_BY_COMPACT = {compact_taxonomy_value(a): c for a, c in LEGACY_CATEGORY_ALIASES.items()}


def normalize_expense_category(value: Any) -> str | None:
    compact = compact_taxonomy_value(value)
    if not compact:
        return None
    return _CATEGORY_BY_COMPACT.get(compact) or _ALIASES_BY_COMPACT.get(compact)


def _food_context_scores(evidence_text: Any) -> tuple[int, int, int, bool]:
    """Return (restaurant, grocery, cafe, strong_restaurant_structure)."""
    text = str(evidence_text or "").lower()
    restaurant = grocery = cafe = 0

    has_menu = bool(re.search(r"메뉴명|주문메뉴|메뉴내역", text, re.I))
    has_order = bool(re.search(r"주문금액|주문내역|주문번호|테이블(?:번호)?|홀주문", text, re.I))
    has_pos = bool(re.search(r"\bpos\b|p0s", text, re.I))
    has_item_table = all(re.search(token, text, re.I) for token in ("단가", "수량", "금액"))
    strong_restaurant_structure = has_pos and has_item_table

    if has_menu:
        restaurant += 2
    if has_order:
        restaurant += 2
    if has_menu and has_pos:
        restaurant += 2
    if strong_restaurant_structure:
        restaurant += 3

    restaurant_menu = (
        r"국밥|찌개|탕|돈까스|치킨|닭강정|떡볶이|라볶이|파스타|피자|"
        r"버거|햄버거|스테이크|삼겹|냉삼|소세지|소시지|어묵|감자튀김|"
        r"꼬치|포케|하이볼|생맥주|소주"
    )
    restaurant += min(3, len(re.findall(restaurant_menu, text, re.I)))

    if re.search(
        r"마트|편의점|슈퍼|식자재|food\s*market|gs\s*25|(?<![a-z])cu(?![a-z])|"
        r"세븐일레븐|이마트|홈플러스|롯데마트|코스트코",
        text, re.I,
    ):
        grocery += 3
    if re.search(r"상품코드|바코드|종량제|재사용.*봉투", text, re.I):
        grocery += 1

    if re.search(r"카페|coffee|공차|투썸|스타벅스|팀홀튼|커피빈|메가커피|컴포즈", text, re.I):
        cafe += 2
    if re.search(r"아메리카노|카페라떼|밀크티|스무디|휘낭시에|마카롱|마들렌|스콘|케이크|크루아상", text, re.I):
        cafe += 2
    cafe += min(2, len(re.findall(r"커피|라떼|주스|블랙티|아이스티|에이드|쿠키|디저트|베이커리", text, re.I)))

    return restaurant, grocery, cafe, strong_restaurant_structure


def refine_expense_category(value: Any, evidence_text: Any = None) -> str | None:
    canonical = normalize_expense_category(value)
    if canonical is None:
        return None

    raw = compact_taxonomy_value(value)
    text = str(evidence_text or "").lower()
    restaurant, grocery, cafe, strong_restaurant = _food_context_scores(text)

    if canonical in {"식품/장보기", "외식/식사", "카페/음료"}:
        if strong_restaurant and restaurant >= grocery and restaurant > cafe:
            return "외식/식사"
        if restaurant >= 3 and restaurant > grocery and restaurant > cafe:
            return "외식/식사"
        if cafe >= 3 and cafe > restaurant and cafe >= grocery:
            return "카페/음료"
        if cafe >= 2 and grocery == 0 and cafe > restaurant:
            return "카페/음료"
        if grocery >= 3 and grocery > restaurant and grocery > cafe:
            return "식품/장보기"
        return canonical

    if raw == compact_taxonomy_value("취미/쇼핑"):
        if re.search(r"유니클로|\bcos\b|의류|셔츠|가디건|원피스|바지|재킷|신발|가방", text, re.I):
            return "의류/패션"
        if re.search(r"물티슈|세제|생활용품|생활잡화|종량제|재사용.*봉투|스펀지|주방|욕실|청소", text, re.I):
            return "생활용품"
        return "취미/선물"

    if raw == compact_taxonomy_value("교통") and re.search(
        r"주유소|유종|휘발유|경유|등유|lpg|유류|리터|\d+(?:\.\d+)?\s*[lℓ]", text, re.I
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
    normalized_doc_type = str(doc_type or "").strip().upper()
    if normalized_doc_type not in ALLOWED_DOCUMENT_TYPES:
        normalized_doc_type = None

    normalized_deterministic = str(deterministic_doc_type or "").strip().upper()
    if normalized_deterministic not in ALLOWED_DOCUMENT_TYPES:
        normalized_deterministic = None

    category = normalize_expense_category(expense_category)

    if allow_explicit_document_type and normalized_doc_type and category:
        return normalized_doc_type, category, False, None
    if bool(needs_review) and category is None:
        return None, None, True, "model_requested_review"
    if category is None:
        return normalized_deterministic or normalized_doc_type, None, True, "invalid_expense_category"

    category_document_type = CATEGORY_TO_DOCUMENT_TYPE[category]

    if bool(needs_review):
        return normalized_deterministic or category_document_type, category, True, "model_requested_review"

    signals = [v for v in (normalized_doc_type, normalized_deterministic) if v]
    if any(v != category_document_type for v in signals):
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
