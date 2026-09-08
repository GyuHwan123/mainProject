const formatValue = (value) => typeof value === 'number' ? value.toLocaleString('ko-KR', { maximumFractionDigits: 3 }) : String(value ?? '');

export default function FinanceDocumentPreview({ sheet }) {
  const rows = sheet?.rows || [];
  const headerIndex = rows.findIndex((row) => row[0] === '영수증 ID');
  const isDocument = headerIndex === 10;
  const headers = headerIndex >= 0 ? rows[headerIndex] : [];
  const detailRows = rows.slice(headerIndex + 1).filter((row) => row.some((value) => value !== null && value !== '') && !String(row[0] ?? '').startsWith('품목금액 합계'));
  const amounts = detailRows.map((row) => row[headers.length - 1]).filter((value) => typeof value === 'number' && Number.isFinite(value));
  const renderCell = (value, column) => {
    const full = formatValue(value);
    const receiptId = headers[column] === '영수증 ID';
    return <td key={column} className={receiptId ? 'receipt-reference' : typeof value === 'number' ? 'numeric' : undefined} title={receiptId ? full : undefined}>{receiptId && full.length > 12 ? `${full.slice(0, 8)}…` : full}</td>;
  };

  return <div className="finance-document-scroll"><article className="finance-document-paper">
    {isDocument ? <>
      <div className="finance-document-heading"><h2>{rows[1]?.[0] || sheet.name}</h2><table className="finance-approval"><thead><tr>{['기안자', '검토자', '승인자'].map((label) => <th key={label}>{label}</th>)}</tr></thead><tbody><tr>{[5, 6, 7].map((column) => <td key={column}>{formatValue(rows[2]?.[column])}</td>)}</tr></tbody></table></div>
      <dl className="finance-document-metadata">{rows.slice(4, 8).flatMap((row, index) => [0, 4].map((column) => <div key={`${index}-${column}`}><dt>{formatValue(row[column])}</dt><dd>{formatValue(row[column + 1]) || '—'}</dd></div>))}</dl>
      <h3>지출 내역</h3>
    </> : <h2>{sheet?.name}</h2>}
    <table className="finance-document-details">
      {headers.length > 0 && <thead><tr>{headers.map((label, index) => <th key={index}>{label}</th>)}</tr></thead>}
      <tbody>{(headerIndex >= 0 ? detailRows : rows).map((row, index) => <tr key={index}>{row.map(renderCell)}</tr>)}</tbody>
      {isDocument && <tfoot><tr><th colSpan={headers.length - 1}>품목금액 합계 <small>(미확인 금액 제외)</small></th><td className="numeric">{amounts.length ? `${formatValue(amounts.reduce((sum, amount) => sum + amount, 0))} 원` : '—'}</td></tr></tfoot>}
    </table>
  </article></div>;
}
