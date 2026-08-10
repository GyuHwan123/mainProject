from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.config import settings


class FirebaseService:
    def __init__(self) -> None:
        self.project_id = settings.FIREBASE_PROJECT_ID
        self.api_key = settings.FIREBASE_API_KEY

    def verify_id_token(self, id_token: str) -> dict[str, Any]:
        if not self.project_id or not self.api_key:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Firebase 설정이 아직 구성되지 않았습니다. FIREBASE_PROJECT_ID와 FIREBASE_API_KEY를 환경 변수에 설정해주세요.",
            )

        url = f"https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={self.api_key}"
        payload = {"idToken": id_token}

        response = httpx.post(url, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()

        users = data.get("users") or []
        if not users:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="유효하지 않은 Firebase 토큰입니다.",
            )

        return users[0]


firebase_service = FirebaseService()
