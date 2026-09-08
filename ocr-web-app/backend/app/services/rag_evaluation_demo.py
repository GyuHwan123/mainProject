"""One-time seed payload builder. Never imported by the monitoring request path."""

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from uuid import UUID, NAMESPACE_URL, uuid5

from app.services.rag_evaluation_history import METRIC_KEYS
from app.services.rag_demo_baseline import BASELINE_ID, BASELINE_DAYS

KST = timezone(timedelta(hours=9))
DEMO_DAYS = BASELINE_DAYS
DEMO_DEFAULTS = {
    "answer_accuracy": .82, "faithfulness": .88, "hit_at_1": .78,
    "context_precision": .80, "hallucination_rate": .08,
}


def create_demo_seed(user_id: str, batch_id: str, end_date: date,
                     latest: dict | None) -> list[dict]:
    """Fixed historical baseline, for an explicit one-time seed operation.

    A real anchor's missing metrics stay null. Without an anchor, only the five
    chart series receive explicitly synthetic defaults. All rows remain demos,
    including the endpoint copied from the real anchor.
    """
    user_id = str(UUID(user_id))
    if batch_id != BASELINE_ID or not latest:
        raise ValueError("A fixed baseline and the first actual evaluation are required")
    first_day = datetime.fromisoformat(latest["evaluated_at"].replace("Z", "+00:00")).astimezone(KST).date()
    if end_date != first_day - timedelta(days=1):
        raise ValueError("Baseline must end the day before the first actual evaluation")
    anchor = latest or {}
    source_summary = anchor.get("summary_metrics") or {}
    keys = (*METRIC_KEYS, "hit_at_4")
    targets = {
        key: anchor.get(key, source_summary.get(key)) if latest else DEMO_DEFAULTS.get(key)
        for key in keys
    }
    count = anchor.get("question_count") or 100
    configuration = deepcopy(anchor.get("configuration") or {})
    configuration.update(demo=True, demo_batch_id=batch_id,
                         demo_end_date=end_date.isoformat(), demo_anchor_id=anchor.get("id"),
                         demo_anchor_evaluated_at=anchor.get("evaluated_at"))
    rows = []
    for index in range(DEMO_DAYS):
        day = end_date - timedelta(days=DEMO_DAYS - 1 - index)
        # Small repeatable setbacks, with an exact endpoint and no refresh jitter.
        remaining = (DEMO_DAYS - 1 - index) / (DEMO_DAYS - 1)
        gap = max(0, .14 * remaining + (0, -.009, .008, -.005, .011)[index % 5]) if remaining else 0
        metrics = {}
        for key, target in targets.items():
            if target is None:
                metrics[key] = None
            elif key == "hallucination_rate":
                metrics[key] = target + (1 - target) * gap
            else:
                metrics[key] = target * (1 - gap)
        timestamp = datetime.combine(day, time.min, KST).isoformat()
        # Compact synthetic type summaries only; no questions/answers/case rows.
        type_examples = (
            ("single_document_fact", .90), ("paraphrase_semantic", .84),
            ("confusable_reranker", .73), ("multi_document", .76),
            ("unanswerable", .92),
        )
        per_type, remainder = divmod(count, len(type_examples))
        question_types = [
            {"question_type": name, "count": per_type + int(position < remainder),
             "answer_accuracy": accuracy * (1 - gap)}
            for position, (name, accuracy) in enumerate(type_examples)
            if per_type + int(position < remainder) > 0
        ]
        rows.append({
            "id": str(uuid5(NAMESPACE_URL, f"rag-monitoring-demo-v1/{user_id}/{batch_id}/{index}")),
            "user_id": user_id,
            "dataset_name": f"[DEMO] {anchor.get('dataset_name') or 'Synthetic RAG monitoring'}",
            "dataset_hash": sha256(b"rag-monitoring-demo-v1").hexdigest(),
            "question_count": count, "completed_count": count,
            "started_at": timestamp, "evaluated_at": timestamp,
            "model_name": anchor.get("model_name") or "DEMO (no real baseline)",
            "prompt_version": anchor.get("prompt_version") or "demo",
            "evaluator_version": "rag-monitoring-demo-v1",
            "configuration": deepcopy(configuration),
            "average_latency_ms": anchor.get("average_latency_ms"),
            "summary_metrics": {
                "total": count, "top_k": source_summary.get("top_k"), **metrics,
                "response_distribution": {"correct": 76, "incorrect": 9, "rejected": 12, "unknown": 3},
                "response_distribution_sample_total": 100,
                "response_distribution_is_demo": True,
                "question_type_metrics": question_types,
                "question_type_metrics_is_demo": True,
            },
            **{key: metrics[key] for key in METRIC_KEYS},
        })
    return list(reversed(rows))
