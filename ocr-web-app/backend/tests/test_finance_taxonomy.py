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
        self.assertEqual(len(ALLOWED_EXPENSE_CATEGORIES), 16)
        self.assertEqual(set(CATEGORY_TO_DOCUMENT_TYPE), set(ALLOWED_EXPENSE_CATEGORIES))
        self.assertTrue(set(CATEGORY_TO_DOCUMENT_TYPE.values()).issubset(ALLOWED_DOCUMENT_TYPES))

    def test_accepts_only_safe_legacy_aliases(self):
        self.assertEqual(normalize_expense_category("사무용품"), "전자제품/문구")
        self.assertIsNone(normalize_expense_category("기타"))
        self.assertEqual(normalize_expense_category("식비"), "식비")

    def test_keeps_valid_document_type_independent_from_receipt_category(self):
        doc_type, category, needs_review, reason = validate_classification(
            "TRAVEL_EXPENSE", "식비", False
        )
        self.assertEqual(doc_type, "TRAVEL_EXPENSE")
        self.assertEqual(category, "식비")
        self.assertFalse(needs_review)
        self.assertIsNone(reason)

    def test_preserves_review_null_contract(self):
        self.assertEqual(
            validate_classification(None, None, True),
            (None, None, True, "model_requested_review"),
        )

    def test_every_verified_receipt_category_survives_validation(self):
        for expected in ALLOWED_EXPENSE_CATEGORIES:
            with self.subTest(category=expected):
                doc_type, category, needs_review, _ = validate_classification(
                    None, expected, False,
                )
                self.assertEqual(category, expected)
                self.assertIn(doc_type, ALLOWED_DOCUMENT_TYPES)
                self.assertFalse(needs_review)
