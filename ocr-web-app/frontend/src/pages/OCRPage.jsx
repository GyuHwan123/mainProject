import { useEffect, useMemo, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { IoCloseOutline, IoDocumentTextOutline, IoMenuOutline, IoSearchOutline } from 'react-icons/io5';
import { useNavigate } from 'react-router-dom';
import apiClient from '../api/client';
import Sidebar from '../components/Sidebar';
import { getAppUser, saveAppUser } from '../features/appSession';
import { appendFinanceEvaluationRun, clearFinanceEvaluationRuns } from '../features/financeEvaluationStorage';
import { queueFinanceEvaluationInput } from '../features/financeEvaluationTransfer';
import {
  markReceiptEvaluated,
  clearPendingReceipts,
  readReceiptWorkspace,
  rememberPendingReceipt,
  saveReceiptRecords,
} from '../features/receiptWorkspaceMemory';
import { datasetRows, imageNameOf, normalizedFileName, truthOf } from './FinanceEvaluationPage';
import '../style/OCRPage.scss';

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorker;

const EMPTY_FILE_NAME = '영수증을 선택해주세요';
const EMPTY_ITEMS = [];
const RECEIPT_STRONG_PATTERN = /(영수증|매출\s*전표|승인\s*번호|카드\s*번호|사업자\s*(?:NO|번호|등록번호)|결제\s*금액|승인\s*금액|받을\s*금액|현금\s*영수증|공급\s*가액|부가\s*세(?:액)?|총\s*구매\s*금액)/i;
const RECEIPT_WEAK_PATTERNS = [
  /(20\d{2})\s*(?:년|[-./])\s*\d{1,2}\s*(?:월|[-./])\s*\d{1,2}\s*일?/,
  /(합계|총액|소계|결제|승인|거래일시|거래일자)/i,
  /(단가\s*수량\s*금액|품목|상품명|가맹점|상호명)/i,
  /(?<!\d)\d{1,3}(?:[,]\d{3})+(?!\d)|\d[\d,]{2,}\s*원/,
];

function hasReceiptContent(text) {
  const normalized = String(text || '').replace(/\s+/g, ' ').trim();
  if (!normalized) return false;
  if (RECEIPT_STRONG_PATTERN.test(normalized)) return true;
  return RECEIPT_WEAK_PATTERNS.filter((pattern) => pattern.test(normalized)).length >= 2;
}

const FINANCE_DOCUMENTS = {
  EXPENSE_REPORT: { title: '경비지출결의서', headers: ['영수증 ID', '품목 순번', '결제일시', '상호명(가맹점)', '지출용도/품목명', '공급가액', '부가세', '합계금액', '증빙유형'] },
  TRAVEL_EXPENSE: { title: '출장여비교통비정산서', headers: ['영수증 ID', '품목 순번', '구분', '일자', '출발/도착지', '교통/숙박 수단', '상호(가맹점)', '금액', '증빙여부', '비고'] },
  PURCHASE_REQUEST: { title: '구매품의요청서', headers: ['영수증 ID', '품목 순번', '품목명', '규격/옵션', '수량', '단위', '단가', '공급가액', '부가세', '합계금액', '비고'] },
  WELFARE_BENEFIT: { title: '복리후생비신청서', headers: ['영수증 ID', '품목 순번', '지원 항목(구분)', '결제일자', '내용(품목명/사유)', '결제처', '신청 금액', '증빙서류', '비고'] },
};

const financeMoney = (value) => `${Math.round(Number(value || 0)).toLocaleString('ko-KR')}원`;

function financeRecordCells(record, itemNumber, rowItem = null) {
  const data = record.structured_data || {};
  const item = rowItem || {};
  const itemCount = Array.isArray(data.items) ? data.items.length : 0;
  const isSingle = itemCount <= 1;
  const receiptId = record.document_id || record.id || '미확인';
  if (record.document_type === 'TRAVEL_EXPENSE') {
    return [receiptId, itemNumber, record.expense_category || '확인 필요', record.transaction_date, data.route || data.location, data.transport_method || data.service_type, record.merchant, Number(item.total_amount || (isSingle ? record.total_amount : 0)), data.evidence_status || '첨부', item.name || item.note || data.note || record.description];
  }
  if (record.document_type === 'WELFARE_BENEFIT') {
    return [receiptId, itemNumber, record.expense_category, record.transaction_date, item.name || record.description, record.merchant, Number(item.total_amount || (isSingle ? record.total_amount : 0)), data.evidence_type || record.payment_method || '영수증', item.note || data.note];
  }
  if (record.document_type === 'PURCHASE_REQUEST') {
    const quantity = Number(item.quantity || 1);
    const unitPrice = Number(item.unit_price || 0);
    const supply = Number(item.supply_amount || quantity * unitPrice || (isSingle ? record.supply_amount : 0));
    const tax = Number(item.tax_amount || (isSingle ? record.tax_amount : 0));
    const total = Number(item.total_amount || supply + tax || (isSingle ? record.total_amount : 0));
    return [receiptId, itemNumber, item.name || record.description, item.specification || item.option, quantity, item.unit || '개', unitPrice, supply, tax, total, item.note];
  }
  const quantity = Number(item.quantity || 1);
  const unitPrice = Number(item.unit_price || 0);
  const supply = Number(item.supply_amount || quantity * unitPrice || (isSingle ? record.supply_amount : 0));
  const tax = Number(item.tax_amount || (isSingle ? record.tax_amount : 0));
  const total = Number(item.total_amount || supply + tax || (isSingle ? record.total_amount : 0));
  return [receiptId, itemNumber, record.transaction_date, record.merchant, item.name || record.description || record.expense_category, supply, tax, total, record.payment_method || '영수증'];
}

function financeRecordRows(records) {
  return records.flatMap((record) => {
    const items = Array.isArray(record.structured_data?.items)
      ? record.structured_data.items
      : [];
    if (items.length) return items.map((item, index) => financeRecordCells(record, index + 1, item));
    return [financeRecordCells(record, 1)];
  });
}

function FinanceReceiptWorksheet({ records, user }) {
  const record = records[records.length - 1];
  const definition = FINANCE_DOCUMENTS[record.document_type] || FINANCE_DOCUMENTS.EXPENSE_REPORT;
  const total = records.reduce((sum, item) => sum + Number(item.total_amount || 0), 0);
  return <div className="receipt-form-sheet">
    <div className="receipt-form-title"><strong>{definition.title}</strong><span>현재 기록 · {record.status === 'CONFIRMED' ? '확정' : '확인 필요'}</span></div>
    <div className="receipt-form-meta"><span><b>작성자</b>{user?.name || '로그인 사용자'}</span><span><b>이메일</b>{user?.email || '미확인'}</span><span><b>결제일</b>{record.transaction_date || '미확인'}</span><span><b>합계</b>{financeMoney(total)}</span></div>
    <div className="receipt-form-table"><table><thead><tr>{definition.headers.map((header) => <th key={header}>{header}</th>)}</tr></thead><tbody>{financeRecordRows(records).map((row, rowIndex) => <tr key={`${record.id}-${rowIndex}`}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell ?? '-'}</td>)}</tr>)}</tbody></table></div>
    <p>현재 영수증에서 선별한 값만 표시됩니다.</p>
  </div>;
}

function buildReadingOrder(content, viewport) {
  const words = content.items.filter((item) => item.str?.trim()).map((item) => {
    const transform = pdfjsLib.Util.transform(viewport.transform, item.transform);
    const x = transform[4];
    const baselineY = transform[5];
    const height = Math.max(Math.hypot(transform[2], transform[3]), Math.abs(item.height || 0), 7);
    const width = Math.max(Math.abs(item.width || 0), 2);
    return {
      text: item.str.trim(),
      baselineY,
      height,
      bbox: [
        [Math.max(0, x), Math.max(0, baselineY - height * 1.05)],
        [Math.min(viewport.width, x + width), Math.min(viewport.height, baselineY + height * 0.2)],
      ],
    };
  }).filter((item) => item.bbox.flat().every(Number.isFinite)).sort((a, b) => a.baselineY - b.baselineY);

  const midpoint = viewport.width / 2;
  const centerBand = viewport.width * 0.006;
  const clearLeftItems = words.filter((word) => word.bbox[1][0] < midpoint - centerBand);
  const clearRightItems = words.filter((word) => word.bbox[0][0] > midpoint + centerBand);
  const hasTwoColumns = clearLeftItems.length >= 5 && clearRightItems.length >= 5;

  if (hasTwoColumns) {
    words.forEach((word) => {
      const [x0] = word.bbox[0];
      const [x1] = word.bbox[1];
      if (x0 < midpoint - centerBand && x1 > midpoint + centerBand) word.column = 'spanning';
      else word.column = (x0 + x1) / 2 < midpoint ? 'left' : 'right';
    });
  }

  const lines = [];
  words.forEach((word) => {
    let line = lines.find((candidate) => (
      (!hasTwoColumns || candidate.column === word.column)
      && Math.abs(candidate.baselineY - word.baselineY) <= Math.max(7, Math.max(candidate.height, word.height) * 0.75)
    ));
    if (!line) {
      line = { words: [], baselineY: word.baselineY, height: word.height, column: word.column };
      lines.push(line);
    }
    line.words.push(word);
    line.baselineY = line.words.reduce((sum, value) => sum + value.baselineY, 0) / line.words.length;
    line.height = line.words.reduce((sum, value) => sum + value.height, 0) / line.words.length;
  });

  const splitLines = lines.flatMap((line) => {
    line.words.sort((a, b) => a.bbox[0][0] - b.bbox[0][0]);
    const segments = [];
    let segment = [];
    line.words.forEach((word) => {
      const previous = segment.at(-1);
      const horizontalGap = previous ? word.bbox[0][0] - previous.bbox[1][0] : 0;
      const splitThreshold = Math.max(viewport.width * 0.055, Math.max(line.height, word.height) * 3.5);
      if (previous && horizontalGap > splitThreshold) {
        segments.push(segment);
        segment = [];
      }
      segment.push(word);
    });
    if (segment.length) segments.push(segment);
    return segments;
  });

  const mergedLines = splitLines.map((wordsInLine) => {
    const xs = wordsInLine.flatMap((word) => [word.bbox[0][0], word.bbox[1][0]]);
    const ys = wordsInLine.flatMap((word) => [word.bbox[0][1], word.bbox[1][1]]);
    return { text: wordsInLine.map((word) => word.text).join(' '), bbox: [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]] };
  });

  const gutter = viewport.width * 0.04;
  const left = mergedLines.filter((line) => line.bbox[1][0] < midpoint + gutter);
  const right = mergedLines.filter((line) => line.bbox[0][0] > midpoint - gutter);
  if (left.length < 2 || right.length < 2) return mergedLines.sort((a, b) => a.bbox[0][1] - b.bbox[0][1]);

  const columnTop = Math.min(...left.map((line) => line.bbox[0][1]), ...right.map((line) => line.bbox[0][1]));
  const columnBottom = Math.max(...left.map((line) => line.bbox[1][1]), ...right.map((line) => line.bbox[1][1]));
  const spanning = mergedLines.filter((line) => !left.includes(line) && !right.includes(line));
  const byTop = (a, b) => a.bbox[0][1] - b.bbox[0][1];
  return [
    ...spanning.filter((line) => line.bbox[1][1] <= columnTop).sort(byTop),
    ...left.sort(byTop),
    ...right.sort(byTop),
    ...spanning.filter((line) => line.bbox[1][1] > columnTop && line.bbox[0][1] < columnBottom).sort(byTop),
    ...spanning.filter((line) => line.bbox[0][1] >= columnBottom).sort(byTop),
  ];
}

function PdfCanvas({ pdf, pageNumber, scale = 1.25, thumbnail = false, items = [], selectedItemIndex = null, onSelectItem }) {
  const canvasRef = useRef(null);
  const selectedOverlayRef = useRef(null);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    if (!pdf) return undefined;
    let cancelled = false;
    let renderTask;
    pdf.getPage(pageNumber).then((page) => {
      if (cancelled || !canvasRef.current) return;
      const viewport = page.getViewport({ scale });
      const canvas = canvasRef.current;
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      setCanvasSize({ width: viewport.width, height: viewport.height });
      renderTask = page.render({ canvasContext: canvas.getContext('2d'), viewport });
    });
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [pdf, pageNumber, scale]);

  useEffect(() => {
    selectedOverlayRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
  }, [selectedItemIndex]);

  if (thumbnail) return <canvas ref={canvasRef} className="pdf-thumb-canvas" />;
  return (
    <div className="pdf-preview-wrap" style={canvasSize}>
      <canvas ref={canvasRef} className="pdf-main-canvas" />
      {items.map((item, index) => {
        const xs = item.bbox.map((point) => point[0]);
        const ys = item.bbox.map((point) => point[1]);
        const x0 = Math.min(...xs);
        const y0 = Math.min(...ys);
        const x1 = Math.max(...xs);
        const y1 = Math.max(...ys);
        return <button ref={selectedItemIndex === index ? selectedOverlayRef : null} key={`${index}-${item.text}`} type="button" className={`bbox-overlay ${selectedItemIndex === index ? 'selected' : ''}`} style={{ left: x0 * scale, top: y0 * scale, width: Math.max((x1 - x0) * scale, 2), height: Math.max((y1 - y0) * scale, 2) }} onClick={() => onSelectItem?.(index)} aria-label={`${item.text} 위치`} />;
      })}
    </div>
  );
}


function ImagePreview({ src, fileName, scale, items = [], selectedItemIndex, onSelectItem, loading }) {
  const [naturalSize, setNaturalSize] = useState({ width: 0, height: 0 });
  const selectedOverlayRef = useRef(null);

  useEffect(() => {
    selectedOverlayRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
  }, [selectedItemIndex]);

  const width = naturalSize.width * scale;
  const height = naturalSize.height * scale;
  return (
    <div className="image-preview-wrap" style={naturalSize.width ? { width, height } : undefined}>
      <img
        className="image-main-preview"
        src={src}
        alt={`${fileName} 미리보기`}
        draggable="false"
        onClick={(event) => event.stopPropagation()}
        onLoad={(event) => setNaturalSize({
          width: event.currentTarget.naturalWidth,
          height: event.currentTarget.naturalHeight,
        })}
      />
      {naturalSize.width > 0 && items.map((item, index) => {
        const points = Array.isArray(item.bbox) ? item.bbox : [];
        const xs = points.map((point) => Number(point?.[0])).filter(Number.isFinite);
        const ys = points.map((point) => Number(point?.[1])).filter(Number.isFinite);
        if (!xs.length || !ys.length) return null;
        const x0 = Math.max(0, Math.min(...xs));
        const y0 = Math.max(0, Math.min(...ys));
        const x1 = Math.min(naturalSize.width, Math.max(...xs));
        const y1 = Math.min(naturalSize.height, Math.max(...ys));
        if (x1 <= x0 || y1 <= y0) return null;
        const boxWidth = x1 - x0;
        const boxHeight = y1 - y0;
        // A single OCR line cannot reasonably cover most of the page height.
        // Ignore malformed detector coordinates instead of obscuring the image.
        if (boxHeight > naturalSize.height * 0.2 || boxWidth * boxHeight > naturalSize.width * naturalSize.height * 0.18) return null;
        return <button ref={selectedItemIndex === index ? selectedOverlayRef : null} key={`${index}-${item.text}`} type="button" className={`bbox-overlay ${selectedItemIndex === index ? 'selected' : ''}`} style={{ left: x0 * scale, top: y0 * scale, width: Math.max(boxWidth * scale, 2), height: Math.max(boxHeight * scale, 2) }} onClick={(event) => { event.stopPropagation(); onSelectItem?.(index); }} aria-label={`${item.text} 위치`} />;
      })}
      {loading && <div className="image-processing"><span />OCR 처리 중...</div>}
    </div>
  );
}


function SpreadsheetPreview({ rows, items, selectedItemIndex, onSelectItem }) {
  const columnCount = Math.max(0, ...rows.map((row) => row.length));
  const columnLabel = (index) => {
    let value = index + 1;
    let label = '';
    while (value > 0) {
      value -= 1;
      label = String.fromCharCode(65 + (value % 26)) + label;
      value = Math.floor(value / 26);
    }
    return label;
  };
  const itemIndexByCell = new Map(items.map((item, index) => [`${item.row}:${item.column}`, index]));

  return (
    <div className="spreadsheet-preview">
      <table>
        <thead><tr><th className="sheet-corner" />{Array.from({ length: columnCount }, (_, index) => <th key={index}>{columnLabel(index)}</th>)}</tr></thead>
        <tbody>{rows.map((row, rowIndex) => (
          <tr key={rowIndex}>
            <th>{rowIndex + 1}</th>
            {Array.from({ length: columnCount }, (_, columnIndex) => {
              const itemIndex = itemIndexByCell.get(`${rowIndex + 1}:${columnIndex + 1}`);
              return <td key={columnIndex} className={itemIndex === selectedItemIndex ? 'selected' : ''}><button type="button" onClick={() => itemIndex !== undefined && onSelectItem?.(itemIndex)} disabled={itemIndex === undefined}>{row[columnIndex] ?? ''}</button></td>;
            })}
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function GeneratedWorkbookPreview({ title, rows, onBack, onDownload }) {
  const columnCount = Math.max(1, ...rows.map((row) => row.length));
  return <div className="generated-workbook-preview">
    <header><span className="xlsx-mark">X</span><div><strong>{title}</strong><small>선택한 행으로 만든 새 Excel 문서</small></div><nav><button type="button" onClick={onBack}>원본 보기</button><button type="button" className="download" onClick={onDownload}>Excel 다운로드</button></nav></header>
    <div className="workbook-sheet"><table>
      <thead><tr><th className="sheet-corner" />{Array.from({ length: columnCount }, (_, index) => <th key={index}>{columnLabel(index)}</th>)}</tr></thead>
      <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}><th>{rowIndex + 1}</th>{Array.from({ length: columnCount }, (_, columnIndex) => <td key={columnIndex}>{row[columnIndex] || ''}</td>)}</tr>)}</tbody>
    </table></div>
  </div>;
}

function amountFromCell(value) {
  const normalized = String(value ?? '').replace(/[^0-9.+-]/g, '');
  if (!normalized || !/[0-9]/.test(normalized)) return null;
  const amount = Number(normalized);
  return Number.isFinite(amount) ? amount : null;
}

function buildExtractionRows(rows, items) {
  if (Array.isArray(rows) && rows.length) {
    return rows.slice(0, 500).map((row, index) => ({
      id: `sheet-${index}`,
      cells: (row || []).map((cell) => String(cell ?? '')),
      evidenceIndex: items.findIndex((item) => item.row === index + 1),
    }));
  }
  const positioned = items.map((item, index) => {
    const points = Array.isArray(item.bbox) ? item.bbox : [];
    const xs = points.map((point) => Number(point?.[0])).filter(Number.isFinite);
    const ys = points.map((point) => Number(point?.[1])).filter(Number.isFinite);
    if (!(item.text || '').trim() || !xs.length || !ys.length) return null;
    const x0 = Math.min(...xs); const x1 = Math.max(...xs);
    const y0 = Math.min(...ys); const y1 = Math.max(...ys);
    return { text: String(item.text).trim(), index, x0, x1, y0, y1, cy: (y0 + y1) / 2, height: Math.max(y1 - y0, 1) };
  }).filter(Boolean).sort((a, b) => a.cy - b.cy || a.x0 - b.x0);
  if (!positioned.length) return [];

  // OCR 단어 상자의 세로 겹침을 이용해 실제 문서의 한 행으로 묶는다.
  const visualLines = [];
  positioned.forEach((item) => {
    let line = visualLines.find((candidate) => {
      const overlap = Math.min(candidate.y1, item.y1) - Math.max(candidate.y0, item.y0);
      return overlap >= Math.min(candidate.height, item.height) * 0.35
        || Math.abs(candidate.cy - item.cy) <= Math.max(candidate.height, item.height) * 0.58;
    });
    if (!line) {
      line = { items: [], y0: item.y0, y1: item.y1, cy: item.cy, height: item.height };
      visualLines.push(line);
    }
    line.items.push(item);
    line.y0 = Math.min(line.y0, item.y0); line.y1 = Math.max(line.y1, item.y1);
    line.cy = line.items.reduce((sum, value) => sum + value.cy, 0) / line.items.length;
    line.height = Math.max(line.y1 - line.y0, 1);
  });
  visualLines.forEach((line) => line.items.sort((a, b) => a.x0 - b.x0));
  visualLines.sort((a, b) => a.cy - b.cy);

  // 여러 행에서 반복되는 X 시작점을 열 경계로 인식한다. 제목처럼 한 칸인 행은 A열에 둔다.
  const minX = Math.min(...positioned.map((item) => item.x0));
  const maxX = Math.max(...positioned.map((item) => item.x1));
  const tolerance = Math.max((maxX - minX) * 0.025, 12);
  const clusters = [];
  visualLines.filter((line) => line.items.length >= 2).flatMap((line) => line.items).forEach((item) => {
    let cluster = clusters.find((value) => Math.abs(value.x - item.x0) <= tolerance);
    if (!cluster) { cluster = { x: item.x0, count: 0 }; clusters.push(cluster); }
    cluster.x = (cluster.x * cluster.count + item.x0) / (cluster.count + 1);
    cluster.count += 1;
  });
  let anchors = clusters.filter((cluster) => cluster.count >= 2).sort((a, b) => a.x - b.x).map((cluster) => cluster.x);
  if (anchors.length < 2) anchors = [minX];
  if (anchors.length > 12) anchors = anchors.slice(0, 12);

  return visualLines.slice(0, 500).map((line, rowIndex) => {
    const cells = Array.from({ length: line.items.length >= 2 ? anchors.length : 1 }, () => '');
    line.items.forEach((item) => {
      const column = line.items.length < 2 ? 0 : anchors.reduce((best, anchor, index) => (
        Math.abs(anchor - item.x0) < Math.abs(anchors[best] - item.x0) ? index : best
      ), 0);
      cells[column] = cells[column] ? `${cells[column]} ${item.text}` : item.text;
    });
    return {
      id: `ocr-line-${rowIndex}`,
      cells,
      evidenceIndex: line.items[0].index,
      evidenceIndices: line.items.map((item) => item.index),
      isTableRow: line.items.length >= 2,
    };
  });
}

function columnLabel(index) {
  let value = index + 1;
  let label = '';
  while (value > 0) {
    value -= 1;
    label = String.fromCharCode(65 + (value % 26)) + label;
    value = Math.floor(value / 26);
  }
  return label;
}

function ExtractionWorksheet({ rows, onChange, onEvidence, selectedIds, onSelectRange }) {
  const dragStartRef = useRef(null);
  const columnCount = Math.max(1, ...rows.map((row) => row.cells.length));
  const gridStyle = { gridTemplateColumns: `42px repeat(${columnCount}, minmax(140px, 1fr)) 76px` };
  const updateCell = (id, cellIndex, value) => onChange((current) => current.map((row) => row.id === id
    ? { ...row, cells: Array.from({ length: Math.max(row.cells.length, cellIndex + 1) }, (_, index) => index === cellIndex ? value : row.cells[index] || '') }
    : row));
  const selectThrough = (index) => {
    if (dragStartRef.current === null) return;
    const from = Math.min(dragStartRef.current, index);
    const to = Math.max(dragStartRef.current, index);
    onSelectRange(rows.slice(from, to + 1).map((row) => row.id));
  };
  const startSelecting = (rowIndex, rowId, event) => {
    if (event.button !== 0 || event.target.closest('.evidence-button')) return;
    dragStartRef.current = rowIndex;
    onSelectRange([rowId]);
  };
  useEffect(() => {
    const stopDragging = () => { dragStartRef.current = null; };
    window.addEventListener('pointerup', stopDragging);
    window.addEventListener('pointercancel', stopDragging);
    return () => {
      window.removeEventListener('pointerup', stopDragging);
      window.removeEventListener('pointercancel', stopDragging);
    };
  }, []);
  return <div className="financial-sheet extraction-sheet">
    <div className="sheet-column-head" style={gridStyle}><b>#</b>{Array.from({ length: columnCount }, (_, index) => <b key={index}>{columnLabel(index)}</b>)}<b>근거</b></div>
    <div className="sheet-rows">{rows.map((row, rowIndex) => (
      <div className={`sheet-row ${row.isTableRow ? 'table-row' : 'text-row'} ${selectedIds.includes(row.id) ? 'selected' : ''}`} style={gridStyle} key={row.id} onPointerDown={(event) => startSelecting(rowIndex, row.id, event)} onPointerEnter={() => selectThrough(rowIndex)}>
        <button className="row-selector" type="button" onPointerDown={(event) => event.preventDefault()}>{rowIndex + 1}</button>
        {Array.from({ length: columnCount }, (_, cellIndex) => <input key={cellIndex} value={row.cells[cellIndex] || ''} onChange={(event) => updateCell(row.id, cellIndex, event.target.value)} aria-label={`${rowIndex + 1}행 ${columnLabel(cellIndex)}열`} />)}
        <button className="evidence-button" type="button" disabled={row.evidenceIndex < 0} onClick={() => onEvidence(row.evidenceIndex)}>근거 보기</button>
      </div>
    ))}{!rows.length && <div className="sheet-empty"><strong>추출된 데이터가 없습니다.</strong><p>오른쪽에 문서를 업로드하면 OCR 결과가 행으로 구성됩니다.</p></div>}</div>
  </div>;
}
export default function OCRPage() {
  const navigate = useNavigate();
  const [user, setUser] = useState(getAppUser);
  const [pdf, setPdf] = useState(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState('');
  const [preprocessedImageUrl, setPreprocessedImageUrl] = useState('');
  const [previewVariant, setPreviewVariant] = useState('original');
  const [preprocessingInfo, setPreprocessingInfo] = useState(null);
  const [validationRows, setValidationRows] = useState([]);
  const [selectedRowIds, setSelectedRowIds] = useState([]);
  const [exportingRows, setExportingRows] = useState(false);
  const [generatedWorkbook, setGeneratedWorkbook] = useState(null);
  const [fileName, setFileName] = useState(EMPTY_FILE_NAME);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageTexts, setPageTexts] = useState([]);
  const [pageItems, setPageItems] = useState([]);
  const [pageRows, setPageRows] = useState([]);
  const [sheetNames, setSheetNames] = useState([]);
  const [selectedItemIndex, setSelectedItemIndex] = useState(null);
  const [currentDocumentId, setCurrentDocumentId] = useState(null);
  const [pendingFile, setPendingFile] = useState(null);
  const [processingMode, setProcessingMode] = useState('receipt');
  const [zoom, setZoom] = useState(1.05);
  const [loading, setLoading] = useState(false);
  const [projectTransition, setProjectTransition] = useState(false);
  const [resultTab, setResultTab] = useState('text');
  const [groundTruth, setGroundTruth] = useState('');
  const [groundTruthFileName, setGroundTruthFileName] = useState('');
  const [processingTimeMs, setProcessingTimeMs] = useState(null);
  const [evaluationStatus, setEvaluationStatus] = useState('');
  const [error, setError] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyItems, setHistoryItems] = useState([]);
  const [historySearch, setHistorySearch] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [financeRecord, setFinanceRecord] = useState(() => readReceiptWorkspace().financeRecord);
  const [financeRecords, setFinanceRecords] = useState(() => readReceiptWorkspace().financeRecords);
  const [financeReviewOpen, setFinanceReviewOpen] = useState(false);
  const [financeReviewDraft, setFinanceReviewDraft] = useState(null);
  const [financeTaxonomy, setFinanceTaxonomy] = useState({ document_types: [], expense_categories: [], category_to_document_type: {} });
  const [savedFinanceOpen, setSavedFinanceOpen] = useState(false);
  const [savedFinanceRecords, setSavedFinanceRecords] = useState([]);
  const [savedFinanceLoading, setSavedFinanceLoading] = useState(false);
  const [savedFinanceSearch, setSavedFinanceSearch] = useState('');
  const [financeDuplicateNotice, setFinanceDuplicateNotice] = useState('');
  const [evaluationDatasetFile, setEvaluationDatasetFile] = useState(null);
  const [receiptBatchStatus, setReceiptBatchStatus] = useState('');
  const [receiptBatchActive, setReceiptBatchActive] = useState(false);
  const inputRef = useRef(null);
  const imagePreviewRef = useRef('');
  const projectTransitionTimerRef = useRef(null);
  const extractionStartedAtRef = useRef(null);
  const evaluationPanelRef = useRef(null);
  const groundTruthFileRef = useRef(null);
  const evaluationDatasetRef = useRef(null);
  const evaluationFolderRef = useRef(null);
  const generatedWorkbookUrlRef = useRef('');
  const receiptBatchRef = useRef({ files: [], index: -1, active: false, recordIds: [], evaluationEntries: [] });

  useEffect(() => {
    saveReceiptRecords(financeRecord, financeRecords);
  }, [financeRecord, financeRecords]);

  useEffect(() => {
    let active = true;
    apiClient.get('/finance/taxonomy')
      .then(({ data }) => { if (active) setFinanceTaxonomy(data); })
      .catch(() => { if (active) setFinanceTaxonomy({ document_types: [], expense_categories: [], category_to_document_type: {} }); });
    return () => { active = false; };
  }, []);

  const openReceiptBatchEvaluation = async (fileList) => {
    const files = Array.from(fileList || []).filter((file) => /\.(png|jpe?g|webp|bmp|pdf)$/i.test(file.name));
    if (files.length < 2) {
      setError('일괄 처리할 영수증 이미지 또는 PDF를 2개 이상 선택해 주세요.');
      return;
    }
    if (files.length > 50) {
      setError('이미지 일괄 입력은 한 번에 최대 50개까지 처리할 수 있습니다.');
      return;
    }
    if (isReceiptEvaluator && evaluationDatasetFile) {
      clearPendingReceipts();
      clearFinanceEvaluationRuns();
      queueFinanceEvaluationInput(evaluationDatasetFile, files);
      localStorage.setItem('pic_to_text_receipt_report_tab', 'experiment');
      navigate('/reports?view=developer&developerReport=receipt&receiptTab=experiment');
      return;
    }
    clearPendingReceipts();
    clearFinanceEvaluationRuns();
    setError('');
    receiptBatchRef.current = { files, index: 0, active: true, recordIds: [], evaluationEntries: [] };
    setFinanceRecords([]);
    setFinanceRecord(null);
    setReceiptBatchActive(true);
    setReceiptBatchStatus(`1/${files.length} · ${files[0].name} 처리 중`);
    await runExtraction(files[0]);
  };

  const replaceImagePreview = (file) => {
    if (imagePreviewRef.current) URL.revokeObjectURL(imagePreviewRef.current);
    const nextUrl = file ? URL.createObjectURL(file) : '';
    imagePreviewRef.current = nextUrl;
    setImagePreviewUrl(nextUrl);
  };

  const loadSavedFinanceRecords = async () => {
    setSavedFinanceLoading(true);
    try {
      const { data } = await apiClient.get('/finance/records');
      setSavedFinanceRecords(Array.isArray(data) ? data : []);
    } catch {
      setSavedFinanceRecords([]);
    } finally {
      setSavedFinanceLoading(false);
    }
  };

  const resetDocumentView = ({ preserveGroundTruth = false, preserveFinance = false } = {}) => {
    if (generatedWorkbookUrlRef.current) URL.revokeObjectURL(generatedWorkbookUrlRef.current);
    generatedWorkbookUrlRef.current = '';
    setGeneratedWorkbook(null);
    setPdf(null);
    replaceImagePreview(null);
    setPreprocessedImageUrl('');
    setPreviewVariant('original');
    setPreprocessingInfo(null);
    setValidationRows([]);
    setSelectedRowIds([]);
    setPageTexts([]);
    setPageItems([]);
    setPageRows([]);
    setSheetNames([]);
    setPageNumber(1);
    setSelectedItemIndex(null);
    setCurrentDocumentId(null);
    setResultTab('text');
    if (!preserveGroundTruth) {
      setGroundTruth('');
      setGroundTruthFileName('');
    }
    setProcessingTimeMs(null);
    setEvaluationStatus('');
    if (!preserveFinance) setFinanceRecord(null);
  };

  const startNewProject = () => {
    if (loading || projectTransition) return;
    setProjectTransition(true);
    projectTransitionTimerRef.current = window.setTimeout(() => {
      resetDocumentView();
      setFileName(EMPTY_FILE_NAME);
      setProcessingMode('receipt');
      setZoom(1.05);
      setError('');
      setPendingFile(null);
      setFinanceRecords([]);
      receiptBatchRef.current = { files: [], index: -1, active: false, recordIds: [], evaluationEntries: [] };
      setReceiptBatchActive(false);
      setReceiptBatchStatus('');
      if (inputRef.current) inputRef.current.value = '';
      setProjectTransition(false);
      projectTransitionTimerRef.current = null;
    }, 420);
  };

  const loadOcrHistory = async () => {
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const { data } = await apiClient.get('/ocr/history', { params: { upload_origin: 'OCR' } });
      setHistoryItems(Array.isArray(data) ? data : []);
    } catch (requestError) {
      setHistoryError(requestError.response?.data?.detail || 'OCR 처리 기록을 불러오지 못했습니다.');
    } finally {
      setHistoryLoading(false);
    }
  };

  const openHistory = () => {
    setHistoryOpen(true);
    loadOcrHistory();
  };

  const loadHistoryDocument = async (documentId, targetPage = 1, targetBbox = null) => {
    setPendingFile(null);
    setLoading(true);
    setError('');
    resetDocumentView();
    try {
      const [{ data: result }, fileResult] = await Promise.all([
        apiClient.get(`/ocr/documents/${documentId}`),
        apiClient.get(`/ocr/documents/${documentId}/file`, { responseType: 'blob', timeout: 60000 }),
      ]);
      const blob = fileResult.data;
      const pages = result.pages || [];

      setPageTexts(pages.map((page) => page.text || ''));
      setPageItems(pages.map((page) => page.items || []));
      setPageRows(pages.map((page) => page.rows ?? null));
      setSheetNames(pages.map((page) => page.sheet_name || ''));
      setFileName(result.filename);
      setCurrentDocumentId(documentId);
      const safePage = Math.min(Math.max(Number(targetPage) || 1, 1), Math.max(pages.length, 1));
      setPageNumber(safePage);
      if (targetBbox && pages[safePage - 1]?.items?.length) {
        const [targetStart, targetEnd] = targetBbox;
        const targetX = (Number(targetStart?.[0]) + Number(targetEnd?.[0])) / 2;
        const targetY = (Number(targetStart?.[1]) + Number(targetEnd?.[1])) / 2;
        const closest = pages[safePage - 1].items.reduce((best, item, index) => {
          const xs = (item.bbox || []).map((point) => Number(point[0])).filter(Number.isFinite);
          const ys = (item.bbox || []).map((point) => Number(point[1])).filter(Number.isFinite);
          if (!xs.length || !ys.length) return best;
          const distance = Math.hypot((Math.min(...xs) + Math.max(...xs)) / 2 - targetX, (Math.min(...ys) + Math.max(...ys)) / 2 - targetY);
          return distance < best.distance ? { index, distance } : best;
        }, { index: null, distance: Infinity });
        setSelectedItemIndex(closest.index);
      }

      if (/\.pdf$/i.test(result.filename)) {
        const pdfData = await blob.arrayBuffer();
        setPdf(await pdfjsLib.getDocument({ data: pdfData }).promise);
      } else if (/\.(png|jpe?g|webp|bmp)$/i.test(result.filename)) {
        replaceImagePreview(blob);
      }
    } catch (requestError) {
      setError(requestError.response?.data?.detail || requestError.message || '저장된 문서를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let active = true;

    apiClient.get('/auth/me')
      .then(({ data }) => {
        if (!active) return;
        setUser(data);
        saveAppUser(data);
      })
      .catch(() => {
        // 저장된 세션 정보로 사용자 영역을 유지합니다.
      });
    const queryParams = new URLSearchParams(window.location.search);
    const linkedDocument = queryParams.get('document');
    const linkedPage = queryParams.get('page');
    let linkedBbox = null;
    try { linkedBbox = JSON.parse(queryParams.get('bbox') || 'null'); } catch { linkedBbox = null; }
    if (linkedDocument) loadHistoryDocument(linkedDocument, linkedPage, linkedBbox);
    loadSavedFinanceRecords();

    return () => {
      active = false;
      if (projectTransitionTimerRef.current) window.clearTimeout(projectTransitionTimerRef.current);
      if (imagePreviewRef.current) URL.revokeObjectURL(imagePreviewRef.current);
    };
  }, []);

  useEffect(() => {
    if (!historyOpen) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') setHistoryOpen(false);
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [historyOpen]);

  useEffect(() => {
    setResultTab('text');
  }, [pageNumber]);

  const loadWithOcr = async (file) => {
    const formData = new FormData();
    formData.append('file', file);
    const { data } = await apiClient.post('/ocr/upload?processing_mode=receipt', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 300000,
    });

    setPdf(null);
    setPageTexts((data.pages || []).map((page) => page.text || ''));
    setPageItems((data.pages || []).map((page) => page.items || []));
    setPageRows((data.pages || []).map((page) => page.rows ?? null));
    setSheetNames((data.pages || []).map((page) => page.sheet_name || ''));
    setSelectedItemIndex(null);
    setPageNumber(1);
    setFileName(data.filename || file.name);
    setCurrentDocumentId(data.document_id || null);
    setPreprocessedImageUrl(data.preprocessed_image || '');
    setPreprocessingInfo(data.preprocessing ? { ...data.preprocessing, timings: data.timings || null, evaluation: data.evaluation || null } : null);
    // Keep the extracted evidence visible. The processed image remains an
    // explicit comparison view, but must not replace the result automatically.
    setPreviewVariant('original');
    return data;
  };

  const loadDocxPreview = async (file) => {
    const formData = new FormData();
    formData.append('file', file);
    const { data } = await apiClient.post('/ocr/docx-preview', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      responseType: 'arraybuffer',
      timeout: 120000,
    });
    return pdfjsLib.getDocument({ data }).promise;
  };

  const loadSpreadsheetPreview = async (file) => {
    const formData = new FormData();
    formData.append('file', file);
    const { data } = await apiClient.post('/ocr/spreadsheet-preview', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    });
    const pages = data.pages || [];
    setPageTexts(pages.map((page) => page.text || ''));
    setPageItems(pages.map((page) => page.items || []));
    setPageRows(pages.map((page) => page.rows ?? []));
    setSheetNames(pages.map((page) => page.sheet_name || ''));
    setPageNumber(1);
    setSelectedItemIndex(null);
  };

  const loadPdf = async (file) => {
    if (!file || (file.type !== 'application/pdf' && !/\.pdf$/i.test(file.name))) {
      setError('PDF 파일만 업로드할 수 있습니다.');
      return;
    }
    setLoading(true);
    setError('');
    resetDocumentView({ preserveGroundTruth: true });
    setFileName(file.name);
    try {
      const data = await file.arrayBuffer();
      const document = await pdfjsLib.getDocument({ data }).promise;
      const extractedPages = await Promise.all(
        Array.from({ length: document.numPages }, async (_, index) => {
          const page = await document.getPage(index + 1);
          const content = await page.getTextContent();
          const items = buildReadingOrder(content, page.getViewport({ scale: 1 }));
          return { items, text: items.map((item) => item.text).join('\n') };
        }),
      );
      const texts = extractedPages.map((page) => page.text);
      let documentId = null;
      let resultTexts = texts;
      let resultPages = extractedPages;
      if (texts.some((text) => text.length > 0)) {
        const archiveData = new FormData();
        archiveData.append('file', file);
        archiveData.append('result_json', JSON.stringify({
          filename: file.name,
          content_type: 'text_only',
          pages: extractedPages.map((page, index) => ({
            page: index + 1,
            text: page.text,
            items: page.items.map((item) => ({ text: item.text, confidence: 1, bbox: item.bbox })),
          })),
        }));
        const { data: archived } = await apiClient.post('/ocr/archive', archiveData, {
          headers: { 'Content-Type': 'multipart/form-data' },
          timeout: 60000,
        });
        setPdf(document);
        setPageTexts(texts);
        setPageItems(extractedPages.map((page) => page.items));
        setSelectedItemIndex(null);
        setPageNumber(1);
        setFileName(file.name);
        setCurrentDocumentId(archived.document_id || null);
        documentId = archived.document_id || null;
      } else {
        const result = await loadWithOcr(file);
        documentId = result.document_id || null;
        resultTexts = (result.pages || []).map((page) => page.text || '');
        resultPages = result.pages || [];
        setPdf(document);
      }
      return { success: true, documentId, texts: resultTexts, pages: resultPages };
    } catch (requestError) {
      setError(requestError.response?.data?.detail || 'PDF를 읽지 못했습니다. 손상되었거나 지원하지 않는 파일일 수 있습니다.');
      return { success: false };
    } finally {
      if (extractionStartedAtRef.current) setProcessingTimeMs(performance.now() - extractionStartedAtRef.current);
      setLoading(false);
    }
  };

  const loadFile = async (file, preparedPdf = null) => {
    if (!file) return;
    extractionStartedAtRef.current = performance.now();

    if (file.type === 'application/pdf' || /\.pdf$/i.test(file.name)) {
      return loadPdf(file);
    }

    resetDocumentView({ preserveGroundTruth: true });
    setFileName(file.name);
    const canPreviewImage = /\.(png|jpe?g|webp|bmp)$/i.test(file.name);
    replaceImagePreview(canPreviewImage ? file : null);

    setLoading(true);
    setError('');
    try {
      const result = await loadWithOcr(file);
      if (/\.docx$/i.test(file.name) && preparedPdf) setPdf(preparedPdf);
      return {
        success: true,
        documentId: result.document_id || null,
        texts: (result.pages || []).map((page) => page.text || ''),
        pages: result.pages || [],
      };
    } catch (requestError) {
      setError(requestError.response?.data?.detail || '파일에서 텍스트를 추출하지 못했습니다. OCR 서버 상태와 파일 형식을 확인해 주세요.');
      return { success: false };
    } finally {
      setProcessingTimeMs(performance.now() - extractionStartedAtRef.current);
      setLoading(false);
    }
  };

  const prepareFile = async (file) => {
    if (!file) return;
    if (!receiptBatchRef.current.active) setReceiptBatchStatus('');
    resetDocumentView({
      preserveGroundTruth: isDeveloper,
      preserveFinance: true,
    });
    setProcessingMode('receipt');
    setPendingFile(file);
    setFileName(file.name);
    setError('');
    try {
      if (file.type === 'application/pdf' || /\.pdf$/i.test(file.name)) {
        const document = await pdfjsLib.getDocument({ data: await file.arrayBuffer() }).promise;
        setPdf(document);
      } else if (/\.docx$/i.test(file.name)) {
        setLoading(true);
        setPdf(await loadDocxPreview(file));
      } else if (/\.(xlsx|xlsm)$/i.test(file.name)) {
        setLoading(true);
        await loadSpreadsheetPreview(file);
      } else {
        replaceImagePreview(/\.(png|jpe?g|webp|bmp)$/i.test(file.name) ? file : null);
      }
    } catch (previewError) {
      setError(previewError.response?.data?.detail || previewError.message || '파일 미리보기를 준비하지 못했습니다.');
    } finally {
      setLoading(false);
    }
  };

  const evaluateReceiptForReport = async (file, result, financeRecord) => {
    if (!isReceiptEvaluator || !evaluationDatasetFile || !result.documentId || !financeRecord?.id) return;
    setEvaluationStatus('영수증 문서화 완료 · 상세 평가 중...');
    try {
      const rows = datasetRows(JSON.parse(await evaluationDatasetFile.text()));
      const uploadedName = normalizedFileName(file.name);
      const singleWithoutImageName = rows.length === 1 && !imageNameOf(rows[0]);
      const matches = singleWithoutImageName
        ? [{ row: rows[0], index: 0 }]
        : rows.map((row, index) => ({ row, index })).filter(({ row }) => normalizedFileName(imageNameOf(row)) === uploadedName);
      if (matches.length !== 1) {
        throw new Error(matches.length ? `${file.name}와 매칭되는 정답이 여러 개입니다.` : `정답 JSON에서 ${file.name}을 찾지 못했습니다.`);
      }
      const { data: modelData } = await apiClient.get('/finance-evaluations/models');
      const available = Array.isArray(modelData?.models) ? modelData.models : [];
      const configuredModel = String(modelData?.default_model || '').trim();
      if (modelData?.warning) throw new Error(modelData.warning);
      const modelNames = available.includes(configuredModel) ? [configuredModel] : [];
      if (!modelNames.length) throw new Error('영수증 LLM 모델이 설정되지 않았습니다.');
      const { data: evaluation } = await apiClient.post('/finance-evaluations/record', {
        document_id: result.documentId,
        record_id: financeRecord.id,
        ground_truth: truthOf(matches[0].row),
        dataset_name: evaluationDatasetFile.name,
        dataset_index: matches[0].index,
        source_file_name: file.name,
        latency_ms: Math.round(processingTimeMs || 0),
      }, { timeout: 1200000 });
      appendFinanceEvaluationRun({
        ...evaluation,
        dataset_name: evaluationDatasetFile.name,
        dataset_index: matches[0].index,
        matched_image: file.name,
        evaluated_at: new Date().toISOString(),
        batch_id: null,
      });
      markReceiptEvaluated(result.documentId);
      localStorage.setItem('pic_to_text_developer_report', 'receipt');
      setEvaluationStatus('문서화와 상세 평가가 완료되었습니다. 리포트에서 확인할 수 있습니다.');
    } catch (evaluationError) {
      setEvaluationStatus(`문서화는 완료되었지만 평가를 저장하지 못했습니다. ${evaluationError.response?.data?.detail || evaluationError.message}`);
    }
  };

  const runExtraction = async (queuedFile = null) => {
    const sourceFile = queuedFile instanceof File ? queuedFile : pendingFile;
    if (!sourceFile || loading) return;
    setFinanceDuplicateNotice('');
    const result = await loadFile(sourceFile, queuedFile instanceof File ? null : pdf);
    if (!result?.success) {
      if (receiptBatchRef.current.active) {
        receiptBatchRef.current.active = false;
        setReceiptBatchActive(false);
        setReceiptBatchStatus(`일괄 처리가 중단되었습니다 · ${sourceFile.name}`);
      }
      return;
    }
    setPendingFile(null);

    const extractedText = (result.texts || []).join('\n');
    if (!hasReceiptContent(extractedText)) {
      if (receiptBatchRef.current.active) {
        receiptBatchRef.current.active = false;
        setReceiptBatchActive(false);
        setReceiptBatchStatus(`영수증 내용이 없어 일괄 처리가 중단되었습니다 · ${sourceFile.name}`);
      }
      setError('추출된 문서에서 영수증 관련 내용을 찾지 못했습니다. 영수증이 포함된 파일인지 확인해 주세요.');
      return;
    }

    if (result.documentId) {
      setLoading(true);
      try {
        const { data: financeRecord } = await apiClient.post('/finance/records/classify', { document_id: result.documentId }, { timeout: 180000 });
        setFinanceRecord(financeRecord);
        setFinanceRecords((current) => current.some((item) => item.id === financeRecord.id) ? current : [...current, financeRecord]);
        if (receiptBatchRef.current.active && !receiptBatchRef.current.recordIds.includes(financeRecord.id)) {
          receiptBatchRef.current.recordIds.push(financeRecord.id);
        }
        setSavedFinanceRecords((current) => current.some((item) => item.id === financeRecord.id) ? current.map((item) => item.id === financeRecord.id ? financeRecord : item) : [financeRecord, ...current]);
        rememberPendingReceipt({
          document_id: result.documentId,
          document_name: sourceFile.name,
          model_name: financeRecord.model_name,
          created_at: new Date().toISOString(),
        });
        if (financeRecord.structured_data?.duplicate_detection?.is_duplicate) setFinanceDuplicateNotice('이미 문서화된 영수증입니다. OCR 결과만 갱신하고 재무 행은 추가하지 않았습니다.');
        setResultTab('text');
        if (receiptBatchRef.current.active) {
          const batch = receiptBatchRef.current;
          batch.evaluationEntries.push({ file: sourceFile, result, financeRecord });
          setReceiptBatchStatus(`${batch.index + 1}/${batch.files.length} · 사용자 최종 확인 대기 · ${sourceFile.name}`);
        } else {
          await evaluateReceiptForReport(sourceFile, result, financeRecord);
        }
      } catch (requestError) {
        if (receiptBatchRef.current.active) {
          receiptBatchRef.current.active = false;
          setReceiptBatchActive(false);
          setReceiptBatchStatus(`문서화 오류로 일괄 처리가 중단되었습니다 · ${sourceFile.name}`);
        }
        setError(requestError.response?.data?.detail || 'OCR은 완료됐지만 재무 양식에 자동 입력하지 못했습니다.');
      } finally {
        setLoading(false);
      }
    }

    if (processingMode !== 'receipt' && isDeveloper && truthForEvaluation && result.documentId) {
      setEvaluationStatus('평가 저장 중...');
      try {
        await apiClient.post('/reports/evaluations', {
          document_id: result.documentId,
          document_name: fileName,
          extracted_text: (result.texts || []).join('\n\n'),
          ground_truth: truthForEvaluation,
          processing_time_ms: extractionStartedAtRef.current ? performance.now() - extractionStartedAtRef.current : processingTimeMs,
        });
        setGroundTruth(truthForEvaluation);
        setEvaluationStatus('추출과 평가가 완료되었습니다. 성능 리포트에서 확인할 수 있습니다.');
      } catch (requestError) {
        setGroundTruth(truthForEvaluation);
        setEvaluationStatus(requestError.response?.data?.detail || '추출은 완료됐지만 평가를 저장하지 못했습니다. 다시 평가해 주세요.');
      }
    }
  };

  const saveDeveloperEvaluation = async () => {
    if (!currentDocumentId || !groundTruth.trim()) return;
    setEvaluationStatus('저장 중...');
    try {
      await apiClient.post('/reports/evaluations', {
        document_id: currentDocumentId,
        document_name: fileName,
        extracted_text: pageTexts.join('\n\n'),
        ground_truth: groundTruth,
        processing_time_ms: processingTimeMs,
      });
      setEvaluationStatus('평가 데이터가 저장되었습니다. 리포트에서 확인할 수 있습니다.');
    } catch (requestError) {
      setEvaluationStatus(requestError.response?.data?.detail || '평가 데이터를 저장하지 못했습니다.');
    }
  };

  const loadGroundTruthFile = async (file) => {
    if (!file) return;
    try {
      const raw = await file.text();
      if (/\.json$/i.test(file.name) || file.type === 'application/json') {
        const data = JSON.parse(raw);
        const preferred = data?.ground_truth ?? data?.text ?? data?.content ?? data?.answer;
        const value = typeof preferred === 'string'
          ? preferred
          : Array.isArray(preferred)
            ? preferred.map((item) => typeof item === 'string' ? item : JSON.stringify(item)).join('\n')
            : typeof data === 'string'
              ? data
              : JSON.stringify(data, null, 2);
        setGroundTruth(value);
        setGroundTruthFileName(file.name);
      } else {
        setGroundTruth(raw);
        setGroundTruthFileName(file.name);
      }
      setEvaluationStatus(`${file.name} 파일을 불러왔습니다.`);
    } catch (fileError) {
      setEvaluationStatus(fileError instanceof SyntaxError ? 'JSON 파일 형식이 올바르지 않습니다.' : '정답 데이터 파일을 읽지 못했습니다.');
    }
  };

  const downloadText = () => {
    const body = pageTexts.map((text, index) => `--- ${index + 1} 페이지 ---\n${text}`).join('\n\n');
    const url = URL.createObjectURL(new Blob([body], { type: 'text/plain;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${fileName.replace(/\.pdf$/i, '') || 'extracted'}-text.txt`;
    anchor.click();
    URL.revokeObjectURL(url);
  };
  const currentText = pageTexts[pageNumber - 1] || '';
  const currentItems = pageItems[pageNumber - 1] || EMPTY_ITEMS;
  const currentRows = pageRows[pageNumber - 1];
  const currentFinanceRecords = financeRecord ? [financeRecord] : [];
  const pageCount = pdf?.numPages || pageTexts.length;
  const hasResult = pageTexts.length > 0;
  const isDeveloper = ['DEVELOPER', 'ADMIN'].includes(user?.role) || user?.email === 'developer@docunex.com';
  const isReceiptEvaluator = user?.role === 'DEVELOPER' || user?.email === 'developer@docunex.com';
  const isEnterprise = user?.subscription_tier === 'ENTERPRISE' || user?.subscriptionTier === 'ENTERPRISE';
  const fileExtension = fileName.includes('.') ? fileName.split('.').pop().toUpperCase() : 'FILE';
  const displayedImageUrl = previewVariant === 'processed' && preprocessedImageUrl ? preprocessedImageUrl : imagePreviewUrl;
  const handleReceiptFileSelection = (file) => {
    if (!file) return;
    if (!receiptBatchRef.current.active) {
      clearPendingReceipts();
      clearFinanceEvaluationRuns();
      receiptBatchRef.current = { files: [], index: -1, active: false, recordIds: [], evaluationEntries: [] };
    }
    prepareFile(file);
  };
  const savedFinanceGroups = useMemo(() => {
    const groups = new Map();
    savedFinanceRecords.forEach((record) => {
      const createdAt = record.created_at ? new Date(record.created_at) : new Date();
      const month = Number.isNaN(createdAt.getTime()) ? '날짜 미확인' : `${createdAt.getFullYear()}-${String(createdAt.getMonth() + 1).padStart(2, '0')}`;
      const key = `${month}:${record.document_type}`;
      if (!groups.has(key)) groups.set(key, { key, month, documentType: record.document_type, records: [] });
      groups.get(key).records.push(record);
    });
    return [...groups.values()].map((group) => {
      group.records.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
      group.total = group.records.reduce((sum, record) => sum + Number(record.total_amount || 0), 0);
      const latest = group.records[group.records.length - 1];
      const workflow = latest?.structured_data?.finance_workflow || {};
      group.status = workflow.finance_confirmed_at ? '재무팀 확인' : workflow.submitted_at ? '재무팀 전달' : group.records.every((record) => record.status === 'CONFIRMED') ? '최종 확정' : group.records.some((record) => record.status === 'CONFIRMED') ? '확인 필요' : '작성 중';
      return group;
    }).sort((a, b) => b.key.localeCompare(a.key));
  }, [savedFinanceRecords]);

  useEffect(() => {
    setValidationRows(buildExtractionRows(currentRows, currentItems));
    setSelectedRowIds([]);
  }, [currentDocumentId, pageNumber, currentRows, currentItems]);

  const createExcelDocument = async () => {
    const selectedRows = validationRows.filter((row) => selectedRowIds.includes(row.id));
    if (!selectedRows.length || exportingRows) return;
    setExportingRows(true);
    setError('');
    try {
      const { data } = await apiClient.post('/ocr/export-workbook', {
        title: `${fileName.replace(/\.[^.]+$/, '') || '추출 문서'} 선택 데이터`,
        rows: selectedRows.map((row) => row.cells),
      }, { responseType: 'blob', timeout: 60000 });
      if (generatedWorkbookUrlRef.current) URL.revokeObjectURL(generatedWorkbookUrlRef.current);
      const url = URL.createObjectURL(data);
      generatedWorkbookUrlRef.current = url;
      setGeneratedWorkbook({
        title: `${fileName.replace(/\.[^.]+$/, '') || '추출 문서'} 선택 데이터`,
        fileName: `${fileName.replace(/\.[^.]+$/, '') || '추출-문서'}-선택행.xlsx`,
        rows: selectedRows.map((row) => [...row.cells]),
        url,
      });
    } catch (requestError) {
      setError(requestError.response?.data?.detail || '선택한 행으로 Excel 문서를 만들지 못했습니다.');
    } finally {
      setExportingRows(false);
    }
  };

  const downloadFinanceDocument = async () => {
    if (!financeRecord) return;
    setExportingRows(true);
    setError('');
    try {
      const batchRecordIds = new Set(receiptBatchRef.current.recordIds || []);
      const records = batchRecordIds.size
        ? financeRecords.filter((item) => batchRecordIds.has(item.id))
        : [financeRecord];
      if (!records.length) throw new Error('Excel에 포함할 재무 기록이 없습니다.');
      const response = await apiClient.post('/finance/records/export', { record_ids: records.map((item) => item.id) }, { responseType: 'blob', timeout: 60000 });
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = batchRecordIds.size > 1
        ? `일괄_재무문서_${new Date().toISOString().slice(0, 10)}.xlsx`
        : `${FINANCE_DOCUMENTS[financeRecord.document_type]?.title || '재무문서'}_${fileName.replace(/\.[^.]+$/, '')}.xlsx`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (requestError) {
      setError(requestError.response?.data?.detail || '재무 양식 Excel을 만들지 못했습니다.');
    } finally {
      setExportingRows(false);
    }
  };

  const openFinanceReview = () => {
    if (!financeRecord) return;
    setFinanceReviewDraft({
      document_type: financeRecord.document_type,
      expense_category: financeRecord.expense_category || '',
      merchant: financeRecord.merchant || '',
      transaction_date: financeRecord.transaction_date || '',
      supply_amount: Number(financeRecord.supply_amount || 0),
      tax_amount: Number(financeRecord.tax_amount || 0),
      total_amount: Number(financeRecord.total_amount || 0),
      payment_method: financeRecord.payment_method || '',
      description: financeRecord.description || '',
    });
    setFinanceReviewOpen(true);
  };

  const saveFinanceReview = async (event) => {
    event.preventDefault();
    if (!financeRecord || !financeReviewDraft) return;
    setLoading(true);
    setError('');
    try {
      const { data } = await apiClient.patch(`/finance/records/${financeRecord.id}`, {
        ...financeReviewDraft,
        supply_amount: Number(financeReviewDraft.supply_amount || 0),
        tax_amount: Number(financeReviewDraft.tax_amount || 0),
        total_amount: Number(financeReviewDraft.total_amount || 0),
        merchant: financeReviewDraft.merchant || null,
        transaction_date: financeReviewDraft.transaction_date || null,
        payment_method: financeReviewDraft.payment_method || null,
        description: financeReviewDraft.description || null,
        status: 'REVIEW',
      });
      setFinanceRecord(data);
      setFinanceRecords((current) => current.map((item) => item.id === data.id ? data : item));
      setSavedFinanceRecords((current) => current.map((item) => item.id === data.id ? data : item));
      setFinanceReviewOpen(false);
      setFinanceReviewDraft(null);
    } catch (requestError) {
      setError(requestError.response?.data?.detail || '수정한 재무 정보를 저장하지 못했습니다.');
    } finally {
      setLoading(false);
    }
  };

  const confirmFinanceRecord = async () => {
    if (!financeRecord || financeRecord.status === 'CONFIRMED') return;
    if (!financeRecord.document_type || !financeRecord.expense_category) {
      setError('문서 유형과 카테고리를 검토 화면에서 선택해 주세요.');
      return;
    }
    let nextBatchFile = null;
    let completedEvaluationEntries = null;
    setLoading(true);
    setError('');
    try {
      const { data } = await apiClient.patch(`/finance/records/${financeRecord.id}`, {
        document_type: financeRecord.document_type,
        expense_category: financeRecord.expense_category,
        merchant: financeRecord.merchant || null,
        transaction_date: financeRecord.transaction_date || null,
        supply_amount: Number(financeRecord.supply_amount || 0),
        tax_amount: Number(financeRecord.tax_amount || 0),
        total_amount: Number(financeRecord.total_amount || 0),
        payment_method: financeRecord.payment_method || null,
        description: financeRecord.description || null,
        status: 'CONFIRMED',
      });
      setFinanceRecord(data);
      setFinanceRecords((current) => current.map((item) => item.id === data.id ? data : item));
      setSavedFinanceRecords((current) => current.map((item) => item.id === data.id ? data : item));
      if (receiptBatchRef.current.active) {
        const batch = receiptBatchRef.current;
        const nextIndex = batch.index + 1;
        if (nextIndex >= batch.files.length) {
          batch.active = false;
          completedEvaluationEntries = [...batch.evaluationEntries];
          setReceiptBatchActive(false);
          setReceiptBatchStatus(`일괄 문서화 완료 · ${batch.files.length}/${batch.files.length}`);
        } else {
          batch.index = nextIndex;
          nextBatchFile = batch.files[nextIndex];
          setReceiptBatchStatus(`${nextIndex + 1}/${batch.files.length} · ${nextBatchFile.name} 처리 중`);
        }
      }
    } catch (requestError) {
      setError(requestError.response?.data?.detail || '재무 기록을 확정하지 못했습니다.');
    } finally {
      setLoading(false);
    }
    if (nextBatchFile) await runExtraction(nextBatchFile);
    if (completedEvaluationEntries?.length && evaluationDatasetFile) {
      setLoading(true);
      setEvaluationStatus(`일괄 문서화 완료 · ${completedEvaluationEntries.length}건 평가 데이터 생성 중...`);
      for (const entry of completedEvaluationEntries) {
        await evaluateReceiptForReport(entry.file, entry.result, entry.financeRecord);
      }
      setEvaluationStatus(`일괄 문서화 및 평가 완료 · ${completedEvaluationEntries.length}건이 리포트에 저장되었습니다.`);
      setReceiptBatchStatus(`일괄 문서화 및 평가 완료 · ${completedEvaluationEntries.length}/${completedEvaluationEntries.length}`);
      setLoading(false);
    }
  };

  const submitFinanceRecord = async () => {
    if (!financeRecord || financeRecord.status !== 'CONFIRMED') return;
    setLoading(true);
    setError('');
    try {
      const { data } = await apiClient.post(`/finance/records/${financeRecord.id}/submit`);
      setFinanceRecord(data);
      setFinanceRecords((current) => current.map((item) => item.id === data.id ? data : item));
      setSavedFinanceRecords((current) => current.map((item) => item.id === data.id ? data : item));
    } catch (requestError) {
      setError(requestError.response?.data?.detail || '재무팀에 문서를 보내지 못했습니다.');
    } finally {
      setLoading(false);
    }
  };
  return (
    <div className={`app-shell ocr-app-shell ${processingMode === 'receipt' ? 'finance-page-shell' : ''}`}>
      <Sidebar />
      {historyOpen && <button className="ocr-history-backdrop" type="button" aria-label="OCR 기록 닫기" onClick={() => setHistoryOpen(false)} />}
      <aside className={`ocr-history-drawer ${historyOpen ? 'open' : ''}`} aria-hidden={!historyOpen}>
        <div className="ocr-history-header">
          <div><strong>OCR 처리 기록</strong><small>재무 문서와 명세서 이력</small></div>
          <button type="button" aria-label="OCR 기록 닫기" onClick={() => setHistoryOpen(false)}><IoCloseOutline /></button>
        </div>
        <label className="ocr-history-search">
          <IoSearchOutline />
          <input value={historySearch} onChange={(event) => setHistorySearch(event.target.value)} placeholder="문서명 검색" />
        </label>
        <div className="ocr-history-list">
          {historyLoading ? <div className="ocr-history-empty">처리 기록을 불러오는 중입니다.</div> : historyError ? <div className="ocr-history-empty error">{historyError}<button type="button" onClick={loadOcrHistory}>다시 시도</button></div> : historyItems.filter((item) => item.file_name?.toLowerCase().includes(historySearch.trim().toLowerCase())).length ? historyItems.filter((item) => item.file_name?.toLowerCase().includes(historySearch.trim().toLowerCase())).map((item) => (
            <button key={item.id} type="button" className={`ocr-history-item ${currentDocumentId === item.id ? 'active' : ''}`} onClick={() => { setHistoryOpen(false); loadHistoryDocument(item.id); }} disabled={loading}>
              <span className="ocr-history-icon"><IoDocumentTextOutline /></span>
              <span><strong>{item.file_name}</strong><small>{new Date(item.created_at).toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' })}</small></span>
              <em>{item.status === 'COMPLETED' ? '완료' : item.status}</em>
            </button>
          )) : <div className="ocr-history-empty">{historySearch ? '검색 결과가 없습니다.' : '저장된 OCR 처리 기록이 없습니다.'}</div>}
        </div>
      </aside>
      <main className="ocr-workspace">
        <header className="ocr-header">
          <div className="header-title">
            {processingMode !== 'receipt' && <button className="history-menu-button" type="button" onClick={openHistory} aria-label="OCR 처리 기록 열기" aria-expanded={historyOpen}><IoMenuOutline /></button>}
            <button className="back-button" type="button" onClick={() => window.history.back()} aria-label="뒤로 가기">‹</button>
            <div><h1>{processingMode === 'receipt' ? '영수증 자동 문서화' : '재무 데이터 분석 및 검증'}</h1><p>{processingMode === 'receipt' ? '영수증 원본 · 재무 양식 · AI 검산을 한 화면에서 처리합니다' : 'OCR · 표 인식 · 정량 데이터 검증 워크스페이스'}</p></div>
          </div>
          <div className="ocr-header-actions">
            <span className="extract-method"><i /> {processingMode === 'receipt' ? '최종 확정은 사용자가 결정합니다' : '문서 원문과 추출 데이터 대조'}</span>
            {processingMode !== 'receipt' && isDeveloper && <button className="developer-jump-button" type="button" onClick={() => evaluationPanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>비교 텍스트 보기</button>}
            {processingMode !== 'receipt' && isDeveloper && <button className="developer-report-button" type="button" onClick={() => navigate('/reports')}>성능 리포트</button>}
            {processingMode !== 'receipt' && <button className="ocr-primary" onClick={() => inputRef.current?.click()}>파일 선택</button>}
          </div>
        </header>

        <input ref={inputRef} hidden type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.docx,.xlsx,.xlsm,.txt,.md,.csv" onChange={(e) => { const file = e.target.files?.[0]; handleReceiptFileSelection(file); e.target.value = ''; }} />

        <div className="ocr-filebar">
          <div className={`processing-mode auto ${processingMode}`}><span>자동 판별</span><strong>{processingMode === 'receipt' ? '영수증·거래 증빙' : '재무 문서'}</strong></div>
          <div className="file-identity">
            <span className="pdf-badge">{fileExtension}</span>
            <span><strong>{fileName}</strong><small>{hasResult ? `${pageCount}페이지 · 텍스트 추출 완료` : pendingFile ? '파일 준비 완료 · 추출 버튼을 눌러주세요' : 'PDF, 이미지, DOCX 및 텍스트 파일'}</small></span>
          </div>
          {processingMode !== 'receipt' && isDeveloper && pendingFile && <div className="developer-truth-upload">
            <span>개발자 정답 파일</span>
            {groundTruthFileName ? <><strong title={groundTruthFileName}>{groundTruthFileName}</strong><button type="button" className="remove-truth-button" onClick={() => { setGroundTruth(''); setGroundTruthFileName(''); setEvaluationStatus(''); }}>제거</button></> : <button type="button" className="truth-select-button" onClick={() => groundTruthFileRef.current?.click()}>TXT · JSON 선택</button>}
          </div>}
          <div className="filebar-actions">{pendingFile && <button className="extract-start-button" onClick={runExtraction} disabled={loading}>{loading ? '처리 중...' : processingMode !== 'receipt' && isDeveloper && groundTruth.trim() ? '추출 및 평가 시작' : 'OCR 텍스트 추출'}</button>}{(pendingFile || hasResult) && <button className="ghost-button" onClick={() => inputRef.current?.click()}>파일 변경</button>}</div>
        </div>

        <section className={`ocr-editor ${processingMode === 'receipt' ? 'receipt-workspace' : ''}`} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); handleReceiptFileSelection(e.dataTransfer.files?.[0]); }}>
          {processingMode !== 'receipt' && <aside className="pages-panel">
            <div className="panel-heading"><span>페이지</span><b>{pageCount}</b></div>
            <div className="thumb-list">
              {hasResult ? Array.from({ length: pageCount }, (_, index) => (
                <button key={index} className={`page-thumb ${pageNumber === index + 1 ? 'active' : ''}`} onClick={() => { setPageNumber(index + 1); setSelectedItemIndex(null); }}>
                  <span className="thumb-paper">{pdf ? <PdfCanvas pdf={pdf} pageNumber={index + 1} scale={0.22} thumbnail /> : imagePreviewUrl ? <img className="image-thumb" src={imagePreviewUrl} alt="업로드 이미지 미리보기" /> : <IoDocumentTextOutline />}</span>
                  <span>{sheetNames[index] || `${index + 1} 페이지`}</span>
                </button>
              )) : <div className="empty-pages">PDF를 업로드하면<br />페이지별로 표시됩니다.</div>}

            </div>
          </aside>}

          <div className="preview-panel">
            <div className="preview-toolbar">
              <div>
                <button disabled={!hasResult || pageNumber === 1} onClick={() => setPageNumber((p) => p - 1)} aria-label="이전 페이지">‹</button>
                <span>{hasResult ? `${pageNumber} / ${pageCount}` : '0 / 0'}</span>
                <button disabled={!hasResult || pageNumber === pageCount} onClick={() => setPageNumber((p) => p + 1)} aria-label="다음 페이지">›</button>
              </div>
              <strong>{generatedWorkbook ? '새 Excel 문서 미리보기' : processingMode === 'receipt' ? '1. 영수증 원본' : '원문 및 OCR 근거'}</strong>
              <div>
                {preprocessedImageUrl && <div className="preview-variant" role="group" aria-label="이미지 비교">
                  <button className={previewVariant === 'original' ? 'active' : ''} onClick={() => setPreviewVariant('original')}>원본</button>
                  <button className={previewVariant === 'processed' ? 'active' : ''} onClick={() => setPreviewVariant('processed')}>전처리</button>
                </div>}
                <button onClick={() => setZoom((z) => Math.max(0.55, z - 0.15))} aria-label="축소">−</button>
                <span>{Math.round(zoom * 100)}%</span>
                <button onClick={() => setZoom((z) => Math.min(2, z + 0.15))} aria-label="확대">＋</button>
              </div>
            </div>
            {processingMode === 'receipt' && <div className="receipt-upload-actions">
              <div><span className="pdf-badge">{fileExtension}</span><span><strong>{fileName}</strong><small>{hasResult ? 'OCR 추출 완료' : pendingFile ? '추출 준비 완료' : (isEnterprise && receiptBatchStatus) || '영수증 이미지 혹은 영수증 내용이 들어간 문서를 선택해주세요'}</small></span></div>
              <nav>{pendingFile && <button type="button" className="extract" onClick={runExtraction} disabled={loading}>{loading ? '처리 중...' : 'OCR 텍스트 추출'}</button>}{(isReceiptEvaluator || isEnterprise) && <button type="button" className="batch" disabled={loading || (isReceiptEvaluator && !evaluationDatasetFile)} onClick={() => evaluationFolderRef.current?.click()}>영수증 일괄 입력</button>}<button type="button" onClick={() => inputRef.current?.click()}>{pendingFile || hasResult ? '파일 변경' : '영수증 선택'}</button></nav>
              <nav className="batch-always-nav"><button type="button" className="batch batch-always" disabled={loading || receiptBatchActive} onClick={() => evaluationFolderRef.current?.click()}>영수증 일괄 입력</button></nav>
              <input ref={evaluationFolderRef} hidden type="file" accept=".png,.jpg,.jpeg,.webp,.bmp,.pdf" multiple webkitdirectory="true" directory="true" onChange={(event) => { openReceiptBatchEvaluation(event.target.files); event.target.value = ''; }} />
            </div>}
            {processingMode === 'receipt' && receiptBatchStatus && <div className="receipt-batch-status"><strong>{receiptBatchActive ? '일괄 문서화 진행 중' : '일괄 문서화'}</strong><span>{receiptBatchStatus}</span></div>}
            {processingMode === 'receipt' && isReceiptEvaluator && <div className="receipt-evaluation-transfer">
              <div><strong>평가 페이지에서 상세한 평가가 가능합니다</strong><small>{evaluationStatus || (evaluationDatasetFile ? `정답 데이터: ${evaluationDatasetFile.name} · OCR 추출 후 평가 결과가 자동으로 연결됩니다.` : '정답 JSON을 선택하면 모델별 정확도와 OCR 영향을 상세히 확인할 수 있습니다.')}</small></div>
              <nav><button type="button" onClick={() => evaluationDatasetRef.current?.click()}>{evaluationDatasetFile ? '다른 정답 데이터 입력' : '정답 데이터 입력'}</button></nav>
              <input ref={evaluationDatasetRef} hidden type="file" accept=".json,application/json" onChange={(event) => { const file = event.target.files?.[0] || null; setEvaluationDatasetFile(file); setEvaluationStatus(file ? `${file.name} 정답 데이터를 불러왔습니다.` : ''); setError(''); }} />
            </div>}
            {processingMode === 'receipt' && financeDuplicateNotice && <div className="receipt-duplicate-notice"><strong>중복 문서</strong><span>{financeDuplicateNotice}</span></div>}
            <div className="preview-stage">
              {generatedWorkbook ? <GeneratedWorkbookPreview title={generatedWorkbook.title} rows={generatedWorkbook.rows} onBack={() => setGeneratedWorkbook(null)} onDownload={() => { const anchor = document.createElement('a'); anchor.href = generatedWorkbook.url; anchor.download = generatedWorkbook.fileName; anchor.click(); }} /> : projectTransition ? <div className="loader"><span />새 프로젝트를 준비하고 있습니다...</div> : loading && !imagePreviewUrl ? <div className="loader"><span />파일을 분석하고 있습니다...</div> : pdf ? <PdfCanvas pdf={pdf} pageNumber={pageNumber} scale={zoom} items={currentItems} selectedItemIndex={selectedItemIndex} onSelectItem={setSelectedItemIndex} /> : currentRows ? <SpreadsheetPreview rows={currentRows} items={currentItems} selectedItemIndex={selectedItemIndex} onSelectItem={setSelectedItemIndex} /> : displayedImageUrl ? <ImagePreview src={displayedImageUrl} fileName={fileName} scale={zoom} items={previewVariant === 'processed' ? [] : currentItems} selectedItemIndex={selectedItemIndex} onSelectItem={setSelectedItemIndex} loading={loading} /> : hasResult ? (
                <div className="loader">OCR 텍스트 추출이 완료되었습니다.</div>
              ) : pendingFile ? (
                <div className="pending-document" role="status">
                  <span className="pending-document-icon"><IoDocumentTextOutline /></span>
                  <strong>{pendingFile.name}</strong>
                  <small>{(pendingFile.size / 1024).toLocaleString('ko-KR', { maximumFractionDigits: 1 })} KB</small>
                  <p>{fileExtension} 파일 준비 완료</p>
                  <button type="button" onClick={runExtraction} disabled={loading}>OCR 텍스트 추출</button>
                </div>
              ) : (
                <button className="dropzone" onClick={() => inputRef.current?.click()}>
                  <span className="drop-icon">⇧</span><strong>{processingMode === 'receipt' ? '영수증 업로드' : 'PDF를 여기에 놓아주세요'}</strong><small>{processingMode === 'receipt' ? '사진 또는 PDF를 놓거나 클릭해서 선택하세요' : '또는 클릭해서 파일을 선택하세요'}</small>
                </button>
              )}
              {error && <div className="ocr-error">{error}</div>}
            </div>
            {processingMode === 'receipt' && <section className="receipt-raw-result">
              <header><div><strong>텍스트 추출 결과</strong><small>영수증 OCR 원문</small></div><span>{currentItems.length}개 항목</span></header>
              <div>{currentItems.length ? currentItems.map((item, index) => <button key={`${index}-${item.text}`} type="button" className={selectedItemIndex === index ? 'selected' : ''} onClick={() => setSelectedItemIndex(index)}>{item.text}</button>) : <p>영수증을 업로드하면 추출된 텍스트가 여기에 표시됩니다.</p>}</div>
            </section>}
          </div>

          <aside className="text-panel">
            <div className="text-tabs">
              <button className={resultTab === 'text' ? 'active' : ''} onClick={() => setResultTab('text')}>{processingMode === 'receipt' ? '2. 문서화 · 행 추가' : financeRecord ? '2. 재무 양식' : '문서 추출 워크시트'}</button>
              {processingMode !== 'receipt' && <button className={resultTab === 'raw' ? 'active' : ''} onClick={() => setResultTab('raw')}>OCR 원문</button>}
              {processingMode === 'receipt' && <button type="button" className={`saved-finance-trigger ${savedFinanceOpen ? 'open' : ''}`} onClick={() => { setSavedFinanceOpen((open) => !open); if (!savedFinanceOpen) loadSavedFinanceRecords(); }}>저장된 기록 <b>{savedFinanceGroups.length}</b></button>}
            </div>
            {processingMode === 'receipt' && savedFinanceOpen && <section className="saved-finance-panel">
              <header><div><strong>저장된 재무 기록</strong><small>기존 문서를 선택해 이어서 작업할 수 있습니다.</small></div><button type="button" aria-label="닫기" onClick={() => setSavedFinanceOpen(false)}><IoCloseOutline /></button></header>
              <label><IoSearchOutline /><input value={savedFinanceSearch} onChange={(event) => setSavedFinanceSearch(event.target.value)} placeholder="문서 유형 또는 작성 월 검색" /></label>
              <div className="saved-finance-list">
                {savedFinanceLoading ? <p>저장된 기록을 불러오는 중입니다.</p> : savedFinanceGroups.filter((group) => `${group.month} ${FINANCE_DOCUMENTS[group.documentType]?.title || group.documentType}`.toLowerCase().includes(savedFinanceSearch.trim().toLowerCase())).map((group) => <button type="button" key={group.key} onClick={() => { setFinanceRecords(group.records); setFinanceRecord(group.records[group.records.length - 1]); setResultTab('text'); setSavedFinanceOpen(false); }}>
                  <span className="saved-finance-file">XLSX</span><span><strong>{group.month.replace('-', '년 ')}월 {FINANCE_DOCUMENTS[group.documentType]?.title || '재무 문서'}</strong><small>{group.records.length}개 행 · 누적 {financeMoney(group.total)}</small></span><em className={`status-${group.status.replaceAll(' ', '-')}`}>{group.status}</em><i>›</i>
                </button>)}
                {!savedFinanceLoading && !savedFinanceGroups.length && <p>저장된 재무 기록이 아직 없습니다.</p>}
              </div>
            </section>}
            <div className="text-header">
              <div><span>{resultTab === 'text' ? (financeRecord ? FINANCE_DOCUMENTS[financeRecord.document_type]?.title : processingMode === 'receipt' ? '재무 양식 작성 영역' : 'Excel형 문서 추출 워크시트') : 'OCR 원문 데이터'}</span><small>{financeRecord ? `현재 영수증 · ${financeRecordRows(currentFinanceRecords).length}개 품목 행` : processingMode === 'receipt' ? '영수증 분석 대기' : hasResult ? `${pageNumber} 페이지 · ${validationRows.length}행` : '대기 중'}</small></div>
              {resultTab === 'text' ? (financeRecord ? <div className="worksheet-actions"><span>재무 기록 저장 완료</span><button className="create-document-button" disabled={exportingRows} onClick={downloadFinanceDocument}>{exportingRows ? '문서 생성 중...' : '재무 양식 Excel 받기'}</button></div> : processingMode !== 'receipt' ? <div className="worksheet-actions"><span>{selectedRowIds.length}행 선택</span><button className="create-document-button" disabled={!selectedRowIds.length || exportingRows} onClick={createExcelDocument}>{exportingRows ? '문서 생성 중...' : '새 Excel 문서 만들기'}</button></div> : null) : <button disabled={!hasResult} onClick={downloadText} title="텍스트 다운로드">⇩</button>}
            </div>
            <div className="text-meta"><span>{currentText.length.toLocaleString()}자</span><span>{resultTab === 'text' ? (financeRecord ? 'OCR 선별값' : '드래그 행 선택') : '텍스트 레이어'}</span></div>
            {preprocessingInfo && <div className="receipt-preprocess-status"><strong>영수증 전처리 완료</strong><span>{(preprocessingInfo.applied_steps || []).map((step) => ({ perspective_correction: '원근', deskew: '기울기', crop: '여백', upscale: '확대', illumination_correction: '조명', contrast_enhancement: '대비', closing: '획 연결', sharpen: '선명화' }[step] || step)).join(' · ')}</span></div>}
            {resultTab === 'text' ? (financeRecord ? <FinanceReceiptWorksheet records={currentFinanceRecords} user={user} /> : processingMode === 'receipt' ? <div className="receipt-document-empty"><span>＋</span><strong>선별값이 들어갈 재무 문서</strong><p>영수증을 분석하면 날짜, 상호, 카테고리와 금액을 선별해<br />해당 재무 양식의 새로운 행으로 자동 추가합니다.</p><div><b>01</b> 영수증 인식 <i>→</i><b>02</b> 값 선별 <i>→</i><b>03</b> 행 추가</div></div> : <ExtractionWorksheet rows={validationRows} onChange={setValidationRows} selectedIds={selectedRowIds} onSelectRange={setSelectedRowIds} onEvidence={(itemIndex) => { setSelectedItemIndex(itemIndex); document.querySelector('.preview-panel')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }} />) : <div className={`extracted-copy ${!hasResult ? 'placeholder' : ''}`}>{hasResult ? (currentItems.length ? currentItems.map((item, index) => <button key={`${index}-${item.text}`} type="button" className={`extracted-line ${selectedItemIndex === index ? 'selected' : ''}`} onClick={() => setSelectedItemIndex(index)}>{item.text}</button>) : (currentText || '현재 페이지에는 추출 가능한 텍스트가 없습니다.')) : '파일을 업로드하면 페이지별 OCR 원문이 표시됩니다.'}</div>}
            <div className="text-note"><b>i</b><p>{resultTab === 'text' ? (financeRecord ? '영수증에서 선별한 값이 재무 양식의 새로운 행으로 저장되었습니다.' : '행 번호를 누른 채 위아래로 드래그해 범위를 선택하고, 새 Excel 문서 만들기를 누르세요.') : 'OCR 원문을 선택하면 오른쪽 원본의 해당 근거 영역이 강조됩니다.'}</p></div>
          </aside>
          {processingMode === 'receipt' && <aside className="receipt-agent-panel">
            <header><span>AI FINANCE AGENT</span><h2>3. AI 재무 에이전트</h2><p>자동 추출 결과를 검산하고 최종 결정을 사용자에게 맡깁니다.</p></header>
            <div className="agent-progress">
              <article className={hasResult ? 'done' : ''}><i>{hasResult ? '✓' : '1'}</i><span><strong>OCR 텍스트 추출</strong><small>{hasResult ? '완료' : '영수증을 등록해 주세요'}</small></span></article>
              <article className={financeRecord ? 'done' : ''}><i>{financeRecord ? '✓' : '2'}</i><span><strong>문서 유형·항목 선별</strong><small>{financeRecord ? FINANCE_DOCUMENTS[financeRecord.document_type]?.title : '분석 대기'}</small></span></article>
              <article className={financeRecord && Number(financeRecord.total_amount || 0) > 0 && Number(financeRecord.supply_amount || 0) + Number(financeRecord.tax_amount || 0) === Number(financeRecord.total_amount || 0) ? 'done' : 'warning'}><i>3</i><span><strong>금액 자동 검산</strong><small>{financeRecord ? `${financeMoney(financeRecord.supply_amount)} + ${financeMoney(financeRecord.tax_amount)} = ${financeMoney(financeRecord.total_amount)}` : '분석 대기'}</small></span></article>
            </div>
            {financeRecord ? <div className="agent-decision">
              <span className={`agent-status ${financeRecord.status === 'CONFIRMED' ? 'confirmed' : ''}`}>{financeRecord.status === 'CONFIRMED' ? '사용자 확정 완료' : '사용자 확인 필요'}</span>
              <dl><div><dt>분류</dt><dd>{financeRecord.expense_category}</dd></div><div><dt>상호</dt><dd>{financeRecord.merchant || '확인 필요'}</dd></div><div><dt>일자</dt><dd>{financeRecord.transaction_date || '확인 필요'}</dd></div><div><dt>금액</dt><dd>{financeMoney(financeRecord.total_amount)}</dd></div></dl>
              <button type="button" className="agent-review" onClick={openFinanceReview}>내용 검토·수정</button>
              <button type="button" className="agent-confirm" disabled={loading || financeRecord.status === 'CONFIRMED'} onClick={confirmFinanceRecord}>{financeRecord.status === 'CONFIRMED' ? '사용자 확정 완료' : '이 내용으로 최종 확정'}</button>
              {financeRecord.status === 'CONFIRMED' && <button type="button" className="agent-submit" disabled={loading || Boolean(financeRecord.structured_data?.finance_workflow?.submitted_at)} onClick={submitFinanceRecord}>{financeRecord.structured_data?.finance_workflow?.submitted_at ? '재무팀 전달 완료' : '완성 문서를 재무팀에 보내기'}</button>}
              <p>{financeRecord.structured_data?.finance_workflow?.submitted_at ? '전달된 문서는 마이페이지 재무 히스토리에서 확인할 수 있습니다.' : '확정 전에는 수정할 수 있으며, 사용자가 확정한 뒤 재무팀에 전달합니다.'}</p>
            </div> : <div className="agent-empty"><strong>분석할 영수증을 기다리고 있습니다.</strong><p>왼쪽에 영수증을 넣고 OCR 텍스트 추출을 실행하세요.</p></div>}
          </aside>}
        </section>
        {financeReviewOpen && financeReviewDraft && <div className="finance-review-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !loading) setFinanceReviewOpen(false); }}>
          <section className="finance-review-modal" role="dialog" aria-modal="true" aria-labelledby="finance-review-title">
            <header><div><span>AI FINANCE AGENT</span><h2 id="finance-review-title">추출 정보 검토·수정</h2><p>AI가 선별한 값을 확인하고 필요한 항목만 수정해주세요.</p></div><button type="button" aria-label="닫기" disabled={loading} onClick={() => setFinanceReviewOpen(false)}><IoCloseOutline /></button></header>
            <form onSubmit={saveFinanceReview}>
              <div className="finance-review-grid">
                <label><span>문서 유형</span><select required value={financeReviewDraft.document_type || ''} onChange={(event) => setFinanceReviewDraft((draft) => ({ ...draft, document_type: event.target.value, expense_category: '' }))}><option value="" disabled>선택</option>{Object.entries(FINANCE_DOCUMENTS).filter(([value]) => !financeTaxonomy.document_types.length || financeTaxonomy.document_types.includes(value)).map(([value, definition]) => <option key={value} value={value}>{definition.title}</option>)}</select></label>
                <label><span>카테고리</span><select required value={financeReviewDraft.expense_category || ''} onChange={(event) => setFinanceReviewDraft((draft) => ({ ...draft, expense_category: event.target.value }))}><option value="" disabled>선택</option>{financeTaxonomy.expense_categories.filter((category) => !financeReviewDraft.document_type || financeTaxonomy.category_to_document_type[category] === financeReviewDraft.document_type).map((category) => <option key={category} value={category}>{category}</option>)}</select></label>
                <label><span>상호(가맹점)</span><input maxLength="200" value={financeReviewDraft.merchant} onChange={(event) => setFinanceReviewDraft((draft) => ({ ...draft, merchant: event.target.value }))} /></label>
                <label><span>결제일</span><input type="date" value={financeReviewDraft.transaction_date} onChange={(event) => setFinanceReviewDraft((draft) => ({ ...draft, transaction_date: event.target.value }))} /></label>
                <label><span>공급가액</span><input type="number" min="0" value={financeReviewDraft.supply_amount} onChange={(event) => setFinanceReviewDraft((draft) => ({ ...draft, supply_amount: event.target.value }))} /></label>
                <label><span>부가세</span><input type="number" min="0" value={financeReviewDraft.tax_amount} onChange={(event) => setFinanceReviewDraft((draft) => ({ ...draft, tax_amount: event.target.value }))} /></label>
                <label><span>합계금액</span><input type="number" min="0" value={financeReviewDraft.total_amount} onChange={(event) => setFinanceReviewDraft((draft) => ({ ...draft, total_amount: event.target.value }))} /></label>
                <label><span>결제수단</span><input maxLength="100" value={financeReviewDraft.payment_method} onChange={(event) => setFinanceReviewDraft((draft) => ({ ...draft, payment_method: event.target.value }))} /></label>
                <label className="wide"><span>적요·사용 내역</span><textarea maxLength="1000" value={financeReviewDraft.description} onChange={(event) => setFinanceReviewDraft((draft) => ({ ...draft, description: event.target.value }))} /></label>
              </div>
              <div className="finance-review-check"><strong>금액 검산</strong><span>{financeMoney(financeReviewDraft.supply_amount)} + {financeMoney(financeReviewDraft.tax_amount)} = {financeMoney(Number(financeReviewDraft.supply_amount || 0) + Number(financeReviewDraft.tax_amount || 0))}</span><em className={Number(financeReviewDraft.total_amount || 0) === Number(financeReviewDraft.supply_amount || 0) + Number(financeReviewDraft.tax_amount || 0) ? 'valid' : ''}>{Number(financeReviewDraft.total_amount || 0) === Number(financeReviewDraft.supply_amount || 0) + Number(financeReviewDraft.tax_amount || 0) ? '일치' : '합계 확인 필요'}</em></div>
              <footer><button type="button" disabled={loading} onClick={() => setFinanceReviewOpen(false)}>취소</button><button type="submit" className="save" disabled={loading}>{loading ? '저장 중...' : '수정 내용 저장'}</button></footer>
            </form>
          </section>
        </div>}
        {processingMode !== 'receipt' && isDeveloper && <section ref={evaluationPanelRef} className={`developer-evaluation-panel ${hasResult ? '' : 'waiting'}`}>
          <header>
            <div><span>DEVELOPER ONLY</span><h2>OCR 정답 데이터 평가</h2><p>OCR 결과와 사람이 검수한 정답을 비교해 Precision, Recall, F1 Score를 계산합니다.</p></div>
            <div className="developer-evaluation-meta"><small>추출 처리 시간</small><strong>{processingTimeMs ? `${(processingTimeMs / 1000).toFixed(2)}초` : '측정 대기'}</strong></div>
          </header>
          <div className="developer-evaluation-body">
            <div className="developer-extracted-summary"><div><strong>OCR 추출 결과</strong><span>{pageTexts.join('\n\n').length.toLocaleString()}자 · {pageCount}페이지</span></div><pre>{hasResult ? pageTexts.join('\n\n').slice(0, 1800) : '문서를 업로드하고 OCR 추출을 완료하면 결과가 표시됩니다.'}</pre></div>
            <label><div><strong>정답 데이터 (Ground Truth)</strong><span className="ground-truth-tools"><em>TXT · JSON</em><button type="button" disabled={!hasResult} onClick={() => groundTruthFileRef.current?.click()}>파일 불러오기</button></span></div><textarea disabled={!hasResult} value={groundTruth} onChange={(event) => { setGroundTruth(event.target.value); setEvaluationStatus(''); }} placeholder="직접 입력하거나 TXT 또는 JSON 정답 파일을 불러오세요." /><input ref={groundTruthFileRef} hidden type="file" accept=".txt,.json,text/plain,application/json" onChange={(event) => { loadGroundTruthFile(event.target.files?.[0]); event.target.value = ''; }} /></label>
          </div>
          <footer><span className={evaluationStatus.includes('저장되었습니다') ? 'success' : ''}>{evaluationStatus || (hasResult ? '정답 데이터를 입력하면 평가 결과가 개발자 리포트에 저장됩니다.' : '먼저 문서를 업로드해 주세요.')}</span><div><button className="open-report-button" type="button" onClick={() => navigate('/reports')}>리포트 열기</button><button disabled={!hasResult || !groundTruth.trim() || !currentDocumentId || evaluationStatus === '저장 중...'} onClick={saveDeveloperEvaluation}>정답 저장 및 성능 평가</button></div></footer>
        </section>}
      </main>
    </div>
  );
}
