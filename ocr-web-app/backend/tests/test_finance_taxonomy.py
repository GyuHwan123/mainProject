from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.constants.finance_taxonomy import (  # noqa: E402
    ALLOWED_DOCUMENT_TYPES,
    ALLOWED_EXPENSE_CATEGORIES,
    CATEGORY_CLASSIFICATION_POLICIES,
    CATEGORY_TO_DOCUMENT_TYPE,
    normalize_expense_category,
    refine_expense_category,
    validate_classification,
)


class FinanceTaxonomyTests(unittest.TestCase):
    def test_canonical_taxonomy_is_complete(self):
        self.assertEqual(len(ALLOWED_EXPENSE_CATEGORIES), 14)
        self.assertEqual(set(CATEGORY_TO_DOCUMENT_TYPE), set(ALLOWED_EXPENSE_CATEGORIES))
        self.assertEqual(set(CATEGORY_CLASSIFICATION_POLICIES), set(ALLOWED_EXPENSE_CATEGORIES))
        self.assertTrue(set(CATEGORY_TO_DOCUMENT_TYPE.values()).issubset(ALLOWED_DOCUMENT_TYPES))

    def test_transport_category_selects_travel_expense(self):
        self.assertEqual(CATEGORY_TO_DOCUMENT_TYPE["대중교통"], "TRAVEL_EXPENSE")
        self.assertEqual(
            validate_classification(None, "교통", False),
            (
                "TRAVEL_EXPENSE",
                "대중교통",
                False,
                "document_type_derived_from_category",
            ),
        )

    def test_accepts_only_safe_legacy_aliases(self):
        self.assertEqual(normalize_expense_category("사무용품"), "전자제품/문구")
        self.assertIsNone(normalize_expense_category("기타"))
        self.assertEqual(normalize_expense_category("식비"), "외식/식사")
        self.assertEqual(normalize_expense_category("식비/생활"), "식품/장보기")
        self.assertEqual(normalize_expense_category("생활/식비"), "식품/장보기")
        self.assertEqual(normalize_expense_category("식비/쇼핑"), "식품/장보기")
        self.assertEqual(normalize_expense_category("식비/주류"), "식품/장보기")
        self.assertEqual(normalize_expense_category("주유/교통"), "주유/차량")
        self.assertEqual(normalize_expense_category("미용/생활"), "미용/뷰티")
        self.assertEqual(normalize_expense_category("전자제품"), "전자제품/문구")

    def test_refines_legacy_parent_categories_from_receipt_evidence(self):
        self.assertEqual(refine_expense_category("식비", "투썸플레이스 아이스 아메리카노"), "카페/음료")
        self.assertEqual(refine_expense_category("식비", "GS25 편의점 과자 생수"), "식품/장보기")
        self.assertEqual(refine_expense_category("식비", "식당 떡볶이 주문"), "외식/식사")
        self.assertEqual(refine_expense_category("취미/쇼핑", "유니클로 가디건"), "의류/패션")
        self.assertEqual(refine_expense_category("취미/쇼핑", "물티슈 생활잡화"), "생활용품")
        self.assertEqual(refine_expense_category("교통", "보통휘발유 20.9L 주유소"), "주유/차량")

    def test_marks_model_document_type_conflict_for_review(self):
        doc_type, category, needs_review, reason = validate_classification(
            "TRAVEL_EXPENSE", "외식/식사", False
        )
        self.assertEqual(doc_type, "WELFARE_BENEFIT")
        self.assertEqual(category, "외식/식사")
        self.assertTrue(needs_review)
        self.assertEqual(reason, "category_document_type_conflict")

    def test_strong_business_context_selects_document_but_keeps_conflict_visible(self):
        self.assertEqual(
            validate_classification(
                "EXPENSE_REPORT",
                "외식/식사",
                False,
                deterministic_doc_type="TRAVEL_EXPENSE",
                deterministic_source="FILENAME_BUSINESS_CONTEXT",
            ),
            (
                "TRAVEL_EXPENSE",
                "외식/식사",
                True,
                "category_document_type_conflict",
            ),
        )

    def test_user_review_can_explicitly_keep_cross_taxonomy_document_type(self):
        self.assertEqual(
            validate_classification(
                "TRAVEL_EXPENSE",
                "외식/식사",
                allow_explicit_document_type=True,
            ),
            ("TRAVEL_EXPENSE", "외식/식사", False, None),
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
