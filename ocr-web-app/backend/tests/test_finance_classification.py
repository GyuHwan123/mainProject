from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes.finance import (  # noqa: E402
    _classify_receipt_with_model,
    _normalize,
    _receipt_hints,
    _receipt_item_candidates,
    _receipt_items_prompt,
    _receipt_prompt,
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
        self.assertIn('"raw_cells": ["볼펜", "2", "1,000"]', items_prompt)

    def test_builds_item_candidates_from_table_and_single_box_ocr_lines(self):
        candidates = _receipt_item_candidates([{
            "page": 1,
            "tables": [{"rows": [["볼펜", "2", "1,000", "2,000"], ["합계", "2,000"]]}],
            "text": "노트 1 3,000 3,000\n부가세 455",
        }])

        self.assertEqual([item["name_candidate"] for item in candidates], ["볼펜", "노트"])
        self.assertEqual(candidates[0]["quantity_candidate"], 2)
        self.assertEqual(candidates[0]["unit_price_candidate"], 1000)
        self.assertEqual(candidates[0]["amount_candidate"], 2000)

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
        self.assertEqual(hints["expense_category"], "일비/식대")
        self.assertEqual(hints["transaction_date"], "2025-10-05")
        self.assertEqual(hints["supply_amount"], 28545)
        self.assertEqual(hints["tax_amount"], 2855)
        self.assertEqual(hints["total_amount"], 31400)

    def test_recovers_date_when_ocr_joins_date_and_time(self):
        hints = _receipt_hints("판매일자 2025-10-0516:50", "서울출장_식비.jpg")
        self.assertEqual(hints["transaction_date"], "2025-10-05")

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
            {"doc_type": "TRAVEL_EXPENSE", "expense_category": "교통비", "total_amount": 96200},
            "receipt_005.jpg",
            "결제금액 96,200원",
        )

        self.assertEqual(normalized["document_type"], "TRAVEL_EXPENSE")
        self.assertEqual(normalized["structured_data"]["doc_type"], "TRAVEL_EXPENSE")

    def test_keeps_legacy_document_type_compatible(self):
        normalized = _normalize(
            {"document_type": "WELFARE_BENEFIT", "total_amount": 10000},
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
