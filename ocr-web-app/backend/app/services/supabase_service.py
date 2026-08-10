from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.config import settings


class SupabaseService:
    def __init__(self) -> None:
        self.url = settings.SUPABASE_URL.rstrip("/")
        self.anon_key = settings.SUPABASE_ANON_KEY
        self.service_role_key = settings.SUPABASE_SERVICE_ROLE_KEY
        self.users_table = settings.SUPABASE_USERS_TABLE

    def upsert_user(
        self,
        *,
        email: str,
        provider: str,
        provider_id: str | None = None,
    ) -> None:
        """Create or update the public Supabase user row after a successful login."""
        if not self.url or not self.service_role_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Supabase 사용자 저장 설정이 필요합니다. "
                    "SUPABASE_URL과 SUPABASE_SERVICE_ROLE_KEY를 확인해 주세요."
                ),
            )

        try:
            response = httpx.post(
                f"{self.url}/rest/v1/{self.users_table}",
                params={"on_conflict": "email"},
                headers={
                    "Authorization": f"Bearer {self.service_role_key}",
                    "apikey": self.service_role_key,
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates,return=minimal",
                },
                json={
                    "email": email,
                    "social_provider": provider.lower(),
                    # The Supabase schema requires social_id for every provider.
                    # Local accounts do not have an external ID, so email is stable.
                    "social_id": provider_id or email,
                },
                timeout=15,
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Supabase 사용자 정보 저장 서버에 연결할 수 없습니다.",
            ) from exc

        if response.status_code not in (200, 201, 204):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Supabase 사용자 정보 저장에 실패했습니다: {response.text}",
            )

    def get_user_from_token(self, access_token: str) -> dict[str, Any]:
        if not self.url or not self.anon_key:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Supabase 환경 변수가 아직 설정되지 않았습니다. SUPABASE_URL, SUPABASE_ANON_KEY를 입력해주세요.",
            )

        headers = {
            "Authorization": f"Bearer {access_token}",
            "apikey": self.anon_key,
        }

        try:
            response = httpx.get(
                f"{self.url}/auth/v1/user",
                headers=headers,
                timeout=15,
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Supabase 인증 서버에 연결할 수 없습니다.",
            ) from exc

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Supabase 인증 토큰이 유효하지 않습니다.",
            )

        data = response.json()
        return {
            "id": data.get("id"),
            "email": data.get("email"),
            "name": data.get("user_metadata", {}).get("full_name") or data.get("email"),
            "provider": data.get("app_metadata", {}).get("provider") or "supabase",
        }


supabase_service = SupabaseService()
