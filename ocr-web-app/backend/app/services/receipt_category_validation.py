"""Validate category suggestions against OCR without modifying extraction fields."""
from app.constants.finance_taxonomy import normalize_expense_category, refine_expense_category

MAX_CATEGORY_EVIDENCE = 2
MAX_CATEGORY_EVIDENCE_CHARS = 40


def validate_category(receipt: dict, text: str) -> dict:
    suggestion = receipt.get('expense_category_suggestion', receipt.get('expense_category'))
    category = normalize_expense_category(suggestion)
    evidence = receipt.get('expense_category_evidence')
    reasons = []
    matched = []
    lines = text.splitlines()
    if suggestion not in (None, '') and category is None:
        reasons.append('CATEGORY_INVALID_VALUE')
    if evidence is not None and not isinstance(evidence, list):
        reasons.append('CATEGORY_INVALID_EVIDENCE')
    if isinstance(evidence, list) and len(evidence) > MAX_CATEGORY_EVIDENCE:
        reasons.append('CATEGORY_EVIDENCE_LIMIT_EXCEEDED')
    for entry in evidence[:MAX_CATEGORY_EVIDENCE] if isinstance(evidence, list) else []:
        if not isinstance(entry, dict):
            reasons.append('CATEGORY_INVALID_EVIDENCE')
            continue
        quote = entry.get('text')
        if isinstance(quote, str) and len(quote) > MAX_CATEGORY_EVIDENCE_CHARS:
            # Never turn an invalid full quote into apparently grounded evidence by truncating it.
            reasons.append('CATEGORY_EVIDENCE_LIMIT_EXCEEDED')
            continue
        # Exact line references are computed by the validator, never trusted from the model.
        positions = [i + 1 for i, line in enumerate(lines)
                     if isinstance(quote, str) and quote.strip() and quote.strip() in line]
        if not positions:
            reasons.append('CATEGORY_EVIDENCE_NOT_IN_OCR')
        else:
            matched.append({'text': quote.strip(), 'ocr_lines': positions})
    recommendation = refine_expense_category(category, text) if category else None
    if reasons:
        decision = 'REVIEW'
    else:
        decision = 'USER_CONFIRM'
        if not category:
            reasons.append('CATEGORY_SUGGESTION_MISSING')
        if not matched:
            reasons.append('CATEGORY_EVIDENCE_MISSING')
        if recommendation != category:
            reasons.append('CATEGORY_CONTEXT_CONFLICT')
        # A quote match is not proof of purchase or semantic category correctness.
        # No production-validated acceptance policy is available yet.
        reasons.append('CATEGORY_ACCEPTANCE_NOT_VALIDATED')
    return {'suggested_category': suggestion, 'normalized_category': category,
            'recommended_category': recommendation, 'decision': decision,
            'reasons': list(dict.fromkeys(reasons)), 'matched_evidence': matched,
            'validator_version': 'category-evidence-v2-bounded'}
