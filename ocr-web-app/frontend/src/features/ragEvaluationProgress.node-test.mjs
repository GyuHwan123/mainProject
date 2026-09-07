import test from 'node:test';
import assert from 'node:assert/strict';
import { evaluationCompletion, evaluationStatusLabel } from './ragEvaluationProgress.mjs';

test('legacy completed checkpoint with errors is shown as partial', () => {
  assert.equal(evaluationStatusLabel({ status: 'completed', total: 200, completed_count: 177, error_count: 23 }), '부분 완료 · 177/200 · 오류 23');
});

test('partial response and older cases-only response both retain failed count', () => {
  for (const response of [{ completed_count: 177, error_count: 23 }, { cases: Array(177).fill({}) }]) {
    assert.deepEqual(evaluationCompletion(response, 200), { status: 'partial', completed_count: 177, error_count: 23 });
  }
});

test('successful retry displays 200/200 complete', () => {
  const progress = evaluationCompletion({ completed_count: 200, error_count: 0 }, 200);
  assert.equal(evaluationStatusLabel({ ...progress, total: 200 }), '완료 · 200/200');
});
