import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app.api.routes import rag_evaluations
from app.api.routes.rag_evaluations import RagEvaluationDataset, evaluate_rag
from app.models.user import User


def _case_result(question_id: str) -> dict:
    return {
        "question_id": question_id,
        "question": f"question {question_id}",
        "question_type": "fact",
        "difficulty": "easy",
        "answerable": True,
        "expected_documents": ["DOC-1"],
        "retrieved_documents": ["DOC-1"],
        "answer": "answer",
        "expected_answer": "answer",
        "hit": True,
        "recall": 1.0,
        "reciprocal_rank": 1.0,
        "ndcg_at_k": 1.0,
        "answer_score": 1.0,
        "answer_correct": True,
        "faithfulness": 1.0,
        "hallucination_score": 0.0,
        "citation_accuracy": 1.0,
        "rejected": False,
        "sources": [],
        "latency_ms": {stage: 1.0 for stage in (
            "query_rewrite", "embedding", "dense", "bm25", "reranker",
            "retrieval", "llm_answer", "total",
        )},
    }


class RagEvaluationCheckpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.checkpoint_directory = Path(self.temporary_directory.name)
        self.directory_patch = patch.object(rag_evaluations, "_CHECKPOINT_DIR", self.checkpoint_directory)
        self.directory_patch.start()
        rag_evaluations._running_rag_evaluations.clear()
        rag_evaluations._rag_evaluation_states.clear()
        rag_evaluations._latest_evaluations.clear()
        self.user = User(id="developer", name="Developer", email="developer@example.com", role="DEVELOPER")
        self.dataset = RagEvaluationDataset.model_validate({
            "dataset_name": "five-case-checkpoint-test",
            "question_count": 5,
            "cases": [{
                "question_id": f"{index:03d}",
                "question": f"question {index}",
                "expected_documents": ["DOC-1"],
                "expected_answer": "answer",
                "answerable": True,
            } for index in range(1, 6)],
        })

    def tearDown(self):
        self.directory_patch.stop()
        self.temporary_directory.cleanup()

    async def test_five_cases_complete_and_summary_is_built(self):
        evaluator = AsyncMock(side_effect=lambda case, *_args: _case_result(case.question_id))
        with (
            patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})),
            patch.object(rag_evaluations, "_evaluate_rag_case", evaluator),
        ):
            result = await evaluate_rag(self.dataset, self.user)

        checkpoint = json.loads(next(self.checkpoint_directory.glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(evaluator.await_count, 5)
        self.assertEqual(checkpoint["completed_question_ids"], ["001", "002", "003", "004", "005"])
        self.assertEqual(checkpoint["errors"], {})
        self.assertEqual(result["summary"]["total"], 5)
        self.assertEqual(result["summary"]["retrieval_evaluated"], 5)
        self.assertEqual(result["summary"]["hit_at_k"], 1.0)

    async def test_interrupted_checkpoint_resumes_without_repeating_completed_cases(self):
        first_calls = []

        async def interrupted(case, *_args):
            first_calls.append(case.question_id)
            if case.question_id == "003":
                raise KeyboardInterrupt()
            return _case_result(case.question_id)

        with (
            patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})),
            patch.object(rag_evaluations, "_evaluate_rag_case", side_effect=interrupted),
        ):
            with self.assertRaises(KeyboardInterrupt):
                await evaluate_rag(self.dataset, self.user)

        saved = json.loads(next(self.checkpoint_directory.glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(saved["completed_question_ids"], ["001", "002"])

        resumed_calls = []

        async def resumed(case, *_args):
            resumed_calls.append(case.question_id)
            return _case_result(case.question_id)

        with (
            patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})),
            patch.object(rag_evaluations, "_evaluate_rag_case", side_effect=resumed),
        ):
            result = await evaluate_rag(self.dataset, self.user)

        self.assertEqual(resumed_calls, ["003", "004", "005"])
        self.assertEqual(len(result["cases"]), 5)

    async def test_exhausted_network_retries_record_error_and_continue(self):
        calls: dict[str, int] = {}

        async def flaky(case, *_args):
            calls[case.question_id] = calls.get(case.question_id, 0) + 1
            if case.question_id == "003":
                raise httpx.ConnectError("temporary name resolution failure")
            return _case_result(case.question_id)

        with (
            patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})),
            patch.object(rag_evaluations, "_evaluate_rag_case", side_effect=flaky),
            patch.object(rag_evaluations.asyncio, "sleep", new_callable=AsyncMock) as sleep,
        ):
            result = await evaluate_rag(self.dataset, self.user)

        checkpoint = json.loads(next(self.checkpoint_directory.glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(calls["003"], 4)
        self.assertEqual(sleep.await_args_list[0].args[0], 2)
        self.assertEqual(sleep.await_args_list[1].args[0], 5)
        self.assertEqual(sleep.await_args_list[2].args[0], 10)
        self.assertEqual(checkpoint["errors"]["003"]["retry_count"], 3)
        self.assertIn("004", checkpoint["completed_question_ids"])
        self.assertIn("005", checkpoint["completed_question_ids"])
        self.assertEqual(len(result["cases"]), 4)
        self.assertEqual(result["summary"]["total"], 5)

    async def test_http_503_retries_but_non_transient_error_does_not(self):
        attempts = {"001": 0, "002": 0}

        async def failures(case, *_args):
            if case.question_id in attempts:
                attempts[case.question_id] += 1
            if case.question_id == "001":
                raise HTTPException(status_code=503, detail="temporary backend failure")
            if case.question_id == "002":
                raise HTTPException(status_code=403, detail="forbidden")
            return _case_result(case.question_id)

        with (
            patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})),
            patch.object(rag_evaluations, "_evaluate_rag_case", side_effect=failures),
            patch.object(rag_evaluations.asyncio, "sleep", new_callable=AsyncMock),
        ):
            await evaluate_rag(self.dataset, self.user)

        self.assertEqual(attempts["001"], 4)
        self.assertEqual(attempts["002"], 1)

    async def test_same_dataset_cannot_run_twice(self):
        dataset_hash = rag_evaluations._dataset_hash(self.dataset)
        rag_evaluations._running_rag_evaluations.add(dataset_hash)
        try:
            with self.assertRaises(HTTPException) as raised:
                await evaluate_rag(self.dataset, self.user)
            self.assertEqual(raised.exception.status_code, 409)
        finally:
            rag_evaluations._running_rag_evaluations.discard(dataset_hash)


if __name__ == "__main__":
    unittest.main()
