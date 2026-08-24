let pendingEvaluationInput = null;

export function queueFinanceEvaluationInput(datasetFile, imageFiles) {
  pendingEvaluationInput = {
    datasetFile,
    imageFiles: Array.from(imageFiles || []),
  };
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
