import { useCallback, useEffect, useMemo, useState } from 'react';
import { IoAnalyticsOutline, IoCodeSlashOutline, IoDownloadOutline, IoLayersOutline, IoTimerOutline } from 'react-icons/io5';
import Sidebar from '../components/Sidebar';
import apiClient from '../api/client';
import '../style/ReportPage.scss';

const percent = (value) => `${((value || 0) * 100).toFixed(2)}%`;

export default function ReportPage() {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);

  const loadEvaluations = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { data } = await apiClient.get('/reports/evaluations', { params: { refresh: Date.now() } });
      setRuns(Array.isArray(data) ? data : []);
      setLastUpdated(new Date());
    } catch (requestError) {
      setError(requestError.response?.data?.detail || '평가 기록을 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadEvaluations();
    const refreshWhenVisible = () => { if (document.visibilityState === 'visible') loadEvaluations(); };
    window.addEventListener('focus', loadEvaluations);
    document.addEventListener('visibilitychange', refreshWhenVisible);
    return () => {
      window.removeEventListener('focus', loadEvaluations);
      document.removeEventListener('visibilitychange', refreshWhenVisible);
    };
  }, [loadEvaluations]);

  const summary = useMemo(() => {
    if (!runs.length) return { precision: 0, recall: 0, f1: 0, time: null };
    const average = (field) => runs.reduce((sum, run) => sum + (run[field] || 0), 0) / runs.length;
    const timed = runs.filter((run) => run.processing_time_ms != null);
    return { precision: average('precision'), recall: average('recall'), f1: average('f1_score'), time: timed.length ? timed.reduce((sum, run) => sum + run.processing_time_ms, 0) / timed.length : null };
  }, [runs]);

  const exportReport = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify({ generated_at: new Date().toISOString(), runs }, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'ocr-developer-report.json'; anchor.click(); URL.revokeObjectURL(url);
  };

  return <div className="app-shell developer-report-shell"><Sidebar />
    <main className="developer-report">
      <header className="report-header"><div><p>DEVELOPER ANALYTICS</p><h1>OCR 성능 리포트</h1><span>OCR 페이지에서 저장한 정답 데이터 평가 결과를 확인합니다.{lastUpdated && ` · ${lastUpdated.toLocaleTimeString('ko-KR')} 갱신`}</span></div><div className="report-header-actions"><button className="refresh-report" disabled={loading} onClick={loadEvaluations}>{loading ? '불러오는 중...' : '데이터 새로고침'}</button><button disabled={!runs.length} onClick={exportReport}><IoDownloadOutline /> 리포트 내보내기</button></div></header>
      {error && <div className="report-access-error">{error}</div>}
      <section className="report-metric-grid">
        <article><span className="metric-icon"><IoTimerOutline /></span><div><small>평균 OCR 처리 시간</small><strong>{summary.time == null ? '측정 대기' : `${(summary.time / 1000).toFixed(2)}s`}</strong><p>파일 선택부터 텍스트 추출 완료까지</p></div></article>
        <article><span className="metric-icon"><IoAnalyticsOutline /></span><div><small>평균 Precision</small><strong>{runs.length ? percent(summary.precision) : '평가 대기'}</strong><p>추출 토큰 중 정답 토큰 비율</p></div></article>
        <article><span className="metric-icon"><IoCodeSlashOutline /></span><div><small>평균 Recall</small><strong>{runs.length ? percent(summary.recall) : '평가 대기'}</strong><p>정답 토큰 중 추출된 토큰 비율</p></div></article>
        <article><span className="metric-icon"><IoLayersOutline /></span><div><small>평균 F1 Score</small><strong>{runs.length ? percent(summary.f1) : '평가 대기'}</strong><p>총 {runs.length}건 평가 결과</p></div></article>
      </section>

      <section className="report-body-grid">
        <article className="report-panel report-result-main"><header><div><h2>문서별 OCR 평가 결과</h2><p>OCR 페이지에서 개발자가 등록한 Ground Truth 기준입니다.</p></div><b>{runs.length}</b></header><div className="report-table"><div className="report-table-head"><span>문서명</span><span>처리 시간</span><span>Precision</span><span>Recall</span><span>F1</span><span>TP / FP / FN</span></div>{runs.map((run) => <div key={run.id}><strong>{run.document_name}<small>{new Date(run.created_at).toLocaleString('ko-KR')}</small></strong><span>{run.processing_time_ms == null ? '—' : `${(run.processing_time_ms / 1000).toFixed(2)}s`}</span><span>{percent(run.precision)}</span><span>{percent(run.recall)}</span><b>{percent(run.f1_score)}</b><span>{run.true_positive} / {run.false_positive} / {run.false_negative}</span></div>)}{!loading && !runs.length && <div className="empty-report-row">OCR 페이지에서 문서를 추출한 뒤 정답 데이터를 저장해 주세요.</div>}{loading && <div className="empty-report-row">평가 기록을 불러오고 있습니다...</div>}</div></article>
        <aside className="report-side-column"><article className="report-panel pipeline-panel"><header><div><h2>파이프라인 상태</h2><p>개발 성능 측정 항목</p></div></header><ul><li className="ready"><span><IoAnalyticsOutline /></span><div><strong>OCR 정답 평가</strong><small>Precision / Recall / F1</small></div><b>READY</b></li><li className="ready"><span><IoTimerOutline /></span><div><strong>처리 시간</strong><small>브라우저 추출 구간 측정</small></div><b>READY</b></li><li><span><IoLayersOutline /></span><div><strong>Embedding 분석</strong><small>벡터 모델 연결 예정</small></div><b>PENDING</b></li></ul></article><article className="report-panel formula-panel"><header><div><h2>평가 공식</h2><p>정규화 토큰 기준</p></div></header><code>Precision = TP / (TP + FP)</code><code>Recall = TP / (TP + FN)</code><code>F1 = 2 × P × R / (P + R)</code></article></aside>
      </section>
    </main>
  </div>;
}
