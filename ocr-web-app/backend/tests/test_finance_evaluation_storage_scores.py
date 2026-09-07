import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.supabase_document_finance_repository import (
    _extraction_score,
    _legacy_extraction_score,
)


class FinanceEvaluationStorageScoreTests(unittest.TestCase):
    def test_current_scores_fit_legacy_column_without_losing_original(self):
        for score in (0, 94, 95, 99, 100):
            with self.subTest(score=score):
                rubric = {"max_extraction_score": 100, "extraction_score": score}
                stored = _legacy_extraction_score(rubric)
                self.assertLessEqual(stored, 95)
                self.assertAlmostEqual(stored, score * 0.95)
                self.assertEqual(_extraction_score({
                    "selection_rubric": rubric, "extraction_score_95": stored,
                }), score)

    def test_legacy_and_missing_scores(self):
        self.assertEqual(_legacy_extraction_score({"extraction_score": 95}), 95)
        self.assertIsNone(_legacy_extraction_score({}))
        self.assertEqual(_extraction_score({"extraction_score_95": 80}), 80)
        self.assertIsNone(_extraction_score({}))

    def test_mixed_rows_include_perfect_and_zero_scores(self):
        rows = [
            {"selection_rubric": {"extraction_score": 100}, "extraction_score_95": 95},
            {"selection_rubric": {"extraction_score": 0}, "extraction_score_95": 0},
            {"extraction_score_95": 80},
        ]
        scores = [_extraction_score(row) for row in rows]
        self.assertEqual(sum(scores) / len(scores), 60)


if __name__ == "__main__":
    unittest.main()
