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
        self.assertEqual(result['classification_validation']['measured'], 1)
        self.assertEqual(result['final']['rate'], 1 / 3)
        self.assertIsNone(aggregate([])['stages']['final']['rate'])


if __name__ == '__main__':
    unittest.main()
