from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.constants.finance_taxonomy import normalize_expense_category


def normalize_date(value: Any) -> str | None:
    """Return an unambiguous calendar date as YYYY-MM-DD.

    Receipt-specific short years are interpreted as 20YY. A slash-separated
    year-last value uses the explicit US MM/DD/YYYY policy.
    """
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None

    compact_match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    separated_match = re.fullmatch(
        r"(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})"
        r"(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?",
        text,
    )
    korean_match = re.fullmatch(
        r"(\d{4})\s*\ub144\s*(\d{1,2})\s*\uc6d4\s*(\d{1,2})\s*\uc77c"
        r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?",
        text,
    )
    short_year_match = re.fullmatch(r"(\d{2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})", text)
    us_year_last_match = re.fullmatch(r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})", text)
    match = compact_match or separated_match or korean_match
    if short_year_match:
        parts = (2000 + int(short_year_match.group(1)), int(short_year_match.group(2)), int(short_year_match.group(3)))
    elif us_year_last_match:
        parts = (int(us_year_last_match.group(3)), int(us_year_last_match.group(1)), int(us_year_last_match.group(2)))
    elif match:
        parts = tuple(int(part) for part in match.groups())
    else:
        return None
    try:
        return date(*parts).isoformat()
    except ValueError:
        return None


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())


MERCHANT_ALIASES = {
    "korail": "한국철도공사",
    "한국철도공사": "한국철도공사",
    "sk네트웍스": "sk네트웍스",
    "sk네트웍스직영엔크린주유소": "sk네트웍스",
}


def semantic_normalized_value(field: str, value: Any) -> str | None:
    if field == "transaction_date":
        return normalize_date(value)
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
