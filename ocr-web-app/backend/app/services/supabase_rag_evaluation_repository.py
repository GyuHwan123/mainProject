"""Completed RAG runs, separate from receipt evaluation storage."""

from uuid import uuid4

import httpx


class RagEvaluationMixin:
    def get_rag_evaluation_run(self, user_email: str, run_id: str) -> dict | None:
        user_id = self.get_public_user_id(user_email)
        response = httpx.get(
            f"{self.url}/rest/v1/rag_evaluation_runs",
            params={"select": "*", "user_id": f"eq.{user_id}", "run_id": f"eq.{run_id}", "limit": "1"},
            headers=self._service_headers(), timeout=20,
        )
        self._raise_for_supabase(response, "RAG 평가 상세 조회 실패")
        rows = response.json()
        return rows[0] if rows else None

    def latest_rag_evaluation_run(self, user_email: str) -> dict | None:
        """Read the user's latest persisted run without any date-window limit."""
        user_id = self.get_public_user_id(user_email)
        response = httpx.get(
            f"{self.url}/rest/v1/rag_evaluation_runs",
            params={"select": "*", "user_id": f"eq.{user_id}",
                    "or": "(configuration->>demo.is.null,configuration->>demo.neq.true)",
                    "order": "evaluated_at.desc,id.desc", "limit": "1"},
            headers=self._service_headers(), timeout=20,
        )
        self._raise_for_supabase(response, "RAG 최신 평가 이력 조회 실패")
        rows = response.json()
        return rows[0] if rows else None

    def list_rag_evaluation_runs(self, user_email: str, start_at: str | None, end_at: str | None,
                                 demo_batch_id: str | None = None) -> list[dict]:
        user_id = self.get_public_user_id(user_email)
        filters = ([("configuration->>demo", "eq.true"),
                    ("configuration->>demo_batch_id", f"eq.{demo_batch_id}")]
                   if demo_batch_id else
                   [("or", "(configuration->>demo.is.null,configuration->>demo.neq.true)")])
        date_filters = []
        if start_at is not None:
            date_filters.append(("evaluated_at", f"gte.{start_at}"))
        if end_at is not None:
            date_filters.append(("evaluated_at", f"lt.{end_at}"))
        rows = []
        while True:
            response = httpx.get(
                f"{self.url}/rest/v1/rag_evaluation_runs",
                params=[("select", "*"), ("user_id", f"eq.{user_id}"),
                        *date_filters,
                        ("order", "evaluated_at.desc,id.desc"), ("offset", str(len(rows))), ("limit", "500"), *filters],
                headers=self._service_headers(), timeout=20,
            )
            self._raise_for_supabase(response, "RAG 평가 이력 조회 실패")
            page = response.json()
            if not page:
                return rows
            rows.extend(page)

    def save_rag_evaluation_run(self, user_email: str, payload: dict) -> None:
        user_id = self.get_public_user_id(user_email)
        run_id = str(payload.get("run_id") or payload.get("id") or uuid4())
        row = {**payload, "user_id": user_id, "run_id": run_id}

        # A completed evaluation must produce a fresh row for each run_id. Reusing a
        # stale history id from a previous checkpoint can otherwise cause the insert to
        # collide on the row primary key or be ignored as an idempotent duplicate.
        row["id"] = str(uuid4())

        response = httpx.post(
            f"{self.url}/rest/v1/rag_evaluation_runs",
            params={"on_conflict": "run_id"},
            headers={**self._service_headers(), "Prefer": "resolution=ignore-duplicates,return=minimal"},
            json=row,
            timeout=20,
        )
        self._raise_for_supabase(response, "RAG 평가 실행 이력 저장 실패")
