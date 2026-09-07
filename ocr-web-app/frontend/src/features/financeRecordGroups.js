export function groupFinanceRecords(records) {
  const groups = new Map();
  for (const record of records) {
    const data = record.structured_data || {};
    if (record.status !== 'CONFIRMED' || !data.excel_saved_at || record.deleted_at) continue;
    const date = new Date(data.excel_saved_at);
    const month = Number.isNaN(date.getTime()) ? '날짜 미확인' : `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
    const sentAt = data.finance_workflow?.submitted_at;
    const key = `${month}:${record.document_type}:${sentAt || 'pending'}`;
    if (!groups.has(key)) {
      const sentDate = sentAt ? new Date(sentAt) : null;
      const batchLabel = sentDate && !Number.isNaN(sentDate.getTime())
        ? `${sentDate.toLocaleString('sv-SE', { timeZone: 'Asia/Seoul' })} 발송`
        : sentAt ? `${sentAt} 발송` : '새 정산분';
      groups.set(key, { key, month, documentType: record.document_type, records: [], sent: Boolean(sentAt),
        batchLabel, status: sentAt ? '재무팀 발송 완료' : '재무팀 미발송' });
    }
    groups.get(key).records.push(record);
  }
  return [...groups.values()].map((group) => ({ ...group,
    records: group.records.sort((a, b) => new Date(a.created_at) - new Date(b.created_at)),
    total: group.records.reduce((sum, record) => sum + Number(record.total_amount || 0), 0),
  })).sort((a, b) => b.key.localeCompare(a.key));
}
