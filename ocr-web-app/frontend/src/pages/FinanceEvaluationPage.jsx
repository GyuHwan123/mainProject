import { useMemo, useRef, useState } from 'react';
import { IoDownloadOutline } from 'react-icons/io5';

import apiClient from '../api/client';
import Sidebar from '../components/Sidebar';
import '../style/FinanceEvaluationPage.scss';

const STORAGE_KEY = 'pic_to_text_finance_evaluations_v1';
const DEFAULT_MODELS = ['gemma2:2b', 'finance-gemma2-qlora'];
const LABELS = {
  document_type: '문서 유형', expense_category: '카테고리', merchant: '상호', transaction_date: '날짜',
  supply_amount: '공급가액', tax_amount: '부가세', total_amount: '합계금액', payment_method: '결제수단',
};

function readStoredRuns() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'); } catch { return []; }
}

function datasetRows(payload) {
  if (Array.isArray(payload)) return payload;
  return payload.receipts || payload.data || payload.items || [];
}

function truthOf(row) {
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

function summarize(runs, mode, model) {
  const rows = runs.flatMap((run) => run.results || []).filter((result) => result.model_name === model);
  const scores = rows.map((result) => result[mode]?.score).filter(Boolean);
  const evaluated = scores.reduce((sum, score) => sum + Number(score.evaluated_fields || 0), 0);
  const correct = scores.reduce((sum, score) => sum + Number(score.correct_fields || 0), 0);
  return {
    documents: rows.length,
    success: rows.filter((row) => row.success).length,
    accuracy: evaluated ? correct / evaluated : 0,
    complete: scores.filter((score) => score.complete_match).length,
    latency: rows.length ? rows.reduce((sum, row) => sum + Number(row.latency_ms || 0), 0) / rows.length : 0,
  };
}

export default function FinanceEvaluationPage() {
  const imageRef = useRef(null);
  const [runs, setRuns] = useState(readStoredRuns);
  const [dataset, setDataset] = useState([]);
  const [datasetName, setDatasetName] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [models, setModels] = useState(DEFAULT_MODELS);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('정답 JSON을 불러온 뒤 해당 영수증 이미지를 선택하세요.');

  const current = dataset[selectedIndex];
  const summaries = useMemo(() => models.flatMap((model) => ['pure', 'system'].map((mode) => ({ model, mode, ...summarize(runs, mode, model) }))), [runs, models]);
  const comparison = useMemo(() => ['pure', 'system'].map((mode) => {
    const baseline = summarize(runs, mode, models[0]);
    const trained = summarize(runs, mode, models[1]);
    return { mode, baseline, trained, delta: trained.accuracy - baseline.accuracy };
  }), [runs, models]);

  const saveRuns = (next) => { setRuns(next); localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); };
  const loadDataset = async (file) => {
    if (!file) return;
    try {
      const rows = datasetRows(JSON.parse(await file.text()));
      if (!rows.length) throw new Error('정답 항목을 찾지 못했습니다.');
      setDataset(rows); setDatasetName(file.name); setSelectedIndex(0);
      setStatus(`${rows.length}개 정답을 불러왔습니다.`);
    } catch (error) { setStatus(`정답 JSON 오류: ${error.message}`); }
  };

  const runEvaluation = async (file) => {
    if (!file || !current || loading) return;
    setLoading(true); setStatus(`${file.name} OCR 및 모델 비교 중...`);
    try {
      const form = new FormData(); form.append('file', file);
      const { data: ocr } = await apiClient.post('/ocr/upload?processing_mode=receipt', form, { timeout: 360000 });
      const { data: evaluation } = await apiClient.post('/finance-evaluations/run', {
        document_id: ocr.document_id,
        ground_truth: truthOf(current),
        model_names: models,
      }, { timeout: 360000 });
      const entry = { ...evaluation, dataset_name: datasetName, dataset_index: selectedIndex, evaluated_at: new Date().toISOString() };
      const key = `${datasetName}:${selectedIndex}`;
      const next = [...runs.filter((run) => `${run.dataset_name}:${run.dataset_index}` !== key), entry];
      saveRuns(next);
      setStatus(`${file.name} 평가 완료 · ${selectedIndex + 1}/${dataset.length}`);
      if (selectedIndex < dataset.length - 1) setSelectedIndex(selectedIndex + 1);
    } catch (error) {
      setStatus(error.response?.data?.detail || error.message || '평가에 실패했습니다.');
    } finally { setLoading(false); if (imageRef.current) imageRef.current.value = ''; }
  };

  const exportResults = () => {
    const content = JSON.stringify({ exported_at: new Date().toISOString(), models, summaries, runs }, null, 2);
    const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'finance-model-evaluation.json'; anchor.click(); URL.revokeObjectURL(url);
  };

  return <div className="app-shell finance-eval-shell"><Sidebar /><main className="finance-eval-page">
    <header><div><p>FINANCE MODEL LAB</p><h1>영수증 모델 비교 평가</h1><span>동일 OCR 입력으로 순수 모델과 최종 시스템 결과를 비교합니다.</span></div><button disabled={!runs.length} onClick={exportResults}><IoDownloadOutline /> 결과 JSON</button></header>
    <section className="eval-setup">
      <label><span>정답 데이터</span><input type="file" accept=".json,application/json" onChange={(event) => loadDataset(event.target.files?.[0])} /><small>{datasetName || 'receipt_kr.json을 선택하세요'}</small></label>
      <label><span>기존 모델</span><input value={models[0]} onChange={(event) => setModels([event.target.value, models[1]])} /></label>
      <label><span>학습 모델</span><input value={models[1]} onChange={(event) => setModels([models[0], event.target.value])} /></label>
      <label><span>현재 정답 항목</span><select disabled={!dataset.length} value={selectedIndex} onChange={(event) => setSelectedIndex(Number(event.target.value))}>{dataset.map((row, index) => <option value={index} key={`${nameOf(row)}-${index}`}>{index + 1}. {nameOf(row) || `항목 ${index + 1}`}</option>)}</select></label>
      <button className="eval-upload" disabled={!current || loading || models.some((model) => !model.trim())} onClick={() => imageRef.current?.click()}>{loading ? '평가 실행 중...' : '현재 영수증 이미지 선택·평가'}</button>
      <input ref={imageRef} hidden type="file" accept=".png,.jpg,.jpeg,.webp,.bmp,.pdf" onChange={(event) => runEvaluation(event.target.files?.[0])} />
      <p>{status}</p>
    </section>

    <section className="eval-summary-grid">{summaries.map((item) => <article key={`${item.model}-${item.mode}`}><small>{item.mode === 'pure' ? 'PURE MODEL' : 'FINAL SYSTEM'}</small><h2>{item.model}</h2><strong>{(item.accuracy * 100).toFixed(1)}%</strong><p>필드 정확도 · {item.documents}건</p><dl><div><dt>완전 정답</dt><dd>{item.complete}/{item.documents}</dd></div><div><dt>호출 성공</dt><dd>{item.success}/{item.documents}</dd></div><div><dt>평균 응답</dt><dd>{(item.latency / 1000).toFixed(1)}초</dd></div></dl></article>)}</section>
    <section className="eval-comparison">{comparison.map((item) => <article key={item.mode}><div><small>{item.mode === 'pure' ? '순수 모델 비교' : '최종 시스템 비교'}</small><strong>{models[1]}</strong><span>vs {models[0]}</span></div><em className={item.delta >= 0 ? 'ok' : 'bad'}>{item.delta >= 0 ? '+' : ''}{(item.delta * 100).toFixed(1)}%p</em></article>)}</section>

    <section className="eval-results"><header><div><h2>누적 결과</h2><p>브라우저에 자동 저장됩니다. 같은 정답 항목을 다시 평가하면 최신 결과로 교체됩니다.</p></div><span>{runs.length}/{dataset.length || 17}건</span></header>
      <div className="eval-table"><div className="eval-row eval-head"><span>데이터</span><span>모델</span><span>순수</span><span>최종</span><span>Excel</span><span>응답시간</span></div>
      {[...runs].sort((a, b) => a.dataset_index - b.dataset_index).flatMap((run) => (run.results || []).map((result) => <div className="eval-row" key={`${run.dataset_name}-${run.dataset_index}-${result.model_name}`}><span>{run.dataset_index + 1}. {run.document_name}</span><strong>{result.model_name}</strong><span>{(result.pure.score.field_accuracy * 100).toFixed(1)}%</span><span>{(result.system.score.field_accuracy * 100).toFixed(1)}%</span><span className={result.system.workbook.success ? 'ok' : 'bad'}>{result.system.workbook.success ? '정상' : '실패'}</span><span>{(result.latency_ms / 1000).toFixed(1)}초</span><details><summary>필드 비교</summary><div>{Object.entries(result.system.score.fields || {}).map(([field, value]) => field === 'items' ? <p className={value.actual_count === value.expected_count ? 'ok' : 'bad'} key={field}><b>품목 수</b><span>{value.actual_count}</span><em>정답 {value.expected_count}</em></p> : <p className={value.correct ? 'ok' : 'bad'} key={field}><b>{LABELS[field] || field}</b><span>{String(value.actual ?? '-')}</span><em>정답 {String(value.expected ?? '-')}</em></p>)}</div></details></div>))}
      {!runs.length && <p className="eval-empty">아직 저장된 평가 결과가 없습니다.</p>}</div>
    </section>
  </main></div>;
}
