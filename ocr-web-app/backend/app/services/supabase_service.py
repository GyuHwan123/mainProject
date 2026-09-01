from __future__ import annotations

from datetime import datetime, timedelta, timezone
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

    def save_finance_record(
        self,
        *,
        user_email: str,
        document_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.get_ocr_document(user_email, document_id)
        user_id = self.get_public_user_id(user_email)
        response = httpx.post(
            f"{self.url}/rest/v1/finance_records",
            params={"on_conflict": "document_id"},
            headers={**self._service_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
            json={"user_id": user_id, "document_id": document_id, **payload, "updated_at": datetime.now(timezone.utc).isoformat()},
            timeout=20,
        )
        self._raise_for_supabase(response, "재무 문서 저장 실패")
        return response.json()[0]

    def list_finance_records(self, user_email: str, *, limit: int = 200) -> list[dict[str, Any]]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.get(
            f"{self.url}/rest/v1/finance_records",
            params={"select": "*", "user_id": f"eq.{user_id}", "order": "created_at.desc", "limit": str(limit)},
            headers=self._service_headers(),
            timeout=20,
        )
        self._raise_for_supabase(response, "재무 문서 목록 조회 실패")
        return response.json()

    def update_finance_record(self, user_email: str, record_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.patch(
            f"{self.url}/rest/v1/finance_records",
            params={"id": f"eq.{record_id}", "user_id": f"eq.{user_id}"},
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json={**payload, "updated_at": datetime.now(timezone.utc).isoformat()},
            timeout=15,
        )
        self._raise_for_supabase(response, "재무 문서 상태 변경 실패")
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=404, detail="재무 문서를 찾을 수 없습니다.")
        return rows[0]

    def create_finance_evaluation_batch(
        self,
        *,
        user_email: str,
        batch_name: str,
        model_name: str,
        dataset_name: str | None = None,
        total_items: int = 0,
        evaluation_mode: str = "SINGLE",
    ) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.post(
            f"{self.url}/rest/v1/finance_evaluation_batches",
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json={
                "user_id": user_id,
                "batch_name": batch_name[:200],
                "dataset_name": dataset_name,
                "model_name": model_name,
                "evaluation_mode": evaluation_mode,
                "requested_items": max(int(total_items), 0),
                # Item triggers become the source of truth once rows are added.
                "total_items": max(int(total_items), 0),
            },
            timeout=20,
        )
        self._raise_for_supabase(response, "재무 평가 배치 저장 실패")
        return response.json()[0]

    def _get_finance_evaluation_batch(self, user_email: str, batch_id: str) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.get(
            f"{self.url}/rest/v1/finance_evaluation_batches",
            params={"select": "*", "id": f"eq.{batch_id}", "user_id": f"eq.{user_id}", "limit": "1"},
            headers=self._service_headers(),
            timeout=15,
        )
        self._raise_for_supabase(response, "재무 평가 배치 조회 실패")
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=404, detail="재무 평가 배치를 찾을 수 없습니다.")
        return rows[0]

    def list_finance_evaluation_batches(self, user_email: str, limit: int = 30) -> list[dict[str, Any]]:
        user_id = self.get_public_user_id(user_email)
        response = httpx.get(
            f"{self.url}/rest/v1/finance_evaluation_batches",
            params={
                "select": "id,batch_name,dataset_name,model_name,status,total_items,completed_items,failed_items,summary_metrics,created_at,completed_at",
                "user_id": f"eq.{user_id}",
                "evaluation_mode": "eq.BULK",
                "order": "created_at.desc",
                "limit": str(max(1, min(int(limit), 100))),
            },
            headers=self._service_headers(),
            timeout=15,
        )
        self._raise_for_supabase(response, "재무 평가 배치 목록 조회 실패")
        return response.json()

    def save_finance_record_evaluation(
        self,
        *,
        user_email: str,
        document: dict[str, Any],
        record: dict[str, Any],
        ground_truth: dict[str, Any],
        normalized_ground_truth: dict[str, Any],
        result: dict[str, Any],
        dataset_index: int = 0,
        dataset_name: str | None = None,
        source_file_name: str | None = None,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        if batch_id:
            batch = self._get_finance_evaluation_batch(user_email, batch_id)
        else:
            batch = self.create_finance_evaluation_batch(
                user_email=user_email,
                batch_name=source_file_name or document.get("file_name") or "receipt evaluation",
                dataset_name=dataset_name,
                model_name=str(result.get("model_name") or record.get("model_name") or "unknown"),
                total_items=1,
            )
            batch_id = batch["id"]

        item_response = httpx.post(
            f"{self.url}/rest/v1/finance_evaluation_items",
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json={
                "batch_id": batch_id,
                "user_id": user_id,
                "dataset_index": max(int(dataset_index), 0),
                "source_file_name": source_file_name or document.get("file_name") or "receipt",
                "source_storage_path": document.get("file_url"),
                "document_id": document["id"],
                "finance_record_id": record["id"],
                "ground_truth": ground_truth,
                "normalized_ground_truth": normalized_ground_truth,
                "status": "EVALUATING",
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
            timeout=20,
        )
        self._raise_for_supabase(item_response, "재무 평가 항목 저장 실패")
        item = item_response.json()[0]
        system = result.get("system") or {}
        score = system.get("score") or {}
        try:
            evaluation_response = httpx.post(
                f"{self.url}/rest/v1/finance_record_evaluations",
                headers={**self._service_headers(), "Prefer": "return=representation"},
                json={
                    "batch_id": batch_id,
                    "item_id": item["id"],
                    "user_id": user_id,
                    "document_id": document["id"],
                    "finance_record_id": record["id"],
                    "ground_truth": ground_truth,
                    "normalized_ground_truth": normalized_ground_truth,
                    "prediction": system.get("prediction") or {},
                    "pipeline_trace": system.get("pipeline_trace") or {},
                    "error_analysis": system.get("error_analysis") or {},
                    "error_tags": (system.get("error_analysis") or {}).get("error_tags") or [],
                    "analysis_version": (system.get("error_analysis") or {}).get("analysis_version"),
                    "needs_review": bool((system.get("error_analysis") or {}).get("needs_review")),
                    "field_scores": score.get("fields") or {},
                    "ocr_impact": system.get("ocr_impact") or {},
                    "workbook_result": system.get("workbook") or {},
                    "model_name": str(result.get("model_name") or record.get("model_name") or "unknown"),
                    "model_version": result.get("model_version"),
                    "prompt_version": result.get("prompt_version"),
                    "pipeline_version": FINANCE_PIPELINE_VERSION,
                    "correct_fields": int(score.get("correct_fields") or 0),
                    "evaluated_fields": int(score.get("evaluated_fields") or 0),
                    "field_accuracy": float(score.get("field_accuracy") or 0),
                    "complete_match": bool(score.get("complete_match")),
                    "latency_ms": max(int(result.get("latency_ms") or 0), 0),
                    "status": "COMPLETED" if result.get("success", True) else "FAILED",
                    "error_message": result.get("error"),
                },
                timeout=20,
            )
            self._raise_for_supabase(evaluation_response, "재무 평가 결과 저장 실패")
            evaluation = evaluation_response.json()[0]
            item_patch = httpx.patch(
                f"{self.url}/rest/v1/finance_evaluation_items",
                params={"id": f"eq.{item['id']}"},
                headers={**self._service_headers(), "Prefer": "return=minimal"},
                json={
                    "status": "COMPLETED" if result.get("success", True) else "FAILED",
                    "evaluation_time_ms": max(int(result.get("latency_ms") or 0), 0),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "error_stage": None if result.get("success", True) else "EVALUATION",
                    "error_message": result.get("error"),
                },
                timeout=15,
            )
            self._raise_for_supabase(item_patch, "재무 평가 항목 완료 처리 실패")
            self._refresh_finance_evaluation_summary(batch_id)
            return {"batch": batch, "item": item, "evaluation": evaluation}
        except Exception as exc:
            httpx.patch(
                f"{self.url}/rest/v1/finance_evaluation_items",
                params={"id": f"eq.{item['id']}"},
                headers={**self._service_headers(), "Prefer": "return=minimal"},
                json={"status": "FAILED", "error_stage": "EVALUATION", "error_message": str(exc)[:2000], "completed_at": datetime.now(timezone.utc).isoformat()},
                timeout=15,
            )
            raise

    def _refresh_finance_evaluation_summary(self, batch_id: str, *, finalize: bool = False) -> dict[str, Any]:
        response = httpx.get(
            f"{self.url}/rest/v1/finance_record_evaluations",
            params={
                "select": "field_accuracy,latency_ms,status,complete_match,field_scores,ocr_impact,workbook_result",
                "batch_id": f"eq.{batch_id}",
            },
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "재무 평가 요약 조회 실패")
        rows = response.json()
        completed = [row for row in rows if row.get("status") == "COMPLETED"]
        batch = httpx.get(
            f"{self.url}/rest/v1/finance_evaluation_batches",
            params={"select": "requested_items,evaluation_mode", "id": f"eq.{batch_id}", "limit": "1"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(batch, "재무 평가 배치 집계 조회 실패")
        batch_rows = batch.json()
        requested = int((batch_rows[0] if batch_rows else {}).get("requested_items") or len(rows))
        items_response = httpx.get(
            f"{self.url}/rest/v1/finance_evaluation_items",
            params={"select": "status,error_stage", "batch_id": f"eq.{batch_id}"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(items_response, "재무 평가 항목 집계 조회 실패")
        items = items_response.json()
        field_error_counts: dict[str, int] = {}
        for row in completed:
            for field, detail in (row.get("field_scores") or {}).items():
                if not isinstance(detail, dict):
                    continue
                if field != "items":
                    if not detail.get("correct"):
                        field_error_counts[field] = field_error_counts.get(field, 0) + 1
                    continue
                if not detail.get("count_correct"):
                    field_error_counts["items.count"] = field_error_counts.get("items.count", 0) + 1
                for item in detail.get("items") or []:
                    for item_field, item_detail in (item.get("fields") or {}).items():
                        if isinstance(item_detail, dict) and not item_detail.get("correct"):
                            key = f"items.{item_field}"
                            field_error_counts[key] = field_error_counts.get(key, 0) + 1
        error_stage_counts = {stage: 0 for stage in ("UPLOAD", "OCR", "DOCUMENTATION", "EVALUATION")}
        for item in items:
            stage = item.get("error_stage")
            if stage in error_stage_counts:
                error_stage_counts[stage] += 1
        unregistered_count = max(requested - len(items), 0)
        if unregistered_count:
            error_stage_counts["UNREGISTERED"] = unregistered_count
        ocr_rates = [float((row.get("ocr_impact") or {}).get("ocr_evidence_rate")) for row in completed if (row.get("ocr_impact") or {}).get("ocr_evidence_rate") is not None]
        metrics = {
            "batch_mode": (batch_rows[0] if batch_rows else {}).get("evaluation_mode") or "SINGLE",
            "requested_count": requested,
            "registered_count": len(items),
            "evaluated_count": len(rows),
            "successful_count": len(completed),
            "failed_count": max(requested - len(completed), 0),
            "unsuccessful_count": max(requested - len(completed), 0),
            "average_field_accuracy": sum(float(row.get("field_accuracy") or 0) for row in completed) / len(completed) if completed else 0,
            "complete_match_rate": sum(bool(row.get("complete_match")) for row in completed) / len(completed) if completed else 0,
            "average_latency_ms": round(sum(int(row.get("latency_ms") or 0) for row in completed) / len(completed)) if completed else 0,
            "total_processing_time_ms": sum(int(row.get("latency_ms") or 0) for row in rows),
            "schema_success_rate": sum(bool((row.get("workbook_result") or {}).get("success")) for row in completed) / len(completed) if completed else 0,
            "total_amount_accuracy": sum(bool(((row.get("field_scores") or {}).get("total_amount") or {}).get("correct")) for row in completed) / len(completed) if completed else 0,
            "ocr_evidence_rate": sum(ocr_rates) / len(ocr_rates) if ocr_rates else 0,
            "field_error_counts": field_error_counts,
            "error_stage_counts": error_stage_counts,
        }
        batch_status = None
        if finalize:
            batch_status = "COMPLETED" if requested > 0 and len(completed) == requested else "FAILED" if not completed else "PARTIAL"
        patch = httpx.patch(
            f"{self.url}/rest/v1/finance_evaluation_batches",
            params={"id": f"eq.{batch_id}"},
            headers={**self._service_headers(), "Prefer": "return=minimal"},
            json={"summary_metrics": metrics, **({"status": batch_status, "completed_at": datetime.now(timezone.utc).isoformat()} if batch_status else {})}, timeout=15,
        )
        self._raise_for_supabase(patch, "재무 평가 요약 저장 실패")
        return metrics

    def finalize_finance_evaluation_batch(self, user_email: str, batch_id: str) -> dict[str, Any]:
        self._get_finance_evaluation_batch(user_email, batch_id)
        metrics = self._refresh_finance_evaluation_summary(batch_id, finalize=True)
        return {"id": batch_id, "summary_metrics": metrics}

    def list_finance_record_evaluations(
        self,
        user_email: str,
        *,
        limit: int = 200,
        evaluation_mode: str | None = None,
        batch_id: str | None = None,
    ) -> list[dict[str, Any]]:
        user_id = self.get_public_user_id(user_email)
        batch_relation = "finance_evaluation_batches!inner" if evaluation_mode else "finance_evaluation_batches"
        params = {
            "select": f"*,finance_evaluation_items(dataset_index,source_file_name),{batch_relation}(dataset_name,evaluation_mode),ocr_documents(file_name,extracted_text,bounding_boxes)",
            "user_id": f"eq.{user_id}",
            "order": "evaluated_at.desc",
            "limit": str(max(1, min(int(limit), 200))),
        }
        if evaluation_mode:
            params["finance_evaluation_batches.evaluation_mode"] = f"eq.{evaluation_mode.upper()}"
        if batch_id:
            params["batch_id"] = f"eq.{batch_id}"
        response = httpx.get(
            f"{self.url}/rest/v1/finance_record_evaluations",
            params=params,
            headers=self._service_headers(), timeout=20,
        )
        self._raise_for_supabase(response, "재무 평가 결과 조회 실패")
        return response.json()

    def list_finance_monitoring_data(
        self,
        user_email: str,
        *,
        start_at: str,
        end_at: str,
        model_name: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return the persisted rows needed by the receipt monitoring dashboard."""
        user_id = self.get_public_user_id(user_email)
        # PostgREST needs separate keys for the lower and upper bound. Passing
        # them as a list preserves both filters through httpx.
        evaluation_params = [
            ("select", "id,item_id,field_accuracy,complete_match,latency_ms,status,field_scores,error_tags,error_message,evaluated_at,model_name,batch_id"),
            ("user_id", f"eq.{user_id}"),
            ("evaluated_at", f"gte.{start_at}"),
            ("evaluated_at", f"lt.{end_at}"),
            ("order", "evaluated_at.asc"),
            ("limit", "10000"),
        ]
        if model_name:
            evaluation_params.append(("model_name", f"eq.{model_name}"))
        evaluations_response = httpx.get(
            f"{self.url}/rest/v1/finance_record_evaluations",
            params=evaluation_params,
            headers=self._service_headers(), timeout=20,
        )
        self._raise_for_supabase(evaluations_response, "영수증 모니터링 평가 조회 실패")

        item_response = httpx.get(
            f"{self.url}/rest/v1/finance_evaluation_items",
            params=[
                ("select", "id,status,error_stage,error_message,started_at,completed_at,batch_id"),
                ("user_id", f"eq.{user_id}"),
                ("started_at", f"gte.{start_at}"),
                ("started_at", f"lt.{end_at}"),
                ("order", "started_at.asc"),
                ("limit", "10000"),
            ],
            headers=self._service_headers(), timeout=20,
        )
        self._raise_for_supabase(item_response, "영수증 모니터링 처리 이력 조회 실패")
        batch_response = httpx.get(
            f"{self.url}/rest/v1/finance_evaluation_batches",
            params=[
                ("select", "id,batch_name,model_name,status,total_items,completed_items,failed_items,summary_metrics,created_at,completed_at"),
                ("user_id", f"eq.{user_id}"),
                ("created_at", f"gte.{start_at}"),
                ("created_at", f"lt.{end_at}"),
                ("order", "created_at.desc"),
                ("limit", "200"),
            ],
            headers=self._service_headers(), timeout=20,
        )
        self._raise_for_supabase(batch_response, "영수증 모니터링 실행 이력 조회 실패")
        return {"evaluations": evaluations_response.json(), "items": item_response.json(), "batches": batch_response.json()}

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

    def create_chat_session(self, user_email: str, title: str, document_id: str | None) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        payload: dict[str, Any] = {"user_id": user_id, "group_id": None, "title": title[:120]}
        if document_id is not None:
            payload["document_id"] = document_id
        response = httpx.post(
            f"{self.url}/rest/v1/chat_sessions",
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json=payload, timeout=15,
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
        response = httpx.get(
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
        owned_document = httpx.get(
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

        response = httpx.delete(
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
        upsert = httpx.post(
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
        delete = httpx.delete(
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
            insert = httpx.post(
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
        include_company_documents: bool = False,
    ) -> list[dict[str, Any]]:
        documents = self.list_rag_documents(user_email)
        company_documents: list[dict[str, Any]] = []
        if include_company_documents:
            company_response = httpx.get(
                f"{self.url}/rest/v1/rag_documents",
                params={
                    "select": "id,doc_id,title,owner,filename",
                    "doc_id": f"in.({','.join(COMPANY_RAG_DOCUMENT_IDS)})",
                },
                headers=self._service_headers(), timeout=15,
            )
            self._raise_for_supabase(company_response, "RAG company documents lookup failed")
            company_documents = company_response.json()
        documents.extend(company_documents)
        document_by_id = {row["id"]: row for row in documents}
        if rag_document_id and rag_document_id not in document_by_id:
            raise HTTPException(status_code=404, detail="RAG 문서를 찾을 수 없습니다.")
        allowed_document_ids = [rag_document_id] if rag_document_id else list(document_by_id)
        if not allowed_document_ids:
            return []
        response = httpx.post(
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
        response = httpx.get(
            f"{self.url}/rest/v1/rag_documents",
            params={"select": "doc_id,title,filename", "order": "created_at.asc"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "RAG 문서 카탈로그 조회 실패")
        return response.json()

    def get_accessible_rag_document(
        self, user_email: str, rag_document_id: str, *, include_company_documents: bool = False,
    ) -> dict[str, Any]:
        response = httpx.get(
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
            response = httpx.get(
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
        response = httpx.patch(
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
        response = httpx.get(
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

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        response = httpx.get(
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
        response = httpx.post(
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
            response = httpx.post(
                f"{self.url}/rest/v1/{self.users_table}",
                params={"on_conflict": "email"},
                headers={
                    "Authorization": f"Bearer {self.service_role_key}",
                    "apikey": self.service_role_key,
                    "Content-Type": "application/json",
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
        rows = response.json() if response.content else []
        if rows:
            return rows[0]
        user = self.get_user_by_email(email)
        if not user:
            raise HTTPException(status_code=502, detail="Supabase 사용자 저장 결과를 확인할 수 없습니다.")
        return user

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
        metadata = data.get("user_metadata") or {}
        provider = data.get("app_metadata", {}).get("provider") or "supabase"
        # Supabase Custom OAuth can retain a verified provider email in user
        # metadata while leaving auth.users.email empty for the new identity.
        email = data.get("email") or (metadata.get("email") if provider == "custom:naver" else None)
        return {
            "id": data.get("id"),
            "email": email,
            "name": metadata.get("full_name") or metadata.get("name") or metadata.get("nickname") or email,
            "provider": provider.removeprefix("custom:"),
        }


supabase_service = SupabaseService()
