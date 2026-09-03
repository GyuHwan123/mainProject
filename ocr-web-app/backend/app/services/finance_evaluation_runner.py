from __future__ import annotations

from app.services.finance_evaluation_workbook import *

async def evaluate_models(
    *, text: str, filename: str, truth: dict[str, Any], model_names: list[str],
    pages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    truth = normalize_ground_truth(truth)
    results = []
    for model_name in model_names:
        logger.warning("Finance evaluation model start: model=%s filename=%s", model_name, filename)
        started = perf_counter()
        try:
            pure = await _classify_receipt_with_model(text, filename, model_name, pages)
            latency_ms = round((perf_counter() - started) * 1000)
            system = _normalize(dict(pure), filename, text)
            system_prediction = {field: system.get(field) for field in CORE_FIELDS}
            structured = system.get("structured_data") or {}
            system_prediction["items"] = structured.get("items") or []
            summary = structured.get("receipt_summary") if isinstance(structured.get("receipt_summary"), dict) else {}
            system_prediction["total_quantity"] = (
                structured.get("total_quantity")
                if structured.get("total_quantity") is not None
                else summary.get("stated_total_quantity")
            )
            system_prediction["discount_amount"] = structured.get("discount_amount")
            system_prediction["card_number"] = structured.get("card_number")
            system_score = score_fields(system_prediction, truth, pure, pages)
            pipeline_trace = {
                "llm": structured.get("llm_trace") or {},
                "validation": structured.get("automation_validation") or {},
            }
            error_analysis = analyze_finance_evaluation_failure(
                ocr_text=text,
                ground_truth=truth,
                prediction=system_prediction,
                pipeline_trace=pipeline_trace,
            )
            results.append({
                "model_name": model_name,
                "success": True,
                "latency_ms": latency_ms,
                "system": {
                    "prediction": system_prediction,
                    "pipeline_trace": pipeline_trace,
                    "error_analysis": error_analysis,
                    "score": system_score,
                    "ocr_impact": estimate_ocr_impact(text, truth, system_score),
                    "automation": {
                        **(structured.get("automation_validation") or {}),
                        "auto_approved": (structured.get("automation_validation") or {}).get("decision") == "PASS",
                        "auto_approved_correct": bool(
                            (structured.get("automation_validation") or {}).get("decision") == "PASS"
                            and system_score.get("complete_match")
                        ),
                    },
                    "workbook": verify_workbook(system),
                },
            })
        except Exception as exc:
            results.append({
                "model_name": model_name,
                "success": False,
                "latency_ms": round((perf_counter() - started) * 1000),
                "error": str(exc),
                "system": {"prediction": {}, "score": score_fields({}, truth), "workbook": {"success": False}},
            })
    return results

__all__ = [name for name in globals() if not name.startswith("__")]
