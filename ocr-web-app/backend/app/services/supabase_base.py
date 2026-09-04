from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import HTTPException, status

from app.core.config import settings
from app.services.finance_pipeline import FINANCE_PIPELINE_VERSION


COMPANY_RAG_DOCUMENT_IDS = (
    "HR-001", "HR-002", "HR-003", "HR-004", "HR-005",
    "GA-001", "GA-002", "GA-003", "GA-004",
    "IS-001", "IS-002",
    "SH-001", "SH-002", "SH-003", "SH-004",
    "ER-001", "ER-002", "ER-003",
)


class SupabaseBase:
    def __init__(self) -> None:
        self.url = settings.SUPABASE_URL.rstrip("/")
        self.anon_key = settings.SUPABASE_ANON_KEY
        self.service_role_key = settings.SUPABASE_SERVICE_ROLE_KEY
        self.users_table = settings.SUPABASE_USERS_TABLE
        self.ocr_documents_table = settings.SUPABASE_OCR_DOCUMENTS_TABLE
        self.documents_bucket = settings.SUPABASE_DOCUMENTS_BUCKET

    def _service_headers(self, *, json_content: bool = True) -> dict[str, str]:
        if not self.url or not self.service_role_key:
            raise HTTPException(status_code=503, detail="Supabase 서버 설정이 필요합니다.")
        headers = {"apikey": self.service_role_key}
        # Legacy anon/service-role keys are JWTs and may be sent as Bearer
        # credentials. New sb_publishable_/sb_secret_ keys are opaque API keys;
        # sending those in Authorization makes PostgREST try to parse them as
        # JWTs and reject the request (PGRST303).
        if self.service_role_key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {self.service_role_key}"
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    def _raise_for_supabase(self, response: httpx.Response, message: str) -> None:
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"{message}: {response.text}")

    def get_public_user_id(self, email: str) -> str:
        response = httpx.get(
            f"{self.url}/rest/v1/{self.users_table}",
            params={"select": "id", "email": f"eq.{email}", "limit": "1"},
            headers=self._service_headers(),
            timeout=15,
        )
        self._raise_for_supabase(response, "Supabase 사용자 조회 실패")
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=409, detail="Supabase users 테이블에서 사용자를 찾을 수 없습니다.")
        return rows[0]["id"]

    def get_subscription(self, user_email: str) -> dict[str, Any] | None:
        user_id = self.get_public_user_id(user_email)
        response = httpx.get(
            f"{self.url}/rest/v1/subscriptions",
            params={"select": "*", "user_id": f"eq.{user_id}", "limit": "1"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "구독 정보 조회 실패")
        rows = response.json()
        return rows[0] if rows else None

    def request_subscription_cancellation(self, user_email: str, reason: str | None) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        existing = self.get_subscription(user_email)
        now = datetime.now(timezone.utc)
        period_end = existing.get("current_period_end") if existing else None
        if not period_end:
            period_end = (now + timedelta(days=30)).isoformat()
        payload = {
            "user_id": user_id,
            "subscription_tier": "ENTERPRISE",
            "status": "CANCEL_SCHEDULED",
            "billing_provider": (existing or {}).get("billing_provider") or "MANUAL",
            "current_period_start": (existing or {}).get("current_period_start") or now.isoformat(),
            "current_period_end": period_end,
            "cancel_at_period_end": True,
            "cancellation_reason": (reason or "").strip()[:500] or None,
            "cancellation_requested_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        response = httpx.post(
            f"{self.url}/rest/v1/subscriptions",
            params={"on_conflict": "user_id"},
            headers={**self._service_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
            json=payload, timeout=15,
        )
        self._raise_for_supabase(response, "구독 취소 예약 실패")
        return response.json()[0]

    def revoke_subscription_cancellation(self, user_email: str) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.patch(
            f"{self.url}/rest/v1/subscriptions", params={"user_id": f"eq.{user_id}"},
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json={"status": "ACTIVE", "cancel_at_period_end": False, "cancellation_reason": None, "cancellation_requested_at": None, "updated_at": datetime.now(timezone.utc).isoformat()}, timeout=15,
        )
        self._raise_for_supabase(response, "구독 취소 철회 실패")
        rows = response.json()
        if not rows: raise HTTPException(status_code=404, detail="구독 정보를 찾을 수 없습니다.")
        return rows[0]

    def list_billing_history(self, user_email: str) -> list[dict[str, Any]]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.get(
            f"{self.url}/rest/v1/billing_history",
            params={"select": "*", "user_id": f"eq.{user_id}", "order": "paid_at.desc", "limit": "100"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "결제 이력 조회 실패")
        return response.json()

__all__ = [name for name in globals() if not name.startswith("__")]
