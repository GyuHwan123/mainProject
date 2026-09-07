import unittest

from app.services.postprocess_service import normalize_ocr_text


class NormalizeOcrTextTests(unittest.TestCase):
    def test_collapses_duplicate_spaces_and_blank_lines(self):
        self.assertEqual(
            normalize_ocr_text("  첫째   줄 \r\n\r\n\t둘째  줄  "),
            "첫째 줄\n둘째 줄",
        )


if __name__ == "__main__":
    unittest.main()
