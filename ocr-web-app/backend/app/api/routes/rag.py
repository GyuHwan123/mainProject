import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.routes.auth import require_current_user
from app.models.user import User
from app.services.document_summary_service import get_or_create_document_summary
from app.services.rag_service import rag_service
from app.services.supabase_service import COMPANY_RAG_DOCUMENT_IDS, supabase_service
from app.services.pii_service import PRIVACY_RESPONSE, is_sensitive_query

router = APIRouter()

COMPANY_DOCUMENTS_DIR = (
    Path(__file__).resolve().parents[4]
    / "models" / "bge-m3" / "data" / "company_documents" / "documents"
)
COMPANY_DOCUMENT_CATALOG = COMPANY_DOCUMENTS_DIR.parent / "metadata" / "document_catalog.json"


@lru_cache(maxsize=1)
def _company_document_files() -> dict[str, str]:
    catalog = json.loads(COMPANY_DOCUMENT_CATALOG.read_text(encoding="utf-8"))
    allowed_ids = set(COMPANY_RAG_DOCUMENT_IDS)
    return {
        str(item["doc_id"]): str(item["filename"])
        for item in catalog.get("documents", [])
        if item.get("doc_id") in allowed_ids and item.get("filename")
    }


class RagSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4_000)
    rag_document_id: str | None = None
    limit: int = Field(default=5, ge=1, le=20)


@router.get("/documents")
def list_documents(user: User = Depends(require_current_user)) -> list[dict[str, Any]]:
    return supabase_service.list_rag_documents(user.email)


@router.delete("/documents/{rag_document_id}", status_code=204)
def delete_document(
    rag_document_id: str, user: User = Depends(require_current_user),
) -> None:
    supabase_service.delete_rag_document(user.email, rag_document_id)


@router.post("/documents/{rag_document_id}/summary")
async def summarize_document(
    rag_document_id: str, force_regenerate: bool = False,
    user: User = Depends(require_current_user),
) -> dict[str, Any]:
    return await get_or_create_document_summary(
        user.email, rag_document_id,
        user_role=user.role, subscription_tier=user.subscription_tier,
        force_regenerate=force_regenerate,
    )


@router.get("/company-documents/{doc_id}/file")
def get_company_document_file(
    doc_id: str, _user: User = Depends(require_current_user),
) -> FileResponse:
    filename = _company_document_files().get(doc_id)
    if not filename:
        raise HTTPException(status_code=404, detail="기업 공용문서를 찾을 수 없습니다.")
    documents_dir = COMPANY_DOCUMENTS_DIR.resolve()
    file_path = (documents_dir / filename).resolve()
    if file_path.parent != documents_dir or not file_path.is_file():
        raise HTTPException(status_code=404, detail="기업 공용문서 원본을 찾을 수 없습니다.")
    return FileResponse(
        file_path, media_type="application/pdf", filename=filename,
        content_disposition_type="inline",
    )


@router.post("/documents/{document_id}/index")
async def index_document(document_id: str, user: User = Depends(require_current_user)) -> dict[str, Any]:
    return await rag_service.index_document(user.email, document_id)


@router.post("/search")
async def search(payload: RagSearchRequest, user: User = Depends(require_current_user)) -> list[dict[str, Any]]:
    if is_sensitive_query(payload.query):
        raise HTTPException(status_code=403, detail=PRIVACY_RESPONSE)
    return await rag_service.search(
        user.email, payload.query.strip(), payload.rag_document_id, payload.limit,
        user_role=user.role, subscription_tier=user.subscription_tier,
    )
