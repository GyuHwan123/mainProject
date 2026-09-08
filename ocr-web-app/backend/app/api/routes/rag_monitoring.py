"""Read-only monitoring of persisted completed runs; never runs the evaluator."""

from datetime import date, datetime, time, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.rag_evaluations import require_developer
from app.models.user import User
from app.services.rag_evaluation_history import METRIC_KEYS
from app.services.supabase_service import supabase_service

router = APIRouter()
KST = timezone(timedelta(hours=9))


def summarize_runs(runs: list[dict]) -> dict:
    summary = {"total": sum(row["question_count"] for row in runs), "run_count": len(runs)}
    for key in (*METRIC_KEYS, "average_latency_ms", "hit_at_4"):
        values = [row.get(key, (row.get("summary_metrics") or {}).get(key)) for row in runs]
        values = [value for value in values if value is not None]
        summary[key] = sum(values) / len(values) if values else None
    top_ks = {(row.get("summary_metrics") or {}).get("top_k") for row in runs}
    summary["top_k"] = next(iter(top_ks)) if len(top_ks) == 1 else None
    return summary


@router.get("/evaluation/monitoring")
def rag_monitoring(start_date: date, end_date: date, user: User = Depends(require_developer),
                   demo: bool = False, demo_batch_id: str | None = None) -> dict:
    if end_date < start_date or (end_date - start_date).days > 365:
        raise HTTPException(status_code=422, detail="시작일~종료일은 최대 366일 범위로 선택하세요.")
    if demo and (not demo_batch_id or not demo_batch_id.strip()):
        raise HTTPException(status_code=422, detail="시연 이력 조회에는 demo_batch_id가 필요합니다.")
    rows = supabase_service.list_rag_evaluation_runs(
        user.email, datetime.combine(start_date, time.min, KST).isoformat(),
        datetime.combine(end_date + timedelta(days=1), time.min, KST).isoformat(),
    )
    runs = [row for row in rows if row.get("question_count", 0) > 0 and row.get("completed_count") == row.get("question_count")]
    recent_runs = list(runs)
    if demo:
        demo_rows = supabase_service.list_rag_evaluation_runs(
            user.email, datetime.combine(start_date, time.min, KST).isoformat(),
            datetime.combine(end_date + timedelta(days=1), time.min, KST).isoformat(),
            demo_batch_id=demo_batch_id,
        )
        demo_runs = [row for row in demo_rows if row.get("question_count", 0) > 0
                     and row.get("completed_count") == row.get("question_count")]
        actual_days = {
            datetime.fromisoformat(row["evaluated_at"].replace("Z", "+00:00")).astimezone(KST).date()
            for row in runs
        }
        # Select whole runs by Korean calendar day. Never fill missing metrics
        # on an actual evaluation with synthetic scores or average both sources.
        runs.extend(row for row in demo_runs if
                    datetime.fromisoformat(row["evaluated_at"].replace("Z", "+00:00")).astimezone(KST).date()
                    not in actual_days)
        recent_runs.extend(demo_runs)
        recent_runs.sort(key=lambda row: (
            datetime.fromisoformat(row["evaluated_at"].replace("Z", "+00:00")), row["id"]), reverse=True)
    daily = []
    grouped = {}
    for row in runs:
        day = datetime.fromisoformat(row["evaluated_at"].replace("Z", "+00:00")).astimezone(KST).date().isoformat()
        grouped.setdefault(day, []).append(row)
    day = start_date
    while day <= end_date:
        daily.append({"date": day.isoformat(), **summarize_runs(grouped.get(day.isoformat(), []))})
        day += timedelta(days=1)
    return {"start_date": start_date, "end_date": end_date, "summary": summarize_runs(runs),
            "daily": daily, "recent_runs": recent_runs[:50], "recent_run_count": len(recent_runs),
            "aggregation": "run_mean", "timezone": "Asia/Seoul"}
