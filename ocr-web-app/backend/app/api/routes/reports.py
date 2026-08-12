import re
from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from app.api.routes.auth import require_current_user
from app.models.user import User
from app.services.supabase_service import supabase_service

router = APIRouter()


class EvaluationCreate(BaseModel):
    document_id: str
    document_name: str
    extracted_text: str = Field(min_length=1)
    ground_truth: str = Field(min_length=1)
    processing_time_ms: float | None = Field(default=None, ge=0)


class EvaluationResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    document_name: str
    processing_time_ms: float | None
    precision: float
    recall: float
    f1_score: float
    true_positive: int
    false_positive: int
    false_negative: int
    created_at: datetime


def require_developer(user: User = Depends(require_current_user)) -> User:
    if user.role not in {"DEVELOPER", "ADMIN"}:
        raise HTTPException(status_code=403, detail="개발자 권한이 필요합니다.")
    return user


def score(prediction: str, truth: str) -> tuple[int, int, int, float, float, float]:
    tokens = lambda value: re.findall(r"[가-힣a-z0-9]+", value.lower())
    predicted, expected = Counter(tokens(prediction)), Counter(tokens(truth))
    tp = sum((predicted & expected).values())
    fp, fn = sum(predicted.values()) - tp, sum(expected.values()) - tp
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    return tp, fp, fn, precision, recall, f1


def character_error_rate(prediction: str, truth: str) -> float:
    predicted, expected = prediction.lower().strip(), truth.lower().strip()
    if not expected:
        return 0.0 if not predicted else 1.0
    previous = list(range(len(predicted) + 1))
    for row, expected_char in enumerate(expected, 1):
        current = [row]
        for column, predicted_char in enumerate(predicted, 1):
            current.append(min(current[-1] + 1, previous[column] + 1, previous[column - 1] + (expected_char != predicted_char)))
        previous = current
    return previous[-1] / len(expected)


@router.post("/evaluations", response_model=EvaluationResult)
def create_evaluation(payload: EvaluationCreate, user: User = Depends(require_developer)) -> EvaluationResult:
    tp, fp, fn, precision, recall, f1 = score(payload.extracted_text, payload.ground_truth)
    remote = supabase_service.save_ocr_evaluation(
        user_email=user.email,
        document_id=payload.document_id,
        confidence_score=precision,
        processing_time_ms=round(payload.processing_time_ms or 0),
        cer_score=character_error_rate(payload.extracted_text, payload.ground_truth),
        precision_score=precision,
        recall_score=recall,
    )
    return EvaluationResult(
        id=remote["id"], document_id=payload.document_id, document_name=payload.document_name,
        processing_time_ms=remote.get("processing_time_ms"), precision=precision, recall=recall,
        f1_score=f1, true_positive=tp, false_positive=fp, false_negative=fn,
        created_at=remote.get("evaluated_at"),
    )


@router.get("/evaluations", response_model=list[EvaluationResult])
def list_evaluations(user: User = Depends(require_developer)) -> list[EvaluationResult]:
    results = []
    for row in supabase_service.list_ocr_evaluations(user.email):
        precision, recall = row.get("precision_score") or 0, row.get("recall_score") or 0
        results.append(EvaluationResult(
            id=row["id"], document_id=row["document_id"], document_name=row["document_name"],
            processing_time_ms=row.get("processing_time_ms"), precision=precision, recall=recall,
            f1_score=(2 * precision * recall / (precision + recall)) if precision + recall else 0,
            true_positive=0, false_positive=0, false_negative=0, created_at=row["evaluated_at"],
        ))
    return results
