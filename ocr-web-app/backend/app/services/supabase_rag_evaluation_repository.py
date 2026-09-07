"""Completed RAG runs, separate from receipt evaluation storage."""

import httpx


class RagEvaluationMixin:
    def list_rag_evaluation_runs(self, user_email: str, start_at: str, end_at: str) -> list[dict]:
        user_id = self.get_public_user_id(user_email)
        rows = []
        while True:
            response = httpx.get(
                f"{self.url}/rest/v1/rag_evaluation_runs",
                params=[("select", "*"), ("user_id", f"eq.{user_id}"),
                        ("evaluated_at", f"gte.{start_at}"), ("evaluated_at", f"lt.{end_at}"),
                        ("order", "evaluated_at.desc,id.desc"), ("offset", str(len(rows))), ("limit", "500")],
                headers=self._service_headers(), timeout=20,
            )
            self._raise_for_supabase(response, "RAG 평가 이력 조회 실패")
            page = response.json()
            if not page:
                return rows
            rows.extend(page)

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
