import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.receipt_item_grounding import ground_items


def cell(text, x, y, width=60, height=20):
    return dict(text=text, bbox=[[x, y], [x + width, y + height]], confidence=.99)


def page_of(*rows):
    items = [c for row in rows for c in row]
    text = '\n'.join(c['text'] for c in items)
    return text, dict(page=1, text=text, items=items)


def header(y=30):
    return [cell('상품명', 0, y), cell('수량', 200, y), cell('단가', 300, y), cell('금액', 420, y)]


def coffee(y=70, name='아메리카노', q='2', u='4,500', a='9,000'):
    return [cell(name, 0, y, width=140), cell(q, 200, y), cell(u, 300, y), cell(a, 420, y)]


def model_item(name='아메리카노', q=2, u=9000, a=4500):
    return dict(name=name, quantity=q, unit_price=u, total_amount=a)


class ItemGroundingTests(unittest.TestCase):
    def test_corrects_swapped_prices_without_changing_names_or_count(self):
        text, page = page_of(header(), coffee())
        items = [model_item()]
        trace = ground_items(items, text, [page])
        self.assertEqual(items, [model_item(q=2, u=4500, a=9000)])
        self.assertEqual(trace['changed_items'], 1)
        self.assertEqual(trace['items'][0]['changes']['unit_price'], dict(before=9000, after=4500))

    def test_nearby_product_price_does_not_leak(self):
        text, page = page_of(header(), coffee(name='초코스콘', q='1', u='3200', a='3200'),
                             coffee(110, q='1', u='1800', a='1800'))
        items = [model_item('초코스콘', 1, 1800, 3200), model_item(q=1, u=3200, a=1800)]
        ground_items(items, text, [page])
        self.assertEqual(items[0]['unit_price'], 3200)
        self.assertEqual(items[1]['unit_price'], 1800)

    def test_strong_unmatched_logical_item_is_added(self):
        text, page = page_of(header(), coffee(), coffee(110, name='녹차라떼'))
        items = [model_item()]
        ground_items(items, text, [page])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['name'], '아메리카노')
        self.assertEqual(items[1], model_item('녹차라떼', 2, 4500, 9000))

    def test_punctuation_and_unique_fuzzy_name_match(self):
        for name in ('아메리카노.', '아메리카노1'):
            with self.subTest(name=name):
                text, page = page_of(header(), coffee(name=name))
                items = [model_item()]
                self.assertEqual(ground_items(items, text, [page])['changed_items'], 1)
                self.assertEqual(items[0]['name'], '아메리카노')

    def test_ambiguous_fuzzy_names_are_not_overwritten(self):
        text, page = page_of(header(), coffee(name='아메리카노1'), coffee(110, name='아메리카노2'))
        items = [model_item()]
        trace = ground_items(items, text, [page])
        self.assertEqual(items, [model_item()])
        self.assertEqual(trace['items'][0]['reason'], 'unmatched_or_ambiguous_table_name')

    def test_duplicate_names_on_different_rows_are_not_guessed(self):
        text, page = page_of(header(), coffee(), coffee(110))
        items = [model_item(), model_item()]
        self.assertEqual(ground_items(items, text, [page])['changed_items'], 0)

    def test_two_model_items_cannot_share_one_numeric_row(self):
        text, page = page_of(header(), coffee())
        items = [model_item(), model_item('아메리카노.')]
        trace = ground_items(items, text, [page])
        self.assertEqual(trace['changed_items'], 0)
        self.assertTrue(all(e['reason'] == 'shared_numeric_row' for e in trace['items']))

    def test_arithmetic_without_headers_is_unique_or_abstains(self):
        text, page = page_of(coffee())
        items = [model_item()]
        self.assertEqual(ground_items(items, text, [page])['changed_items'], 1)
        text, page = page_of(coffee(q='2', u='20', a='40'))
        items = [model_item()]
        self.assertEqual(ground_items(items, text, [page])['changed_items'], 0)

    def test_missing_unit_price_is_not_invented(self):
        text, page = page_of(header(), [cell('아메리카노', 0, 70), cell('2', 200, 70), cell('9000', 420, 70)])
        items = [model_item(q=1, u=None, a=4500)]
        ground_items(items, text, [page])
        self.assertEqual(items, [model_item(q=1, u=None, a=4500)])

    def test_single_unlabelled_price_cannot_become_unit_or_total(self):
        text, page = page_of([cell('아메리카노', 0, 70), cell('9000', 420, 70)])
        items = [model_item()]
        self.assertEqual(ground_items(items, text, [page])['changed_items'], 0)

    def test_discounted_or_negative_rows_keep_model_values(self):
        for amount in ('8000', '-9000'):
            text, page = page_of(header(), coffee(a=amount))
            items = [model_item()]
            self.assertEqual(ground_items(items, text, [page])['changed_items'], 0)
        text, page = page_of(header(), coffee(), [cell('쿠폰 할인', 500, 70)])
        self.assertEqual(ground_items([model_item()], text, [page])['changed_items'], 0)

    def test_tax_and_total_are_not_grounded_as_products(self):
        text, page = page_of(header(), coffee(name='부가세'), coffee(110, name='합계'))
        items = [model_item('부가세'), model_item('합계')]
        trace = ground_items(items, text, [page])
        self.assertEqual(items, [model_item('부가세'), model_item('합계')])
        self.assertEqual(len(trace['removed_items']), 0)
        self.assertEqual(trace['item_layout_type'], 'UNKNOWN')

    def test_partial_columns_keep_model_even_if_merged_arithmetic_would_match(self):
        for cells in ([cell('2', 200, 70), cell('9000', 420, 70)],
                      [cell('4500', 300, 70), cell('9000', 420, 70)],
                      [cell('2', 200, 70), cell('4500', 300, 70)]):
            with self.subTest(cells=cells):
                text, page = page_of(header(), [cell('아메리카노', 0, 70)] + cells)
                items = [model_item(q=2, u=4500, a=4500)]
                before = copy.deepcopy(items)
                trace = ground_items(items, text, [page])
                self.assertEqual(items, before)
                self.assertEqual(trace['changed_items'], 0)

    def test_quantity_changes_only_with_complete_row_arithmetic(self):
        for amount, expected in [('9000', 2), ('8000', 7)]:
            text, page = page_of(header(), coffee(a=amount))
            items = [model_item(q=7, u=4500, a=9000)]
            ground_items(items, text, [page])
            self.assertEqual(items[0]['quantity'], expected)

    def test_multiple_summary_candidates_preserve_real_and_summary_items(self):
        text, page = page_of(header(), coffee(), coffee(110, name='합계'),
                             coffee(150, name='부가세'))
        items = [model_item(q=2, u=4500, a=9000), model_item('합계'), model_item('부가세')]
        before = copy.deepcopy(items)
        trace = ground_items(items, text, [page])
        self.assertEqual(items, before)
        self.assertEqual(trace['removed_items'], [])

    def test_unknown_wrapped_logical_row_does_not_add_items(self):
        text, page = page_of(header(), [cell('아이스', 0, 55)], coffee(name='아메리카노'))
        items = []
        trace = ground_items(items, text, [page])
        self.assertEqual(trace['logical_row_count'], 1)
        self.assertEqual(items, [])
        self.assertEqual(trace['item_layout_type'], 'UNKNOWN')
        ground_items(items, text, [page])
        self.assertEqual(len(items), 0)

    def test_coherent_item_reserves_ocr_row_before_addition(self):
        text, page = page_of(header(), coffee(), coffee(110, name='녹차라떼'))
        items = [model_item(q=1, u=100, a=100)]
        ground_items(items, text, [page])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0], model_item(q=2, u=4500, a=9000))

    def test_table_geometry_corrects_arithmetically_consistent_row_shift(self):
        # Unit-price and quantity columns are deliberately opposite to coffee().
        text, page = page_of(
            [cell('상품명', 0, 30), cell('단가', 200, 30), cell('수량', 300, 30), cell('금액', 420, 30)],
            coffee(name='명란바게트', q='5500', u='1', a='5500'),
            coffee(110, name='호롱소세지', q='5000', u='1', a='5000'))
        page['items'].reverse()  # OCR serialization order cannot select a row.
        items = [model_item('명란바게트', 1, 5500, 5500), model_item('호롱소세지', 1, 5500, 5500)]
        trace = ground_items(items, text, [page])
        self.assertTrue(trace['table_detected'])
        self.assertEqual(trace['item_layout_type'], 'COLUMN_TABLE')
        self.assertEqual(trace['applied_postprocessor'], 'column_table_grounding')
        self.assertEqual(items[1], model_item('호롱소세지', 1, 5000, 5000))
        self.assertEqual(trace['items'][1]['reason'], 'strong_same_row_table_geometry')
        self.assertEqual(trace['items'][1]['matched_row']['text'], '호롱소세지')
        self.assertEqual(trace['items'][1]['original_item']['unit_price'], 5500)
        self.assertEqual(trace['items'][1]['corrected_item'], items[1])
        self.assertEqual(trace['added_items'], [])
        self.assertEqual(trace['removed_items'], [])
        self.assertEqual(ground_items(items, text, [page])['changed_items'], 0)

    def test_table_requires_repeated_complete_same_band_columns(self):
        layouts = [
            (header(), coffee()),
            (coffee(), coffee(110, name='카페라테')),
            (header(), coffee(), [cell('추가토핑', 30, 100)], coffee(140, name='카페라테')),
            (header(), coffee(), [cell('카페라테', 0, 110), cell('2', 240, 110),
                                  cell('4500', 340, 110), cell('9000', 460, 110)]),
        ]
        for rows in layouts:
            with self.subTest(rows=rows):
                text, page = page_of(*rows)
                items = [model_item(q=1, u=100, a=100), model_item('카페라테', 1, 100, 100)]
                before = copy.deepcopy(items)
                trace = ground_items(items, text, [page])
                self.assertFalse(trace['table_detected'])
                self.assertEqual(items[:2], before)

    def test_table_detection_does_not_require_all_rows_to_pass_arithmetic(self):
        text, page = page_of(header(), coffee(), coffee(110, name='카페라테', a='8000'))
        items = [model_item(), model_item('카페라테', 1, 100, 100)]
        trace = ground_items(items, text, [page])
        self.assertEqual(trace['item_layout_type'], 'COLUMN_TABLE')
        self.assertEqual(items[0], model_item(q=2, u=4500, a=9000))
        self.assertEqual(items[1], model_item('카페라테', 1, 100, 100))
        self.assertEqual(trace['items'][1]['reason'], 'row_arithmetic_conflict')

    def test_three_column_header_repairs_only_observed_values_and_adds_complete_row(self):
        text, page = page_of(
            [cell('메 뉴명', 0, 30), cell('수량', 240, 30), cell('금액', 420, 30)],
            [cell('레몬에이드', 0, 70), cell('2', 250, 70), cell('8000', 410, 70)],
            [cell('아이스티', 0, 110), cell('3', 250, 110), cell('9000', 410, 110)])
        for unit in (None, 100):
            with self.subTest(unit=unit):
                items = [model_item('레몬에이드', 1, unit, 100)]
                trace = ground_items(items, text, [page])
                self.assertEqual(trace['item_layout_type'], 'COLUMN_TABLE')
                self.assertEqual(trace['table_schema'][0]['schema'], '3_COLUMN')
                self.assertEqual(items[0], model_item('레몬에이드', 2, unit, 8000))
                self.assertEqual(items[1]['quantity'], 3)
                self.assertIsNone(items[1].get('unit_price'))
                self.assertEqual(len(trace['added_items']), 1)

    def test_merged_headers_skew_scale_and_indented_names_still_form_table(self):
        for name_header in ('상품명', '품명', '제품명', '상품', '메뉴'):
            for scale, slope in ((1, -.06), (3, .04)):
                with self.subTest(header=name_header, scale=scale, slope=slope):
                    def box(text, x, y, width=60):
                        return cell(text, x * scale, (y + slope * x) * scale,
                                    width=width * scale, height=20 * scale)
                    rows = [[box(name_header, 0, 30), box('단 가 수량', 200, 30, 160), box('금액', 420, 30)]]
                    expected = []
                    for j, name in enumerate(('명란바게트', '호롱소세지', '크림치즈빵')):
                        price = 3500 + j * 700
                        rows.append([box(name, j * 17, 80 + j * 45, 130), box(str(price), 210, 80 + j * 45),
                                     box('2', 290, 80 + j * 45), box(str(price * 2), 425, 80 + j * 45)])
                        expected.append(model_item(name, 2, price, price * 2))
                    rows[0][-1]['confidence'] = .76  # Clear label with weaker OCR score.
                    text, page = page_of(*rows)
                    items = [model_item(i['name'], 1, 100, 100) for i in expected]
                    trace = ground_items(items, text, [page])
                    self.assertEqual(trace['item_layout_type'], 'COLUMN_TABLE')
                    self.assertEqual(items, expected)
                    self.assertEqual(len(trace['logical_rows']), 3)
                    self.assertEqual(len(trace['corrected_items']), 3)

    def test_missing_names_and_quantity_do_not_prevent_table_detection_or_invent_items(self):
        text, page = page_of(
            [cell('품명', 0, 30), cell('단가 수량', 200, 25, 160), cell('금액', 420, 20)],
            [cell('5000', 210, 70), cell('10000', 420, 65)],
            [cell('8000', 210, 110), cell('16000', 420, 105)],
            [cell('냉삼', 30, 150), cell('6500', 210, 150), cell('32500', 420, 145)])
        items = [model_item('냉삼', 1, 100, 100)]
        before = copy.deepcopy(items)
        trace = ground_items(items, text, [page])
        self.assertEqual(trace['item_layout_type'], 'COLUMN_TABLE')
        self.assertEqual(items, before)
        self.assertEqual(trace['added_items'], [])
        self.assertTrue(all(not r['repairable'] for r in trace['logical_rows']))

    def test_table_fuzzy_name_and_order_match_preserve_llm_spelling(self):
        names = ['처음메뉴', '아메리카노', '마지막메뉴', '아메리카노']
        text, page = page_of(header(), *(coffee(70 + j * 40, name=name) for j, name in enumerate(names)))
        items = [model_item('처음메뉴'), model_item('아메리카너'), model_item('마지막메뉴')]
        trace = ground_items(items, text, [page])
        self.assertEqual(items[1], model_item('아메리카너', 2, 4500, 9000))
        self.assertEqual(trace['row_matching'][1]['row_index'], 1)

    def test_leading_name_and_trailing_price_guard_prevents_whole_row_shift(self):
        text, page = page_of(header(), [cell('명란바게트', 0, 70)],
                             coffee(110, name='호롱소세지', q='1', u='5500', a='5500'),
                             [cell('1', 200, 150), cell('5000', 300, 150), cell('5000', 420, 150)])
        items = [model_item('명란바게트', 1, 5500, 5500), model_item('호롱소세지', 1, 5000, 5000)]
        before = copy.deepcopy(items)
        trace = ground_items(items, text, [page])
        self.assertEqual(trace['item_layout_type'], 'COLUMN_TABLE')
        self.assertEqual(items, before)
        self.assertEqual(trace['added_items'], [])
        self.assertTrue(all(r['reason'] == 'unresolved_name_numeric_row_offset' for r in trace['logical_rows']))

    def test_explicit_zero_price_child_rows_do_not_enable_table_additions(self):
        rows = [header(), coffee(), coffee(110, name='카페라테')]
        for y, name in ((150, '추가 소스'), (190, '사이드 옵션')):
            rows.append([cell(name, 40, y, 130), cell('1', 200, y), cell('0', 300, y), cell('0', 420, y)])
        text, page = page_of(*rows)
        items = [model_item()]
        before = copy.deepcopy(items)
        trace = ground_items(items, text, [page])
        self.assertIn(trace['item_layout_type'], ('HIERARCHICAL', 'UNKNOWN'))
        self.assertEqual(trace['applied_postprocessor'], 'preserve_llm_items')
        self.assertEqual(items, before)

    def test_table_duplicate_names_and_shared_rows_are_preserved(self):
        for duplicate_ocr in (True, False):
            text, page = page_of(header(), coffee(), coffee(110, name='아메리카노' if duplicate_ocr else '카페라테'))
            items = [model_item(q=1, u=100, a=100), model_item(q=1, u=200, a=200)]
            before = copy.deepcopy(items)
            trace = ground_items(items, text, [page])
            self.assertTrue(trace['table_detected'])
            self.assertEqual(items[:2], before)
            self.assertEqual(trace['changed_items'], 0)

    def test_layout_routes_hierarchy_and_discount_blocks_without_mutation(self):
        hierarchy = (header(), coffee(),
                     [cell('추가 소스', 40, 100), cell('0', 420, 100)],
                     [cell('사이드 변경', 40, 130), cell('0', 420, 130)])
        discounts = tuple(row for y, name in ((70, '아메리카노'), (190, '카페라테'))
                          for row in ([cell(name, 0, y)],
                                      [cell('정가', 0, y + 25), cell('5000', 420, y + 25)],
                                      [cell('할인 10%', 0, y + 50), cell('-500', 420, y + 50)],
                                      [cell('최종가격', 0, y + 75), cell('4500', 420, y + 75)]))
        for rows, kind in ((hierarchy, 'HIERARCHICAL'), (discounts, 'DISCOUNT_BLOCK'),
                           (hierarchy[:3], 'UNKNOWN')):
            with self.subTest(kind=kind):
                text, page = page_of(*rows)
                items = [model_item(), model_item('카페라테'), model_item('할인')]
                before = copy.deepcopy(items)
                trace = ground_items(items, text, [page])
                self.assertEqual(trace['item_layout_type'], kind)
                self.assertEqual(trace['applied_postprocessor'], 'preserve_llm_items')
                self.assertFalse(trace['table_detected'])
                self.assertEqual(items, before)
                self.assertEqual(trace['added_items'], [])
                self.assertEqual(trace['removed_items'], [])

    def test_layout_headers_alone_and_mixed_pages_are_unknown(self):
        text, page = page_of(header(), coffee())
        trace = ground_items([model_item(q=1, u=100, a=100)], text, [page])
        self.assertEqual(trace['item_layout_type'], 'UNKNOWN')
        self.assertFalse(trace['table_detected'])
        other_text, other_page = page_of(header(), coffee(), coffee(110, name='카페라테'))
        items = [model_item()]
        before = copy.deepcopy(items)
        trace = ground_items(items, text + '\n' + other_text, [page, other_page])
        self.assertEqual(trace['item_layout_type'], 'UNKNOWN')
        self.assertEqual(trace['layout_reason'], 'mixed_page_layouts')
        self.assertEqual(items, before)

    def test_weak_unmatched_rows_are_not_added(self):
        for rows in ((coffee(),), (header(), coffee(a='8000')),
                     (header(), [cell('아메리카노', 0, 70), cell('9000', 420, 70)])):
            text, page = page_of(*rows)
            items = []
            ground_items(items, text, [page])
            self.assertEqual(items, [])
        text, page = page_of(header(), coffee())
        page['items'][4]['confidence'] = .8
        items = []
        ground_items(items, text, [page])
        self.assertEqual(items, [])

    def test_unmatched_real_items_and_unconfirmed_summary_are_preserved(self):
        text, page = page_of(header(), coffee())
        items = [model_item(), model_item('딸기케이크'), model_item('합계'), model_item('할인세트')]
        before = copy.deepcopy(items[1:])
        ground_items(items, text, [page])
        self.assertEqual(items[1:], before)

    def test_summary_is_removed_even_when_arithmetic_is_consistent(self):
        text, page = page_of(header(), coffee(), coffee(110, name='카페라테'),
                             [cell('합계', 0, 150), cell('18000', 420, 150)])
        items = [model_item(), model_item('카페라테'), model_item('합계', 1, 18000, 18000)]
        trace = ground_items(items, text, [page])
        self.assertEqual(len(items), 2)
        self.assertEqual(trace['before_count'], 3)
        self.assertEqual(trace['after_count'], 2)

    def test_paid_bag_and_product_codes(self):
        text, page = page_of(header(), [cell('000022', -90, 70)] + coffee(name='쇼핑백', q='1', u='100', a='100'))
        items = [model_item('쇼핑백')]
        ground_items(items, text, [page])
        self.assertEqual(items, [model_item('쇼핑백', 1, 100, 100)])

    def test_wrapped_name_above_skewed_price_row(self):
        text, page = page_of(header(), [cell('초코스콘', 0, 55)],
                             [cell('2개', 200, 77), cell('3200', 300, 73), cell('6400', 420, 70)])
        items = [model_item('초코스콘')]
        ground_items(items, text, [page])
        self.assertEqual(items, [model_item('초코스콘', 2, 3200, 6400)])

    def test_name_split_across_boxes(self):
        text, page = page_of(header(), [cell('아이스', 0, 70), cell('아메리카노', 65, 70, width=130),
                             cell('2', 200, 70), cell('4500', 300, 70), cell('9000', 420, 70)])
        items = [model_item('아이스 아메리카노')]
        self.assertEqual(ground_items(items, text, [page])['changed_items'], 1)

    def test_low_confidence_and_stale_layout_do_not_change_values(self):
        text, page = page_of(header(), coffee())
        page['items'][-1]['confidence'] = .4
        self.assertEqual(ground_items([model_item()], text, [page])['changed_items'], 0)
        text, page = page_of(header(), coffee())
        self.assertEqual(ground_items([model_item()], '수정된 OCR 원문', [page])['changed_items'], 0)

    def test_cross_page_duplicate_is_ambiguous(self):
        text, page = page_of(header(), coffee())
        trace = ground_items([model_item()], text + '\n' + text, [page, page])
        self.assertEqual(trace['items'][0]['reason'], 'ambiguous_name')

    def test_malformed_and_missing_inputs(self):
        for pages in (None, {}, [None], [dict(text='a', items=[])], [dict(text='a', items=[dict(text='a', bbox=[[0, 0], [float('nan'), 2]])])]):
            self.assertEqual(ground_items([model_item()], 'a', pages)['changed_items'], 0)
        for items in (None, {}, [], ['bad']):
            ground_items(items, 'text', [])

    def test_missing_and_summary_reconcile_at_equal_count(self):
        text, page = page_of(header(), coffee(), coffee(110, name='\ub179\ucc28\ub77c\ub5bc'),
                             [cell('\uc18c\uacc4', 0, 150), cell('18000', 420, 150)])
        items = [model_item(), model_item('\uc18c\uacc4', 1, 18000, 18000)]
        trace = ground_items(items, text, [page])
        self.assertEqual([i['name'] for i in items], ['\uc544\uba54\ub9ac\uce74\ub178', '\ub179\ucc28\ub77c\ub5bc'])
        self.assertEqual([e['action'] for e in trace['items']], ['corrected', 'removed', 'added'])

    def test_partial_columns_cannot_introduce_arithmetic_error(self):
        text, page = page_of(header(), [cell('\uc544\uba54\ub9ac\uce74\ub178', 0, 70), cell('2', 200, 70), cell('9000', 420, 70)])
        items = [model_item(q=1, u=3000, a=4500)]
        before = copy.deepcopy(items)
        ground_items(items, text, [page])
        self.assertEqual(items, before)

    def test_ocr_volume_suffix_confusion_still_grounds_numbers(self):
        text, page = page_of(header(), coffee(name='Beer500m1', q='9', u='3300', a='29700'))
        items = [model_item('Beer 500ml', 9, 29700, 29700)]
        ground_items(items, text, [page])
        self.assertEqual(items, [model_item('Beer 500ml', 9, 3300, 29700)])

    def test_idempotent(self):
        text, page = page_of(header(), coffee())
        items = [model_item()]
        ground_items(items, text, [page])
        self.assertEqual(ground_items(items, text, [page])['changed_items'], 0)

    def test_coherent_model_recovery_is_preserved_despite_different_bbox_prices(self):
        text, page = page_of(header(), coffee(name='종이백', q='1', u='3200', a='3200'))
        items = [model_item('종이백', 1, 100, 100)]
        trace = ground_items(items, text, [page])
        self.assertEqual(items, [model_item('종이백', 1, 100, 100)])
        self.assertEqual(trace['items'][0]['reason'], 'model_arithmetic_consistent')


if __name__ == '__main__':
    unittest.main()
