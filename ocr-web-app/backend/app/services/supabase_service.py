from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import HTTPException, status

from app.core.config import settings


class SupabaseService:
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
        headers = {
            "Authorization": f"Bearer {self.service_role_key}",
            "apikey": self.service_role_key,
        }
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

    def save_ocr_document(
        self,
        *,
        user_email: str,
        filename: str,
        mime_type: str,
        content: bytes,
        content_type: str,
        pages: list[dict[str, Any]],
        upload_origin: str = "OCR",
    ) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        safe_suffix = Path(filename).suffix.lower()
        storage_path = f"{user_id}/{uuid4().hex}{safe_suffix}"
        encoded_path = quote(storage_path, safe="/")
        upload = httpx.post(
            f"{self.url}/storage/v1/object/{self.documents_bucket}/{encoded_path}",
            headers={**self._service_headers(json_content=False), "Content-Type": mime_type, "x-upsert": "false"},
            content=content,
            timeout=60,
        )
        self._raise_for_supabase(upload, "Supabase Storage 업로드 실패")

        payload = {
            "user_id": user_id,
            "group_id": None,
            "file_name": filename,
            "file_url": storage_path,
            "extracted_text": "\n\n".join(page.get("text", "") for page in pages),
            "summary_text": None,
            "translated_text": None,
            "bounding_boxes": pages,
            "status": "COMPLETED",
            "upload_origin": upload_origin,
        }
        insert = httpx.post(
            f"{self.url}/rest/v1/{self.ocr_documents_table}",
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json=payload,
            timeout=15,
        )
        if insert.status_code >= 400:
            httpx.delete(
                f"{self.url}/storage/v1/object/{self.documents_bucket}/{encoded_path}",
                headers=self._service_headers(json_content=False),
                timeout=15,
            )
            self._raise_for_supabase(insert, "ocr_documents 저장 실패")
        return insert.json()[0]

    def list_ocr_documents(self, user_email: str, upload_origin: str | None = None) -> list[dict[str, Any]]:
        user_id = self.get_public_user_id(user_email)
        params = {"select": "id,file_name,file_url,status,created_at", "user_id": f"eq.{user_id}", "order": "created_at.desc", "limit": "100"}
        if upload_origin:
            params["upload_origin"] = f"eq.{upload_origin}"
        response = httpx.get(
            f"{self.url}/rest/v1/{self.ocr_documents_table}",
            params=params,
            headers=self._service_headers(),
            timeout=15,
        )
        self._raise_for_supabase(response, "문서 히스토리 조회 실패")
        return response.json()

    def get_ocr_document(self, user_email: str, document_id: str) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.get(
            f"{self.url}/rest/v1/{self.ocr_documents_table}",
            params={"select": "*", "id": f"eq.{document_id}", "user_id": f"eq.{user_id}", "limit": "1"},
            headers=self._service_headers(),
            timeout=15,
        )
        self._raise_for_supabase(response, "문서 조회 실패")
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
        return rows[0]

    def save_ocr_evaluation(
        self,
        *,
        user_email: str,
        document_id: str,
        confidence_score: float,
        processing_time_ms: int,
        cer_score: float,
        precision_score: float,
        recall_score: float,
    ) -> dict[str, Any]:
        # Ownership check prevents a developer from evaluating another user's document.
        self.get_ocr_document(user_email, document_id)
        response = httpx.post(
            f"{self.url}/rest/v1/ocr_evaluations",
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json={
                "document_id": document_id,
                "confidence_score": confidence_score,
                "processing_time_ms": processing_time_ms,
                "cer_score": cer_score,
                "precision_score": precision_score,
                "recall_score": recall_score,
            },
            timeout=15,
        )
        self._raise_for_supabase(response, "OCR 평가 결과 저장 실패")
        return response.json()[0]

    def list_ocr_evaluations(self, user_email: str) -> list[dict[str, Any]]:
        documents = self.list_ocr_documents(user_email)
        if not documents:
            return []
        names = {str(document["id"]): document["file_name"] for document in documents}
        document_filter = ",".join(names)
        response = httpx.get(
            f"{self.url}/rest/v1/ocr_evaluations",
            params={"select": "*", "document_id": f"in.({document_filter})", "order": "evaluated_at.desc"},
            headers=self._service_headers(),
            timeout=15,
        )
        self._raise_for_supabase(response, "OCR 평가 결과 조회 실패")
        rows = response.json()
        for row in rows:
            row["document_name"] = names.get(str(row["document_id"]), "알 수 없는 문서")
        return rows

    def create_document_signed_url(self, storage_path: str) -> str:
        encoded_path = quote(storage_path, safe="/")
        response = httpx.post(
            f"{self.url}/storage/v1/object/sign/{self.documents_bucket}/{encoded_path}",
            headers=self._service_headers(),
            json={"expiresIn": 3600},
            timeout=15,
        )
        self._raise_for_supabase(response, "원본 파일 URL 생성 실패")
        signed_url = response.json().get("signedURL") or response.json().get("signedUrl")
        return f"{self.url}/storage/v1{signed_url}"

    def download_document(self, storage_path: str) -> tuple[bytes, str]:
        encoded_path = quote(storage_path, safe="/")
        response = httpx.get(
            f"{self.url}/storage/v1/object/{self.documents_bucket}/{encoded_path}",
            headers=self._service_headers(json_content=False),
            timeout=60,
        )
        self._raise_for_supabase(response, "원본 파일 다운로드 실패")
        return response.content, response.headers.get("content-type", "application/octet-stream")

    def list_chat_sessions(self, user_email: str) -> list[dict[str, Any]]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.get(
            f"{self.url}/rest/v1/chat_sessions",
            params={"select": "*", "user_id": f"eq.{user_id}", "order": "created_at.desc", "limit": "100"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "채팅 기록 조회 실패")
        return response.json()

    def create_chat_session(self, user_email: str, title: str, document_id: str) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.post(
            f"{self.url}/rest/v1/chat_sessions",
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json={"user_id": user_id, "document_id": document_id, "group_id": None, "title": title[:120]}, timeout=15,
        )
        self._raise_for_supabase(response, "채팅 세션 생성 실패")
        return response.json()[0]

    def get_chat_session(self, user_email: str, session_id: str) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.get(
            f"{self.url}/rest/v1/chat_sessions",
            params={"select": "*", "id": f"eq.{session_id}", "user_id": f"eq.{user_id}", "limit": "1"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "채팅 세션 조회 실패")
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=404, detail="채팅 세션을 찾을 수 없습니다.")
        return rows[0]

    def list_chat_messages(self, user_email: str, session_id: str) -> list[dict[str, Any]]:
        self.get_chat_session(user_email, session_id)
        response = httpx.get(
            f"{self.url}/rest/v1/chat_messages",
            params={"select": "*", "session_id": f"eq.{session_id}", "order": "created_at.asc"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "채팅 메시지 조회 실패")
        return [{
            **row,
            "role": str(row.get("sender", "")).lower(),
            "content": row.get("message", ""),
            "sources": row.get("top_k_chunks") or [],
            "model_name": None,
        } for row in response.json()]

    def save_chat_message(
        self, *, user_email: str, session_id: str, role: str, content: str,
        sources: list[dict[str, Any]] | None = None, model_name: str | None = None,
    ) -> dict[str, Any]:
        self.get_chat_session(user_email, session_id)
        response = httpx.post(
            f"{self.url}/rest/v1/chat_messages",
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json={
                "id": uuid4().int & ((1 << 63) - 1),
                "session_id": session_id,
                "sender": role.upper(),
                "message": content,
                "top_k_chunks": sources or [],
            },
            timeout=15,
        )
        self._raise_for_supabase(response, "채팅 메시지 저장 실패")
        row = response.json()[0]
        return {
            **row,
            "role": str(row.get("sender", "")).lower(),
            "content": row.get("message", ""),
            "sources": row.get("top_k_chunks") or [],
            "model_name": model_name,
        }

    def delete_chat_session(self, user_email: str, session_id: str) -> None:
        self.get_chat_session(user_email, session_id)
        response = httpx.delete(
            f"{self.url}/rest/v1/chat_sessions", params={"id": f"eq.{session_id}"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "채팅 세션 삭제 실패")

    def list_knowledge_scraps(self, user_email: str) -> list[dict[str, Any]]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.get(
            f"{self.url}/rest/v1/knowledge_scraps",
            params={"select": "*", "user_id": f"eq.{user_id}", "order": "created_at.desc"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "지식 바구니 조회 실패")
        return response.json()

    def create_knowledge_scrap(self, user_email: str, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.post(
            f"{self.url}/rest/v1/knowledge_scraps",
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json={**payload, "user_id": user_id}, timeout=15,
        )
        self._raise_for_supabase(response, "지식 카드 저장 실패")
        return response.json()[0]

    def delete_knowledge_scrap(self, user_email: str, scrap_id: str) -> None:
        user_id = self.get_public_user_id(user_email)
        response = httpx.delete(
            f"{self.url}/rest/v1/knowledge_scraps",
            params={"id": f"eq.{scrap_id}", "user_id": f"eq.{user_id}"},
            headers={**self._service_headers(), "Prefer": "return=representation"}, timeout=15,
        )
        self._raise_for_supabase(response, "지식 카드 삭제 실패")
        if not response.json():
            raise HTTPException(status_code=404, detail="지식 카드를 찾을 수 없습니다.")

    def list_rag_documents(self, user_email: str) -> list[dict[str, Any]]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.get(
            f"{self.url}/rest/v1/rag_documents",
            params={"select": "*,ocr_documents(file_name,created_at)", "user_id": f"eq.{user_id}", "order": "updated_at.desc"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "RAG 문서 조회 실패")
        return response.json()

    def replace_rag_index(
        self, *, user_email: str, document: dict[str, Any], chunks: list[dict[str, Any]],
        embeddings: list[list[float]], embedding_model: str,
    ) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        upsert = httpx.post(
            f"{self.url}/rest/v1/rag_documents",
            params={"on_conflict": "user_id,document_id"},
            headers={**self._service_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
            json={"user_id": user_id, "document_id": document["id"], "status": "INDEXING", "embedding_model": embedding_model, "chunk_count": 0, "error_message": None, "updated_at": datetime.now(timezone.utc).isoformat()},
            timeout=15,
        )
        self._raise_for_supabase(upsert, "RAG 문서 연결 실패")
        rag_document = upsert.json()[0]
        delete = httpx.delete(
            f"{self.url}/rest/v1/rag_chunks", params={"rag_document_id": f"eq.{rag_document['id']}"},
            headers=self._service_headers(), timeout=30,
        )
        self._raise_for_supabase(delete, "기존 RAG 청크 삭제 실패")
        rows = [{
            "rag_document_id": rag_document["id"], "document_id": document["id"], "user_id": user_id,
            "chunk_index": index, "page_number": chunk["page_number"], "content": chunk["content"],
            "bbox": chunk["bbox"], "embedding": embedding,
        } for index, (chunk, embedding) in enumerate(zip(chunks, embeddings))]
        for start in range(0, len(rows), 50):
            insert = httpx.post(
                f"{self.url}/rest/v1/rag_chunks", headers=self._service_headers(),
                json=rows[start:start + 50], timeout=60,
            )
            self._raise_for_supabase(insert, "RAG 청크 저장 실패")
        update = httpx.patch(
            f"{self.url}/rest/v1/rag_documents", params={"id": f"eq.{rag_document['id']}"},
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json={"status": "RAG_READY", "chunk_count": len(rows), "updated_at": datetime.now(timezone.utc).isoformat()}, timeout=15,
        )
        self._raise_for_supabase(update, "RAG 상태 갱신 실패")
        result = update.json()[0]
        result["file_name"] = document.get("file_name")
        return result

    def mark_rag_failed(self, user_email: str, document_id: str, message: str) -> None:
        user_id = self.get_public_user_id(user_email)
        response = httpx.post(
            f"{self.url}/rest/v1/rag_documents", params={"on_conflict": "user_id,document_id"},
            headers={**self._service_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"user_id": user_id, "document_id": document_id, "status": "FAILED", "chunk_count": 0, "error_message": message[:1000], "updated_at": datetime.now(timezone.utc).isoformat()},
            timeout=15,
        )
        self._raise_for_supabase(response, "RAG 실패 상태 저장 실패")

    def search_rag_chunks(
        self, user_email: str, embedding: list[float], rag_document_id: str | None, limit: int,
    ) -> list[dict[str, Any]]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.post(
            f"{self.url}/rest/v1/rpc/match_rag_chunks", headers=self._service_headers(),
            json={"query_embedding": embedding, "filter_user_id": user_id, "filter_rag_document_id": rag_document_id, "match_threshold": 0.2, "match_count": limit},
            timeout=30,
        )
        self._raise_for_supabase(response, "RAG 벡터 검색 실패")
        rows = response.json()
        document_ids = {row["document_id"] for row in rows}
        names: dict[str, str] = {}
        for document_id in document_ids:
            document = self.get_ocr_document(user_email, document_id)
            names[document_id] = document["file_name"]
        for row in rows:
            row["source"] = names.get(row["document_id"], "문서")
        return rows

    def list_rag_chunks(self, user_email: str, rag_document_id: str) -> list[dict[str, Any]]:
        owned_document = next(
            (item for item in self.list_rag_documents(user_email) if item.get("id") == rag_document_id),
            None,
        )
        if not owned_document:
            raise HTTPException(status_code=404, detail="RAG 문서를 찾을 수 없습니다.")
        response = httpx.get(
            f"{self.url}/rest/v1/rag_chunks",
            params={
                "select": "id,document_id,rag_document_id,chunk_index,page_number,content,bbox",
                "rag_document_id": f"eq.{rag_document_id}",
                "order": "chunk_index.asc",
                "limit": "5000",
            },
            headers=self._service_headers(),
            timeout=30,
        )
        self._raise_for_supabase(response, "RAG 문서 전체 청크 조회 실패")
        rows = response.json()
        if rows:
            document = self.get_ocr_document(user_email, rows[0]["document_id"])
            for row in rows:
                row["source"] = document["file_name"]
        return rows

    def upsert_user(
        self,
        *,
        email: str,
        provider: str,
        provider_id: str | None = None,
        role: str = "USER",
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
                    "role": role,
                },
                timeout=5,
            )
        except httpx.RequestError as exc:
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
