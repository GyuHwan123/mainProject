from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes.finance import (  # noqa: E402
    EXPENSE_CATEGORIES,
    _classify_item_structure,
    _item_parser_profile,
    _classify_receipt_with_model,
    _normalize,
    _normalize_expense_category,
    _reconcile_items_with_candidates,
    _receipt_hints,
    _receipt_item_candidates,
    _receipt_items_prompt,
    _receipt_prompt,
    _reliable_item_candidates,
    _semantic_prompt_payload,
    _semantic_receipt_evidence,
    _strict_grounded_item_fast_path,
)


SAMPLE_OCR = """주문번호 5,125.00
매장명 전화번호 02-736-1260 광화문점
서울특별시 종로구 종로1길5
판매일자 대표자 사업자NO O.A동 1층(중학동) 2025-10-05 16:50 주혜윤 105-87-46831
[메뉴] [수량] [금액]
케일청포도 주스 케일바나나주스 리코타 샐러드 1 1 1 15.800 7,800 7.800
결제금액 공급가액 부가세액 31.400 28.545 2,855
[거래 종류] 카드결제 31,400원
"""


class FinanceClassificationTests(unittest.IsolatedAsyncioTestCase):
    def test_receipt_prompt_restricts_expense_category_to_managed_list(self):
        prompt = _receipt_prompt("브러쉬드 알파카 실", "receipt.jpg")

        self.assertIn("고정 목록 중 정확히 하나", prompt)
        self.assertIn("취미/쇼핑", prompt)
        self.assertTrue(all(category in prompt for category in EXPENSE_CATEGORIES))
        self.assertIn("needs_review", prompt)

    def test_unknown_expense_category_requires_review_without_guessing(self):
        self.assertEqual(_normalize_expense_category("사무용품"), "전자제품/문구")
        self.assertIsNone(_normalize_expense_category("출장숙박"))
        self.assertEqual(_normalize_expense_category("취미/쇼핑"), "취미/쇼핑")
        self.assertIsNone(_normalize_expense_category("모델이 만든 새 분류"))
        normalized = _normalize({"doc_type": "EXPENSE_REPORT", "expense_category": "임의 카테고리"}, "receipt.jpg", "상호 영수증")
        self.assertIsNone(normalized["expense_category"])
        self.assertEqual(normalized["document_type"], "EXPENSE_REPORT")
        self.assertTrue(normalized["structured_data"]["needs_review"])

    def test_verified_category_is_not_replaced_by_validator(self):
        normalized = _normalize(
            {"doc_type": "PURCHASE_REQUEST", "expense_category": "취미/쇼핑"},
            "receipt_001.jpg",
            "바늘이야기 알파카 실 81,300원",
        )

        self.assertEqual(normalized["expense_category"], "취미/쇼핑")
        self.assertEqual(normalized["document_type"], "PURCHASE_REQUEST")

    def test_requires_explicit_alcohol_evidence_for_food_and_alcohol_category(self):
        food = _normalize(
            {"expense_category": "식비/주류", "items": [{"name": "초콜릿", "quantity": 2, "total_amount": 6900}]},
            "receipt.jpg",
            "GS25 초콜릿 2개 결제금액 6,900원",
        )
        alcohol = _normalize(
            {"expense_category": "식비/주류", "items": [{"name": "와인", "quantity": 1, "total_amount": 19000}]},
            "receipt.jpg",
            "레드 와인 1병 결제금액 19,000원",
        )
        non_alcohol = _normalize(
            {"expense_category": "식비/주류", "items": [{"name": "무알코올 음료", "quantity": 1, "total_amount": 3000}]},
            "receipt.jpg",
            "무알코올 음료 결제금액 3,000원",
        )

        self.assertEqual(food["expense_category"], "식비")
        self.assertEqual(alcohol["expense_category"], "식비/주류")
        self.assertEqual(non_alcohol["expense_category"], "식비")

    def test_explicit_final_amount_overrides_model_selected_item_amount(self):
        ocr = "상품명 금액\n샤프 8,300\n최종 결제금액 56,300원"
        normalized = _normalize({"total_amount": 8300, "items": [{"name": "샤프", "quantity": 1, "total_amount": 8300}]}, "receipt.jpg", ocr)

        self.assertEqual(normalized["total_amount"], 56300)
        self.assertEqual(normalized["structured_data"]["deterministic_hints"]["total_amount_source"], "labeled_final")

    def test_splits_metadata_and_item_prompts(self):
        prompt = _receipt_prompt(
            "볼펜 2 1,000",
            "receipt.jpg",
            [{"page": 1, "tables": [{"confidence": .95, "rows": [["볼펜", "2", "1,000"]]}]}],
        )
        items_prompt = _receipt_items_prompt(
            "볼펜 2 1,000",
            [{"page": 1, "tables": [{"confidence": .95, "rows": [["볼펜", "2", "1,000"]]}]}],
        )
        self.assertIn("요약 정보만", prompt)
        self.assertNotIn("품목 행 후보", prompt)
        self.assertIn("품목 근거", items_prompt)
        self.assertIn('"raw_cells":["볼펜","2","1,000"]', items_prompt)

        self.assertIn('"evidence_bundles"', items_prompt)
        self.assertIn('"normalized_numbers"', items_prompt)
        self.assertIn('"arithmetic_relations"', items_prompt)
        self.assertIn('"parser_hypothesis"', items_prompt)
        self.assertIn('"is_binding":false', items_prompt)
        self.assertIn('"raw_cells_are_authoritative":true', items_prompt)
        self.assertNotIn('"resolved_fields"', items_prompt)
        self.assertIn('"parser_profile":"3-column_single-line_flat"', items_prompt)
        self.assertIn('"applicable_rules"', items_prompt)
        self.assertIn('"common_rules"', items_prompt)

    def test_semantic_evidence_uses_line_ids_and_allows_multiple_roles(self):
        ocr = "문구점 사업자번호 123-45-67890\n볼펜 2 1,000 2,000\n결제금액 신용카드 2,000원"
        candidates = [{
            "name_candidate": "볼펜", "raw_cells": ["볼펜", "2", "1,000", "2,000"],
            "quantity_candidate": 2, "unit_price_candidate": 1000, "amount_candidate": 2000,
        }]
        evidence = _semantic_receipt_evidence(ocr, candidates=candidates)

        self.assertEqual([line["id"] for line in evidence["lines"]], ["L001", "L002", "L003"])
        self.assertIn("L001", evidence["sections"]["issuer"])
        self.assertIn("L001", evidence["sections"]["business_info"])
        self.assertIn("L002", evidence["sections"]["items"])
        self.assertIn("L003", evidence["sections"]["settlement"])
        self.assertIn("L003", evidence["sections"]["payment"])

    def test_summary_and_item_payloads_do_not_repeat_item_lines(self):
        ocr = "상호명 문구점\n볼펜 2 1,000 2,000\n결제금액 2,000원\n신용카드 승인번호 12345678"
        pages = [{"page": 1, "tables": [{"rows": [["볼펜", "2", "1,000", "2,000"]]}]}]
        candidates = _receipt_item_candidates(pages)

        summary = _semantic_prompt_payload(ocr, pages, candidates, item_pass=False)
        items = _semantic_prompt_payload(ocr, pages, candidates, item_pass=True)

        self.assertNotIn("items", summary["sections"])
        self.assertIn("items", items["sections"])
        self.assertNotIn("item_candidates", summary)
        self.assertEqual(items["item_candidates"][0]["name_candidate"], "볼펜")
        self.assertEqual(summary["item_summary"]["candidate_amount_sum"], 2000)

    def test_semantic_lines_preserve_repeated_products_without_numeric_overmatching(self):
        ocr = "볼펜 1 1,000 1,000\n볼펜 1 1,000 1,000\n승인번호 10002000"
        evidence = _semantic_receipt_evidence(ocr, candidates=[{
            "name_candidate": "볼펜", "raw_cells": ["볼펜", "1", "1,000", "1,000"],
            "amount_candidate": 1000,
        }])

        item_texts = [line["text"] for line in evidence["lines"] if line["id"] in evidence["sections"]["items"]]
        self.assertEqual(item_texts.count("볼펜 1 1,000 1,000"), 2)
        approval_id = next(line["id"] for line in evidence["lines"] if line["text"] == "승인번호 10002000")
        self.assertNotIn(approval_id, evidence["sections"]["items"])

    def test_item_payload_excludes_unknown_lines_and_coordinates(self):
        pages = [{
            "page": 1,
            "items": [
                {"text": "문구점", "bbox": [[0, 0], [50, 10]]},
                {"text": "볼펜 1 1,000 1,000", "bbox": [[0, 20], [100, 30]]},
                {"text": "감사합니다", "bbox": [[0, 40], [60, 50]]},
            ],
            "tables": [{"rows": [["볼펜", "1", "1,000", "1,000"]]}],
        }]
        candidates, _ = _reliable_item_candidates(_receipt_item_candidates(pages))
        payload = _semantic_prompt_payload("", pages, candidates, item_pass=True)

        self.assertNotIn("unknown", payload["sections"])
        self.assertTrue(all("bbox" not in line and "page" not in line for line in payload["lines"]))

    def test_rejects_metadata_and_accepts_arithmetic_product_candidates(self):
        accepted, rejected = _reliable_item_candidates([
            {"source": "ocr_line_unscoped", "name_candidate": "카드 번호", "amount_candidate": 1922, "raw_cells": ["카드 번호 4140-****-1922"]},
            {"source": "ocr_line_unscoped", "name_candidate": "볼펜", "quantity_candidate": 2, "unit_price_candidate": 1000, "amount_candidate": 2000, "raw_cells": ["볼펜 2 1,000 2,000"]},
        ])

        self.assertEqual([item["name_candidate"] for item in accepted], ["볼펜"])
        self.assertEqual([item["name_candidate"] for item in rejected], ["카드 번호"])
        self.assertEqual(accepted[0]["rel"], "M")
        self.assertIn("A", accepted[0]["why"])
        self.assertEqual(rejected[0]["rel"], "L")

    def test_candidate_reliability_uses_compact_count_and_total_reasons(self):
        accepted, _ = _reliable_item_candidates([{
            "source": "table", "name_candidate": "USB", "amount_candidate": 7900,
            "raw_cells": ["USB", "7,900"],
        }], {"stated_item_count": 1, "total_amount": 7900})

        self.assertEqual(accepted[0]["rel"], "H")
        self.assertEqual(accepted[0]["why"], ["T", "C", "S"])
        prompt = _receipt_items_prompt("총품목/총수량 총구매금액\n1/1 7,900", [{
            "page": 1, "tables": [{"rows": [["USB", "7,900"]]}],
        }])
        self.assertIn('"reliability":"H"', prompt)
        self.assertIn('"reasons":["T","A","C","S"]', prompt)

    async def test_uses_strict_fast_path_before_item_model_for_grounded_candidates(self):
        pages = [{"page": 1, "tables": [{"rows": [
            ["책", "1", "5,000", "5,000"], ["노트", "1", "3,000", "3,000"],
        ]}]}]
        responses = ['{"merchant":"문구점","total_amount":8000}', ValueError("invalid item json")]
        with patch("app.api.routes.finance.generate", new=AsyncMock(side_effect=responses)):
            result = await _classify_receipt_with_model("총품목/총수량 총구매금액\n2/2 8,000", "receipt.jpg", "test-model", pages)

        self.assertEqual([item["name"] for item in result["items"]], ["책", "노트"])
        self.assertEqual(result["item_extraction_diagnostics"]["fallback_used"], "strict_grounded_fast_path")
        self.assertEqual(result["llm_trace"]["items_call_status"], "skipped_grounded_fast_path")
        self.assertIn("summary_latency_ms", result["llm_trace"])
        self.assertIn("items_prompt_chars", result["llm_trace"])

    def test_classifies_item_structure_as_independent_attributes(self):
        structure = _classify_item_structure([{
            "source": "discounted_item_block", "rel": "H",
            "name_candidate": "coffee", "quantity_candidate": 2,
            "unit_price_candidate": 4000, "amount_candidate": 8000,
            "option_candidates": ["extra shot"],
        }])

        self.assertEqual(structure, {
            "column_schema": "4-column",
            "layout": "multi-line",
            "relationship": "parent-child",
            "confidence": "high",
        })

    def test_assigns_only_bundle_specific_parser_rules(self):
        two_column = _item_parser_profile({
            "source": "table", "raw_cells": ["coffee", "5,000"],
            "name_candidate": "coffee", "amount_candidate": 5000,
        })
        parent_child = _item_parser_profile({
            "source": "discounted_item_block",
            "raw_cells": ["extra shot", "1,000"],
            "name_candidate": "extra shot", "amount_candidate": 1000,
            "option_candidates": ["extra shot"],
        })

        self.assertEqual(two_column["profile"], "2-column_single-line_flat")
        self.assertTrue(any("Do not default quantity" in rule for rule in two_column["rules"]))
        self.assertFalse(any("four observed cells" in rule for rule in two_column["rules"]))
        self.assertEqual(parent_child["profile"], "2-column_multi-line_parent-child")
        self.assertTrue(any("preceding parent" in rule for rule in parent_child["rules"]))

    def test_strict_grounded_fast_path_requires_complete_exact_evidence(self):
        candidates = [{
            "source": "table", "rel": "H", "why": ["T", "A", "C", "S"],
            "name_candidate": "책", "quantity_candidate": 2,
            "unit_price_candidate": 4000, "amount_candidate": 8000,
        }]

        items, reason = _strict_grounded_item_fast_path(
            candidates,
            {"stated_item_count": 1, "total_amount": 8000},
            1,
        )

        self.assertEqual(reason, "strict_grounded_fast_path")
        self.assertEqual(items[0]["name"], "책")
        self.assertEqual(items[0]["quantity"], 2)

    def test_strict_grounded_fast_path_rejects_uncertain_or_metadata_candidates(self):
        base = {
            "source": "table", "rel": "H", "name_candidate": "책",
            "quantity_candidate": 1, "unit_price_candidate": 8000,
            "amount_candidate": 8000,
        }
        uncertain = {**base, "uncertainty": ["quantity_recovered_from_total"]}
        metadata = {**base, "name_candidate": "영수증 20250828 책"}

        self.assertEqual(
            _strict_grounded_item_fast_path([uncertain], {"total_amount": 8000}, 1),
            ([], None),
        )
        self.assertEqual(
            _strict_grounded_item_fast_path([metadata], {"total_amount": 8000}, 1),
            ([], None),
        )

    def test_strict_grounded_fast_path_accepts_multirow_table_with_model_total(self):
        candidates = [
            {
                "source": "table", "rel": "H", "name_candidate": "책 A",
                "quantity_candidate": 1, "unit_price_candidate": 5000,
                "amount_candidate": 5000,
            },
            {
                "source": "table", "rel": "H", "name_candidate": "책 B",
                "quantity_candidate": 1, "unit_price_candidate": 3000,
                "amount_candidate": 3000,
            },
        ]

        items, reason = _strict_grounded_item_fast_path(candidates, {}, None, 8000)

        self.assertEqual(reason, "strict_grounded_fast_path")
        self.assertEqual([item["name"] for item in items], ["책 A", "책 B"])

    async def test_skips_item_llm_for_strict_grounded_candidates(self):
        pages = [{"page": 1, "tables": [{"rows": [
            ["책", "2", "4,000", "8,000"],
        ]}]}]
        responses = ['{"merchant":"문구점","total_amount":8000}']
        with patch("app.api.routes.finance.generate", new=AsyncMock(side_effect=responses)) as mocked:
            result = await _classify_receipt_with_model(
                "총품목/총수량 총구매금액\n1/2 8,000", "receipt.jpg", "test-model", pages,
            )

        self.assertEqual(mocked.await_count, 1)
        self.assertEqual(result["items"][0]["name"], "책")
        self.assertEqual(result["llm_trace"]["items_latency_ms"], 0)
        self.assertEqual(result["llm_trace"]["items_call_status"], "skipped_grounded_fast_path")

    async def test_recovers_multirow_arithmetic_table_and_total_after_model_failure(self):
        pages = [{"page": 1, "tables": [{"rows": [
            ["책 A", "1", "6,700", "6,700"],
            ["책 B", "1", "4,900", "4,900"],
            ["책 C", "1", "8,300", "8,300"],
        ]}]}]
        responses = ['{"merchant":"서점","total_amount":8300}', ValueError("timeout")]
        with patch("app.api.routes.finance.generate", new=AsyncMock(side_effect=responses)):
            result = await _classify_receipt_with_model("서점 구매 영수증", "receipt.jpg", "test-model", pages)

        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(result["total_amount"], 19900)
        self.assertEqual(result["item_extraction_diagnostics"]["fallback_used"], "validated_table_candidate_recovery")

    def test_prefers_table_and_does_not_rescan_page_metadata(self):
        candidates = _receipt_item_candidates([{
            "page": 1,
            "tables": [{"rows": [["볼펜", "2", "1,000", "2,000"], ["합계", "2,000"]]}],
            "text": "노트 1 3,000 3,000\n부가세 455",
        }])

        self.assertEqual([item["name_candidate"] for item in candidates], ["볼펜"])
        self.assertEqual(candidates[0]["quantity_candidate"], 2)
        self.assertEqual(candidates[0]["unit_price_candidate"], 1000)
        self.assertEqual(candidates[0]["amount_candidate"], 2000)

    def test_resolves_quantity_and_unit_price_without_fixed_column_order(self):
        candidates = _receipt_item_candidates([{
            "page": 1,
            "tables": [{"rows": [["브러시드 알파카", "12,600", "6", "75,600"]]}],
            "text": "판매번호 7 6,000 42,000",
        }])

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["quantity_candidate"], 6)
        self.assertEqual(candidates[0]["unit_price_candidate"], 12600)
        self.assertEqual(candidates[0]["column_resolution"], "arithmetic")

    def test_uses_explicit_physical_column_roles(self):
        candidates = _receipt_item_candidates([{
            "page": 1,
            "tables": [{
                "columns": ["name", "unit_price", "quantity", "amount"],
                "rows": [["브러시드 알파카", "12,600", "6", "75,600"]],
            }],
        }])

        self.assertEqual(candidates[0]["quantity_candidate"], 6)
        self.assertEqual(candidates[0]["unit_price_candidate"], 12600)
        self.assertEqual(candidates[0]["column_resolution"], "header")

    def test_preserves_parenthesized_alternate_price_without_dropping_item(self):
        candidates = _receipt_item_candidates([{
            "page": 1,
            "tables": [{"rows": [["[DIY] 스카프 도안", "1", "6,000 (5,700)"]]}],
        }])

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["quantity_candidate"], 1)
        self.assertEqual(candidates[0]["unit_price_candidate"], 6000)
        self.assertEqual(candidates[0]["amount_candidate"], 6000)
        self.assertEqual(candidates[0]["alternate_price_candidates"], [5700])
        self.assertEqual(candidates[0]["candidate_type"], "incomplete_item")

    async def test_retries_item_only_extraction_when_first_result_is_incomplete(self):
        pages = [{
            "page": 1,
            "text": "볼펜 1 1,000 1,000\n노트 1 3,000 3,000",
            "tables": [{"rows": [["볼펜", "1", "1,000", "1,000"], ["노트", "1", "3,000", "3,000"]]}],
        }]
        responses = [
            '{"merchant":"문구점","items":[{"name":"볼펜"}]}',
            '{"items":[{"name":"볼펜"},{"name":"노트"}]}',
        ]
        with patch("app.api.routes.finance.generate", new=AsyncMock(side_effect=responses)) as mocked:
            result = await _classify_receipt_with_model("품목 정보", "receipt.jpg", "test-model", pages)

        self.assertEqual([item["name"] for item in result["items"]], ["볼펜", "노트"])
        self.assertEqual(mocked.await_count, 2)

    def test_recovers_finance_values_and_filename_context(self):
        hints = _receipt_hints(SAMPLE_OCR, "서울출장_식비.jpg")
        self.assertEqual(hints["document_type"], "TRAVEL_EXPENSE")
        self.assertEqual(hints["expense_category"], "식비")
        self.assertEqual(hints["transaction_date"], "2025-10-05")
        self.assertEqual(hints["supply_amount"], 28545)
        self.assertEqual(hints["tax_amount"], 2855)
        self.assertEqual(hints["total_amount"], 31400)

    def test_recovers_date_when_ocr_joins_date_and_time(self):
        hints = _receipt_hints("판매일자 2025-10-0516:50", "서울출장_식비.jpg")
        self.assertEqual(hints["transaction_date"], "2025-10-05")

    def test_recovers_two_digit_pos_transaction_date(self):
        hints = _receipt_hints("거래일시:23/08/05 13:07:52", "receipt.jpg")

        self.assertEqual(hints["transaction_date"], "2023-08-05")

    def test_recovers_unit_price_from_item_total_and_quantity(self):
        normalized = _normalize(
            {"items": [{"name": "헤어컷", "quantity": 1, "total_amount": 140000}]},
            "receipt.jpg",
            "헤어컷 140,000원",
        )

        item = normalized["structured_data"]["items"][0]
        self.assertEqual(item["unit_price"], 140000)
        self.assertIn("단가 복원", item["note"])

    def test_deterministic_values_override_broken_llm_numbers(self):
        normalized = _normalize(
            {
                "document_type": "EXPENSE_REPORT",
                "expense_category": "식비",
                "merchant": "광화문점",
                "transaction_date": None,
                "supply_amount": 28.545,
                "tax_amount": 2.855,
                "total_amount": 31.4,
                "items": [],
            },
            "서울출장_식비.jpg",
            SAMPLE_OCR,
        )
        self.assertEqual(normalized["document_type"], "TRAVEL_EXPENSE")
        self.assertEqual(normalized["total_amount"], 31400)

    def test_ocr_cash_evidence_overrides_llm_placeholder(self):
        normalized = _normalize(
            {"payment_method": "-", "items": []},
            "receipt.jpg",
            "개인택시 현금결제 96,200원",
        )

        self.assertEqual(normalized["payment_method"], "현금")

    def test_derives_total_quantity_from_resolved_item_quantities(self):
        normalized = _normalize(
            {
                "doc_type": "TRAVEL_EXPENSE",
                "expense_category": "교통",
                "merchant": "개인택시",
                "total_amount": 96200,
                "items": [{
                    "name": "택시 이용",
                    "quantity": 1,
                    "unit_price": 96200,
                    "total_amount": 96200,
                }],
            },
            "receipt_005.jpg",
            "개인택시 현금결제 96,200원",
        )

        self.assertEqual(normalized["structured_data"]["total_quantity"], 1)

    def test_does_not_derive_total_quantity_when_an_item_quantity_is_missing(self):
        normalized = _normalize(
            {
                "items": [
                    {"name": "상품 A", "quantity": 1, "total_amount": 1000},
                    {"name": "상품 B", "quantity": None, "total_amount": 2000},
                ],
                "total_amount": 3000,
            },
            "receipt.jpg",
            "결제금액 3,000원",
        )

        self.assertIsNone(normalized["structured_data"].get("total_quantity"))

    def test_rejects_cashback_and_fuel_discount_rows_returned_as_items(self):
        normalized = _normalize(
            {
                "items": [
                    {"name": "보통 휘발유", "quantity": "20.994", "unit_price": 1429, "total_amount": 30000},
                    {"name": "자동 캐시백", "quantity": 1, "total_amount": 840},
                    {"name": "할인 전 주유단가", "quantity": 1, "total_amount": 1469},
                ],
                "total_amount": 30000,
                "item_extraction_diagnostics": {"candidates": []},
            },
            "receipt_011.jpg",
            "보통 휘발유 1,429 X 20.994 30,000\n자동 캐시백 -840원\n할인 전 주유단가 1,469원",
        )

        structured = normalized["structured_data"]
        self.assertEqual([item["name"] for item in structured["items"]], ["보통 휘발유"])
        self.assertEqual(structured["items"][0]["quantity"], 20.994)
        self.assertEqual(len(structured["item_extraction_diagnostics"]["rejected_adjustment_items"]), 2)

    def test_excludes_cashback_rows_from_item_candidates(self):
        candidates = _receipt_item_candidates([{
            "page": 1,
            "text": "보통 휘발유 1,429 20.994 30,000\n자동 캐시백 -840원",
            "tables": [{"rows": [
                ["보통 휘발유", "1,429", "20.994", "30,000"],
                ["자동 캐시백", "1", "840", "840"],
            ]}],
        }])

        self.assertEqual([candidate["name_candidate"] for candidate in candidates], ["보통 휘발유"])
        self.assertEqual(candidates[0]["quantity_candidate"], 20.994)

    def test_discounted_item_pair_preserves_explicit_quantity_column(self):
        candidates = _receipt_item_candidates([{
            "page": 1,
            "text": "상품 A 3,000 2\n할인 -200 2,800",
            "tables": [{
                "columns": ["name", "unit_price", "quantity"],
                "rows": [
                    ["상품 A", "3,000", "2"],
                    ["할인", "-200", "2,800"],
                ],
            }],
        }])

        self.assertEqual(candidates[0]["quantity_candidate"], 2)
        self.assertEqual(candidates[0]["quantity_resolution"], "explicit_table_column")

    def test_discounted_item_pair_uses_unique_arithmetic_quantity_without_headers(self):
        candidates = _receipt_item_candidates([{
            "page": 1,
            "text": "상품 B 1,500 2 3,000\n할인 -200 2,800",
            "tables": [{"rows": [
                ["상품 B", "1,500", "2", "3,000"],
                ["할인", "-200", "2,800"],
            ]}],
        }])

        self.assertEqual(candidates[0]["quantity_candidate"], 2)
        self.assertEqual(candidates[0]["quantity_resolution"], "discount_block_arithmetic")

    def test_discounted_item_pair_keeps_single_item_default_without_quantity_evidence(self):
        candidates = _receipt_item_candidates([{
            "page": 1,
            "text": "상품 C 3,000\n할인 -200 2,800",
            "tables": [{
                "columns": ["name", "unit_price"],
                "rows": [
                    ["상품 C", "3,000"],
                    ["할인 -200", "2,800"],
                ],
            }],
        }])

        self.assertEqual(candidates[0]["quantity_candidate"], 1)
        self.assertEqual(candidates[0]["quantity_resolution"], "single_item_default")

    def test_builds_reliable_multi_line_fuel_sale_candidate(self):
        text = """농협중앙회 신용매출전표
카드종류 NH카드/신용승인
합계금액 72,000원
초저유황경유 51.8L 1390원"""
        pages = [{"page": 1, "text": text, "tables": []}]

        raw_candidates = _receipt_item_candidates(pages)
        candidates, rejected = _reliable_item_candidates(raw_candidates, _receipt_hints(text, "receipt_013.jpg"))

        self.assertFalse(rejected)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["candidate_type"], "fuel_sale_item")
        self.assertEqual(candidate["source"], "fuel_sale_block")
        self.assertEqual(candidate["name_candidate"], "초저유황경유")
        self.assertEqual(candidate["quantity_candidate"], 51.8)
        self.assertEqual(candidate["unit"], "L")
        self.assertEqual(candidate["unit_price_candidate"], 1390)
        self.assertEqual(candidate["amount_candidate"], 72000)
        self.assertEqual(candidate["rel"], "H")
        self.assertTrue({"F", "A", "S"}.issubset(candidate["why"]))

    def test_uses_fuel_specific_item_prompt_profile(self):
        text = "합계금액 72,000원\n초저유황경유 51.8L 1390원"
        prompt = _receipt_items_prompt(text, [{"page": 1, "text": text, "tables": []}])

        self.assertIn('"candidate_type":"fuel_sale_item"', prompt)
        self.assertIn('"parser_profile":"fuel_sale_item_multi-line_flat"', prompt)
        self.assertIn("price per litre", prompt)
        self.assertIn('"tolerance":144', prompt)

    async def test_recovers_fuel_item_when_item_model_fails(self):
        text = """농협중앙회 신용매출전표
카드종류 NH카드/신용승인
합계금액 72,000원
초저유황경유 51.8L 1390원"""
        pages = [{"page": 1, "text": text, "tables": []}]
        responses = ['{"merchant":"속입주유소","total_amount":72000}', ValueError("invalid item json")]

        with patch("app.api.routes.finance.generate", new=AsyncMock(side_effect=responses)):
            result = await _classify_receipt_with_model(text, "receipt_013.jpg", "test-model", pages)

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["name"], "초저유황경유")
        self.assertEqual(result["items"][0]["quantity"], 51.8)
        self.assertEqual(result["items"][0]["unit"], "L")
        self.assertEqual(result["items"][0]["unit_price"], 1390)
        self.assertEqual(result["items"][0]["total_amount"], 72000)
        self.assertEqual(result["items"][0]["candidate_type"], "fuel_sale_item")
        self.assertEqual(result["item_extraction_diagnostics"]["fallback_used"], "ocr_candidates_match_receipt_total")

    def test_builds_narrow_unitemized_golf_service_candidate(self):
        text = """비씨카드 승인
가맹점 (주)한양컨트리클럽
합계금액 79,000원"""
        raw_candidates = _receipt_item_candidates([{"page": 1, "text": text, "tables": []}])
        candidates, _ = _reliable_item_candidates(raw_candidates, _receipt_hints(text, "receipt_017.jpg"))

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["structure_type"], "unitemized_charge")
        self.assertEqual(candidate["candidate_type"], "single_service_charge")
        self.assertEqual(candidate["service_type"], "golf_course_service")
        self.assertEqual(candidate["name_candidate"], "컨트리클럽 이용")
        self.assertEqual(candidate["quantity_candidate"], 1)
        self.assertEqual(candidate["amount_candidate"], 79000)
        self.assertEqual(candidate["rel"], "M")
        self.assertIn("D", candidate["why"])

    def test_uses_unitemized_service_prompt_profile(self):
        text = "비씨카드 승인\n가맹점 한양컨트리클럽\n합계금액 79,000원"
        prompt = _receipt_items_prompt(text, [{"page": 1, "text": text, "tables": []}])

        self.assertIn('"structure_type":"unitemized_charge"', prompt)
        self.assertIn('"service_type":"golf_course_service"', prompt)
        self.assertIn('"parser_profile":"single_service_charge_unitemized_flat"', prompt)

    async def test_recovers_unitemized_service_when_item_model_fails(self):
        text = "비씨카드 승인\n가맹점 (주)한양컨트리클럽\n합계금액 79,000원"
        pages = [{"page": 1, "text": text, "tables": []}]
        responses = ['{"merchant":"한양컨트리클럽","total_amount":79000}', ValueError("invalid item json")]

        with patch("app.api.routes.finance.generate", new=AsyncMock(side_effect=responses)):
            result = await _classify_receipt_with_model(text, "receipt_017.jpg", "test-model", pages)

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["name"], "컨트리클럽 이용")
        self.assertEqual(result["items"][0]["quantity"], 1)
        self.assertEqual(result["items"][0]["unit"], "회")
        self.assertEqual(result["items"][0]["total_amount"], 79000)
        self.assertEqual(result["items"][0]["item_resolution"], "single_service_domain_recovery")

    def test_does_not_infer_unitemized_service_for_generic_or_itemized_receipts(self):
        generic = "비씨카드 승인\n가맹점 일반상점\n합계금액 79,000원"
        self.assertFalse(any(
            candidate.get("candidate_type") == "single_service_charge"
            for candidate in _receipt_item_candidates([{"page": 1, "text": generic, "tables": []}])
        ))

        itemized = "한양컨트리클럽\n상품명 수량 금액\n생수 1 1,000\n합계금액 1,000원"
        self.assertFalse(any(
            candidate.get("candidate_type") == "single_service_charge"
            for candidate in _receipt_item_candidates([{
                "page": 1,
                "text": itemized,
                "tables": [{"columns": ["name", "quantity", "amount"], "rows": [["생수", "1", "1,000"]]}],
            }])
        ))

    def test_explicit_ocr_payment_evidence_overrides_conflicting_llm_value(self):
        cash = _normalize(
            {"payment_method": "신용카드", "items": []},
            "receipt.jpg",
            "현금결제 96,200원",
        )
        card = _normalize(
            {"payment_method": "현금", "items": []},
            "receipt.jpg",
            "신용카드 승인 96,200원",
        )

        self.assertEqual(cash["payment_method"], "현금")
        self.assertEqual(card["payment_method"], "카드")

    def test_preserves_card_issuer_when_it_agrees_with_ocr_evidence(self):
        normalized = _normalize(
            {"payment_method": "신한카드", "items": []},
            "receipt.jpg",
            "카드결제 96,200원",
        )

        self.assertEqual(normalized["payment_method"], "신한카드")

    def test_accepts_only_explicitly_labeled_masked_card_number(self):
        accepted = _normalize(
            {"card_number": "LLM-잘못된값", "items": []},
            "receipt.jpg",
            "카드결제\n카드번호: 1234-56**-****-7890\n승인번호 998877",
        )
        rejected = _normalize(
            {"card_number": "1234-5678-9012-3456", "items": []},
            "receipt.jpg",
            "카드결제\n카드번호: 1234-5678-9012-3456",
        )

        self.assertEqual(accepted["structured_data"]["card_number"], "1234-56**-****-7890")
        self.assertTrue(accepted["structured_data"]["card_number_evidence"]["accepted"])
        self.assertIsNone(rejected["structured_data"]["card_number"])
        self.assertEqual(
            rejected["structured_data"]["card_number_evidence"]["reason"],
            "missing_explicit_label_or_mask",
        )

    def test_evidenced_final_amount_is_not_overridden_by_arithmetic_hint(self):
        ocr = """품목1 6,000
품목2 75,600
상품합계 81,600
최종 결제금액 81,300원"""

        hints = _receipt_hints(ocr, "receipt.jpg")
        normalized = _normalize(
            {"total_amount": 81300, "items": []},
            "receipt.jpg",
            ocr,
        )

        self.assertEqual(hints["total_amount"], 81300)
        self.assertEqual(normalized["total_amount"], 81300)

    def test_recognizes_additional_final_payment_labels_and_spaced_amount(self):
        hints = _receipt_hints("신용 판매액 : 96, 200원", "receipt.jpg")

        self.assertEqual(hints["total_amount"], 96200)

    def test_reconciles_item_name_with_unique_ocr_amount_row(self):
        resolved = _reconcile_items_with_candidates(
            [{"name": "판매일시 POS 상품코드 노트", "quantity": 1, "unit_price": 7900, "total_amount": 7900}],
            [{"name_candidate": "노트", "quantity_candidate": 1, "unit_price_candidate": 7900, "amount_candidate": 7900}],
            1,
        )

        self.assertEqual(resolved[0]["name"], "노트")
        self.assertEqual(resolved[0]["raw_model_name"], "판매일시 POS 상품코드 노트")
        self.assertEqual(resolved[0]["name_resolution"], "unique_ocr_amount_match")

    def test_recovers_missing_quantities_only_from_unique_structured_arithmetic_rows(self):
        resolved = _reconcile_items_with_candidates(
            [
                {"name": "LG 모니터", "quantity": None, "unit_price": None, "total_amount": 799000},
                {"name": "RAZER 마우스", "quantity": None, "unit_price": None, "total_amount": 69900},
            ],
            [
                {
                    "source": "table", "column_resolution": "header", "name_candidate": "LG 모니터",
                    "quantity_candidate": 1, "unit_price_candidate": 799000, "amount_candidate": 799000,
                },
                {
                    "source": "table", "column_resolution": "header", "name_candidate": "RAZER 마우스",
                    "quantity_candidate": 1, "unit_price_candidate": 69900, "amount_candidate": 69900,
                },
            ],
            2,
        )

        self.assertEqual([item["quantity"] for item in resolved], [1, 1])
        self.assertEqual([item["unit_price"] for item in resolved], [799000, 69900])
        self.assertTrue(all(
            item["quantity_resolution"] == "unique_structured_arithmetic_match"
            for item in resolved
        ))

    def test_high_confidence_candidate_numbers_override_llm_changes(self):
        resolved = _reconcile_items_with_candidates(
            [{"name": "LG 모니터", "quantity": 9, "unit_price": 111, "total_amount": 999}],
            [{
                "rel": "H", "source": "table", "column_resolution": "header",
                "name_candidate": "LG 모니터", "quantity_candidate": 1,
                "unit_price_candidate": 799000, "amount_candidate": 799000,
            }],
            1,
        )

        self.assertEqual(resolved[0]["quantity"], 1)
        self.assertEqual(resolved[0]["unit_price"], 799000)
        self.assertEqual(resolved[0]["total_amount"], 799000)
        self.assertEqual(
            resolved[0]["protected_candidate_fields"],
            ["quantity", "unit_price", "total_amount"],
        )
        self.assertEqual(resolved[0]["raw_model_total_amount"], 999)

    def test_does_not_recover_quantity_from_unstructured_or_failed_arithmetic_candidate(self):
        resolved = _reconcile_items_with_candidates(
            [{"name": "상품", "quantity": None, "unit_price": None, "total_amount": 10000}],
            [{
                "source": "ocr_line_unscoped", "column_resolution": "plausibility",
                "name_candidate": "상품", "quantity_candidate": 2,
                "unit_price_candidate": 4000, "amount_candidate": 10000,
            }],
            1,
        )

        self.assertIsNone(resolved[0]["quantity"])
        self.assertIsNone(resolved[0]["unit_price"])

    def test_does_not_reconcile_ambiguous_same_price_item_rows(self):
        resolved = _reconcile_items_with_candidates(
            [{"name": "모델 상품명", "quantity": 1, "unit_price": 5000, "total_amount": 5000}],
            [
                {"name_candidate": "노트", "quantity_candidate": 1, "unit_price_candidate": 5000, "amount_candidate": 5000},
                {"name_candidate": "펜", "quantity_candidate": 1, "unit_price_candidate": 5000, "amount_candidate": 5000},
            ],
            2,
        )

        self.assertEqual(resolved[0]["name"], "모델 상품명")

    def test_recognizes_discount_relation_instead_of_tax_relation(self):
        ocr = "상품합계 3,900원\n할인금액 780원\n최종 결제금액 3,120원"

        hints = _receipt_hints(ocr, "receipt.jpg")
        normalized = _normalize(
            {"supply_amount": 3120, "tax_amount": 780, "total_amount": 3900},
            "receipt.jpg",
            ocr,
        )

        self.assertEqual(hints["total_amount"], 3120)
        self.assertEqual(hints["discount_amount"], 780)
        self.assertEqual(hints["amount_relation"]["type"], "GROSS_MINUS_DISCOUNT_EQUALS_PAID")
        self.assertEqual(normalized["total_amount"], 3120)
        self.assertEqual(normalized["structured_data"]["discount_amount"], 780)

    def test_removes_payment_summary_and_unsupported_zero_amount_items(self):
        normalized = _normalize(
            {
                "total_amount": 60000,
                "items": [
                    {"name": "유니클로(과세)", "quantity": 1, "unit_price": 60000, "total_amount": 60000},
                    {"name": "카드 결제액", "quantity": 1, "unit_price": 0, "total_amount": 0},
                    {"name": "승인번호", "quantity": 1},
                ],
            },
            "test01.jpg",
            "유니클로(과세) 1 60,000 카드 결제액 60,000 승인번호 123456",
        )

        self.assertEqual(len(normalized["structured_data"]["items"]), 1)
        self.assertEqual(normalized["structured_data"]["items"][0]["name"], "유니클로(과세)")

    def test_keeps_explicitly_free_item(self):
        normalized = _normalize(
            {"items": [{"name": "증정 상품", "quantity": 1, "unit_price": 0, "total_amount": 0}]},
            "receipt.jpg",
            "증정 상품 1 0",
        )

        self.assertEqual(len(normalized["structured_data"]["items"]), 1)

    def test_normalizes_trained_doc_type_key_for_internal_workbook_schema(self):
        normalized = _normalize(
            {"doc_type": "TRAVEL_EXPENSE", "expense_category": "교통", "total_amount": 96200},
            "receipt_005.jpg",
            "결제금액 96,200원",
        )

        self.assertEqual(normalized["document_type"], "TRAVEL_EXPENSE")
        self.assertEqual(normalized["structured_data"]["doc_type"], "TRAVEL_EXPENSE")

    def test_keeps_legacy_document_type_compatible(self):
        normalized = _normalize(
            {"document_type": "WELFARE_BENEFIT", "expense_category": "식비/생활", "total_amount": 10000},
            "receipt.jpg",
            "결제금액 10,000원",
        )

        self.assertEqual(normalized["document_type"], "WELFARE_BENEFIT")


    def test_recovers_korean_date_and_overrides_wrong_llm_date(self):
        ocr = "[등록]2016년09월 18일(일)12:55 POSNo.01"
        hints = _receipt_hints(ocr, "receipt_011.jpg")
        normalized = _normalize(
            {"transaction_date": "2023-09-18", "total_amount": 30000},
            "receipt_011.jpg",
            ocr,
        )

        self.assertEqual(hints["transaction_date"], "2016-09-18")
        self.assertEqual(normalized["transaction_date"], "2016-09-18")

    def test_prefers_labeled_transaction_date_over_longer_quality_inspection_date(self):
        ocr = "품질검사일자: 2017-11-09\n거래일시: 18/01/10 18:05:24"
        hints = _receipt_hints(ocr, "receipt_012.jpg")
        normalized = _normalize(
            {"transaction_date": "2017-11-09", "items": []},
            "receipt_012.jpg",
            ocr,
        )

        self.assertEqual(hints["transaction_date"], "2018-01-10")
        self.assertEqual(normalized["transaction_date"], "2018-01-10")

    def test_limits_items_to_stated_ocr_item_count_without_reinterpreting_them(self):
        ocr = "총품목/총수량 총구매금액\n2/7 81,300"
        items = [{"name": name} for name in ["첫 품목", "두 번째 해석", "오인식 1", "오인식 2", "오인식 3"]]
        normalized = _normalize(
            {"document_type": "PURCHASE_REQUEST", "items": items},
            "receipt.jpg",
            ocr,
        )

        self.assertEqual(normalized["structured_data"]["receipt_summary"]["stated_item_count"], 2)
        self.assertEqual(normalized["structured_data"]["receipt_summary"]["stated_total_quantity"], 7)
        self.assertEqual(normalized["structured_data"]["receipt_summary"]["stated_total_amount"], 81300)
        self.assertEqual(normalized["structured_data"]["items"], items[:2])

    def test_reads_korean_item_and_quantity_count_suffixes(self):
        hints = _receipt_hints("총품목/총수량 총구매금액\n2개/7개 81,600", "receipt.jpg")

        self.assertEqual(hints["stated_item_count"], 2)
        self.assertEqual(hints["stated_total_quantity"], 7)
        self.assertEqual(hints["stated_total_amount"], 81600)

    def test_reads_item_count_when_value_precedes_label(self):
        hints = _receipt_hints("2개/7개\n총품목/총수량\n81,600", "receipt.jpg")

        self.assertEqual(hints["stated_item_count"], 2)
        self.assertEqual(hints["stated_total_quantity"], 7)

    def test_reads_vertically_flattened_item_and_quantity_summary(self):
        hints = _receipt_hints(
            "총품목수 총수량\n2 2\n면세 761,755\n결제대상금액 837,930원",
            "receipt_018.jpg",
        )

        self.assertEqual(hints["stated_item_count"], 2)
        self.assertEqual(hints["stated_total_quantity"], 2)

    def test_does_not_treat_money_as_vertical_total_quantity(self):
        hints = _receipt_hints(
            "총품목수 총수량\n2 837,930원",
            "receipt.jpg",
        )

        self.assertIsNone(hints["stated_item_count"])
        self.assertIsNone(hints["stated_total_quantity"])

    def test_reads_ticket_total_count_without_inventing_item_count(self):
        ocr = "총매수: 2매\n취소매수: 0매\n승인금액: 16,200원"
        hints = _receipt_hints(ocr, "receipt_007.jpg")
        normalized = _normalize(
            {"items": [{"name": "티머니모빌리티 승차권", "quantity": None, "total_amount": 16200}]},
            "receipt_007.jpg",
            ocr,
        )

        self.assertIsNone(hints["stated_item_count"])
        self.assertEqual(hints["stated_total_quantity"], 2)
        self.assertEqual(normalized["structured_data"]["total_quantity"], 2)

    def test_sums_ticket_passenger_counts_as_total_quantity(self):
        ocr = "KTX 125 일반실 입석\n어른 1매, 어린이 0매 | 할인: 0명\n결제금액 50,800원"
        hints = _receipt_hints(ocr, "receipt_010.jpg")
        normalized = _normalize(
            {"items": [{"name": "KTX 125 일반실 승차권", "quantity": None, "total_amount": 50800}]},
            "receipt_010.jpg",
            ocr,
        )

        self.assertEqual(hints["stated_total_quantity"], 1)
        self.assertEqual(normalized["structured_data"]["total_quantity"], 1)

    def test_prefers_explicit_ticket_total_over_passenger_breakdown(self):
        hints = _receipt_hints(
            "총매수: 3매\n성인 1매 어린이 1매",
            "ticket.jpg",
        )

        self.assertEqual(hints["stated_total_quantity"], 3)

    def test_reads_short_date_with_time_attached_to_day(self):
        hints = _receipt_hints("거래일시: 25/09/2115:47:32", "receipt.jpg")

        self.assertEqual(hints["transaction_date"], "2025-09-21")

    def test_trims_metadata_preserves_empty_columns_and_recovers_missing_quantity(self):
        candidates = _receipt_item_candidates([{
            "page": 1,
            "text": "2개/7개\n총품목/총수량",
            "tables": [{
                "columns": ["name", "unit_price", "quantity", "amount"],
                "rows": [
                    ["판매번호 2850787 포스번호 P007 [DIY] 스카프 도안", "", "6,000", "6,000 (5,700)"],
                    ["브러시드 알파카 퍼루", "", "12,600", "6 75,600"],
                ],
            }],
        }])

        self.assertEqual(len(candidates), 2)
        self.assertTrue(candidates[0]["name_candidate"].startswith("[DIY]"))
        self.assertEqual(candidates[0]["quantity_candidate"], 1)
        self.assertEqual(candidates[0]["quantity_resolution"], "receipt_total_remainder")
        self.assertEqual(candidates[0]["unit_price_candidate"], 6000)
        self.assertEqual(candidates[1]["quantity_candidate"], 6)
        self.assertEqual(candidates[1]["unit_price_candidate"], 12600)

    def test_removes_embedded_transaction_header_from_book_item_name(self):
        polluted_name = (
            "[판매 매] 2021-09-20 11:36:10 POS:0117-0011 "
            "상품코드 단가 수량 금액 [레디스크] 271/16G"
        )
        pages = [{
            "page": 1,
            "tables": [{
                "columns": ["name", "unit_price", "quantity", "amount"],
                "rows": [[polluted_name, "7,900", "1", "7,900"]],
            }],
        }]

        candidates = _receipt_item_candidates(pages)

        self.assertEqual(candidates[0]["name_candidate"], "[레디스크] 271/16G")
        self.assertEqual(candidates[0]["raw_name_candidate"], polluted_name)
        self.assertIn("embedded_item_header_removed", candidates[0]["name_cleanup"])

    def test_postprocesses_polluted_model_item_name_without_inventing_text(self):
        raw_name = "2021-09-20 POS:0117-0011 상품코드 단가 수량 금액 [레디스크] 271/16G"
        normalized = _normalize(
            {"items": [{"name": raw_name, "quantity": 1, "unit_price": 7900, "total_amount": 7900}]},
            "receipt.jpg",
            raw_name,
        )

        item = normalized["structured_data"]["items"][0]
        self.assertEqual(item["name"], "[레디스크] 271/16G")
        self.assertEqual(item["raw_name"], raw_name)
        self.assertIn("embedded_item_header_removed", item["name_cleanup"])

    def test_merges_two_line_book_items_and_separates_inventory_codes(self):
        candidates = _receipt_item_candidates([{
            "page": 1,
            "tables": [{
                "columns": ["name", "unit_price", "quantity", "amount"],
                "rows": [
                    ["001 [중고] 미학사전 U102768084", "6,700", "", "6,700"],
                    ["002 [중고] 바로크와 로코코", "", "", ""],
                    ["U102384189", "4,900", "1", "4,900"],
                    ["005 [중고] 여인들의 행복 백화점2", "", "", ""],
                    ["U602490729", "5,500", "1", "5,500"],
                    ["수 량", "", "", "9"],
                    ["총합계", "", "", "56,300"],
                    ["면세상품 액", "", "", "56,300"],
                ],
            }],
        }])

        self.assertEqual([candidate["name_candidate"] for candidate in candidates], [
            "[중고] 미학사전", "[중고] 바로크와 로코코", "[중고] 여인들의 행복 백화점2",
        ])
        self.assertEqual([candidate["product_code"] for candidate in candidates], [
            "U102768084", "U102384189", "U602490729",
        ])
        self.assertEqual([candidate["quantity_candidate"] for candidate in candidates], [1, 1, 1])
        self.assertEqual([candidate["unit_price_candidate"] for candidate in candidates], [6700, 4900, 5500])

    def test_postprocesses_summary_rows_and_item_number_formats(self):
        normalized = _normalize(
            {
                "items": [
                    {"name": " 브러쉬드 알파카 ", "quantity": "6", "unit_price": "12.600"},
                    {"name": "상품 합계 75,600", "total_amount": "75,600"},
                    {"name": "https://store.example/item"},
                ],
            },
            "receipt.jpg",
            "브러쉬드 알파카 6 12.600 상품 합계 75,600",
        )

        self.assertEqual(normalized["structured_data"]["items"], [
            {"name": "브러쉬드 알파카", "quantity": 6.0, "unit_price": 12600.0},
        ])

    def test_drops_hallucinated_discount_without_explicit_ocr_label(self):
        normalized = _normalize(
            {"discount_amount": 73909, "total_amount": 81300, "items": []},
            "receipt.jpg",
            "총 결제금액 81,300원",
        )

        self.assertIsNone(normalized["structured_data"]["discount_amount"])

    def test_keeps_explicit_ocr_discount_over_model_value(self):
        normalized = _normalize(
            {"discount_amount": 73909, "total_amount": 81300, "items": []},
            "receipt.jpg",
            "할인금액 300원 최종 결제금액 81,300원",
        )

        self.assertEqual(normalized["structured_data"]["discount_amount"], 300)

    def test_rejects_flattened_ocr_discount_that_conflicts_with_item_totals(self):
        normalized = _normalize(
            {
                "discount_amount": 73909,
                "total_amount": 81300,
                "items": [],
                "item_extraction_diagnostics": {
                    "candidates": [
                        {"name_candidate": "도안", "amount_candidate": 6000},
                        {"name_candidate": "실", "amount_candidate": 75600},
                    ],
                },
            },
            "receipt.jpg",
            "할인액 73,909원 최종 결제금액 81,300원",
        )

        structured = normalized["structured_data"]
        self.assertIsNone(structured["discount_amount"])
        self.assertEqual(
            structured["financial_evidence_diagnostics"]["expected_discount_amount"],
            300,
        )
        self.assertEqual(
            structured["financial_evidence_diagnostics"]["discount_rejection"],
            "inconsistent_with_item_gross_and_paid_total",
        )

    def test_keeps_discount_when_it_matches_item_gross_minus_paid_total(self):
        normalized = _normalize(
            {
                "discount_amount": 300,
                "total_amount": 81300,
                "items": [],
                "item_extraction_diagnostics": {
                    "candidates": [
                        {"name_candidate": "도안", "amount_candidate": 6000},
                        {"name_candidate": "실", "amount_candidate": 75600},
                    ],
                },
            },
            "receipt.jpg",
            "할인액 300원 최종 결제금액 81,300원",
        )

        self.assertEqual(normalized["structured_data"]["discount_amount"], 300)

    def test_separates_bilingual_alias_and_color_option_from_item_name(self):
        raw_name = "브러쉬드 알파카 페루(Brushed Alpaca Peru) (1볼/50g)(1304 그레이컬러)"
        normalized = _normalize(
            {"items": [{"name": raw_name, "quantity": 6, "unit_price": 12600, "total_amount": 75600}]},
            "receipt.jpg",
            raw_name,
        )

        item = normalized["structured_data"]["items"][0]
        self.assertEqual(item["name"], "브러쉬드 알파카 페루 (1볼/50g)")
        self.assertEqual(item["raw_name"], raw_name)
        self.assertIn("Brushed Alpaca Peru", item["specification"])
        self.assertIn("1304 그레이컬러", item["specification"])
        self.assertEqual(item["aliases"], ["Brushed Alpaca Peru"])
        self.assertEqual(item["options"], ["1304 그레이컬러"])

    def test_structures_ocr_candidate_alias_and_sku_option_before_llm(self):
        candidates = _receipt_item_candidates([{
            "page": 1,
            "tables": [{
                "columns": ["name", "unit_price", "quantity", "amount"],
                "rows": [[
                    "브러쉬드 알파카 페루(Brushed Alpaca Peru) (1볼/50g)(1304 그레이컬)",
                    "12,600", "6", "75,600",
                ]],
            }],
        }])

        self.assertEqual(candidates[0]["name_candidate"], "브러쉬드 알파카 페루 (1볼/50g)")
        self.assertEqual(candidates[0]["alias_candidates"], ["Brushed Alpaca Peru"])
        self.assertEqual(candidates[0]["option_candidates"], ["1304 그레이컬"])

    def test_structures_discounted_retail_item_blocks_and_single_amount_bag(self):
        pages = [{
            "page": 1,
            "text": "COS 현대 무역센터점 날짜:2025-09-21 합계 157,600",
            "tables": [{
                "columns": None,
                "rows": [
                    ["직원:681170 매장:4100257 영수증번호:2398 날짜:2025-09-21 POS번호:0003 시간:오후 3:47 져지", "", "", "", "135,000"],
                    ["1272839S노란색 할인 30%", "30%", "-40,500", "", "94,500"],
                    ["져지", "", "", "", "105,000"],
                    ["1286255S흰색 할인40%", "40%", "-42,000", "", "63,000"],
                    ["COS 쇼핑백", "", "", "", "100"],
                    ["X", "", "", "W157,600", "3"],
                    ["현대HDS", "", "", "", "1157,600"],
                ],
            }],
        }]

        candidates = _receipt_item_candidates(pages)

        self.assertEqual([candidate["name_candidate"] for candidate in candidates], [
            "져지 S노란색", "져지 S흰색", "COS 쇼핑백",
        ])
        self.assertEqual([candidate["unit_price_candidate"] for candidate in candidates], [135000, 105000, 100])
        self.assertEqual([candidate["amount_candidate"] for candidate in candidates], [94500, 63000, 100])
        self.assertEqual([candidate["quantity_candidate"] for candidate in candidates], [1, 1, 1])

    def test_recovers_items_when_structured_candidate_total_matches_receipt(self):
        candidates = [
            {"name_candidate": "져지 S노란색", "quantity_candidate": 1, "unit_price_candidate": 135000, "amount_candidate": 94500, "product_code": "1272839"},
            {"name_candidate": "져지 S흰색", "quantity_candidate": 1, "unit_price_candidate": 105000, "amount_candidate": 63000, "product_code": "1286255"},
            {"name_candidate": "COS 쇼핑백", "quantity_candidate": 1, "unit_price_candidate": 100, "amount_candidate": 100},
        ]
        normalized = _normalize(
            {
                "total_amount": 157600,
                "items": [{"name": "현대HDS", "quantity": 3, "unit_price": 1157600, "total_amount": 3472800}],
                "item_extraction_diagnostics": {"candidates": candidates},
            },
            "receipt.jpg",
            "COS 현대 무역센터점 합계 157,600",
        )

        items = normalized["structured_data"]["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual([item["name"] for item in items], ["져지 S노란색", "져지 S흰색", "COS 쇼핑백"])
        self.assertEqual(sum(item["total_amount"] for item in items), 157600)
        self.assertEqual(
            normalized["structured_data"]["item_extraction_diagnostics"]["items_resolution"],
            "ocr_candidates_match_receipt_total",
        )

    def test_recovers_tenant_merchant_when_model_misses_or_uses_mall_name(self):
        ocr = "Starfield 유니클로(과세) 상품명 주차정산QR코드 http://www.starfield.co.kr"

        missing = _normalize({"merchant": None, "items": []}, "receipt.jpg", ocr)
        mall = _normalize({"merchant": "Starfield", "items": []}, "receipt.jpg", ocr)
        misspelled_mall = _normalize({"merchant": "Starfiled", "items": []}, "receipt.jpg", ocr)
        taxed_brand = _normalize({"merchant": "유니클로(과세)", "items": []}, "receipt.jpg", ocr)

        self.assertEqual(missing["merchant"], "유니클로")
        self.assertEqual(mall["merchant"], "유니클로")
        self.assertEqual(misspelled_mall["merchant"], "유니클로")
        self.assertEqual(taxed_brand["merchant"], "유니클로")

    def test_does_not_override_an_unrelated_merchant_with_tenant_alias(self):
        normalized = _normalize(
            {"merchant": "다른 판매점", "items": []},
            "receipt.jpg",
            "Starfield 유니클로 안내",
        )

        self.assertEqual(normalized["merchant"], "다른 판매점")

    def test_recovers_uniqlo_single_item_and_card_payment_from_ocr(self):
        normalized = _normalize(
            {"merchant": None, "total_amount": 60000, "items": []},
            "receipt.jpg",
            "Starfiled 유니클로(과세) 상품명 카드 승인번호 결제금액 60,000원",
        )

        self.assertEqual(normalized["merchant"], "유니클로")
        self.assertEqual(normalized["payment_method"], "카드")
        self.assertEqual(normalized["structured_data"]["items"], [{
            "name": "유니클로(과세)",
            "quantity": 1.0,
            "unit_price": 60000.0,
            "total_amount": 60000.0,
            "note": "OCR 근거 기반 단일 품목 복원",
        }])

    def test_does_not_infer_card_from_approval_number_alone(self):
        normalized = _normalize(
            {"payment_method": None, "items": []},
            "receipt.jpg",
            "승인번호 123456 공급가액 3,120 부가세 780",
        )

        self.assertIsNone(normalized["payment_method"])

    def test_ignores_card_words_in_refund_and_cancellation_policy(self):
        ocr = """교환/환불은 구매 후 7일 이내에 영수증과
결제카드 지참(카드취소시)하셔야 합니다.
체크카드 취소 시 최대 7일이 소요됩니다"""

        hints = _receipt_hints(ocr, "receipt.jpg")
        normalized = _normalize({"payment_method": "카드", "items": []}, "receipt.jpg", ocr)

        self.assertIsNone(hints["payment_method"])
        self.assertIsNone(normalized["payment_method"])

    def test_keeps_explicit_payment_line_when_policy_text_is_also_present(self):
        ocr = """카드 결제액 3,120원
교환/환불 시 결제카드를 지참하세요
체크카드 취소 시 최대 7일이 소요됩니다"""

        normalized = _normalize({"payment_method": None, "items": []}, "receipt.jpg", ocr)

        self.assertEqual(normalized["payment_method"], "카드")

    def test_recognizes_card_sales_slip_and_ocr_misspelled_credit_approval(self):
        ocr = "현대백화점카드 매출표 신용송인\n9500-0034-****-594*"

        hints = _receipt_hints(ocr, "receipt.jpg")
        normalized = _normalize({"payment_method": None, "items": []}, "receipt.jpg", ocr)

        self.assertEqual(hints["payment_method"], "카드")
        self.assertEqual(normalized["payment_method"], "카드")

    def test_removes_hallucinated_items_from_itemless_card_sales_slip(self):
        result = {
            "payment_method": "현대백화점카드",
            "items": [{"name": "코카콜라 제로 2L", "quantity": 1, "total_amount": 990}],
            "item_extraction_diagnostics": {"candidates": [], "model_items": []},
        }

        normalized = _normalize(
            result,
            "receipt.jpg",
            "현대백화점카드 매출표 신용송인\n합계 157,600원",
        )

        diagnostics = normalized["structured_data"]["item_extraction_diagnostics"]
        self.assertEqual(normalized["structured_data"]["items"], [])
        self.assertEqual(
            diagnostics["items_rejected_reason"],
            "card_sales_slip_without_ocr_item_candidates",
        )
        self.assertEqual(diagnostics["rejected_model_items"][0]["name"], "코카콜라 제로 2L")

    def test_keeps_items_when_card_sales_slip_has_ocr_item_candidates(self):
        normalized = _normalize(
            {
                "items": [{"name": "상품", "quantity": 1, "total_amount": 1000}],
                "item_extraction_diagnostics": {
                    "candidates": [{"name_candidate": "상품", "amount_candidate": 1000}],
                },
            },
            "receipt.jpg",
            "카드 매출표\n상품 1 1,000",
        )

        self.assertEqual(len(normalized["structured_data"]["items"]), 1)

    def test_does_not_recover_uniqlo_item_when_receipt_states_multiple_items(self):
        normalized = _normalize(
            {"total_amount": 60000, "items": []},
            "receipt.jpg",
            "유니클로(과세) 총품목/총수량 2/2 60,000",
        )

        self.assertEqual(normalized["structured_data"]["items"], [])

    def test_does_not_treat_nearby_single_quantity_as_item_count(self):
        ocr = "총품목/총수량 총구매금액\n브러쉬드 알파카 퍼루 1볼/50g"
        items = [{"name": "첫 품목"}, {"name": "두 번째 품목"}]
        normalized = _normalize(
            {"document_type": "PURCHASE_REQUEST", "items": items},
            "receipt.jpg",
            ocr,
        )

        self.assertIsNone(normalized["structured_data"]["deterministic_hints"]["stated_item_count"])
        self.assertEqual(normalized["structured_data"]["items"], items)

    def test_does_not_shorten_items_from_uncertain_single_llm_count(self):
        items = [{"name": "첫 품목"}, {"name": "두 번째 품목"}]
        normalized = _normalize(
            {"document_type": "PURCHASE_REQUEST", "receipt_summary": {"stated_item_count": 1}, "items": items},
            "receipt.jpg",
            "품목 정보",
        )

        self.assertEqual(normalized["structured_data"]["items"], items)


if __name__ == "__main__":
    unittest.main()
