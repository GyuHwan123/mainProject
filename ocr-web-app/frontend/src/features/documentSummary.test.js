import { describe, expect, it } from 'vitest';
import { documentSummary } from './documentSummary.mjs';

describe('documentSummary', () => {
  it('reads the structured response stored as text', () => {
    expect(documentSummary(JSON.stringify({ summary_title: '제목', one_line_summary: '핵심 요약', key_points: ['가', '나', '다'] })))
      .toEqual({ title: '제목', lead: '핵심 요약', points: ['가', '나', '다'] });
  });
  it('preserves every legacy sentence in at most five bullets', () => {
    const text = '전체 요약. 하나. 둘. 셋. 넷. 다섯. 여섯.';
    const result = documentSummary(text, '기존 문서');
    expect(result.title).toBe('');
    expect([result.lead, ...result.points].join(' ')).toBe(text);
    expect(result.points.length).toBeLessThanOrEqual(5);
  });
  it('handles missing and incorrectly typed fields without crashing', () => {
    expect(documentSummary(null).points).toEqual([]);
    expect(documentSummary({ summary_title: 42, key_points: [null, {}, '내용'] }, '문서'))
      .toEqual({ title: '', lead: '', points: ['내용'] });
  });
});
