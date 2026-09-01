from __future__ import annotations

from app.services.finance_receipt_items import *


RECEIPT_SUMMARY_TIMEOUT_SECONDS = 600
RECEIPT_ITEMS_TIMEOUT_SECONDS = 600
RECEIPT_ITEMS_RETRY_TIMEOUT_SECONDS = 300


def _generation_metrics(response: Any) -> dict[str, Any]:
    metrics = getattr(response, "ollama_metrics", None)
    if not isinstance(metrics, dict):
        return {}
    durations = {
        key.removesuffix("_duration") + "_duration_ms": round(value / 1_000_000, 2)
        for key, value in metrics.items()
        if key.endswith("_duration") and isinstance(value, (int, float))
    }
    return {
        "prompt_eval_count": int(metrics.get("prompt_eval_count") or 0),
        "eval_count": int(metrics.get("eval_count") or 0),
        "done_reason": str(metrics.get("done_reason") or ""),
        **durations,
    }


def _items_without_model_notes(items: Any) -> list[dict[str, Any]]:
    """Discard free-form model notes; deterministic post-processing may add its own."""
    if not isinstance(items, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cleaned_item = dict(item)
        cleaned_item.pop("note", None)
        cleaned.append(cleaned_item)
    return cleaned


def _is_generation_timeout(error: BaseException) -> bool:
    """Recognize a wrapped httpx/Ollama timeout without coupling to one client type."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, TimeoutError) or "timeout" in type(current).__name__.lower():
            return True
        current = current.__cause__ or current.__context__
    return False


async def _generate_receipt_json(*args: Any, **kwargs: Any) -> str:
    """Resolve through the public route module for patch/injection compatibility."""
    import sys

    route_module = sys.modules.get("app.api.routes.finance")
    generator = getattr(route_module, "generate", generate) if route_module else generate
    return await generator(*args, **kwargs)


async def _classify_receipt_with_model(
    text: str,
    filename: str,
    model_name: str,
    pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    summary_prompt = _receipt_prompt(text, filename, pages)
    summary_started = perf_counter()
    raw = await _generate_receipt_json(
        summary_prompt,
        json_format=True,
        num_predict=500,
        model_name=model_name,
        request_timeout_seconds=RECEIPT_SUMMARY_TIMEOUT_SECONDS,
    )
    summary_metrics = _generation_metrics(raw)
    summary_latency_ms = round((perf_counter() - summary_started) * 1000)
    result = json.loads(raw)
    if not isinstance(result, dict):
        logger.error("Receipt JSON parsing failed: model=%s filename=%s reason=object_expected raw_response=%s", model_name, filename, raw)
        raise ValueError("object expected")
    result["llm_trace"] = {
        "model_name": model_name,
        "prompt_version": FINANCE_PROMPT_VERSION,
        "summary_raw": json.loads(json.dumps(result, ensure_ascii=False)),
        "summary_response_text": raw,
        "summary_latency_ms": summary_latency_ms,
        "summary_prompt_chars": len(summary_prompt),
        "summary_response_chars": len(raw),
        "summary_ollama": summary_metrics,
    }
    result.pop("items", None)
    hints = _receipt_hints(text, filename)
    candidates, rejected_candidates = _reliable_item_candidates(_receipt_item_candidates(pages), hints)
    item_structure = _classify_item_structure(candidates, hints)
    result["semantic_evidence"] = _semantic_receipt_evidence(text, pages, candidates)
    structured_evidence = result["semantic_evidence"]["structured_evidence"]
    stated_count = hints.get("stated_item_count")

    fast_path_items, fast_path_reason = _strict_grounded_item_fast_path(
        candidates, hints, stated_count, result.get("total_amount"),
    )
    if fast_path_items:
        result["items"] = fast_path_items
        result["item_validation"] = _validate_resolved_items(fast_path_items, result.get("total_amount"))
        result["item_extraction_diagnostics"] = {
            "structure": item_structure,
            "structured_evidence": structured_evidence,
            "candidates": candidates,
            "rejected_candidates": rejected_candidates,
            "model_items": [],
            "resolved_items": json.loads(json.dumps(fast_path_items, ensure_ascii=False)),
            "fallback_used": fast_path_reason,
        }
        result["llm_trace"].update({
            "items_raw": None,
            "items_response_text": None,
            "items_latency_ms": 0,
            "items_prompt_chars": 0,
            "items_response_chars": 0,
            "items_ollama": {},
            "items_call_status": "skipped_grounded_fast_path",
            "items_skip_reason": fast_path_reason,
            "item_structure": item_structure,
        })
        return result

    items_prompt = _receipt_items_prompt(text, pages)
    items_started = perf_counter()
    item_attempts: list[dict[str, Any]] = []
    try:
        items_raw = ""
        items_result: dict[str, Any] | None = None
        items_metrics: dict[str, Any] = {}
        prompts = [
            (
                "recovery_full" if not candidates else "full",
                items_prompt,
                min(max(400, 220 + len(candidates) * 75), 750),
                RECEIPT_ITEMS_TIMEOUT_SECONDS,
            ),
        ]
        if candidates:
            prompts.append((
                "compact_retry", _receipt_items_retry_prompt(candidates, stated_count),
                min(max(240, 120 + len(candidates) * 55), 600),
                RECEIPT_ITEMS_RETRY_TIMEOUT_SECONDS,
            ))
        for attempt_name, attempt_prompt, num_predict, timeout_seconds in prompts:
            attempt_started = perf_counter()
            try:
                candidate_raw = await _generate_receipt_json(
                    attempt_prompt,
                    json_format=True,
                    num_predict=num_predict,
                    model_name=model_name,
                    request_timeout_seconds=timeout_seconds,
                )
                attempt_metrics = _generation_metrics(candidate_raw)
                candidate_result = json.loads(candidate_raw)
                if not isinstance(candidate_result, dict) or not isinstance(candidate_result.get("items"), list):
                    raise ValueError("items array expected")
                items_raw, items_result = candidate_raw, candidate_result
                items_metrics = attempt_metrics
                item_attempts.append({
                    "name": attempt_name, "status": "success",
                    "prompt_chars": len(attempt_prompt),
                    "latency_ms": round((perf_counter() - attempt_started) * 1000),
                    "ollama": attempt_metrics,
                })
                break
            except Exception as attempt_error:
                timed_out = _is_generation_timeout(attempt_error)
                item_attempts.append({
                    "name": attempt_name, "status": "failed",
                    "prompt_chars": len(attempt_prompt),
                    "latency_ms": round((perf_counter() - attempt_started) * 1000),
                    "failure_type": type(attempt_error).__name__,
                    "timed_out": timed_out,
                })
                # A timed-out request already consumed the safe request budget.
                # With no candidates, a compact retry would also have no facts.
                if timed_out or not candidates or attempt_name == "compact_retry":
                    raise
        if items_result is None:
            raise ValueError("item extraction produced no result")
        items_latency_ms = round((perf_counter() - items_started) * 1000)
        raw_model_items = _items_without_model_notes(items_result.get("items"))
        items_result["items"] = raw_model_items
        model_items = _deduplicate_model_items(raw_model_items, candidates)
        model_items_snapshot = json.loads(json.dumps(model_items, ensure_ascii=False))
        result["items"] = _reconcile_items_with_candidates(model_items, candidates, stated_count)
        fallback_reason = None
        if (
            len(result["items"]) < len(candidates)
            or _conflicts_with_single_inferred_service(result["items"], candidates)
        ):
            recovered, recovered_reason = _recover_items_when_grounded(candidates, hints, stated_count)
            if recovered:
                result["items"], fallback_reason = recovered, recovered_reason
        if fallback_reason in {"ocr_candidates_match_receipt_total", "validated_table_candidate_recovery"}:
            candidate_total = sum(_clean_number(candidate.get("amount_candidate")) for candidate in candidates)
            model_total = _clean_number(result.get("total_amount"))
            candidate_amounts = {_clean_number(candidate.get("amount_candidate")) for candidate in candidates}
            if candidate_total >= 100 and not hints.get("discount_amount") and (
                fallback_reason == "ocr_candidates_match_receipt_total" or model_total in candidate_amounts
            ):
                result["total_amount"] = candidate_total
        result["item_validation"] = _validate_resolved_items(result["items"], result.get("total_amount"))
        result["item_extraction_diagnostics"] = {
            "structure": item_structure,
            "structured_evidence": structured_evidence,
            "candidates": candidates,
            "rejected_candidates": rejected_candidates,
            "model_items": model_items_snapshot,
            "resolved_items": json.loads(json.dumps(result["items"], ensure_ascii=False)),
            "fallback_used": fallback_reason,
        }
        result["llm_trace"].update({
            "items_raw": json.loads(json.dumps(items_result, ensure_ascii=False)),
            "items_response_text": items_raw,
            "items_latency_ms": items_latency_ms,
            "items_prompt_chars": len(items_prompt),
            "items_response_chars": len(items_raw),
            "items_ollama": items_metrics,
            "items_call_status": "success",
            "items_attempts": item_attempts,
            "item_structure": item_structure,
        })
    except Exception as exc:
        items_latency_ms = round((perf_counter() - items_started) * 1000)
        result["items"], fallback_reason = _recover_items_when_grounded(candidates, hints, stated_count)
        if fallback_reason in {"ocr_candidates_match_receipt_total", "validated_table_candidate_recovery"} and not hints.get("discount_amount"):
            candidate_total = sum(_clean_number(candidate.get("amount_candidate")) for candidate in candidates)
            model_total = _clean_number(result.get("total_amount"))
            candidate_amounts = {_clean_number(candidate.get("amount_candidate")) for candidate in candidates}
            if fallback_reason == "ocr_candidates_match_receipt_total" or model_total in candidate_amounts:
                result["total_amount"] = candidate_total
        result["item_validation"] = _validate_resolved_items(result["items"], result.get("total_amount"))
        result["item_extraction_diagnostics"] = {
            "structure": item_structure,
            "structured_evidence": structured_evidence,
            "candidates": candidates,
            "rejected_candidates": rejected_candidates,
            "model_items": [],
            "failed": True,
            "failure_type": type(exc).__name__,
            "fallback_used": fallback_reason,
            "resolved_items": json.loads(json.dumps(result["items"], ensure_ascii=False)),
        }
        result["llm_trace"].update({
            "items_raw": None,
            "items_response_text": None,
            "items_latency_ms": items_latency_ms,
            "items_prompt_chars": len(items_prompt),
            "items_response_chars": 0,
            "items_ollama": {},
            "items_call_status": "failed",
            "items_attempts": item_attempts,
            "items_failure_type": type(exc).__name__,
            "item_structure": item_structure,
        })
        logger.warning("Receipt item extraction failed: model=%s filename=%s error=%s", model_name, filename, type(exc).__name__)
    return result


async def _classify_receipt(
    text: str,
    filename: str,
    pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    hints = _receipt_hints(text, filename)
    try:
        return await _classify_receipt_with_model(text, filename, RECEIPTS_MODEL_NAME, pages)
    except Exception:
        # OCR 결과는 LLM 장애와 무관하게 재무 양식에 먼저 저장합니다.
        # 학습 모델이 준비되면 같은 검토 화면에서 분류값을 보완할 수 있습니다.
        return {
            "doc_type": hints.get("document_type"),
            "expense_category": _normalize_expense_category(hints.get("expense_category")),
            "needs_review": not bool(hints.get("document_type") and _normalize_expense_category(hints.get("expense_category"))),
            "transaction_date": hints.get("transaction_date"),
            "supply_amount": hints.get("supply_amount") or 0,
            "tax_amount": hints.get("tax_amount") or 0,
            "total_amount": hints.get("total_amount") or 0,
            "description": "LLM 분류 전 OCR 자동 입력",
            "items": [],
            "_model_name": "rules-fallback",
        }

__all__ = [name for name in globals() if not name.startswith("__")]
