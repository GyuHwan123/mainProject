from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.constants.finance_taxonomy import (  # noqa: E402
    ALLOWED_DOCUMENT_TYPES,
    ALLOWED_EXPENSE_CATEGORIES,
    CATEGORY_TO_DOCUMENT_TYPE,
    normalize_expense_category,
    validate_classification,
)


class FinanceTaxonomyTests(unittest.TestCase):
    def test_canonical_taxonomy_is_complete(self):
        self.assertEqual(len(ALLOWED_EXPENSE_CATEGORIES), 15)
        self.assertEqual(set(CATEGORY_TO_DOCUMENT_TYPE), set(ALLOWED_EXPENSE_CATEGORIES))
        self.assertEqual(set(CATEGORY_TO_DOCUMENT_TYPE.values()), set(ALLOWED_DOCUMENT_TYPES))

    def test_accepts_only_safe_legacy_aliases(self):
        self.assertEqual(normalize_expense_category("사무용품"), "소모품비")
        self.assertIsNone(normalize_expense_category("기타"))
        self.assertIsNone(normalize_expense_category("식비"))

    def test_detects_category_document_type_mismatch(self):
        doc_type, category, needs_review, reason = validate_classification(
            "EXPENSE_REPORT", "출장숙박비", False
        )
        self.assertIsNone(doc_type)
        self.assertIsNone(category)
        self.assertTrue(needs_review)
        self.assertEqual(reason, "category_document_type_mismatch")

    def test_preserves_review_null_contract(self):
        self.assertEqual(
            validate_classification(None, None, True),
            (None, None, True, "model_requested_review"),
        )
