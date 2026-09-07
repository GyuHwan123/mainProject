import { describe, it, expect } from 'vitest';
import { groupFinanceRecords } from './financeRecordGroups';

const record = (id, submitted_at) => ({ id, document_type: 'WELFARE_BENEFIT', status: 'CONFIRMED', total_amount: 100,
  structured_data: { excel_saved_at: '2026-09-07T10:00:00+09:00', finance_workflow: { submitted_at } } });

describe('finance record batches', () => {
  it('keeps previous sends separate from new accumulated records', () => {
    const groups = groupFinanceRecords([record('a', '2026-09-07T01:00:00Z'), record('b', '2026-09-07T02:00:00Z'), record('c'), record('d')]);
    expect(groups).toHaveLength(3);
    expect(groups.find((group) => !group.sent).records).toHaveLength(2);
    expect(groups.filter((group) => group.sent)).toHaveLength(2);
    expect(new Set(groups.map((group) => group.batchLabel)).size).toBe(3);
  });
  it('has no pending group after all records are sent', () => {
    expect(groupFinanceRecords([record('a', '2026-09-07T01:00:00Z')]).some((group) => !group.sent)).toBe(false);
    expect(groupFinanceRecords([record('a', '2026-09-07T01:00:00Z'), record('b')]).some((group) => !group.sent)).toBe(true);
  });
});
