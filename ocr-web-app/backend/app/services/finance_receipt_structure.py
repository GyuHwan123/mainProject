from __future__ import annotations

import re
from typing import Any


_COMPACT_RE = re.compile(r"[^0-9A-Za-z가-힣]")
_NUMBER_RE = re.compile(r"[-+]?\d[\d,.]*")
_ROLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("TAX", re.compile(r"공급가액|부가세|부가가치세|과세|면세|세액", re.I)),
    ("PAYMENT", re.compile(r"신용카드|체크카드|현금|간편결제|카드번호|승인번호|할부|일시불", re.I)),
    ("SETTLEMENT", re.compile(r"최종\s*결제|결제금액|받을\s*금액|청구금액|승인금액|총\s*합계|합계금액|소계|거스름돈", re.I)),
    ("ITEM_ADJUSTMENT", re.compile(r"할인|쿠폰|포인트|캐시백|에누리", re.I)),
    ("METADATA", re.compile(r"사업자|대표자|주소|전화|TEL\b|거래번호|판매번호|영수증번호|Help\s*Desk|고객센터|https?://|www\.", re.I)),
)


def _compact(value: Any) -> str:
    return _COMPACT_RE.sub("", str(value or "")).lower()


def _number(value: Any) -> float | None:
    match = _NUMBER_RE.search(str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _line_roles(line: dict[str, Any], candidate_indexes: list[int], main_indexes: set[int]) -> list[dict[str, Any]]:
    text = str(line.get("text") or "")
    roles = [{"role": role, "confidence": .98, "source": "label_rule"}
             for role, pattern in _ROLE_PATTERNS if pattern.search(text)]
    if candidate_indexes:
        role = "ITEM_MAIN" if any(index in main_indexes for index in candidate_indexes) else "ITEM_CONTINUATION"
        roles.append({"role": role, "confidence": .95, "source": "candidate_anchor"})
    if not roles:
        roles.append({"role": "UNKNOWN", "confidence": .2, "source": "unclassified"})
    return roles


def _candidate_line_indexes(lines: list[dict[str, Any]], candidate: dict[str, Any]) -> list[int]:
    name = _compact(candidate.get("name_candidate"))
    raw_tokens = [_compact(cell) for cell in candidate.get("raw_cells") or []
                  if len(_compact(cell)) >= 2 and re.search(r"[A-Za-z가-힣]", str(cell or ""))]
    matched: list[int] = []
    for index, line in enumerate(lines):
        compact_line = _compact(line.get("text"))
        if not compact_line:
            continue
        if name and (name in compact_line or compact_line in name):
            matched.append(index)
        elif any(token in compact_line or compact_line in token for token in raw_tokens):
            matched.append(index)
    if matched and candidate.get("source") in {
        "discounted_item_block", "fuel_sale_block", "unresolved_title", "incomplete_item",
    }:
        start = min(matched)
        for index in range(start + 1, min(start + 5, len(lines))):
            if re.search(r"소계|합계|공급가액|부가세|결제금액|승인금액|받을\s*금액", str(lines[index].get("text") or ""), re.I):
                break
            matched.append(index)
    return sorted(set(matched))


def _field(value: Any, row_ids: list[str], confidence: float, source: str) -> dict[str, Any] | None:
    if value is None or value == "":
        return None
    return {"value": value, "confidence": round(confidence, 2), "source": source, "row_ids": row_ids}


def _item_structure(candidate: dict[str, Any]) -> str:
    candidate_type = str(candidate.get("candidate_type") or "")
    source = str(candidate.get("source") or "")
    if candidate_type in {"fuel_sale_item", "measured_quantity_item"} or source == "fuel_sale_block":
        return "MEASURED_SALE"
    if source == "discounted_item_block" or candidate.get("discount_amount_candidate"):
        return "DISCOUNT_CHAIN"
    if candidate.get("option_candidates") or candidate.get("product_code"):
        return "OPTION_CHAIN"
    if candidate.get("structure_type") == "unitemized_charge":
        return "SERVICE"
    return "STANDARD_ROW"


def _field_confidence(candidate: dict[str, Any], field_name: str) -> float:
    base = {"H": .95, "M": .7, "L": .25}.get(str(candidate.get("rel") or "M").upper(), .7)
    resolution = str(candidate.get(f"{field_name}_resolution") or "")
    return min(base, .55) if any(token in resolution for token in ("default", "inferred", "derived")) else base


def _field_source(candidate: dict[str, Any], field_name: str) -> str:
    resolution = str(candidate.get(f"{field_name}_resolution") or "")
    if "inferred" in resolution or "default" in resolution or candidate.get("inferred"):
        return "inferred"
    return "derived" if "derived" in resolution else "observed"


def _structured_receipt_evidence(lines: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a loss-preserving row/block graph without changing candidate compatibility."""
    matches = [_candidate_line_indexes(lines, candidate) for candidate in candidates]
    reverse: dict[int, list[int]] = {}
    main_matches: dict[int, set[int]] = {}
    for candidate_index, indexes in enumerate(matches):
        if indexes:
            main_matches.setdefault(indexes[0], set()).add(candidate_index)
        for index in indexes:
            reverse.setdefault(index, []).append(candidate_index)

    rows = []
    for index, line in enumerate(lines):
        row = {
            "row_id": line.get("id") or f"R{index + 1:03d}", "text": line.get("text") or "",
            "roles": _line_roles(line, reverse.get(index, []), main_matches.get(index, set())),
            "numbers": [value for value in (_number(match.group(0)) for match in _NUMBER_RE.finditer(str(line.get("text") or ""))) if value is not None],
        }
        for key in ("page", "bbox"):
            if line.get(key) is not None:
                row[key] = line[key]
        rows.append(row)

    item_blocks, consumed = [], set()
    for index, candidate in enumerate(candidates):
        row_ids = [rows[row_index]["row_id"] for row_index in matches[index]]
        consumed.update(row_ids)
        fields = {}
        for output_name, candidate_name in (("name", "name"), ("quantity", "quantity"), ("unit_price", "unit_price"), ("amount", "amount"), ("discount", "discount_amount")):
            value = candidate.get(f"{candidate_name}_candidate")
            field = _field(value, row_ids, _field_confidence(candidate, candidate_name), _field_source(candidate, candidate_name))
            if field:
                fields[output_name] = field
        parent_id = row_ids[0] if row_ids else None
        relations = [{"from": child_id, "to": parent_id, "type": "PART_OF_ITEM"} for child_id in row_ids[1:]] if parent_id else []
        item_blocks.append({
            "block_id": f"B{index + 1:03d}", "kind": "ITEM", "structure": _item_structure(candidate),
            "row_ids": row_ids, "relations": relations, "fields": fields, "candidate_ref": index,
        })

    settlement_rows = [row for row in rows if row["row_id"] not in consumed and any(
        role["role"] in {"SETTLEMENT", "TAX", "PAYMENT"} for role in row["roles"]
    )]
    structures = {block["structure"] for block in item_blocks}
    receipt_structure = next(iter(structures)) if len(structures) == 1 else "MIXED" if structures else "UNKNOWN"
    return {
        "receipt_structure": receipt_structure, "rows": rows, "item_blocks": item_blocks,
        "non_item_blocks": ([{"block_id": "S001", "kind": "SETTLEMENT", "structure": "SETTLEMENT_SUMMARY",
                              "row_ids": [row["row_id"] for row in settlement_rows]}] if settlement_rows else []),
    }
