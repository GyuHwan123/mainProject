from pathlib import Path
import json, re

TEST=Path(r"C:\Users\2Class_08\Desktop\영수증 test\라벨링데이터-20260821T030610Z-1-001\라벨링데이터\test01_test20_ground_truth_receipts_taxonomy.json")
VER=Path(r"C:\Users\2Class_08\Desktop\receipt_dataset_verified\receipt_dataset_verified\receipts.json")

def n(v):
    if v is None:return None
    try:return float(str(v).replace(',',''))
    except:return None

def audit(label,path):
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    print(f"\n[{label}] count={len(data)}")
    seen=set()
    for row in data:
        image=row.get('image'); issues=[]
        if image in seen:issues.append('DUPLICATE_IMAGE')
        seen.add(image)
        required=('image','가게명','구매일자','구매물품','총 물품 수량','총 결제액','카테고리','결제방식','카드번호')
        missing=[k for k in required if k not in row]
        if missing:issues.append('MISSING_KEYS='+','.join(missing))
        items=row.get('구매물품') or []
        qty=sum(n(x.get('수량')) or 0 for x in items)
        stated=n(row.get('총 물품 수량'))
        if items and stated is None:issues.append('QTY_NULL_WITH_ITEMS')
        if stated is not None and abs(qty-stated)>.001:issues.append(f'QTY_SUM({qty}!={stated})')
        if not items and stated not in (None,0):issues.append(f'QTY_WITHOUT_ITEMS({stated})')
        for i,x in enumerate(items):
            unit=n(x.get('단가')); q=n(x.get('수량')); amount=n(x.get('금액'))
            if not str(x.get('상품명') or '').strip():issues.append(f'ITEM{i+1}_NAME_EMPTY')
            if None not in (unit,q,amount) and abs(unit*q-amount)>1:
                issues.append(f'ITEM{i+1}_ARITH({unit}*{q}!={amount})')
        gross=sum(n(x.get('금액')) or 0 for x in items)
        total=n(row.get('총 결제액'))
        discount=n(row.get('할인액'))
        if items and total is not None:
            if discount is not None and abs(gross-discount-total)>1:
                issues.append(f'GROSS_DISCOUNT_TOTAL({gross}-{discount}!={total})')
            elif discount is None and abs(gross-total)>1:
                issues.append(f'ITEM_SUM_TOTAL({gross}!={total})')
        date=str(row.get('구매일자') or '')
        if date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}(?::\d{2})?)?',date):issues.append('DATE_FORMAT')
        if issues:print(image, '|', '; '.join(issues), '| total=',total,'discount=',discount,'gross=',gross)

audit('test',TEST)
audit('verified',VER)
