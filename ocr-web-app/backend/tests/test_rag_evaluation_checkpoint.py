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
        self.history_patch = patch.object(rag_evaluations.supabase_service, "save_rag_evaluation_run")
        self.history_save = self.history_patch.start()
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
        self.history_patch.stop()
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
        self.history_save.assert_called_once()
        payload = self.history_save.call_args.args[1]
        self.assertEqual(payload["summary_metrics"], result["summary"])
        self.assertEqual(payload["average_latency_ms"], result["latency"]["total"]["average_ms"])
        self.assertEqual(payload["question_count"], payload["completed_count"])
        self.assertNotIn("cases", payload)
        self.assertNotIn("result_snapshot", payload)
        self.assertEqual(result["history"]["status"], "saved")

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
        self.history_save.assert_called_once()
        self.assertEqual(self.history_save.call_args.args[1]["id"], saved["history"]["id"])

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
        self.history_save.assert_not_called()
        self.assertEqual(checkpoint["status"], "partial")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(rag_evaluations._rag_progress(self.user.email)["status"], "partial")

    async def test_legacy_177_successes_retry_only_23_errors(self):
        dataset = RagEvaluationDataset.model_validate({
            "dataset_name": "200-case-resume", "question_count": 200,
            "cases": [{**self.dataset.cases[0].model_dump(), "question_id": f"EVAL-{i:03d}"} for i in range(1, 201)],
        })
        checkpoint = rag_evaluations._load_checkpoint(dataset)
        ids = [case.question_id for case in dataset.cases]
        checkpoint.update(status="completed", completed_question_ids=ids[:177],
                          results={qid: _case_result(qid) for qid in ids[:177]},
                          errors={qid: {"error_type": "ConnectError"} for qid in ids[177:]})
        original_results = dict(checkpoint["results"])
        rag_evaluations._save_checkpoint(checkpoint)
        status = rag_evaluations.rag_evaluation_checkpoint_status(dataset, self.user)
        self.assertEqual((status["status"], status["completed_count"], status["error_count"]), ("partial", 177, 23))
        progress = []
        async def resumed(case, *_args):
            progress.append(rag_evaluations._rag_progress(self.user.email))
            return _case_result(case.question_id)
        with patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})), patch.object(rag_evaluations, "_evaluate_rag_case", side_effect=resumed) as evaluator:
            result = await evaluate_rag(dataset, self.user, retry_failed=True)
        self.assertEqual([call.args[0].question_id for call in evaluator.await_args_list], ids[177:])
        self.assertEqual(progress[0]["progress_percent"], 88.5)
        self.assertIsNone(progress[0]["estimated_remaining_seconds"])
        self.assertEqual((result["status"], result["completed_count"], result["error_count"]), ("completed", 200, 0))
        saved = rag_evaluations._load_checkpoint(dataset)
        self.assertEqual(saved["errors"], {})
        self.assertEqual({qid: saved["results"][qid] for qid in ids[:177]}, original_results)
        self.assertEqual(len(result["cases"]), 200)
        # Legacy ownership metadata remains unchanged: do not invent DB attribution.
        self.history_save.assert_not_called()

    async def test_explicit_retry_rejects_missing_or_incompatible_checkpoint(self):
        with self.assertRaises(HTTPException) as missing:
            await evaluate_rag(self.dataset, self.user, retry_failed=True)
        self.assertEqual(missing.exception.status_code, 409)
        checkpoint = rag_evaluations._load_checkpoint(self.dataset)
        checkpoint["configuration"] = {"old": "configuration"}
        rag_evaluations._save_checkpoint(checkpoint)
        path = next(self.checkpoint_directory.glob("*.json"))
        before = path.read_bytes()
        status = rag_evaluations.rag_evaluation_checkpoint_status(self.dataset, self.user)
        self.assertFalse(status["configuration_matches"])
        with self.assertRaises(HTTPException) as changed:
            await evaluate_rag(self.dataset, self.user, retry_failed=True)
        self.assertEqual(changed.exception.status_code, 409)
        self.assertEqual(path.read_bytes(), before)

    async def test_repeated_completed_result_does_not_insert_again(self):
        evaluator = AsyncMock(side_effect=lambda case, *_args: _case_result(case.question_id))
        with patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})), patch.object(rag_evaluations, "_evaluate_rag_case", evaluator):
            first = await evaluate_rag(self.dataset, self.user)
            second = await evaluate_rag(self.dataset, self.user)
        self.history_save.assert_called_once()
        self.assertEqual(evaluator.await_count, 5)
        self.assertEqual(first["history"]["id"], second["history"]["id"])

    async def test_database_failure_retries_frozen_payload_without_evaluation(self):
        self.history_save.side_effect = RuntimeError("database unavailable")
        with patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})), patch.object(rag_evaluations, "_evaluate_rag_case", side_effect=lambda case, *_args: _case_result(case.question_id)):
            result = await evaluate_rag(self.dataset, self.user)
        self.assertEqual(result["history"]["status"], "pending")
        payload = self.history_save.call_args.args[1]
        saved = json.loads(next(self.checkpoint_directory.glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "completed")
        # Simulate process restart: only the checkpoint remains.
        rag_evaluations._latest_evaluations.clear()
        self.history_save.side_effect = None
        with patch.object(rag_evaluations, "_evaluate_rag_case", new_callable=AsyncMock) as evaluator, patch.object(rag_evaluations, "_catalog_maps") as catalog:
            retried = await rag_evaluations.retry_rag_evaluation_history(self.dataset, self.user)
        self.assertEqual(retried["status"], "saved")
        self.assertEqual(self.history_save.call_args.args[1], payload)
        evaluator.assert_not_awaited()
        catalog.assert_not_called()
        await rag_evaluations.retry_rag_evaluation_history(self.dataset, self.user)
        self.assertEqual(self.history_save.call_count, 2)

    async def test_failed_question_saves_history_only_after_successful_resume(self):
        def first(case, *_args):
            if case.question_id == "003":
                raise HTTPException(status_code=400, detail="failed")
            return _case_result(case.question_id)
        with patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})), patch.object(rag_evaluations, "_evaluate_rag_case", side_effect=first):
            await evaluate_rag(self.dataset, self.user)
        self.history_save.assert_not_called()
        with patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})), patch.object(rag_evaluations, "_evaluate_rag_case", side_effect=lambda case, *_args: _case_result(case.question_id)) as evaluator:
            await evaluate_rag(self.dataset, self.user)
        self.assertEqual(evaluator.await_count, 1)
        self.history_save.assert_called_once()

    async def test_legacy_checkpoint_resumes_but_is_not_misattributed(self):
        checkpoint = rag_evaluations._load_checkpoint(self.dataset)
        checkpoint["results"] = {"001": _case_result("001")}
        checkpoint["completed_question_ids"] = ["001"]
        rag_evaluations._save_checkpoint(checkpoint)
        with patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})), patch.object(rag_evaluations, "_evaluate_rag_case", side_effect=lambda case, *_args: _case_result(case.question_id)) as evaluator:
            result = await evaluate_rag(self.dataset, self.user)
        self.assertEqual(evaluator.await_count, 4)
        self.assertEqual(result["history"]["status"], "skipped")
        self.history_save.assert_not_called()

    async def test_retry_rejects_other_owner_and_active_evaluation(self):
        with patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})), patch.object(rag_evaluations, "_evaluate_rag_case", side_effect=lambda case, *_args: _case_result(case.question_id)):
            await evaluate_rag(self.dataset, self.user)
        other = User(id="other", name="Other", email="other@example.com", role="DEVELOPER")
        with self.assertRaises(HTTPException) as denied:
            await rag_evaluations.retry_rag_evaluation_history(self.dataset, other)
        self.assertEqual(denied.exception.status_code, 403)
        rag_evaluations._running_rag_evaluations.add(rag_evaluations._dataset_hash(self.dataset))
        with self.assertRaises(HTTPException) as busy:
            await rag_evaluations.retry_rag_evaluation_history(self.dataset, self.user)
        self.assertEqual(busy.exception.status_code, 409)

    async def test_model_changed_during_resume_keeps_results_but_skips_history(self):
        # Keep the existing checkpoint configuration unchanged: otherwise the
        # rewrite-model fallback changes too and the loader correctly starts fresh.
        rewrite_patch = patch.object(rag_evaluations.settings, "RAG_QUERY_REWRITE_MODEL", "fixed-rewrite-model")
        rewrite_patch.start()
        self.addCleanup(rewrite_patch.stop)
        def first(case, *_args):
            if case.question_id == "003":
                raise HTTPException(status_code=400, detail="failed")
            return _case_result(case.question_id)
        with patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})), patch.object(rag_evaluations, "_evaluate_rag_case", side_effect=first):
            await evaluate_rag(self.dataset, self.user)
        with patch.object(rag_evaluations.settings, "RAG_LLM_MODEL", "different-model"), patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})), patch.object(rag_evaluations, "_evaluate_rag_case", side_effect=lambda case, *_args: _case_result(case.question_id)):
            result = await evaluate_rag(self.dataset, self.user)
        self.assertEqual(len(result["cases"]), 5)
        self.assertEqual(result["history"]["status"], "skipped")
        self.history_save.assert_not_called()

    async def test_configuration_change_starts_new_history_id_like_existing_checkpoint_loader(self):
        with patch.object(rag_evaluations, "_catalog_maps", return_value=({}, {})), patch.object(rag_evaluations, "_evaluate_rag_case", side_effect=lambda case, *_args: _case_result(case.question_id)) as evaluator:
            first = await evaluate_rag(self.dataset, self.user)
            with patch.object(rag_evaluations.settings, "RAG_TOP_K", rag_evaluations.settings.RAG_TOP_K + 1):
                second = await evaluate_rag(self.dataset, self.user)
        self.assertEqual(evaluator.await_count, 10)
        self.assertEqual(self.history_save.call_count, 2)
        self.assertNotEqual(first["history"]["id"], second["history"]["id"])

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
