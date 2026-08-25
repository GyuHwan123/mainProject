const MAX_RUNS = 200;
let evaluationRuns = [];

try {
  localStorage.removeItem('pic_to_text_finance_evaluations_v1');
  localStorage.removeItem('pic_to_text_finance_evaluations_v2');
} catch {
  // Evaluation history is intentionally memory-only.
}

export function readFinanceEvaluationRuns() {
  return evaluationRuns;
}

export function saveFinanceEvaluationRuns(runs) {
  evaluationRuns = (Array.isArray(runs) ? runs : []).slice(-MAX_RUNS);
  window.dispatchEvent(new CustomEvent('finance-evaluations-updated'));
  return evaluationRuns;
}

export function appendFinanceEvaluationRun(run) {
  const current = readFinanceEvaluationRuns();
  const captureId = run.capture_id;
  const next = captureId
    ? [...current.filter((item) => item.capture_id !== captureId), run]
    : [...current, run];
  return saveFinanceEvaluationRuns(next);
}

export function clearFinanceEvaluationRuns() {
  return saveFinanceEvaluationRuns([]);
}
