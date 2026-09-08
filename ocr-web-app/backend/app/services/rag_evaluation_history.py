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
            "id": str(uuid4()), "owner_email": user_email,
            "configuration": deepcopy(configuration), "status": "waiting",
        }
    elif history.get("status") in {"saved", "skipped"}:
        return
    elif history.get("payload") and history.get("owner_email") == user_email:
        # A completed payload remains valid if only the current runtime changed.
        return
    elif history.get("owner_email") != user_email or history.get("configuration") != configuration:
        history.update(status="skipped", reason="execution_metadata_mismatch")


def completed_history_payload(checkpoint: dict, dataset: dict, user_email: str) -> dict | None:
    history = checkpoint.get("history") or {}
    if history.get("status") in {"saved", "skipped"} or history.get("owner_email") != user_email:
        return None
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
    # A lost HTTP response or repeated completed evaluation must use the same row.
    if history.get("payload"):
        return history["payload"]
    result = checkpoint.get("result") or {}
    summary = result.get("summary") or {}
    if summary.get("total") != len(ids) or len(result.get("cases") or []) != len(ids):
        return None
    configuration = history["configuration"]
    payload = {
        "id": history["id"], "dataset_name": dataset["dataset_name"],
        "dataset_hash": checkpoint["dataset_hash"],
        "question_count": len(ids), "completed_count": len(ids),
        "started_at": checkpoint["started_at"], "evaluated_at": result["created_at"],
        "model_name": configuration["model_name"], "prompt_version": configuration["prompt_version"],
        "configuration": deepcopy(configuration), "evaluator_version": "rag-evaluator-v1",
        "average_latency_ms": result.get("latency", {}).get("total", {}).get("average_ms"),
        "summary_metrics": deepcopy(summary),
        **{key: summary.get(key) for key in METRIC_KEYS},
    }
    history["payload"] = payload
    return payload
