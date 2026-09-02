import json
from pathlib import Path
d=json.loads(Path(r"C:\Users\2Class_08\Desktop\receipt_dataset_verified\receipt_dataset_verified\receipts.json").read_text(encoding="utf-8-sig"))
wanted={'receipt_023.jpg','receipt_026.jpg','receipt_048.jpg','receipt_049.jpg','receipt_052.jpg','receipt_36.jpg','receipt_047.jpg'}
for x in d:
    if x.get('image') in wanted:
        print('\n',x)
