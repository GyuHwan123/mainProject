import sys
import types
import unittest

try:
    import cv2
    CV2_AVAILABLE = hasattr(cv2, "line")
except ModuleNotFoundError:
    cv2 = types.ModuleType("cv2")
    sys.modules["cv2"] = cv2
    CV2_AVAILABLE = False
import numpy as np

from app.schemas.ocr import OCRItem, OCRPage
from app.services.document_table_service import enhance_document_tables


def item(text, x1, y1, x2, y2):
    return OCRItem(text=text, confidence=.98, bbox=[[x1, y1], [x2, y2]])


class DocumentTableServiceTests(unittest.TestCase):
    @unittest.skipUnless(CV2_AVAILABLE, "OpenCV is installed in the OCR Docker image")
    def test_detects_ruled_table_and_assigns_cells(self):
        image = np.full((160, 240, 3), 255, dtype=np.uint8)
        for x in (10, 110, 230):
            cv2.line(image, (x, 10), (x, 150), (0, 0, 0), 2)
        for y in (10, 55, 100, 150):
            cv2.line(image, (10, y), (230, y), (0, 0, 0), 2)
        page = OCRPage(page=1, text="", items=[
            item("부서", 25, 20, 60, 40), item("인원", 135, 20, 170, 40),
            item("개발", 25, 65, 60, 85), item("10", 135, 65, 160, 85),
            item("영업", 25, 110, 60, 130), item("20", 135, 110, 160, 130),
        ])
        enhance_document_tables(page, image)
        self.assertEqual(page.tables[0].rows, [["부서", "인원"], ["개발", "10"], ["영업", "20"]])
        self.assertEqual(page.items[-1].cell, "R3C2")
        self.assertGreater(page.tables[0].confidence, .8)

    def test_detects_repeated_columns_without_lines(self):
        page = OCRPage(page=1, text="", items=[
            item("부서", 10, 10, 50, 25), item("인원", 180, 10, 220, 25),
            item("개발", 10, 40, 50, 55), item("10", 180, 40, 205, 55),
            item("영업", 10, 70, 50, 85), item("20", 180, 70, 205, 85),
        ])
        enhance_document_tables(page)
        self.assertEqual(page.tables[0].rows[1], ["개발", "10"])
        self.assertEqual(page.tables[0].confidence, .65)

    def test_ignores_sparse_callouts_around_repeated_columns(self):
        page = OCRPage(page=1, text="", items=[
            item("평균", 100, 20, 140, 35), item("표준편차", 210, 20, 270, 35), item("평균", 330, 20, 370, 35),
            item("실험1", 10, 50, 55, 65), item("77.40", 100, 50, 145, 65), item("8.55", 210, 50, 245, 65), item("92.19", 330, 50, 375, 65),
            item("실험2", 10, 80, 55, 95), item("77.40", 100, 80, 145, 95), item("8.55", 210, 80, 245, 95), item("92.19", 330, 80, 375, 95),
            item("합계", 10, 110, 45, 125), item("77.40", 100, 110, 145, 125), item("8.55", 210, 110, 245, 125), item("92.19", 330, 110, 375, 125),
            item("테두리 굵기", 500, 45, 590, 60), item("색상 안내", 620, 140, 690, 155),
        ])
        enhance_document_tables(page)
        self.assertIsNotNone(page.tables)
        self.assertIn("77.40", page.tables[0].rows[1])
        self.assertNotIn("테두리 굵기", str(page.tables[0].rows))

    def test_splits_distant_repeated_callout_column(self):
        page = OCRPage(page=1, text="", items=[])
        for row_index, y in enumerate((20, 50, 80, 110), start=1):
            page.items.extend([
                item(f"행{row_index}", 10, y, 50, y + 15),
                item("77.40", 140, y, 190, y + 15),
                item("8.55", 270, y, 310, y + 15),
                item("92.19", 400, y, 450, y + 15),
                item(f"설명{row_index}", 700, y, 780, y + 15),
            ])
        enhance_document_tables(page)
        self.assertEqual(len(page.tables[0].rows[0]), 4)
        self.assertNotIn("설명", str(page.tables[0].rows))

    @unittest.skipUnless(CV2_AVAILABLE, "OpenCV is installed in the OCR Docker image")
    def test_keeps_headers_above_first_horizontal_rule(self):
        image = np.full((220, 520, 3), 255, dtype=np.uint8)
        for y in (70, 105, 140, 175):
            cv2.line(image, (20, y), (400, y), (0, 0, 0), 2)
        page = OCRPage(page=1, text="", items=[
            item("평균", 130, 42, 175, 58),
            item("표준편차", 230, 42, 300, 58),
            item("평균", 340, 42, 385, 58),
            item("실험1", 25, 78, 75, 96), item("77.40", 130, 78, 180, 96),
            item("8.55", 240, 78, 280, 96), item("92.19", 340, 78, 390, 96),
            item("실험2", 25, 113, 75, 131), item("77.40", 130, 113, 180, 131),
            item("8.55", 240, 113, 280, 131), item("92.19", 340, 113, 390, 131),
            item("합계", 25, 148, 65, 166), item("77.40", 130, 148, 180, 166),
            item("8.55", 240, 148, 280, 166), item("92.19", 340, 148, 390, 166),
            item("표1. 연구수행 결과표", 100, 8, 280, 28),
        ])

        enhance_document_tables(page, image)

        self.assertEqual(page.tables[0].columns, ["", "평균", "표준편차", "평균"])
        self.assertEqual(page.tables[0].row_count, 4)
        self.assertNotIn("연구수행 결과표", str(page.tables[0].rows))

    def test_keeps_paragraph_like_ocr_as_plain_text(self):
        page = OCRPage(page=1, text="", items=[
            item("첫 번째 문장입니다", 10, 10, 150, 25),
            item("두 번째 문장입니다", 15, 40, 170, 55),
            item("마지막 문장입니다", 8, 70, 160, 85),
            item("본문", 10, 100, 40, 115),
        ])
        enhance_document_tables(page)
        self.assertIsNone(page.tables)


if __name__ == "__main__":
    unittest.main()
