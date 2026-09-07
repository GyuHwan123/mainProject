"""Regression checks for independent extraction and routing decisions."""
import ast
import copy
from pathlib import Path
import unittest


class ValidationStagesTests(unittest.TestCase):
    def test_stage_combinations_and_repeat_normalization(self):
        path = Path(__file__).resolve().parents[1] / 'app/services/finance_receipt_simple.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_normalize')
        for extraction_status in ('PASS', 'REVIEW'):
            for routing_status in ('PASS', 'REVIEW'):
                with self.subTest(extraction=extraction_status, routing=routing_status):
                    namespace = {
                        'Any': object, 'RECEIPTS_MODEL_NAME': 'test',
                        '_category_evidence_text': lambda r, t: t,
                        '_normalize_expense_category': lambda v, t: v,
                        '_clean_model_items': lambda v: v or [],
                        '_ground_masked_card_number': lambda v, t: (None, {}),
                        '_payment_from_ocr': lambda t: (None, {}),
                        '_extract_explicit_merchant': lambda t: None,
                        '_as_number': lambda v: v,
                        'classify_document_type': lambda r: {
                            'selected_document_type': 'EXPENSE_REPORT',
                            'status': routing_status,
                            'reasons': ['ROUTING_ERROR'] if routing_status == 'REVIEW' else [],
                        },
                    }
                    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
                    validation = {'decision': extraction_status, 'checks': {},
                                  'reasons': ['EXTRACTION_ERROR'] if extraction_status == 'REVIEW' else []}
                    original = copy.deepcopy(validation)
                    receipt = {'merchant': 'Store', 'expense_category': '도서', 'total_amount': 1000,
                               'automation_validation': validation}
                    normalized = namespace['_normalize'](dict(receipt), 'test.png', '')
                    structured = normalized['structured_data']
                    self.assertEqual(validation, original)
                    self.assertEqual(structured['extraction_validation'], original)
                    self.assertEqual(structured['classification_validation']['decision'], routing_status)
                    expected = extraction_status == routing_status == 'PASS'
                    self.assertEqual(structured['automation_validation']['decision'] == 'PASS', expected)
                    self.assertEqual(structured['needs_review'], not expected)
                    again = namespace['_normalize'](dict(structured), 'test.png', '')['structured_data']
                    self.assertEqual(again['extraction_validation'], original)
                    self.assertEqual(again['automation_validation'], structured['automation_validation'])


if __name__ == '__main__':
    unittest.main()
