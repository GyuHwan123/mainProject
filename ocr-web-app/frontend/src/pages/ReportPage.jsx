import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { IoDownloadOutline, IoRefreshOutline } from 'react-icons/io5';
import { useLocation, useNavigate } from 'react-router-dom';
import Sidebar from '../components/Sidebar';
import apiClient from '../api/client';
import { getAppUser } from '../features/appSession';
import FinanceEvaluationPage from './FinanceEvaluationPage';
import '../style/ReportPage.scss';

const percent = (value, digits = 1) => `${((value || 0) * 100).toFixed(digits)}%`;
const RAG_EVALUATION_STORAGE_KEY = 'pic_to_text_rag_evaluation_latest';
const RAG_LLM_EVALUATION_STORAGE_KEY = 'pic_to_text_rag_llm_evaluation_latest';

function ReportDropdown({ value, options, onChange, disabled = false, ariaLabel, onFocus, prefix }) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef(null);
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === value));
  const selectedOption = options[selectedIndex] || options[0];

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutsideClick = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', closeOnOutsideClick);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [open]);

  const openDropdown = () => {
    if (disabled) return;
    setActiveIndex(selectedIndex);
    setOpen(true);
  };
  const selectOption = (option) => {
    if (option?.disabled) return;
    onChange(option.value);
    setOpen(false);
  };
  const handleKeyDown = (event) => {
    if (disabled) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) { openDropdown(); return; }
      const direction = event.key === 'ArrowDown' ? 1 : -1;
      setActiveIndex((current) => (current + direction + options.length) % options.length);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (open) selectOption(options[activeIndex]);
      else openDropdown();
    } else if (event.key === 'Escape') {
      setOpen(false);
    }
  };

  return <div ref={rootRef} className={`report-dropdown light ${open ? 'open' : ''}`}>
    <button type="button" className="report-dropdown-trigger" disabled={disabled} aria-label={ariaLabel} aria-haspopup="listbox" aria-expanded={open} onFocus={onFocus} onClick={() => open ? setOpen(false) : openDropdown()} onKeyDown={handleKeyDown}>
      {prefix && <span className="report-dropdown-prefix">{prefix}</span>}<span className="report-dropdown-value" title={selectedOption?.label}>{selectedOption?.label}</span><i aria-hidden="true" />
    </button>
    {open && <div className="report-dropdown-menu" role="listbox" aria-label={ariaLabel}>
      {options.map((option, index) => <button type="button" role="option" aria-selected={option.value === value} className={`${option.value === value ? 'selected' : ''} ${index === activeIndex ? 'focused' : ''}`} disabled={option.disabled} key={option.value || option.label} onMouseEnter={() => setActiveIndex(index)} onClick={() => selectOption(option)}>{option.label}</button>)}
    </div>}
  </div>;
}

function RagPerformanceReport({ evaluation, modelConfig, umapData, umapError }) {
  const metrics = useMemo(() => {
    if (!evaluation) return null;
    const cases = Array.isArray(evaluation.cases) ? evaluation.cases : [];
    const summary = evaluation.summary || {};
    const retrievalCases = cases.filter((item) => Array.isArray(item.expected_documents) && item.expected_documents.length);
    const hitAt = (k) => retrievalCases.length
      ? retrievalCases.filter((item) => {
        const expected = new Set(item.expected_documents || []);
        return (item.retrieved_documents || []).slice(0, k).some((docId) => expected.has(docId));
      }).length / retrievalCases.length
      : null;
    return {
      total: Number(summary.total || cases.length),
      hitAt1: summary.hit_at_1 ?? hitAt(1),
      hitAt3: summary.hit_at_3 ?? hitAt(3),
      hitAt4: summary.hit_at_4 ?? hitAt(4),
      recall: summary.recall_at_k,
      mrr: summary.mrr,
      contextPrecision: summary.context_precision ?? summary.citation_accuracy,
      answerAccuracy: summary.answer_accuracy,
      faithfulness: summary.faithfulness,
      hallucinationRate: summary.hallucination_rate,
      faithfulnessMethod: summary.faithfulness_method,
    };
  }, [evaluation]);
  const metricValue = (value) => value == null ? '—' : percent(value);
  const retrievalMetrics = [
    ['Hit@1', metrics?.hitAt1], ['Hit@3', metrics?.hitAt3], ['Hit@4', metrics?.hitAt4],
    ['Recall@K', metrics?.recall], ['MRR', metrics?.mrr], ['Context Precision', metrics?.contextPrecision],
  ];
  const answerMetrics = [
    ['Answer Accuracy', metrics?.answerAccuracy], ['Faithfulness', metrics?.faithfulness],
    ['Hallucination Rate', metrics?.hallucinationRate],
  ];
  const questionTypeMetrics = useMemo(() => {
    if (!evaluation || !Array.isArray(evaluation.cases)) return [];
    const labels = {
      single_document_fact: '단일 문서 사실 검색',
      paraphrase_semantic: '의미 변형 질문',
      confusable_reranker: 'Reranker 혼동 질문',
      multi_document: '다중 문서 질문',
      unanswerable: '답변 불가 질문',
    };
    const order = Object.keys(labels);
    const grouped = evaluation.cases.reduce((result, item) => {
      const type = item.question_type || 'unspecified';
      if (!result[type]) result[type] = [];
      result[type].push(item);
      return result;
    }, {});
    return Object.entries(grouped)
      .sort(([left], [right]) => {
        const leftIndex = order.indexOf(left); const rightIndex = order.indexOf(right);
        return (leftIndex < 0 ? order.length : leftIndex) - (rightIndex < 0 ? order.length : rightIndex);
      })
      .map(([type, cases]) => {
        const correctness = cases.map((item) => typeof item.answer_correct === 'boolean'
          ? Number(item.answer_correct)
          : (item.answerable === false && typeof item.rejected === 'boolean' ? Number(item.rejected) : null))
          .filter((value) => value != null);
        const hitValues = cases.map((item) => typeof item.hit === 'boolean' ? Number(item.hit) : null).filter((value) => value != null);
        const mrrValues = cases.map((item) => Number(item.reciprocal_rank)).filter(Number.isFinite);
        const average = (values) => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
        return { type, label: labels[type] || type, count: cases.length, accuracy: average(correctness), hit: average(hitValues), mrr: average(mrrValues) };
      });
  }, [evaluation]);
  return <section className="rag-performance-report">
    <article className="report-card rag-model-card"><header><div><h2>RAG 운영 설정</h2><p>Backend 상태 API가 반환한 현재 실제 구성</p></div><span>{modelConfig.ready ? 'ONLINE' : 'OFFLINE'}</span></header><div className="rag-model-grid">
      <div><span>Embedding Model</span><strong>{modelConfig.embedding_model || '—'}</strong><small>{modelConfig.embedding_dimensions ? `${modelConfig.embedding_dimensions} dimensions` : '차원 미설정'}</small></div>
      <div><span>Reranker</span><strong>{modelConfig.rerank_model || '미사용'}</strong><small>Vector 후보 재정렬</small></div>
      <div><span>LLM</span><strong>{modelConfig.model || '—'}</strong><small>최종 답변 생성</small></div>
      <div><span>Query Rewriting</span><strong>{modelConfig.query_rewriting ? '사용' : '미사용'}</strong><small>현재 검색 경로 기준</small></div>
      <div><span>Top-K</span><strong>{modelConfig.top_k ?? '—'}</strong><small>최종 Context 청크 수</small></div>
    </div></article>

    <section className="rag-metric-columns">
      <article className="report-card rag-metric-card"><header><div><h2>검색 성능</h2><p>정답 문서 ID 기준 document-level 평가</p></div><span>{metrics ? `${metrics.total} CASES` : 'NO DATA'}</span></header><div>{retrievalMetrics.map(([label, value]) => <section key={label}><span>{label}</span><strong>{metricValue(value)}</strong></section>)}</div></article>
      <article className="report-card rag-metric-card answer"><header><div><h2>답변 성능</h2><p>정답 유사도와 검색 Context 근거성 기준</p></div><span>{metrics ? 'ACTUAL' : 'NO DATA'}</span></header><div>{answerMetrics.map(([label, value]) => <section key={label}><span>{label}</span><strong>{metricValue(value)}</strong></section>)}</div>{metrics?.faithfulnessMethod && <footer>Faithfulness: {metrics.faithfulnessMethod}</footer>}</article>
    </section>

    <section className="rag-insight-columns">
      <article className="report-card rag-umap-card"><header><div><h2>BGE-M3 Embedding UMAP</h2><p>현재 Supabase 기업 공용문서 embedding 기준</p></div><span>CURRENT CORPUS</span></header>{umapData?.image_data_url ? <div><img src={umapData.image_data_url} alt="현재 기업 RAG corpus BGE-M3 임베딩 UMAP" /><p>기업문서 {umapData.document_count}개 · {umapData.chunk_count} chunks · {umapData.input_shape?.join(' × ')} → {umapData.output_shape?.join(' × ')}</p></div> : <div className="model-evaluation-empty"><strong>현재 corpus UMAP을 생성할 수 없습니다.</strong><p>{umapError || 'UMAP 데이터를 불러오는 중입니다.'}</p></div>}</article>
      <article className="report-card rag-type-card"><header><div><h2>문항 유형별 성능</h2><p>각 문항 유형별 최종 답변 정확도</p></div><span>{questionTypeMetrics.length ? `${questionTypeMetrics.length} TYPES` : 'NO DATA'}</span></header>{questionTypeMetrics.length ? <div className="rag-type-bars">{questionTypeMetrics.map((item) => <section key={item.type} title={`${item.label} · ${item.count}문항 · Hit@K ${metricValue(item.hit)} · MRR ${metricValue(item.mrr)}`}><div><strong>{item.label}</strong><span>{item.count}문항</span></div><i><b style={{ width: item.accuracy == null ? '0%' : `${Math.max(0, Math.min(100, item.accuracy * 100))}%` }} /></i><em>{metricValue(item.accuracy)}</em><small>Hit@K {metricValue(item.hit)} · MRR {metricValue(item.mrr)}</small></section>)}</div> : <div className="model-evaluation-empty"><strong>평가 결과 없음</strong><p>문항별 question_type 평가 결과가 필요합니다.</p></div>}</article>
    </section>
    {!evaluation && <p className="rag-report-empty">ChatPage에서 RAG 평가를 완료하면 실제 결과가 이 영역에 표시됩니다.</p>}
  </section>;
}

function RagLlmEvaluation() {
  const fileRef = useRef(null);
  const runningRef = useRef(false);
  const [dataset, setDataset] = useState(null);
  const [fileName, setFileName] = useState('');
  const [models, setModels] = useState([]);
  const [modelName, setModelName] = useState('');
  const [status, setStatus] = useState({ status: 'idle', current: 0, total: 0, question_id: null });
  const [error, setError] = useState('');
  const [result, setResult] = useState(() => {
    try { return JSON.parse(localStorage.getItem(RAG_LLM_EVALUATION_STORAGE_KEY) || 'null'); } catch { return null; }
  });

  useEffect(() => {
    apiClient.get('/rag/evaluation/llm/models').then(({ data }) => {
      const installed = Array.isArray(data.models) ? data.models : [];
      setModels(installed);
      setModelName(data.default_model || installed[0] || '');
    }).catch((requestError) => setError(requestError.response?.data?.detail || 'Ollama 설치 모델을 조회할 수 없습니다.'));
    apiClient.get('/rag/evaluation/llm/status').then(({ data }) => {
      if (data.status === 'running') setStatus(data);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (status.status !== 'running') return undefined;
    const poller = window.setInterval(() => {
      apiClient.get('/rag/evaluation/llm/status').then(({ data }) => {
        setStatus(data);
        if (data.status === 'completed' && data.result) {
          setResult(data.result);
          localStorage.setItem(RAG_LLM_EVALUATION_STORAGE_KEY, JSON.stringify(data.result));
        }
      }).catch(() => {});
    }, 800);
    return () => window.clearInterval(poller);
  }, [status.status]);

  const loadDataset = async (file) => {
    if (runningRef.current) return;
    setError('');
    try {
      if (!file || !/\.json$/i.test(file.name)) throw new Error('기존 RAG 평가 JSON 파일만 업로드할 수 있습니다.');
      const parsed = JSON.parse(await file.text());
      if (!parsed.dataset_name || !Array.isArray(parsed.cases) || !parsed.cases.length) throw new Error('dataset_name과 cases가 필요합니다.');
      if (Number(parsed.question_count) !== parsed.cases.length) throw new Error('question_count와 cases 개수가 일치하지 않습니다.');
      parsed.cases.forEach((item, index) => {
        const label = item?.question_id || `${index + 1}번 문항`;
        if (typeof item?.question !== 'string' || !item.question.trim()) throw new Error(`${label}: question이 필요합니다.`);
        if (!Array.isArray(item.expected_documents)) throw new Error(`${label}: expected_documents 배열이 필요합니다.`);
        if (typeof item.expected_answer !== 'string') throw new Error(`${label}: expected_answer가 필요합니다.`);
        if (typeof item.answerable !== 'boolean') throw new Error(`${label}: answerable은 boolean이어야 합니다.`);
      });
      setDataset(parsed); setFileName(file.name); setStatus({ status: 'ready', current: 0, total: parsed.cases.length, question_id: null });
    } catch (loadError) {
      setDataset(null); setFileName(''); setError(loadError.message || '평가 JSON을 읽을 수 없습니다.');
      setStatus({ status: 'error', current: 0, total: 0, question_id: null });
    }
  };

  const runEvaluation = async () => {
    if (!dataset || !modelName || runningRef.current) return;
    runningRef.current = true;
    setError(''); setResult(null);
    setStatus({ status: 'running', current: 0, total: dataset.cases.length, question_id: null });
    try {
      const { data } = await apiClient.post('/rag/evaluation/llm/run', { dataset, model_name: modelName }, { timeout: 3600000 });
      const saved = { ...data, dataset_file_name: fileName };
      setResult(saved); localStorage.setItem(RAG_LLM_EVALUATION_STORAGE_KEY, JSON.stringify(saved));
      setStatus({ status: 'completed', current: data.summary.total, total: data.summary.total, question_id: null });
    } catch (requestError) {
      const detail = requestError.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'LLM 평가 실행에 실패했습니다.');
      setStatus((current) => ({ ...current, status: 'error' }));
    } finally {
      runningRef.current = false;
    }
  };

  const running = status.status === 'running';
  const summary = result?.summary;
  const metric = (value) => value == null ? '계산 불가' : `${(value * 100).toFixed(1)}%`;
  const scoreComponents = summary ? [
    { label: 'Answer Accuracy', value: summary.answer_accuracy, weight: 40 },
    { label: 'Faithfulness', value: summary.faithfulness, weight: 25 },
    { label: 'Answer Relevancy', value: summary.answer_relevancy, weight: 20 },
    { label: 'No-answer Accuracy', value: summary.no_answer_accuracy, weight: 15 },
  ] : [];
  const stateLabel = { idle: '업로드 대기', ready: '평가 준비', running: '평가 실행 중', completed: '평가 완료', error: '오류' }[status.status] || status.status;
  return <article className="report-card rag-llm-evaluation-card">
    <header><div><h2>LLM 성능 평가</h2><p>동일한 RAG 검색·Context·Prompt 조건에서 설치된 Ollama 모델을 비교합니다.</p></div><span>{stateLabel}</span></header>
    <div className="rag-llm-controls">
      <input ref={fileRef} hidden type="file" accept=".json,application/json" disabled={running} onChange={(event) => { loadDataset(event.target.files?.[0]); event.target.value = ''; }} />
      <button type="button" disabled={running} onClick={() => fileRef.current?.click()}>{running ? '업로드 잠김' : '정답 데이터 업로드'}</button>
      <div className="rag-llm-file"><strong>{fileName || '선택된 파일 없음'}</strong><span>{dataset ? `${dataset.cases.length}문항 · 업로드 완료` : '기존 RAG 평가 JSON을 선택하세요.'}</span></div>
      <div className="rag-llm-model-select"><span>평가 모델 선택</span><ReportDropdown ariaLabel="평가 모델 선택" value={modelName} disabled={running || !models.length} options={models.length ? models.map((model) => ({ value: model, label: model })) : [{ value: '', label: '설치 모델 없음', disabled: true }]} onChange={setModelName} /></div>
      <button type="button" className="run" disabled={!dataset || !modelName || running} onClick={runEvaluation}>{running ? '평가 실행 중...' : '평가 시작'}</button>
    </div>
    <div className="rag-llm-progress"><div><strong>{stateLabel}</strong><span>{running ? `${status.current} / ${status.total}${status.question_id ? ` · ${status.question_id}` : ''}` : `${status.current || 0} / ${status.total || dataset?.cases.length || 0}`}</span></div><i><b style={{ width: `${status.total ? Math.min(100, status.current / status.total * 100) : 0}%` }} /></i></div>
    {error && <p className="rag-llm-error" role="alert">{error}</p>}
    {summary && <>
      <section className="rag-llm-summary"><div className="score"><span>최종 LLM 성능 점수</span><strong>{Number(summary.final_score).toFixed(1)}점</strong><small>Answer 40 · Faithfulness 25 · Relevancy 20 · No-answer 15</small></div>{[
        ['Answer Accuracy', summary.answer_accuracy], ['Faithfulness', summary.faithfulness],
        ['Answer Relevancy', summary.answer_relevancy], ['Hallucination Rate', summary.hallucination_rate],
        ['No-answer Accuracy', summary.no_answer_accuracy],
      ].map(([label, value]) => <div key={label}><span>{label}</span><strong>{metric(value)}</strong></div>)}</section>
      <section className="rag-llm-score-breakdown">
        <header><div><strong>종합점수 계산 내역</strong><span>각 지표의 원점수에 확정 가중치를 적용한 합계입니다.</span></div><b>{Number(summary.final_score).toFixed(1)}점</b></header>
        <div>{scoreComponents.map((item) => <article key={item.label}><strong>{item.label}</strong><dl><div><dt>원점수</dt><dd>{metric(item.value)}</dd></div><div><dt>가중치</dt><dd>{item.weight}%</dd></div><div><dt>반영 점수</dt><dd>{(Number(item.value || 0) * item.weight).toFixed(1)}점</dd></div></dl></article>)}</div>
        <footer>{scoreComponents.map((item) => `${(Number(item.value || 0) * 100).toFixed(1)}% × ${item.weight}%`).join(' + ')} = <strong>{Number(summary.final_score).toFixed(1)}점</strong></footer>
      </section>
      <div className="rag-llm-meta"><span>모델 <strong>{result.model_name}</strong></span><span>파일 <strong>{result.dataset_file_name || result.dataset_name}</strong></span><span>총 {summary.total}문항</span><span>평균 Latency <strong>{(summary.average_latency_ms / 1000).toFixed(2)}s</strong></span><span>Output Tokens <strong>{summary.total_output_tokens}</strong></span></div>
      <div className="rag-llm-table-wrap"><div className="rag-llm-table"><div className="head"><span>문항</span><span>Answer Accuracy</span><span>Faithfulness</span><span>Relevancy</span><span>Hallucination</span><span>No-answer</span><span>Context Precision</span><span>Latency</span><span>Tokens</span></div>{result.cases.map((item) => <div key={item.question_id}><strong>{item.question_id}</strong><span>{metric(item.answer_accuracy)}</span><span>{metric(item.faithfulness)}</span><span>{metric(item.answer_relevancy)}</span><span>{metric(item.hallucination_rate)}</span><span>{item.no_answer_correct == null ? '—' : item.no_answer_correct ? '100.0%' : '0.0%'}</span><span>{metric(item.context_utilization)}</span><span>{(item.latency_ms / 1000).toFixed(2)}s</span><span>{item.output_token_count}</span></div>)}</div></div>
    </>}
  </article>;
}

function BusinessReport({ stats, loading }) {
  const estimatedMinutes = stats.documentCount * 12;
  const readyRate = stats.ragCount ? Math.round(stats.readyRagCount / stats.ragCount * 100) : 0;
  return <>
    <section className="business-kpi-grid">
      <article><small>처리한 전체 문서</small><strong>{loading ? '—' : stats.documentCount}<em>건</em></strong><p>OCR 및 RAG 등록 문서</p></article>
      <article><small>검색 준비된 지식</small><strong>{loading ? '—' : stats.readyRagCount}<em>건</em></strong><p>RAG 검색 가능 상태</p></article>
      <article><small>AI 업무 대화</small><strong>{loading ? '—' : stats.sessionCount}<em>건</em></strong><p>저장된 채팅 세션</p></article>
      <article><small>예상 절약 시간</small><strong>{loading ? '—' : Math.round(estimatedMinutes / 60 * 10) / 10}<em>시간</em></strong><p>문서당 수작업 12분 기준</p></article>
    </section>
    <section className="business-main-grid">
      <article className="report-card utilization-card"><header><div><h2>AI 업무 활용 현황</h2><p>내 계정의 실제 문서 및 대화 기록 기준</p></div><span>WORKSPACE</span></header><div className="utilization-bars">
        {[['문서 처리', stats.documentCount, Math.max(stats.documentCount, 10)], ['RAG 지식화', stats.readyRagCount, Math.max(stats.ragCount, 1)], ['AI 대화', stats.sessionCount, Math.max(stats.sessionCount, 10)], ['지식 바구니', stats.scrapCount, Math.max(stats.scrapCount, 10)]].map(([label, value, max]) => <div key={label}><span>{label}</span><i><b style={{ width: `${Math.min(100, value / max * 100)}%` }} /></i><strong>{value}건</strong></div>)}
      </div></article>
      <article className="report-card knowledge-health"><header><div><h2>지식 검색 준비 상태</h2><p>업로드한 RAG 문서의 처리 현황</p></div></header><div className="health-score"><strong style={{ '--score': `${readyRate}%` }}>{readyRate}%</strong><span>검색 준비율</span></div><ul><li><span>전체 RAG 문서</span><b>{stats.ragCount}건</b></li><li><span>검색 준비 완료</span><b>{stats.readyRagCount}건</b></li><li><span>확인 필요</span><b>{Math.max(0, stats.ragCount - stats.readyRagCount)}건</b></li></ul></article>
    </section>
    <section className="business-bottom-grid">
      <article className="report-card recent-business-docs"><header><div><h2>최근 처리 문서</h2><p>가장 최근에 등록한 업무 문서</p></div><span>{stats.recentDocuments.length} DOCS</span></header><div>{stats.recentDocuments.map((document) => <div key={document.id || document.document_id}><strong>{document.file_name || document.name || '문서'}</strong><span>{document.status || 'COMPLETED'}</span><small>{document.created_at ? new Date(document.created_at).toLocaleString('ko-KR') : '최근 등록'}</small></div>)}{!stats.recentDocuments.length && <p className="empty-report-row">처리한 문서가 아직 없습니다.</p>}</div></article>
      <article className="report-card business-guide"><header><div><h2>이번 달 활용 제안</h2><p>업무 효율을 높이기 위한 추천</p></div></header><ul><li>자주 찾는 사내 규정을 RAG 문서로 등록해 보세요.</li><li>유용한 AI 답변은 지식 바구니에 저장할 수 있습니다.</li><li>검색 준비가 안 된 문서는 다시 업로드해 상태를 확인하세요.</li></ul></article>
    </section>
  </>;
}

const MONITORING_METRICS = [
  { key: 'field_accuracy', label: '필드 정확도 (Field Accuracy)', color: '#1767df', type: 'percent', description: '평가한 전체 필드 중 정답과 일치한 필드의 비율입니다. 높을수록 좋습니다.' },
  { key: 'amount_accuracy', label: '금액 정확도 (Amount Accuracy)', color: '#079b62', type: 'percent', description: '영수증의 최종 총금액이 정답 데이터와 정확히 일치한 비율입니다. 높을수록 좋습니다.' },
  { key: 'perfect_receipt_rate', label: '완전 성공률 (Perfect Receipt)', color: '#7c3aed', type: 'percent', description: '기본 필드와 모든 품목 필드가 하나도 틀리지 않은 영수증의 비율입니다. 필드 하나라도 틀리면 실패로 계산합니다.' },
  { key: 'processing_success_rate', label: '처리 성공률 (Processing Success)', color: '#f06a13', type: 'percent', description: '요청된 영수증 중 전체 평가 파이프라인을 정상 완료한 비율입니다. 높을수록 좋습니다.' },
  { key: 'average_latency_ms', label: '평균 처리시간 (Avg Latency)', color: '#24599b', type: 'latency', description: '정상 처리된 영수증의 평균 처리 소요 시간입니다. 낮을수록 빠르고 좋습니다.' },
  { key: 'ocr_success_rate', label: 'OCR 성공률', color: '#079b62', type: 'percent', description: '전체 처리 요청 중 OCR 단계에서 실패하지 않은 비율입니다. 높을수록 좋습니다.' },
];

const dateInputValue = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const metricText = (value, type) => value == null ? '—' : type === 'latency' ? `${(value / 1000).toFixed(1)} sec` : `${(value * 100).toFixed(1)}%`;
const metricDelta = (current, previous, type) => {
  if (current == null || previous == null) return null;
  return type === 'latency' ? (current - previous) / 1000 : (current - previous) * 100;
};
const deltaText = (delta, type) => delta == null ? '—' : `${delta > 0 ? '↑' : delta < 0 ? '↓' : '−'} ${Math.abs(delta).toFixed(1)}${type === 'latency' ? ' sec' : '%p'}`;
const chartPoints = (values, width, height, maxValue = 1) => {
  const valid = values.map((value, index) => value == null ? null : { value, index }).filter(Boolean);
  return valid.map(({ value, index }) => `${values.length === 1 ? width / 2 : index * width / (values.length - 1)},${height - Math.min(value / maxValue, 1) * height}`).join(' ');
};

function MetricSparkline({ values, color, type }) {
  const normalized = type === 'latency' ? values.map((value) => value == null ? null : value / 1000) : values;
  const maxValue = type === 'latency' ? Math.max(...normalized.filter((value) => value != null), 1) : 1;
  const points = chartPoints(normalized, 220, 34, maxValue);
  return <svg className="metric-sparkline" viewBox="0 0 220 38" preserveAspectRatio="none" aria-hidden="true">
    <line x1="0" y1="36" x2="220" y2="36" />
    {points && <><polyline points={points} style={{ stroke: color }} />{points.split(' ').map((point, index) => { const [cx, cy] = point.split(','); return <circle key={`${cx}-${index}`} cx={cx} cy={cy} r="2" style={{ fill: color }} />; })}</>}
  </svg>;
}

function DailyPerformanceChart({ daily }) {
  const series = MONITORING_METRICS.slice(0, 4);
  const width = 620; const height = 190; const top = 10; const plotHeight = 150;
  return <div className="daily-performance-chart">
    <div className="daily-chart-legend">{series.map((metric) => <span key={metric.key}><i style={{ background: metric.color }} />{metric.label.split(' (')[0]}</span>)}</div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="일별 영수증 성능 추세">
      {[0, .25, .5, .75, 1].map((tick) => <g key={tick}><line x1="42" y1={top + plotHeight * (1 - tick)} x2="610" y2={top + plotHeight * (1 - tick)} /><text x="35" y={top + plotHeight * (1 - tick) + 3}>{Math.round(tick * 100)}%</text></g>)}
      {series.map((metric) => {
        const values = daily.map((day) => day[metric.key]);
        const points = chartPoints(values, 568, plotHeight, 1).split(' ').filter(Boolean).map((point) => { const [x, y] = point.split(',').map(Number); return `${x + 42},${y + top}`; }).join(' ');
        return points ? <g className="daily-chart-series" key={metric.key}><polyline points={points} style={{ stroke: metric.color }} />{points.split(' ').map((point, index) => { const [cx, cy] = point.split(','); return <circle key={index} cx={cx} cy={cy} r="2.5" style={{ fill: metric.color }} />; })}</g> : null;
      })}
      {daily.map((day, index) => <text className="date-label" key={day.date} x={daily.length === 1 ? 326 : 42 + index * 568 / (daily.length - 1)} y="181">{day.date.slice(5)}</text>)}
    </svg>
  </div>;
}

const ERROR_META = {
  OCR_ERROR: ['OCR 오류', '#2f75dd'], CANDIDATE_ERROR: ['품목 누락', '#f4aa00'],
  LLM_ERROR: ['환각 / JSON 오류', '#7652d6'], VALIDATION_ERROR: ['검증 오류', '#11a167'],
  PIPELINE_ERROR: ['파이프라인 오류', '#ef5b2a'], UNKNOWN: ['기타 오류', '#8a98aa'],
};
const FIELD_LABELS = {
  merchant: '상호명', transaction_date: '거래일자', total_amount: '총금액', supply_amount: '공급가액',
  tax_amount: '부가세', payment_method: '결제수단', card_number: '영수증/거래번호', expense_category: '비용 구분',
  total_quantity: '총수량', 'items.count': '품목 수', 'items.name': '품목명', 'items.quantity': '수량',
  'items.unit_price': '단가', 'items.total_amount': '품목 금액',
};

function ErrorDistribution({ details }) {
  const rows = details?.error_distribution || [];
  let offset = 0;
  const gradient = rows.length ? `conic-gradient(${rows.map((row) => { const start = offset; offset += row.rate * 100; return `${(ERROR_META[row.category] || ERROR_META.UNKNOWN)[1]} ${start}% ${offset}%`; }).join(', ')})` : '#edf2f6';
  return rows.length ? <div className="error-distribution"><div className="error-donut" style={{ background: gradient }}><span>총 오류<strong>{details.total_errors.toLocaleString()}건</strong></span></div><div className="error-legend">{rows.map((row) => { const meta = ERROR_META[row.category] || [row.category, ERROR_META.UNKNOWN[1]]; return <div key={row.category}><i style={{ background: meta[1] }} /><span>{meta[0]}</span><strong>{row.count} ({(row.rate * 100).toFixed(1)}%)</strong></div>; })}</div></div> : <div className="empty-monitoring-box"><span>집계된 오류가 없습니다.</span></div>;
}

function FieldAccuracyList({ details, previousDetails }) {
  const previous = new Map((previousDetails?.field_accuracy || []).map((row) => [row.field, row.accuracy]));
  const rows = (details?.field_accuracy || []).slice(0, 10);
  return rows.length ? <div className="field-accuracy-list">{rows.map((row) => { const delta = previous.has(row.field) ? (row.accuracy - previous.get(row.field)) * 100 : null; return <div key={row.field}><span>{FIELD_LABELS[row.field] || row.field}</span><strong>{(row.accuracy * 100).toFixed(1)}%</strong><i><b style={{ width: `${row.accuracy * 100}%` }} /></i><em className={delta == null ? 'empty' : delta >= 0 ? 'up' : 'down'}>{delta == null ? '—' : `${delta >= 0 ? '↑' : '↓'} ${Math.abs(delta).toFixed(1)}%p`}</em></div>; })}</div> : <div className="empty-monitoring-box"><span>필드 평가 데이터가 없습니다.</span></div>;
}

function SystemPerformance({ system }) {
  const rows = [
    ['평균 처리시간', metricText(system?.average_latency_ms, 'latency')],
    ['P95 처리시간', metricText(system?.p95_latency_ms, 'latency')],
    ['타임아웃 발생', `${system?.timeout_count || 0}건`], ['OCR 실패', `${system?.ocr_failure_count || 0}건`],
    ['LLM JSON 실패', `${system?.llm_json_failure_count || 0}건`], ['전체 처리 건수', `${(system?.total_count || 0).toLocaleString()}건`],
  ];
  return <div className="system-performance-list">{rows.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>;
}

function RecentRuns({ runs }) {
  return <div className="recent-runs-scroll"><div className="recent-runs-table"><div className="run-table-head"><span>일시</span><span>모델</span><span>처리 수</span><span>필드 정확도</span><span>완전 성공률</span><span>처리 성공률</span><span>평균 처리시간</span></div>{runs.map((run) => { const summary = run.summary_metrics || {}; const total = Number(summary.requested_count ?? run.total_items ?? 0); const success = Number(summary.successful_count ?? run.completed_items ?? 0); return <div key={run.id}><span>{run.created_at ? new Date(run.created_at).toLocaleString('ko-KR') : '—'}</span><span title={run.model_name}>{run.model_name || 'gemma3-4b-trained'}</span><strong>{total.toLocaleString()}</strong><span>{metricText(summary.average_field_accuracy, 'percent')}</span><span>{metricText(summary.complete_match_rate, 'percent')}</span><span>{metricText(total ? success / total : null, 'percent')}</span><span>{metricText(summary.average_latency_ms, 'latency')}</span></div>; })}{!runs.length && <p>선택한 기간의 실행 이력이 없습니다.</p>}</div></div>;
}

function ReceiptMonitoringDashboard({ onExportPdf }) {
  const initialEndDate = new Date();
  const initialStartDate = new Date(initialEndDate); initialStartDate.setDate(initialEndDate.getDate() - 6);
  const [dateRange, setDateRange] = useState({ startDate: dateInputValue(initialStartDate), endDate: dateInputValue(initialEndDate) });
  const [period, setPeriod] = useState('7');
  const [monitoring, setMonitoring] = useState({ summary: {}, details: {}, comparison: { summary: {}, details: {} }, recent_runs: [], daily: [] });
  const [monitoringLoading, setMonitoringLoading] = useState(true);
  const [monitoringError, setMonitoringError] = useState('');
  const monitoringQueryParams = useMemo(() => ({
    start_date: dateRange.startDate,
    end_date: dateRange.endDate,
  }), [dateRange]);
  const periodLabel = period === 'custom' ? '사용자 지정' : `최근 ${period}일`;

  useEffect(() => {
    if (!dateRange.startDate || !dateRange.endDate || dateRange.startDate > dateRange.endDate) return undefined;
    let active = true;
    setMonitoringLoading(true); setMonitoringError('');
    apiClient.get('/finance-evaluations/monitoring', { params: monitoringQueryParams }).then(({ data }) => {
      if (active) setMonitoring(data);
    }).catch((requestError) => {
      if (active) setMonitoringError(requestError.response?.data?.detail || '모니터링 데이터를 불러오지 못했습니다.');
    }).finally(() => { if (active) setMonitoringLoading(false); });
    return () => { active = false; };
  }, [monitoringQueryParams]);

  const changeDate = (field, value) => {
    setDateRange((current) => ({ ...current, [field]: value }));
    setPeriod('custom');
  };

  const changePeriod = (value) => {
    if (value === 'custom') {
      setPeriod(value);
      return;
    }
    const endDate = new Date();
    const startDate = new Date(endDate);
    startDate.setDate(endDate.getDate() - Number(value) + 1);
    setDateRange({ startDate: dateInputValue(startDate), endDate: dateInputValue(endDate) });
    setPeriod(value);
  };
  const comparisonLabel = period === 'custom' ? '이전 동일 기간' : `지난 ${period}일`;

  return <section className="receipt-monitoring" data-start-date={monitoringQueryParams.start_date} data-end-date={monitoringQueryParams.end_date}>
    <div className="receipt-monitoring-heading">
      <div><p>FINANCE MODEL LAB</p><h2>영수증 서비스 성능 모니터링 대시보드</h2><span>서비스 운영 데이터를 기반으로 모델/파이프라인의 성능을 모니터링합니다.</span></div>
      <div className="receipt-monitoring-filters" aria-label="영수증 성능 필터">
        <div className="receipt-date-range">
          <input type="date" aria-label="조회 시작일" value={dateRange.startDate} max={dateRange.endDate} onChange={(event) => changeDate('startDate', event.target.value)} />
          <span>~</span>
          <input type="date" aria-label="조회 종료일" value={dateRange.endDate} min={dateRange.startDate} onChange={(event) => changeDate('endDate', event.target.value)} />
        </div>
        <select aria-label="조회 기간" value={period} onChange={(event) => changePeriod(event.target.value)}>
          <option value="7">최근 7일</option>
          <option value="30">최근 30일</option>
          <option value="90">최근 90일</option>
          <option value="custom">사용자 지정</option>
        </select>
        <button type="button" className="receipt-model-filter" disabled>모델: gemma3-4b-trained</button>
        <button type="button" className="receipt-pdf-download" onClick={onExportPdf}><IoDownloadOutline /> PDF 다운로드</button>
      </div>
    </div>
    {monitoringError && <div className="report-access-error">{monitoringError}</div>}
    <div className="receipt-monitoring-kpis">{MONITORING_METRICS.map((metric) => {
      const current = monitoring.summary[metric.key];
      const previous = monitoring.comparison?.summary?.[metric.key];
      const delta = metricDelta(current, previous, metric.type);
      return <article key={metric.key}><h3>{metric.label}<span className="kpi-info" tabIndex="0" role="img" aria-label={`${metric.label} 설명`}><i>i</i><span className="kpi-tooltip" role="tooltip">{metric.description}</span></span></h3><div className="monitoring-kpi-value"><strong style={{ color: current == null ? undefined : metric.color }}>{monitoringLoading ? '…' : metricText(current, metric.type)}</strong><span className={delta == null ? 'empty' : delta > 0 ? 'up' : delta < 0 ? 'down' : 'same'}>{deltaText(delta, metric.type)}</span><small>(vs {comparisonLabel} {metricText(previous, metric.type)})</small></div><MetricSparkline values={monitoring.daily.map((day) => day[metric.key])} color={metric.color} type={metric.type} /></article>;
    })}</div>
    <div className="receipt-monitoring-panels">
      <article className="performance-trend-panel"><header><h3>기간별 성능 추세</h3><span>{periodLabel} · 일별</span></header>{monitoringLoading ? <div className="empty-monitoring-box"><span>불러오는 중</span></div> : monitoring.daily.some((day) => day.total_count) ? <DailyPerformanceChart daily={monitoring.daily} /> : <div className="empty-monitoring-box"><span>선택한 기간의 평가 데이터가 없습니다.</span></div>}</article>
      <article><header><h3>오류 유형 분포</h3><span>{periodLabel}</span></header><ErrorDistribution details={monitoring.details} /></article>
      <article><header><h3>필드별 정확도</h3><span>{periodLabel}</span></header><FieldAccuracyList details={monitoring.details} previousDetails={monitoring.comparison?.details} /></article>
      <article><header><h3>시스템 성능</h3><span>{periodLabel}</span></header><SystemPerformance system={monitoring.details?.system} /></article>
    </div>
    <div className="receipt-monitoring-bottom"><article><header><h3>알림 / 이상 탐지</h3></header><div className="empty-monitoring-row">—</div></article><article className="recent-runs-card"><header><h3>최근 실행 이력</h3><span>{monitoring.recent_runs?.length || 0}회</span></header><RecentRuns runs={monitoring.recent_runs || []} /></article></div>
  </section>;
}

export default function ReportPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const user = getAppUser();
  const isDeveloper = ['DEVELOPER', 'ADMIN'].includes(user.role) || user.email === 'developer@docunex.com';
  const requestedDeveloperReport = new URLSearchParams(window.location.search).get('developerReport') || localStorage.getItem('pic_to_text_developer_report');
  const requestedReceiptTab = new URLSearchParams(window.location.search).get('receiptTab') || localStorage.getItem('pic_to_text_receipt_report_tab');
  const [reportView, setReportView] = useState(isDeveloper ? 'developer' : 'business');
  const [developerReport, setDeveloperReport] = useState(requestedDeveloperReport === 'receipt' ? 'receipt' : 'rag');
  const [receiptTab, setReceiptTab] = useState(requestedReceiptTab === 'experiment' ? 'experiment' : 'monitoring');
  const [runs, setRuns] = useState([]);
  const [businessStats, setBusinessStats] = useState({ documentCount: 0, ragCount: 0, readyRagCount: 0, sessionCount: 0, scrapCount: 0, recentDocuments: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);
  const [ragEvaluation, setRagEvaluation] = useState(() => {
    try { return JSON.parse(localStorage.getItem(RAG_EVALUATION_STORAGE_KEY) || 'null'); } catch { return null; }
  });
  const [umapData, setUmapData] = useState(() => {
    try {
      const saved = localStorage.getItem('pic_to_text_rag_umap_latest');
      return saved ? JSON.parse(saved) : null;
    } catch {
      return null;
    }
  });
  const [umapError, setUmapError] = useState('');

  useEffect(() => {
    if (location.pathname !== '/reports') return;
    const params = new URLSearchParams(location.search);
    const requestedView = params.get('view');
    const requestedReport = params.get('developerReport') || localStorage.getItem('pic_to_text_developer_report');
    const requestedTab = params.get('receiptTab') || localStorage.getItem('pic_to_text_receipt_report_tab');
    if (isDeveloper && (requestedView === 'developer' || requestedReport === 'receipt')) setReportView('developer');
    if (isDeveloper && requestedReport === 'receipt') setDeveloperReport('receipt');
    if (isDeveloper && requestedReport === 'receipt') setReceiptTab(requestedTab === 'experiment' ? 'experiment' : 'monitoring');
  }, [isDeveloper, location.pathname, location.search]);
  const [modelConfig, setModelConfig] = useState({ model: '미설정', embedding_model: '미설정', embedding_dimensions: null, rerank_model: null, prompt_version: '미설정', top_k: null, chunk_target_chars: null, ready: false });

  const loadEvaluations = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const { data } = await apiClient.get('/reports/evaluations', { params: { refresh: Date.now() } });
      setRuns(Array.isArray(data) ? data : []); setLastUpdated(new Date());
    } catch (requestError) {
      setError(requestError.response?.data?.detail || '평가 기록을 불러오지 못했습니다.');
    } finally { setLoading(false); }
  }, []);

  const loadBusinessStats = useCallback(async () => {
    const results = await Promise.allSettled([
      apiClient.get('/ocr/history'), apiClient.get('/rag/documents'), apiClient.get('/chatbot/sessions'), apiClient.get('/chatbot/scraps'),
    ]);
    const values = results.map((result) => result.status === 'fulfilled' && Array.isArray(result.value.data) ? result.value.data : []);
    const [documents, ragDocuments, sessions, scraps] = values;
    setBusinessStats({ documentCount: documents.length, ragCount: ragDocuments.length, readyRagCount: ragDocuments.filter((item) => item.status === 'RAG_READY').length, sessionCount: sessions.length, scrapCount: scraps.length, recentDocuments: documents.slice(0, 5) });
  }, []);

  const loadRagReport = useCallback(async () => {
    try {
      const { data } = await apiClient.get('/rag/evaluate/latest');
      setRagEvaluation(data);
      localStorage.setItem(RAG_EVALUATION_STORAGE_KEY, JSON.stringify(data));
    } catch (requestError) {
      if (requestError.response?.status !== 404) setError(requestError.response?.data?.detail || 'RAG 평가 결과를 불러오지 못했습니다.');
    }
  }, []);

  useEffect(() => {
    loadBusinessStats();
    apiClient.get('/chatbot/status').then(({ data }) => setModelConfig(data)).catch(() => {});
    if (isDeveloper) loadEvaluations(); else setLoading(false);
  }, [isDeveloper, loadBusinessStats, loadEvaluations]);

  useEffect(() => {
    if (isDeveloper && developerReport === 'rag') loadRagReport();
  }, [developerReport, isDeveloper, loadRagReport]);

  useEffect(() => {
    if (!isDeveloper || developerReport !== 'rag') return undefined;
    let active = true;
    setUmapError('');
    apiClient.get('/rag/evaluation/umap').then(({ data }) => {
      if (!active) return;

      setUmapData(data);
      localStorage.setItem(
        'pic_to_text_rag_umap_latest',
        JSON.stringify(data)
      );
    })
    return () => { active = false; };
  }, [developerReport, isDeveloper]);

  const summary = useMemo(() => {
    if (!runs.length) return { accuracy: 0, time: null };
    const average = (field) => runs.reduce((sum, run) => sum + Number(run[field] || 0), 0) / runs.length;
    const timed = runs.filter((run) => run.processing_time_ms != null);
    return { accuracy: average('f1_score'), time: timed.length ? timed.reduce((sum, run) => sum + run.processing_time_ms, 0) / timed.length : null };
  }, [runs]);

  const attentionRuns = useMemo(() => [...runs].sort((a, b) => Number(a.f1_score || 0) - Number(b.f1_score || 0)).slice(0, 4), [runs]);
  const exportReport = () => {
    const payload = { generated_at: new Date().toISOString(), evaluations: runs };
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'developer-performance-report.json'; anchor.click(); URL.revokeObjectURL(url);
  };
  const exportDashboardPdf = () => {
    const originalTitle = document.title;
    const reportName = reportView === 'business' ? '기업 업무 리포트' : developerReport === 'receipt' ? '영수증 서비스 대시보드' : 'AI 성능 리포트';
    document.title = `${reportName}-${new Date().toISOString().slice(0, 10)}`;
    const restorePrintState = () => {
      document.title = originalTitle;
      window.removeEventListener('afterprint', restorePrintState);
    };
    window.addEventListener('afterprint', restorePrintState);
    window.print();
    window.setTimeout(restorePrintState, 300000);
  };

  const ocrAccuracy = runs.length ? percent(summary.accuracy) : '평가 대기';
  const ocrLatency = summary.time == null ? null : summary.time / 1000;
  const pipeline = [
    { label: 'OCR 추출', value: ocrLatency, color: '#3270a6' },
    { label: 'RAG 검색', value: null, color: '#20ae83' },
    { label: 'LLM TTFT', value: null, color: '#ef9b18' },
    { label: '답변 완료', value: null, color: '#6558dc' },
  ];

  return <div className="app-shell developer-report-shell"><Sidebar />
    <main className="developer-report page-enter">
      <header className="report-header">
        <div>
          <p>{reportView === 'developer'? 'DEVELOPER ANALYTICS': 'ENTERPRISE WORK REPORT'}</p>
          <h1>{reportView === 'developer'? 'AI 성능 리포트': '기업 업무 리포트'}</h1>

          <span>Dashboard &gt;{' '}{reportView === 'developer'? 'Performance Report': 'Business Report'}
            {lastUpdated && reportView === 'developer' && ` · ${lastUpdated.toLocaleTimeString('ko-KR')} 갱신`}
          </span>
        </div>

        <div className="report-header-actions">
          {isDeveloper && (
            <div className="report-view-toggle">
              <button className={reportView === 'business' ? 'active' : ''}
                onClick={() => setReportView('business')}
              >
                기업용
              </button>

              <div
                className={reportView === 'developer'  ? 'active developer-report-select'
                    : 'developer-report-select'
                }
              >
                <ReportDropdown ariaLabel="개발자용 리포트 선택" prefix="개발자용" value={developerReport} options={[{ value: 'rag', label: 'RAG' }, { value: 'receipt', label: 'Expense flow' }]} onFocus={() => setReportView('developer')} onChange={(nextValue) => { setDeveloperReport(nextValue); setReportView('developer'); }} />
              </div>
            </div>
          )}

          <button  className="refresh-report"  disabled={loading}
            onClick={() => {
              loadBusinessStats();

              if (isDeveloper) {loadEvaluations();}

              if (developerReport === 'receipt') {window.dispatchEvent(new Event('finance-evaluations-updated'));}
            }}
          >
            <IoRefreshOutline />
            새로고침
          </button>

          {/*
          {reportView === 'developer' &&
            developerReport !== 'receipt' && (
              <button
                disabled={!runs.length}
                onClick={exportReport}
              >
                <IoDownloadOutline />
                JSON 내보내기
              </button>
            )}
          */}
        </div>
      </header>
      {error && <div className="report-access-error">{error}</div>}
      {reportView === 'business' ? <BusinessReport stats={businessStats} loading={loading} /> : developerReport === 'receipt' ? <>
        <div className="receipt-report-tab-bar" role="tablist" aria-label="영수증 성능 리포트 보기">
          <button type="button" role="tab" aria-selected={receiptTab === 'monitoring'} className={receiptTab === 'monitoring' ? 'active' : ''} onClick={() => { setReceiptTab('monitoring'); localStorage.setItem('pic_to_text_receipt_report_tab', 'monitoring'); navigate('/reports?view=developer&developerReport=receipt&receiptTab=monitoring', { replace: true }); }}>운영 모니터링 대시보드</button>
          <button type="button" role="tab" aria-selected={receiptTab === 'experiment'} className={receiptTab === 'experiment' ? 'active' : ''} onClick={() => { setReceiptTab('experiment'); localStorage.setItem('pic_to_text_receipt_report_tab', 'experiment'); navigate('/reports?view=developer&developerReport=receipt&receiptTab=experiment', { replace: true }); }}>개발 실험 평가 도구</button>
        </div>
        {receiptTab === 'experiment' ? <FinanceEvaluationPage embedded /> : <ReceiptMonitoringDashboard onExportPdf={exportDashboardPdf} />}
      </> : <>
      <RagPerformanceReport evaluation={ragEvaluation} modelConfig={modelConfig} umapData={umapData} umapError={umapError} />
      <RagLlmEvaluation />
      {/* Legacy RAG report page 2: retained for later restoration, intentionally hidden. */}
      {false && <>
      <section className="report-kpi-grid">
        <article><div><small>OCR 평균 정확도</small><strong>{ocrAccuracy}</strong></div><span className="positive">▲ 실제 평가</span></article>
        <article><div><small>RAG 검색 적합도</small><strong>평가 대기</strong></div><span className="info">실행 로그 필요</span></article>
        <article><div><small>환각 응답률</small><strong>평가 대기</strong></div><span className="danger">평가 세트 필요</span></article>
        <article><div><small>평균 응답 시간</small><strong>측정 대기</strong></div><span className="waiting">계측 연결 예정</span></article>
      </section>

      <section className="report-visual-grid">
        <article className="report-card quality-card"><header><div><h2>Baseline 모델 구성</h2><p>환경변수로 연결된 현재 평가 기준 조합</p></div><span>{modelConfig.ready ? 'ONLINE' : 'OFFLINE'}</span></header><div className="baseline-config"><div><span>Embedding</span><strong>{modelConfig.embedding_model}</strong><small>{modelConfig.embedding_dimensions ? `${modelConfig.embedding_dimensions} dimensions` : '차원 미설정'}</small></div><div><span>Answer LLM</span><strong>{modelConfig.model}</strong><small>최종 모델 선정 전 Baseline</small></div><div><span>Reranker</span><strong>{modelConfig.rerank_model || '미사용'}</strong><small>선정 후 환경변수로 연결</small></div><div><span>Retrieval</span><strong>Top-K {modelConfig.top_k ?? '—'}</strong><small>Chunk {modelConfig.chunk_target_chars ?? '—'}자 · {modelConfig.prompt_version}</small></div><p>모델을 변경해도 동일한 평가 세트와 실행 기록을 기준으로 비교합니다.</p></div></article>
        <article className="report-card latency-card"><header><div><h2>파이프라인 구간별 Latency</h2><p>실제 계측값만 표시합니다.</p></div><span>WATERFALL</span></header><div className="latency-list">{pipeline.map((step) => <div key={step.label}><strong>{step.label}</strong><span className="latency-track"><i style={{ width: step.value == null ? '0%' : `${Math.max(12, step.value / Math.max(ocrLatency || 1, .1) * 70)}%`, background: step.color }} /></span><b>{step.value == null ? '—' : `${step.value.toFixed(1)}s`}</b></div>)}</div><footer>{ocrLatency == null ? '평가 실행 후 구간별 응답 시간이 표시됩니다.' : <>현재 측정된 OCR 평균 <strong>{ocrLatency.toFixed(1)}초</strong></>}</footer></article>
      </section>

      <article className="report-card comparison-card"><header><div><h2>LLM 답변 성능 비교</h2><p>동일 평가 질문으로 Baseline과 후보 모델을 비교합니다.</p></div><span>NO EVALUATION</span></header><div className="model-evaluation-empty"><strong>아직 저장된 모델 평가 실행이 없습니다.</strong><p>평가 세트와 후보 모델을 선정한 뒤 동일한 문서·질문·Top-K 조건으로 실행하세요.</p></div></article>

      <section className="report-bottom-grid">
        <article className="report-card keyword-card"><header><div><h2>최다 검색 사내 키워드</h2><p>질문 분석 로그 연동 대기</p></div></header><div className="keyword-empty">실제 검색 로그가 쌓이면 Top 5 키워드가 표시됩니다.</div></article>
        <article className="report-card attention-card"><header><div><h2>주의 필요 문서</h2><p>OCR 신뢰도가 낮은 실제 평가 기록</p></div><span>{attentionRuns.length} DOCS</span></header><div className="attention-table"><div className="table-head"><span>문서명</span><span>OCR F1</span><span>처리 시간</span><span>상태</span></div>{attentionRuns.map((run) => <div key={run.id}><strong>{run.document_name}</strong><span>{percent(run.f1_score)}</span><span>{run.processing_time_ms == null ? '—' : `${(run.processing_time_ms / 1000).toFixed(1)}s`}</span><b className={Number(run.f1_score) < .8 ? 'danger' : 'review'}>{Number(run.f1_score) < .8 ? '주의' : '검토 필요'}</b></div>)}{!loading && !attentionRuns.length && <p className="empty-report-row">OCR 페이지에서 정답 평가를 저장하면 표시됩니다.</p>}</div></article>
      </section>
      </>}
      </>}
    </main>
  </div>;
}
