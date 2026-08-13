import unittest
from pathlib import Path

from openpyxl import Workbook

from app.services.spreadsheet_service import extract_spreadsheet


class SpreadsheetServiceTests(unittest.TestCase):
    def test_extracts_sheets_values_formulas_and_cell_coordinates(self):
        file_path = Path(__file__).parent / "sample.xlsx"
        workbook = Workbook()
        first = workbook.active
        first.title = "매출"
        first.append(["상품", "수량", "합계"])
        first.append(["노트북", 2, "=B2*100"])
        second = workbook.create_sheet("메모")
        second["B2"] = "확인"
        workbook.save(file_path)
        workbook.close()

        try:
            pages = extract_spreadsheet(file_path)
        finally:
            file_path.unlink(missing_ok=True)

        self.assertEqual([page.sheet_name for page in pages], ["매출", "메모"])
        self.assertEqual(pages[0].rows[1], ["노트북", "2", "=B2*100"])
        formula_item = next(item for item in pages[0].items if item.cell == "C2")
        self.assertEqual(formula_item.text, "=B2*100")
        self.assertEqual((formula_item.row, formula_item.column), (2, 3))
        self.assertEqual(pages[1].rows[1], ["", "확인"])


if __name__ == "__main__":
    unittest.main()
