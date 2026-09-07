"""Evaluate post-LLM item grounding using saved model responses, without inference.

python scripts/evaluate_receipt_grounding.py --input /tmp/batch.json --output /tmp/grounding.json
Repeat --input to replay several model exports. No OCR, Ollama or database writes.
"""
import argparse
import copy
import json
from pathlib import Path
from statistics import mean
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.routes.finance import _simple_receipt_prompt
from app.services.finance_receipt_simple import _clean_model_items
from app.services.finance_evaluation_scoring import normalize_ground_truth, score_fields
from app.services.receipt_item_grounding import ground_items, VERSION


def item_errors(items, truth, score):
    detail = score['fields']['items']
    counts = {key: sum(not row['fields'][key]['correct'] for row in detail['items']
                      if key in row['fields']) for key in ('name', 'quantity', 'unit_price', 'total_amount')}
    counts['item_count'] = int(not detail['count_correct'])
    # EXTRA_ITEM is the evaluator's surplus-item count; missing fields are counted above.
    counts['EXTRA_ITEM'] = detail['false_positive_count']
    def quantity(rows):
        try:
            return sum(float(str(i.get('quantity')).replace(',', '')) for i in rows)
        except (TypeError, ValueError):
            return None
    expected = truth.get('total_quantity')
    if expected is None:
        expected = quantity(truth.get('items') or [])
    actual = quantity(items)
    counts['total_quantity'] = int(actual is None or expected is None or actual != float(expected))
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = dict(baseline='cleaned_saved_llm_items_before_grounding', extra_item_definition='surplus predicted count: max(0, predicted - expected); not error-analysis tag count', version=VERSION, extra_llm_calls=0, live_latency_measured=False, cases=[])
    for path in args.input:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        for run in data['runs']:
            text, pages = run['ocr_text'], run.get('ocr_pages') or []
            filename = run['document_name']
            # Verify layout never changes the actual model input.
            assert _simple_receipt_prompt(text, filename)[0] == _simple_receipt_prompt(text, filename, pages)[0]
            truth = normalize_ground_truth(run.get('normalized_ground_truth') or run['ground_truth'])
            for result in run['results']:
                llm = result.get('system', {}).get('pipeline_trace', {}).get('llm', {})
                if not llm.get('response_text'):
                    continue
                parsed = json.loads(llm['response_text'])
                before = _clean_model_items(parsed.get('items'))
                after = copy.deepcopy(before)
                trace = ground_items(after, text, pages)
                scores = [score_fields(dict(items=items), dict(items=truth.get('items') or [])) for items in (before, after)]
                error_counts = [item_errors(items, truth, score) for items, score in zip((before, after), scores)]
                comparisons = []
                for old, new in zip(scores[0]['fields']['items']['items'], scores[1]['fields']['items']['items']):
                    for field, old_value in old['fields'].items():
                        new_value = new['fields'][field]
                        comparisons.append(dict(index=old['index'], field=field, expected=old_value['expected'],
                                                before=old_value['actual'], after=new_value['actual'],
                                                correct_before=old_value['correct'], correct_after=new_value['correct']))
                report['cases'].append(dict(source=path.name, image=filename, model=result['model_name'],
                                           before_items=before, after_items=after, grounding=trace, comparisons=comparisons,
                                           errors_before=error_counts[0], errors_after=error_counts[1]))
    fields = [field for case in report['cases'] for field in case['comparisons']]
    numeric = [f for f in fields if f['field'] != 'name']
    report['summary'] = dict(cases=len(report['cases']), documents_changed=sum(bool(c['grounding']['changed_items'] or c['grounding']['added_items'] or c['grounding']['removed_items']) for c in report['cases']),
                            changed_items=sum(c['grounding']['changed_items'] for c in report['cases']),
                            numeric_fields=len(numeric), numeric_correct_before=sum(f['correct_before'] for f in numeric),
                            numeric_correct_after=sum(f['correct_after'] for f in numeric),
                            fixed_fields=sum(not f['correct_before'] and f['correct_after'] for f in numeric),
                            regressed_fields=sum(f['correct_before'] and not f['correct_after'] for f in numeric),
                            grounding_ms_mean=mean(c['grounding']['elapsed_ms'] for c in report['cases']) if report['cases'] else 0,
                            prompt_identical=True)
    report['summary']['item_error_counts'] = {key: dict(
        before=sum(c['errors_before'][key] for c in report['cases']),
        after=sum(c['errors_after'][key] for c in report['cases']),
        delta=sum(c['errors_after'][key] - c['errors_before'][key] for c in report['cases']))
        for key in ('item_count', 'total_quantity', 'name', 'quantity', 'unit_price', 'total_amount', 'EXTRA_ITEM')}
    report['summary']['added_items'] = sum(len(c['grounding']['added_items']) for c in report['cases'])
    report['summary']['removed_items'] = sum(len(c['grounding']['removed_items']) for c in report['cases'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
