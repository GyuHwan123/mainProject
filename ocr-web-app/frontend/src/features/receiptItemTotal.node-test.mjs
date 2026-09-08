import { test } from 'node:test';
import assert from 'node:assert/strict';
import { receiptItemTotalCheck } from './receiptItemTotal.js';

test('recalculates after editing item amounts or payment total', () => {
  const items = [{ total_amount: '10000' }, { total_amount: '10600' }];
  assert.equal(receiptItemTotalCheck(items, '17600').difference, 3000);
  assert.equal(receiptItemTotalCheck(items, '17600').matches, false);
  assert.equal(receiptItemTotalCheck(items, '20600').matches, true);
  assert.equal(receiptItemTotalCheck([{ total_amount: '10000' }, { total_amount: '7600' }], '17600').matches, true);
});

test('distinguishes missing amounts from zero and handles decimals', () => {
  for (const items of [[], [{ total_amount: '' }], [{ total_amount: null }], [{ total_amount: 'invalid' }]]) {
    assert.equal(receiptItemTotalCheck(items, 0).complete, false);
    assert.equal(receiptItemTotalCheck(items, 0).matches, false);
  }
  assert.equal(receiptItemTotalCheck([{ total_amount: 0 }], '').complete, false);
  assert.equal(receiptItemTotalCheck([{ total_amount: 0 }], 0).matches, true);
  assert.equal(receiptItemTotalCheck([{ total_amount: 0.1 }, { total_amount: 0.2 }], 0.3).matches, true);
});
