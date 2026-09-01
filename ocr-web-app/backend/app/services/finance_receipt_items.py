from __future__ import annotations

from app.services.finance_receipt_candidates import *

def _reconcile_items_with_candidates(
    items: list[dict[str, Any]], candidates: list[dict[str, Any]], stated_count: int | None,
) -> list[dict[str, Any]]:
    """Ground item names in a uniquely matching OCR amount/quantity row."""
    resolved = [dict(item) for item in items if isinstance(item, dict)]
    used_candidates: set[int] = set()

    for item in resolved:
        item_amount = _clean_number(item.get("total_amount"))
        item_unit_price = _clean_number(item.get("unit_price"))
        item_quantity = _clean_number(item.get("quantity"))
        matches: list[tuple[int, int]] = []
        for index, candidate in enumerate(candidates):
            if index in used_candidates or not candidate.get("name_candidate"):
                continue
            score = 0
            candidate_amount = _clean_number(candidate.get("amount_candidate"))
            candidate_unit_price = _clean_number(candidate.get("unit_price_candidate"))
            candidate_quantity = _clean_number(candidate.get("quantity_candidate"))
            compact_item_name = _compact_evidence_text(item.get("name"))
            compact_candidate_name = _compact_evidence_text(candidate.get("name_candidate"))
            if compact_item_name and compact_item_name == compact_candidate_name:
                score += 6
            elif (
                len(compact_item_name) >= 3 and len(compact_candidate_name) >= 3
                and (compact_item_name in compact_candidate_name or compact_candidate_name in compact_item_name)
            ):
                score += 3
            if item_amount and candidate_amount and item_amount == candidate_amount:
                score += 4
            if item_unit_price and candidate_unit_price and item_unit_price == candidate_unit_price:
                score += 2
            if item_quantity and candidate_quantity and item_quantity == candidate_quantity:
                score += 1
            if score >= 4:
                matches.append((score, index))
        if not matches:
            continue
        best_score = max(score for score, _ in matches)
        best = [index for score, index in matches if score == best_score]
        if len(best) != 1:
            continue
        candidate_index = best[0]
        candidate = candidates[candidate_index]
        candidate_name = str(candidate.get("name_candidate") or "").strip()
        model_name = str(item.get("name") or "").strip()
        if candidate_name and candidate_name != model_name:
            item["raw_model_name"] = model_name or None
            item["name"] = candidate_name
            item["name_resolution"] = "unique_ocr_amount_match"
        candidate_metadata = [
            *candidate.get("alias_candidates", []),
            *candidate.get("specification_candidates", []),
            *candidate.get("option_candidates", []),
        ]
        if candidate_metadata:
            existing_specification = str(item.get("specification") or "").strip()
            item["specification"] = " · ".join(filter(None, [existing_specification, *candidate_metadata]))
        if candidate.get("alias_candidates"):
            item["aliases"] = candidate["alias_candidates"]
        if candidate.get("option_candidates"):
            item["options"] = candidate["option_candidates"]
        candidate_quantity = _clean_number(candidate.get("quantity_candidate"))
        candidate_unit_price = _clean_number(candidate.get("unit_price_candidate"))
        candidate_amount = _clean_number(candidate.get("amount_candidate"))
        arithmetic_tolerance = _clean_number(candidate.get("arithmetic_tolerance")) or .01
        structured_arithmetic = bool(
            candidate.get("source") in {"table", "item_region", "discounted_item_block", "fuel_sale_block"}
            and candidate.get("column_resolution") in {
                "header", "arithmetic", "decimal_quantity_arithmetic",
                "discount_arithmetic", "fuel_arithmetic",
            }
            and 0 < candidate_quantity <= 999
            and candidate_unit_price >= 1
            and candidate_amount >= 100
            and abs(candidate_quantity * candidate_unit_price - candidate_amount) <= arithmetic_tolerance
        )
        # H-grade numbers are deterministic OCR/parser observations. Once the
        # row is uniquely matched, the language model must not rewrite them.
        if str(candidate.get("rel") or "").upper() == "H":
            protected_fields: list[str] = []
            for item_field, candidate_field in (
                ("quantity", "quantity_candidate"),
                ("unit_price", "unit_price_candidate"),
                ("total_amount", "amount_candidate"),
            ):
                raw_candidate_value = candidate.get(candidate_field)
                candidate_value = _clean_number(raw_candidate_value)
                if raw_candidate_value is None or candidate_value <= 0:
                    continue
                previous_value = item.get(item_field)
                if previous_value is not None and _clean_number(previous_value) != candidate_value:
                    item[f"raw_model_{item_field}"] = previous_value
                item[item_field] = candidate_value
                item[f"{item_field}_resolution"] = "protected_high_confidence_ocr_candidate"
                protected_fields.append(item_field)
            if protected_fields:
                item["protected_candidate_fields"] = protected_fields
        # Fill numeric fields only after a unique amount match and an exact
        # structured-row arithmetic check. This avoids borrowing a nearby
        # small number merely because it sits under a visually similar column.
        if structured_arithmetic:
            if item.get("quantity") is None:
                item["quantity"] = candidate_quantity
                item["quantity_resolution"] = "unique_structured_arithmetic_match"
            if item.get("unit_price") is None:
                item["unit_price"] = candidate_unit_price
                item["unit_price_resolution"] = "unique_structured_arithmetic_match"
        used_candidates.add(candidate_index)

    # Recover a completely missed item pass only when the receipt-declared row
    # count exactly agrees with fully evidenced OCR candidates.
    if not resolved and stated_count and len(candidates) == int(stated_count):
        recoverable = [candidate for candidate in candidates if candidate.get("name_candidate") and candidate.get("amount_candidate")]
        if len(recoverable) == int(stated_count):
            resolved = [{
                "name": candidate["name_candidate"],
                "quantity": candidate.get("quantity_candidate"),
                "unit_price": candidate.get("unit_price_candidate"),
                "total_amount": candidate.get("amount_candidate"),
                "specification": " · ".join([
                    *candidate.get("alias_candidates", []),
                    *candidate.get("specification_candidates", []),
                    *candidate.get("option_candidates", []),
                ]) or None,
                "aliases": candidate.get("alias_candidates") or None,
                "options": candidate.get("option_candidates") or None,
                "name_resolution": "receipt_count_confirmed_ocr_recovery",
            } for candidate in recoverable]

    return resolved


def _receipt_prompt(text: str, filename: str, pages: list[dict[str, Any]] | None = None) -> str:
    hints = _receipt_hints(text, filename)
    candidates, _ = _reliable_item_candidates(_receipt_item_candidates(pages), hints)
    semantic_evidence = _semantic_prompt_payload(text, pages, candidates, item_pass=False)
    category_policies = "\n".join(
        f"- {category}: {policy}"
        for category, policy in CATEGORY_CLASSIFICATION_POLICIES.items()
    )
    return f"""OCR 영수증의 요약 정보만 JSON 객체 하나로 반환하세요. items는 추출하지 마세요.
OCR에 없는 값은 추측하지 말고 null로 작성하세요.

doc_type: EXPENSE_REPORT(일반 경비), TRAVEL_EXPENSE(출장·교통·숙박),
PURCHASE_REQUEST(물품·장비·소프트웨어), WELFARE_BENEFIT(도서·교육·의료·복리후생) 중 하나.

반환 키: image, doc_type, expense_category, category_evidence_line_ids, needs_review, merchant,
transaction_date, supply_amount, tax_amount, discount_amount, total_amount, payment_method, card_number, description.

expense_category는 아래 고정 목록 중 하나를 선택하되 실제 결제 대상과 가장 잘 부합하는 값을 우선하세요.
{json.dumps(EXPENSE_CATEGORIES, ensure_ascii=False)}

카테고리 정책:
{category_policies}

판단 규칙:
1. OCR 근거만 사용합니다. 날짜는 YYYY-MM-DD, 금액은 숫자이며 확인할 수 없는 개별 값은 null입니다.
2. 카테고리는 실제 결제 품목·서비스를 우선하고, 거래를 직접 설명하는 문구, 판매·서비스 주체 순서로 판단합니다. 이 중 하나가 정책에 합리적으로 부합하면 해당 카테고리를 선택하고, 근거가 완벽하지 않다는 이유만으로 null을 선택하지 마세요. null은 거래 대상 정보가 거의 없거나 둘 이상의 정책이 비슷하게 충돌하거나 어느 정책에도 합리적으로 부합하지 않을 때만 사용합니다.
3. expense_category를 먼저 판단한 뒤 사용한 OCR line id가 있으면 category_evidence_line_ids에 최대 3개 반환하세요. line id를 자신 있게 고르지 못해도 유효한 expense_category를 null로 바꾸지 말고 빈 배열을 허용합니다. doc_type은 보조 의견이며 카테고리와 문서 목적이 충돌하거나 업무 목적이 불명확하면 needs_review=true로 작성하세요.
4. merchant는 issuer/business_info에서 실제 판매·발행 주체를 고릅니다. 카드사·PG사·쇼핑몰·URL은 판매자가 아니면 제외하고 `(과세)/(면세)`는 제거합니다.
5. total_amount는 settlement의 최종 결제·받을·승인·청구 금액을 우선하고 tax_summary, adjustments, item_summary는 검산에만 씁니다. 품목 단가·소계는 최종금액이 아닙니다.
6. sections는 line id를 참조하며 한 행은 여러 section에 속할 수 있습니다. 코드 힌트보다 명시적 OCR 라벨을 우선합니다.
7. card_number는 명시적인 카드번호 라벨과 마스킹된 값이 함께 보일 때만 작성하고, 그렇지 않으면 null입니다.

[파일명]
{filename}

[코드 확인값]
{json.dumps(hints, ensure_ascii=False)}

[의미별 OCR 근거]
{json.dumps(semantic_evidence, ensure_ascii=False, separators=(",", ":"))}
"""


def _receipt_items_prompt(text: str, pages: list[dict[str, Any]] | None = None) -> str:
    summary = _receipt_hints(text, "receipt")
    candidates, _ = _reliable_item_candidates(_receipt_item_candidates(pages), summary)
    stated_count = summary.get("stated_item_count")
    stated_quantity = summary.get("stated_total_quantity")
    if not candidates:
        table_rows: list[list[str]] = []
        seen_rows: set[str] = set()
        for page in pages or []:
            for table in page.get("tables") or []:
                for row in table.get("rows") or []:
                    cells = [" ".join(str(cell or "").strip().split())[:100] for cell in row[:6]]
                    cells = [cell for cell in cells if cell]
                    key = _compact_evidence_text(" ".join(cells))
                    if not cells or not key or key in seen_rows:
                        continue
                    table_rows.append(cells)
                    seen_rows.add(key)
                    if len(table_rows) >= 12:
                        break
                if len(table_rows) >= 12:
                    break
            if len(table_rows) >= 12:
                break
        recovery_payload = {
            "printed_item_count": stated_count,
            "printed_total_quantity": stated_quantity,
            "ocr_excerpt": text[:1800],
            "table_rows": table_rows or None,
        }
        recovery_evidence = json.dumps(
            {key: value for key, value in recovery_payload.items() if value not in (None, "", [])},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"""신뢰 가능한 품목 candidate가 없습니다. 아래 OCR에서 명시적으로 확인되는 실제 구매 품목만 JSON으로 복구하세요.
형식: {{"items":[{{"name":...,"specification":...,"quantity":...,"unit":...,"unit_price":...,"supply_amount":...,"tax_amount":...,"total_amount":...}}]}}

규칙:
1. OCR에 직접 보이는 품목만 반환하고 추측하지 마세요.
2. 합계·소계·세금·할인·결제·승인·카드·사업자 정보는 품목이 아닙니다.
3. 품목명이나 품목 금액을 확인할 수 없으면 items=[]를 반환하세요.
4. 불명확한 개별 필드는 null로 두고 OCR 행 순서를 유지하세요.
5. 표 행과 OCR 본문이 충돌하면 표 행을 우선하되 숫자의 의미를 임의로 바꾸지 마세요.

[복구 근거]
{recovery_evidence}
"""
    evidence_payload = _semantic_prompt_payload(text, pages, candidates, item_pass=True)
    structure = _classify_item_structure(candidates, summary)
    evidence_bundles = []
    for index, candidate in enumerate(candidates):
        parser_profile = _item_parser_profile(candidate)
        raw_cells = [str(cell or "") for cell in candidate.get("raw_cells") or []]
        normalized_numbers = [
            {"cell_index": cell_index, "raw": cell, "value": _clean_number(cell)}
            for cell_index, cell in enumerate(raw_cells)
            if re.fullmatch(r"\s*[-+]?\d[\d,.]*\s*(?:원|₩|[lℓ])?\s*", cell, re.IGNORECASE)
        ]
        quantity = _clean_number(candidate.get("quantity_candidate")) or None
        unit_price = _clean_number(candidate.get("unit_price_candidate")) or None
        amount = _clean_number(candidate.get("amount_candidate")) or None
        arithmetic_relations = []
        if quantity and unit_price and amount:
            arithmetic_tolerance = _clean_number(candidate.get("arithmetic_tolerance")) or .01
            arithmetic_relations.append({
                "operands": [quantity, unit_price],
                "operator": "multiply",
                "observed_result": amount,
                "difference": abs(quantity * unit_price - amount),
                "tolerance": arithmetic_tolerance,
                "matched": abs(quantity * unit_price - amount) <= arithmetic_tolerance,
            })
        evidence_bundles.append({
            "bundle_id": f"I{index + 1:03d}",
            "parser_profile": parser_profile["profile"],
            "applicable_rules": parser_profile["rules"],
            "raw_cells": raw_cells,
            "normalized_numbers": normalized_numbers,
            "source_observation": {
                "source": candidate.get("source"),
                "page": candidate.get("page"),
                "columns": candidate.get("columns"),
                "candidate_type": candidate.get("candidate_type"),
                "structure_type": candidate.get("structure_type"),
                "service_type": candidate.get("service_type"),
                "inferred": bool(candidate.get("inferred")),
            },
            "text_observations": {
                "name_fragment": candidate.get("name_candidate"),
                "raw_name_fragment": candidate.get("raw_name_candidate"),
                "aliases": candidate.get("alias_candidates") or [],
                "specifications": candidate.get("specification_candidates") or [],
                "options": candidate.get("option_candidates") or [],
            },
            "arithmetic_relations": arithmetic_relations,
            "alternative_price_observations": candidate.get("alternate_price_candidates") or [],
            "support_signals": {
                "reliability": candidate.get("rel"),
                "reasons": candidate.get("why") or [],
                "uncertainty": candidate.get("uncertainty") or [],
            },
            "parser_hypothesis": {
                "column_resolution": candidate.get("column_resolution"),
                "quantity": quantity,
                "unit_price": unit_price,
                "total_amount": amount,
                "unit": candidate.get("unit"),
                "name_resolution": candidate.get("name_resolution"),
                "quantity_resolution": candidate.get("quantity_resolution"),
                "arithmetic_tolerance": candidate.get("arithmetic_tolerance"),
                "is_binding": False,
            },
        })
    # Candidate raw cells are already the authoritative representation of
    # their OCR rows. Do not send an identical line a second time.
    candidate_rows = {
        _compact_evidence_text(" ".join(str(cell or "") for cell in candidate.get("raw_cells") or []))
        for candidate in candidates
    }
    duplicate_line_ids = {
        line["id"] for line in evidence_payload.get("lines") or []
        if _compact_evidence_text(line.get("text")) in candidate_rows
    }
    if duplicate_line_ids:
        evidence_payload["lines"] = [
            line for line in evidence_payload.get("lines") or []
            if line.get("id") not in duplicate_line_ids
        ]
        evidence_payload["sections"] = {
            section: [line_id for line_id in line_ids if line_id not in duplicate_line_ids]
            for section, line_ids in (evidence_payload.get("sections") or {}).items()
            if any(line_id not in duplicate_line_ids for line_id in line_ids)
        }
        structured_evidence = evidence_payload.get("structured_evidence") or {}
        if isinstance(structured_evidence, dict):
            evidence_payload["structured_evidence"] = {
                **structured_evidence,
                "rows": [
                    row for row in structured_evidence.get("rows") or []
                    if row.get("row_id") not in duplicate_line_ids
                ],
            }
    evidence_payload.pop("item_candidates", None)
    evidence_payload["evidence_bundles"] = evidence_bundles
    evidence_payload["structure_hypothesis"] = {**structure, "is_binding": False}
    evidence_payload["common_rules"] = [
        "Use only OCR evidence from this payload.",
        "Do not emit totals, subtotals, tax, payment, approval/order numbers, discounts, or headers as products.",
        "Keep receipt item order and return null for fields that the applicable bundle rules cannot establish.",
        "Apply only each bundle's applicable_rules; do not borrow a column rule from another bundle.",
    ]
    evidence_payload["evidence_policy"] = {
        "raw_cells_are_authoritative": True,
        "parser_hypotheses_are_refutable": True,
        "preserve_conflicting_observations": True,
        "missing_values_remain_null": True,
    }
    # Include a recovery excerpt only when structural extraction is absent or
    # conflicts with a receipt-declared item count. Normal receipts avoid
    # repeating the same OCR text in both model calls.
    if not candidates or (stated_count and len(candidates) != int(stated_count)):
        evidence_payload["recovery_excerpt"] = text[:1600]
    def without_empty_values(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: cleaned for key, nested in value.items()
                if (cleaned := without_empty_values(nested)) not in (None, "", [], {})
            }
        if isinstance(value, list):
            return [cleaned for nested in value if (cleaned := without_empty_values(nested)) not in (None, "", [], {})]
        return value

    evidence = json.dumps(without_empty_values(evidence_payload), ensure_ascii=False, separators=(",", ":"))
    return f"""영수증의 실제 구매 품목만 JSON으로 반환하세요.
형식: {{"items":[{{"name":...,"specification":...,"quantity":...,"unit":...,"unit_price":...,"supply_amount":...,"tax_amount":...,"total_amount":...}}]}}

규칙:
1. 근거 행 하나는 원칙적으로 품목 하나입니다. 확인되는 품목을 누락하지 마세요.
2. 합계·소계·공급가액·부가세·할인·결제·승인·안내 행은 품목이 아닙니다.
3. 후보의 name/quantity/unit_price/amount는 코드 추정값입니다. raw_cells가 다르면 raw_cells를 우선합니다.
4. 확인할 수 없는 필드는 null로 두고 상품 자체가 확인되면 품목을 삭제하지 마세요.
5. 영수증에 명시된 품목 수: {stated_count if stated_count is not None else '미확인'}
6. 열 순서는 영수증마다 다릅니다. raw_cells의 순서를 수량-단가로 고정하지 말고 column_resolution과 산술 관계를 확인하세요.
7. 판매번호·거래번호·주문번호·승인번호·사업자번호는 품목명이 아닙니다.
8. candidate_type이 incomplete_item이어도 상품명과 주가격이 있으면 품목을 삭제하지 말고 불명확한 필드만 null로 두세요.
9. alternate_price_candidates는 괄호로 표시된 회원가·할인가·참고가격 후보입니다. 명확한 라벨이 없으면 주가격을 대체하지 마세요.
10. 영수증에 명시된 총수량: {stated_quantity if stated_quantity is not None else '미확인'}. quantity_resolution이 receipt_total_remainder이면 다른 품목 수량과 총수량으로 복원한 값입니다.
11. product_code는 재고·상품 식별 코드이며 품목명이 아닙니다. name_candidate를 품목명으로 사용하고 필요한 코드는 specification에만 보존하세요.
12. raw_name_candidate와 name_cleanup이 있으면 거래일시·POS·판매번호·상품 열 제목을 제거한 name_candidate를 사용하세요. 상품명에 날짜, POS 번호, `상품코드/단가/수량/금액` 헤더를 포함하지 마세요.
13. alias_candidates는 다른 언어로 반복 표기된 같은 상품명, specification_candidates는 중량·크기·묶음 규격, option_candidates는 SKU·색상 옵션입니다. 이 값들은 name에 다시 합치지 말고 specification에 보존하세요.
14. item_candidates가 행 근거와 충돌하거나 품목을 누락하면 lines와 recovery_excerpt(있는 경우)를 사용해 복원하세요. 정가 다음 행에 SKU·색상·할인액·할인 후 금액이 이어지면 같은 품목입니다.
15. sections는 line id 참조이며 items가 주요 품목 근거입니다. adjustments·settlement·tax_summary 행은 품목으로 만들지 말고 검산과 제외 근거로만 사용하세요.
16. rel은 H=높음, M=검토 필요입니다. why는 T=표, R=품목영역, F=주유블록, D=도메인 서비스 추론, A=산술일치, C=품목수일치, S=합계일치, E=기타근거입니다. H 후보를 우선하고 M은 lines로 확인하세요.
17. candidate_type이 fuel_sale_item이면 분리된 유종명·리터 수량·리터당 단가·결제금액을 하나의 주유 품목으로 유지하세요. arithmetic_tolerance 안의 반올림 차이는 허용하고 결제·세금·할인 행을 별도 품목으로 만들지 마세요.
18. candidate_type이 single_service_charge이면 명확히 식별된 서비스 사업자와 단일 결제 총액을 한 건의 서비스 품목으로 해석하세요. 같은 서비스를 일반 후보로 중복 생성하지 말고 quantity_resolution이 single_service_default인 수량 1은 추론값으로 보존하세요.
19. candidate_type이 measured_quantity_item이면 명시된 단가 × 측정량 ≈ 금액 관계를 한 품목으로 유지하세요. 소수 측정량을 개수로 반올림하지 말고, 단위가 OCR에 없으면 새로 추측하지 마세요.

[품목 근거]
{evidence}
"""


def _classify_item_structure(
    candidates: list[dict[str, Any]], hints: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Describe item evidence without spending an LLM call on classification."""
    hints = hints or {}
    if candidates and all(candidate.get("structure_type") == "unitemized_charge" for candidate in candidates):
        return {
            "column_schema": "unknown",
            "layout": "unitemized",
            "relationship": "flat",
            "confidence": "medium",
        }
    if not candidates:
        return {
            "column_schema": "unknown",
            "layout": "multi-line" if hints.get("stated_item_count") else "unknown",
            "relationship": "flat",
            "confidence": "low",
        }

    field_counts = []
    for candidate in candidates:
        raw_count = len([cell for cell in candidate.get("raw_cells") or [] if str(cell or "").strip()])
        if 2 <= raw_count <= 4:
            # Classify the observed shape, not fields inferred later by rules
            # (for example, a 2-column row may receive default quantity=1).
            count = raw_count
        else:
            count = 1  # name
            count += bool(_clean_number(candidate.get("quantity_candidate")))
            count += bool(_clean_number(candidate.get("unit_price_candidate")))
            count += bool(_clean_number(candidate.get("amount_candidate")))
        field_counts.append(count)
    dominant = max(set(field_counts), key=field_counts.count)
    column_schema = {4: "4-column", 3: "3-column", 2: "2-column"}.get(dominant, "unknown")

    multi_line_sources = {"discounted_item_block", "unresolved_title", "incomplete_item", "fuel_sale_block"}
    layout = "multi-line" if any(
        candidate.get("source") in multi_line_sources
        or candidate.get("candidate_type") == "incomplete_item"
        for candidate in candidates
    ) else "single-line"
    relationship = "parent-child" if any(
        candidate.get("option_candidates")
        or candidate.get("source") == "discounted_item_block"
        or re.search(r"(?:option|add|extra|discount)", " ".join(map(str, candidate.get("raw_cells") or [])), re.I)
        for candidate in candidates
    ) else "flat"

    complete = sum(bool(candidate.get("name_candidate") and candidate.get("amount_candidate")) for candidate in candidates)
    high = sum(candidate.get("rel") == "H" for candidate in candidates)
    confidence = "high" if complete == len(candidates) and high == len(candidates) else (
        "medium" if complete >= max(1, len(candidates) // 2) else "low"
    )
    return {
        "column_schema": column_schema,
        "layout": layout,
        "relationship": relationship,
        "confidence": confidence,
    }


def _item_parser_profile(candidate: dict[str, Any]) -> dict[str, Any]:
    """Select only the rules applicable to one evidence bundle."""
    structure = _classify_item_structure([candidate])
    column_schema = structure["column_schema"]
    layout = structure["layout"]
    relationship = structure["relationship"]

    if candidate.get("candidate_type") == "fuel_sale_item":
        return {
            "profile": "fuel_sale_item_multi-line_flat",
            "rules": [
                "Treat the fuel name, litre volume, price per litre, and paid total as one fuel item even when they occur on separate OCR lines.",
                "Use quantity_candidate as litres, unit_price_candidate as price per litre, and amount_candidate as the item total.",
                "Accept the documented arithmetic_tolerance for receipt rounding; do not require exact floating-point multiplication.",
                "Do not emit tax, approval, cashback, discount, QR, or settlement lines as additional items.",
            ],
        }

    if candidate.get("candidate_type") == "measured_quantity_item":
        return {
            "profile": "measured_quantity_inline_arithmetic_flat",
            "rules": [
                "Treat the explicitly multiplied unit price, measured quantity, and observed amount as one item.",
                "Preserve the decimal quantity and its unit when observed; do not round it to a count.",
                "Accept the documented arithmetic_tolerance for receipt rounding.",
                "Do not turn tax, discount, settlement, or membership rows into additional items.",
            ],
        }

    if candidate.get("candidate_type") == "single_service_charge":
        return {
            "profile": "single_service_charge_unitemized_flat",
            "rules": [
                "Treat the strongly identified merchant/service and single paid total as one service item.",
                "Use quantity 1 only as the documented single-service default, not as directly printed quantity evidence.",
                "Do not emit approval, card, tax, settlement, or generic merchant fragments as additional items.",
                "Prefer this specific service candidate over a duplicate generic candidate for the same service.",
            ],
        }

    column_rules = {
        "4-column": [
            "Evaluate the four observed cells as name, quantity, unit_price, and total_amount.",
            "Use the multiplication relation as supporting evidence; if it conflicts, preserve OCR values and leave the ambiguous mapping null.",
        ],
        "3-column": [
            "Evaluate the observed cells as name, quantity, and total_amount.",
            "Set unit_price only when it is explicitly observed; do not derive it merely by division.",
        ],
        "2-column": [
            "Extract only the observed name and total_amount.",
            "Do not default quantity to 1 and do not copy total_amount into unit_price; leave both null unless explicitly observed elsewhere in this bundle.",
        ],
        "unknown": [
            "Do not assume a column order; map only fields directly supported by raw_cells and nearby referenced lines.",
        ],
    }[column_schema]
    layout_rules = {
        "single-line": [
            "Treat this bundle as one item row and do not merge unrelated neighboring bundles.",
        ],
        "multi-line": [
            "Join only adjacent fragments that share item evidence; never cross a new-item, subtotal, tax, or settlement boundary.",
        ],
        "unknown": [
            "Keep fragments separate unless adjacency and shared numeric evidence clearly establish one item.",
        ],
    }[layout]
    relationship_rules = {
        "flat": [
            "Treat the row as an independent item; do not invent a parent-child relation.",
        ],
        "parent-child": [
            "Determine whether option/addition evidence belongs to the immediately preceding parent item.",
            "Store a supported child as specification; emit it separately only with evidence that it was independently sold.",
        ],
    }[relationship]
    return {
        "profile": f"{column_schema}_{layout}_{relationship}",
        "rules": [*column_rules, *layout_rules, *relationship_rules],
    }


def _validate_resolved_items(items: list[dict[str, Any]], total_amount: Any = None) -> dict[str, Any]:
    """Deterministically report inconsistencies; never trigger another LLM call."""
    issues: list[dict[str, Any]] = []
    item_sum = 0.0
    for index, item in enumerate(items):
        quantity = _clean_number(item.get("quantity"))
        unit_price = _clean_number(item.get("unit_price"))
        amount = _clean_number(item.get("total_amount"))
        arithmetic_tolerance = _clean_number(item.get("arithmetic_tolerance")) or .01
        if amount:
            item_sum += amount
        if quantity and unit_price and amount and abs(quantity * unit_price - amount) > arithmetic_tolerance:
            issues.append({"item_index": index, "code": "quantity_unit_price_mismatch"})
        if not str(item.get("name") or "").strip():
            issues.append({"item_index": index, "code": "missing_name"})
    receipt_total = _clean_number(total_amount)
    if item_sum and receipt_total and abs(item_sum - receipt_total) >= .01:
        issues.append({"code": "item_sum_receipt_total_mismatch", "item_sum": item_sum, "receipt_total": receipt_total})
    return {"valid": not issues, "issues": issues, "item_sum": item_sum or None}


def _candidate_items(candidates: list[dict[str, Any]], resolution: str) -> list[dict[str, Any]]:
    return [{
        "name": candidate.get("name_candidate"),
        "quantity": candidate.get("quantity_candidate"),
        "unit": candidate.get("unit"),
        "unit_price": candidate.get("unit_price_candidate"),
        "list_price": candidate.get("list_price_candidate"),
        "discount_amount": candidate.get("discount_amount_candidate"),
        "paid_unit_price": candidate.get("paid_price_candidate"),
        "total_amount": candidate.get("amount_candidate"),
        "candidate_type": candidate.get("candidate_type"),
        "item_type": candidate.get("item_type"),
        "structure_type": candidate.get("structure_type"),
        "service_type": candidate.get("service_type"),
        "inferred": candidate.get("inferred"),
        "name_resolution": candidate.get("name_resolution"),
        "quantity_resolution": candidate.get("quantity_resolution"),
        "arithmetic_tolerance": candidate.get("arithmetic_tolerance"),
        "product_code": candidate.get("product_code"),
        "options": candidate.get("option_candidates") or None,
        "specification": " · ".join([
            *candidate.get("alias_candidates", []),
            *candidate.get("specification_candidates", []),
            *candidate.get("option_candidates", []),
        ]) or None,
        "item_resolution": resolution,
    } for candidate in candidates]


def _receipt_items_retry_prompt(candidates: list[dict[str, Any]], stated_count: int | None) -> str:
    """Build a compact retry prompt from grounded OCR candidate facts."""
    compact_candidates = [{
        "id": f"I{index + 1:03d}",
        "name": candidate.get("name_candidate"),
        "quantity": candidate.get("quantity_candidate"),
        "unit": candidate.get("unit"),
        "unit_price": candidate.get("unit_price_candidate"),
        "list_price": candidate.get("list_price_candidate"),
        "discount_amount": candidate.get("discount_amount_candidate"),
        "paid_price": candidate.get("paid_price_candidate"),
        "amount": candidate.get("amount_candidate"),
        "product_code": candidate.get("product_code"),
        "options": candidate.get("option_candidates") or [],
        "raw": [str(value)[:120] for value in (candidate.get("raw_cells") or [])[:3]],
    } for index, candidate in enumerate(candidates)]
    payload = json.dumps(compact_candidates, ensure_ascii=False, separators=(",", ":"))
    return (
        "Return one JSON object with an items array only. Use only the supplied OCR candidates. "
        "Do not add totals, tax, payment, approval, discount-summary, or metadata rows as items. "
        "Preserve candidate order and use null for unknown values. "
        f"Printed item count: {stated_count if stated_count is not None else 'unknown'}. "
        "Item keys: name,specification,quantity,unit,unit_price,supply_amount,tax_amount,total_amount.\n"
        f"Candidates:{payload}"
    )


def _deduplicate_model_items(items: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove model repetitions while preserving repeated OCR source rows."""
    candidate_counts: dict[tuple[str, float], int] = {}
    for candidate in candidates:
        key = (_compact_evidence_text(candidate.get("name_candidate")), _clean_number(candidate.get("amount_candidate")))
        candidate_counts[key] = candidate_counts.get(key, 0) + 1
    seen: dict[tuple[str, float, float, float], int] = {}
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (
            _compact_evidence_text(item.get("name")), _clean_number(item.get("quantity")),
            _clean_number(item.get("unit_price")), _clean_number(item.get("total_amount")),
        )
        allowed = max(candidate_counts.get((key[0], key[3]), 0), 1)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] <= allowed:
            result.append(item)
    return result


def _conflicts_with_single_inferred_service(
    items: list[dict[str, Any]], candidates: list[dict[str, Any]],
) -> bool:
    """Detect a fabricated item when OCR proves only one unitemized service."""
    if len(candidates) != 1:
        return False
    candidate = candidates[0]
    if not (
        candidate.get("candidate_type") == "single_service_charge"
        and candidate.get("structure_type") == "unitemized_charge"
        and candidate.get("inferred")
    ):
        return False
    if len(items) != 1:
        return True
    item = items[0]
    item_name = _compact_evidence_text(item.get("name"))
    candidate_name = _compact_evidence_text(candidate.get("name_candidate"))
    name_matches = bool(
        item_name and candidate_name
        and (item_name == candidate_name or item_name in candidate_name or candidate_name in item_name)
    )
    amount_matches = abs(
        _clean_number(item.get("total_amount"))
        - _clean_number(candidate.get("amount_candidate"))
    ) < .01
    return not (name_matches and amount_matches)


def _recover_items_when_grounded(
    candidates: list[dict[str, Any]], hints: dict[str, Any], stated_count: int | None,
) -> tuple[list[dict[str, Any]], str | None]:
    inferred_services = [
        candidate for candidate in candidates
        if candidate.get("candidate_type") == "single_service_charge"
        and candidate.get("structure_type") == "unitemized_charge"
        and candidate.get("inferred")
    ]
    hinted_total = _clean_number(hints.get("total_amount")) or _clean_number(hints.get("stated_total_amount"))
    if (
        len(candidates) == 1
        and len(inferred_services) == 1
        and inferred_services[0].get("name_candidate")
        and hinted_total >= 100
        and abs(_clean_number(inferred_services[0].get("amount_candidate")) - hinted_total) < .01
        and (not stated_count or int(stated_count) == 1)
    ):
        reason = "single_service_domain_recovery"
        return _candidate_items(inferred_services, reason), reason

    # A collapsed OCR row with an explicit multiplication marker is already
    # fully grounded even if the summary model selected a pre-discount total.
    # Preserve it when the item-model call fails instead of returning no items.
    if len(candidates) == 1:
        candidate = candidates[0]
        quantity = _clean_number(candidate.get("quantity_candidate"))
        unit_price = _clean_number(candidate.get("unit_price_candidate"))
        amount = _clean_number(candidate.get("amount_candidate"))
        tolerance = _clean_number(candidate.get("arithmetic_tolerance")) or .01
        if (
            candidate.get("source") == "inline_arithmetic_fallback"
            and candidate.get("rel") == "H"
            and candidate.get("explicit_arithmetic_operator")
            and candidate.get("name_candidate")
            and quantity > 0 and unit_price > 0 and amount >= 100
            and abs(quantity * unit_price - amount) <= tolerance
        ):
            reason = "grounded_inline_arithmetic_recovery"
            return _candidate_items(candidates, reason), reason

    grounded = [candidate for candidate in candidates if candidate.get("rel") == "H"]
    if not grounded or len(grounded) != len(candidates) or not all(
        candidate.get("name_candidate") and _clean_number(candidate.get("amount_candidate")) >= 100
        for candidate in grounded
    ):
        return [], None
    candidate_total = sum(_clean_number(candidate.get("amount_candidate")) for candidate in grounded)
    hinted_total = _clean_number(hints.get("total_amount")) or _clean_number(hints.get("stated_total_amount"))
    count_matches = bool(stated_count and len(grounded) == int(stated_count))
    total_matches = bool(hinted_total >= 100 and abs(candidate_total - hinted_total) < .01)
    strong_table = bool(len(grounded) >= 2 and all(
        candidate.get("source") in {"table", "discounted_item_block"}
        and _clean_number(candidate.get("quantity_candidate")) > 0
        and _clean_number(candidate.get("unit_price_candidate")) > 0
        and abs(
            _clean_number(candidate.get("quantity_candidate")) * _clean_number(candidate.get("unit_price_candidate"))
            - _clean_number(candidate.get("amount_candidate"))
        ) < .01
        for candidate in grounded
    ))
    if not count_matches and not total_matches and not strong_table:
        return [], None
    reason = (
        "receipt_count_confirmed_ocr_recovery" if count_matches
        else "ocr_candidates_match_receipt_total" if total_matches
        else "validated_table_candidate_recovery"
    )
    return _candidate_items(grounded, reason), reason


def _strict_grounded_item_fast_path(
    candidates: list[dict[str, Any]], hints: dict[str, Any], stated_count: int | None,
    model_total: Any = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Skip the item LLM only when receipt structure proves every item value.

    This is deliberately stricter than the failure recovery path. A normal LLM
    call may still help clean ambiguous names or fill incomplete numeric fields,
    so the fast path requires a complete table, exact count/total/arithmetic,
    and no candidate-level uncertainty.
    """
    if not candidates or (stated_count and len(candidates) != int(stated_count)):
        return [], None
    # Without an explicit count, a single clean row cannot prove that OCR did
    # not miss another item. Multi-row arithmetic tables can be corroborated
    # by an independently extracted receipt total below.
    if not stated_count and len(candidates) < 2:
        return [], None
    if hints.get("discount_amount") or hints.get("amount_relation"):
        return [], None

    metadata_name = re.compile(
        r"영수증|거래\s*(?:일시|번호)|판매\s*(?:일시|번호)|POS\b|"
        r"20\d{2}[-./]?\d{2}[-./]?\d{2}|\b\d{6,}\b",
        re.IGNORECASE,
    )
    for candidate in candidates:
        name = str(candidate.get("name_candidate") or "").strip()
        quantity = _clean_number(candidate.get("quantity_candidate"))
        unit_price = _clean_number(candidate.get("unit_price_candidate"))
        amount = _clean_number(candidate.get("amount_candidate"))
        if (
            candidate.get("rel") != "H"
            or candidate.get("source") not in {"table", "discounted_item_block"}
            or candidate.get("uncertainty")
            or candidate.get("alternate_price_candidates")
            or candidate.get("candidate_type") == "incomplete_item"
            or not name
            or metadata_name.search(name)
            or quantity <= 0
            or unit_price <= 0
            or amount < 100
            or abs(quantity * unit_price - amount) >= .01
        ):
            return [], None

    candidate_total = sum(_clean_number(candidate.get("amount_candidate")) for candidate in candidates)
    corroborating_totals = {
        value for value in (
            _clean_number(hints.get("total_amount")),
            _clean_number(hints.get("stated_total_amount")),
            _clean_number(model_total),
        )
        if value >= 100
    }
    if not any(abs(candidate_total - total) < .01 for total in corroborating_totals):
        return [], None
    return _candidate_items(candidates, "strict_grounded_fast_path"), "strict_grounded_fast_path"

__all__ = [name for name in globals() if not name.startswith("__")]
