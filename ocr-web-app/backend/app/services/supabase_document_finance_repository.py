from __future__ import annotations

import sys

from app.services.supabase_base import *

def _legacy_httpx():
    return sys.modules["app.services.supabase_service"].httpx

class DocumentFinanceMixin:
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
        upload = _legacy_httpx().post(
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
        insert = _legacy_httpx().post(
            f"{self.url}/rest/v1/{self.ocr_documents_table}",
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json=payload,
            timeout=15,
        )
        if insert.status_code >= 400:
            _legacy_httpx().delete(
                f"{self.url}/storage/v1/object/{self.documents_bucket}/{encoded_path}",
                headers=self._service_headers(json_content=False),
                timeout=15,
            )
            self._raise_for_supabase(insert, "ocr_documents 저장 실패")
        return insert.json()[0]

    def list_ocr_documents(self, user_email: str, upload_origin: str | None = None) -> list[dict[str, Any]]:
        user_id = self.get_public_user_id(user_email)
        params = {"select": "id,file_name,file_url,status,created_at", "user_id": f"eq.{user_id}", "order": "created_at.desc"}
        if upload_origin:
            params["upload_origin"] = f"eq.{upload_origin}"
        rows: list[dict[str, Any]] = []
        page_size = 1000
        while True:
            response = _legacy_httpx().get(
                f"{self.url}/rest/v1/{self.ocr_documents_table}",
                params={**params, "limit": str(page_size), "offset": str(len(rows))},
                headers=self._service_headers(), timeout=20,
            )
            self._raise_for_supabase(response, "문서 히스토리 조회 실패")
            page = response.json(); rows.extend(page)
            if len(page) < page_size: break
        return rows

    def get_ocr_document(self, user_email: str, document_id: str) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        response = _legacy_httpx().get(
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
        response = _legacy_httpx().post(
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
        response = _legacy_httpx().get(
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
        response = _legacy_httpx().post(
            f"{self.url}/rest/v1/finance_records",
            params={"on_conflict": "document_id"},
            headers={**self._service_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
            json={"user_id": user_id, "document_id": document_id, **payload, "updated_at": datetime.now(timezone.utc).isoformat()},
            timeout=20,
        )
        self._raise_for_supabase(response, "재무 문서 저장 실패")
        return response.json()[0]

    def list_finance_records(self, user_email: str, *, limit: int | None = 200) -> list[dict[str, Any]]:
        user_id = self.get_public_user_id(user_email)
        rows: list[dict[str, Any]] = []
        page_size = 1000
        while limit is None or len(rows) < limit:
            request_size = page_size if limit is None else min(page_size, limit - len(rows))
            response = _legacy_httpx().get(
                f"{self.url}/rest/v1/finance_records",
                params={"select": "*", "user_id": f"eq.{user_id}", "order": "created_at.desc", "limit": str(request_size), "offset": str(len(rows))},
                headers=self._service_headers(), timeout=20,
            )
            self._raise_for_supabase(response, "재무 문서 목록 조회 실패")
            page = response.json(); rows.extend(page)
            if len(page) < request_size: break
        return rows

    def save_receipt_archive(
        self,
        *,
        user_email: str,
        document_id: str,
        finance_record: dict[str, Any],
        source_file_name: str,
        source_storage_path: str,
    ) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        structured_data = finance_record.get("structured_data") or {}
        receipt_fingerprint = structured_data.get("receipt_identity_key") or structured_data.get("receipt_fingerprint")
        conflict_columns = "user_id,receipt_fingerprint" if receipt_fingerprint else "finance_record_id"
        response = _legacy_httpx().post(
            f"{self.url}/rest/v1/receipt_archive",
            params={"on_conflict": conflict_columns},
            headers={**self._service_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
            json={
                "user_id": user_id,
                "document_id": document_id,
                "finance_record_id": finance_record["id"],
                "source_file_name": source_file_name[:500],
                "source_storage_path": source_storage_path,
                "receipt_fingerprint": receipt_fingerprint,
                "expense_category": finance_record.get("expense_category"),
                "merchant": finance_record.get("merchant"),
                "transaction_date": finance_record.get("transaction_date"),
                "total_amount": finance_record.get("total_amount") or 0,
                "deleted_at": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            timeout=20,
        )
        self._raise_for_supabase(response, "영수증 보관함 저장 실패")
        return response.json()[0]

    def list_receipt_archive(self, user_email: str, *, category: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        user_id = self.get_public_user_id(user_email)
        params = {
            "select": "*,finance_records!inner(*),ocr_documents(file_name,file_url)",
            "user_id": f"eq.{user_id}",
            "deleted_at": "is.null",
            "order": "created_at.desc",
            "limit": str(limit),
        }
        if category == "UNCLASSIFIED":
            params["finance_records.expense_category"] = "is.null"
        elif category:
            params["finance_records.expense_category"] = f"eq.{category}"
        response = _legacy_httpx().get(
            f"{self.url}/rest/v1/receipt_archive",
            params=params,
            headers=self._service_headers(),
            timeout=20,
        )
        self._raise_for_supabase(response, "영수증 보관함 조회 실패")
        return response.json()

    def soft_delete_receipt_archive(self, user_email: str, archive_id: str | None = None) -> int:
        user_id = self.get_public_user_id(user_email)
        params = {"user_id": f"eq.{user_id}", "deleted_at": "is.null"}
        if archive_id:
            params["id"] = f"eq.{archive_id}"
        now = datetime.now(timezone.utc).isoformat()
        response = _legacy_httpx().patch(
            f"{self.url}/rest/v1/receipt_archive",
            params=params,
            headers={**self._service_headers(), "Prefer": "return=representation"},
            json={"deleted_at": now, "updated_at": now},
            timeout=20,
        )
        self._raise_for_supabase(response, "영수증 보관함 삭제 실패")
        return len(response.json())

    def update_finance_record(self, user_email: str, record_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = self.get_public_user_id(user_email)
        response = _legacy_httpx().patch(
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
        updated_record = rows[0]
        archive_response = _legacy_httpx().patch(
            f"{self.url}/rest/v1/receipt_archive",
            params={"finance_record_id": f"eq.{record_id}", "user_id": f"eq.{user_id}"},
            headers=self._service_headers(),
            json={
                "expense_category": updated_record.get("expense_category"),
                "merchant": updated_record.get("merchant"),
                "transaction_date": updated_record.get("transaction_date"),
                "total_amount": updated_record.get("total_amount") or 0,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            timeout=15,
        )
        self._raise_for_supabase(archive_response, "영수증 보관함 카테고리 동기화 실패")
        return updated_record

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
        response = _legacy_httpx().post(
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
        response = _legacy_httpx().get(
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
        response = _legacy_httpx().get(
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

        item_response = _legacy_httpx().post(
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
        selection_rubric = score.get("selection_rubric") or {}
        try:
            evaluation_response = _legacy_httpx().post(
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
                    "selection_rubric": selection_rubric or None,
                    "score_version": selection_rubric.get("version"),
                    "extraction_score_95": selection_rubric.get("extraction_score"),
                    "json_schema_rate": selection_rubric.get("schema_rate"),
                    "total_amount_correct": selection_rubric.get("total_amount_correct"),
                    "hallucination_count": selection_rubric.get("hallucination_count"),
                    "latency_ms": max(int(result.get("latency_ms") or 0), 0),
                    "status": "COMPLETED" if result.get("success", True) else "FAILED",
                    "error_message": result.get("error"),
                },
                timeout=20,
            )
            self._raise_for_supabase(evaluation_response, "재무 평가 결과 저장 실패")
            evaluation = evaluation_response.json()[0]
            item_patch = _legacy_httpx().patch(
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
            _legacy_httpx().patch(
                f"{self.url}/rest/v1/finance_evaluation_items",
                params={"id": f"eq.{item['id']}"},
                headers={**self._service_headers(), "Prefer": "return=minimal"},
                json={"status": "FAILED", "error_stage": "EVALUATION", "error_message": str(exc)[:2000], "completed_at": datetime.now(timezone.utc).isoformat()},
                timeout=15,
            )
            raise

    def _refresh_finance_evaluation_summary(self, batch_id: str, *, finalize: bool = False) -> dict[str, Any]:
        response = _legacy_httpx().get(
            f"{self.url}/rest/v1/finance_record_evaluations",
            params={
                "select": "field_accuracy,latency_ms,status,complete_match,field_scores,ocr_impact,workbook_result,selection_rubric,extraction_score_95,json_schema_rate,total_amount_correct",
                "batch_id": f"eq.{batch_id}",
            },
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(response, "재무 평가 요약 조회 실패")
        rows = response.json()
        completed = [row for row in rows if row.get("status") == "COMPLETED"]
        batch = _legacy_httpx().get(
            f"{self.url}/rest/v1/finance_evaluation_batches",
            params={"select": "requested_items,evaluation_mode", "id": f"eq.{batch_id}", "limit": "1"},
            headers=self._service_headers(), timeout=15,
        )
        self._raise_for_supabase(batch, "재무 평가 배치 집계 조회 실패")
        batch_rows = batch.json()
        requested = int((batch_rows[0] if batch_rows else {}).get("requested_items") or len(rows))
        items_response = _legacy_httpx().get(
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
        schema_rates = [
            float(row["json_schema_rate"])
            for row in completed if row.get("json_schema_rate") is not None
        ]
        if not schema_rates:
            schema_rates = [
                float((row.get("selection_rubric") or {}).get("schema_rate"))
                for row in completed
                if (row.get("selection_rubric") or {}).get("schema_rate") is not None
            ]
        amount_results = [
            bool(row["total_amount_correct"])
            for row in completed if row.get("total_amount_correct") is not None
        ]
        if not amount_results:
            amount_results = [
                bool(((row.get("field_scores") or {}).get("total_amount") or {}).get("correct"))
                for row in completed
            ]
        workbook_success_rate = (
            sum(bool((row.get("workbook_result") or {}).get("success")) for row in completed) / len(completed)
            if completed else 0
        )
        json_schema_rate = sum(schema_rates) / len(schema_rates) if schema_rates else 0
        extraction_scores = [
            float(row["extraction_score_95"])
            for row in completed if row.get("extraction_score_95") is not None
        ]
        if not extraction_scores:
            extraction_scores = [
                float((row.get("selection_rubric") or {}).get("extraction_score"))
                for row in completed
                if (row.get("selection_rubric") or {}).get("extraction_score") is not None
            ]
        extraction_score_95 = sum(extraction_scores) / len(extraction_scores) if extraction_scores else 0
        latencies = sorted(int(row.get("latency_ms") or 0) for row in completed if int(row.get("latency_ms") or 0) > 0)
        average_latency_ms = round(sum(latencies) / len(latencies)) if latencies else 0
        p95_latency_ms = latencies[max(ceil(len(latencies) * .95) - 1, 0)] if latencies else 0
        speed_score_3 = max(0.0, min(3.0, (120 - average_latency_ms / 1000) / 30)) if average_latency_ms else 0.0
        if p95_latency_ms > 180000:
            speed_score_3 = min(speed_score_3, 1.0)
        local_cost_score_2 = 2 if completed else 0
        total_amount_accuracy = sum(amount_results) / len(amount_results) if amount_results else 0
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
            "average_latency_ms": average_latency_ms,
            "p95_latency_ms": p95_latency_ms,
            "total_processing_time_ms": sum(int(row.get("latency_ms") or 0) for row in rows),
            # Compatibility alias: this now has the same JSON-schema meaning
            # as the frontend selection card.
            "schema_success_rate": json_schema_rate,
            "json_schema_rate": json_schema_rate,
            "workbook_success_rate": workbook_success_rate,
            "total_amount_accuracy": total_amount_accuracy,
            "extraction_score_95": extraction_score_95,
            "speed_rubric_version": "absolute-latency-v1",
            "speed_score_3": speed_score_3,
            "local_cost_score_2": local_cost_score_2,
            "final_score_100": extraction_score_95 + speed_score_3 + local_cost_score_2,
            "quality_gate_passed": json_schema_rate >= .98 and total_amount_accuracy >= .95,
            "ocr_evidence_rate": sum(ocr_rates) / len(ocr_rates) if ocr_rates else 0,
            "field_error_counts": field_error_counts,
            "error_stage_counts": error_stage_counts,
        }
        batch_status = None
        if finalize:
            batch_status = "COMPLETED" if requested > 0 and len(completed) == requested else "FAILED" if not completed else "PARTIAL"
        patch = _legacy_httpx().patch(
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
        response = _legacy_httpx().get(
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
        # them as a list preserves both filters through _legacy_httpx().
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
        evaluations_response = _legacy_httpx().get(
            f"{self.url}/rest/v1/finance_record_evaluations",
            params=evaluation_params,
            headers=self._service_headers(), timeout=20,
        )
        self._raise_for_supabase(evaluations_response, "영수증 모니터링 평가 조회 실패")

        item_response = _legacy_httpx().get(
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
        batch_response = _legacy_httpx().get(
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
        response = _legacy_httpx().post(
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
        response = _legacy_httpx().get(
            f"{self.url}/storage/v1/object/{self.documents_bucket}/{encoded_path}",
            headers=self._service_headers(json_content=False),
            timeout=60,
        )
        self._raise_for_supabase(response, "원본 파일 다운로드 실패")
        return response.content, response.headers.get("content-type", "application/octet-stream")


