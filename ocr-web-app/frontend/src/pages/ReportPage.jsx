import { useCallback, useEffect, useMemo, useState } from 'react';
import { IoDownloadOutline, IoRefreshOutline } from 'react-icons/io5';
import { useLocation } from 'react-router-dom';
import Sidebar from '../components/Sidebar';
import apiClient from '../api/client';
import { getAppUser } from '../features/appSession';
import FinanceEvaluationPage from './FinanceEvaluationPage';
import '../style/ReportPage.scss';

const percent = (value, digits = 1) => `${((value || 0) * 100).toFixed(digits)}%`;
const RAG_EVALUATION_STORAGE_KEY = 'pic_to_text_rag_evaluation_latest';

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
      hitAt5: summary.hit_at_5 ?? hitAt(5),
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
    ['Hit@1', metrics?.hitAt1], ['Hit@3', metrics?.hitAt3], ['Hit@5', metrics?.hitAt5],
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

export default function ReportPage() {
  const location = useLocation();
  const user = getAppUser();
  const isDeveloper = ['DEVELOPER', 'ADMIN'].includes(user.role) || user.email === 'developer@docunex.com';
  const requestedDeveloperReport = new URLSearchParams(window.location.search).get('developerReport') || localStorage.getItem('pic_to_text_developer_report');
  const [reportView, setReportView] = useState(isDeveloper ? 'developer' : 'business');
  const [developerReport, setDeveloperReport] = useState(requestedDeveloperReport === 'receipt' ? 'receipt' : 'rag');
  const [runs, setRuns] = useState([]);
  const [businessStats, setBusinessStats] = useState({ documentCount: 0, ragCount: 0, readyRagCount: 0, sessionCount: 0, scrapCount: 0, recentDocuments: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);
  const [ragEvaluation, setRagEvaluation] = useState(() => {
    try { return JSON.parse(localStorage.getItem(RAG_EVALUATION_STORAGE_KEY) || 'null'); } catch { return null; }
  });
  const [umapData, setUmapData] = useState(null);
  const [umapError, setUmapError] = useState('');

  useEffect(() => {
    if (location.pathname !== '/reports') return;
    const params = new URLSearchParams(location.search);
    const requestedView = params.get('view');
    const requestedReport = params.get('developerReport') || localStorage.getItem('pic_to_text_developer_report');
    if (isDeveloper && (requestedView === 'developer' || requestedReport === 'receipt')) setReportView('developer');
    if (isDeveloper && requestedReport === 'receipt') setDeveloperReport('receipt');
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
    }).catch((requestError) => {
      if (!active) return;
      setUmapData(null);
      setUmapError(requestError.response?.data?.detail || '현재 corpus UMAP을 생성할 수 없습니다.');
    });
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

  const ocrAccuracy = runs.length ? percent(summary.accuracy) : '평가 대기';
  const ocrLatency = summary.time == null ? null : summary.time / 1000;
  const pipeline = [
    { label: 'OCR 추출', value: ocrLatency, color: '#3270a6' },
    { label: 'RAG 검색', value: null, color: '#20ae83' },
    { label: 'LLM TTFT', value: null, color: '#ef9b18' },
    { label: '답변 완료', value: null, color: '#6558dc' },
  ];

  return <div className="app-shell developer-report-shell"><Sidebar />
    <main className="developer-report">
      <header className="report-header"><div><p>{reportView === 'developer' ? 'DEVELOPER ANALYTICS' : 'ENTERPRISE WORK REPORT'}</p><h1>{reportView === 'developer' ? 'AI 성능 리포트' : '기업 업무 리포트'}</h1><span>Dashboard &gt; {reportView === 'developer' ? 'Performance Report' : 'Business Report'}{lastUpdated && reportView === 'developer' && ` · ${lastUpdated.toLocaleTimeString('ko-KR')} 갱신`}</span></div><div className="report-header-actions">{isDeveloper && <div className="report-view-toggle"><button className={reportView === 'business' ? 'active' : ''} onClick={() => setReportView('business')}>기업용</button><label className={reportView === 'developer' ? 'active developer-report-select' : 'developer-report-select'}><span>개발자용</span><select aria-label="개발자용 리포트 선택" value={developerReport} onFocus={() => setReportView('developer')} onChange={(event) => { setDeveloperReport(event.target.value); setReportView('developer'); }}><option value="rag">RAG</option><option value="receipt">영수증</option></select></label></div>}<button className="refresh-report" disabled={loading} onClick={() => { loadBusinessStats(); if (isDeveloper) loadEvaluations(); if (developerReport === 'receipt') window.dispatchEvent(new Event('finance-evaluations-updated')); }}><IoRefreshOutline />새로고침</button>{reportView === 'developer' && <button disabled={developerReport === 'receipt' || !runs.length} onClick={exportReport}><IoDownloadOutline /> 내보내기</button>}</div></header>
      {error && <div className="report-access-error">{error}</div>}
      {reportView === 'business' ? <BusinessReport stats={businessStats} loading={loading} /> : developerReport === 'receipt' ? <FinanceEvaluationPage embedded /> : <>
      <RagPerformanceReport evaluation={ragEvaluation} modelConfig={modelConfig} umapData={umapData} umapError={umapError} />
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
    </main>
  </div>;
}
