import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import React, { useMemo } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithOxc } from 'vite';

const chartSource = fs.readFileSync(new URL('../components/RagMonitoringChart.jsx', import.meta.url), 'utf8')
  .replace("import React from 'react';", '').replace('export const ', 'const ').replace('export default function ', 'function ');
const chartCode = (await transformWithOxc(chartSource, 'Chart.jsx', { jsx: { runtime: 'classic' } })).code;
const [Chart, trendMetrics] = new Function('React', chartCode + '\nreturn [RagMonitoringChart, RAG_TREND_METRICS];')(React);
const page = fs.readFileSync(new URL('../pages/ReportPage.jsx', import.meta.url), 'utf8');
const reportCode = (await transformWithOxc(page.slice(page.indexOf('const RAG_KPI_COLORS ='), page.indexOf('\nfunction RagLlmEvaluation(')), 'Report.jsx', { jsx: { runtime: 'classic' } })).code;
const factory = new Function('React', 'useMemo', 'useState', 'useEffect', 'apiClient', 'RagMonitoringChart', 'createInitialMonitoringDateRange', 'dateInputValue', 'percent', 'IoDownloadOutline', 'RAG_TREND_METRICS', reportCode + '\nreturn RagPerformanceReport;');

function harness({ detail = null, data = null, api = async () => ({ data }), range = { startDate: '2026-09-01', endDate: '2026-09-07' }, refresh = 0 } = {}) {
  const states = [range, '7', data, false, '', detail];
  const changes = [];
  const effects = [];
  const requests = [];
  let cursor = 0;
  const Report = factory(React, useMemo, () => {
    const index = cursor++;
    return [states[index], value => changes.push([index, value])];
  }, (effect, deps) => effects.push({ effect, deps }), { get: (...args) => { requests.push(args); return api(...args); } }, Chart, () => range, d => d.toISOString().slice(0, 10), v => `${((v || 0) * 100).toFixed(1)}%`, () => null, trendMetrics);
  const html = renderToStaticMarkup(React.createElement(Report, { modelConfig: {}, refreshVersion: refresh }));
  return { html, changes, effects, requests };
}

test('selected dates and refresh are wired to the read-only monitoring API', async () => {
  const h = harness({ refresh: 3 });
  h.effects[0].effect();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(h.requests[0][0], '/rag/evaluation/monitoring');
  assert.deepEqual(h.requests[0][1].params, { start_date: '2026-09-01', end_date: '2026-09-07' });
  assert.deepEqual(h.effects[0].deps, ['2026-09-01', '2026-09-07', 3, false]);
});

test('changed dates request their own range and stale responses are ignored', async () => {
  let resolve;
  const h = harness({ range: { startDate: '2026-08-01', endDate: '2026-08-31' }, api: () => new Promise(r => { resolve = r; }) });
  const cleanup = h.effects[0].effect();
  const before = h.changes.length;
  cleanup();
  resolve({ data: { summary: {} } });
  await new Promise(r => setTimeout(r, 0));
  assert.equal(h.changes.length, before);
  assert.equal(h.requests[0][1].params.start_date, '2026-08-01');
});

test('API errors are surfaced instead of substituting local evaluation results', async () => {
  const h = harness({ api: async () => { throw { response: { data: { detail: 'DB unavailable' } } }; } });
  h.effects[0].effect();
  await new Promise(r => setTimeout(r, 0));
  assert.ok(h.changes.some(([index, value]) => index === 4 && value === 'DB unavailable'));
});

test('selected execution loads persisted details and ignores an aborted response', async () => {
  let resolve;
  const run = { id: 'run-id', evaluated_at: '2026-09-07T00:00:00Z', summary_metrics: {} };
  const h = harness({ data: { summary: {}, recent_runs: [run], daily: [] }, api: () => new Promise(r => { resolve = r; }) });
  const cleanup = h.effects[1].effect();
  assert.equal(h.requests[0][0], '/rag/evaluation/history/run-id');
  const before = h.changes.length;
  cleanup();
  assert.equal(h.requests[0][1].signal.aborted, true);
  resolve({ data: { ...run, summary_metrics: { evaluation_result: { cases: [] } } } });
  await new Promise(r => setTimeout(r, 0));
  assert.equal(h.changes.length, before);
});

test('DB summary and recent run render with zero scores preserved', () => {
  const h = harness({ data: { summary: { total: 200, run_count: 1, answer_accuracy: 0 }, daily: [], recent_runs: [{ id: 'run', dataset_name: 'db-dataset', model_name: 'db-model', question_count: 200, answer_accuracy: 0, evaluated_at: '2026-09-07T00:00:00Z' }] } });
  assert.ok(h.html.includes('db-dataset'));
  assert.ok(h.html.includes('db-model'));
  assert.ok(h.html.includes('0.0%'));
  assert.ok(h.html.includes('200'));
});

test('composition and category cards restore twenty cases without backend memory', () => {
  const run = { id: 'latest-20', question_count: 20, evaluated_at: '2026-09-09T00:00:00Z', summary_metrics: {} };
  const cases = Array.from({ length: 20 }, (_, index) => ({
    question_id: String(index), question_type: 'single_document_fact',
    answer_correct: index < 15, hit: true, reciprocal_rank: 1,
  }));
  const data = { summary: { total: 20, run_count: 1 }, recent_runs: [run], daily: [] };
  const detail = { ...run, summary_metrics: { evaluation_result: { cases } } };
  const { html } = harness({ data, detail });
  assert.ok(html.includes('20문항'));
  assert.ok(html.includes('75.0%'));
  assert.ok(!html.includes('NO DATA'));
  const stale = harness({ data, detail: { ...detail, id: 'older-run' } });
  assert.ok(stale.html.includes('NO DATA'));
});

test('graph does not connect across days with no evaluations', () => {
  const daily = [{ date: '2026-09-01', run_count: 1, answer_accuracy: 0 }, { date: '2026-09-02', run_count: 0, answer_accuracy: null }, { date: '2026-09-03', run_count: 1, answer_accuracy: 1 }];
  const html = renderToStaticMarkup(React.createElement(Chart, { daily, metric: 'answer_accuracy' }));
  assert.equal((html.match(/<polyline/g) || []).length, 2);
  assert.equal((html.match(/<circle/g) || []).length, 2);
});

test('empty history renders no synthetic graph', () => {
  const html = renderToStaticMarkup(React.createElement(Chart, { daily: [] }));
  assert.ok(!html.includes('<svg'));
});
