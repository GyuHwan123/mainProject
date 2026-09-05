# Item grounding v3 validation

All changes run in Python after the existing single Ollama call. Prompt contents are unchanged.

Numeric repairs require the resulting quantity * unit_price to equal total_amount within 1 currency unit. Coherent model values are preserved. Volume suffix OCR confusion (500m1 / 500ml) is normalized only for matching, never for display. Missing additions and summary removals are evaluated independently of total item count. Additions still require explicit name/quantity/unit-price/amount columns, confident OCR, and unique arithmetic. Unconfirmed summary names are preserved.

Per-item trace includes original_item, matched_ocr_row where available, corrected_item, action, reason and rule confidence. Confidence is a heuristic, not a calibrated probability.

Replay: 22 saved responses from grounding-20260905.json, no new inference. Baseline is cleaned saved LLM items before grounding. Results are in reports/receipt_grounding_v3_replay.json. The report records field errors against the existing evaluator matching, document item-count errors, and summed item quantity errors. EXTRA_ITEM here means surplus predicted count, not the separate error-analysis service tag (which distinguishes discount/total/hallucination).

Errors before -> after: item count 9 -> 9; total quantity 8 -> 8; name 20 -> 20; quantity 11 -> 11; unit price 31 -> 30; item amount 31 -> 31; surplus items 14 -> 14. No numeric regressions. No additions/removals in this dataset met the strict thresholds. receipt_030.jpg unit price was corrected from 29700 to 3300 for quantity 9 and amount 29700.

Validation: 28 grounding tests and 99 finance regression tests passed. Synthetic cases cover swapped prices, adjacent-row contamination, simultaneous missing/summary rows at equal counts, confirmed tax/total removal, uncertain rows, and idempotence.

Reproduce in backend environment:

```sh
python scripts/evaluate_receipt_grounding.py --input ../reports/receipt_grounding_replay_input.json --output ../reports/receipt_grounding_v3_replay.json
python -m unittest discover -s tests -p 'test_receipt_item_grounding.py'
python -m unittest discover -s tests -p 'test_finance_*.py'
```
