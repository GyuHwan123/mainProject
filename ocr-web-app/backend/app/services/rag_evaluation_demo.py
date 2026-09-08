"""Pure, memory-only monitoring fixtures. Never persist these rows."""

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256

from app.services.rag_evaluation_history import METRIC_KEYS

KST = timezone(timedelta(hours=9))
DEMO_DAYS = 30
DEMO_DEFAULTS = {
    "answer_accuracy": .82, "faithfulness": .88, "hit_at_1": .78,
    "context_precision": .80, "hallucination_rate": .08,
}


def create_demo_runs(start_date: date, end_date: date, latest: dict | None,
                     today: date | None = None) -> list[dict]:
    """Thirty consecutive days ending today, filtered without shifting the curve.

    A real anchor's missing metrics stay null. Without an anchor, only the five
    chart series receive explicitly synthetic defaults. All rows remain demos,
    including the endpoint copied from the real anchor.
    """
    today = today or datetime.now(KST).date()
    anchor = latest or {}
    source_summary = anchor.get("summary_metrics") or {}
    keys = (*METRIC_KEYS, "hit_at_4")
    targets = {
        key: anchor.get(key, source_summary.get(key)) if latest else DEMO_DEFAULTS.get(key)
        for key in keys
    }
    count = anchor.get("question_count") or 100
    configuration = deepcopy(anchor.get("configuration") or {})
    configuration.update(demo=True, demo_anchor_id=anchor.get("id"),
                         demo_anchor_evaluated_at=anchor.get("evaluated_at"))
    rows = []
    for index in range(DEMO_DAYS):
        day = today - timedelta(days=DEMO_DAYS - 1 - index)
        if not start_date <= day <= end_date:
            continue
        remaining = (DEMO_DAYS - 1 - index) / (DEMO_DAYS - 1)
        # Small repeatable setbacks, with an exact endpoint and no refresh jitter.
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
        rows.append({
            "id": f"demo-rag-{day.isoformat()}",
            "dataset_name": f"[DEMO] {anchor.get('dataset_name') or 'Synthetic RAG monitoring'}",
            "dataset_hash": sha256(b"rag-monitoring-demo-v1").hexdigest(),
            "question_count": count, "completed_count": count,
            "started_at": timestamp, "evaluated_at": timestamp,
            "model_name": anchor.get("model_name") or "DEMO (no real baseline)",
            "prompt_version": anchor.get("prompt_version") or "demo",
            "evaluator_version": "rag-monitoring-demo-v1",
            "configuration": deepcopy(configuration),
            "average_latency_ms": anchor.get("average_latency_ms"),
            "summary_metrics": {"total": count, "top_k": source_summary.get("top_k"), **metrics},
            **metrics,
        })
    return list(reversed(rows))
