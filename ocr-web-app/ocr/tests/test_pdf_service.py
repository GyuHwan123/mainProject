import sys
import types
import unittest

fitz = types.ModuleType("fitz")
fitz.open = lambda _path: None
fitz.Pixmap = object
sys.modules.setdefault("fitz", fitz)
# test_docx_service installs a lightweight module stub during discovery.
sys.modules.pop("app.services.pdf_service", None)

from app.schemas.ocr import OCRItem, OCRPage  # noqa: E402
from app.services.pdf_service import (  # noqa: E402
    _rect_bbox,
    _remove_repeated_headers_and_footers,
    _sort_document_items,
)


class RectBboxTests(unittest.TestCase):
    def test_rounds_outward_to_preserve_glyph_edges(self):
        self.assertEqual(
            _rect_bbox((10.8, 20.2, 30.1, 40.9), 100, 100),
            [[10, 20], [31, 20], [31, 41], [10, 41]],
        )

    def test_clips_bbox_to_page(self):
        self.assertEqual(
            _rect_bbox((-4.2, -1.0, 105.7, 90.2), 100, 80),
            [[0, 0], [100, 0], [100, 80], [0, 80]],
        )

    def test_rejects_empty_or_invalid_bbox(self):
        self.assertIsNone(_rect_bbox((4, 4, 4, 9), 100, 100))
        self.assertIsNone(_rect_bbox((0, 0, float("nan"), 9), 100, 100))


class DocumentLayoutTests(unittest.TestCase):
    @staticmethod
    def item(text, x, y):
        return OCRItem(text=text, confidence=1.0, bbox=[[x, y], [x + 30, y], [x + 30, y + 10], [x, y + 10]])

    def test_removes_only_repeated_margin_text(self):
        pages = [
            OCRPage(page=index + 1, text="", items=[self.item("Company confidential", 10, 2), self.item(f"body {index}", 10, 50)])
            for index in range(3)
        ]
        _remove_repeated_headers_and_footers(pages, [100, 100, 100])
        self.assertEqual([[item.text for item in page.items] for page in pages], [["body 0"], ["body 1"], ["body 2"]])

    def test_orders_two_columns_without_changing_receipt_parser(self):
        items = []
        for y in (10, 30, 50, 70, 90):
            items.extend([self.item(f"L{y}", 10, y), self.item(f"R{y}", 200, y)])
        _sort_document_items(items)
        self.assertEqual([item.text for item in items], ["L10", "L30", "L50", "L70", "L90", "R10", "R30", "R50", "R70", "R90"])


if __name__ == "__main__":
    unittest.main()
