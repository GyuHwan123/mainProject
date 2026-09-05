"""Ground item fields and cardinality in OCR geometry, after the LLM call."""
from __future__ import annotations

from collections import Counter
from bisect import bisect_left
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import permutations
import math
import re
from statistics import median
from time import perf_counter
from typing import Any


VERSION = 'bbox-item-grounding-v4-header-table'
FIELDS = ('quantity', 'unit_price', 'total_amount')
_NUMBER = re.compile(r'^[₩￦]?(?P<n>-?(?:\d{1,3}(?:[,.]\d{3})+|\d+)(?:\.\d+)?)(?P<unit>개|ea|원)?$', re.I)
_EXCLUDE = re.compile(r'할인|쿠폰|취소|반품|환불|공급가액|부가세|과세|면세|합계|소계|결제|승인|사업자|카드|현금|subtotal|total', re.I)
_HEADERS = {
    'name': re.compile(r'^(상품명|품명|품목|메뉴|메뉴명|제품명|상품)$'),
    'quantity': re.compile(r'^(수량|qty)$', re.I),
    'unit_price': re.compile(r'^(단가|판매가|unitprice)$', re.I),
    'total_amount': re.compile(r'^(금액|합계|amount)$', re.I),
    'discount': re.compile(r'^(할인|할인액)$'),
}


def _compact(value: str) -> str:
    return re.sub(r'\s+', '', value).lower()


def _name_key(value: str) -> str:
    # OCR frequently reads the l in a volume suffix as the digit 1.
    value = re.sub(r'(?<=\d)m1\b', 'ml', value, flags=re.I)
    return re.sub(r'[^a-z0-9가-힣]', '', value.lower())


def _box(value: Any) -> tuple[float, float, float, float] | None:
    try:
        if not isinstance(value, list) or len(value) not in (2, 4):
            return None
        points = [(float(p[0]), float(p[1])) for p in value]
        if not all(math.isfinite(v) for p in points for v in p):
            return None
        xs, ys = zip(*points)
        result = min(xs), min(ys), max(xs), max(ys)
        return result if result[2] > result[0] and result[3] > result[1] else None
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


@dataclass
class Cell:
    text: str
    box: tuple[float, float, float, float]
    confidence: float
    index: int

    @property
    def y(self) -> float:
        return (self.box[1] + self.box[3]) / 2

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]


def _number(cell: Cell) -> tuple[int | float, bool] | None:
    token = _compact(cell.text)
    if re.fullmatch(r'0\d{4,}', token):
        return None  # Product codes are not tendered quantities/prices.
    match = _NUMBER.fullmatch(token)
    if not match:
        return None
    raw = match['n']
    try:
        n = float(re.sub(r'[,.]', '', raw) if re.fullmatch(r'-?\d{1,3}(?:[,.]\d{3})+', raw) else raw.replace(',', ''))
    except ValueError:
        return None
    if not math.isfinite(n) or abs(n) > 100_000_000:
        return None
    return (int(n) if n.is_integer() else n, match['unit'] in ('개', 'ea'))


def _rows(cells: list[Cell]) -> list[list[Cell]]:
    rows: list[list[Cell]] = []
    for cell in sorted(cells, key=lambda c: (c.y, c.box[0])):
        for row in reversed(rows[-3:]):
            tolerance = min(cell.height, median(c.height for c in row)) * .65
            if abs(cell.y - median(c.y for c in row)) > tolerance:
                continue
            if any(min(cell.box[2], c.box[2]) - max(cell.box[0], c.box[0]) > min(cell.box[2] - cell.box[0], c.box[2] - c.box[0]) * .25 for c in row):
                continue
            row.append(cell)
            break
        else:
            rows.append([cell])
    return [sorted(row, key=lambda c: c.box[0]) for row in rows]


def _page_index(text: str, pages: Any) -> list[dict[str, Any]]:
    if not isinstance(pages, list):
        return []
    document = _compact(text)
    indexed, cursor = [], 0
    for page_index, page in enumerate(pages[:30]):
        if not isinstance(page, dict) or not isinstance(page.get('items'), list):
            continue
        page_text = _compact(str(page.get('text') or ''))
        begin = document.find(page_text, cursor) if page_text else -1
        if begin < 0 or len(page['items']) > 2000:
            continue
        cursor = begin + len(page_text)
        cells = []
        for i, item in enumerate(page['items']):
            if not isinstance(item, dict):
                continue
            value = str(item.get('text') or '').strip()
            box = _box(item.get('bbox'))
            try:
                confidence = float(item.get('confidence', 1))
            except (TypeError, ValueError):
                continue
            if box and value and _compact(value) in page_text and math.isfinite(confidence):
                cells.append(Cell(value, box, confidence, i))
        if not cells:
            continue
        anchors = {}
        header_y = None
        for row in _rows(cells):
            found = {key: (c.box[0] + c.box[2]) / 2 for c in row for key, pattern in _HEADERS.items() if pattern.fullmatch(_compact(c.text))}
            if 'name' in found and len(found) >= 2:
                anchors = found
                header_y = median(c.y for c in row)
                break
        # Split merged header labels only when the entire box is known headers.
        headers = []
        for c in cells:
            compact = _compact(c.text)
            parts = list(re.finditer(r'상품명|메뉴명|제품명|품명|품목|상품|메뉴|단가|수량|금액', compact))
            if not parts or ''.join(p.group() for p in parts) != compact or c.confidence < .85:
                continue
            for part in parts:
                role = next(k for k, p in _HEADERS.items() if p.fullmatch(part.group()))
                x = c.box[0] + (part.start() + part.end()) / (2 * len(compact)) * (c.box[2] - c.box[0])
                headers.append(dict(role=role, x=x, cell=c, text=part.group()))
        detected = []
        for name in (h for h in headers if h['role'] == 'name'):
            nearby = [h for h in headers if abs(h['cell'].y - name['cell'].y) <= max(h['cell'].height, name['cell'].height) * 1.2]
            roles = [h['role'] for h in nearby]
            if (len(roles) == len(set(roles)) and {'name', 'quantity', 'total_amount'} <= set(roles)
                    and all(h['x'] > name['x'] for h in nearby if h['role'] != 'name')):
                detected = nearby
                anchors = {h['role']: h['x'] for h in nearby}
                header_y = median(h['cell'].y for h in nearby)
                break
        named = [c for c in cells if _number(c) is None and len(re.findall(r'[a-z가-힣]', c.text.lower())) >= 2 and not _EXCLUDE.search(_compact(c.text))]
        indexed.append(dict(page=page_index + 1, cells=cells, names=_rows(named),
                            numbers=_rows([c for c in cells if _number(c) is not None]),
                            anchors=anchors, header_y=header_y, headers=detected, height=median(c.height for c in cells)))
    return indexed


def _match_name(name: str, pages: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    key = _name_key(name)
    if len(key) < 2 or _EXCLUDE.search(_compact(name)):
        return None, 'unsupported_item_name'
    matches = []
    for page in pages:
        # Search only for this model item. Windows allow names split into boxes.
        for row in page['names']:
            for start in range(len(row)):
                for width in range(1, min(4, len(row) - start) + 1):
                    cells = row[start:start + width]
                    value = ' '.join(c.text for c in cells)
                    candidate_key = _name_key(value)
                    if min(c.confidence for c in cells) < .8:
                        continue
                    score = 1.0 if key == candidate_key else SequenceMatcher(None, key, candidate_key).ratio()
                    if score < .88 or (score < 1 and min(len(key), len(candidate_key)) < 4):
                        continue
                    matches.append(dict(page_data=page, cells=cells, text=value, score=score,
                                        y=median(c.y for c in cells)))
    matches.sort(key=lambda m: m['score'], reverse=True)
    if not matches:
        return None, 'name_not_grounded'
    best = matches[0]
    for other in matches[1:]:
        same_location = other['page_data']['page'] == best['page_data']['page'] and bool(
            {c.index for c in other['cells']} & {c.index for c in best['cells']})
        if not same_location and best['score'] - other['score'] < .12:
            return None, 'ambiguous_name'
    return best, 'matched'


def _nearby_numbers(match: dict[str, Any]) -> tuple[list[Cell] | None, str]:
    page, y = match['page_data'], match['y']
    height = median(c.height for c in match['cells'])
    options = []
    for row in page['numbers']:
        delta = median(c.y for c in row) - y
        if -height * .55 <= delta <= height * 1.4:
            options.append((abs(delta), row))
    options.sort(key=lambda pair: pair[0])
    if not options:
        return None, 'no_nearby_numbers'
    if len(options) > 1 and options[1][0] - options[0][0] < height * .35:
        return None, 'ambiguous_numeric_row'
    row = options[0][1]
    row_y = median(c.y for c in row)
    if any(c.confidence < .85 for c in row):
        return None, 'low_numeric_confidence'
    # Discounts/cancellations and summary labels near this row invalidate repair.
    for c in page['cells']:
        if _EXCLUDE.search(_compact(c.text)) and abs(c.y - row_y) <= height * .75:
            if not any(pattern.fullmatch(_compact(c.text)) for pattern in _HEADERS.values()):
                return None, 'summary_or_discount_row'
    return row, 'matched'


def _resolve_numbers(row: list[Cell], anchors: dict[str, float], height: float) -> tuple[dict[str, Any] | None, str]:
    values = [_number(c) for c in row]
    if len(row) > 4 or any(n < 0 for n, _ in values):
        return None, 'complex_or_negative_row'
    fields, hits = {}, {}
    numeric_anchors = {k: x for k, x in anchors.items() if k != 'name'}
    for i, (c, (n, quantity_mark)) in enumerate(zip(row, values)):
        role = None
        if quantity_mark:
            role = 'quantity'
        elif numeric_anchors:
            x = (c.box[0] + c.box[2]) / 2
            distances = sorted((abs(x - pos), key) for key, pos in numeric_anchors.items())
            if distances[0][0] <= height * 2 and (len(distances) == 1 or distances[1][0] - distances[0][0] >= height * .5):
                role = distances[0][1]
        if role:
            hits.setdefault(role, []).append(i)
    for role, indices in hits.items():
        if len(indices) != 1:
            return None, 'ambiguous_column'
        n = values[indices[0]][0]
        if role == 'discount':
            if n != 0:
                return None, 'discounted_row'
        else:
            fields[role] = n
    if 'quantity' in fields and (not 0 < fields['quantity'] <= 100 or int(fields['quantity']) != fields['quantity']):
        return None, 'invalid_quantity'
    usable = [i for i, (n, _) in enumerate(values) if n > 0]
    if len(usable) == 3:
        possibilities = set()
        for indices in permutations(usable):
            q, u, a = (values[i][0] for i in indices)
            if not 0 < q <= 100 or int(q) != q or abs(q * u - a) > 1:
                continue
            if any(fields.get(k, n) != n for k, n in zip(FIELDS, (q, u, a))):
                continue
            possibilities.add((q, u, a))
        if len(possibilities) == 1:
            return dict(zip(FIELDS, next(iter(possibilities)))), 'unique_arithmetic_with_geometry'
        return None, 'arithmetic_conflict_or_ambiguity'
    # All three operands must be observed on this OCR row. LLM values cannot
    # supply a missing operand merely to make a proposed correction consistent.
    return None, 'insufficient_numeric_evidence'


def _logical_rows(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join name fragments and their uniquely associated numeric row once."""
    logical = []
    for page in pages:
        grouped = {}
        for cells in page['names']:
            # Only text to the left of the numeric columns can name an item.
            cells = [c for c in cells if not any(p.fullmatch(_compact(c.text)) for p in _HEADERS.values())]
            if not cells:
                continue
            match = dict(page_data=page, cells=cells, y=median(c.y for c in cells))
            numbers, _ = _nearby_numbers(match)
            if numbers is None or max(c.box[2] for c in cells) > min(c.box[0] for c in numbers):
                continue
            identity = (page['page'], tuple(sorted(c.index for c in numbers)))
            row = grouped.setdefault(identity, dict(page_data=page, cells=[], numbers=numbers, identity=identity))
            row['cells'].extend(cells)
        for row in grouped.values():
            row['cells'].sort(key=lambda c: (c.y, c.box[0]))
            row['text'] = ' '.join(c.text for c in row['cells'])
            row['fields'], row['reason'] = _resolve_numbers(row['numbers'], page['anchors'], page['height'])
            # Merged vertical names also participate in the existing name grounding.
            if row['cells'] not in page['names']:
                page['names'].append(row['cells'])
            logical.append(row)
    return logical


def _related_name(name: str, candidate: str) -> bool:
    key, other = _name_key(name), _name_key(candidate)
    return bool(key and other) and (key in other or other in key or SequenceMatcher(None, key, other).ratio() >= .6)


def _table_rows(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Header columns select rows; arithmetic only qualifies individual repairs."""
    tables = []
    for page in pages:
        headers, anchors, height = page['headers'], page['anchors'], page['height']
        page['table_reason'] = 'no_complete_header_schema'
        if not headers:
            continue
        roles = sorted(anchors, key=anchors.get)
        numeric_roles = [k for k in roles if k != 'name']
        gaps = [anchors[b] - anchors[a] for a, b in zip(roles, roles[1:])]
        if min(gaps) < height * .6:
            page['table_reason'] = 'overlapping_header_columns'
            continue
        left, right = min(headers, key=lambda h: h['x']), max(headers, key=lambda h: h['x'])
        slope = (right['cell'].y - left['cell'].y) / (right['x'] - left['x'])
        if abs(slope) > .2:
            page['table_reason'] = 'excessive_header_skew'
            continue
        def row_y(c):
            return c.y - slope * ((c.box[0] + c.box[2]) / 2 - anchors['name'])
        top = max(row_y(h['cell']) for h in headers)
        # Receipt summary labels terminate the item area, not arbitrary OCR order.
        stops = [row_y(c) for c in page['cells'] if row_y(c) > top + height * .5
                 and re.search(r'합계|주문총액|소계|부가세|공급가액|결제|승인|계금액|subtotal|total', _compact(c.text), re.I)]
        bottom = min(stops, default=float('inf'))
        body = [c for c in page['cells'] if top + height * .4 < row_y(c) < bottom - height * .35]
        width = max(c.box[2] for c in page['cells']) - min(c.box[0] for c in page['cells'])
        columns = {k: [] for k in numeric_roles}
        names = []
        name_boundary = (anchors['name'] + anchors[numeric_roles[0]]) / 2
        for c in body:
            n = _number(c)
            if n is None:
                if (c.box[0] < name_boundary and c.box[2] < anchors[numeric_roles[0]]
                        and len(re.findall(r'[a-z가-힣]', c.text.lower())) >= 2):
                    names.append(c)
                continue
            x = (c.box[0] + c.box[2]) / 2
            distances = sorted((abs(x - anchors[k]), k) for k in numeric_roles)
            distance, role = distances[0]
            gap = min(abs(anchors[role] - anchors[k]) for k in roles if k != role)
            tolerance = min(gap * .46, max(height * 1.3, (c.box[2] - c.box[0]) * .65, width * .025))
            if distance <= tolerance and (len(distances) == 1 or distances[1][0] - distance > gap * .12):
                columns[role].append(c)
        seeds = sorted(columns['total_amount'], key=row_y)
        if len(seeds) < 2:
            page['table_reason'] = 'insufficient_repeated_amount_rows'
            continue
        ys = [row_y(c) for c in seeds]
        rows = [dict(page_data=page, cells=[], numbers=[c], fields={'total_amount': _number(c)[0]},
                     columns={'total_amount': c}, ambiguous=False) for c in seeds]
        def attach(c):
            y = row_y(c)
            pos = bisect_left(ys, y)
            options = sorted((abs(y - ys[i]), i) for i in (pos - 1, pos) if 0 <= i < len(ys))
            delta, index = options[0]
            neighbors = [abs(ys[index] - ys[j]) for j in (index - 1, index + 1) if 0 <= j < len(ys)]
            tolerance = min(max(height, c.height) * .85, min(neighbors) * .46)
            if delta > tolerance or (len(options) > 1 and options[1][0] - delta < height * .15):
                return None
            return rows[index]
        for role in numeric_roles:
            if role == 'total_amount':
                continue
            for c in columns[role]:
                row = attach(c)
                if row is None:
                    continue
                if role in row['columns']:
                    row['ambiguous'] = True
                row['columns'][role] = c
                row['numbers'].append(c)
                row['fields'][role] = _number(c)[0]
        unattached = []
        for c in names:
            row = attach(c)
            if row is None:
                unattached.append(c)
            else:
                row['cells'].append(c)
        repeated = sum(len(r['columns']) >= 2 and not r['ambiguous'] for r in rows)
        if repeated < 2:
            page['table_reason'] = 'insufficient_repeated_numeric_columns'
            continue
        # Child rows must be real option evidence, not minor shifts of product x.
        children = [c for c in unattached if re.search(r'옵션|소스|토핑|사이드|추가|구성', c.text)
                    and c.box[0] > min((n.box[0] for n in names), default=0) + height]
        if len(children) >= 2:
            page['table_reason'] = 'unresolved_child_rows'
            continue
        for index, row in enumerate(rows):
            row['cells'].sort(key=lambda c: c.box[0])
            row['text'] = ' '.join(c.text for c in row['cells'])
            row['identity'] = (page['page'], tuple(sorted(c.index for c in row['numbers'])))
            row['row_index'] = index
            all_cells = row['cells'] + row['numbers']
            complete = bool(row['cells']) and set(row['fields']) == set(numeric_roles) and not row['ambiguous']
            excluded = bool(_EXCLUDE.search(_compact(row['text'])))
            # Adjacent discount text invalidates repair of its product, never
            # substitutes the discount or final price for a quantity.
            discounted = any(re.search(r'할인|쿠폰|%', c.text) and abs(row_y(c) - ys[index]) < height for c in body)
            q, u, amount = (row['fields'].get(k) for k in FIELDS)
            arithmetic = (q is not None and 0 < q <= 100 and int(q) == q and amount >= 0
                          and (u is None or u >= 0 and abs(q * u - amount) <= 1))
            row['row_confidence'] = min(c.confidence for c in all_cells) if complete and not excluded and not discounted else 0.0
            row['repairable'] = row['row_confidence'] >= .9 and arithmetic
            row['reason'] = ('strong_same_row_table_geometry' if row['repairable'] else
                             'incomplete_or_low_confidence_row' if row['row_confidence'] < .9 else 'row_arithmetic_conflict')
        page['table_reason'] = 'header_schema_and_repeated_numeric_columns'
        page['table_schema'] = '4_COLUMN' if 'unit_price' in anchors else '3_COLUMN'
        page['table_complete'] = all(r['repairable'] for r in rows) and not unattached
        tables.extend(rows)
    return tables


_SUMMARY_NAME = re.compile(
    r'^(?:총합계|합계|소계|총액|총금액|결제금액|결제|할인|할인액|쿠폰할인|'
    r'부가세|부가가치세|세금|공급가액|과세|면세|카드|신용카드|현금|현금결제|카드결제|승인|승인금액|승인번호|'
    r'total|subtotal|tax|vat|discount|payment|cash|creditcard)$', re.I)


def _item_layout(pages: list[dict[str, Any]], table_rows: list[dict[str, Any]]) -> tuple[str, float, str]:
    """Classify repeated item-area geometry, never merchant/LLM metadata."""
    layouts = []
    for page in pages:
        if any(r['identity'][0] == page['page'] for r in table_rows):
            layouts.append(('COLUMN_TABLE', .95, 'header_schema_and_repeated_numeric_columns'))
            continue
        height = page['height']
        bands = []
        for row in _rows(page['cells']):
            if page['header_y'] is not None and min(c.y for c in row) <= page['header_y']:
                continue
            label = _compact(' '.join(c.text for c in row if _number(c) is None))
            if re.fullmatch(r'총합계|합계|소계|결제금액|결제|공급가액|부가세|total|subtotal|payment', label, re.I):
                break
            if min(c.confidence for c in row) < .9:
                continue
            bands.append(row)
        children = discounts = 0
        main = None
        product = None
        discount_pending = False
        suspicious = False
        for row in bands:
            label = _compact(' '.join(c.text for c in row if _number(c) is None))
            numbers = [_number(c)[0] for c in row if _number(c) is not None]
            y, x = median(c.y for c in row), min(c.box[0] for c in row)
            is_discount = bool(re.search(r'할인|discount|\d+(?:\.\d+)?%', label, re.I))
            is_final = bool(re.fullmatch(r'최종가격|최종금액|할인후금액|할인가|판매금액|최종가', label))
            if (product and label in ('정가', '정상가', '정상가격') and numbers
                    and 0 < y - product[0] <= height * 2):
                main, discount_pending = product, False
                continue
            if is_discount or is_final:
                suspicious = True
                if main and 0 < y - main[0] <= height * 5:
                    if is_final and discount_pending and numbers:
                        discounts += 1
                        main, discount_pending = None, False
                    elif is_discount and (numbers or '%' in label):
                        discount_pending = True
                continue
            if (main and 0 < y - main[0] <= height * 5 and x - main[1] >= height
                    and label and (not numbers or all(n == 0 for n in numbers))):
                children += 1
                suspicious = True
                continue
            if label and any(n > 0 for n in numbers) and not _EXCLUDE.search(label):
                main, discount_pending = (y, x), False
            elif label and not numbers and not _EXCLUDE.search(label):
                product = (y, x)
        if children >= 2 and discounts >= 2:
            layouts.append(('UNKNOWN', 0.0, 'conflicting_layout_evidence'))
        elif discounts >= 2:
            layouts.append(('DISCOUNT_BLOCK', .9, 'repeated_product_discount_final_blocks'))
        elif children >= 2:
            layouts.append(('HIERARCHICAL', .9, 'repeated_indented_child_rows'))
        elif suspicious:
            layouts.append(('UNKNOWN', 0.0, 'incomplete_special_layout_evidence'))
        elif any(r['identity'][0] == page['page'] for r in table_rows):
            layouts.append(('COLUMN_TABLE', .95, 'repeated_same_band_name_and_numeric_columns'))
        else:
            layouts.append(('UNKNOWN', 0.0, 'insufficient_layout_evidence'))
    if len({kind for kind, _, _ in layouts}) > 1:
        return 'UNKNOWN', 0.0, 'mixed_page_layouts'
    return min(layouts, key=lambda result: result[1]) if layouts else ('UNKNOWN', 0.0, 'no_usable_layout')


def _reconcile_count(items: list, logical: list[dict[str, Any]], pages: list[dict[str, Any]], trace: dict) -> None:
    original = list(items)
    trace['logical_row_count'] = len(logical)
    # Reserve every plausible match, including ambiguous matches and coherent LLM
    # items that do not need numeric repair. Uncertainty must never create duplicates.
    related = {i: [r for r in logical if _related_name(str(item.get('name') or ''), r['text'])]
               for i, item in enumerate(original) if isinstance(item, dict)}
    if logical:
        for row in logical:
            if any(row in matches for matches in related.values()):
                continue
            fields = row['fields']
            if (not row.get('repairable') or row['row_confidence'] < .95
                    or not row['page_data'].get('table_complete')
                    or _EXCLUDE.search(_compact(row['text']))):
                continue
            # New items require explicit table columns, not arithmetic alone.
            if not all(k in row['page_data']['anchors'] for k in ('name', 'quantity', 'total_amount')):
                continue
            page = row['page_data']
            top = min(c.y for c in row['cells'])
            bottom = max(c.y for c in row['numbers'])
            if page['header_y'] is None or top <= page['header_y']:
                continue
            if any(page['header_y'] < c.y < bottom and _SUMMARY_NAME.fullmatch(_compact(c.text))
                   for c in page['cells']):
                continue
            item = dict(name=row['text'], **fields)
            items.append(item)
            trace['added_items'].append(dict(item=dict(item), page=row['identity'][0],
                                            reason='strong_unmatched_logical_row'))
    if original:
        removals = []
        for i, item in enumerate(original):
            if not isinstance(item, dict) or related.get(i):
                continue
            name = _compact(str(item.get('name') or ''))
            if not _SUMMARY_NAME.fullmatch(name):
                continue
            # Require a matching OCR summary label with a numeric value on its row.
            supported = any(any(_compact(c.text) == name and c.confidence >= .95 for c in row)
                            and any(_number(c) is not None and c.confidence >= .85 for c in row)
                            for page in pages for row in _rows(page['cells']))
            if supported:
                removals.append(dict(index=i, item=dict(item), reason='ocr_summary_not_item'))
        # A suspicious batch is kept intact instead of choosing arbitrary items
        # to delete. The budget uses the original count, before OCR additions.
        if len(removals) <= max(1, len(original) // 5):
                trace['removed_items'].extend(removals)
        else:
            for entry in trace['items']:
                if any(r['index'] == entry['index'] for r in removals):
                    entry['reason'] = 'excessive_removal_guard'
        removed = {e['index'] for e in trace['removed_items']}
        items[:] = [item for i, item in enumerate(items) if i not in removed]


def _match_table_items(items: list, rows: list[dict[str, Any]]) -> dict[int, tuple[int, float]]:
    """Match names first; established neighboring matches can resolve row order.

    At most 50 LLM items are considered, independently of OCR box count.
    Numeric values are deliberately absent from this matching step.
    """
    candidates = {}
    keys = [_name_key(r['text']) for r in rows]
    for i, item in enumerate(items[:50]):
        if not isinstance(item, dict):
            continue
        key = _name_key(str(item.get('name') or ''))
        if len(key) < 2:
            continue
        scores = []
        for j, other in enumerate(keys):
            if len(other) < 2:
                continue
            score = 1.0 if key == other else SequenceMatcher(None, key, other).ratio()
            if score >= .88 and (score == 1 or min(len(key), len(other)) >= 4):
                scores.append((j, score))
        if scores:
            best = max(score for _, score in scores)
            candidates[i] = [(j, score) for j, score in scores if best - score < .12]
    matched = {i: options[0] for i, options in candidates.items() if len(options) == 1}
    # Do not guess duplicate names from order alone. Require two unique bracketing
    # matches, an unused row, and agreement on page identity.
    usage = Counter(j for j, _ in matched.values())
    for i, options in candidates.items():
        if i in matched:
            continue
        before = [k for k in matched if k < i and usage[matched[k][0]] == 1]
        after = [k for k in matched if k > i and usage[matched[k][0]] == 1]
        if not before or not after:
            continue
        left, right = matched[max(before)][0], matched[min(after)][0]
        options = [(j, score) for j, score in options if left < j < right and usage[j] == 0
                   and rows[left]['identity'][0] == rows[j]['identity'][0] == rows[right]['identity'][0]]
        if len(options) == 1:
            matched[i] = options[0]
            usage[options[0][0]] += 1
    return matched


def ground_items(items: Any, text: str, pages: Any) -> dict[str, Any]:
    """Mutate fields and count only when independent OCR evidence is strong."""
    started = perf_counter()
    trace: dict[str, Any] = dict(version=VERSION, changed_items=0, items=[], added_items=[], removed_items=[], table_detected=False)
    trace.update(item_layout_type='UNKNOWN', layout_confidence=0.0,
                 layout_reason='no_usable_layout', applied_postprocessor='preserve_llm_items',
                 detected_headers=[], detected_columns=[], table_schema=[], logical_rows=[],
                 row_confidence=[], row_matching=[], corrected_items=[], removal_candidates=[])
    if not isinstance(items, list):
        trace.update(reason='no_items', elapsed_ms=round((perf_counter() - started) * 1000, 3))
        return trace
    indexed = _page_index(text, pages)
    if not indexed:
        trace.update(reason='no_usable_layout', elapsed_ms=round((perf_counter() - started) * 1000, 3))
        return trace
    table_rows = _table_rows(indexed)
    for page in indexed:
        trace['detected_headers'].extend(dict(page=page['page'], role=h['role'], text=h['text'],
            bbox=list(h['cell'].box), x=h['x']) for h in page['headers'])
        trace['detected_columns'].append(dict(page=page['page'], anchors=page['anchors'], reason=page['table_reason']))
        if page.get('table_schema'):
            trace['table_schema'].append(dict(page=page['page'], schema=page['table_schema']))
    for r in table_rows:
        trace['logical_rows'].append(dict(page=r['identity'][0], row_index=r['row_index'], name=r['text'],
            **r['fields'], source_bbox=[list(c.box) for c in r['cells'] + r['numbers']],
            row_confidence=round(r['row_confidence'], 4), reason=r['reason'], repairable=r['repairable']))
    trace['row_confidence'] = [r['row_confidence'] for r in trace['logical_rows']]
    kind, confidence, reason = _item_layout(indexed, table_rows)
    preserve = kind in ('HIERARCHICAL', 'DISCOUNT_BLOCK') or (kind == 'UNKNOWN' and reason != 'insufficient_layout_evidence')
    trace.update(item_layout_type=kind, layout_confidence=confidence, layout_reason=reason,
                 applied_postprocessor='preserve_llm_items' if preserve else
                 'column_table_grounding' if kind == 'COLUMN_TABLE' else 'conservative_grounding')
    if kind != 'COLUMN_TABLE':
        table_rows = []
    logical = table_rows if kind == 'COLUMN_TABLE' else [] if preserve else _logical_rows(indexed)
    table_matches = _match_table_items(items, table_rows) if table_rows else {}
    trace['table_detected'] = bool(table_rows)
    proposals = []
    for index, item in enumerate(items[:50]):
        if not isinstance(item, dict):
            continue
        entry = dict(index=index, original_item=dict(item), corrected_item=dict(item),
                     matched_ocr_row=None, matched_row=None, table_detected=False, action='kept', confidence=0.0,
                     before={k: item.get(k) for k in FIELDS}, changes={})
        trace['items'].append(entry)
        if preserve:
            entry['reason'] = 'layout_preserves_llm_item'
            continue
        if kind == 'COLUMN_TABLE':
            entry['table_detected'] = True
            if index not in table_matches:
                entry['reason'] = 'unmatched_or_ambiguous_table_name'
                trace['removal_candidates'].append(dict(index=index, reason=entry['reason'], action='kept'))
                continue
            row_index, score = table_matches[index]
            r = table_rows[row_index]
            entry.update(reason=r['reason'], match_score=score, row_confidence=r['row_confidence'],
                         matched_row=dict(page=r['identity'][0], row_index=r['row_index'], text=r['text'],
                             name_boxes=[list(c.box) for c in r['cells']],
                             numbers=[dict(text=c.text, bbox=list(c.box)) for c in r['numbers']]))
            entry['matched_ocr_row'] = entry['matched_row']
            trace['row_matching'].append(dict(item_index=index, page=r['identity'][0],
                                             row_index=r['row_index'], name_score=score))
            if r['repairable']:
                proposals.append((item, entry, r['fields'], r['identity']))
            continue
        # Strong table geometry takes precedence; other layouts retain the
        # existing conservative arithmetic-consistency fallback.
        try:
            q, u, a = (float(str(item.get(k)).replace(',', '')) for k in FIELDS)
            consistent = all(math.isfinite(v) for v in (q, u, a)) and q > 0 and u >= 0 and a >= 0 and abs(q * u - a) <= 1
        except (TypeError, ValueError, OverflowError):
            consistent = False
        candidates = [r for r in logical if _related_name(str(item.get('name') or ''), r['text'])]
        if len(candidates) == 1:
            r = candidates[0]
            entry['matched_ocr_row'] = dict(page=r['identity'][0], text=r['text'],
                numbers=[dict(text=c.text, bbox=list(c.box)) for c in r['numbers']])
        match, reason = _match_name(str(item.get('name') or ''), indexed)
        if match is not None:
            entry['table_detected'] = any(r['identity'][0] == match['page_data']['page'] for r in table_rows)
            grounded = [r for r in table_rows if r['identity'][0] == match['page_data']['page']
                        and _name_key(r['text']) == _name_key(str(item.get('name') or ''))
                        and {c.index for c in match['cells']} == {c.index for c in r['cells']}]
            if len(grounded) == 1:
                r = grounded[0]
                entry.update(reason='strong_same_row_table_geometry', match_score=match['score'],
                             matched_row=dict(page=r['identity'][0], text=r['text'],
                                 name_boxes=[list(c.box) for c in r['cells']],
                                 numbers=[dict(text=c.text, bbox=list(c.box)) for c in r['numbers']]))
                entry['matched_ocr_row'] = entry['matched_row']
                proposals.append((item, entry, r['fields'], r['identity']))
                continue
            if entry['table_detected']:
                entry['reason'] = 'ambiguous_table_row'
                continue
        if consistent:
            entry['reason'] = 'model_arithmetic_consistent'
            continue
        entry['reason'] = reason
        if match is None:
            continue
        entry.update(matched_text=match['text'], match_score=round(match['score'], 4), page=match['page_data']['page'],
                     name_boxes=[list(c.box) for c in match['cells']])
        row, entry['reason'] = _nearby_numbers(match)
        if row is None:
            continue
        entry['numbers'] = [dict(text=c.text, bbox=list(c.box)) for c in row]
        fields, entry['reason'] = _resolve_numbers(row, match['page_data']['anchors'], match['page_data']['height'])
        if fields is not None:
            identity = (entry['page'], tuple(sorted(c.index for c in row)))
            logical_matches = [r for r in logical if r['identity'] == identity
                               and {c.index for c in match['cells']} <= {c.index for c in r['cells']}]
            if (len(logical_matches) != 1
                    or _name_key(match['text']) != _name_key(logical_matches[0]['text'])):
                entry['reason'] = 'ambiguous_logical_row'
                continue
            proposals.append((item, entry, fields, identity))
    counts = Counter(p[3] for p in proposals)
    for item, entry, fields, identity in proposals:
        if counts[identity] > 1:
            entry['reason'] = 'shared_numeric_row'
            continue
        candidate = dict(item, **fields)
        try:
            q, u, a = (float(str(candidate.get(k)).replace(',', '')) for k in FIELDS)
            valid = all(math.isfinite(v) for v in (q, u, a)) and q > 0 and u >= 0 and a >= 0 and abs(q * u - a) <= 1
        except (TypeError, ValueError, OverflowError):
            valid = False
        if kind == 'COLUMN_TABLE' and 'unit_price' not in fields:
            # A three-column table observes quantity and amount independently.
            # Preserve any LLM unit price; never derive a missing price by division.
            valid = fields['quantity'] > 0 and fields['total_amount'] >= 0
        if not valid:
            entry['reason'] = 'candidate_arithmetic_not_verified'
            continue
        for key, value in fields.items():
            if item.get(key) != value:
                entry['changes'][key] = dict(before=item.get(key), after=value)
                item[key] = value
        if entry['changes']:
            trace['changed_items'] += 1
            entry.update(action='corrected', corrected_item=dict(item), confidence=entry.get('match_score', 0))
            trace['corrected_items'].append(dict(index=entry['index'], original_item=entry['original_item'],
                                                 corrected_item=dict(item), reason=entry['reason'], changes=entry['changes']))
        entry['after'] = {k: item.get(k) for k in FIELDS}
    trace['before_count'] = len(items)
    if kind == 'COLUMN_TABLE':
        _reconcile_count(items, logical, indexed, trace)
    else:
        trace['logical_row_count'] = len(logical)
    for removed in trace['removed_items']:
        entry = next((e for e in trace['items'] if e['index'] == removed['index']), None)
        if entry is not None:
            entry.update(action='removed', corrected_item=None, reason=removed['reason'], confidence=.95)
    for added in trace['added_items']:
        trace['items'].append(dict(original_item=None, corrected_item=added['item'],
            matched_ocr_row=dict(page=added['page'], text=added['item']['name']),
            action='added', reason=added['reason'], confidence=.95))
    trace['after_count'] = len(items)
    trace['elapsed_ms'] = round((perf_counter() - started) * 1000, 3)
    return trace
