"""Completed RAG runs, separate from receipt evaluation storage."""

import httpx


class RagEvaluationMixin:
    def save_rag_evaluation_run(self, user_email: str, payload: dict) -> None:
        user_id = self.get_public_user_id(user_email)
        response = httpx.post(
            f"{self.url}/rest/v1/rag_evaluation_runs",
            params={"on_conflict": "id"},
            headers={**self._service_headers(), "Prefer": "resolution=ignore-duplicates,return=minimal"},
            json={**payload, "user_id": user_id},
            timeout=20,
        )
        self._raise_for_supabase(response, "RAG 평가 실행 이력 저장 실패")
