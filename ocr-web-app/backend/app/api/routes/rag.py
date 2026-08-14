from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.auth import require_current_user
from app.models.user import User
from app.services.rag_service import rag_service
from app.services.supabase_service import supabase_service
from app.services.pii_service import PRIVACY_RESPONSE, is_sensitive_query

router = APIRouter()


class RagSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4_000)
    rag_document_id: str | None = None
    limit: int = Field(default=5, ge=1, le=20)


@router.get("/documents")
def list_documents(user: User = Depends(require_current_user)) -> list[dict[str, Any]]:
    return supabase_service.list_rag_documents(user.email)


@router.post("/documents/{document_id}/index")
async def index_document(document_id: str, user: User = Depends(require_current_user)) -> dict[str, Any]:
    return await rag_service.index_document(user.email, document_id)


@router.post("/search")
async def search(payload: RagSearchRequest, user: User = Depends(require_current_user)) -> list[dict[str, Any]]:
    if is_sensitive_query(payload.query):
        raise HTTPException(status_code=403, detail=PRIVACY_RESPONSE)
    return await rag_service.search(user.email, payload.query.strip(), payload.rag_document_id, payload.limit)
