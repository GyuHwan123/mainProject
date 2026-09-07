import { describe, expect, it } from 'vitest';
import { canApplyFinanceTaxSplit, applyFinanceTaxSplit } from './financeTaxSplit';

describe('finance tax split option', () => {
  it('assigns the full total to supply for zero tax', () => {
    const draft = { supply_amount: null, tax_amount: '', total_amount: '10000' };
    expect(applyFinanceTaxSplit(draft, 0)).toEqual({ ...draft, supply_amount: '10000', tax_amount: '0' });
  });
  it('fills missing amounts from the total without changing other fields', () => {
    const draft = { supply_amount: null, tax_amount: '', total_amount: '10000', merchant: '상호' };
    expect(canApplyFinanceTaxSplit(draft)).toBe(true);
    expect(applyFinanceTaxSplit(draft)).toEqual({ ...draft, supply_amount: '9000', tax_amount: '1000' });
  });
  it('rounds tax to won and preserves the total', () => {
    const result = applyFinanceTaxSplit({ total_amount: '10005' });
    expect(result.tax_amount).toBe('1001');
    expect(Number(result.supply_amount) + Number(result.tax_amount)).toBe(10005);
  });
  it.each([
    { supply_amount: 0, tax_amount: null, total_amount: 10000 },
    { supply_amount: null, tax_amount: '0', total_amount: 10000 },
    ...[null, '', ' ', 0, -1, 'invalid', Infinity].map((total_amount) => ({ total_amount })),
  ])('does not overwrite existing amounts or use invalid totals: %j', (draft) => {
    expect(canApplyFinanceTaxSplit(draft)).toBe(false);
    expect(applyFinanceTaxSplit(draft)).toBe(draft);
  });
});
