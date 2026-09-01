from __future__ import annotations

from app.services.finance_receipt_evidence import *
from app.services.finance_receipt_structure import _structured_receipt_evidence

def _receipt_table_hint(pages: list[dict[str, Any]] | None) -> str:
    tables = []
    for page in pages or []:
        for table_index, table in enumerate(page.get("tables") or [], start=1):
            if table.get("rows"):
                tables.append({
                    "page": page.get("page"),
                    "table": table_index,
                    "confidence": table.get("confidence"),
                    "rows": table["rows"],
                })
    return json.dumps(tables, ensure_ascii=False) if tables else "없음"


SEMANTIC_SECTIONS = (
    "issuer", "business_info", "transaction", "items", "service_detail",
    "adjustments", "tax_summary", "settlement", "payment", "auxiliary", "unknown",
)


def _compact_evidence_text(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or "")).lower()


_NON_ITEM_EVIDENCE = re.compile(
    r"카드\s*번호|승인\s*번호|거래\s*(?:일시|번호)|판매\s*(?:일시|번호)|주문\s*번호|"
    r"영수증\s*번호|승차권\s*번호|발행\s*일시|사업자|주소|소재지|대표자|TEL\b|FAX\b|"
    r"전화|합계|소계|결제|공급가액|부가세|세액|할인|쿠폰|포인트|플랫폼|에누리|증정|"
    r"매출전표|신용매출|SSGPAY|안내|법\s*제\d+조|Help\s*Desk|고객\s*센터|"
    r"금융결제원|여신금[융응]협회|현금영수증\s*문의|신고\s*안내|포상금|"
    r"고객\s*센터|Help\s*Desk",
    re.IGNORECASE,
)


def _looks_like_non_item_evidence(name: Any, raw: Any = None) -> bool:
    """Reject settlement/tax/meta rows even when OCR inserted spaces or noise."""
    name_text = str(name or "")
    raw_text = " ".join(filter(None, (name_text, str(raw or ""))))
    stable_non_item_labels = re.compile(
        r"(?:^|\s)(?:\uC18C\uACC4|\uD569\uACC4|\uCD1D\s*\uD569\uACC4|\uACB0\uC81C\s*\uAE08\uC561|"
        r"\uCD5C\uC885\s*\uACB0\uC81C|\uC2B9\uC778\s*\uAE08\uC561|\uCCAD\uAD6C\s*\uAE08\uC561|"
        r"\uACF5\uAE09\uAC00\uC561|\uBD80\uAC00\uC138|\uC138\uAE08|\uACFC\uC138|\uBA74\uC138|"
        r"\uD560\uC778|\uCFE0\uD3F0|\uAC70\uC2A4\uB984\uB3C8|\uCE74\uB4DC\s*\uBC88\uD638|"
        r"\uC2B9\uC778\s*\uBC88\uD638|\uAC70\uB798\s*\uBC88\uD638)(?:\s|:|$)",
        re.IGNORECASE,
    )
    if stable_non_item_labels.search(raw_text):
        return True
    compact_name = _compact_evidence_text(name_text)
    compact_raw = _compact_evidence_text(raw_text)
    exact_or_prefix = (
        "소계", "합계", "총합계", "결제금액", "승인금액", "받을금액", "총구매금액",
        "과세물품가액", "면세물품가액", "공급가액", "부가세", "부가세액", "세액",
        "할인액", "할인금액", "쿠폰", "포인트", "캐시백", "거스름돈",
    )
    if any(compact_name == token or compact_name.startswith(token) for token in exact_or_prefix):
        return True
    # OCR examples include ``부 사가 세``, ``한 계``, ``합인액`` and other
    # near-label fragments. Only apply fuzzy matching to short label-like names.
    if len(compact_name) <= 8 and re.search(
        r"(?:소.?계|한.?계|합.?계|합인액|부.?사?가.?세|과세.*가액|면세.*가액|공급.*가액)",
        re.sub(r"[^0-9A-Za-z가-힣]", "", raw_text),
        re.IGNORECASE,
    ):
        return True
    return bool(_NON_ITEM_EVIDENCE.search(raw_text) and any(
        token in compact_raw for token in ("합계", "소계", "부가세", "공급가액", "과세물품", "할인")
    ))


def _annotate_candidate_reliability(
    candidates: list[dict[str, Any]], hints: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Attach compact, explainable reliability grades without verbose prose."""
    hints = hints or {}
    stated_count = _clean_number(hints.get("stated_item_count"))
    hinted_total = _clean_number(hints.get("total_amount")) or _clean_number(hints.get("stated_total_amount"))
    candidate_total = sum(_clean_number(candidate.get("amount_candidate")) for candidate in candidates)
    count_matches = bool(stated_count and len(candidates) == int(stated_count))
    total_matches = bool(hinted_total >= 100 and abs(candidate_total - hinted_total) < .01)
    annotated: list[dict[str, Any]] = []
    for candidate in candidates:
        source = str(candidate.get("source") or "")
        quantity = _clean_number(candidate.get("quantity_candidate"))
        unit_price = _clean_number(candidate.get("unit_price_candidate"))
        amount = _clean_number(candidate.get("amount_candidate"))
        arithmetic_tolerance = _clean_number(candidate.get("arithmetic_tolerance")) or .01
        reasons: list[str] = []
        if source in {"table", "discounted_item_block", "single_amount_item_row"}:
            reasons.append("T")
        if source in {"item_region", "coordinate_row_fallback", "inline_arithmetic_fallback"}:
            reasons.append("R")
        if source == "fuel_sale_block":
            reasons.append("F")
        if source == "semantic_service_inference":
            reasons.append("D")
        if quantity > 0 and unit_price > 0 and amount > 0 and abs(quantity * unit_price - amount) <= arithmetic_tolerance:
            reasons.append("A")
        if count_matches:
            reasons.append("C")
        if total_matches:
            reasons.append("S")
        high = bool((("T" in reasons or "R" in reasons) and "A" in reasons) or "C" in reasons or "S" in reasons)
        if candidate.get("inferred"):
            high = False
        annotated.append({**candidate, "rel": "H" if high else "M", "why": reasons or ["E"]})
    return annotated


def _reliable_item_candidates(
    candidates: list[dict[str, Any]], hints: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep only product-like OCR rows before grounding or prompting the model."""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float, float, str]] = set()
    for candidate in candidates:
        name = str(candidate.get("name_candidate") or "").strip()
        amount = _clean_number(candidate.get("amount_candidate"))
        quantity = _clean_number(candidate.get("quantity_candidate"))
        unit_price = _clean_number(candidate.get("unit_price_candidate"))
        source = str(candidate.get("source") or "")
        raw = " ".join(str(cell or "") for cell in candidate.get("raw_cells") or [])
        arithmetic_tolerance = _clean_number(candidate.get("arithmetic_tolerance")) or .01
        arithmetic = bool(
            quantity > 0 and unit_price > 0 and amount > 0
            and abs(quantity * unit_price - amount) <= arithmetic_tolerance
        )
        structured_source = source in {
            "table", "item_region", "discounted_item_block", "single_amount_item_row", "fuel_sale_block",
            "inline_arithmetic_fallback", "coordinate_row_fallback",
            "semantic_service_inference",
        }
        compact_name = _compact_evidence_text(name)
        looks_like_location = bool(re.search(
            r"(?:서울|경기|인천|부산|대구|광주|대전|울산|세종|충북|충남|전북|전남|경북|경남|강원|제주|광역시|특별시)(?:\s|$)|(?:로|길)\s*\d+",
            name,
        ))
        looks_like_short_amount_label = len(compact_name) <= 4 and compact_name.endswith("금")
        looks_like_contact_or_identifier = bool(re.search(
            r"(?:TEL|전화|문의|협회|고객\s*센터|Help\s*Desk|신고\s*안내|포상금)|"
            r"(?:^|[^\d])\(?\d{2,4}\)?[- ]\d{3,4}[- ]\d{4}(?:[^\d]|$)",
            raw,
            re.IGNORECASE,
        ))
        reliable = bool(
            name and compact_name not in {"수", "금", "계"} and len(compact_name) >= 1 and amount >= 100
            and not _NON_ITEM_EVIDENCE.search(f"{name} {raw}")
            and not _looks_like_non_item_evidence(name, raw)
            and not looks_like_location and not looks_like_short_amount_label
            and not (looks_like_contact_or_identifier and not arithmetic)
            and (structured_source or arithmetic)
        )
        if source == "single_amount_item_row" and (amount < 1000 or re.search(r"행복을\s*만나다|가맹점|매장", name)):
            reliable = False
        if source == "single_amount_item_row" and any(str(cell or "").strip().startswith("-") for cell in candidate.get("raw_cells") or []):
            reliable = False
        if source == "ocr_line_unscoped" and not arithmetic:
            reliable = False
        if not reliable:
            rejected.append({**candidate, "rel": "L", "why": ["X"]})
            continue
        key = (_compact_evidence_text(name), quantity, unit_price, amount, _compact_evidence_text(raw))
        if key in seen:
            rejected.append({**candidate, "rel": "L", "why": ["D"], "rejection_reason": "duplicate_candidate"})
            continue
        accepted.append(candidate)
        seen.add(key)
    return _annotate_candidate_reliability(accepted, hints), rejected


def _ocr_line_registry(text: str, pages: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Build one canonical line dictionary so prompts can reference IDs instead of copying text."""
    lines: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append_line(value: Any, page: Any = None, bbox: Any = None, *, deduplicate: bool = True) -> None:
        line_text = " ".join(str(value or "").strip().split())
        key = _compact_evidence_text(line_text)
        if not key or (deduplicate and key in seen):
            return
        entry: dict[str, Any] = {"id": f"L{len(lines) + 1:03d}", "text": line_text}
        if page is not None:
            entry["page"] = page
        if bbox:
            entry["bbox"] = bbox
        lines.append(entry)
        seen.add(key)

    for page in pages or []:
        page_items = [item for item in (page.get("items") or []) if item.get("text")]
        if page_items:
            ordered = sorted(page_items, key=lambda item: (
                (item.get("bbox") or [[0, 0], [0, 0]])[0][1],
                (item.get("bbox") or [[0, 0], [0, 0]])[0][0],
            ))
            heights = [
                max((item.get("bbox") or [[0, 0], [0, 0]])[1][1] - (item.get("bbox") or [[0, 0], [0, 0]])[0][1], 1)
                for item in ordered
            ]
            tolerance = (sorted(heights)[len(heights) // 2] if heights else 10) * .45
            grouped: list[list[dict[str, Any]]] = []
            for item in ordered:
                bbox = item.get("bbox") or [[0, 0], [0, 0]]
                center_y = (bbox[0][1] + bbox[1][1]) / 2
                target = next((group for group in reversed(grouped[-3:]) if abs(
                    center_y - sum(((member.get("bbox") or [[0, 0], [0, 0]])[0][1] + (member.get("bbox") or [[0, 0], [0, 0]])[1][1]) / 2 for member in group) / len(group)
                ) <= tolerance), None)
                if target is None:
                    grouped.append([item])
                else:
                    target.append(item)
            for group in grouped:
                group.sort(key=lambda item: (item.get("bbox") or [[0, 0], [0, 0]])[0][0])
                boxes = [item.get("bbox") or [[0, 0], [0, 0]] for item in group]
                append_line(
                    " ".join(str(item.get("text") or "").strip() for item in group),
                    page.get("page"),
                    [[min(box[0][0] for box in boxes), min(box[0][1] for box in boxes)],
                     [max(box[1][0] for box in boxes), max(box[1][1] for box in boxes)]],
                    deduplicate=False,
                )
        else:
            for raw_line in str(page.get("text") or "").splitlines():
                append_line(raw_line, page.get("page"), deduplicate=False)

    # Use one representation only. Mixing box-grouped lines with flattened OCR
    # lines makes the same product appear to the model as multiple purchases.
    if not lines:
        for raw_line in str(text or "").splitlines():
            append_line(raw_line, deduplicate=False)
    return lines


def _semantic_receipt_evidence(
    text: str, pages: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assign conservative, multi-label semantic roles before either LLM call."""
    lines = _ocr_line_registry(text, pages)
    item_candidates = candidates if candidates is not None else _receipt_item_candidates(pages)
    sections: dict[str, list[str]] = {section: [] for section in SEMANTIC_SECTIONS}

    patterns = {
        "business_info": re.compile(r"사업자|사업자\s*(?:등록)?번호|대표자|주소|소재지|대표전화|전화번호|TEL\b", re.I),
        "transaction": re.compile(r"거래\s*(?:일시|일자|번호)|판매\s*(?:일시|일자|번호)|주문번호|영수증번호|승차권\s*번호|발행일시|POS\b|테이블\s*번호", re.I),
        "service_detail": re.compile(r"KTX|SRT|열차|승차|출발|도착|좌석|호차|입석|객실|숙박|체크인|체크아웃|진료|처방|환자", re.I),
        "adjustments": re.compile(r"할인|쿠폰|적립금|포인트|캐시백|배송비|봉투|봉사료|서비스료", re.I),
        "tax_summary": re.compile(r"공급가액|부가세|부가가치세|과세|면세|세액", re.I),
        "settlement": re.compile(r"최종\s*결제|결제금액|받을\s*금액|청구금액|승인금액|상품합계|총\s*합계|합계금액|소계|반환금액|환불금액|반환수수료|거스름돈", re.I),
        "payment": re.compile(r"신용카드|체크카드|현금|간편결제|카드번호|승인번호|승인일자|할부|일시불|결제수단|카드사", re.I),
        "auxiliary": re.compile(r"교환|환불\s*안내|유효기간|이벤트|설문|홈페이지|https?://|www\.|QR|바코드", re.I),
    }
    explicit_issuer = re.compile(r"(?:상호|상호명|가맹점|매장명|판매자|발행자)\s*[:：]?", re.I)
    candidate_tokens = []
    for candidate in item_candidates:
        name_token = _compact_evidence_text(candidate.get("name_candidate"))
        raw_token = _compact_evidence_text(" ".join(str(cell or "") for cell in candidate.get("raw_cells") or []))
        # Never use isolated quantity/price cells as text anchors. A product
        # name or a complete source row is specific enough to avoid tagging
        # every date/payment line as an item.
        candidate_tokens.extend(token for token in (name_token, raw_token) if len(token) >= 2)

    for line_index, line in enumerate(lines):
        line_id, line_text = line["id"], line["text"]
        compact = _compact_evidence_text(line_text)
        labels = [name for name, pattern in patterns.items() if pattern.search(line_text)]
        if explicit_issuer.search(line_text):
            labels.append("issuer")
        # A business-registration line identifies the issuer as well as carrying legal details.
        if re.search(r"사업자", line_text, re.I) and re.search(r"[A-Za-z가-힣]{2,}", line_text):
            labels.append("issuer")
        if any(token and (token in compact or compact in token) for token in candidate_tokens):
            labels.append("items")
        if (
            line_index < 6 and not labels and re.search(r"[A-Za-z가-힣]{2,}", line_text)
            and len(re.findall(r"\d{1,3}(?:[.,]\d{3})+|\d+", line_text)) <= 1
        ):
            # Keep short top-of-receipt names visible to the merchant pass even
            # when OCR omitted an explicit 상호/매장명 label.
            labels.append("issuer")
        if not labels:
            labels.append("unknown")
        for label in dict.fromkeys(labels):
            sections[label].append(line_id)

    structured = _structured_receipt_evidence(lines, item_candidates)
    return {
        "lines": lines,
        "sections": {key: value for key, value in sections.items() if value},
        "item_summary": {
            "candidate_count": len(item_candidates),
            "candidate_amount_sum": sum(_clean_number(item.get("amount_candidate")) for item in item_candidates) or None,
        },
        "structured_evidence": structured,
    }


def _semantic_prompt_payload(
    text: str, pages: list[dict[str, Any]] | None, candidates: list[dict[str, Any]], *, item_pass: bool,
) -> dict[str, Any]:
    evidence = _semantic_receipt_evidence(text, pages, candidates)
    sections = evidence["sections"]
    wanted = {"items", "adjustments", "settlement", "tax_summary"} if item_pass else set(SEMANTIC_SECTIONS) - {"items"}
    referenced_ids = {
        line_id for section, line_ids in sections.items() if section in wanted for line_id in line_ids
    }
    # Category selection should prioritize what was purchased over merchant
    # industry. Include only a bounded sample so summary latency stays stable.
    category_item_ids = list(sections.get("items") or [])[:8] if not item_pass else []
    referenced_ids.update(category_item_ids)
    payload: dict[str, Any] = {
        # Coordinates remain in semantic_evidence for diagnostics but do not
        # consume model context. Meaning selection needs only stable IDs/text.
        "lines": [
            {"id": line["id"], "text": line["text"]}
            for line in evidence["lines"] if line["id"] in referenced_ids
        ],
        "sections": {section: ids for section, ids in sections.items() if section in wanted and ids},
    }
    if category_item_ids:
        payload["sections"]["items"] = category_item_ids
    if item_pass:
        payload["item_candidates"] = candidates
        payload["item_summary"] = evidence["item_summary"]
        payload["structured_evidence"] = evidence["structured_evidence"]
    else:
        payload["item_summary"] = evidence["item_summary"]
    return payload


def _fuel_sale_item_candidate(
    text: str, hints: dict[str, Any], page_number: Any = None,
) -> dict[str, Any] | None:
    """Recover a fuel item whose name, volume, unit price, and total are split."""
    fuel_matches = list(re.finditer(
        r"(?:초저유황\s*경유|고급\s*휘발유|보통\s*휘발유|자동차용\s*경유|"
        r"휘발유|경유|등유|LPG|유류)",
        text,
        re.IGNORECASE,
    ))
    if not fuel_matches:
        return None

    # Treat the fuel row as an unordered relationship. OCR commonly emits
    # ``fuel + unit price + volume`` even though other receipts print volume
    # before price. A labelled price is preferred; an unlabelled price is only
    # accepted when the final arithmetic relation proves it.
    resolved = None
    for fuel_match in fuel_matches:
        line_start = text.rfind("\n", 0, fuel_match.start()) + 1
        line_end = text.find("\n", fuel_match.end())
        if line_end < 0:
            line_end = len(text)
        nearby = text[line_start:min(len(text), line_end + 120)]
        quantity_match = re.search(r"(\d{1,4}(?:\.\d{1,3})?)\s*[lℓ](?![A-Za-z])", nearby, re.IGNORECASE)
        if not quantity_match:
            continue
        unit_price_match = re.search(
            r"(?:단\s*가|리터\s*당)\s*[:：]?\s*"
            r"(?<!\d)(\d{1,3}(?:[,.]\d{3})|\d{3,5})\s*원?",
            nearby,
            re.IGNORECASE,
        )
        if not unit_price_match:
            numeric_prices = list(re.finditer(
                r"(?<![\d.])(\d{1,3}(?:[,.]\d{3})|\d{3,5})\s*(?:원|/?\s*[lℓ])(?![A-Za-z])",
                nearby,
                re.IGNORECASE,
            ))
            unit_price_match = next(
                (match for match in numeric_prices if not (
                    match.start() <= quantity_match.start() < match.end()
                    or quantity_match.start() <= match.start() < quantity_match.end()
                )),
                None,
            )
        if unit_price_match:
            resolved = (fuel_match, nearby, quantity_match, unit_price_match)
            break
    if not resolved:
        return None

    fuel_match, nearby, quantity_match, unit_price_match = resolved
    quantity = _quantity_number(quantity_match.group(1))
    unit_price = _receipt_number(unit_price_match.group(1))
    amount = _clean_number(hints.get("total_amount")) or _clean_number(hints.get("stated_total_amount"))
    if not (0 < quantity <= 999 and unit_price >= 100 and amount >= 100):
        return None
    tolerance = max(10.0, amount * 0.002)
    arithmetic_difference = abs(quantity * unit_price - amount)
    if arithmetic_difference > tolerance:
        return None

    name = " ".join(fuel_match.group(0).split())
    return {
        "page": page_number,
        "source": "fuel_sale_block",
        "candidate_type": "fuel_sale_item",
        "raw_cells": [name, quantity_match.group(0), unit_price_match.group(0), str(int(amount))],
        "name_candidate": name,
        "quantity_candidate": quantity,
        "unit": "L",
        "unit_price_candidate": unit_price,
        "amount_candidate": amount,
        "column_resolution": "fuel_arithmetic",
        "arithmetic_tolerance": tolerance,
        "arithmetic_difference": arithmetic_difference,
    }


def _inline_arithmetic_item_candidates(
    pages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Recover collapsed item rows by proving unit-price x quantity = amount.

    This is deliberately domain-neutral.  It runs alongside the table parser,
    but accepts only rows with an explicit multiplication marker and a numeric
    relation that holds within receipt-rounding tolerance.
    """
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"(?P<prefix>[^\n]{1,160}?)"
        r"(?P<unit_price>\d{1,3}(?:[,.]\d{3})+|\d{3,7})\s*(?:원)?\s*"
        r"(?P<operator>[×xX*])\s*"
        r"(?P<quantity>\d{1,4}(?:\.\d{1,3})?)\s*"
        r"(?P<unit>kg|㎏|g|mg|l|ℓ|ml|㎖|m|cm)?\s+"
        r"(?P<amount>\d{1,3}(?:[,.]\d{3})+|\d{3,9})(?!\d)",
        re.IGNORECASE,
    )
    header_prefix = re.compile(
        r"^.*(?:상품|품목)\s*명(?:\s*\([^)]*\))?.*?(?:금액|합계)\s*",
        re.IGNORECASE,
    )
    metadata_prefix = re.compile(
        r"^.*(?:영수증(?:\([^)]*\))?|매출전표|P[O0]S\s*N[o0]?\.?\s*\d+)\s*",
        re.IGNORECASE,
    )

    for page in pages or []:
        page_number = page.get("page")
        sources: list[str] = [
            line.strip() for line in str(page.get("text") or "").splitlines() if line.strip()
        ]
        for table in page.get("tables") or []:
            sources.extend(
                " ".join(str(cell or "").strip() for cell in row if str(cell or "").strip())
                for row in table.get("rows") or []
            )
        for source_text in sources:
            for match in pattern.finditer(source_text):
                quantity = _quantity_number(match.group("quantity"))
                unit_price = _receipt_number(match.group("unit_price"))
                amount = _receipt_number(match.group("amount"))
                tolerance = max(1.0, amount * .001)
                difference = abs(quantity * unit_price - amount)
                if not (0 < quantity <= 9999 and unit_price >= 1 and amount >= 100):
                    continue
                if difference > tolerance:
                    continue

                name = header_prefix.sub("", match.group("prefix")).strip(" |:-")
                name = metadata_prefix.sub("", name).strip(" |:-")
                # Terminal parenthesized station/SKU numbers are identifiers,
                # not part of the canonical product name.
                name = re.sub(r"\(\s*\d{1,6}\s*\)\s*$", "", name).strip()
                if not re.search(r"[A-Za-z가-힣]", name):
                    continue
                compact_name = _compact_evidence_text(name)
                key = f"{compact_name}:{quantity}:{unit_price}:{amount}"
                if not compact_name or key in seen:
                    continue

                raw_unit = (match.group("unit") or "").strip()
                decimal_quantity = "." in match.group("quantity")
                measured = bool(raw_unit or decimal_quantity)
                normalized_unit = raw_unit.upper() if raw_unit else None
                if normalized_unit in {"ℓ"}:
                    normalized_unit = "L"
                candidate: dict[str, Any] = {
                    "page": page_number,
                    "source": "inline_arithmetic_fallback",
                    "candidate_type": "measured_quantity_item" if measured else "inline_arithmetic_item",
                    "item_type": "MEASURED_QUANTITY" if measured else "COUNT_BASED",
                    "raw_cells": [
                        name,
                        match.group("unit_price"),
                        match.group("quantity") + raw_unit,
                        match.group("amount"),
                    ],
                    "name_candidate": name,
                    "quantity_candidate": quantity,
                    "unit_price_candidate": unit_price,
                    "amount_candidate": amount,
                    "column_resolution": "inline_arithmetic_fallback",
                    "quantity_resolution": "explicit_multiplication_operand",
                    "arithmetic_tolerance": tolerance,
                    "arithmetic_difference": difference,
                    "explicit_arithmetic_operator": match.group("operator"),
                }
                if normalized_unit:
                    candidate["unit"] = normalized_unit
                result.append(candidate)
                seen.add(key)
    return result


def _coordinate_item_rows(
    pages: list[dict[str, Any]] | None,
) -> list[tuple[Any, list[str]]]:
    """Rebuild OCR item rows from boxes when table extraction is absent/incomplete."""
    rebuilt: list[tuple[Any, list[str]]] = []
    header_pattern = re.compile(r"상품\s*명|품\s*명|품목\s*명|메뉴", re.IGNORECASE)
    boundary_pattern = re.compile(
        r"소\s*계|합\s*계|결제\s*금액|받을\s*금액|공급\s*가액|과세\s*(?:물품)?\s*가액|부\s*가\s*세",
        re.IGNORECASE,
    )
    for page in pages or []:
        boxes = [
            item for item in page.get("items") or []
            if item.get("text") and isinstance(item.get("bbox"), list) and len(item["bbox"]) == 2
        ]
        if not boxes:
            continue
        regions = [region.get("bbox") for region in page.get("regions") or [] if region.get("type") == "items" and region.get("bbox")]
        if not regions:
            headers = [item for item in boxes if header_pattern.search(str(item.get("text") or ""))]
            if not headers:
                continue
            header_bottom = max(item["bbox"][1][1] for item in headers)
            later_boundaries = [
                item["bbox"][0][1] for item in boxes
                if item["bbox"][0][1] > header_bottom
                and boundary_pattern.search(str(item.get("text") or ""))
            ]
            page_x1 = min(item["bbox"][0][0] for item in boxes)
            page_x2 = max(item["bbox"][1][0] for item in boxes)
            page_y2 = max(item["bbox"][1][1] for item in boxes)
            regions = [[[page_x1, header_bottom], [page_x2, min(later_boundaries) if later_boundaries else page_y2]]]

        for region in regions:
            (rx1, ry1), (rx2, ry2) = region
            selected = []
            for item in boxes:
                (x1, y1), (x2, y2) = item["bbox"]
                center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
                if rx1 <= center_x <= rx2 and ry1 <= center_y <= ry2:
                    selected.append(item)
            if not selected:
                continue
            selected.sort(key=lambda item: (
                (item["bbox"][0][1] + item["bbox"][1][1]) / 2,
                item["bbox"][0][0],
            ))
            heights = [max(item["bbox"][1][1] - item["bbox"][0][1], 1) for item in selected]
            tolerance = (sorted(heights)[len(heights) // 2] if heights else 10) * .45
            lines: list[list[dict[str, Any]]] = []
            for item in selected:
                center_y = (item["bbox"][0][1] + item["bbox"][1][1]) / 2
                line = next((line for line in reversed(lines[-3:]) if abs(
                    center_y - sum((entry["bbox"][0][1] + entry["bbox"][1][1]) / 2 for entry in line) / len(line)
                ) <= tolerance), None)
                if line is None:
                    lines.append([item])
                else:
                    line.append(item)
            for line in lines:
                line.sort(key=lambda item: item["bbox"][0][0])
                texts = [str(item.get("text") or "").strip() for item in line if str(item.get("text") or "").strip()]
                if texts and not header_pattern.search(" ".join(texts)):
                    rebuilt.append((page.get("page"), texts))
    return rebuilt


def _unitemized_service_candidate(
    text: str, hints: dict[str, Any], candidates: list[dict[str, Any]], page_number: Any = None,
) -> dict[str, Any] | None:
    """Resolve an unitemized payment only for a narrowly identified service."""
    if re.search(r"승인\s*취소|거래\s*취소|취소\s*금액|취소\s*전표", text, re.IGNORECASE):
        return None
    if re.search(r"(?:상품|품목)\s*명.*수량.*금액", text, re.IGNORECASE | re.DOTALL):
        return None
    if any(
        candidate.get("candidate_type") == "fuel_sale_item"
        or (
            candidate.get("source") in {"table", "item_region", "discounted_item_block"}
            and candidate.get("name_candidate")
            and _clean_number(candidate.get("amount_candidate")) >= 100
        )
        for candidate in candidates
    ):
        return None

    service_rules = (
        (r"개인\s*택시|법인\s*택시|택시", "taxi_transport_service", "택시 이용"),
        (r"컨트리\s*클럽|골프\s*클럽|골프장", "golf_course_service", "컨트리클럽 이용"),
        (
            r"(?:헤어\s*(?:샵|살롱|스튜디오)?|미용\s*(?:실|원)|뷰티\s*(?:샵|살롱)|"
            r"네일\s*(?:샵|살롱)|바버\s*(?:샵)?)",
            "beauty_service",
            "미용 서비스",
        ),
    )
    resolved = next(
        ((service_type, name) for pattern, service_type, name in service_rules if re.search(pattern, text, re.IGNORECASE)),
        None,
    )
    amount = _clean_number(hints.get("total_amount")) or _clean_number(hints.get("stated_total_amount"))
    if not resolved or amount < 100:
        return None
    service_type, name = resolved
    return {
        "page": page_number,
        "source": "semantic_service_inference",
        "structure_type": "unitemized_charge",
        "candidate_type": "single_service_charge",
        "item_type": "SERVICE",
        "service_type": service_type,
        "raw_cells": [name, str(int(amount))],
        "name_candidate": name,
        "quantity_candidate": 1,
        "unit": "회",
        "unit_price_candidate": amount,
        "amount_candidate": amount,
        "column_resolution": "unitemized_single_service",
        "name_resolution": "merchant_service_inference",
        "quantity_resolution": "single_service_default",
        "inferred": True,
    }


def _receipt_item_candidates(pages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Create compact item candidates without assuming physical column order."""
    summary_labels = re.compile(
        r"(?:합계|소계|결제|승인|공급가액|부가세|할인|쿠폰|적립|캐시백|거스름|카드번호|사업자번호|판매번호|거래번호|주문번호|영수증번호|총품목|총수량|총\s*매수|취소\s*매수)",
        re.IGNORECASE,
    )
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    non_item_row = re.compile(
        r"^(?:X|현대\s*HDS|CAT\s*ID|승인\s*번호|수\s*량|총\s*수량|총\s*매수|취소\s*매수|계|합계|총합계)$",
        re.IGNORECASE,
    )

    def append_structured_candidate(candidate: dict[str, Any]) -> None:
        raw = " | ".join(str(value) for value in candidate.get("raw_cells", []) if value)
        dedupe_key = re.sub(r"[^0-9A-Za-z가-힣]", "", raw).lower()
        if not raw or dedupe_key in seen:
            return
        candidates.append(candidate)
        seen.add(dedupe_key)

    def discounted_item_pair(
        row: list[Any], next_row: list[Any], page_number: Any,
        columns: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Merge a retail price row with its following SKU/discount row."""
        first = " ".join(str(cell or "").strip() for cell in row if str(cell or "").strip())
        second = " ".join(str(cell or "").strip() for cell in next_row if str(cell or "").strip())
        if not first or not re.search(r"할인", second):
            return None
        first_amounts = [_receipt_number(value) for value in re.findall(r"\d{1,3}(?:[.,]\d{3})+|\d{3,8}", first)]
        signed_second = [
            int(re.sub(r"[,.]", "", value))
            for value in re.findall(r"-?\d{1,3}(?:[.,]\d{3})+|-?\d{3,8}", second)
        ]
        negative = [abs(value) for value in signed_second if value < 0]
        positive_money = [value for value in signed_second if value >= 100]
        if not first_amounts or not negative or not positive_money:
            return None
        unit_price = first_amounts[-1]
        final_amount = positive_money[-1]
        if unit_price - negative[-1] != final_amount:
            return None

        quantity = None
        quantity_resolution = None
        if columns and len(columns) == len(row) and "quantity" in columns:
            quantity_cell = str(row[columns.index("quantity")] or "").strip()
            if re.fullmatch(r"\d{1,3}(?:\.\d{1,3})?", quantity_cell):
                explicit_quantity = _quantity_number(quantity_cell)
                if 0 < explicit_quantity <= 999:
                    quantity = explicit_quantity
                    quantity_resolution = "explicit_table_column"

        # When headers are unavailable, accept a quantity only if one unique
        # small-number cell completes both the gross-price and discount math.
        if quantity is None and len(first_amounts) >= 2:
            small_number_cells = [
                _quantity_number(str(cell or "").strip())
                for cell in row[1:]
                if re.fullmatch(r"\d{1,3}(?:\.\d{1,3})?", str(cell or "").strip())
            ]
            gross_amount = first_amounts[-1]
            arithmetic_quantities = {
                candidate_quantity
                for candidate_quantity in small_number_cells
                if 0 < candidate_quantity <= 999
                and any(abs(price * candidate_quantity - gross_amount) < .01 for price in first_amounts[:-1])
                and gross_amount - negative[-1] == final_amount
            }
            if len(arithmetic_quantities) == 1:
                quantity = arithmetic_quantities.pop()
                quantity_resolution = "discount_block_arithmetic"

        if quantity is None:
            quantity = 1
            quantity_resolution = "single_item_default"

        raw_name = str(row[0] or "").strip()
        # Metadata can be joined to the first product by borderless-table line
        # grouping. The actual product is the final textual fragment.
        raw_name = re.sub(
            r"^.*(?:시간\s*:\s*(?:오전|오후)?\s*\d{1,2}:\d{2}|P[O0]S\s*번호\s*:\s*\S+)\s*",
            "",
            raw_name,
            flags=re.IGNORECASE,
        ).strip()
        if len(raw_name) > 50:
            fragments = re.findall(r"[A-Za-z가-힣][A-Za-z가-힣 ]{1,30}", raw_name)
            raw_name = fragments[-1].strip() if fragments else raw_name
        option_source = str(next_row[0] or "").strip()
        sku_match = re.match(r"(\d{6,})(.*)", option_source)
        product_code = sku_match.group(1) if sku_match else None
        option = (sku_match.group(2) if sku_match else option_source)
        option = re.split(r"\s*할인", option, maxsplit=1)[0].strip()
        name = " ".join(value for value in (raw_name, option) if value)
        if not re.search(r"[A-Za-z가-힣]", name):
            return None
        candidate = {
            "page": page_number,
            "source": "discounted_item_block",
            "raw_cells": [first, second],
            "name_candidate": name,
            "quantity_candidate": quantity,
            "quantity_resolution": quantity_resolution,
            "unit_price_candidate": unit_price,
            "list_price_candidate": unit_price,
            "amount_candidate": final_amount,
            "paid_price_candidate": final_amount,
            "discount_amount_candidate": negative[-1],
            "column_resolution": "discount_arithmetic",
        }
        if product_code:
            candidate["product_code"] = product_code
        return candidate

    def single_amount_item(row: list[Any], page_number: Any) -> dict[str, Any] | None:
        cells = [str(cell or "").strip() for cell in row]
        name = cells[0] if cells else ""
        raw = " | ".join(cell for cell in cells if cell)
        if (
            not name or summary_labels.search(raw)
            or non_item_row.fullmatch(re.sub(r"\s+", "", name))
            or _looks_like_non_item_evidence(name, raw)
        ):
            return None
        if re.search(r"(?:직원|매장|영수증|날짜|시간|POS|CATID|승인|현대HDS|^X$)", name, re.IGNORECASE):
            return None
        numbers = re.findall(r"(?<!\d)\d{1,3}(?:[.,]\d{3})+|(?<!\d)\d{1,8}(?!\d)", " | ".join(cells[1:]))
        if len(numbers) != 1 or not re.search(r"[A-Za-z가-힣]", name):
            return None
        amount = _receipt_number(numbers[0])
        if amount <= 0:
            return None
        return {
            "page": page_number,
            "source": "single_amount_item_row",
            "raw_cells": [cell for cell in cells if cell],
            "name_candidate": name,
            "quantity_candidate": 1,
            "unit_price_candidate": amount,
            "amount_candidate": amount,
            "column_resolution": "single_amount_default_quantity",
        }

    def add_candidate(cells: list[str], page_number: Any, source: str, columns: list[str] | None = None) -> None:
        # Keep empty cells until column roles are resolved. Removing them shifts
        # every value after a missed cell into the wrong semantic column.
        aligned_cells = [str(cell or "").strip() for cell in cells]
        raw_first_cell = aligned_cells[0] if aligned_cells else ""
        cleanup_reasons: list[str] = []
        if aligned_cells:
            aligned_cells[0] = re.sub(
                r"^(?:(?:판매번호|포스번호|거래번호|주문번호|영수증번호)\s*[:#]?\s*[0-9A-Za-z-]+\s*)+",
                "",
                aligned_cells[0],
                flags=re.IGNORECASE,
            ).strip()
            # Receipt line numbers and U-prefixed inventory identifiers are
            # metadata, not product names. Preserve the code separately.
            aligned_cells[0] = re.sub(r"^\s*\d{1,3}\s+(?=\D)", "", aligned_cells[0]).strip()
            aligned_cells[0], cleanup_reasons = _clean_item_name_evidence(aligned_cells[0])
        product_code = None
        if aligned_cells:
            code_match = re.search(r"(?<![0-9A-Za-z])(U\d{6,})(?!\d)", aligned_cells[0], re.IGNORECASE)
            if code_match:
                product_code = code_match.group(1).upper()
                aligned_cells[0] = " ".join(
                    value for value in (aligned_cells[0][:code_match.start()].strip(), aligned_cells[0][code_match.end():].strip())
                    if value
                )
        display_cells = [cell for cell in aligned_cells if cell]
        raw = " | ".join(display_cells)
        dedupe_key = re.sub(r"[^0-9A-Za-z가-힣]", "", raw).lower()
        if (
            not raw or dedupe_key in seen or summary_labels.search(raw)
            or _looks_like_non_item_evidence(aligned_cells[0] if aligned_cells else "", raw)
            or non_item_row.fullmatch(re.sub(r"\s+", "", aligned_cells[0] if aligned_cells else ""))
        ):
            return
        if not re.search(r"[A-Za-z가-힣]", raw):
            return
        numeric_raw = " | ".join(aligned_cells[1:]) if len(aligned_cells) > 1 else raw
        numbers = re.findall(r"(?<!\d)\d{1,3}(?:[.,]\d{3})+|(?<!\d)\d{1,8}(?!\d)", numeric_raw)
        if len(numbers) < 2:
            return

        first_number = re.search(r"\d", raw)
        if len(aligned_cells) > 1 and aligned_cells[0] and re.search(r"[A-Za-z가-힣]", aligned_cells[0]):
            name = aligned_cells[0]
        else:
            name = raw[:first_number.start()].strip(" |:-") if first_number else ""
        parsed = [_receipt_number(value) for value in numbers]
        parenthesized = [
            _receipt_number(value)
            for value in re.findall(r"[\(（]\s*(\d{1,3}(?:[.,]\d{3})+|\d{3,8})\s*[\)）]", numeric_raw)
        ]
        primary_parsed = list(parsed)
        for alternate in parenthesized:
            try:
                primary_parsed.remove(alternate)
            except ValueError:
                pass
        candidate: dict[str, Any] = {
            "page": page_number,
            "source": source,
            "raw_cells": display_cells,
            "name_candidate": name or None,
            "amount_candidate": primary_parsed[-1] if primary_parsed else parsed[-1],
        }
        if name:
            name_parts = _structure_item_name(name)
            candidate["name_candidate"] = name_parts["canonical_name"]
            if name_parts["aliases"]:
                candidate["alias_candidates"] = name_parts["aliases"]
            if name_parts["specifications"]:
                candidate["specification_candidates"] = name_parts["specifications"]
            if name_parts["options"]:
                candidate["option_candidates"] = name_parts["options"]
        if cleanup_reasons:
            candidate["raw_name_candidate"] = raw_first_cell
            candidate["name_cleanup"] = cleanup_reasons
        if product_code:
            candidate["product_code"] = product_code
        if parenthesized:
            candidate["alternate_price_candidates"] = parenthesized
            candidate["candidate_type"] = "incomplete_item"
            candidate["uncertainty"] = ["parenthesized_price_role"]
        resolved_by_header = False
        if columns and len(columns) == len(aligned_cells):
            values_by_role = {role: aligned_cells[index] for index, role in enumerate(columns) if aligned_cells[index]}
            for role, target in (("quantity", "quantity_candidate"), ("unit_price", "unit_price_candidate"), ("amount", "amount_candidate")):
                value = values_by_role.get(role)
                if value and re.search(r"\d", value):
                    value_numbers = re.findall(r"\d{1,3}(?:[.,]\d{3})+|\d{1,8}", value)
                    if value_numbers:
                        amount_index = 0 if parenthesized else -1
                        numeric_value = (
                            _quantity_number(value_numbers[0])
                            if role == "quantity"
                            else _receipt_number(value_numbers[amount_index] if role == "amount" else value_numbers[0])
                        )
                        # A money-sized value in the quantity column is evidence
                        # of a missed/shifted cell, not a quantity of thousands.
                        if role != "quantity" or 0 < numeric_value <= 999:
                            candidate[target] = numeric_value
            resolved_by_header = all(candidate.get(field) is not None for field in (
                "quantity_candidate", "unit_price_candidate", "amount_candidate",
            ))
        if not resolved_by_header and len(numbers) >= 3:
            # In headerless fuel rows, a comma-formatted price beside a
            # period-formatted volume disambiguates ``1,429 × 20.994``. Keep
            # the period as a decimal only for the quantity operand.
            amount = parsed[-1]
            decimal_quantity_indexes = [
                index for index, token in enumerate(numbers[:-1])
                if "." in token and "," not in token and 0 < _quantity_number(token) <= 999
            ]
            comma_money_indexes = [
                index for index, token in enumerate(numbers[:-1])
                if "," in token and _receipt_number(token) >= 100
            ]
            compatible = []
            for quantity_index in decimal_quantity_indexes:
                for price_index in comma_money_indexes:
                    if quantity_index == price_index:
                        continue
                    quantity = _quantity_number(numbers[quantity_index])
                    price = _receipt_number(numbers[price_index])
                    tolerance = max(1.0, amount * 0.001)
                    if abs(quantity * price - amount) <= tolerance:
                        compatible.append((quantity, price))
            if len(compatible) == 1:
                quantity, price = compatible[0]
                candidate.update(
                    quantity_candidate=quantity,
                    unit_price_candidate=price,
                    column_resolution="decimal_quantity_arithmetic",
                )
                resolved_by_header = True
        if len(primary_parsed) >= 3 and not resolved_by_header:
            first, second, amount = primary_parsed[-3], primary_parsed[-2], primary_parsed[-1]
            # Compare both possible quantity/unit-price assignments. Arithmetic
            # plus a receipt-sized quantity is stronger than physical order.
            options = [(first, second), (second, first)]
            valid = [(quantity, price) for quantity, price in options if 0 < quantity <= 999 and price >= 1 and quantity * price == amount]
            if valid:
                quantity, price = min(valid, key=lambda pair: pair[0])
                candidate.update(quantity_candidate=quantity, unit_price_candidate=price, column_resolution="arithmetic")
            else:
                plausible = [(quantity, price) for quantity, price in options if 0 < quantity <= 100 and price >= 100]
                if plausible:
                    quantity, price = min(plausible, key=lambda pair: pair[0])
                    candidate.update(quantity_candidate=quantity, unit_price_candidate=price, column_resolution="plausibility")
                else:
                    candidate["unresolved_numeric_cells"] = primary_parsed[-3:]
        elif len(primary_parsed) >= 2 and primary_parsed[0] <= 100:
            quantity, price = primary_parsed[0], primary_parsed[1]
            candidate.update(
                quantity_candidate=quantity,
                unit_price_candidate=price,
                amount_candidate=quantity * price,
                column_resolution="item_block",
            )
        elif primary_parsed and primary_parsed[0] <= 100:
            candidate["quantity_candidate"] = primary_parsed[0]
        if resolved_by_header:
            candidate["column_resolution"] = "header"
        if (
            not candidate.get("quantity_candidate")
            and candidate.get("unit_price_candidate")
            and candidate.get("amount_candidate")
            and candidate["unit_price_candidate"] == candidate["amount_candidate"]
        ):
            candidate["quantity_candidate"] = 1
            candidate["quantity_resolution"] = "unit_price_equals_amount"
        candidates.append(candidate)
        seen.add(dedupe_key)

    for page in pages or []:
        page_number = page.get("page")
        tables = page.get("tables") or []
        for table in tables:
            pending_title: str | None = None
            table_rows = table.get("rows") or []
            skip_rows: set[int] = set()
            for row_index, row in enumerate(table_rows):
                if row_index in skip_rows:
                    continue
                if row_index + 1 < len(table_rows):
                    paired = discounted_item_pair(
                        row,
                        table_rows[row_index + 1],
                        page_number,
                        table.get("columns"),
                    )
                    if paired:
                        append_structured_candidate(paired)
                        skip_rows.add(row_index + 1)
                        continue
                single = single_amount_item(row, page_number)
                if single:
                    append_structured_candidate(single)
                    continue
                aligned_row = [str(cell or "").strip() for cell in row]
                first_cell = aligned_row[0] if aligned_row else ""
                compact_first = re.sub(r"\s+", "", first_cell)
                if re.search(r"^(?:수량|총수량|총매수|취소매수|계|합계|총합계|면세상품|과세상품|부가세|결제금액)", compact_first):
                    break
                other_cells = [cell for cell in aligned_row[1:] if cell]
                title_text = re.sub(r"^\s*\d{1,3}\s+(?=\D)", "", first_cell).strip()
                is_code_row = re.fullmatch(r"U\d{6,}", first_cell, re.IGNORECASE) is not None
                is_title_only = bool(re.search(r"[A-Za-z가-힣]", title_text)) and not other_cells and not is_code_row
                if is_title_only:
                    pending_title = title_text
                    continue
                if pending_title and is_code_row:
                    aligned_row[0] = f"{pending_title} {first_cell}"
                    pending_title = None
                elif pending_title:
                    # Do not silently discard an unresolved title when the
                    # next row is not the expected code/price continuation.
                    add_candidate([pending_title], page_number, "unresolved_title", None)
                    pending_title = None
                add_candidate(aligned_row, page_number, "table", table.get("columns"))
            if pending_title:
                add_candidate([pending_title], page_number, "unresolved_title", None)
        # A valid table is authoritative. Rescanning page.text would re-add
        # payment, approval, and receipt-number lines as fake products.
        if not tables:
            item_regions = [region for region in page.get("regions") or [] if region.get("type") == "items"]
            page_items = page.get("items") or []
            if item_regions and page_items:
                for region in item_regions:
                    (rx1, ry1), (rx2, ry2) = region.get("bbox") or [[0, 0], [0, 0]]
                    selected = []
                    for item in page_items:
                        (x1, y1), (x2, y2) = item.get("bbox") or [[0, 0], [0, 0]]
                        center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
                        if rx1 <= center_x <= rx2 and ry1 <= center_y <= ry2:
                            selected.append(item)
                    selected.sort(key=lambda item: ((item["bbox"][0][1] + item["bbox"][1][1]) / 2, item["bbox"][0][0]))
                    heights = [max(item["bbox"][1][1] - item["bbox"][0][1], 1) for item in selected]
                    # A conservative threshold prevents adjacent compact
                    # receipt rows from being merged into one product line.
                    tolerance = (sorted(heights)[len(heights) // 2] if heights else 10) * .4
                    lines: list[list[dict[str, Any]]] = []
                    for item in selected:
                        center_y = (item["bbox"][0][1] + item["bbox"][1][1]) / 2
                        line = next((line for line in reversed(lines[-3:]) if abs(center_y - sum((entry["bbox"][0][1] + entry["bbox"][1][1]) / 2 for entry in line) / len(line)) <= tolerance), None)
                        if line is None:
                            lines.append([item])
                        else:
                            line.append(item)
                    for line in lines:
                        line.sort(key=lambda item: item["bbox"][0][0])
                        add_candidate([item.get("text", "") for item in line], page_number, "item_region")
            elif not item_regions:
                for line in str(page.get("text") or "").splitlines():
                    add_candidate([line], page_number, "ocr_line_unscoped")
    combined_text = "\n".join(str(page.get("text") or "") for page in pages or [])
    summary = _receipt_hints(combined_text, "receipt")
    stated_count_for_recovery = _clean_number(summary.get("stated_item_count"))
    if not candidates or (
        stated_count_for_recovery and len(candidates) != int(stated_count_for_recovery)
    ):
        for page_number, row in _coordinate_item_rows(pages):
            add_candidate(row, page_number, "coordinate_row_fallback")
    for inline_candidate in _inline_arithmetic_item_candidates(pages):
        inline_name = _compact_evidence_text(inline_candidate.get("name_candidate"))
        # The unscoped scanner can partially parse the same collapsed line
        # (for example treating the unit price as the amount).  Replace only
        # that overlapping, incomplete observation; leave unrelated items.
        candidates = [
            candidate for candidate in candidates
            if not (
                candidate.get("source") in {"ocr_line_unscoped", "item_region", "table"}
                and inline_name
                and inline_name in _compact_evidence_text(
                    " ".join(str(cell or "") for cell in candidate.get("raw_cells") or [])
                )
                and re.search(
                    r"[×xX*]",
                    " ".join(str(cell or "") for cell in candidate.get("raw_cells") or []),
                )
                and (
                    not candidate.get("quantity_candidate")
                    or not candidate.get("unit_price_candidate")
                    or abs(
                        _clean_number(candidate.get("amount_candidate"))
                        - _clean_number(inline_candidate.get("amount_candidate"))
                    ) >= .01
                )
            )
        ]
        duplicate = any(
            _compact_evidence_text(candidate.get("name_candidate")) == inline_name
            and abs(
                _clean_number(candidate.get("amount_candidate"))
                - _clean_number(inline_candidate.get("amount_candidate"))
            ) < .01
            and _clean_number(candidate.get("quantity_candidate")) > 0
            and _clean_number(candidate.get("unit_price_candidate")) > 0
            for candidate in candidates
        )
        if not duplicate:
            candidates.append(inline_candidate)
    fuel_candidate = _fuel_sale_item_candidate(
        combined_text,
        summary,
        (pages or [{}])[0].get("page"),
    )
    if fuel_candidate:
        fuel_name = _compact_evidence_text(fuel_candidate["name_candidate"])
        complete_existing = any(
            _compact_evidence_text(candidate.get("name_candidate")) == fuel_name
            and _clean_number(candidate.get("quantity_candidate")) > 0
            and _clean_number(candidate.get("unit_price_candidate")) > 0
            and abs(
                _clean_number(candidate.get("amount_candidate"))
                - _clean_number(fuel_candidate.get("amount_candidate"))
            ) < .01
            for candidate in candidates
        )
        if not complete_existing:
            # Replace incomplete fragments of the same fuel line with the
            # domain candidate so they do not become duplicate products.
            candidates = [
                candidate for candidate in candidates
                if _compact_evidence_text(candidate.get("name_candidate")) != fuel_name
            ]
            candidates.append(fuel_candidate)
    service_candidate = _unitemized_service_candidate(
        combined_text,
        summary,
        candidates,
        (pages or [{}])[0].get("page"),
    )
    if service_candidate:
        service_name = _compact_evidence_text(service_candidate["name_candidate"])
        candidates = [
            candidate for candidate in candidates
            if not (
                candidate.get("source") in {"ocr_line_unscoped", "unresolved_title"}
                and _compact_evidence_text(candidate.get("name_candidate")) == service_name
            )
        ]
        candidates.append(service_candidate)
    candidates = candidates[:40]
    stated_count = summary.get("stated_item_count")
    stated_quantity = summary.get("stated_total_quantity")
    if stated_count and stated_quantity and len(candidates) == int(stated_count):
        known = [candidate.get("quantity_candidate") for candidate in candidates]
        missing = [index for index, value in enumerate(known) if not value or not 0 < float(value) <= 999]
        if len(missing) == 1:
            remainder = int(stated_quantity) - sum(int(value) for value in known if value and 0 < float(value) <= 999)
            if 0 < remainder <= 999:
                candidate = candidates[missing[0]]
                candidate["quantity_candidate"] = remainder
                candidate["quantity_resolution"] = "receipt_total_remainder"
                candidate.setdefault("uncertainty", []).append("quantity_recovered_from_total")
                amount = candidate.get("amount_candidate")
                if not candidate.get("unit_price_candidate") and amount and int(amount) % remainder == 0:
                    candidate["unit_price_candidate"] = int(amount) // remainder
                    candidate["unit_price_resolution"] = "amount_divided_by_quantity"
    return candidates

__all__ = [name for name in globals() if not name.startswith("__")]
