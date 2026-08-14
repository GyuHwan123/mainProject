import sys
import types
import unittest

fitz = types.ModuleType("fitz")
fitz.open = lambda _path: None
fitz.Pixmap = object
sys.modules.setdefault("fitz", fitz)
# test_docx_service installs a lightweight module stub during discovery.
sys.modules.pop("app.services.pdf_service", None)

from app.services.pdf_service import _rect_bbox  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
