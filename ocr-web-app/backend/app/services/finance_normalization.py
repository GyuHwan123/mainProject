from __future__ import annotations

import re
from typing import Any

from app.constants.finance_taxonomy import normalize_expense_category


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())


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
        canonical = normalize_expense_category(value)
        return _compact(canonical) if canonical else compact
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
