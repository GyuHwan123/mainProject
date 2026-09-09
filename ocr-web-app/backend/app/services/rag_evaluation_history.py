"""Additive checkpoint metadata; no evaluation or resume decisions live here."""

from copy import deepcopy
from uuid import uuid4


METRIC_KEYS = (
    "answer_accuracy", "faithfulness", "hit_at_1", "hit_at_k", "hit_at_3", "hit_at_5",
    "recall_at_k", "mrr", "ndcg_at_k", "context_precision", "hallucination_rate",
    "unanswerable_rejection_rate",
)


def prepare_history(checkpoint: dict, user_email: str, configuration: dict) -> None:
    history = checkpoint.get("history")
    if history is None:
        # Old checkpoints cannot establish which user/model produced their results.
        if checkpoint.get("results") or checkpoint.get("completed_question_ids") or checkpoint.get("errors"):
            checkpoint["history"] = {"status": "skipped", "reason": "legacy_checkpoint"}
            return
        checkpoint["history"] = {
            "id": str(uuid4()), "run_id": checkpoint.get("run_id") or str(uuid4()),
            "owner_email": user_email,
            "configuration": deepcopy(configuration), "status": "waiting",
        }
    elif history.get("status") in {"saved", "skipped"}:
        return
    elif history.get("run_id") and checkpoint.get("run_id") and history.get("run_id") != checkpoint.get("run_id"):
        history.update(status="skipped", reason="run_id_mismatch")
        return
    elif history.get("payload") and history.get("owner_email") == user_email:
        if history["payload"].get("run_id") == (checkpoint.get("run_id") or history.get("run_id")):
            return
        history["payload"] = None
    elif history.get("owner_email") != user_email or history.get("configuration") != configuration:
        history.update(status="skipped", reason="execution_metadata_mismatch")


def completed_history_payload(checkpoint: dict, dataset: dict, user_email: str) -> dict | None:
    history = checkpoint.get("history") or {}
    if history.get("status") in {"saved", "skipped"} or history.get("owner_email") != user_email:
        return None
    run_id = checkpoint.get("run_id") or history.get("run_id")
    if history.get("payload"):
        if history["payload"].get("run_id") == run_id:
            return history["payload"]
        history["payload"] = None
    ids = [case["question_id"] for case in dataset["cases"]]
    results = checkpoint.get("results") or {}
    if (
        checkpoint.get("status") != "completed" or checkpoint.get("errors")
        or len(set(ids)) != len(ids)
        or set(results) != set(ids)
        or set(checkpoint.get("completed_question_ids") or []) != set(ids)
        or any(results[key].get("question_id") != key for key in ids)
    ):
        return None
    # Freeze the first fully completed summary and timestamp before attempting I/O.
    # Re-using a saved payload is only valid for the same run_id; new runs must write a fresh row.
    result = checkpoint.get("result") or {}
    summary = result.get("summary") or {}
    if summary.get("total") != len(ids) or len(result.get("cases") or []) != len(ids):
        return None
    configuration = history["configuration"]
    payload = {
        "id": history["id"], "run_id": run_id,
        "dataset_name": dataset["dataset_name"],
        "dataset_hash": checkpoint["dataset_hash"],
        "question_count": len(ids), "completed_count": len(ids),
        "started_at": checkpoint["started_at"], "evaluated_at": result["created_at"],
        "model_name": configuration["model_name"], "prompt_version": configuration["prompt_version"],
        "configuration": {
            **deepcopy(configuration),
            "embedding_checkpoint": configuration.get("embedding_model"),
            "checkpoint_dataset_hash": checkpoint["dataset_hash"],
            "hybrid_enabled": configuration.get("retrieval_method") == "dense_bm25_hybrid",
            "bm25_enabled": bool(configuration.get("bm25_candidate_count")),
        }, "evaluator_version": "rag-evaluator-v1",
        "average_latency_ms": result.get("latency", {}).get("total", {}).get("average_ms"),
        "summary_metrics": {
            "source_accuracy": None,
            "answer_relevance": None,
            **deepcopy(summary),
            "evaluation_result": deepcopy(result),
            "execution_counts": {
                "total": len(ids), "success": len(ids), "failed": 0,
                "errors": len(checkpoint.get("errors") or {}),
                "answer_passed": sum(item.get("answer_correct") is True for item in results.values()),
                "answer_failed": sum(item.get("answer_correct") is False for item in results.values()),
            },
        },
        **{key: summary.get(key) for key in METRIC_KEYS},
    }
    history["payload"] = payload
    return payload
