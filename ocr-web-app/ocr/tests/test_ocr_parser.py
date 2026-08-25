import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.ocr.ocr_parser import sort_text_lines  # noqa: E402


class OCRParserReceiptLayoutTests(unittest.TestCase):
    def test_receipt_layout_does_not_read_columns_separately(self):
        texts = ["상품A", "1,000", "상품B", "2,000", "안내1", "값1", "안내2", "값2", "안내3", "값3"]
        scores = [.99] * len(texts)
        boxes = [
            [10, 10, 80, 30], [300, 10, 360, 30], [10, 50, 80, 70], [300, 50, 360, 70],
            [10, 90, 80, 110], [300, 90, 360, 110], [10, 130, 80, 150], [300, 130, 360, 150],
            [10, 170, 80, 190], [300, 170, 360, 190],
        ]

        lines = sort_text_lines(texts, scores, boxes, receipt_layout=True)

        flattened = [entry["text"] for line in lines for entry in line]
        self.assertLess(flattened.index("1,000"), flattened.index("상품B"))
        self.assertLess(flattened.index("2,000"), flattened.index("안내1"))


if __name__ == "__main__":
    unittest.main()
