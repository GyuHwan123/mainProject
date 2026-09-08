"""Read-only monitoring of persisted completed runs; never runs the evaluator."""

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.rag_evaluations import require_developer
from app.models.user import User
from app.services.rag_evaluation_history import METRIC_KEYS
from app.services.rag_demo_baseline import BASELINE_ID
from app.services.supabase_service import supabase_service

router = APIRouter()
KST = timezone(timedelta(hours=9))


def summarize_runs(runs: list[dict]) -> dict:
    summary = {"total": sum(row["question_count"] for row in runs), "run_count": len(runs)}
    for key in (*METRIC_KEYS, "average_latency_ms", "hit_at_4", "citation_accuracy", "source_accuracy", "answer_relevance"):
        values = [row.get(key, (row.get("summary_metrics") or {}).get(key)) for row in runs]
        values = [value for value in values if value is not None]
        summary[key] = sum(values) / len(values) if values else None
    top_ks = {(row.get("summary_metrics") or {}).get("top_k") for row in runs}
    summary["top_k"] = next(iter(top_ks)) if len(top_ks) == 1 else None
    return summary


@router.get("/evaluation/monitoring")
def rag_monitoring(start_date: date, end_date: date, user: User = Depends(require_developer),
                   demo: bool = False) -> dict:
    if end_date < start_date or (end_date - start_date).days > 365:
        raise HTTPException(status_code=422, detail="시작일~종료일은 최대 366일 범위로 선택하세요.")
    start_at = datetime.combine(start_date, time.min, KST).isoformat()
    end_at = datetime.combine(end_date + timedelta(days=1), time.min, KST).isoformat()
    actual = supabase_service.list_rag_evaluation_runs(user.email, start_at, end_at)

    def completed(row):
        return row.get("question_count", 0) > 0 and row.get("completed_count") == row.get("question_count")

    def run_day(row):
        return datetime.fromisoformat(row["evaluated_at"].replace("Z", "+00:00")).astimezone(KST).date()

    actual = [row for row in actual if completed(row) and start_date <= run_day(row) <= end_date]
    baseline = []
    if demo or not actual:
        baseline = supabase_service.list_rag_evaluation_runs(
            user.email, start_at, end_at, demo_batch_id=BASELINE_ID,
        )
        baseline = [row for row in baseline if completed(row) and start_date <= run_day(row) <= end_date]
    # Preserve the existing demo trend, while actual runs win on their own days.
    actual_days = {run_day(row) for row in actual}
    runs = actual + [row for row in baseline if run_day(row) not in actual_days]
    runs.sort(key=lambda row: (datetime.fromisoformat(row["evaluated_at"].replace("Z", "+00:00")), row["id"]), reverse=True)
    # Full question payloads are fetched only for a selected execution.
    runs = [{**row, "summary_metrics": {
        key: value for key, value in (row.get("summary_metrics") or {}).items()
        if key != "evaluation_result"
    }} for row in runs]
    grouped = {}
    for row in runs:
        grouped.setdefault(run_day(row).isoformat(), []).append(row)
    daily = []
    day = start_date
    while day <= end_date:
        daily.append({"date": day.isoformat(), **summarize_runs(grouped.get(day.isoformat(), []))})
        day += timedelta(days=1)
    return {"start_date": start_date, "end_date": end_date, "summary": summarize_runs(runs),
            "daily": daily, "recent_runs": runs, "recent_run_count": len(runs),
            "data_source": "database" if actual else "demo" if baseline else "empty",
            "baseline_runs": baseline, "aggregation": "run_mean", "timezone": "Asia/Seoul"}


@router.get("/evaluation/history/{run_id}")
def rag_evaluation_detail(run_id: UUID, user: User = Depends(require_developer)) -> dict:
    run = supabase_service.get_rag_evaluation_run(user.email, str(run_id))
    if not run:
        raise HTTPException(status_code=404, detail="RAG evaluation run not found")
    return run
