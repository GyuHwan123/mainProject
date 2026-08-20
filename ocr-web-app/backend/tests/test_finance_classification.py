from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes.finance import _normalize, _receipt_hints  # noqa: E402


SAMPLE_OCR = """주문번호 5,125.00
매장명 전화번호 02-736-1260 광화문점
서울특별시 종로구 종로1길5
판매일자 대표자 사업자NO O.A동 1층(중학동) 2025-10-05 16:50 주혜윤 105-87-46831
[메뉴] [수량] [금액]
케일청포도 주스 케일바나나주스 리코타 샐러드 1 1 1 15.800 7,800 7.800
결제금액 공급가액 부가세액 31.400 28.545 2,855
[거래 종류] 카드결제 31,400원
"""


class FinanceClassificationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
