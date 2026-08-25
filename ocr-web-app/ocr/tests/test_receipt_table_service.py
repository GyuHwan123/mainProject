import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.ocr import OCRItem  # noqa: E402
from app.services.receipt_table_service import detect_receipt_tables  # noqa: E402


def item(text, confidence, x1, y1, x2, y2):
    return OCRItem(text=text, confidence=confidence, bbox=[[x1, y1], [x2, y2]])


class ReceiptTableServiceTests(unittest.TestCase):
    def test_recovers_repeated_receipt_rows_without_changing_text(self):
        items = [
            item("볼펜", .96, 20, 100, 130, 120),
            item("2", .98, 210, 100, 225, 120),
            item("1,000", .97, 300, 100, 360, 120),
            item("노트", .95, 20, 130, 130, 150),
            item("1", .98, 210, 130, 225, 150),
            item("3,000", .96, 300, 130, 360, 150),
        ]

        tables = detect_receipt_tables(items)

        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].rows, [["볼펜", "2", "1,000"], ["노트", "1", "3,000"]])
        self.assertGreater(tables[0].confidence, .9)

    def test_ignores_single_key_value_line(self):
        items = [
            item("합계", .98, 20, 100, 80, 120),
            item("3,000", .97, 300, 100, 360, 120),
        ]

        self.assertEqual(detect_receipt_tables(items), [])

    def test_attaches_multiline_product_name_to_its_numeric_row(self):
        items = [
            item("[DIY] 브러쉬드 알파카 페루", .96, 20, 70, 250, 90),
            item("베텔린 스카프 (도안)", .95, 35, 94, 230, 114),
            item("1", .98, 210, 120, 225, 140), item("6,000", .97, 300, 120, 360, 140),
            item("브러쉬드 알파카 페루 1볼/50g", .96, 20, 150, 260, 170),
            item("6", .98, 210, 176, 225, 196), item("12,600", .97, 270, 176, 330, 196),
            item("75,600", .97, 350, 176, 410, 196),
        ]
        tables = detect_receipt_tables(items)
        self.assertEqual(len(tables), 1)
        self.assertIn("베텔린 스카프 (도안)", tables[0].rows[0][0])
        self.assertIn("브러쉬드 알파카 페루 1볼/50g", tables[0].rows[1][0])
        self.assertIn("12,600", tables[0].rows[1])
        self.assertIn("75,600", tables[0].rows[1])


if __name__ == "__main__":
    unittest.main()
