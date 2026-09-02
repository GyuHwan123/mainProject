from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any


def receipt_fingerprint(text: str) -> str:
    canonical = re.sub(r"[^0-9a-z\uac00-\ud7a3]", "", str(text or "").lower())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def receipt_hints(text: str, filename: str) -> dict[str, Any]:
    del filename
    date_match = re.search(r"(?<!\d)(20\d{2})[-./]\s*(\d{1,2})[-./]\s*(\d{1,2})(?!\d)", text or "")
    transaction_date = None
    if date_match:
        try:
            transaction_date = date(*(int(value) for value in date_match.groups())).isoformat()
        except ValueError:
            pass
    amounts = [
        int(re.sub(r"\D", "", token))
        for token in re.findall(r"(?<!\d)(?:\d{1,3}(?:,\d{3})+|\d{3,8})(?!\d)", text or "")
    ]
    return {"transaction_date": transaction_date, "total_amount": max(amounts, default=0)}


def receipt_identity_key(text: str, hints: dict[str, Any]) -> str | None:
    reference = re.search(
        r"(?:\uc2b9\uc778|\uac70\ub798|\uc8fc\ubb38)\s*\ubc88\ud638\s*[:\uff1a]?\s*([0-9A-Za-z*-]{4,})",
        text or "",
        re.IGNORECASE,
    )
    if not reference:
        return None
    value = re.sub(r"[^0-9A-Za-z]", "", reference.group(1)).lower()
    if len(value) < 4:
        return None
    raw = f"{value}|{hints.get('transaction_date') or ''}|{hints.get('total_amount') or 0}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def legacy_receipt_key(record: dict[str, Any]) -> str | None:
    data = record.get("structured_data") or {}
    filename = re.sub(r"\s*\(\d+\)(?=\.[^.]+$)", "", str(data.get("source_filename") or "").lower())
    filename = re.sub(r"[^0-9a-z\uac00-\ud7a3]", "", filename)
    transaction_date = str(record.get("transaction_date") or "")
    total = round(float(record.get("total_amount") or 0), 2)
    supply = round(float(record.get("supply_amount") or 0), 2)
    tax = round(float(record.get("tax_amount") or 0), 2)
    if not filename or not transaction_date or total <= 0:
        return None
    return f"{filename}|{transaction_date}|{supply}|{tax}|{total}"
