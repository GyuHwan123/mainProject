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
        self.assertEqual(len(ALLOWED_EXPENSE_CATEGORIES), 13)
        self.assertEqual(set(CATEGORY_TO_DOCUMENT_TYPE), set(ALLOWED_EXPENSE_CATEGORIES))
        self.assertTrue(set(CATEGORY_TO_DOCUMENT_TYPE.values()).issubset(ALLOWED_DOCUMENT_TYPES))

    def test_transport_category_selects_travel_expense(self):
        self.assertEqual(CATEGORY_TO_DOCUMENT_TYPE["교통"], "TRAVEL_EXPENSE")
        self.assertEqual(
            validate_classification(None, "교통", False),
            (
                "TRAVEL_EXPENSE",
                "교통",
                False,
                "document_type_derived_from_category",
            ),
        )

    def test_accepts_only_safe_legacy_aliases(self):
        self.assertEqual(normalize_expense_category("사무용품"), "전자제품/문구")
        self.assertIsNone(normalize_expense_category("기타"))
        self.assertEqual(normalize_expense_category("식비"), "식비")
        self.assertEqual(normalize_expense_category("식비/생활"), "식비")
        self.assertEqual(normalize_expense_category("생활/식비"), "식비")
        self.assertEqual(normalize_expense_category("식비/쇼핑"), "식비")

    def test_marks_model_document_type_conflict_for_review(self):
        doc_type, category, needs_review, reason = validate_classification(
            "TRAVEL_EXPENSE", "식비", False
        )
        self.assertEqual(doc_type, "WELFARE_BENEFIT")
        self.assertEqual(category, "식비")
        self.assertTrue(needs_review)
        self.assertEqual(reason, "category_document_type_conflict")

    def test_strong_business_context_selects_document_but_keeps_conflict_visible(self):
        self.assertEqual(
            validate_classification(
                "EXPENSE_REPORT",
                "식비",
                False,
                deterministic_doc_type="TRAVEL_EXPENSE",
                deterministic_source="FILENAME_BUSINESS_CONTEXT",
            ),
            (
                "TRAVEL_EXPENSE",
                "식비",
                True,
                "category_document_type_conflict",
            ),
        )

    def test_user_review_can_explicitly_keep_cross_taxonomy_document_type(self):
        self.assertEqual(
            validate_classification(
                "TRAVEL_EXPENSE",
                "식비",
                allow_explicit_document_type=True,
            ),
            ("TRAVEL_EXPENSE", "식비", False, None),
        )

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
