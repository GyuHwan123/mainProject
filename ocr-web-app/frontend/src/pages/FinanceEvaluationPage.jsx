import { useEffect, useMemo, useRef, useState } from 'react';
import { IoDownloadOutline } from 'react-icons/io5';

import apiClient from '../api/client';
import Sidebar from '../components/Sidebar';
import {
  FINANCE_EVALUATION_STORAGE_KEY,
  readFinanceEvaluationRuns,
  saveFinanceEvaluationRuns,
} from '../features/financeEvaluationStorage';
import { clearFinanceEvaluationInput, peekFinanceEvaluationInput } from '../features/financeEvaluationTransfer';
import '../style/FinanceEvaluationPage.scss';

const DEFAULT_MODELS = ['llama3b-receipt-v3:latest', 'gemma2:2b', 'finance-gemma2-qlora-v1', 'finance-gemma2-qlora-v2'];
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

function OcrSheetPreview({ pages, text }) {
  const rows = useMemo(() => buildOcrGrid(pages, text), [pages, text]);
  const columnCount = Math.max(1, ...rows.map((row) => row.length));
  return <div className="ocr-sheet-mini"><table><thead><tr><th>#</th>{Array.from({ length: columnCount }, (_, index) => <th key={index}>{columnLabel(index)}</th>)}</tr></thead><tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}><th>{rowIndex + 1}</th>{Array.from({ length: columnCount }, (_, columnIndex) => <td key={columnIndex}>{row[columnIndex] || ''}</td>)}</tr>)}</tbody></table></div>;
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
      <section><h3>2. OCR Excel형 워크시트</h3>{progress.stage === 'ocr' ? <div className="pipeline-loader"><i /><strong>OCR 결과를 추출하고 있습니다.</strong><span>문자와 표 위치를 분석하는 중입니다.</span></div> : <OcrSheetPreview pages={progress.ocr_pages} text={progress.ocr_text} />}</section>
      <section><h3>3. LLM 구조화 · Excel 결과</h3><div className="pipeline-loader"><i /><strong>{progress.stage === 'ocr' ? 'OCR 완료 후 LLM을 실행합니다.' : `${model} 응답을 기다리고 있습니다.`}</strong><span>{progress.stage === 'ocr' ? 'OCR 처리 대기' : '품목을 구조화하고 Excel을 생성하는 중입니다.'}</span></div></section>
    </div>
  </article>);
}

function ModelPipelineResult({ run, result, imagePreview }) {
  const [imageOpen, setImageOpen] = useState(false);
  const score = result.system?.score || {};
  const impact = result.system?.ocr_impact;
  const workbook = result.system?.workbook;
  const fieldMatches = flattenedMatches(score);
  const matched = fieldMatches
    .filter((field) => field.correct)
    .map((field) => ({
      ...field,
      actual: `결과 ${String(field.actual ?? '-')} · 정답 ${String(field.expected ?? '-')}`,
    }));
  const unmatched = fieldMatches.filter((field) => !field.correct);
  return <article className="model-pipeline-result"><header><div><small>FINAL SERVICE</small><h2>{result.model_name}</h2></div><dl><div><dt>정확도</dt><dd>{(Number(score.field_accuracy || 0) * 100).toFixed(1)}%</dd></div><div><dt>필드 매칭</dt><dd>{score.correct_fields || 0}/{score.evaluated_fields || 0}</dd></div><div><dt>OCR 영향</dt><dd>{impact?.counts?.LIKELY_OCR_ERROR || 0}개 가능</dd></div><div><dt>응답시간</dt><dd>{(Number(result.latency_ms || 0) / 1000).toFixed(1)}초</dd></div></dl></header><div className="pipeline-boxes"><section><h3>1. 입력 이미지 · OCR 박스 · 클릭해서 확대</h3><button className="image-mini" type="button" onClick={() => imagePreview && setImageOpen(true)}>{imagePreview?.type?.startsWith('image/') ? <OcrBoxedImage preview={imagePreview} pages={run.ocr_pages} alt={run.document_name} /> : <span className="eval-preview-empty">{run.document_name}<br />이미지 미리보기 없음</span>}</button></section><section><h3>2. OCR Excel형 워크시트</h3><OcrSheetPreview pages={run.ocr_pages} text={run.ocr_text} /></section><section><h3>3. 생성 Excel 결과</h3><ExcelMiniPreview workbook={workbook} /></section></div><div className="match-status-board"><section className="matched-fields"><header><strong>매칭된 필드</strong><span>{matched.length}개</span></header><div>{matched.map((field) => <p key={field.field}><b>{field.label}</b><span>{String(field.actual ?? '-')}</span></p>)}{!matched.length && <em>매칭된 필드가 없습니다.</em>}</div></section><section className="unmatched-fields"><header><strong>매칭되지 않은 필드</strong><span>{unmatched.length}개</span></header><div>{unmatched.map((field) => <p key={field.field}><b>{field.label}</b><span>결과 {String(field.actual ?? '-')}</span><em>정답 {String(field.expected ?? '-')}</em></p>)}{!unmatched.length && <em>모든 필드가 매칭됐습니다.</em>}</div></section></div><details><summary>OCR 영향 상세 보기</summary><OcrImpact impact={impact} /></details>{imageOpen && imagePreview?.type?.startsWith('image/') && <div className="image-lightbox" role="dialog" aria-modal="true" aria-label="OCR 박스가 표시된 입력 이미지 확대" onClick={() => setImageOpen(false)}><button className="lightbox-close" type="button" aria-label="닫기" onClick={() => setImageOpen(false)}>×</button><OcrBoxedImage preview={imagePreview} pages={run.ocr_pages} alt={run.document_name} expanded /></div>}</article>;
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
  const category = source['구매물품']?.find((item) => item?.['카테고리'])?.['카테고리'];
  return {
    merchant: source['가게명'],
    transaction_date: String(source['구매일자'] || '').slice(0, 10) || null,
    expense_category: category,
    total_amount: source['총 결제액'],
    payment_method: source['결제방식'],
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
    latency: rows.length ? rows.reduce((sum, row) => sum + Number(row.latency_ms || 0), 0) / rows.length : 0,
    workbookSuccess: rows.filter((row) => row.system?.workbook?.success).length,
    sheets,
    correct,
    evaluated,
    extractionScore,
    schemaRate,
    totalAmountRate,
  };
}

export default function FinanceEvaluationPage({ embedded = false }) {
  const imageRef = useRef(null);
  const folderRef = useRef(null);
  const imageUrlRef = useRef('');
  const [runs, setRuns] = useState(readFinanceEvaluationRuns);
  const [dataset, setDataset] = useState([]);
  const [datasetName, setDatasetName] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [installedModels, setInstalledModels] = useState([]);
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('정답 JSON을 불러온 뒤 해당 영수증 이미지를 선택하세요.');
  const [imagePreview, setImagePreview] = useState(null);
  const [question, setQuestion] = useState('');
  const [questionLoading, setQuestionLoading] = useState(false);
  const [questionHistory, setQuestionHistory] = useState([]);
  const [pipelineProgress, setPipelineProgress] = useState(null);
  const [activeBatchId, setActiveBatchId] = useState('');
  const [batchComplete, setBatchComplete] = useState(false);
  const [queuedBatchFiles, setQueuedBatchFiles] = useState(null);

  const batchRuns = useMemo(() => activeBatchId ? runs.filter((run) => run.batch_id === activeBatchId) : [], [runs, activeBatchId]);
  const summaries = useMemo(() => models.map((model) => ({ model, ...summarize(batchRuns, model) })), [batchRuns, models]);
  const scoredSummaries = useMemo(() => {
    const measured = summaries.filter((summary) => summary.latency > 0);
    const fastest = measured.length ? Math.min(...measured.map((summary) => summary.latency)) : 0;
    return summaries.map((summary) => {
      const speedScore = fastest && summary.latency ? 3 * fastest / summary.latency : 0;
      const costScore = summary.documents ? 2 : 0;
      return {
        ...summary,
        speedScore,
        costScore,
        finalScore: summary.extractionScore + speedScore + costScore,
        qualityGate: summary.documents > 0 && summary.schemaRate >= 0.98 && summary.totalAmountRate >= 0.95,
      };
    });
  }, [summaries]);
  const latestRun = runs[runs.length - 1];

  useEffect(() => {
    if (!latestRun?.document_id || imagePreview?.name === latestRun.document_name) return undefined;
    let active = true;
    apiClient.get(`/ocr/documents/${latestRun.document_id}/file`, { responseType: 'blob' }).then(({ data }) => {
      if (!active) return;
      if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current);
      imageUrlRef.current = URL.createObjectURL(data);
      setImagePreview({ url: imageUrlRef.current, type: data.type, name: latestRun.document_name });
    }).catch(() => {});
    return () => { active = false; };
  }, [latestRun?.document_id, latestRun?.document_name, imagePreview?.name]);

  useEffect(() => () => {
    if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current);
  }, []);

  useEffect(() => {
    let active = true;
    apiClient.get('/finance-evaluations/runs').then(({ data }) => {
      if (!active || !Array.isArray(data)) return;
      const local = readFinanceEvaluationRuns();
      const persistedIds = new Set(data.map((run) => run.evaluation_id).filter(Boolean));
      setRuns(saveFinanceEvaluationRuns([
        ...local.filter((run) => !run.evaluation_id || !persistedIds.has(run.evaluation_id)),
        ...data,
      ]));
    }).catch(() => {
      // Keep the local cache available if Supabase is temporarily unavailable.
    });
    apiClient.get('/finance-evaluations/models').then(({ data }) => {
      if (!active) return;
      const available = Array.isArray(data?.models) ? data.models : [];
      setInstalledModels(available);
      const preferred = DEFAULT_MODELS.filter((model) => available.includes(model));
      setModels((current) => {
        const valid = current.filter((model) => available.includes(model));
        return valid.length ? valid : (preferred.length ? preferred : available.slice(0, 1));
      });
      if (!available.length) setStatus('Ollama에 설치된 모델이 없습니다. 먼저 모델을 설치해 주세요.');
    }).catch((error) => {
      if (active) setStatus(error.response?.data?.detail || 'Ollama 모델 목록을 불러오지 못했습니다.');
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const refreshRuns = () => setRuns(readFinanceEvaluationRuns());
    const refreshFromStorage = (event) => {
      if (!event.key || event.key === FINANCE_EVALUATION_STORAGE_KEY) refreshRuns();
    };
    window.addEventListener('finance-evaluations-updated', refreshRuns);
    window.addEventListener('storage', refreshFromStorage);
    window.addEventListener('focus', refreshRuns);
    return () => {
      window.removeEventListener('finance-evaluations-updated', refreshRuns);
      window.removeEventListener('storage', refreshFromStorage);
      window.removeEventListener('focus', refreshRuns);
    };
  }, []);

  const setModel = (index, value) => setModels((currentModels) => currentModels.map((model, modelIndex) => modelIndex === index ? value : model));
  const addModel = () => setModels((current) => {
    const next = installedModels.find((model) => !current.includes(model));
    return next && current.length < 4 ? [...current, next] : current;
  });
  const removeModel = (index) => setModels((current) => current.filter((_, modelIndex) => modelIndex !== index));

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
    setQuestionHistory([]);
    setPipelineProgress({ stage: 'ocr', document_name: file.name, ocr_text: '', ocr_pages: [] });
    const form = new FormData(); form.append('file', file);
    const { data: ocr } = await apiClient.post('/ocr/upload?processing_mode=receipt', form, { timeout: 360000 });
    setPipelineProgress({
      stage: 'llm', document_name: ocr.filename || file.name,
      ocr_text: (ocr.pages || []).map((page) => page.text || '').join('\n'), ocr_pages: ocr.pages || [],
    });
    const { data: record } = await apiClient.post('/finance/records/classify', {
      document_id: ocr.document_id,
    }, { timeout: 180000 });
    const { data: evaluation } = await apiClient.post('/finance-evaluations/record', {
      document_id: ocr.document_id,
      record_id: record.id,
      ground_truth: truthOf(matched.row),
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
    setActiveBatchId(''); setBatchComplete(false); setLoading(true);
    try {
      const matched = matchDatasetRow(file);
      setStatus(`${file.name}을 ${matched.index + 1}번 정답과 매핑했습니다. OCR 및 모델 비교 중...`);
      const entry = await evaluateFile(file, matched);
      saveRuns([...runs, entry]);
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
    let batchId = '';
    try {
      const { data: batch } = await apiClient.post('/finance-evaluations/batches', {
        batch_name: `${datasetName || 'receipt'} 일괄 평가`,
        dataset_name: datasetName || null,
        model_name: models[0],
        total_items: files.length,
        evaluation_mode: 'BULK',
      });
      batchId = batch.id;
    } catch (error) {
      setStatus(`평가 배치를 저장하지 못했습니다: ${error.response?.data?.detail || error.message}`);
      if (folderRef.current) folderRef.current.value = '';
      return;
    }
    setActiveBatchId(batchId); setBatchComplete(false); setQuestionHistory([]); setLoading(true);
    let accumulated = [...runs]; let completed = 0; const errors = [];
    for (const file of files) {
      try {
        const matched = matchDatasetRow(file);
        setStatus(`폴더 일괄 평가 ${completed + 1}/${files.length} · ${file.name}`);
        const entry = await evaluateFile(file, matched, batchId);
        accumulated = [...accumulated, entry]; saveRuns(accumulated); completed += 1;
      } catch (error) {
        errors.push(`${file.name}: ${error.response?.data?.detail || error.message || '평가 실패'}`);
      }
    }
    try {
      await apiClient.post(`/finance-evaluations/batches/${batchId}/finalize`);
    } catch (error) {
      errors.push(`배치 집계 저장: ${error.response?.data?.detail || error.message || '저장 실패'}`);
    }
    setPipelineProgress(null); setLoading(false); setBatchComplete(completed >= 2);
    setStatus(`폴더 일괄 평가 완료 · 성공 ${completed}/${files.length}${errors.length ? ` · 실패 ${errors.length}` : ''}`);
    if (folderRef.current) folderRef.current.value = '';
  };

  useEffect(() => {
    const input = peekFinanceEvaluationInput();
    if (!input?.datasetFile || !input.imageFiles?.length) return;
    let active = true;
    input.datasetFile.text().then((text) => {
      if (!active) return;
      const rows = datasetRows(JSON.parse(text));
      if (!rows.length) throw new Error('정답 항목을 찾지 못했습니다.');
      setDataset(rows);
      setDatasetName(input.datasetFile.name);
      setSelectedIndex(0);
      setQueuedBatchFiles(input.imageFiles);
      clearFinanceEvaluationInput(input);
      setStatus(`${rows.length}개 정답과 ${input.imageFiles.length}개 파일을 받았습니다. 평가 모델을 준비하는 중입니다.`);
    }).catch((error) => {
      if (active) setStatus(`정답 JSON 오류: ${error.message}`);
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!queuedBatchFiles || !dataset.length || !models.length || loading) return;
    if (models.some((model) => !installedModels.includes(model))) return;
    const files = queuedBatchFiles;
    setQueuedBatchFiles(null);
    if (files.length === 1) runEvaluation(files[0]);
    else runFolderEvaluation(files);
  }, [queuedBatchFiles, dataset.length, models, installedModels, loading]);

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
          image: run.matched_image || run.document_name,
          model: result.model_name,
          field: key,
          actual: field.actual ?? null,
          expected: field.expected ?? null,
        });
      });
    }));
    const modelStatistics = (isBatchExport ? scoredSummaries : models.map((model) => ({ model, ...summarize(selectedRuns, model) })))
      .map((summary) => ({
        model: summary.model, evaluated_documents: summary.documents, successful_documents: summary.success,
        extraction_score_95: summary.extractionScore, schema_success_rate: summary.schemaRate,
        total_amount_accuracy: summary.totalAmountRate, average_latency_ms: summary.latency,
        speed_score_3: summary.speedScore ?? null, local_cost_score_2: summary.costScore ?? null,
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

  const askAllModels = async () => {
    const value = question.trim();
    if (!latestRun || !value || questionLoading) return;
    const evaluatedModels = (latestRun.results || []).map((result) => result.model_name).filter(Boolean);
    setQuestionLoading(true);
    try {
      const { data } = await apiClient.post('/finance-evaluations/ask', {
        document_id: latestRun.document_id,
        question: value,
        model_names: evaluatedModels,
      }, { timeout: 360000 });
      setQuestionHistory((history) => [...history, { ...data, asked_at: new Date().toISOString() }]);
      setQuestion('');
    } catch (error) {
      setStatus(error.response?.data?.detail || error.message || '공통 질문 처리에 실패했습니다.');
    } finally { setQuestionLoading(false); }
  };

  return <div className={embedded ? 'finance-eval-embedded-shell' : 'app-shell finance-eval-shell'}>{!embedded && <Sidebar />}<main className={`finance-eval-page ${embedded ? 'embedded' : ''}`}>
    <header><div><p>FINANCE MODEL LAB</p><h1>영수증 서비스 결과 평가</h1><span>동일 OCR 입력으로 최종 서비스의 필드 매칭과 Excel 변환 결과를 비교합니다.</span></div><button disabled={!runs.length} onClick={exportResults}><IoDownloadOutline /> {batchComplete && batchRuns.length > 1 ? '일괄 통계 JSON' : '결과 JSON'}</button></header>
    {!embedded && <section className="eval-setup">
      <label><span>정답 데이터</span><input type="file" accept=".json,application/json" onChange={(event) => loadDataset(event.target.files?.[0])} /><small>{datasetName || 'receipt_kr.json을 선택하세요'}</small></label>
      {models.map((model, index) => <label className="model-selector" key={`${model}-${index}`}><span>평가 모델 {index + 1}</span><div><select value={model} onChange={(event) => setModel(index, event.target.value)}>{installedModels.map((name) => <option value={name} key={name} disabled={models.includes(name) && name !== model}>{name}</option>)}</select><button type="button" onClick={() => removeModel(index)} aria-label={`${model} 제거`}>×</button></div></label>)}
      {models.length < Math.min(4, installedModels.length) && <button className="add-model-button" type="button" onClick={addModel}>＋ 모델 추가</button>}
      <label><span>현재 정답 항목</span><select disabled={!dataset.length} value={selectedIndex} onChange={(event) => setSelectedIndex(Number(event.target.value))}>{dataset.map((row, index) => <option value={index} key={`${nameOf(row)}-${index}`}>{index + 1}. {nameOf(row) || `항목 ${index + 1}`}</option>)}</select></label>
      <button className="eval-upload" disabled={!dataset.length || loading || !models.length || models.some((model) => !installedModels.includes(model))} onClick={() => imageRef.current?.click()}>{loading ? '평가 실행 중...' : `${models.length || 0}개 모델로 평가 시작`}</button>
      <input ref={imageRef} hidden type="file" accept=".png,.jpg,.jpeg,.webp,.bmp,.pdf" onChange={(event) => runEvaluation(event.target.files?.[0])} />
      <button className="eval-upload batch-upload" disabled={!dataset.length || loading || !models.length || models.some((model) => !installedModels.includes(model))} onClick={() => folderRef.current?.click()}>{loading ? '일괄 평가 중...' : '프로젝트 폴더 일괄 평가'}</button>
      <input ref={folderRef} hidden type="file" accept=".png,.jpg,.jpeg,.webp,.bmp,.pdf" multiple webkitdirectory="true" directory="true" onChange={(event) => runFolderEvaluation(event.target.files)} />
      <p>{status}</p>
    </section>}

    {batchComplete && batchRuns.length > 1 && <section className="eval-summary-grid">
      {scoredSummaries.map((summary) => <article key={summary.model}>
        <small>TEST01~TEST20 선정 지표</small><h2 title={summary.model}>{summary.model}</h2>
        <strong>{summary.finalScore.toFixed(1)}점</strong><p>총점 100 · {summary.qualityGate ? '품질 게이트 통과' : '품질 게이트 재검토'}</p>
        <dl>
          <div><dt>추출 정확도</dt><dd>{summary.extractionScore.toFixed(1)} / 95</dd></div>
          <div><dt>핵심·품목·카테고리·스키마·안정성</dt><dd>{summary.documents}건</dd></div>
          <div><dt>스키마 성공률</dt><dd>{(summary.schemaRate * 100).toFixed(1)}%</dd></div>
          <div><dt>총 결제액 정확도</dt><dd>{(summary.totalAmountRate * 100).toFixed(1)}%</dd></div>
          <div><dt>평균 응답시간</dt><dd>{(summary.latency / 1000).toFixed(1)}초</dd></div>
          <div><dt>속도점수</dt><dd>{summary.speedScore.toFixed(2)} / 3</dd></div>
          <div><dt>로컬 비용점수</dt><dd>{summary.costScore.toFixed(1)} / 2</dd></div>
        </dl>
      </article>)}
    </section>}

    <section className="latest-pipeline-results"><header><div><h2>{pipelineProgress ? '현재 평가 진행 상황' : '최근 실행 결과'}</h2><p>모델별로 입력 이미지, 공통 OCR 원문, 실제 생성된 Excel을 나란히 확인합니다.</p></div><span>{pipelineProgress?.document_name || latestRun?.document_name || '평가 대기'}</span></header>{pipelineProgress ? <PipelineLoading progress={pipelineProgress} models={models} imagePreview={imagePreview} /> : latestRun ? (latestRun.results || []).map((result) => <ModelPipelineResult key={`${latestRun.evaluated_at}-${result.model_name}`} run={latestRun} result={result} imagePreview={imagePreview} />) : <p className="eval-empty">이미지를 선택해 평가하면 모델별 처리 화면이 여기에 표시됩니다.</p>}</section>

    <section className="common-question-panel"><header><div><h2>모델 공통 질문</h2><p>최근 평가 영수증의 동일한 OCR 원문과 질문을 모든 모델에 전달합니다.</p></div><span>{latestRun ? `${latestRun.results?.length || 0}개 모델` : '평가 후 사용 가능'}</span></header><div className="question-compose"><textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="예: 이 영수증의 결제 금액과 주요 구매 품목을 알려줘." disabled={!latestRun || questionLoading} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); askAllModels(); } }} /><button type="button" disabled={!latestRun || !question.trim() || questionLoading} onClick={askAllModels}>{questionLoading ? '모델 답변 생성 중...' : '모든 모델에 질문'}</button></div><div className="question-history">{questionHistory.map((entry, entryIndex) => <article key={`${entry.asked_at}-${entryIndex}`}><h3>Q. {entry.question}</h3><div>{(entry.answers || []).map((answer) => <section className={answer.success ? '' : 'answer-error'} key={answer.model_name}><header><strong>{answer.model_name}</strong><span>{(Number(answer.latency_ms || 0) / 1000).toFixed(1)}초</span></header><p>{answer.success ? answer.answer : answer.error || '답변 생성 실패'}</p></section>)}</div></article>)}{!questionHistory.length && <p className="eval-empty">평가 완료 후 질문하면 모델별 답변이 나란히 표시됩니다.</p>}</div></section>

    <section className="eval-results"><header><div><h2>누적 결과</h2><p>브라우저에 자동 저장됩니다. 같은 이미지도 실행 날짜와 시간이 다르면 별도 결과로 누적됩니다.</p></div><span>{runs.length}회</span></header>
      <div className="eval-table"><div className="eval-row eval-head"><span>데이터</span><span>모델</span><span>최종 정확도</span><span>필드 매칭</span><span>생성 Excel 문서</span><span>OCR 영향</span><span>응답시간</span></div>
      {[...runs].sort((a, b) => String(b.evaluated_at || '').localeCompare(String(a.evaluated_at || ''))).flatMap((run) => (run.results || []).map((result) => { const impact = result.system.ocr_impact; const likelyOcrErrors = impact?.counts?.LIKELY_OCR_ERROR || 0; const score = result.system.score; const workbook = result.system.workbook; return <div className="eval-row" key={`${run.dataset_name}-${run.dataset_index}-${run.evaluated_at}-${result.model_name}`}><span title={evaluatedTime(run.evaluated_at)}>{run.dataset_index + 1}. {run.document_name}<small>{evaluatedTime(run.evaluated_at)}</small></span><strong>{result.model_name}</strong><span>{(score.field_accuracy * 100).toFixed(1)}%</span><span className={score.complete_match ? 'ok' : 'bad'}>{score.correct_fields}/{score.evaluated_fields}</span><span className={workbook.success ? 'ok' : 'bad'}>{workbook.active_sheet || '생성 실패'}</span><span className={likelyOcrErrors ? 'ocr-error' : 'ok'}>{impact ? `${likelyOcrErrors}개 가능` : '-'}</span><span>{(result.latency_ms / 1000).toFixed(1)}초</span><details><summary>매칭 상세 및 OCR 영향 보기</summary><div>{Object.entries(score.fields || {}).map(([field, value]) => field === 'items' ? <p className={value.count_correct ? 'ok' : 'bad'} key={field}><b>품목 수</b><span>{value.actual_count}</span><em>정답 {value.expected_count} · 초과 {value.false_positive_count || 0}</em></p> : <p className={value.correct ? 'ok' : 'bad'} key={field}><b>{LABELS[field] || field}</b><span>{String(value.actual ?? '-')}</span><em>정답 {String(value.expected ?? '-')}</em></p>)}</div><OcrImpact impact={impact} /></details></div>; }))}
      {!runs.length && <p className="eval-empty">아직 저장된 평가 결과가 없습니다.</p>}</div>
    </section>
  </main></div>;
}
