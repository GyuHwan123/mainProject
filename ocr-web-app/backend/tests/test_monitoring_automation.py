import ast
from collections import Counter
from pathlib import Path
import unittest


class MonitoringAutomationTests(unittest.TestCase):
    def test_mixed_history_and_duplicate_reasons(self):
        path = Path(__file__).resolve().parents[1] / 'app/api/routes/finance_evaluations.py'
        function = next(n for n in ast.parse(path.read_text(encoding='utf-8')).body
                        if isinstance(n, ast.FunctionDef) and n.name == '_monitoring_automation')
        namespace = {'Any': object, 'Counter': Counter}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
        aggregate = namespace['_monitoring_automation']
        rows = [
            {'validation': {'decision': 'REVIEW',
                            'extraction_validation': {'decision': 'REVIEW', 'reasons': ['ITEM_SUM_MISMATCH', 'ITEM_SUM_TOTAL_MISMATCH']},
                            'classification_validation': {'decision': 'REVIEW', 'reasons': ['LOW_CONFIDENCE']}}},
            {'pipeline_trace': {'validation': {'decision': 'PASS', 'extraction_validation': {'decision': 'PASS'}}}},
            {'validation': {'decision': 'REVIEW'}}, {},
        ]
        result = aggregate(rows)['stages']
        self.assertEqual(result['extraction_validation']['rate'], .5)
        self.assertEqual(result['extraction_validation']['unmeasured'], 2)
        self.assertEqual(result['extraction_validation']['reasons'], [{'code': 'ITEM_SUM_TOTAL_MISMATCH', 'count': 1}])
        self.assertEqual(set(result), {'extraction_validation'})
        self.assertIsNone(aggregate([])['stages']['extraction_validation']['rate'])
        pending = aggregate([{'validation': {
            'decision': 'USER_CONFIRM', 'reasons': ['CATEGORY_EVIDENCE_MISSING'],
            'category_validation': {'decision': 'USER_CONFIRM', 'reasons': ['CATEGORY_EVIDENCE_MISSING']},
        }}, {}])['stages']
        self.assertEqual(set(pending), {'extraction_validation'})
        self.assertEqual(pending['extraction_validation']['measured'], 0)
        self.assertEqual(pending['extraction_validation']['unmeasured'], 2)


if __name__ == '__main__':
    unittest.main()
