export const FINANCE_EVALUATION_STORAGE_KEY = 'pic_to_text_finance_evaluations_v2';
const LEGACY_STORAGE_KEY = 'pic_to_text_finance_evaluations_v1';
const MAX_RUNS = 200;

export function readFinanceEvaluationRuns() {
  try {
    localStorage.removeItem(LEGACY_STORAGE_KEY);
    const value = JSON.parse(localStorage.getItem(FINANCE_EVALUATION_STORAGE_KEY) || '[]');
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

export function saveFinanceEvaluationRuns(runs) {
  const value = (Array.isArray(runs) ? runs : []).slice(-MAX_RUNS);
  localStorage.setItem(FINANCE_EVALUATION_STORAGE_KEY, JSON.stringify(value));
  window.dispatchEvent(new CustomEvent('finance-evaluations-updated'));
  return value;
}

export function appendFinanceEvaluationRun(run) {
  const current = readFinanceEvaluationRuns();
  const captureId = run.capture_id;
  const next = captureId
    ? [...current.filter((item) => item.capture_id !== captureId), run]
    : [...current, run];
  return saveFinanceEvaluationRuns(next);
}
