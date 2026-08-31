from __future__ import annotations

import httpx

from app.services.supabase_base import COMPANY_RAG_DOCUMENT_IDS, SupabaseBase
from app.services.supabase_collaboration_repository import CollaborationMixin
from app.services.supabase_document_finance_repository import DocumentFinanceMixin
from app.services.supabase_identity_repository import IdentityMixin


class SupabaseService(IdentityMixin, CollaborationMixin, DocumentFinanceMixin, SupabaseBase):
    """Compatibility facade composed from domain-specific repositories."""


supabase_service = SupabaseService()
