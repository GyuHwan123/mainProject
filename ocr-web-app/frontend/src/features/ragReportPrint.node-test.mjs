import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareRagReportPrint } from './ragReportPrint.mjs';

test('print expands RAG detail sections and restores mixed open states', () => {
  const sections = [{ open: false }, { open: true }];
  const restore = prepareRagReportPrint({ querySelectorAll(selector) {
    assert.equal(selector, '.rag-monitoring details');
    return sections;
  } });
  assert.deepEqual(sections.map(section => section.open), [true, true]);
  restore();
  assert.deepEqual(sections.map(section => section.open), [false, true]);
});

test('receipt and business reports without RAG details are untouched', () => {
  prepareRagReportPrint({ querySelectorAll: () => [] })();
});
