from __future__ import annotations

import sys

from app.services.supabase_base import *

def _legacy_httpx():
    return sys.modules["app.services.supabase_service"].httpx

class IdentityMixin:
    def invalidate_password_reset_tokens(self, user_id: str, used_at: str) -> None:
        response = _legacy_httpx().patch(
            f"{self.url}/rest/v1/password_reset_tokens",
            params={"user_id": f"eq.{user_id}", "used_at": "is.null"},
            headers=self._service_headers(),
            json={"used_at": used_at},
            timeout=15,
        )
        self._raise_for_supabase(response, "비밀번호 재설정 토큰 무효화 실패")

    def create_password_reset_token(self, user_id: str, token_hash: str, expires_at: str) -> None:
        response = _legacy_httpx().post(
            f"{self.url}/rest/v1/password_reset_tokens",
            headers=self._service_headers(),
            json={"user_id": user_id, "token_hash": token_hash, "expires_at": expires_at},
            timeout=15,
        )
        self._raise_for_supabase(response, "비밀번호 재설정 토큰 생성 실패")

    def confirm_password_reset(self, token_hash: str, password_hash: str) -> bool:
        response = _legacy_httpx().post(
            f"{self.url}/rest/v1/rpc/confirm_password_reset",
            headers=self._service_headers(),
            json={"p_token_hash": token_hash, "p_password_hash": password_hash},
            timeout=15,
        )
        self._raise_for_supabase(response, "비밀번호 재설정 처리 실패")
        return response.json() is True

    def update_user_account(self, email: str, values: dict[str, Any]) -> dict[str, Any]:
        response = _legacy_httpx().patch(
            f"{self.url}/rest/v1/{self.users_table}", params={"email": f"eq.{email.lower()}"},
            headers={**self._service_headers(), "Prefer": "return=representation"}, json=values, timeout=15,
        )
        self._raise_for_supabase(response, "사용자 정보 수정 실패")
        rows = response.json()
        if not rows: raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
        return rows[0]

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        response = _legacy_httpx().get(
            f"{self.url}/rest/v1/{self.users_table}",
            params={"select": "*", "email": f"eq.{email.lower()}", "limit": "1"},
            headers=self._service_headers(),
            timeout=15,
        )
        self._raise_for_supabase(response, "Supabase 사용자 조회 실패")
        rows = response.json()
        return rows[0] if rows else None

    def create_user(
        self,
        *,
        name: str,
        email: str,
        password_hash: str | None,
        provider: str = "local",
        provider_id: str | None = None,
        role: str = "USER",
    ) -> dict[str, Any]:
        response = _legacy_httpx().post(
            f"{self.url}/rest/v1/{self.users_table}",
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json={
                "name": name,
                "email": email.lower(),
                "password_hash": password_hash,
                "social_provider": provider.lower(),
                "social_id": provider_id or email.lower(),
                "role": role,
            },
            timeout=15,
        )
        if response.status_code == 409:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 등록된 이메일입니다.")
        self._raise_for_supabase(response, "Supabase 사용자 생성 실패")
        return response.json()[0]

    def upsert_user(
        self,
        *,
        email: str,
        provider: str,
        provider_id: str | None = None,
        role: str = "USER",
        name: str | None = None,
    ) -> dict[str, Any]:
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
            response = _legacy_httpx().post(
                f"{self.url}/rest/v1/{self.users_table}",
                params={"on_conflict": "email"},
                headers={
                    **self._service_headers(),
                    "Prefer": "resolution=merge-duplicates,return=representation",
                },
                json={
                    "email": email,
                    **({"name": name} if name else {}),
                    "social_provider": provider.lower(),
                    # The Supabase schema requires social_id for every provider.
                    # Local accounts do not have an external ID, so email is stable.
                    "social_id": provider_id or email,
                    "role": role,
                },
                timeout=5,
            )
        except _legacy_httpx().RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Supabase 사용자 정보 저장 서버에 연결할 수 없습니다.",
            ) from exc

        if response.status_code not in (200, 201, 204):
            detail = response.text
            if "chk_users_social_provider" in detail:
                detail = (
                    "users.social_provider 제약조건에 'local'이 없습니다. "
                    "docs/01-supabase-enums.sql과 02-supabase-schema.sql을 순서대로 적용해 주세요."
                )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Supabase 사용자 정보 저장에 실패했습니다: {detail}",
            )
        rows = response.json() if response.content else []
        if rows:
            return rows[0]
        user = self.get_user_by_email(email)
        if not user:
            raise HTTPException(status_code=502, detail="Supabase 사용자 저장 결과를 확인할 수 없습니다.")
        return user

    def get_user_from_social_token(self, access_token: str) -> dict[str, Any]:
        """Validate a Supabase-brokered social token and normalize its identity."""
        if not self.url or not self.anon_key:
            raise HTTPException(status_code=503, detail="소셜 로그인 서버 설정이 필요합니다.")
        try:
            response = _legacy_httpx().get(
                f"{self.url}/auth/v1/user",
                headers={"Authorization": f"Bearer {access_token}", "apikey": self.anon_key},
                timeout=15,
            )
        except _legacy_httpx().RequestError as exc:
            raise HTTPException(status_code=502, detail="소셜 인증 서버에 연결할 수 없습니다.") from exc
        if response.status_code != 200:
            raise HTTPException(status_code=401, detail="소셜 로그인 토큰이 유효하지 않습니다.")

        data = response.json()
        metadata = data.get("user_metadata") or {}
        provider = data.get("app_metadata", {}).get("provider") or "social"
        email = data.get("email") or (metadata.get("email") if provider == "custom:naver" else None)
        return {
            "id": data.get("id"),
            "email": email,
            "name": metadata.get("full_name") or metadata.get("name") or metadata.get("nickname") or email,
            "provider": provider.removeprefix("custom:"),
        }

