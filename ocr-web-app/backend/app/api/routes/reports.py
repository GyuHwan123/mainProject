from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class ReportSummary(BaseModel):
    document_name: str
    accuracy: float
    recall: float
    embedding_similarity: float
    status: str = "completed"


@router.get("", response_model=list[ReportSummary])
def list_reports() -> list[ReportSummary]:
    return [
        ReportSummary(
            document_name="프로젝트 발표자료.pdf",
            accuracy=0.964,
            recall=0.932,
            embedding_similarity=0.91,
        ),
        ReportSummary(
            document_name="영수증_0527.pdf",
            accuracy=0.941,
            recall=0.918,
            embedding_similarity=0.87,
        ),
    ]


@router.get("/similar")
def similar_reports() -> list[str]:
    return ["프로젝트_4인_발표.pdf", "기획안_요약본.pdf", "회의록_2026.pdf"]
