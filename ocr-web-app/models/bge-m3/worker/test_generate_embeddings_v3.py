"""Validation tests only: no model loading or embedding generation."""
from copy import deepcopy
import unittest

import numpy as np
import generate_embeddings_v3 as worker


class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks, _ = worker.load_chunks()

    def test_frozen_source_and_sample_row_mapping(self):
        result = worker.validate_chunks(self.chunks)
        self.assertEqual(result["chunk_fingerprint_sha256"], worker.EXPECTED_FINGERPRINT)
        rows = worker.row_map(self.chunks)
        self.assertEqual((rows[3]["doc_id"], rows[3]["chunk_index"], rows[3]["article_title"]),
                         ("HR-001", 3, "제6조 (근로시간)"))
        self.assertEqual((rows[38]["doc_id"], rows[38]["chunk_index"], rows[38]["table_id"]),
                         ("GA-001", 2, "GA-001:p1:t1"))

    def test_missing_empty_and_duplicate_inputs_rejected(self):
        for mutation in [lambda c: c[0].pop("text"), lambda c: c[0].update(text=" "),
                         lambda c: c.__setitem__(1, c[0]), lambda c: c.pop()]:
            chunks = deepcopy(self.chunks)
            mutation(chunks)
            with self.assertRaises(ValueError):
                worker.validate_chunks(chunks)

    def test_invalid_shape_dtype_finite_and_norm_rejected(self):
        valid = np.zeros((115, 1024), dtype=np.float32)
        valid[:, 0] = 1
        self.assertEqual(worker.validate_vectors(valid)["norm_mean"], 1)
        bad_nan, bad_inf = valid.copy(), valid.copy()
        bad_nan[0, 0], bad_inf[0, 0] = np.nan, np.inf
        for value in [valid[:37], valid.astype(np.float64), bad_nan, bad_inf, valid * .5]:
            with self.assertRaises(ValueError):
                worker.validate_vectors(value)

    def test_input_order_affects_fingerprint(self):
        shuffled = list(self.chunks)
        shuffled[0], shuffled[1] = shuffled[1], shuffled[0]
        self.assertNotEqual(worker.chunk_fingerprint(shuffled), worker.EXPECTED_FINGERPRINT)


if __name__ == "__main__":
    unittest.main()
