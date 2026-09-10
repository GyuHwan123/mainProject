import json
import sys
from pathlib import Path
import urllib.request
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        env[key.strip()] = value.strip().strip('\"').strip("'")
key = env['SUPABASE_SERVICE_ROLE_KEY']
headers = {'apikey': key}
if key.startswith('eyJ'):
    headers['Authorization'] = 'Bearer ' + key

def get(table, params):
    url = env['SUPABASE_URL'].rstrip('/') + '/rest/v1/' + table + '?' + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
        return json.load(response)

batches = get('finance_evaluation_batches', {'select': 'id,batch_name,model_name,total_items,completed_items,failed_items,created_at,status,dataset_name,summary_metrics', 'evaluation_mode': 'eq.BULK', 'order': 'created_at.desc', 'limit': '10'})
if len(sys.argv) == 1:
    print(json.dumps(batches, ensure_ascii=False, indent=2))
else:
    batch = next(b for b in batches if b['id'] == sys.argv[1])
    rows = get('finance_record_evaluations', {'select': '*', 'batch_id': 'eq.' + batch['id'], 'order': 'evaluated_at.asc'})
    items = get('finance_evaluation_items', {'select': '*', 'batch_id': 'eq.' + batch['id'], 'order': 'dataset_index.asc'})
    out = ROOT / 'report_work' / ('previous_20_evaluation_source.json' if len(sys.argv) > 2 else 'latest_20_evaluation_source.json')
    out.write_text(json.dumps({'batch': batch, 'evaluations': rows, 'items': items}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'path': str(out), 'evaluations': len(rows), 'items': len(items)}))
