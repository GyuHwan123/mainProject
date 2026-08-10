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

        response = httpx.get(
            f"{self.url}/auth/v1/user",
            headers=headers,
            timeout=15,
        )

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
        }


supabase_service = SupabaseService()
