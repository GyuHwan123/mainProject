from __future__ import annotations

import sys

from app.services.supabase_base import *

def _legacy_httpx():
    return sys.modules["app.services.supabase_service"].httpx

class CollaborationMixin:
    def list_chat_sessions(self, user_email: str) -> list[dict[str, Any]]:
        user_id = self.get_public_user_id(user_email)
        response = _legacy_httpx().get(
            f"{self.url}/rest/v1/chat_sessions",
            params={
                "select": "*", "user_id": f"eq.{user_id}", "deleted_at": "is.null",
                "order": "created_at.desc", "limit": "100",
            },
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "채팅 기록 조회 실패")
        return response.json()

    def create_chat_session(self, user_email: str, title: str, document_id: str | None) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        payload: dict[str, Any] = {"user_id": user_id, "group_id": None, "title": title[:120]}
        if document_id is not None:
            payload["document_id"] = document_id
        response = _legacy_httpx().post(
            f"{self.url}/rest/v1/chat_sessions",
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json=payload, timeout=15,
        )
        self._raise_for_supabase(response, "채팅 세션 생성 실패")
        return response.json()[0]

    def get_chat_session(self, user_email: str, session_id: str) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        response = _legacy_httpx().get(
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
        response = _legacy_httpx().get(
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
        response = _legacy_httpx().post(
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
        session = self.get_chat_session(user_email, session_id)
        response = _legacy_httpx().patch(
            f"{self.url}/rest/v1/chat_sessions",
            params={"id": f"eq.{session_id}", "user_id": f"eq.{session['user_id']}"},
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json={"deleted_at": datetime.now(timezone.utc).isoformat()}, timeout=15,
        )
        self._raise_for_supabase(response, "채팅 세션 삭제 실패")
        if not response.json():
            raise HTTPException(status_code=404, detail="채팅 세션을 찾을 수 없습니다.")

    def list_knowledge_scraps(self, user_email: str) -> list[dict[str, Any]]:
        user_id = self.get_public_user_id(user_email)
        response = _legacy_httpx().get(
            f"{self.url}/rest/v1/knowledge_scraps",
            params={"select": "*", "user_id": f"eq.{user_id}", "order": "created_at.desc"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "지식 바구니 조회 실패")
        return response.json()

    def create_knowledge_scrap(self, user_email: str, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        response = _legacy_httpx().post(
            f"{self.url}/rest/v1/knowledge_scraps",
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json={**payload, "user_id": user_id}, timeout=15,
        )
        self._raise_for_supabase(response, "지식 카드 저장 실패")
        return response.json()[0]

    def delete_knowledge_scrap(self, user_email: str, scrap_id: str) -> None:
        user_id = self.get_public_user_id(user_email)
        response = _legacy_httpx().delete(
            f"{self.url}/rest/v1/knowledge_scraps",
            params={"id": f"eq.{scrap_id}", "user_id": f"eq.{user_id}"},
            headers={**self._service_headers(), "Prefer": "return=representation"}, timeout=15,
        )
        self._raise_for_supabase(response, "지식 카드 삭제 실패")
        if not response.json():
            raise HTTPException(status_code=404, detail="지식 카드를 찾을 수 없습니다.")

    def list_rag_documents(self, user_email: str) -> list[dict[str, Any]]:
        response = _legacy_httpx().get(
            f"{self.url}/rest/v1/rag_documents",
            params={"select": "*,rag_chunks(count)", "owner": f"eq.{user_email}", "order": "created_at.desc"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "RAG 문서 조회 실패")
        rows = response.json()
        for row in rows:
            chunk_counts = row.pop("rag_chunks", []) or []
            row["chunk_count"] = int(chunk_counts[0].get("count", 0)) if chunk_counts else 0
            # Compatibility aliases consumed by the existing chat page.
            row["document_id"] = row.get("doc_id")
            row["file_name"] = row.get("filename") or row.get("title")
            row["status"] = "RAG_READY" if row["chunk_count"] else "EMPTY"
        return rows

    def delete_rag_document(self, user_email: str, rag_document_id: str) -> None:
        owned_document = _legacy_httpx().get(
            f"{self.url}/rest/v1/rag_documents",
            params={
                "select": "id",
                "id": f"eq.{rag_document_id}",
                "owner": f"eq.{user_email}",
                "limit": "1",
            },
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(owned_document, "RAG 문서 삭제 권한 확인 실패")
        if not owned_document.json():
            raise HTTPException(status_code=404, detail="RAG 문서를 찾을 수 없습니다.")

        response = _legacy_httpx().delete(
            f"{self.url}/rest/v1/rag_documents",
            params={"id": f"eq.{rag_document_id}", "owner": f"eq.{user_email}"},
            headers={**self._service_headers(), "Prefer": "return=representation"},
            timeout=15,
        )
        self._raise_for_supabase(response, "RAG 문서 삭제 실패")
        if not response.json():
            raise HTTPException(status_code=404, detail="RAG 문서를 찾을 수 없습니다.")

    def replace_rag_index(
        self, *, user_email: str, document: dict[str, Any], chunks: list[dict[str, Any]],
        embeddings: list[list[float]], embedding_model: str,
    ) -> dict[str, Any]:
        filename = document.get("file_name") or "document"
        upsert = _legacy_httpx().post(
            f"{self.url}/rest/v1/rag_documents",
            params={"on_conflict": "doc_id"},
            headers={**self._service_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
            json={
                "doc_id": document["id"],
                "title": Path(filename).stem,
                "owner": user_email,
                "security": "PRIVATE",
                "version": "v1.0",
                "effective_date": datetime.now(timezone.utc).date().isoformat(),
                "filename": filename,
                "tags": ["RAG", embedding_model],
            },
            timeout=15,
        )
        self._raise_for_supabase(upsert, "RAG 문서 연결 실패")
        rag_document = upsert.json()[0]
        delete = _legacy_httpx().delete(
            f"{self.url}/rest/v1/rag_chunks", params={"document_id": f"eq.{rag_document['id']}"},
            headers=self._service_headers(), timeout=30,
        )
        self._raise_for_supabase(delete, "기존 RAG 청크 삭제 실패")
        rows = [{
            "document_id": rag_document["id"],
            "chunk_index": index, "page_number": chunk["page_number"], "content": chunk["content"],
            "embedding": embedding,
        } for index, (chunk, embedding) in enumerate(zip(chunks, embeddings))]
        for start in range(0, len(rows), 50):
            insert = _legacy_httpx().post(
                f"{self.url}/rest/v1/rag_chunks", headers=self._service_headers(),
                json=rows[start:start + 50], timeout=60,
            )
            self._raise_for_supabase(insert, "RAG 청크 저장 실패")
        rag_document["document_id"] = rag_document["doc_id"]
        rag_document["file_name"] = rag_document["filename"]
        rag_document["status"] = "RAG_READY" if rows else "EMPTY"
        rag_document["chunk_count"] = len(rows)
        return rag_document

    def mark_rag_failed(self, user_email: str, document_id: str, message: str) -> None:
        # The current rag_documents schema has no status or error column.
        # Keep the failure local to the request instead of issuing an invalid DB write.
        return None

    def search_rag_chunks(
        self, user_email: str, embedding: list[float], rag_document_id: str | None, limit: int, *,
        include_company_documents: bool = True,
    ) -> list[dict[str, Any]]:
        personal_documents = self.list_rag_documents(user_email)
        company_documents: list[dict[str, Any]] = []
        if include_company_documents:
            company_response = _legacy_httpx().get(
                f"{self.url}/rest/v1/rag_documents",
                params={
                    "select": "id,doc_id,title,owner,filename",
                    "doc_id": f"in.({','.join(COMPANY_RAG_DOCUMENT_IDS)})",
                },
                headers=self._service_headers(), timeout=15,
            )
            self._raise_for_supabase(company_response, "RAG company documents lookup failed")
            company_documents = company_response.json()
        personal_document_by_id = {row["id"]: row for row in personal_documents}
        company_document_by_id = {row["id"]: row for row in company_documents}
        accessible_document_by_id = {**personal_document_by_id, **company_document_by_id}
        if rag_document_id and rag_document_id not in accessible_document_by_id:
            raise HTTPException(status_code=404, detail="RAG 문서를 찾을 수 없습니다.")
        document_by_id = dict(company_document_by_id)
        if rag_document_id:
            document_by_id[rag_document_id] = accessible_document_by_id[rag_document_id]
        allowed_document_ids = list(document_by_id)
        if not allowed_document_ids:
            return []
        response = _legacy_httpx().post(
            f"{self.url}/rest/v1/rpc/match_rag_chunks", headers=self._service_headers(),
            json={
                "query_embedding": embedding,
                "allowed_document_ids": allowed_document_ids,
                "match_threshold": 0.2,
                "match_count": limit,
            },
            timeout=30,
        )
        self._raise_for_supabase(response, "RAG 벡터 검색 실패")
        rows = [
            row for row in response.json()
            if row.get("document_id") in document_by_id
        ][:limit]
        for row in rows:
            rag_id = row["document_id"]
            document = document_by_id[rag_id]
            row["rag_document_id"] = rag_id
            row["document_id"] = document["doc_id"]
            row["source"] = document["filename"]
            row["bbox"] = None
        return rows

    def list_rag_document_catalog(self) -> list[dict[str, Any]]:
        response = _legacy_httpx().get(
            f"{self.url}/rest/v1/rag_documents",
            params={"select": "doc_id,title,filename", "order": "created_at.asc"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "RAG 문서 카탈로그 조회 실패")
        return response.json()

    def get_accessible_rag_document(
        self, user_email: str, rag_document_id: str, *, include_company_documents: bool = False,
    ) -> dict[str, Any]:
        response = _legacy_httpx().get(
            f"{self.url}/rest/v1/rag_documents",
            params={
                "select": "id,doc_id,title,owner,filename,summary",
                "id": f"eq.{rag_document_id}",
                "limit": "1",
            },
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "RAG 문서 접근 권한 확인 실패")
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=404, detail="RAG 문서를 찾을 수 없습니다.")
        document = rows[0]
        is_owned = str(document.get("owner") or "").lower() == user_email.lower()
        is_company = include_company_documents and document.get("doc_id") in COMPANY_RAG_DOCUMENT_IDS
        if not (is_owned or is_company):
            raise HTTPException(status_code=404, detail="RAG 문서를 찾을 수 없습니다.")
        return document

    def list_all_rag_chunks(self, rag_document_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_size = 1_000
        while True:
            response = _legacy_httpx().get(
                f"{self.url}/rest/v1/rag_chunks",
                params={
                    "select": "id,document_id,chunk_index,page_number,content",
                    "document_id": f"eq.{rag_document_id}",
                    "order": "chunk_index.asc",
                    "limit": str(page_size),
                    "offset": str(len(rows)),
                },
                headers=self._service_headers(), timeout=30,
            )
            self._raise_for_supabase(response, "RAG 문서 전체 청크 조회 실패")
            page = response.json()
            rows.extend(page)
            if len(page) < page_size:
                return rows

    def save_rag_document_summary(self, rag_document_id: str, summary: str) -> None:
        response = _legacy_httpx().patch(
            f"{self.url}/rest/v1/rag_documents",
            params={"id": f"eq.{rag_document_id}"},
            headers={**self._service_headers(), "Prefer": "return=minimal"},
            json={"summary": summary}, timeout=15,
        )
        self._raise_for_supabase(response, "RAG 문서 요약 저장 실패")

    def list_rag_chunks(self, user_email: str, rag_document_id: str) -> list[dict[str, Any]]:
        owned_document = next(
            (item for item in self.list_rag_documents(user_email) if item.get("id") == rag_document_id),
            None,
        )
        if not owned_document:
            raise HTTPException(status_code=404, detail="RAG 문서를 찾을 수 없습니다.")
        response = _legacy_httpx().get(
            f"{self.url}/rest/v1/rag_chunks",
            params={
                "select": "id,document_id,chunk_index,page_number,content",
                "document_id": f"eq.{rag_document_id}",
                "order": "chunk_index.asc",
                "limit": "5000",
            },
            headers=self._service_headers(),
            timeout=30,
        )
        self._raise_for_supabase(response, "RAG 문서 전체 청크 조회 실패")
        rows = response.json()
        for row in rows:
            row["rag_document_id"] = rag_document_id
            row["document_id"] = owned_document["doc_id"]
            row["source"] = owned_document["filename"]
            row["bbox"] = None
        return rows


