import ast
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from time import perf_counter

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import receipt_document_classifier as classifier


class FakeModel:
    classes_ = np.array(["EXPENSE_REPORT", "PURCHASE_REQUEST", "TRAVEL_EXPENSE", "WELFARE_BENEFIT"])

    def __init__(self, probabilities):
        self.probabilities = probabilities

    def predict_proba(self, texts):
        return np.array([self.probabilities])


def artifact(probabilities, threshold=.8):
    return {"model": FakeModel(probabilities), "profile": {"with_description": False, "with_category": True},
            "thresholds": dict.fromkeys(FakeModel.classes_, threshold), "production_validated": True,
            "model_version": "test"}


class DocumentClassifierTests(unittest.TestCase):
    def test_bundled_artifact_loads_with_four_classes_and_category_feature(self):
        classifier._load_artifact.cache_clear()
        model = classifier._load_artifact()
        self.assertIsNotNone(model)
        self.assertTrue(model["profile"]["with_category"])
        decision = classifier.classify_document_type({"merchant": "서점", "expense_category": "도서", "items": [{"name": "책"}]})
        self.assertEqual(set(decision["probabilities"]), set(FakeModel.classes_))
        self.assertEqual(decision["status"], "REVIEW")

    def test_allowlist_excludes_ocr_labels_and_gold(self):
        fields = {"merchant": "서점", "items": [{"name": "책"}], "expense_category": "도서"}
        augmented = {**fields, "ocr_text": "secret", "document_type": "EXPENSE_REPORT",
                     "doc_type": "EXPENSE_REPORT", "source_id": "synthetic_123", "description": "gold"}
        self.assertEqual(classifier.feature_text(fields), classifier.feature_text(augmented))
        self.assertIn("category=도서", classifier.feature_text(fields))

    def test_same_category_can_select_different_document_types(self):
        for probabilities, expected in [([.02, .94, .02, .02], "PURCHASE_REQUEST"),
                                        ([.02, .02, .02, .94], "WELFARE_BENEFIT"),
                                        ([.94, .02, .02, .02], "EXPENSE_REPORT")]:
            with patch.object(classifier, "_load_artifact", return_value=artifact(probabilities)):
                decision = classifier.classify_document_type({"expense_category": "도서", "merchant": "서점"})
            self.assertEqual(decision["selected_document_type"], expected)
            self.assertEqual(decision["status"], "PASS")

    def test_low_confidence_and_unvalidated_class_fall_back(self):
        for threshold in [.8, None]:
            with patch.object(classifier, "_load_artifact", return_value=artifact([.4, .2, .2, .2], threshold)):
                decision = classifier.classify_document_type({"expense_category": "도서"})
            self.assertEqual(decision["selected_document_type"], "WELFARE_BENEFIT")
            self.assertEqual(decision["predicted_document_type"], "EXPENSE_REPORT")
            self.assertIn("DOCUMENT_CLASSIFIER_LOW_CONFIDENCE", decision["reasons"])

    def test_strong_conflict_requires_review(self):
        with patch.object(classifier, "_load_artifact", return_value=artifact([.01, .96, .01, .02])):
            decision = classifier.classify_document_type({"expense_category": "도서", "transaction_description": "부산 출장 중 구매"})
        self.assertEqual(decision["selected_document_type"], "PURCHASE_REQUEST")
        self.assertIn("DOCUMENT_CLASSIFIER_STRONG_RULE_CONFLICT", decision["reasons"])

    def test_missing_model_and_unknown_category_fail_closed(self):
        with patch.object(classifier, "_load_artifact", return_value=None):
            decision = classifier.classify_document_type({"expense_category": "unknown"})
        self.assertIsNone(decision["selected_document_type"])
        self.assertEqual(decision["status"], "REVIEW")

    def test_synthetic_artifact_is_never_auto_confirmed(self):
        model = artifact([.94, .02, .02, .02])
        model["production_validated"] = False
        with patch.object(classifier, "_load_artifact", return_value=model):
            decision = classifier.classify_document_type({"expense_category": "도서"})
        self.assertEqual(decision["selected_document_type"], "EXPENSE_REPORT")
        self.assertIn("DOCUMENT_CLASSIFIER_SYNTHETIC_VALIDATION_ONLY", decision["reasons"])

    def test_inference_exception_falls_back(self):
        model = artifact([.94, .02, .02, .02])
        with patch.object(classifier, "_load_artifact", return_value=model), patch.object(model["model"], "predict_proba", side_effect=ValueError):
            decision = classifier.classify_document_type({"expense_category": "도서"})
        self.assertEqual(decision["source"], "RULE_FALLBACK")
        self.assertIn("DOCUMENT_CLASSIFIER_FAILED", decision["reasons"])

    def test_normalize_persists_routing_and_preserves_extraction_review(self):
        # Exercise the actual normalization function without importing unrelated
        # network/embedding application dependencies. Only its I/O helpers are stubbed.
        path = Path(__file__).resolve().parents[1] / "app/services/finance_receipt_simple.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_normalize")
        namespace = {"Any": object, "RECEIPTS_MODEL_NAME": "gemma", "classify_document_type": classifier.classify_document_type,
                     "_normalize_expense_category": lambda v, t: v, "_clean_model_items": lambda v: v or [],
                     "_ground_masked_card_number": lambda v, t: (None, {}), "_payment_from_ocr": lambda t: (None, {}),
                     "_as_number": lambda v: v}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
        receipt = {"expense_category": "도서", "merchant": "서점", "items": [], "total_amount": 1000,
                   "automation_validation": {"decision": "REVIEW", "reasons": ["EXTRACTION_ERROR"], "checks": {}}}
        with patch.object(classifier, "_load_artifact", return_value=artifact([.94, .02, .02, .02])):
            result = namespace["_normalize"](copy.deepcopy(receipt), "receipt.png", "unused OCR")
        self.assertEqual(result["document_type"], "EXPENSE_REPORT")
        self.assertEqual(result["structured_data"]["document_type"], result["document_type"])
        self.assertTrue(result["structured_data"]["needs_review"])
        self.assertIn("EXTRACTION_ERROR", result["structured_data"]["review_reasons"])

    def test_training_adapter_does_not_use_answer_and_groups_are_disjoint(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from train_receipt_document_classifier import parse_input, grouped_splits
        fields = parse_input("매장명: 서점\n참고도서 10,000원\n비고: 출장 중 구매")
        self.assertEqual(fields["items"], [{"name": "참고도서"}])
        self.assertEqual(fields["expense_category"], "도서")
        self.assertNotIn("출장", classifier.feature_text(fields))
        rows = [{"label": label, "fields": {"merchant": f"merchant{n}-{label}", "items": [],
                 "transaction_description": f"purpose{n}-{label}"}} for n in range(20) for label in FakeModel.classes_]
        rows.append(copy.deepcopy(rows[0]))
        splits, groups = grouped_splits(rows)
        self.assertEqual(groups[0], groups[-1])
        self.assertFalse({groups[i] for i in splits["train"]} & {groups[i] for i in splits["test"]})


class ExtractionCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_extraction_still_calls_gemma_once_without_document_type_prompt(self):
        path = Path(__file__).resolve().parents[1] / "app/services/finance_receipt_simple.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_classify_receipt_with_model")
        prompt_node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_simple_receipt_prompt")
        prompt_source = ast.get_source_segment(path.read_text(encoding="utf-8"), prompt_node)
        self.assertNotIn("document_type", prompt_source)
        self.assertNotIn("doc_type", prompt_source)
        generator = AsyncMock(return_value=json.dumps({"merchant": "서점", "expense_category": "도서", "items": []}))
        namespace = {"Any": object, "json": json, "perf_counter": perf_counter,
                     "_preflight_review_reasons": lambda text: [],
                     "_simple_receipt_prompt": lambda *args: ("extract receipt", {}),
                     "_generate_receipt_json": generator, "_reconcile_amounts": lambda *args: None,
                     "_payment_from_ocr": lambda *args: (None, {}), "ground_items": lambda *args: {},
                     "_simple_validation": lambda *args: {"decision": "PASS", "reasons": [], "checks": {}},
                     "_generation_metrics": lambda raw: {}, "RECEIPT_PIPELINE_VERSION": "test", "FINANCE_PROMPT_VERSION": "test",
                     "RECEIPT_LLM_NUM_PREDICT": 800, "RECEIPT_LLM_TIMEOUT_SECONDS": 60,
                     "RECEIPT_LLM_KEEP_ALIVE": "5m", "RECEIPT_LLM_NUM_CTX": 8000}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
        result = await namespace["_classify_receipt_with_model"]("receipt OCR", "receipt.png", "gemma")
        self.assertEqual(generator.await_count, 1)
        self.assertEqual(result["llm_trace"]["call_count"], 1)
        self.assertNotIn("document_type", result)


if __name__ == "__main__":
    unittest.main()
