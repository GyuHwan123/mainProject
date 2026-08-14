import sys
import types
import unittest

sys.modules.setdefault("cv2", types.ModuleType("cv2"))

from app.services.preprocess_service import scale_bbox_to_image


class ScaleBboxTests(unittest.TestCase):
    def test_maps_upscaled_ocr_bbox_back_to_original_image(self):
        self.assertEqual(
            scale_bbox_to_image(
                [[20, 40], [100, 40], [100, 80], [20, 80]],
                (200, 400, 3),
                (100, 200, 3),
            ),
            [[10, 20], [50, 20], [50, 40], [10, 40]],
        )

    def test_clips_coordinates_to_original_image(self):
        self.assertEqual(
            scale_bbox_to_image([[-10, 5], [500, 250]], (200, 400, 3), (100, 200, 3)),
            [[0, 2], [200, 100]],
        )


if __name__ == "__main__":
    unittest.main()
