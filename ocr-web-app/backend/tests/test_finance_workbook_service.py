from io import BytesIO
import unittest
from openpyxl import load_workbook
from app.services.finance_workbook_service import build_finance_workbook, SHEET_NAMES, _receipt_summary_rows


class FinanceWorkbookServiceTests(unittest.TestCase):
    def record(self, kind='PURCHASE_REQUEST'):
        return {'document_id': 'sample', 'document_type': kind, 'merchant': '상점',
                'transaction_date': '2026-09-07', 'supply_amount': 10000, 'tax_amount': 1000,
                'total_amount': 11000, 'structured_data': {'source_filename': 'receipt.png', 'items': [
                    {'name': 'A', 'quantity': 1, 'unit_price': 5500, 'total_amount': 5500},
                    {'name': 'B', 'quantity': 1, 'unit_price': 5500, 'total_amount': 5500}]}}

    def test_four_layouts_and_receipt_tax_preservation(self):
        for kind, name in SHEET_NAMES.items():
            with self.subTest(kind=kind):
                wb = load_workbook(BytesIO(build_finance_workbook([self.record(kind)])))
                self.assertEqual(len(wb.sheetnames), 5)
                ws = wb[name]
                headers = [c.value for c in ws[11]]
                self.assertNotIn('공급가액', headers)
                self.assertNotIn('부가세', headers)
                for label, value in [('품목명', 'A'), ('수량', 1), ('단가', 5500), ('품목금액', 5500)]:
                    self.assertEqual(ws.cell(12, headers.index(label)+1).value, value)
                self.assertEqual(ws['B13'].value, 2)
                summary = wb['영수증요약']
                self.assertEqual([summary.cell(2, c).value for c in (8, 9, 11, 13)], [10000, 1000, 11000, 11000])
                self.assertEqual(summary['N2'].value, '=K2-M2')
                wb.close()

    def test_finance_status_dropdown_survives_excel_export(self):
        wb = load_workbook(BytesIO(build_finance_workbook([self.record()])))
        for name in SHEET_NAMES.values():
            with self.subTest(sheet=name):
                ws = wb[name]
                self.assertEqual(ws['F5'].value, '재무팀 확정 대기중')
                validation, = list(ws.data_validations.dataValidation)
                self.assertEqual(validation.type, 'list')
                self.assertEqual(validation.formula1, '"재무팀 확정 대기중,재무팀 확정"')
                self.assertIn('F5', validation.sqref)
                self.assertFalse(validation.showDropDown)
                self.assertTrue(validation.showErrorMessage)
        wb.close()

    def test_missing_values_and_zero(self):
        r = self.record()
        r['structured_data']['items'] = [{'name': 'unknown'}, {'name': 'free', 'quantity': 0, 'unit_price': 0, 'total_amount': 0}]
        wb = load_workbook(BytesIO(build_finance_workbook([r])))
        ws = wb['구매품의요청서']
        self.assertEqual([ws.cell(12, c).value for c in (6, 7, 8)], [None]*3)
        self.assertEqual([ws.cell(13, c).value for c in (6, 7, 8)], [0]*3)
        self.assertIsNone(wb['영수증요약']['M2'].value)
        wb.close()

    def test_no_items_no_fabricated_receipt_total_in_detail(self):
        r = self.record()
        r['structured_data']['items'] = []
        wb = load_workbook(BytesIO(build_finance_workbook([r])))
        self.assertIsNone(wb['구매품의요청서']['H12'].value)
        self.assertEqual(wb['영수증요약']['K2'].value, 11000)
        self.assertIn('품목 없음', wb['영수증요약']['O2'].value)
        wb.close()

    def test_discount_and_unexplained_difference(self):
        r = self.record()
        r['total_amount'] = 10000
        r['structured_data']['discount_amount'] = 1000
        self.assertEqual(_receipt_summary_rows([r])[0][-1], '할인액과 차이 일치')
        r['structured_data']['discount_amount'] = 500
        self.assertEqual(_receipt_summary_rows([r])[0][-1], '확인 필요: 금액 차이')

    def test_legacy_summary_does_not_override_confirmed_total(self):
        r = self.record()
        r['total_amount'] = 0
        r['structured_data']['receipt_summary'] = {'stated_total_amount': 11000}
        row = _receipt_summary_rows([r])[0]
        self.assertEqual(row[10], 0)
        self.assertEqual(row[-1], '확인 필요: 금액 차이')

    def test_untrusted_text_is_not_exported_as_a_formula(self):
        r = self.record()
        r['merchant'] = '=HYPERLINK("https://example.invalid")'
        r['structured_data']['items'][0]['name'] = '=SUM(A1:A2)'
        wb = load_workbook(BytesIO(build_finance_workbook([r])), data_only=False)
        ws = wb[SHEET_NAMES['PURCHASE_REQUEST']]
        name_cell = ws.cell(12, 5)
        merchant_cell = ws.cell(12, 4)
        self.assertEqual(name_cell.value, "'=SUM(A1:A2)")
        self.assertEqual(merchant_cell.value, "'=HYPERLINK(\"https://example.invalid\")")
        self.assertEqual(name_cell.data_type, 's')
        wb.close()
