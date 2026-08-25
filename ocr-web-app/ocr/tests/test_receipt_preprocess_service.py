import unittest

import numpy as np

from app.services.receipt_preprocess_service import PreprocessOptions, ReceiptPreprocessResult


class ReceiptBboxTransformTests(unittest.TestCase):
    def test_enables_perspective_correction_for_receipts_by_default(self):
        self.assertTrue(PreprocessOptions().perspective_correction)

    def test_maps_composed_crop_and_scale_back_to_original(self):
        # original -> crop x-10/y-20 -> 2x scale
        transform = np.asarray([[2, 0, -20], [0, 2, -40], [0, 0, 1]], dtype=float)
        result = ReceiptPreprocessResult(
            image=np.zeros((100, 100, 3), dtype=np.uint8),
            original_shape=(200, 200, 3),
            forward_transform=transform,
            applied_steps=["crop", "upscale"],
        )
        self.assertEqual(result.bbox_to_original([[20, 20], [60, 80]]), [[20, 30], [40, 60]])

    def test_clips_inverse_bbox_to_original_bounds(self):
        result = ReceiptPreprocessResult(
            image=np.zeros((100, 100, 3), dtype=np.uint8),
            original_shape=(80, 90, 3),
            forward_transform=np.eye(3),
            applied_steps=[],
        )
        self.assertEqual(result.bbox_to_original([[-10, -5], [120, 100]]), [[0, 0], [90, 80]])


if __name__ == "__main__":
    unittest.main()
