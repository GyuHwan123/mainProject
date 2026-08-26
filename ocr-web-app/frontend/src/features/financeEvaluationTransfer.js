let pendingEvaluationInput = null;
export const FINANCE_EVALUATION_INPUT_QUEUED = 'finance-evaluation-input-queued';

export function queueFinanceEvaluationInput(datasetFile, imageFiles) {
  pendingEvaluationInput = {
    datasetFile,
    imageFiles: Array.from(imageFiles || []),
  };
  // The evaluation component can already be mounted inside the report page.
  // In that case its mount-only queue check will not run again after navigation.
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(FINANCE_EVALUATION_INPUT_QUEUED));
  }
}

export function peekFinanceEvaluationInput() {
  return pendingEvaluationInput;
}

export function clearFinanceEvaluationInput(input) {
  if (pendingEvaluationInput === input) pendingEvaluationInput = null;
}

export function takeFinanceEvaluationInput() {
  const input = pendingEvaluationInput;
  pendingEvaluationInput = null;
  return input;
}
