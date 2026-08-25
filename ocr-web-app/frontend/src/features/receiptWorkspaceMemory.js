let workspace = {
  financeRecord: null,
  financeRecords: [],
  pendingEvaluations: [],
};

const notify = () => window.dispatchEvent(new Event('receipt-workspace-updated'));

export function readReceiptWorkspace() {
  return workspace;
}

export function saveReceiptRecords(financeRecord, financeRecords) {
  workspace = { ...workspace, financeRecord, financeRecords };
}

export function rememberReceiptRecord(record) {
  const financeRecords = workspace.financeRecords.some((item) => item.id === record.id)
    ? workspace.financeRecords.map((item) => item.id === record.id ? record : item)
    : [...workspace.financeRecords, record];
  workspace = { ...workspace, financeRecord: record, financeRecords };
  notify();
}

export function rememberPendingReceipt(capture) {
  workspace = {
    ...workspace,
    pendingEvaluations: [
      ...workspace.pendingEvaluations.filter((item) => item.document_id !== capture.document_id),
      capture,
    ],
  };
  notify();
}

export function markReceiptEvaluated(documentId) {
  workspace = {
    ...workspace,
    pendingEvaluations: workspace.pendingEvaluations.filter((item) => item.document_id !== documentId),
  };
  notify();
}

export function clearPendingReceipts() {
  workspace = { ...workspace, pendingEvaluations: [] };
  notify();
}
