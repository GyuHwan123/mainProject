import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { IoBookmarkOutline, IoChatbubbleEllipsesOutline, IoCheckmarkOutline, IoChevronDownOutline, IoCloseOutline, IoDocumentTextOutline, IoDownloadOutline, IoLockClosedOutline, IoRefreshOutline, IoServerOutline } from 'react-icons/io5';
import Sidebar from '../components/Sidebar';
import apiClient from '../api/client';
import { getAppUser } from '../features/appSession';
import '../style/MyPage.scss';

export default function MyPage() {
  const navigate = useNavigate();
  const user = getAppUser();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [plansOpen, setPlansOpen] = useState(false);
  const [benefitsOpen, setBenefitsOpen] = useState(true);
  const [upgradeNotice, setUpgradeNotice] = useState('');
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState('');
  const [cancelSaving, setCancelSaving] = useState(false);
  const [subscription, setSubscription] = useState({ status: 'ACTIVE', current_period_end: null, cancel_at_period_end: false });
  const [data, setData] = useState({ documents: [], ragDocuments: [], sessions: [], scraps: [], financeHistory: [] });

  const loadAccountData = useCallback(async () => {
    setLoading(true); setError('');
    const results = await Promise.allSettled([
      apiClient.get('/ocr/history'), apiClient.get('/rag/documents'), apiClient.get('/chatbot/sessions'), apiClient.get('/chatbot/scraps'), apiClient.get('/users/subscription'), apiClient.get('/finance/history'),
    ]);
    const values = results.map((result) => result.status === 'fulfilled' && Array.isArray(result.value.data) ? result.value.data : []);
    setData({ documents: values[0], ragDocuments: values[1], sessions: values[2], scraps: values[3], financeHistory: values[5] });
    if (results[4]?.status === 'fulfilled') setSubscription(results[4].value.data);
    if (results.every((result) => result.status === 'rejected')) setError('계정 사용 현황을 불러오지 못했습니다.');
    setLoading(false);
  }, []);

  useEffect(() => { loadAccountData(); }, [loadAccountData]);

  const initials = useMemo(() => (user.name || user.email || 'U').trim().slice(0, 2).toUpperCase(), [user]);
  const isEnterprise = user.subscriptionTier === 'ENTERPRISE';
  const roleLabel = user.role === 'ADMIN' ? '관리자' : user.role === 'DEVELOPER' ? '개발자' : (isEnterprise ? '기업 사용자' : '일반 사용자');
  const readyRag = data.ragDocuments.filter((document) => document.status === 'RAG_READY').length;
  const recentDocuments = data.documents.slice(0, 5);
  const cancellationScheduled = subscription.status === 'CANCEL_SCHEDULED' || subscription.cancel_at_period_end;
  const periodEndLabel = subscription.current_period_end ? new Date(subscription.current_period_end).toLocaleDateString('ko-KR') : null;

  const requestCancellation = async () => {
    setCancelSaving(true); setUpgradeNotice('');
    try {
      const { data: updated } = await apiClient.post('/users/subscription/cancel', { reason: cancelReason || null });
      setSubscription(updated); setCancelOpen(false);
      setUpgradeNotice(`${new Date(updated.current_period_end).toLocaleDateString('ko-KR')}에 Enterprise 플랜이 종료되도록 예약했습니다.`);
    } catch (requestError) {
      setUpgradeNotice(requestError.response?.data?.detail || '요금제 취소 요청을 저장하지 못했습니다.');
    } finally { setCancelSaving(false); }
  };

  const downloadFinanceDocument = async (record) => {
    setError('');
    try {
      const response = await apiClient.get(`/finance/records/${record.id}/export`, { responseType: 'blob', timeout: 60000 });
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = record.document_filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (requestError) {
      setError(requestError.response?.data?.detail || '재무 문서 파일을 다운로드하지 못했습니다.');
    }
  };

  const confirmFinanceDocument = async (record) => {
    setError('');
    try {
      await apiClient.post(`/finance/records/${record.id}/finance-confirm`);
      await loadAccountData();
    } catch (requestError) {
      setError(requestError.response?.data?.detail || '재무팀 확인 상태를 변경하지 못했습니다.');
    }
  };

  return <div className="app-shell mypage-shell"><Sidebar />
    <main className="mypage-workspace page-enter">
      <header className="mypage-header"><div><p>ACCOUNT WORKSPACE</p><h1>내 정보</h1><span>계정 정보와 문서 AI 사용 현황을 확인합니다.</span></div><button type="button" disabled={loading} onClick={loadAccountData}><IoRefreshOutline />{loading ? '불러오는 중' : '새로고침'}</button></header>
      {error && <p className="mypage-error">{error}</p>}

      <section className="profile-overview">
        <article className="profile-card"><div className="profile-avatar">{initials}</div><div className="profile-copy"><span>{roleLabel}</span><h2>{user.name || '사용자'} 님</h2><p>{user.email || '이메일 정보 없음'}</p></div><div className="account-state"><i /> 계정 활성</div></article>
        <article className="current-plan-card"><div><small>CURRENT PLAN</small><h2>{isEnterprise ? 'Enterprise Workspace' : 'Personal Pro'}</h2><p>{isEnterprise ? '팀 협업, 사내 지식 자산화와 기업 데이터 보안을 위한 조직용 플랜입니다.' : '개인 문서 OCR, RAG 검색과 AI 비서를 위한 생산성 플랜입니다.'}</p><div className="plan-tags">{isEnterprise ? <><b>Organization</b><b>{user.role === 'ADMIN' ? 'Admin' : 'Member'}</b><b>AI 학습 제외</b><b>월 ₩89,000</b></> : <><b>개인 계정</b><b>문서 1개</b><b>월 ₩19,900</b><b>표준 AI 모델</b></>}</div>{cancellationScheduled && <p className="cancellation-summary">{periodEndLabel || '현재 이용 기간 종료일'}에 Personal로 전환 예정</p>}</div><div className="plan-card-actions"><span className={cancellationScheduled ? 'scheduled' : ''}>{cancellationScheduled ? '취소 예약됨' : '활성'}</span><button type="button" onClick={() => { setUpgradeNotice(''); setPlansOpen(true); }}>{isEnterprise ? '요금제 관리' : '업그레이드'}</button></div></article>
      </section>

      <section className="mypage-stat-grid">
        <article><span><IoDocumentTextOutline /></span><div><small>처리 문서</small><strong>{loading ? '—' : data.documents.length}</strong><p>OCR 및 업로드 기록</p></div></article>
        <article><span><IoServerOutline /></span><div><small>RAG 지식</small><strong>{loading ? '—' : readyRag}</strong><p>검색 준비 완료 문서</p></div></article>
        <article><span><IoChatbubbleEllipsesOutline /></span><div><small>AI 채팅</small><strong>{loading ? '—' : data.sessions.length}</strong><p>저장된 대화 기록</p></div></article>
        <article><span><IoBookmarkOutline /></span><div><small>지식 바구니</small><strong>{loading ? '—' : data.scraps.length}</strong><p>보관한 AI 답변</p></div></article>
      </section>

      <section className="mypage-panel finance-history-panel">
        <header><div><h2>재무 히스토리</h2><p>사용자가 최종 확정하여 재무팀에 전달한 문서입니다.</p></div><button onClick={() => navigate('/reports')}>재무 문서 관리</button></header>
        <div className="finance-history-table">
          <div className="finance-history-head"><span>문서 파일</span><span>금액</span><span>재무팀 상태</span><span>전달일</span><span>확인 날짜</span><span>작업</span></div>
          {data.financeHistory.map((record) => <div className="finance-history-row" key={record.id}>
            <button type="button" className="finance-file" onClick={() => downloadFinanceDocument(record)}><i>XLSX</i><span><strong>{record.document_filename}</strong><small>{record.merchant || record.expense_category || '재무 문서'}</small></span></button>
            <b>{Math.round(Number(record.total_amount || 0)).toLocaleString('ko-KR')}원</b>
            <span className={record.finance_team_status === '확인' ? 'finance-checked' : 'finance-pending'}>{record.finance_team_status}</span>
            <time>{record.submitted_at ? new Date(record.submitted_at).toLocaleDateString('ko-KR') : '-'}</time>
            <time>{record.finance_confirmed_at ? new Date(record.finance_confirmed_at).toLocaleDateString('ko-KR') : '-'}</time>
            <div className="finance-history-actions"><button type="button" onClick={() => downloadFinanceDocument(record)} title="문서 다운로드"><IoDownloadOutline /></button>{['ADMIN', 'DEVELOPER'].includes(user.role) && record.finance_team_status !== '확인' && <button type="button" className="finance-confirm-button" onClick={() => confirmFinanceDocument(record)}>확인 처리</button>}</div>
          </div>)}
          {!loading && !data.financeHistory.length && <div className="mypage-empty">재무팀에 전달한 문서가 아직 없습니다.</div>}
          {loading && <div className="mypage-empty">재무 히스토리를 불러오는 중입니다.</div>}
        </div>
      </section>

      <section className="mypage-content-grid">
        <article className="mypage-panel recent-account-docs"><header><div><h2>최근 문서</h2><p>최근 처리한 OCR 및 RAG 문서입니다.</p></div><button onClick={() => navigate('/ocr')}>문서 관리</button></header><div className="account-doc-table"><div className="account-doc-head"><span>문서명</span><span>상태</span><span>등록일</span></div>{recentDocuments.map((document) => <button key={document.id || document.document_id} onClick={() => navigate('/ocr')}><strong><i>DOC</i>{document.file_name || document.name || '문서'}</strong><span>{document.status || 'COMPLETED'}</span><small>{document.created_at ? new Date(document.created_at).toLocaleDateString('ko-KR') : '최근'}</small></button>)}{!loading && !recentDocuments.length && <div className="mypage-empty">아직 처리한 문서가 없습니다.</div>}</div></article>

        <aside className="mypage-side-column">
          <article className="mypage-panel account-details"><header><div><h2>계정 정보</h2><p>로그인 계정과 접근 권한</p></div></header><dl><div><dt>이름</dt><dd>{user.name || '등록되지 않음'}</dd></div><div><dt>이메일</dt><dd>{user.email || '등록되지 않음'}</dd></div><div><dt>권한</dt><dd>{roleLabel}</dd></div><div><dt>인증 상태</dt><dd className="verified">인증됨</dd></div></dl></article>
          <article className="mypage-panel security-card"><header><div><h2>보안 및 개인정보</h2><p>기업 문서 보호 정책</p></div><IoLockClosedOutline /></header><div><strong>민감정보 보호 활성화</strong><p>RAG 검색과 AI 답변에서 연락처, 주소 등의 민감정보가 보호됩니다.</p><button onClick={() => navigate('/chat')}>AI 채팅 확인</button></div></article>
        </aside>
      </section>
      {plansOpen && <div className="plans-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setPlansOpen(false); }}><section className="plans-dialog" role="dialog" aria-modal="true" aria-label="요금제 비교"><header><div><small>WORKSPACE PLANS</small><h2>업무 방식에 맞는 플랜을 선택하세요</h2><p>가격은 현재 서비스 기획 기준이며 실제 결제 연동 전입니다.</p></div><button onClick={() => setPlansOpen(false)} aria-label="닫기"><IoCloseOutline /></button></header><div className="plan-choice-grid">
        <article className="plan-choice current"><div className="plan-choice-title"><span>{isEnterprise ? '일반 요금제' : '현재 요금제'}</span><h3>Personal Pro</h3><p>개인 생산성을 위한 AI 문서 비서</p></div><div className="plan-price"><strong>₩19,900</strong><span>/월</span><small>매월 청구</small></div><button disabled={!isEnterprise} onClick={() => setUpgradeNotice('플랜 변경 결제 시스템 연결 전입니다. 관리자에게 Personal 변경을 문의해 주세요.')}>{isEnterprise ? 'Personal로 변경 문의' : '현재 이용 중'}</button><ul>{['한 번에 문서 1개 업로드', '기본 이미지·PDF OCR', '개인 문서 기반 RAG', '표준 AI 모델', '개인 히스토리와 캘린더'].map((item) => <li key={item}><IoCheckmarkOutline />{item}</li>)}</ul></article>
        <article className="plan-choice recommended"><div className="recommended-label">{isEnterprise ? '현재 요금제' : '추천 요금제'}</div><div className="plan-choice-title"><span>기업용</span><h3>Enterprise Workspace</h3><p>최대 5명의 팀원과 지식과 업무를 공유하세요.</p></div><div className="plan-price"><strong>₩89,000</strong><span>/월</span><small>5명 포함 · 매월 청구</small></div><button disabled={isEnterprise} onClick={() => setUpgradeNotice('결제 시스템 연결 전입니다. 관리자에게 Enterprise 도입을 문의해 주세요.')}>{isEnterprise ? '현재 이용 중' : 'Enterprise로 업그레이드'}</button><ul>{['대량 문서 Batch OCR과 복잡한 표 인식', '전사·부서별 통합 RAG 지식베이스', '고성능 모델과 고용량 토큰', '기업 입력 데이터 AI 학습 제외', 'Admin / Member 권한 및 팀 프로젝트'].map((item) => <li key={item}><IoCheckmarkOutline />{item}</li>)}</ul></article>
      </div>{upgradeNotice && <p className="upgrade-notice">{upgradeNotice}</p>}<button className={`all-benefits-toggle ${benefitsOpen ? 'open' : ''}`} onClick={() => setBenefitsOpen((open) => !open)}>모든 혜택 {benefitsOpen ? '숨기기' : '보기'} <IoChevronDownOutline /></button>{benefitsOpen && <div className="enterprise-benefits"><div><IoCheckmarkOutline /><span><strong>팀 업무 자동화</strong><small>커스텀 Agent, 회의록 기반 할 일 등록과 메신저 알림</small></span></div><div><IoCheckmarkOutline /><span><strong>외부 업무 도구 연동</strong><small>Slack, Google Workspace 등 기업 도구 연결</small></span></div><div><IoCheckmarkOutline /><span><strong>기업 전용 지원</strong><small>전담 엔지니어, SSO 연동과 통합 정산 지원</small></span></div></div>}{isEnterprise && <div className="subscription-cancel-zone"><div><strong>{cancellationScheduled ? '요금제 취소가 예약되어 있습니다.' : 'Enterprise 요금제를 취소하시겠습니까?'}</strong><p>{cancellationScheduled ? `${periodEndLabel || '현재 이용 기간 종료일'}까지 Enterprise 기능을 사용할 수 있습니다.` : '취소해도 현재 이용 기간 종료일까지 Enterprise 기능과 조직 데이터를 사용할 수 있습니다.'}</p></div>{cancellationScheduled ? <span>종료 예정 · {periodEndLabel || '관리자 확인 중'}</span> : <button onClick={() => setCancelOpen(true)}>요금제 취소 요청</button>}</div>}{cancelOpen && <div className="cancel-confirm-panel"><h3>Enterprise 취소 예약</h3><ul><li>Enterprise 기능은 즉시 중단되지 않습니다.</li><li>30일 후 Personal 플랜으로 전환될 예정입니다.</li><li>팀 공유 및 조직 데이터의 후속 처리 정책을 확인해 주세요.</li></ul><label>취소 사유<select value={cancelReason} onChange={(event) => setCancelReason(event.target.value)}><option value="">선택하지 않음</option><option value="비용 부담">비용 부담</option><option value="사용 빈도 감소">사용 빈도 감소</option><option value="필요 기능 부족">필요 기능 부족</option><option value="다른 서비스 이용">다른 서비스 이용</option></select></label><div><button onClick={() => setCancelOpen(false)}>돌아가기</button><button className="confirm-cancel" disabled={cancelSaving} onClick={requestCancellation}>{cancelSaving ? '저장 중...' : '취소 예약 확정'}</button></div></div>}</section></div>}
    </main>
  </div>;
}
