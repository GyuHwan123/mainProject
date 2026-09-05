"""Local document routing from structured fields only; never reads OCR or calls an LLM."""
from __future__ import annotations

from functools import lru_cache
import logging
import math
from pathlib import Path
import re
from typing import Any

from app.constants.finance_taxonomy import (
    ALLOWED_DOCUMENT_TYPES, CATEGORY_TO_DOCUMENT_TYPE, normalize_expense_category,
)

ARTIFACT_PATH = Path(__file__).resolve().parents[2] / "data/document-classifier/model.joblib"
FEATURE_VERSION = "structured-receipt-v1"
logger = logging.getLogger(__name__)


def feature_text(receipt: dict[str, Any], *, with_description: bool = False,
                 with_category: bool = True) -> str:
    """Allowlist fields, with identical bounds at training and serving time."""
    def clean(value: Any, limit: int) -> str:
        return " ".join(str(value or "").lower().split())[:limit]

    parts = []
    category = normalize_expense_category(receipt.get("expense_category"))
    if with_category and category:
        parts.append("category=" + category)
    merchant = clean(receipt.get("merchant"), 300)
    if merchant:
        parts.append("merchant=" + merchant)
    items = receipt.get("items")
    for item in (items[:60] if isinstance(items, list) else []):
        if isinstance(item, dict) and item.get("name"):
            parts.append("item=" + clean(item["name"], 160))
    if with_description and receipt.get("transaction_description"):
        parts.append("description=" + clean(receipt["transaction_description"], 240))
    return "\n".join(parts)


def strong_context_rule(receipt: dict[str, Any]) -> str | None:
    # Only an explicit, already structured business description is strong.
    # Category mappings are weak priors, so a different category never vetoes a model.
    description = str(receipt.get("transaction_description") or "")[:240]
    if re.search(r"아님|아닌|취소|개인|not\b|cancel", description, re.I):
        return None
    signals = set()
    if re.search(r"출장", description):
        signals.add("TRAVEL_EXPENSE")
    if re.search(r"직원\s*복지|복리후생", description):
        signals.add("WELFARE_BENEFIT")
    return next(iter(signals)) if len(signals) == 1 else None


@lru_cache(maxsize=1)
def _load_artifact() -> dict[str, Any] | None:
    try:
        import joblib
        # Deployment-owned artifact only: no user path or uploaded pickle is loaded.
        artifact = joblib.load(ARTIFACT_PATH)
        if artifact.get("feature_version") != FEATURE_VERSION:
            raise ValueError("unsupported classifier feature version")
        if set(artifact["model"].classes_) != set(ALLOWED_DOCUMENT_TYPES):
            raise ValueError("classifier must contain all four classes")
        return artifact
    except Exception as exc:
        logger.warning("Document classifier unavailable: %s", type(exc).__name__)
        return None


def classify_document_type(receipt: dict[str, Any]) -> dict[str, Any]:
    category = normalize_expense_category(receipt.get("expense_category"))
    fallback = CATEGORY_TO_DOCUMENT_TYPE.get(category)
    strong = strong_context_rule(receipt)
    decision: dict[str, Any] = {
        "selected_document_type": strong or fallback,
        "predicted_document_type": None, "expense_category": category,
        "confidence": None, "threshold": None, "probabilities": {},
        "source": "RULE_FALLBACK", "status": "REVIEW", "reasons": [],
        "fallback_document_type": fallback, "strong_rule_document_type": strong,
        "feature_version": FEATURE_VERSION,
    }
    artifact = _load_artifact()
    if artifact is None:
        decision["reasons"].append("DOCUMENT_CLASSIFIER_UNAVAILABLE")
        return decision
    try:
        profile = artifact["profile"]
        text = feature_text(receipt, with_description=profile["with_description"],
                            with_category=profile["with_category"])
        if not text:
            decision["reasons"].append("DOCUMENT_CLASSIFIER_EMPTY_FEATURES")
            return decision
        model = artifact["model"]
        probabilities = model.predict_proba([text])[0]
        if not all(math.isfinite(float(p)) for p in probabilities):
            raise ValueError("non-finite probabilities")
        best = int(probabilities.argmax())
        predicted, confidence = str(model.classes_[best]), float(probabilities[best])
        threshold = artifact["thresholds"].get(predicted)
        decision.update({
            "predicted_document_type": predicted, "confidence": confidence,
            "threshold": threshold, "model_version": artifact["model_version"],
            "probabilities": dict(zip(map(str, model.classes_), map(float, probabilities))),
            "category_mapping_disagrees": bool(fallback and fallback != predicted),
        })
        if threshold is None or not 0 <= float(threshold) <= 1 or confidence < threshold:
            decision["reasons"].append("DOCUMENT_CLASSIFIER_LOW_CONFIDENCE")
        else:
            decision["selected_document_type"] = predicted
            decision["source"] = "CLASSIFIER"
        if strong and strong != predicted:
            decision["reasons"].append("DOCUMENT_CLASSIFIER_STRONG_RULE_CONFLICT")
        if not category:
            decision["reasons"].append("DOCUMENT_CLASSIFIER_INVALID_CATEGORY")
        if not artifact.get("production_validated", False):
            decision["reasons"].append("DOCUMENT_CLASSIFIER_SYNTHETIC_VALIDATION_ONLY")
        decision["status"] = "REVIEW" if decision["reasons"] else "PASS"
        return decision
    except Exception as exc:
        logger.warning("Document classifier inference failed: %s", type(exc).__name__)
        decision["reasons"].append("DOCUMENT_CLASSIFIER_FAILED")
        return decision
