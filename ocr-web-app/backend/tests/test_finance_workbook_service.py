from io import BytesIO
from pathlib import Path
import sys
import unittest

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.finance_workbook_service import build_finance_workbook  # noqa: E402


class FinanceWorkbookServiceTests(unittest.TestCase):
    def test_builds_four_standard_sheets_and_expense_totals(self):
        content = build_finance_workbook([
            {
                "document_type": "EXPENSE_REPORT",
                "expense_category": "회의비",
                "merchant": "테스트카페",
                "transaction_date": "2026-08-18",
                "supply_amount": 10000,
                "tax_amount": 1000,
                "total_amount": 11000,
                "payment_method": "법인카드",
                "description": "고객 미팅",
                "structured_data": {},
                "created_at": "2026-08-18T10:00:00+09:00",
            }
        ], author={"name": "테스트 사용자", "email": "tester@example.com"})
        workbook = load_workbook(BytesIO(content), data_only=False)
        self.assertEqual(
            workbook.sheetnames,
            ["경비지출결의서", "출장여비교통비정산서", "구매품의요청서", "복리후생비신청서"],
        )
        sheet = workbook["경비지출결의서"]
        self.assertEqual(sheet["A5"].value, "문서번호")
        self.assertTrue(sheet["B5"].value.startswith("EXP-"))
        self.assertEqual(sheet["B7"].value, "테스트 사용자")
        self.assertEqual(sheet["F7"].value, "tester@example.com")
        self.assertEqual(sheet["F3"].value, "테스트 사용자")
        self.assertEqual([sheet.cell(11, column).value for column in range(1, 9)], [
            "No", "결제일시", "상호명(가맹점)", "지출용도(적요)", "공급가액", "부가세", "합계금액", "증빙유형",
        ])
        self.assertEqual(sheet["C12"].value, "테스트카페")
        self.assertEqual(sheet["G12"].value, 11000)
        self.assertEqual(sheet["G13"].value, "=SUM(G12:G12)")

    def test_places_confirmed_travel_meal_in_travel_sheet_columns(self):
        content = build_finance_workbook([{
            "document_type": "TRAVEL_EXPENSE",
            "expense_category": "일비/식대",
            "merchant": "카페마마스 광화문점",
            "transaction_date": "2025-10-05",
            "total_amount": 31400,
            "description": "서울 출장 식비",
            "structured_data": {"location": "서울 종로구", "evidence_status": "첨부"},
            "created_at": "2025-10-05T16:50:00+09:00",
        }])
        workbook = load_workbook(BytesIO(content), data_only=False)
        sheet = workbook["출장여비교통비정산서"]
        self.assertEqual(workbook.active.title, "출장여비교통비정산서")
        self.assertEqual(sheet["A12"].value, "일비/식대")
        self.assertEqual(sheet["B12"].value, "2025-10-05")
        self.assertEqual(sheet["C12"].value, "서울 종로구")
        self.assertEqual(sheet["E12"].value, "카페마마스 광화문점")
        self.assertEqual(sheet["F12"].value, 31400)
        self.assertEqual(sheet["H12"].value, "서울 출장 식비")

    def test_writes_every_purchase_item_to_its_own_row(self):
        content = build_finance_workbook([{
            "document_type": "PURCHASE_REQUEST",
            "expense_category": "사무용품",
            "merchant": "한국문구",
            "transaction_date": "2026-08-20",
            "total_amount": 8800,
            "structured_data": {"items": [
                {"name": "볼펜", "specification": "검정 0.5mm", "quantity": 2, "unit": "자루", "unit_price": 1100, "supply_amount": 2000, "tax_amount": 200, "total_amount": 2200},
                {"name": "노트", "specification": "A5 80매", "quantity": 3, "unit": "권", "unit_price": 2200, "supply_amount": 6000, "tax_amount": 600, "total_amount": 6600},
            ]},
        }])
        workbook = load_workbook(BytesIO(content), data_only=False)
        sheet = workbook["구매품의요청서"]

        self.assertEqual([sheet.cell(11, column).value for column in range(1, 11)], [
            "No", "품목명", "규격/옵션", "수량", "단위", "단가", "공급가액", "부가세", "합계금액", "비고",
        ])
        self.assertEqual([sheet["B12"].value, sheet["C12"].value, sheet["D12"].value, sheet["E12"].value], ["볼펜", "검정 0.5mm", 2, "자루"])
        self.assertEqual([sheet["B13"].value, sheet["C13"].value, sheet["D13"].value, sheet["E13"].value], ["노트", "A5 80매", 3, "권"])
        self.assertEqual(sheet["G14"].value, "=SUM(G12:G13)")
        self.assertEqual(sheet["H14"].value, "=SUM(H12:H13)")
        self.assertEqual(sheet["I14"].value, "=SUM(I12:I13)")
        self.assertEqual(sheet["B8"].value, 8800)


if __name__ == "__main__":
    unittest.main()
