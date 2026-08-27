import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { IoDownloadOutline } from 'react-icons/io5';

import apiClient from '../api/client';
import Sidebar from '../components/Sidebar';
import { clearFinanceEvaluationRuns, readFinanceEvaluationRuns, saveFinanceEvaluationRuns } from '../features/financeEvaluationStorage';
import { FINANCE_EVALUATION_INPUT_QUEUED, clearFinanceEvaluationInput, peekFinanceEvaluationInput } from '../features/financeEvaluationTransfer';
import { clearPendingReceipts, readReceiptWorkspace, rememberReceiptRecord } from '../features/receiptWorkspaceMemory';
import '../style/FinanceEvaluationPage.scss';

const LABELS = {
  document_type: '문서 유형', expense_category: '카테고리', merchant: '상호', transaction_date: '날짜',
  supply_amount: '공급가액', tax_amount: '부가세', total_amount: '합계금액', payment_method: '결제수단',
  total_quantity: '총 물품 수량', discount_amount: '할인액', card_number: '카드번호',
  evaluation_status: '평가 상태',
};
const IMPACT_LABELS = {
  SUCCESS: '정상 추출', LIKELY_OCR_ERROR: 'OCR 영향 가능',
  LIKELY_LLM_ERROR: 'LLM 해석 오류 가능', LLM_RECOVERY: 'LLM 보정 가능',
};
const OCR_PREVIEW_CONTEXT = new WeakMap();

function impactLabel(field) {
  const itemMatch = /^items\[(\d+)]\.(.+)$/.exec(field);
  if (!itemMatch) return LABELS[field] || field;
  const itemLabels = { name: '상품명', quantity: '수량', unit_price: '단가', total_amount: '품목 금액' };
  return `${Number(itemMatch[1]) + 1}번 품목 ${itemLabels[itemMatch[2]] || itemMatch[2]}`;
}

function flattenedMatches(score) {
  const matches = [];
  Object.entries(score?.fields || {}).forEach(([field, detail]) => {
    if (field !== 'items') {
      matches.push({ field, label: LABELS[field] || field, correct: Boolean(detail.correct), actual: detail.actual, expected: detail.expected });
      return;
    }
    matches.push({ field: 'items.count', label: '품목 수', correct: Boolean(detail.count_correct), actual: detail.actual_count, expected: detail.expected_count });
    (detail.items || []).forEach((item) => Object.entries(item.fields || {}).forEach(([itemField, itemDetail]) => matches.push({
      field: `items[${item.index}].${itemField}`,
      label: impactLabel(`items[${item.index}].${itemField}`),
      correct: Boolean(itemDetail.correct),
      actual: itemDetail.actual,
      expected: itemDetail.expected,
    })));
  });
  return matches;
}

function OcrImpact({ impact }) {
  if (!impact) return <p className="impact-notice">이전 평가 결과에는 OCR 영향 추정값이 없습니다.</p>;
  const counts = impact.counts || {};
  return <section className="ocr-impact"><header><strong>OCR 영향 추정</strong><span>OCR 근거 발견률 {(Number(impact.ocr_evidence_rate || 0) * 100).toFixed(1)}%</span></header>
    <p className="impact-notice">{impact.notice}</p>
    <div className="impact-summary"><span className="success">정상 {counts.SUCCESS || 0}</span><span className="ocr-error">OCR 영향 {counts.LIKELY_OCR_ERROR || 0}</span><span className="llm-error">LLM 오류 {counts.LIKELY_LLM_ERROR || 0}</span><span className="recovery">LLM 보정 {counts.LLM_RECOVERY || 0}</span></div>
    <div className="impact-fields">{(impact.fields || []).map((item) => <p className={`impact-${item.status.toLowerCase().replaceAll('_', '-')}`} key={item.field}><b>{impactLabel(item.field)}</b><span>{IMPACT_LABELS[item.status] || item.status}</span><em>{item.ocr_evidence_found ? 'OCR 근거 있음' : 'OCR 근거 없음'}</em></p>)}</div>
  </section>;
}

function ExcelMiniPreview({ workbook }) {
  const sheetPreviews = workbook?.sheet_previews || {};
  const sheetNames = Object.keys(sheetPreviews);
  const initialSheet = workbook?.active_sheet || sheetNames[0] || '';
  const [selectedSheet, setSelectedSheet] = useState(initialSheet);
  useEffect(() => { setSelectedSheet(initialSheet); }, [initialSheet]);
  const preview = sheetPreviews[selectedSheet] || workbook?.preview;
  if (!preview) return <div className="eval-preview-empty">Excel 미리보기가 없습니다.</div>;
  return <div className="excel-mini"><div className="excel-mini-title"><div className="excel-sheet-tabs">{sheetNames.length ? sheetNames.map((sheetName) => <button className={sheetName === selectedSheet ? 'active' : ''} type="button" key={sheetName} onClick={() => setSelectedSheet(sheetName)}>{sheetName}</button>) : <strong>{workbook.active_sheet}</strong>}</div><span>{workbook.success ? '생성 정상' : '생성 실패'}</span></div><div className="excel-mini-scroll"><table><thead><tr>{(preview.headers || []).map((header, index) => <th key={`${header}-${index}`}>{header}</th>)}</tr></thead><tbody>{(preview.rows || []).map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, columnIndex) => <td key={columnIndex}>{String(cell ?? '')}</td>)}</tr>)}</tbody></table></div></div>;
}

function columnLabel(index) {
  let value = index + 1; let label = '';
  while (value > 0) { value -= 1; label = String.fromCharCode(65 + (value % 26)) + label; value = Math.floor(value / 26); }
  return label;
}

function buildOcrGrid(pages, fallbackText) {
  const page = Array.isArray(pages) ? pages[0] : null;
  if (Array.isArray(page?.rows) && page.rows.length) return page.rows.map((row) => row.map((cell) => String(cell ?? '')));
  const positioned = (page?.items || []).map((item) => {
    const points = Array.isArray(item.bbox) ? item.bbox : [];
    const xs = points.map((point) => Number(point?.[0])).filter(Number.isFinite);
    const ys = points.map((point) => Number(point?.[1])).filter(Number.isFinite);
    if (!xs.length || !ys.length || !String(item.text || '').trim()) return null;
    const x0 = Math.min(...xs); const y0 = Math.min(...ys); const y1 = Math.max(...ys);
    return { text: String(item.text).trim(), x0, y0, y1, cy: (y0 + y1) / 2, height: Math.max(y1 - y0, 1) };
  }).filter(Boolean).sort((a, b) => a.cy - b.cy || a.x0 - b.x0);
  if (!positioned.length) return String(fallbackText || '').split(/\r?\n/).filter(Boolean).map((line) => [line]);
  const lines = [];
  positioned.forEach((item) => {
    let line = lines.find((candidate) => {
      const overlap = Math.min(candidate.y1, item.y1) - Math.max(candidate.y0, item.y0);
      return overlap >= Math.min(candidate.height, item.height) * .35 || Math.abs(candidate.cy - item.cy) <= Math.max(candidate.height, item.height) * .58;
    });
    if (!line) { line = { items: [], y0: item.y0, y1: item.y1, cy: item.cy, height: item.height }; lines.push(line); }
    line.items.push(item); line.y0 = Math.min(line.y0, item.y0); line.y1 = Math.max(line.y1, item.y1);
    line.cy = line.items.reduce((sum, value) => sum + value.cy, 0) / line.items.length; line.height = Math.max(line.y1 - line.y0, 1);
  });
  lines.forEach((line) => line.items.sort((a, b) => a.x0 - b.x0)); lines.sort((a, b) => a.cy - b.cy);
  const anchors = [];
  lines.filter((line) => line.items.length >= 2).flatMap((line) => line.items).forEach((item) => {
    if (!anchors.some((anchor) => Math.abs(anchor - item.x0) <= 14)) anchors.push(item.x0);
  });
  anchors.sort((a, b) => a - b);
  const columns = anchors.length >= 2 ? anchors.slice(0, 12) : [Math.min(...positioned.map((item) => item.x0))];
  return lines.slice(0, 500).map((line) => {
    const cells = Array.from({ length: line.items.length >= 2 ? columns.length : 1 }, () => '');
    line.items.forEach((item) => { const index = line.items.length < 2 ? 0 : columns.reduce((best, anchor, candidate) => Math.abs(anchor - item.x0) < Math.abs(columns[best] - item.x0) ? candidate : best, 0); cells[index] = cells[index] ? `${cells[index]} ${item.text}` : item.text; });
    return cells;
  });
}

function receiptTableRows(pages) {
  return (Array.isArray(pages) ? pages : []).flatMap((page) => (page?.tables || []).flatMap((table, tableIndex) =>
    (table?.rows || []).map((row, rowIndex) => ({
      page: page.page || 1, table: tableIndex + 1, row: rowIndex + 1,
      cells: (row || []).map((cell) => String(cell ?? '').trim()),
    }))));
}

const ERROR_CATEGORY_LABELS = {
  OCR_ERROR: 'OCR 오류', CANDIDATE_ERROR: '후보 생성 오류', LLM_ERROR: 'LLM 오류',
  VALIDATION_ERROR: '검증 오류', NORMALIZATION_ERROR: '정규화 오류', UNKNOWN: '원인 미확정',
};
const ERROR_CATEGORY_COLORS = {
  OCR_ERROR: '#e64949', CANDIDATE_ERROR: '#f59e0b', LLM_ERROR: '#5b5ce2',
  VALIDATION_ERROR: '#15966f', NORMALIZATION_ERROR: '#0e9fbb', UNKNOWN: '#8a97a8',
};

function tagsForMismatch(field, tags) {
  const itemMatch = /^items\[(\d+)]\.(.+)$/.exec(field);
  if (itemMatch) {
    const scope = `items[${itemMatch[1]}]`;
    return tags.filter((tag) => tag.scope === scope && (!tag.field || tag.field === itemMatch[2]));
  }
  if (field === 'items.count') {
    return tags.filter((tag) => ['ITEM_MISSING', 'EXTRA_ITEM', 'DUPLICATE_ITEM', 'SUMMARY_ITEM_INCONSISTENCY'].includes(tag.code));
  }
  return tags.filter((tag) => tag.field === field);
}

function ErrorTag({ tag }) {
  const reviewLabel = tag.decision === 'AUTO' ? `${Math.round(Number(tag.confidence || 0) * 100)}%` : tag.decision === 'NEEDS_REVIEW' ? '검토 필요' : '원인 미확정';
  return <span className={`error-tag error-tag-${String(tag.category || 'UNKNOWN').toLowerCase()}`} title={tag.message || tag.code}>
    <b>{ERROR_CATEGORY_LABELS[tag.category] || tag.category}</b><code>{tag.code}</code><em>{reviewLabel}</em>
  </span>;
}

function displayEvaluationValue(value) {
  if (value == null || value === '') return '-';
  if (typeof value === 'object') return JSON.stringify(value, null, 0);
  return String(value);
}

function BatchEvaluationProgress({ progress }) {
  const percent = progress.total ? Math.round(progress.completed / progress.total * 100) : 0;
  return <div className="batch-evaluation-progress" role="progressbar" aria-label="일괄 평가 진행률" aria-valuemin="0" aria-valuemax={progress.total} aria-valuenow={progress.completed}>
    <div><strong>{progress.running ? '일괄 평가 진행 중' : '일괄 평가 완료'}</strong><span>{progress.completed} / {progress.total}단계 · {percent}%</span></div>
    <i><b style={{ width: `${percent}%` }} /></i>
    <small>{progress.stage === 'finalizing' ? '평가 결과를 DB에 저장하고 최종 집계하는 중입니다.' : progress.running ? `현재 처리: ${progress.currentFile || '준비 중'}` : `${progress.imageTotal}개 이미지 평가와 최종 집계가 완료되었습니다.`}</small>
  </div>;
}

function normalizedEvidence(value) {
  return String(value || '').replace(/[^0-9A-Za-z가-힣]/g, '').toLowerCase();
}

const RESOLUTION_LABELS = {
  arithmetic: '수량 × 단가 산술 관계로 열 역할 결정', header: '표 머리글 위치로 열 역할 결정',
  plausibility: '값의 범위와 조합 가능성으로 결정', item_block: '여러 OCR 행을 하나의 품목 블록으로 결합',
  single_amount_default_quantity: '금액 하나만 인식되어 수량을 1로 보정',
  discount_arithmetic: '할인 전후 금액의 산술 관계로 결정', ambiguous: '열 역할을 확정하지 못함',
};

function candidateLocations(candidate, pages) {
  const needles = (candidate?.raw_cells || []).map(normalizedEvidence).filter(Boolean);
  if (!needles.length) return [];
  return (Array.isArray(pages) ? pages : []).flatMap((page, pageIndex) => (page?.items || []).flatMap((item, itemIndex) => {
    const evidence = normalizedEvidence(item?.text);
    if (!evidence || !needles.some((needle) => needle.includes(evidence) || evidence.includes(needle))) return [];
    const points = Array.isArray(item?.bbox) ? item.bbox : [];
    const xs = points.map((point) => Number(point?.[0])).filter(Number.isFinite);
    const ys = points.map((point) => Number(point?.[1])).filter(Number.isFinite);
    return [{ page: page?.page || pageIndex + 1, item: itemIndex + 1, text: item.text, bbox: xs.length && ys.length ? [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] : null, confidence: item.confidence ?? item.score ?? null }];
  })).slice(0, 12);
}

function CandidateTrace({ candidate, index, pages, predicted, view }) {
  const locations = candidateLocations(candidate, pages);
  const quantity = Number(candidate?.quantity_candidate);
  const unitPrice = Number(candidate?.unit_price_candidate);
  const amount = Number(candidate?.amount_candidate);
  const calculable = Number.isFinite(quantity) && Number.isFinite(unitPrice) && Number.isFinite(amount);
  const calculated = calculable ? quantity * unitPrice : null;
  const rawName = candidate?.raw_name_candidate || candidate?.raw_cells?.[0] || '-';
  const normalizedName = candidate?.name_candidate || '-';
  const changes = [];
  if (String(rawName) !== String(normalizedName)) changes.push(`품목명 정리: “${rawName}” → “${normalizedName}”`);
  if (candidate?.column_resolution) changes.push(RESOLUTION_LABELS[candidate.column_resolution] || `열 판정 규칙: ${candidate.column_resolution}`);
  (candidate?.uncertainty || []).forEach((item) => changes.push(`불확실성 감지: ${item}`));
  return <article className="candidate-trace-card">
    <header><div><strong>품목 {index + 1} · {normalizedName}</strong><span>{candidate?.uncertainty?.length ? '검토 필요' : '자동 판정'}</span></div><small>발생 단계: {candidate?.uncertainty?.length ? '후보 생성·열 판정' : 'OCR 후처리·구조화'}</small></header>
    {view === 'flow' && <div className="candidate-trace-flow"><section><small>OCR 원문</small><code>{(candidate?.raw_cells || []).join(' | ') || '-'}</code></section><b>→</b><section><small>정규화·후보</small><code>{normalizedName} / {quantity || '-'} / {unitPrice || '-'} / {amount || '-'}</code></section><b>→</b><section><small>최종 구조화</small><code>{predicted ? `${predicted.name ?? '-'} / ${predicted.quantity ?? '-'} / ${predicted.unit_price ?? '-'} / ${predicted.total_amount ?? '-'}` : '미제공'}</code></section></div>}
    {view === 'calculation' && <div className="candidate-trace-grid single"><section><h4>수량·단가·금액 검증식</h4><p className={calculable && calculated !== amount ? 'trace-error' : 'trace-ok'}>{calculable ? `${quantity.toLocaleString()} × ${unitPrice.toLocaleString()} = ${calculated.toLocaleString()} / 인식 금액 ${amount.toLocaleString()}${calculated === amount ? ' · 일치' : ` · ${Math.abs(calculated - amount).toLocaleString()} 차이`}` : '계산에 필요한 수량·단가·금액 일부가 없습니다.'}</p></section></div>}
    {view === 'rules' && <div className="candidate-trace-grid"><section><h4>적용된 보정·판정</h4>{changes.length ? <ul>{changes.map((change) => <li key={change}>{change}</li>)}</ul> : <p>명시적으로 기록된 보정 내역이 없습니다.</p>}</section><section><h4>오류 및 불확실성</h4><p>{candidate?.uncertainty?.length ? candidate.uncertainty.join(', ') : '감지된 불확실성이 없습니다.'}</p></section></div>}
    {view === 'location' && <div className="candidate-trace-grid"><section><h4>원본 위치 · OCR 신뢰도</h4>{locations.length ? <ul>{locations.map((location) => <li key={`${location.page}-${location.item}`}>P{location.page} #{location.item} · {location.bbox ? `bbox [${location.bbox.map(Math.round).join(', ')}]` : '좌표 미제공'} · 신뢰도 {location.confidence == null ? '미제공' : `${Math.round(Number(location.confidence) * 100)}%`}</li>)}</ul> : <p>후보와 연결되는 OCR 박스 위치가 없습니다.</p>}</section><section><h4>원본 행·결합 정보</h4><p>{candidate?.source || candidate?.row_source || candidate?.column_resolution ? `출처: ${candidate.source || candidate.row_source || candidate.column_resolution}` : '원본 행 번호와 결합 이력은 현재 응답에 미제공'}</p></section></div>}
  </article>;
}

function OcrSheetPreview({ pages, text, diagnostics, prediction, truth }) {
  const previewContext = Array.isArray(pages) ? OCR_PREVIEW_CONTEXT.get(pages) : null;
  diagnostics ||= previewContext?.diagnostics;
  prediction ||= previewContext?.prediction;
  truth ||= previewContext?.truth;
  const [selectedView, setSelectedView] = useState('raw');
  const rows = useMemo(() => buildOcrGrid(pages, text), [pages, text]);
  const columnCount = Math.max(1, ...rows.map((row) => row.length));
  const candidates = diagnostics?.candidates || [];
  const predictedItems = Array.isArray(prediction?.items) ? prediction.items : [];
  const tabs = [['raw', '원문 배치'], ['flow', '변환 흐름'], ['calculation', '산술 검증'], ['rules', '보정·판정'], ['location', '원본 위치']];
  return <div className="ocr-sheet-mini ocr-structure-preview">
    <div className="ocr-view-tabs">{tabs.map(([key, label]) => <button className={selectedView === key ? 'active' : ''} type="button" key={key} onClick={() => setSelectedView(key)}>{label}</button>)}</div>
    {diagnostics?.summary && <div className="ocr-diagnostic-summary"><span>박스 {diagnostics.summary.ocr_boxes || 0}</span><span>표 {diagnostics.summary.tables || 0}</span><span>표 행 {diagnostics.summary.table_rows || 0}</span><span>품목 후보 {diagnostics.summary.item_candidates || 0}</span><span className={diagnostics.summary.uncertain_candidates ? 'warning' : ''}>불확실 {diagnostics.summary.uncertain_candidates || 0}</span></div>}
    <div className="ocr-view-scroll">
      {selectedView === 'raw' && <table><thead><tr><th>#</th>{Array.from({ length: columnCount }, (_, index) => <th key={index}>{columnLabel(index)}</th>)}</tr></thead><tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}><th>{rowIndex + 1}</th>{Array.from({ length: columnCount }, (_, columnIndex) => <td key={columnIndex} title={row[columnIndex] || ''}>{row[columnIndex] || ''}</td>)}</tr>)}</tbody></table>}
      {selectedView !== 'raw' && <div className="candidate-trace-list">{candidates.length ? candidates.map((candidate, index) => <CandidateTrace candidate={candidate} index={index} pages={pages} predicted={predictedItems[index]} view={selectedView} key={index} />) : <div className="eval-preview-empty">추적할 품목 후보가 없습니다.</div>}</div>}
    </div>
  </div>;
}

function OcrBoxedImage({ preview, pages, alt, expanded = false }) {
  const imageRef = useRef(null);
  const [imageSize, setImageSize] = useState({ naturalWidth: 0, naturalHeight: 0, width: 0, height: 0 });
  const items = Array.isArray(pages?.[0]?.items) ? pages[0].items : [];

  useEffect(() => {
    const image = imageRef.current;
    if (!image) return undefined;
    const updateSize = () => setImageSize({
      naturalWidth: image.naturalWidth || 0,
      naturalHeight: image.naturalHeight || 0,
      width: image.clientWidth || 0,
      height: image.clientHeight || 0,
    });
    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(image);
    return () => observer.disconnect();
  }, [preview?.url, expanded]);

  const boxes = imageSize.naturalWidth > 0 ? items.map((item, index) => {
    const points = Array.isArray(item?.bbox) ? item.bbox : [];
    const xs = points.map((point) => Number(point?.[0])).filter(Number.isFinite);
    const ys = points.map((point) => Number(point?.[1])).filter(Number.isFinite);
    if (!xs.length || !ys.length) return null;
    const x0 = Math.max(0, Math.min(...xs)); const y0 = Math.max(0, Math.min(...ys));
    const x1 = Math.min(imageSize.naturalWidth, Math.max(...xs)); const y1 = Math.min(imageSize.naturalHeight, Math.max(...ys));
    const boxWidth = x1 - x0; const boxHeight = y1 - y0;
    if (boxWidth <= 0 || boxHeight <= 0) return null;
    if (boxHeight > imageSize.naturalHeight * .2 || boxWidth * boxHeight > imageSize.naturalWidth * imageSize.naturalHeight * .18) return null;
    return {
      key: `${index}-${item.text || ''}`,
      text: String(item.text || '').trim(),
      left: x0 / imageSize.naturalWidth * imageSize.width,
      top: y0 / imageSize.naturalHeight * imageSize.height,
      width: Math.max(boxWidth / imageSize.naturalWidth * imageSize.width, 2),
      height: Math.max(boxHeight / imageSize.naturalHeight * imageSize.height, 2),
    };
  }).filter(Boolean) : [];

  return <div className={`ocr-boxed-image ${expanded ? 'expanded' : ''}`} style={imageSize.width ? { width: imageSize.width, height: imageSize.height } : undefined} onClick={expanded ? (event) => event.stopPropagation() : undefined}>
    <img ref={imageRef} src={preview.url} alt={alt} draggable="false" onLoad={(event) => setImageSize({
      naturalWidth: event.currentTarget.naturalWidth,
      naturalHeight: event.currentTarget.naturalHeight,
      width: event.currentTarget.clientWidth,
      height: event.currentTarget.clientHeight,
    })} />
    {boxes.map((box) => <span className="eval-bbox-overlay" key={box.key} title={box.text || 'OCR 감지 영역'} style={{ left: box.left, top: box.top, width: box.width, height: box.height }} />)}
    {!!boxes.length && <em className="ocr-box-count">OCR {boxes.length}개</em>}
  </div>;
}

function PipelineLoading({ progress, models, imagePreview }) {
  return models.map((model) => <article className="model-pipeline-result pipeline-loading-result" key={`loading-${model}`}>
    <header><div><small>FINAL SERVICE</small><h2>{model}</h2></div><span className="pipeline-stage-label">{progress.stage === 'ocr' ? 'OCR 처리 중' : 'LLM 구조화 및 Excel 생성 중'}</span></header>
    <div className="pipeline-boxes">
      <section><h3>1. 입력 이미지 · OCR 박스</h3><div className="image-mini">{imagePreview?.type?.startsWith('image/') ? <OcrBoxedImage preview={imagePreview} pages={progress.ocr_pages} alt={progress.document_name} /> : <span className="eval-preview-empty">{progress.document_name}</span>}</div></section>
      <section><h3>2. OCR-LLM 파이프라인</h3>{progress.stage === 'ocr' ? <div className="pipeline-loader"><i /><strong>OCR 결과를 추출하고 있습니다.</strong><span>문자와 표 위치를 분석하는 중입니다.</span></div> : <OcrSheetPreview pages={progress.ocr_pages} text={progress.ocr_text} />}</section>
      <section><h3>3. LLM 구조화 · Excel 결과</h3><div className="pipeline-loader"><i /><strong>{progress.stage === 'ocr' ? 'OCR 완료 후 LLM을 실행합니다.' : `${model} 응답을 기다리고 있습니다.`}</strong><span>{progress.stage === 'ocr' ? 'OCR 처리 대기' : '품목을 구조화하고 Excel을 생성하는 중입니다.'}</span></div></section>
    </div>
  </article>);
}

function PendingReceiptEvaluation({ receipt }) {
  const [previewUrl, setPreviewUrl] = useState('');
  useEffect(() => {
    let active = true;
    let objectUrl = '';
    apiClient.get(`/ocr/documents/${receipt.document_id}/file`, { responseType: 'blob' }).then(({ data }) => {
      if (!active) return;
      objectUrl = URL.createObjectURL(data);
      setPreviewUrl(objectUrl);
    }).catch(() => {});
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [receipt.document_id]);
  return <article className="pending-receipt-evaluation">
    <div>{previewUrl ? <img src={previewUrl} alt={receipt.document_name} /> : <span>이미지를 불러오는 중입니다.</span>}</div>
    <section><small>분석 대기</small><h3>{receipt.document_name}</h3><p>정답 데이터가 없습니다.</p><em>정답 JSON을 입력하면 정확도, 필드 매칭 및 OCR 영향 분석을 진행할 수 있습니다.</em><strong>{receipt.model_name || '영수증 모델'}</strong></section>
  </article>;
}

function ModelPipelineResult({ run, result, imagePreview }) {
  const [imageOpen, setImageOpen] = useState(false);
  const score = result.system?.score || {};
  const impact = result.system?.ocr_impact;
  const workbook = result.system?.workbook;
  const errorAnalysis = result.system?.error_analysis || {};
  const errorTags = Array.isArray(errorAnalysis.error_tags) ? errorAnalysis.error_tags : [];
  if (Array.isArray(run.ocr_pages)) OCR_PREVIEW_CONTEXT.set(run.ocr_pages, {
    diagnostics: run.ocr_diagnostics,
    prediction: result.system?.prediction,
    truth: run.normalized_ground_truth || run.ground_truth,
  });
  const fieldMatches = flattenedMatches(score);
  const matched = fieldMatches
    .filter((field) => field.correct)
    .map((field) => ({
      ...field,
      actual: `결과 ${String(field.actual ?? '-')} · 정답 ${String(field.expected ?? '-')}`,
    }));
  const unmatched = fieldMatches.filter((field) => !field.correct);
  return <article className="model-pipeline-result"><header><div><small>FINAL SERVICE</small><h2>{result.model_name}</h2></div><dl><div><dt>정확도</dt><dd>{(Number(score.field_accuracy || 0) * 100).toFixed(1)}%</dd></div><div><dt>필드 매칭</dt><dd>{score.correct_fields || 0}/{score.evaluated_fields || 0}</dd></div><div><dt>OCR 영향</dt><dd>{impact?.counts?.LIKELY_OCR_ERROR || 0}개 가능</dd></div><div><dt>응답시간</dt><dd>{(Number(result.latency_ms || 0) / 1000).toFixed(1)}초</dd></div></dl></header><div className="pipeline-boxes"><section><h3>1. 입력 이미지 · OCR 박스 · 클릭해서 확대</h3><button className="image-mini" type="button" onClick={() => imagePreview && setImageOpen(true)}>{imagePreview?.type?.startsWith('image/') ? <OcrBoxedImage preview={imagePreview} pages={run.ocr_pages} alt={run.document_name} /> : <span className="eval-preview-empty">{run.document_name}<br />이미지 미리보기 없음</span>}</button></section><section><h3>2. OCR-LLM 파이프라인</h3><OcrSheetPreview pages={run.ocr_pages} text={run.ocr_text} /></section><section><h3>3. 생성 Excel 결과</h3><ExcelMiniPreview workbook={workbook} /></section></div><div className="match-status-board"><section className="matched-fields"><header><strong>매칭된 필드</strong><span>{matched.length}개</span></header><div>{matched.map((field) => <p key={field.field}><b>{field.label}</b><span>{String(field.actual ?? '-')}</span></p>)}{!matched.length && <em>매칭된 필드가 없습니다.</em>}</div></section><section className="unmatched-fields"><header><strong>매칭되지 않은 필드</strong><span>{unmatched.length}개</span></header><div>{unmatched.map((field) => { const fieldTags = tagsForMismatch(field.field, errorTags); return <p className="unmatched-field-row" key={field.field}><b>{field.label}</b><span>결과 {String(field.actual ?? '-')}</span><em>정답 {String(field.expected ?? '-')}</em><span className="field-error-tags">{fieldTags.length ? fieldTags.map((tag, index) => <ErrorTag tag={tag} key={`${tag.category}-${tag.code}-${index}`} />) : <small>예상 분류 없음</small>}</span></p>; })}{!unmatched.length && <em>모든 필드가 매칭됐습니다.</em>}</div></section></div>{!!errorTags.length && <section className="error-analysis-summary"><header><strong>예상 오류 분류</strong><span>{errorTags.length}개 태그 · {errorAnalysis.needs_review ? '검토 필요 항목 포함' : '자동 판별'}</span></header><div>{errorTags.map((tag, index) => <ErrorTag tag={tag} key={`${tag.category}-${tag.code}-${tag.scope}-${index}`} />)}</div></section>}<details><summary>OCR 영향 상세 보기</summary><OcrImpact impact={impact} /></details>{imageOpen && imagePreview?.type?.startsWith('image/') && <div className="image-lightbox" role="dialog" aria-modal="true" aria-label="OCR 박스가 표시된 입력 이미지 확대" onClick={() => setImageOpen(false)}><button className="lightbox-close" type="button" aria-label="닫기" onClick={() => setImageOpen(false)}>×</button><OcrBoxedImage preview={imagePreview} pages={run.ocr_pages} alt={run.document_name} expanded /></div>}</article>;
}

function PipelineEmpty() {
  return <article className="model-pipeline-result pipeline-empty-result">
    <header><div><small>FINAL SERVICE</small><h2>평가 결과 대기</h2></div><dl><div><dt>정확도</dt><dd>—</dd></div><div><dt>필드 매칭</dt><dd>—/—</dd></div><div><dt>OCR 영향</dt><dd>—</dd></div><div><dt>응답시간</dt><dd>—</dd></div></dl></header>
    <div className="pipeline-boxes">
      <section><h3>1. 입력 이미지 · OCR 박스</h3><div className="image-mini"><span className="eval-preview-empty">평가할 이미지를 선택해 주세요.</span></div></section>
      <section><h3>2. OCR-LLM 파이프라인</h3><div className="pipeline-empty-content">OCR 결과가 여기에 표시됩니다.</div></section>
      <section><h3>3. 생성 Excel 결과</h3><div className="pipeline-empty-content">생성된 Excel 미리보기가 여기에 표시됩니다.</div></section>
    </div>
    <div className="match-status-board"><section className="matched-fields"><header><strong>매칭된 필드</strong><span>0개</span></header><div><em>평가 후 매칭된 필드가 표시됩니다.</em></div></section><section className="unmatched-fields"><header><strong>매칭되지 않은 필드</strong><span>0개</span></header><div><em>평가 후 확인이 필요한 필드가 표시됩니다.</em></div></section></div>
  </article>;
}

export function datasetRows(payload) {
  if (Array.isArray(payload)) return payload;
  const collection = payload?.receipts || payload?.data;
  if (Array.isArray(collection)) return collection;
  const truthKeys = ['ground_truth', 'truth', 'label', 'expected', 'merchant', 'transaction_date', 'total_amount', 'payment_method'];
  if (Array.isArray(payload?.items) && !truthKeys.some((key) => key in payload)) return payload.items;
  return payload && typeof payload === 'object' ? [payload] : [];
}

export function truthOf(row) {
  const source = row?.ground_truth || row?.truth || row?.label || row?.expected || row || {};
  if (!('가게명' in source || '구매일자' in source || '총 결제액' in source)) return source;
  const items = Array.isArray(source['구매물품']) ? source['구매물품'].map((item) => ({
    name: item['상품명'], quantity: item['수량'], unit_price: item['단가'], total_amount: item['금액'],
  })) : [];
  const category = source['카테고리'] ?? source['구매물품']?.find((item) => item?.['카테고리'])?.['카테고리'];
  const totalQuantity = source['총 물품 수량'] ?? items.reduce((sum, item) => sum + (Number(item.quantity) || 0), 0);
  return {
    merchant: source['가게명'],
    transaction_date: String(source['구매일자'] || '').slice(0, 10) || null,
    expense_category: category,
    total_amount: source['총 결제액'],
    payment_method: source['결제방식'],
    total_quantity: totalQuantity,
    items,
  };
}

function nameOf(row) {
  return row?.filename || row?.file_name || row?.image || row?.name || row?.id || '';
}

export function imageNameOf(row) {
  return row?.image || row?.image_name || row?.filename || row?.file_name || row?.document_name || '';
}

export function normalizedFileName(value) {
  return String(value || '').trim().toLocaleLowerCase();
}

function evaluatedTime(value) {
  if (!value) return '-';
  return new Date(value).toLocaleString('ko-KR');
}

function summarize(runs, model) {
  const rows = runs.flatMap((run) => run.results || []).filter((result) => result.model_name === model);
  const measuredRows = rows.filter((result) => Number(result.latency_ms) > 0);
  const scores = rows.map((result) => result.system?.score).filter(Boolean);
  const evaluated = scores.reduce((sum, score) => sum + Number(score.evaluated_fields || 0), 0);
  const correct = scores.reduce((sum, score) => sum + Number(score.correct_fields || 0), 0);
  const sheets = [...new Set(rows.map((result) => result.system?.workbook?.active_sheet).filter(Boolean))];
  const rubrics = scores.map((score) => score.selection_rubric).filter(Boolean);
  const extractionScore = rubrics.length ? rubrics.reduce((sum, rubric) => sum + Number(rubric.extraction_score || 0), 0) / rubrics.length : 0;
  const schemaRate = rubrics.length ? rubrics.reduce((sum, rubric) => sum + Number(rubric.schema_rate || 0), 0) / rubrics.length : 0;
  const totalAmountRate = rubrics.length ? rubrics.filter((rubric) => rubric.total_amount_correct).length / rubrics.length : 0;
  return {
    documents: rows.length,
    success: rows.filter((row) => row.success).length,
    accuracy: evaluated ? correct / evaluated : 0,
    complete: scores.filter((score) => score.complete_match).length,
    latency: measuredRows.length ? measuredRows.reduce((sum, row) => sum + Number(row.latency_ms), 0) / measuredRows.length : null,
    measuredDocuments: measuredRows.length,
    workbookSuccess: rows.filter((row) => row.system?.workbook?.success).length,
    sheets,
    correct,
    evaluated,
    extractionScore,
    schemaRate,
    totalAmountRate,
  };
}

function rememberedBatchState() {
  const runs = readFinanceEvaluationRuns();
  const batchIds = runs.map((run) => run.batch_id).filter(Boolean);
  const activeBatchId = batchIds[batchIds.length - 1] || '';
  const batchRunCount = activeBatchId ? runs.filter((run) => run.batch_id === activeBatchId).length : 0;
  return { activeBatchId, batchComplete: batchRunCount > 1 };
}

export default function FinanceEvaluationPage({ embedded = false }) {
  const imageRef = useRef(null);
  const folderRef = useRef(null);
  const imageUrlRef = useRef('');
  const [runs, setRuns] = useState(readFinanceEvaluationRuns);
  const [dataset, setDataset] = useState([]);
  const [datasetName, setDatasetName] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('정답 JSON을 불러온 뒤 해당 영수증 이미지를 선택하세요.');
  const [imagePreview, setImagePreview] = useState(null);
  const [pipelineProgress, setPipelineProgress] = useState(null);
  const [activeBatchId, setActiveBatchId] = useState(() => rememberedBatchState().activeBatchId);
  const [batchComplete, setBatchComplete] = useState(() => rememberedBatchState().batchComplete);
  const [queuedBatchFiles, setQueuedBatchFiles] = useState(null);
  const [pendingReceipts, setPendingReceipts] = useState(() => readReceiptWorkspace().pendingEvaluations);
  const [batchHistory, setBatchHistory] = useState([]);
  const [singleHistory, setSingleHistory] = useState([]);
  const [evaluationMode, setEvaluationMode] = useState('single');
  const [hasSessionBatchResults, setHasSessionBatchResults] = useState(false);
  const [batchProgress, setBatchProgress] = useState(null);

  const loadBatchHistory = useCallback(() => apiClient.get('/finance-evaluations/batches')
    .then(({ data }) => setBatchHistory(Array.isArray(data) ? data : []))
    .catch(() => setBatchHistory([])), []);
  const loadSingleHistory = useCallback(() => apiClient.get('/finance-evaluations/runs', { params: { evaluation_mode: 'SINGLE', limit: 30 } })
    .then(({ data }) => setSingleHistory(Array.isArray(data) ? data : []))
    .catch(() => setSingleHistory([])), []);

  const batchRuns = useMemo(() => activeBatchId ? runs.filter((run) => run.batch_id === activeBatchId) : [], [runs, activeBatchId]);
  const models = useMemo(() => {
    const sourceRuns = batchRuns.length ? batchRuns : runs;
    return [...new Set(sourceRuns.flatMap((run) => (run.results || []).map((result) => result.model_name).filter(Boolean)))];
  }, [batchRuns, runs]);
  const summaries = useMemo(() => models.map((model) => ({ model, ...summarize(batchRuns, model) })), [batchRuns, models]);
  const scoredSummaries = useMemo(() => {
    const measured = summaries.filter((summary) => summary.latency != null);
    const fastest = measured.length ? Math.min(...measured.map((summary) => summary.latency)) : 0;
    return summaries.map((summary) => {
      const speedScore = fastest && summary.latency != null ? 3 * fastest / summary.latency : null;
      const costScore = summary.documents ? 2 : 0;
      return {
        ...summary,
        speedScore,
        costScore,
        finalScore: summary.extractionScore + (speedScore || 0) + costScore,
        qualityGate: summary.documents > 0 && summary.schemaRate >= 0.98 && summary.totalAmountRate >= 0.95,
      };
    });
  }, [summaries]);
  const batchMismatchRows = useMemo(() => batchRuns.flatMap((run) => (run.results || []).flatMap((result) => {
    const tags = result.system?.error_analysis?.error_tags || [];
    return flattenedMatches(result.system?.score).filter((field) => !field.correct).map((field) => ({
      imageId: run.document_id || `dataset-${Number(run.dataset_index || 0) + 1}`,
      imageName: run.matched_image || run.document_name || '-',
      datasetIndex: Number(run.dataset_index || 0) + 1,
      model: result.model_name,
      field: field.field,
      label: field.label,
      expected: field.expected,
      actual: field.actual,
      tags: tagsForMismatch(field.field, tags),
    }));
  })), [batchRuns]);
  const batchErrorDistribution = useMemo(() => {
    const counts = {};
    batchRuns.forEach((run) => (run.results || []).forEach((result) => (result.system?.error_analysis?.error_tags || []).forEach((tag) => {
      const key = tag.category || 'UNKNOWN'; counts[key] = (counts[key] || 0) + 1;
    })));
    const total = Object.values(counts).reduce((sum, value) => sum + value, 0);
    let offset = 0;
    const items = Object.entries(counts).map(([category, count]) => {
      const start = total ? offset / total * 100 : 0; offset += count;
      return { category, count, percent: total ? count / total * 100 : 0, start, end: total ? offset / total * 100 : 0, color: ERROR_CATEGORY_COLORS[category] || ERROR_CATEGORY_COLORS.UNKNOWN };
    });
    return { total, items, background: items.length ? `conic-gradient(${items.map((item) => `${item.color} ${item.start}% ${item.end}%`).join(',')})` : '#edf1f5' };
  }, [batchRuns]);
  const batchFieldAccuracy = useMemo(() => {
    const fields = {};
    batchRuns.forEach((run) => (run.results || []).forEach((result) => flattenedMatches(result.system?.score).forEach((field) => {
      const entry = fields[field.label] || { label: field.label, correct: 0, total: 0 };
      entry.total += 1; entry.correct += Number(field.correct); fields[field.label] = entry;
    })));
    return Object.values(fields).map((entry) => ({ ...entry, rate: entry.total ? entry.correct / entry.total : 0 })).sort((a, b) => a.rate - b.rate || a.label.localeCompare(b.label));
  }, [batchRuns]);
  const latestRun = runs[runs.length - 1];
  const latestPendingReceipt = pendingReceipts[pendingReceipts.length - 1];
  const latestDocument = latestPendingReceipt || latestRun;

  useEffect(() => { loadBatchHistory(); }, [loadBatchHistory]);
  useEffect(() => { loadSingleHistory(); }, [loadSingleHistory]);

  useEffect(() => {
    if (!latestDocument?.document_id || imagePreview?.name === latestDocument.document_name) return undefined;
    let active = true;
    apiClient.get(`/ocr/documents/${latestDocument.document_id}/file`, { responseType: 'blob' }).then(({ data }) => {
      if (!active) return;
      if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current);
      imageUrlRef.current = URL.createObjectURL(data);
      setImagePreview({ url: imageUrlRef.current, type: data.type, name: latestDocument.document_name });
    }).catch(() => {});
    return () => { active = false; };
  }, [latestDocument?.document_id, latestDocument?.document_name, imagePreview?.name]);

  useEffect(() => () => {
    if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current);
  }, []);

  useEffect(() => {
    const refreshPendingReceipts = () => setPendingReceipts(readReceiptWorkspace().pendingEvaluations);
    window.addEventListener('receipt-workspace-updated', refreshPendingReceipts);
    return () => window.removeEventListener('receipt-workspace-updated', refreshPendingReceipts);
  }, []);

  useEffect(() => {
    const refreshRuns = () => setRuns(readFinanceEvaluationRuns());
    window.addEventListener('finance-evaluations-updated', refreshRuns);
    window.addEventListener('focus', refreshRuns);
    return () => {
      window.removeEventListener('finance-evaluations-updated', refreshRuns);
      window.removeEventListener('focus', refreshRuns);
    };
  }, []);

  const saveRuns = (next) => setRuns(saveFinanceEvaluationRuns(next));
  const loadDataset = async (file) => {
    if (!file) return;
    try {
      const rows = datasetRows(JSON.parse(await file.text()));
      if (!rows.length) throw new Error('정답 항목을 찾지 못했습니다.');
      setDataset(rows); setDatasetName(file.name); setSelectedIndex(0);
      setStatus(`${rows.length}개 정답을 불러왔습니다.`);
    } catch (error) { setStatus(`정답 JSON 오류: ${error.message}`); }
  };

  const matchDatasetRow = (file) => {
    const uploadedName = normalizedFileName(file.name);
    const singleRowWithoutImageKey = dataset.length === 1 && !imageNameOf(dataset[0]);
    const matches = singleRowWithoutImageKey
      ? [{ row: dataset[0], index: 0 }]
      : dataset
        .map((row, index) => ({ row, index }))
        .filter(({ row }) => normalizedFileName(imageNameOf(row)) === uploadedName);
    if (!matches.length) throw new Error(`정답 JSON의 image 키에서 ${file.name}을 찾지 못했습니다.`);
    if (matches.length > 1) throw new Error(`정답 JSON에 image 값이 ${file.name}인 항목이 ${matches.length}개 있습니다.`);
    return matches[0];
  };

  const evaluateFile = async (file, matched, batchId = '') => {
    if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current);
    imageUrlRef.current = URL.createObjectURL(file);
    setImagePreview({ url: imageUrlRef.current, type: file.type, name: file.name });
    setSelectedIndex(matched.index);
    setPipelineProgress({ stage: 'ocr', document_name: file.name, ocr_text: '', ocr_pages: [] });
    const form = new FormData(); form.append('file', file);
    const { data: ocr } = await apiClient.post('/ocr/upload?processing_mode=receipt', form, { timeout: 360000 });
    setPipelineProgress({
      stage: 'llm', document_name: ocr.filename || file.name,
      ocr_text: (ocr.pages || []).map((page) => page.text || '').join('\n'), ocr_pages: ocr.pages || [],
    });
    const classifyStartedAt = performance.now();
    const { data: record } = await apiClient.post('/finance/records/classify', {
      document_id: ocr.document_id,
    }, { timeout: 180000 });
    const classifyLatencyMs = Math.max(1, Math.round(performance.now() - classifyStartedAt));
    rememberReceiptRecord(record);
    const { data: evaluation } = await apiClient.post('/finance-evaluations/record', {
      document_id: ocr.document_id,
      record_id: record.id,
      ground_truth: truthOf(matched.row),
      latency_ms: classifyLatencyMs,
      batch_id: batchId || null,
      dataset_name: datasetName || null,
      dataset_index: matched.index,
      source_file_name: file.name,
    }, { timeout: 1200000 });
    return {
      ...evaluation, dataset_name: datasetName, dataset_index: matched.index,
      matched_image: file.name, evaluated_at: evaluation.evaluated_at || new Date().toISOString(),
      batch_id: evaluation.batch_id || batchId || null,
    };
  };

  const runEvaluation = async (file) => {
    if (!file || !dataset.length || loading) return;
    clearPendingReceipts();
    setEvaluationMode('single');
    clearFinanceEvaluationRuns();
    setRuns([]);
    setPendingReceipts([]);
    setActiveBatchId(''); setBatchComplete(false); setLoading(true);
    try {
      const matched = matchDatasetRow(file);
      setStatus(`${file.name}을 ${matched.index + 1}번 정답과 매핑했습니다. OCR 및 모델 비교 중...`);
      const entry = await evaluateFile(file, matched);
      saveRuns([entry]);
      loadSingleHistory();
      setStatus(`${file.name} 자동 매핑 및 평가 완료 · ${matched.index + 1}/${dataset.length}`);
      if (matched.index < dataset.length - 1) setSelectedIndex(matched.index + 1);
    } catch (error) {
      setStatus(`평가 실패: ${error.response?.data?.detail || error.message || '알 수 없는 오류'}`);
    } finally {
      setPipelineProgress(null); setLoading(false); if (imageRef.current) imageRef.current.value = '';
    }
  };

  const runFolderEvaluation = async (fileList) => {
    if (!fileList?.length || !dataset.length || loading) return;
    const files = Array.from(fileList).filter((file) => /\.(png|jpe?g|webp|bmp|pdf)$/i.test(file.name));
    if (files.length < 2) {
      setStatus('폴더 일괄 평가는 정답과 매칭되는 이미지가 2장 이상 필요합니다.');
      if (folderRef.current) folderRef.current.value = '';
      return;
    }
    const unmatched = files.filter((file) => {
      try { matchDatasetRow(file); return false; } catch { return true; }
    });
    if (unmatched.length) {
      setStatus(`일괄 평가를 시작하지 못했습니다. 정답 JSON과 이름이 일치하지 않는 파일: ${unmatched.map((file) => file.name).join(', ')}`);
      if (folderRef.current) folderRef.current.value = '';
      return;
    }
    clearPendingReceipts();
    setEvaluationMode('batch');
    setHasSessionBatchResults(false);
    setBatchProgress({ completed: 0, total: files.length + 1, imageTotal: files.length, currentFile: '', stage: 'images', running: true });
    clearFinanceEvaluationRuns();
    setRuns([]);
    setPendingReceipts([]);
    setLoading(true);
    setStatus(`정답 ${dataset.length}개와 이미지 ${files.length}개의 이름 매칭 완료 · 평가 배치 준비 중...`);
    let batchId = '';
    try {
      const { data: batch } = await apiClient.post('/finance-evaluations/batches', {
        batch_name: `${datasetName || 'receipt'} 일괄 평가`,
        dataset_name: datasetName || null,
        total_items: files.length,
        evaluation_mode: 'BULK',
      });
      batchId = batch.id;
    } catch (error) {
      setStatus(`평가 배치를 저장하지 못했습니다: ${error.response?.data?.detail || error.message}`);
      setLoading(false);
      setBatchProgress(null);
      if (folderRef.current) folderRef.current.value = '';
      return;
    }
    setActiveBatchId(batchId); setBatchComplete(false);
    let accumulated = []; let completed = 0; const errors = [];
    let processed = 0;
    for (const file of files) {
      setBatchProgress({ completed: processed, total: files.length + 1, imageTotal: files.length, currentFile: file.name, stage: 'images', running: true });
      try {
        const matched = matchDatasetRow(file);
        setStatus(`폴더 일괄 평가 ${completed + 1}/${files.length} · ${file.name}`);
        const entry = await evaluateFile(file, matched, batchId);
        accumulated = [...accumulated, entry]; saveRuns(accumulated); completed += 1;
        setHasSessionBatchResults(true);
      } catch (error) {
        errors.push(`${file.name}: ${error.response?.data?.detail || error.message || '평가 실패'}`);
      } finally {
        processed += 1;
        setBatchProgress({ completed: processed, total: files.length + 1, imageTotal: files.length, currentFile: file.name, stage: 'images', running: true });
      }
    }
    setBatchProgress({ completed: files.length, total: files.length + 1, imageTotal: files.length, currentFile: files[files.length - 1]?.name || '', stage: 'finalizing', running: true });
    try {
      await apiClient.post(`/finance-evaluations/batches/${batchId}/finalize`);
      loadBatchHistory();
    } catch (error) {
      errors.push(`배치 집계 저장: ${error.response?.data?.detail || error.message || '저장 실패'}`);
    }
    setBatchProgress({ completed: files.length + 1, total: files.length + 1, imageTotal: files.length, currentFile: files[files.length - 1]?.name || '', stage: 'complete', running: false });
    setPipelineProgress(null); setLoading(false); setBatchComplete(completed >= 2);
    setStatus(`폴더 일괄 평가 완료 · 성공 ${completed}/${files.length}${errors.length ? ` · 실패 ${errors.length}` : ''}`);
    if (folderRef.current) folderRef.current.value = '';
  };

  useEffect(() => {
    let active = true;
    let consumingInput = null;
    const consumeQueuedInput = () => {
      const input = peekFinanceEvaluationInput();
      if (!input?.datasetFile || !input.imageFiles?.length || consumingInput === input) return;
      consumingInput = input;
      setStatus(`정답 JSON과 이미지 ${input.imageFiles.length}개를 불러오는 중입니다...`);
      input.datasetFile.text().then((text) => {
        if (!active) return;
        const rows = datasetRows(JSON.parse(text));
        if (!rows.length) throw new Error('정답 항목을 찾지 못했습니다.');
        setDataset(rows);
        setDatasetName(input.datasetFile.name);
        setSelectedIndex(0);
        // Keep the module-level transfer pending until the execution effect
        // actually claims it. If this component unmounts during navigation,
        // the next mount can still recover the same File objects.
        setQueuedBatchFiles({ input, files: input.imageFiles });
        setStatus(`${rows.length}개 정답과 ${input.imageFiles.length}개 파일을 받았습니다. 이름을 확인한 뒤 평가를 시작합니다.`);
      }).catch((error) => {
        if (active) setStatus(`정답 JSON 오류: ${error.message}`);
      }).finally(() => {
        if (consumingInput === input) consumingInput = null;
      });
    };
    window.addEventListener(FINANCE_EVALUATION_INPUT_QUEUED, consumeQueuedInput);
    window.addEventListener('focus', consumeQueuedInput);
    window.addEventListener('pageshow', consumeQueuedInput);
    const consumeWhenVisible = () => {
      if (document.visibilityState === 'visible') consumeQueuedInput();
    };
    document.addEventListener('visibilitychange', consumeWhenVisible);
    consumeQueuedInput();
    return () => {
      active = false;
      window.removeEventListener(FINANCE_EVALUATION_INPUT_QUEUED, consumeQueuedInput);
      window.removeEventListener('focus', consumeQueuedInput);
      window.removeEventListener('pageshow', consumeQueuedInput);
      document.removeEventListener('visibilitychange', consumeWhenVisible);
    };
  }, []);

  useEffect(() => {
    if (!queuedBatchFiles || !dataset.length || loading) return;
    const { input, files } = queuedBatchFiles;
    // This is the acknowledgement point: the dataset is parsed, files are in
    // component state, and an evaluation function is about to run.
    clearFinanceEvaluationInput(input);
    setQueuedBatchFiles(null);
    if (files.length === 1) { setEvaluationMode('single'); runEvaluation(files[0]); }
    else { setEvaluationMode('batch'); runFolderEvaluation(files); }
  }, [queuedBatchFiles, dataset.length, loading]);

  const exportResults = () => {
    const isBatchExport = batchComplete && batchRuns.length > 1;
    const selectedRuns = isBatchExport ? batchRuns : (runs.length ? [runs[runs.length - 1]] : []);
    const sanitizedRuns = selectedRuns.map((run) => ({
      ...run, results: (run.results || []).map(({ pure: _pure, ...result }) => result),
    }));
    const fieldErrorCounts = {}; const errorCases = [];
    sanitizedRuns.forEach((run) => (run.results || []).forEach((result) => {
      flattenedMatches(result.system?.score).filter((field) => !field.correct).forEach((field) => {
        const key = field.label || field.field;
        fieldErrorCounts[key] = (fieldErrorCounts[key] || 0) + 1;
        errorCases.push({
          dataset_index: Number(run.dataset_index) + 1,
          image_id: run.document_id || null,
          image: run.matched_image || run.document_name,
          model: result.model_name,
          field: key,
          actual: field.actual ?? null,
          expected: field.expected ?? null,
          error_tags: tagsForMismatch(field.field, result.system?.error_analysis?.error_tags || []),
        });
      });
    }));
    const modelStatistics = (isBatchExport ? scoredSummaries : models.map((model) => ({ model, ...summarize(selectedRuns, model) })))
      .map((summary) => ({
        model: summary.model, evaluated_documents: summary.documents, successful_documents: summary.success,
        extraction_score_95: summary.extractionScore, schema_success_rate: summary.schemaRate,
        total_amount_accuracy: summary.totalAmountRate, average_latency_ms: summary.latency,
        speed_score_3: summary.speedScore, speed_measured_documents: summary.measuredDocuments,
        local_cost_score_2: summary.costScore ?? null,
        final_score_100: summary.finalScore ?? null, quality_gate_passed: summary.qualityGate ?? null,
      }));
    const payload = {
      exported_at: new Date().toISOString(), export_type: isBatchExport ? 'BATCH_STATISTICS' : 'SINGLE_RESULT',
      batch_id: isBatchExport ? activeBatchId : null, dataset_name: datasetName,
      evaluated_images: selectedRuns.length, models, model_statistics: modelStatistics,
      field_error_counts: Object.fromEntries(Object.entries(fieldErrorCounts).sort((a, b) => b[1] - a[1])),
      error_cases: errorCases, runs: sanitizedRuns,
    };
    const content = JSON.stringify(payload, null, 2);
    const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url;
    anchor.download = isBatchExport ? `finance-model-batch-${selectedRuns.length}-statistics.json` : 'finance-model-evaluation.json';
    anchor.click(); URL.revokeObjectURL(url);
  };

  const batchInsightsReady = hasSessionBatchResults && batchRuns.length > 0;

  return <div className={embedded ? 'finance-eval-embedded-shell' : 'app-shell finance-eval-shell'}>{!embedded && <Sidebar />}<main className={`finance-eval-page ${embedded ? 'embedded' : ''}`}>
    <header><div><p>FINANCE MODEL LAB</p><div className="finance-eval-title-row"><h1>영수증 서비스 결과 평가</h1><nav className="finance-eval-mode-tabs" aria-label="평가 방식"><button type="button" className={evaluationMode === 'single' ? 'active' : ''} onClick={() => setEvaluationMode('single')}>단일 평가</button><button type="button" className={evaluationMode === 'batch' ? 'active' : ''} onClick={() => setEvaluationMode('batch')}>일괄 평가</button></nav></div><span>동일 OCR 입력으로 최종 서비스의 필드 매칭과 Excel 변환 결과를 비교합니다.</span></div><button disabled={!runs.length} onClick={exportResults}><IoDownloadOutline /> {evaluationMode === 'batch' ? '일괄 통계 JSON' : '결과 JSON'}</button></header>
    {!embedded && <section className="eval-setup">
      <label><span>정답 데이터</span><input type="file" accept=".json,application/json" onChange={(event) => loadDataset(event.target.files?.[0])} /><small>{datasetName || 'receipt_kr.json을 선택하세요'}</small></label>
      <label><span>평가 모델</span><small>.env의 RECEIPTS_LLM_MODEL을 사용합니다.</small></label>
      <label><span>현재 정답 항목</span><select disabled={!dataset.length} value={selectedIndex} onChange={(event) => setSelectedIndex(Number(event.target.value))}>{dataset.map((row, index) => <option value={index} key={`${nameOf(row)}-${index}`}>{index + 1}. {nameOf(row) || `항목 ${index + 1}`}</option>)}</select></label>
      {evaluationMode === 'single' && <button className="eval-upload" disabled={!dataset.length || loading} onClick={() => imageRef.current?.click()}>{loading ? '평가 실행 중...' : '평가 시작'}</button>}
      <input ref={imageRef} hidden type="file" accept=".png,.jpg,.jpeg,.webp,.bmp,.pdf" onChange={(event) => runEvaluation(event.target.files?.[0])} />
      {evaluationMode === 'batch' && <button className="eval-upload batch-upload" disabled={!dataset.length || loading} onClick={() => folderRef.current?.click()}>{loading ? '일괄 평가 중...' : '프로젝트 폴더 일괄 평가'}</button>}
      <input ref={folderRef} hidden type="file" accept=".png,.jpg,.jpeg,.webp,.bmp,.pdf" multiple webkitdirectory="true" directory="true" onChange={(event) => runFolderEvaluation(event.target.files)} />
      <p>{status}</p>
      {evaluationMode === 'batch' && batchProgress && <BatchEvaluationProgress progress={batchProgress} />}
    </section>}
    {embedded && <section className="eval-setup embedded-eval-status">{evaluationMode === 'batch' && batchProgress ? <BatchEvaluationProgress progress={batchProgress} /> : <p>{status}</p>}</section>}

    {evaluationMode === 'single' && !!pendingReceipts.length && <section className="pending-receipt-evaluations">
      <header><div><h2>정답 데이터 없는 영수증</h2><p>자동 문서화에서 전달된 이미지입니다. 새로고침 전까지 이 화면에 유지됩니다.</p></div><span>{pendingReceipts.length}건 · 정답 데이터가 없습니다</span></header>
      <div>{pendingReceipts.map((receipt) => <PendingReceiptEvaluation key={receipt.document_id} receipt={receipt} />)}</div>
    </section>}

    {evaluationMode === 'batch' && <section className="batch-insight-grid">
      <article className="batch-history-card"><header><div><h2>일괄 평가 이력</h2><p>DB에 저장된 최근 실행 목록</p></div><span>{batchHistory.length}회</span></header><div>{batchHistory.map((batch) => <section className={batch.id === activeBatchId ? 'active' : ''} key={batch.id}><div><strong>{batch.batch_name}</strong><small>{batch.created_at ? new Date(batch.created_at).toLocaleString('ko-KR') : '-'}</small></div><span>{batch.completed_items ?? 0}/{batch.total_items ?? 0}</span><em>{batch.status}</em></section>)}{!batchHistory.length && <p className="eval-empty">저장된 일괄 평가가 없습니다.</p>}</div></article>
      <section className="eval-summary-grid batch-selection-metrics">{batchInsightsReady && scoredSummaries.map((summary) => <article key={summary.model}>
        <small>TEST01~TEST20 선정 지표</small><h2 title={summary.model}>{summary.model}</h2>
        <strong>{summary.finalScore.toFixed(1)}점</strong><p>총점 100 · {summary.qualityGate ? '품질 게이트 통과' : '품질 게이트 재검토'}</p>
        <dl>
          <div><dt>추출 정확도</dt><dd>{summary.extractionScore.toFixed(1)} / 95</dd></div>
          <div><dt>핵심·품목·카테고리·스키마·안정성</dt><dd>{summary.documents}건</dd></div>
          <div><dt>스키마 성공률</dt><dd>{(summary.schemaRate * 100).toFixed(1)}%</dd></div>
          <div><dt>총 결제액 정확도</dt><dd>{(summary.totalAmountRate * 100).toFixed(1)}%</dd></div>
          <div><dt>평균 응답시간</dt><dd>{summary.latency == null ? '미측정' : `${(summary.latency / 1000).toFixed(1)}초`}</dd></div>
          <div><dt>속도점수</dt><dd>{summary.speedScore == null ? '재평가 필요' : `${summary.speedScore.toFixed(2)} / 3`}</dd></div>
          <div><dt>로컬 비용점수</dt><dd>{summary.costScore.toFixed(1)} / 2</dd></div>
        </dl>
      </article>)}{!batchInsightsReady && <article className="batch-insight-empty"><small>선정 지표</small><h2>평가 결과 대기</h2><p>일괄 평가를 진행하면 선정 지표가 생성됩니다.</p></article>}</section>
      <article className="batch-error-chart"><header><div><h2>오류 유형 분포</h2><p>현재 일괄 평가의 오류 태그 기준</p></div><span>{batchInsightsReady ? batchErrorDistribution.total : 0}건</span></header>{batchInsightsReady ? <div><div className="error-donut" style={{ background: batchErrorDistribution.background }}><i><strong>{batchErrorDistribution.total}</strong><small>총 오류</small></i></div><ul>{batchErrorDistribution.items.map((item) => <li key={item.category}><i style={{ background: item.color }} /><span>{ERROR_CATEGORY_LABELS[item.category] || item.category}</span><b>{item.count} ({item.percent.toFixed(1)}%)</b></li>)}</ul></div> : <p className="eval-empty">일괄 평가를 진행하면 오류 유형 분포가 생성됩니다.</p>}</article>
      <article className="batch-field-chart"><header><div><h2>평균 필드별 매칭도</h2><p>현재 일괄 평가 전체 이미지 기준</p></div><span>{batchInsightsReady ? batchFieldAccuracy.length : 0}필드</span></header><div>{batchInsightsReady && batchFieldAccuracy.map((field) => <section key={field.label}><span title={field.label}>{field.label}</span><i><b style={{ width: `${field.rate * 100}%` }} /></i><strong>{(field.rate * 100).toFixed(1)}%</strong></section>)}{(!batchInsightsReady || !batchFieldAccuracy.length) && <p className="eval-empty">{batchInsightsReady ? '필드 평가 결과가 없습니다.' : '일괄 평가를 진행하면 필드별 매칭도가 생성됩니다.'}</p>}</div></article>
    </section>}

    {evaluationMode === 'batch' && batchInsightsReady && <section className="batch-mismatch-panel">
      <header><div><h2>매칭 실패 오류 목록</h2><p>일괄 평가에서 정답과 일치하지 않은 필드와 예상 원인을 이미지별로 표시합니다.</p></div><span>{batchMismatchRows.length}건</span></header>
      <div className="batch-mismatch-table">
        <div className="batch-mismatch-row batch-mismatch-head"><span>이미지 ID</span><span>이미지 이름</span><span>필드</span><span>정답</span><span>예측값</span><span>예상 오류 분류</span></div>
        {batchMismatchRows.map((row, index) => <div className="batch-mismatch-row" key={`${row.imageId}-${row.model}-${row.field}-${index}`}>
          <span title={row.imageId}><b>#{row.datasetIndex}</b><small>{row.imageId}</small></span>
          <span title={row.imageName}>{row.imageName}<small>{row.model}</small></span>
          <strong>{row.label}</strong>
          <span title={displayEvaluationValue(row.expected)}>{displayEvaluationValue(row.expected)}</span>
          <span title={displayEvaluationValue(row.actual)}>{displayEvaluationValue(row.actual)}</span>
          <span className="batch-error-tags">{row.tags.length ? row.tags.map((tag, tagIndex) => <ErrorTag tag={tag} key={`${tag.category}-${tag.code}-${tagIndex}`} />) : <em>분류 없음</em>}</span>
        </div>)}
        {!batchMismatchRows.length && <p className="eval-empty">일괄 평가에서 매칭 실패한 필드가 없습니다.</p>}
      </div>
    </section>}

    {evaluationMode === 'single' && <section className="latest-pipeline-results"><header><div><h2>{pipelineProgress ? '현재 평가 진행 상황' : '최근 실행 결과'}</h2><p>입력 이미지, OCR 원문, 실제 생성된 Excel을 나란히 확인합니다.</p></div><span>{pipelineProgress?.document_name || latestRun?.document_name || '평가 대기'}</span></header>{pipelineProgress ? <PipelineLoading progress={pipelineProgress} models={models.length ? models : ['최종 서비스']} imagePreview={imagePreview} /> : latestRun ? (latestRun.results || []).map((result) => <ModelPipelineResult key={`${latestRun.evaluated_at}-${result.model_name}`} run={latestRun} result={result} imagePreview={imagePreview} />) : <PipelineEmpty />}</section>}

    {evaluationMode === 'single' && <section className="eval-results single-evaluation-history"><header><div><h2>최근 단일 평가 이력</h2><p>DB에 저장된 최근 단일 평가 결과입니다.</p></div><span>{singleHistory.length}건</span></header>
      <div className="eval-table"><div className="eval-row eval-head"><span>데이터</span><span>모델</span><span>최종 정확도</span><span>필드 매칭</span><span>생성 Excel 문서</span><span>OCR 영향</span><span>응답시간</span></div>
      {singleHistory.flatMap((run) => (run.results || []).map((result) => { const impact = result.system?.ocr_impact; const likelyOcrErrors = impact?.counts?.LIKELY_OCR_ERROR || 0; const score = result.system?.score || {}; const workbook = result.system?.workbook || {}; return <div className="eval-row" key={`single-${run.evaluation_id || run.evaluated_at}-${result.model_name}`}><span title={evaluatedTime(run.evaluated_at)}>{Number(run.dataset_index || 0) + 1}. {run.document_name}<small>{evaluatedTime(run.evaluated_at)}</small></span><strong>{result.model_name}</strong><span>{score.field_accuracy == null ? '-' : `${(score.field_accuracy * 100).toFixed(1)}%`}</span><span className={score.complete_match ? 'ok' : 'bad'}>{score.correct_fields ?? 0}/{score.evaluated_fields ?? 0}</span><span className={workbook.success ? 'ok' : 'bad'}>{workbook.active_sheet || '생성 실패'}</span><span className={likelyOcrErrors ? 'ocr-error' : 'ok'}>{impact ? `${likelyOcrErrors}개 가능` : '-'}</span><span>{result.latency_ms == null ? '-' : `${(result.latency_ms / 1000).toFixed(1)}초`}</span></div>; }))}
      {!singleHistory.length && <p className="eval-empty">저장된 단일 평가 이력이 없습니다.</p>}</div>
    </section>}

    {evaluationMode === 'batch' && <section className="eval-results"><header><div><h2>누적 결과</h2><p>브라우저에 자동 저장됩니다. 같은 이미지도 실행 날짜와 시간이 다르면 별도 결과로 누적됩니다.</p></div><span>{runs.length}회</span></header>
      <div className="eval-table"><div className="eval-row eval-head"><span>데이터</span><span>모델</span><span>최종 정확도</span><span>필드 매칭</span><span>생성 Excel 문서</span><span>OCR 영향</span><span>응답시간</span></div>
      {[...runs].sort((a, b) => String(b.evaluated_at || '').localeCompare(String(a.evaluated_at || ''))).flatMap((run) => (run.results || []).map((result) => { const impact = result.system.ocr_impact; const likelyOcrErrors = impact?.counts?.LIKELY_OCR_ERROR || 0; const score = result.system.score; const workbook = result.system.workbook; return <div className="eval-row" key={`${run.dataset_name}-${run.dataset_index}-${run.evaluated_at}-${result.model_name}`}><span title={evaluatedTime(run.evaluated_at)}>{run.dataset_index + 1}. {run.document_name}<small>{evaluatedTime(run.evaluated_at)}</small></span><strong>{result.model_name}</strong><span>{(score.field_accuracy * 100).toFixed(1)}%</span><span className={score.complete_match ? 'ok' : 'bad'}>{score.correct_fields}/{score.evaluated_fields}</span><span className={workbook.success ? 'ok' : 'bad'}>{workbook.active_sheet || '생성 실패'}</span><span className={likelyOcrErrors ? 'ocr-error' : 'ok'}>{impact ? `${likelyOcrErrors}개 가능` : '-'}</span><span>{(result.latency_ms / 1000).toFixed(1)}초</span><details><summary>매칭 상세 및 OCR 영향 보기</summary><div>{Object.entries(score.fields || {}).map(([field, value]) => field === 'items' ? <p className={value.count_correct ? 'ok' : 'bad'} key={field}><b>품목 수</b><span>{value.actual_count}</span><em>정답 {value.expected_count} · 초과 {value.false_positive_count || 0}</em></p> : <p className={value.correct ? 'ok' : 'bad'} key={field}><b>{LABELS[field] || field}</b><span>{String(value.actual ?? '-')}</span><em>정답 {String(value.expected ?? '-')}</em></p>)}</div><OcrImpact impact={impact} /></details></div>; }))}
      {!runs.length && <p className="eval-empty">아직 저장된 평가 결과가 없습니다.</p>}</div>
    </section>}
  </main></div>;
}
