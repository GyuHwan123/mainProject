from __future__ import annotations

import re
from typing import Any


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())


CATEGORY_ALIASES = {
    "교통": "교통",
    "교통비": "교통",
    "식비": "식비",
    "생활식비": "식비",
    "식비생활": "식비",
    "식비쇼핑": "식비",
    "취미여가": "취미쇼핑",
    "취미쇼핑": "취미쇼핑",
    "생활쇼핑": "취미소품",
    "취미소품": "취미소품",
    "의류쇼핑": "취미쇼핑",
}

MERCHANT_ALIASES = {
    "korail": "한국철도공사",
    "한국철도공사": "한국철도공사",
    "sk네트웍스": "sk네트웍스",
    "sk네트웍스직영엔크린주유소": "sk네트웍스",
}


def semantic_normalized_value(field: str, value: Any) -> str | None:
    compact = _compact(value)
    if not compact:
        return None
    if field == "expense_category":
        return CATEGORY_ALIASES.get(compact, compact)
    if field == "merchant":
        return MERCHANT_ALIASES.get(compact, compact)
    if field == "name":
        ktx = re.search(r"ktx0*(\d+)", compact)
        if ktx:
            return f"ktx{int(ktx.group(1))}"
    return compact


def normalization_equivalent(field: str, expected: Any, actual: Any) -> bool:
    if expected is None or actual is None:
        return False
    expected_normalized = semantic_normalized_value(field, expected)
    actual_normalized = semantic_normalized_value(field, actual)
    return bool(expected_normalized and expected_normalized == actual_normalized)
