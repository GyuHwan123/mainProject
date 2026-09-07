import copy
import unittest

from app.services.receipt_category_validation import validate_category


class CategoryValidationTests(unittest.TestCase):
    def test_evidence_limits_and_no_truncated_grounding(self):
        quote = '가' * 40
        receipt = {'expense_category_suggestion': '카페/음료',
                   'expense_category_evidence': [{'text': quote}, {'text': '커피'}]}
        result = validate_category(receipt, quote + '\n커피')
        self.assertEqual(result['decision'], 'USER_CONFIRM')
        self.assertEqual(len(result['matched_evidence']), 2)
        receipt['expense_category_evidence'].append({'text': '추가'})
        result = validate_category(receipt, quote + '\n커피\n추가')
        self.assertEqual(result['decision'], 'REVIEW')
        self.assertEqual(len(result['matched_evidence']), 2)
        receipt['expense_category_evidence'] = [{'text': quote + '환각'}]
        result = validate_category(receipt, quote)
        self.assertEqual(result['decision'], 'REVIEW')
        self.assertEqual(result['matched_evidence'], [])
        self.assertIn('CATEGORY_EVIDENCE_LIMIT_EXCEEDED', result['reasons'])

    def test_grounded_quote_does_not_imply_validated_acceptance(self):
        receipt = {'expense_category_suggestion': '카페/음료',
                   'expense_category_evidence': [{'text': '아메리카노', 'line_id': 'fake'}],
                   'items': [{'name': '아메리카노', 'total_amount': 4000}], 'tax_amount': 364}
        original = copy.deepcopy(receipt)
        result = validate_category(receipt, '카페\n아메리카노 4000')
        self.assertEqual(result['decision'], 'USER_CONFIRM')
        self.assertEqual(result['matched_evidence'][0]['ocr_lines'], [2])
        self.assertEqual(receipt, original)

    def test_hallucinated_quote_is_review(self):
        result = validate_category({'expense_category_suggestion': '대중교통',
                                    'expense_category_evidence': [{'text': 'KTX'}]}, '커피 4000')
        self.assertEqual(result['decision'], 'REVIEW')
        self.assertIn('CATEGORY_EVIDENCE_NOT_IN_OCR', result['reasons'])

    def test_legacy_category_is_preserved_without_automatic_approval(self):
        result = validate_category({'expense_category': '교통비'}, 'KTX')
        self.assertEqual(result['suggested_category'], '교통비')
        self.assertEqual(result['normalized_category'], '대중교통')
        self.assertEqual(result['decision'], 'USER_CONFIRM')

    def test_invalid_and_missing_values_are_distinct(self):
        self.assertEqual(validate_category({'expense_category_suggestion': 'invented'}, '')['decision'], 'REVIEW')
        self.assertEqual(validate_category({}, '')['decision'], 'USER_CONFIRM')

    def test_malformed_evidence_is_review(self):
        for evidence in ('KTX', [None], [{'text': 42}], [{'text': ''}]):
            with self.subTest(evidence=evidence):
                self.assertEqual(validate_category({'expense_category_suggestion': '대중교통',
                                                    'expense_category_evidence': evidence}, 'KTX')['decision'], 'REVIEW')
