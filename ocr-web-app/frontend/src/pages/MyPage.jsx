import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { loadTossPayments } from '@tosspayments/tosspayments-sdk';
import { IoBookmarkOutline, IoChatbubbleEllipsesOutline, IoCheckmarkOutline, IoCloseOutline, IoDocumentTextOutline, IoDownloadOutline, IoInformationCircleOutline, IoLockClosedOutline, IoPersonOutline, IoRefreshOutline, IoServerOutline, IoTrashOutline } from 'react-icons/io5';
import Sidebar from '../components/Sidebar';
import LoginLoading from '../components/LoginLoading';
import apiClient from '../api/client';
import { clearAppSession, getAppUser, saveAppUser } from '../features/appSession';
import '../style/MyPage.scss';

const getDocumentStatus = (kind, status) => {
  const normalized = String(status || '').toUpperCase();
  if (['FAILED', 'ERROR'].includes(normalized)) return { label: '실패', tone: 'failed' };
  if (kind === 'rag' && ['RAG_READY', 'READY', 'COMPLETED'].includes(normalized)) {
    return { label: '임베딩 완료', tone: 'completed' };
  }
  if (kind === 'ocr' && ['COMPLETED', 'SUCCESS'].includes(normalized)) {
    return { label: '처리 완료', tone: 'completed' };
  }
  return { label: '처리 중', tone: 'processing' };
};

const formatKstDate = (value) => value
  ? new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value))
  : '-';

const enterpriseBenefits = [
  '대량 문서 Batch OCR',
  '사내 공용 RAG',
  '고성능 모델 / 확장된 사용량',
  'Admin / Member 권한 관리',
  '팀 프로젝트 및 업무 자동화',
  '기업용 리포트',
];


export default function MyPage() {
  const navigate = useNavigate();
  const user = getAppUser();
  const [loading, setLoading] = useState(true);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState('');
  const [plansOpen, setPlansOpen] = useState(false);
  const [upgradeNotice, setUpgradeNotice] = useState('');
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState('');
  const [cancelSaving, setCancelSaving] = useState(false);
  const [paymentSaving, setPaymentSaving] = useState(false);
  const [subscription, setSubscription] = useState({ status: 'ACTIVE', current_period_end: null, cancel_at_period_end: false });
  const [data, setData] = useState({ documents: [], ragDocuments: [], sessions: [], scraps: [], financeHistory: [] });
  const [profileName, setProfileName] = useState(user.name || '');
  const [accountNotice, setAccountNotice] = useState('');
  const [accountSaving, setAccountSaving] = useState(false);
  const [passwordForm, setPasswordForm] = useState({ current: '', next: '', confirm: '' });
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [accountSettingsOpen, setAccountSettingsOpen] = useState(false);
  const [deleteForm, setDeleteForm] = useState({ password: '', confirmation: '' });
  const initialRequestRef = useRef(null);

  const loadAccountData = useCallback(async () => {
    setLoading(true); setError('');
    const results = await Promise.allSettled([
      apiClient.get('/ocr/history?upload_origin=OCR'), apiClient.get('/rag/documents'), apiClient.get('/chatbot/sessions'), apiClient.get('/chatbot/scraps'), apiClient.get('/users/subscription'), apiClient.get('/finance/history'),
    ]);
    const values = results.map((result) => result.status === 'fulfilled' && Array.isArray(result.value.data) ? result.value.data : []);
    setData({ documents: values[0], ragDocuments: values[1], sessions: values[2], scraps: values[3], financeHistory: values[5] });
    if (results[4]?.status === 'fulfilled') setSubscription(results[4].value.data);
    if (results.every((result) => result.status === 'rejected')) setError('계정 사용 현황을 불러오지 못했습니다.');
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!initialRequestRef.current) initialRequestRef.current = loadAccountData();
    initialRequestRef.current.finally(() => setInitialLoading(false));
  }, [loadAccountData]);

  const initials = useMemo(() => (profileName || user.email || 'U').trim().slice(0, 2).toUpperCase(), [profileName, user.email]);
  const currentTier = subscription.subscription_tier || user.subscriptionTier;
  const isEnterprise = currentTier === 'ENTERPRISE';
  const roleLabel = user.role === 'ADMIN' ? '관리자' : user.role === 'DEVELOPER' ? '개발자' : (isEnterprise ? '기업 사용자' : '일반 사용자');
  const recentDocuments = useMemo(() => [
    ...data.documents.map((document) => ({
      document,
      kind: 'ocr',
      key: `ocr-${document.id || document.document_id}`,
      createdAt: document.created_at,
    })),
    ...data.ragDocuments.map((document) => ({
      document,
      kind: 'rag',
      key: `rag-${document.id || document.document_id}`,
      createdAt: document.created_at,
    })),
  ].sort((left, right) => new Date(right.createdAt || 0) - new Date(left.createdAt || 0)).slice(0, 5), [data.documents, data.ragDocuments]);
  const cancellationScheduled = subscription.status === 'CANCEL_SCHEDULED' || subscription.cancel_at_period_end;
  const periodEndLabel = subscription.current_period_end ? formatKstDate(subscription.current_period_end) : null;
  const planName = isEnterprise ? 'Enterprise Workspace' : 'FREE';
  const planAmount = Number(subscription.monthly_amount || 0);
  const subscriptionStatusLabel = subscription.status === 'CANCELED'
    ? '종료'
    : cancellationScheduled ? '취소 예정' : isEnterprise ? '활성' : '무료';

  const updateProfile = async () => {
    setAccountSaving(true); setAccountNotice('');
    try { const { data: updated } = await apiClient.patch('/users/me', { name: profileName }); saveAppUser(updated); setProfileName(updated.name || ''); setAccountNotice('프로필 정보가 저장되었습니다.'); }
    catch (requestError) { setAccountNotice(requestError.response?.data?.detail || '프로필을 저장하지 못했습니다.'); }
    finally { setAccountSaving(false); }
  };
  const changePassword = async () => {
    if (passwordForm.next !== passwordForm.confirm) { setAccountNotice('새 비밀번호 확인이 일치하지 않습니다.'); return; }
    setAccountSaving(true); setAccountNotice('');
    try { const { data: result } = await apiClient.post('/users/password', { current_password: passwordForm.current, new_password: passwordForm.next }); setPasswordForm({ current: '', next: '', confirm: '' }); setAccountNotice(result.message); }
    catch (requestError) { setAccountNotice(requestError.response?.data?.detail || '비밀번호를 변경하지 못했습니다.'); }
    finally { setAccountSaving(false); }
  };
  const downloadAccountData = async () => {
    setAccountSaving(true); setAccountNotice('');
    try { const response = await apiClient.get('/users/data-export', { responseType: 'blob', timeout: 120000 }); const url = URL.createObjectURL(response.data); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `account-data-${new Date().toISOString().slice(0, 10)}.json`; anchor.click(); URL.revokeObjectURL(url); setAccountNotice('사용자 데이터 다운로드를 완료했습니다.'); }
    catch (requestError) { setAccountNotice(requestError.response?.data?.detail || '사용자 데이터를 다운로드하지 못했습니다.'); }
    finally { setAccountSaving(false); }
  };
  const revokeCancellation = async () => {
    setCancelSaving(true); setUpgradeNotice('');
    try { const { data: updated } = await apiClient.post('/users/subscription/cancel/revoke'); setSubscription(updated); setUpgradeNotice('구독 취소 예약을 철회했습니다.'); }
    catch (requestError) { setUpgradeNotice(requestError.response?.data?.detail || '구독 취소를 철회하지 못했습니다.'); }
    finally { setCancelSaving(false); }
  };
  const deleteAccount = async () => {
    setAccountSaving(true); setAccountNotice('');
    try { await apiClient.delete('/users/me', { data: { password: deleteForm.password || null, confirmation: deleteForm.confirmation } }); clearAppSession(); navigate('/login', { replace: true }); }
    catch (requestError) { setAccountNotice(requestError.response?.data?.detail || '계정을 탈퇴 처리하지 못했습니다.'); setDeleteOpen(false); }
    finally { setAccountSaving(false); }
  };

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

  const startEnterprisePayment = async () => {
    if (paymentSaving) return;
    setPaymentSaving(true); setUpgradeNotice('');
    let orderId = '';
    try {
      const clientKey = import.meta.env.VITE_TOSS_CLIENT_KEY;
      if (!clientKey?.startsWith('test_gck_')) throw new Error('토스페이먼츠 결제창형 테스트 클라이언트 키 설정이 필요합니다.');
      const { data: order } = await apiClient.post('/billing/orders', { plan: 'ENTERPRISE' });
      orderId = order.orderId;
      sessionStorage.setItem('docunex_payment_order_id', orderId);
      const tossPayments = await loadTossPayments(clientKey);
      const widgets = tossPayments.widgets({ customerKey: order.customerKey });
      await widgets.setAmount({ currency: order.currency, value: order.amount });
      const paymentWindow = await widgets.renderPaymentWindow();
      let requestStarted = false;
      paymentWindow.on('paymentRequest', async () => {
        if (requestStarted) return;
        requestStarted = true;
        try {
          await widgets.requestPayment({
            orderId: order.orderId,
            orderName: order.orderName,
            successUrl: `${window.location.origin}/payment/success`,
            failUrl: `${window.location.origin}/payment/fail`,
            customerEmail: order.customerEmail,
            customerName: order.customerName,
          });
        } catch (paymentError) {
          await apiClient.post(`/billing/orders/${encodeURIComponent(orderId)}/failure`, {
            code: paymentError.code || 'PAYMENT_REQUEST_ERROR', message: paymentError.message, canceled: false,
          }).catch(() => {});
          sessionStorage.removeItem('docunex_payment_order_id');
          setUpgradeNotice(paymentError.message || '결제 요청을 완료하지 못했습니다.');
          setPaymentSaving(false);
        }
      });
      paymentWindow.on('cancel', async () => {
        await apiClient.post(`/billing/orders/${encodeURIComponent(orderId)}/failure`, {
          code: 'PAYMENT_WINDOW_CANCELED', message: '사용자가 결제창을 닫았습니다.', canceled: true,
        }).catch(() => {});
        sessionStorage.removeItem('docunex_payment_order_id');
        setUpgradeNotice('테스트 결제를 취소했습니다.');
        setPaymentSaving(false);
      });
    } catch (requestError) {
      const canceled = requestError.code === 'USER_CANCEL' || requestError.code === 'PAY_PROCESS_CANCELED';
      if (orderId) {
        await apiClient.post(`/billing/orders/${encodeURIComponent(orderId)}/failure`, {
          code: requestError.code || 'PAYMENT_WINDOW_ERROR', message: requestError.message, canceled,
        }).catch(() => {});
        sessionStorage.removeItem('docunex_payment_order_id');
      }
      setUpgradeNotice(canceled ? '테스트 결제를 취소했습니다.' : (requestError.response?.data?.detail || requestError.message || '결제창을 열지 못했습니다.'));
      setPaymentSaving(false);
    }
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

  if (initialLoading) {
    return <div className="app-shell mypage-shell"><Sidebar />
      <main className="page-loading-region">
        <LoginLoading
          mode="content"
          title="내 정보를 불러오는 중입니다."
          ariaLabel="내 정보 불러오는 중"
        />
      </main>
    </div>;
  }

  return <div className="app-shell mypage-shell"><Sidebar />
    <main className="mypage-workspace page-enter">
      <header className="mypage-header"><div><p>ACCOUNT WORKSPACE</p><h1>내 정보</h1><span>계정 정보와 문서 AI 사용 현황을 확인합니다.</span></div><button type="button" disabled={loading} onClick={loadAccountData}><IoRefreshOutline />{loading ? '불러오는 중' : '새로고침'}</button></header>
      {error && <p className="mypage-error">{error}</p>}

      <section className="profile-overview">
        <article className="profile-card"><div className="profile-avatar">{initials}</div><div className="profile-copy"><span>{roleLabel}</span><h2>{profileName || '사용자'} 님</h2><p>{user.email || '이메일 정보 없음'}</p></div><div className="profile-card-actions"><div className="account-state"><i /> 계정 활성</div><button type="button" className={accountSettingsOpen ? 'active' : ''} onClick={() => setAccountSettingsOpen((open) => !open)}>{accountSettingsOpen ? '회원정보 닫기' : '회원정보 관리'}</button></div></article>
        <article className="current-plan-card"><div><small>CURRENT PLAN</small><h2>{planName}</h2><dl className="current-plan-summary"><div><dt>구독 상태</dt><dd>{subscriptionStatusLabel}</dd></div><div><dt>월 요금</dt><dd>₩{planAmount.toLocaleString('ko-KR')}</dd></div><div><dt>다음 결제 예정일</dt><dd>{isEnterprise && !cancellationScheduled ? periodEndLabel || '-' : '-'}</dd></div></dl>{cancellationScheduled && <p className="cancellation-summary">{periodEndLabel || '현재 이용 기간 종료일'}에 구독이 종료될 예정입니다.</p>}</div><div className="plan-card-actions"><span className={cancellationScheduled ? 'scheduled' : ''}>{subscriptionStatusLabel}</span><button type="button" onClick={() => { setUpgradeNotice(''); setPlansOpen(true); }}>요금제 관리</button></div></article>
      </section>

      {accountSettingsOpen && <section className="account-management-grid account-management-open">
        <article className="mypage-panel account-action-card"><header><div><h2><IoPersonOutline /> 프로필 정보 수정</h2><p>서비스에 표시되는 이름을 변경합니다.</p></div></header><div className="account-form"><label>이름<input maxLength="100" value={profileName} onChange={(event) => setProfileName(event.target.value)} /></label><label>이메일<input value={user.email} disabled /></label><button type="button" disabled={accountSaving || !profileName.trim()} onClick={updateProfile}>프로필 저장</button></div></article>
        <article className="mypage-panel account-action-card"><header><div><h2><IoLockClosedOutline /> 비밀번호 변경</h2><p>로컬 로그인 계정의 비밀번호를 변경합니다.</p></div></header><div className="account-form password"><input type="password" autoComplete="current-password" placeholder="현재 비밀번호" value={passwordForm.current} onChange={(event) => setPasswordForm((form) => ({ ...form, current: event.target.value }))} /><input type="password" autoComplete="new-password" placeholder="새 비밀번호 (8자 이상)" value={passwordForm.next} onChange={(event) => setPasswordForm((form) => ({ ...form, next: event.target.value }))} /><input type="password" autoComplete="new-password" placeholder="새 비밀번호 확인" value={passwordForm.confirm} onChange={(event) => setPasswordForm((form) => ({ ...form, confirm: event.target.value }))} /><button type="button" disabled={accountSaving || !passwordForm.current || passwordForm.next.length < 8 || !passwordForm.confirm} onClick={changePassword}>비밀번호 변경</button></div></article>
        <article className="mypage-panel account-action-card data-policy-card"><header><div><h2><IoInformationCircleOutline /> 데이터 다운로드 및 보존</h2><p>계정에 저장된 사용자 데이터를 JSON으로 내려받습니다.</p></div></header><div><p>계정 탈퇴 시 즉시 비활성화됩니다. 관련 데이터는 복구 및 법적 의무 이행을 위해 탈퇴일로부터 30일간 보존 후 삭제 대상이 되며, 결제 기록은 관계 법령에 따른 기간 동안 별도로 보존될 수 있습니다.</p><button type="button" disabled={accountSaving} onClick={downloadAccountData}><IoDownloadOutline /> 사용자 데이터 다운로드</button></div></article>
        <article className="mypage-panel account-action-card danger-account-card"><header><div><h2><IoTrashOutline /> 계정 탈퇴</h2><p>계정을 비활성화하고 서비스 이용을 종료합니다.</p></div></header><div><p>탈퇴 전에 필요한 문서와 데이터를 다운로드해 주세요.</p><button type="button" onClick={() => setDeleteOpen(true)}>계정 탈퇴</button></div></article>
      </section>}
      {accountNotice && <p className="account-action-notice" role="status">{accountNotice}</p>}

      {upgradeNotice && <p className="account-action-notice" role="status">{upgradeNotice}</p>}

      <section className="mypage-stat-grid">
        <article><span><IoDocumentTextOutline /></span><div><small>OCR 처리</small><strong>{loading ? '—' : data.documents.length}</strong><p>영수증 OCR 처리 기록</p></div></article>
        <article><span><IoServerOutline /></span><div><small>RAG 문서</small><strong>{loading ? '—' : data.ragDocuments.length}</strong><p>등록된 지식 문서</p></div></article>
        <article><span><IoChatbubbleEllipsesOutline /></span><div><small>AI 대화</small><strong>{loading ? '—' : data.sessions.length}</strong><p>저장된 대화 기록</p></div></article>
        <article><span><IoBookmarkOutline /></span><div><small>지식 바구니</small><strong>{loading ? '—' : data.scraps.length}</strong><p>보관한 AI 답변</p></div></article>
      </section>

      <section className="mypage-content-grid">
        <article className="mypage-panel recent-account-docs"><header><div><h2>최근 문서</h2><p>최근 처리한 OCR 및 RAG 문서입니다.</p></div><button onClick={() => navigate('/ocr')}>문서 관리</button></header><div className="account-doc-table"><div className="account-doc-head"><span>문서명</span><span>유형</span><span>상태</span><span>등록일</span></div>{recentDocuments.map(({ document, kind, key }) => { const status = getDocumentStatus(kind, document.status); return <button key={key} onClick={() => navigate(kind === 'rag' ? '/chat' : '/ocr')}><strong title={document.file_name || document.filename || document.name || document.title || '문서'}><i>{kind === 'rag' ? 'RAG' : 'OCR'}</i><em>{document.file_name || document.filename || document.name || document.title || '문서'}</em></strong><span className={`document-type-badge ${kind}`}>{kind === 'rag' ? 'RAG 문서' : '영수증 OCR'}</span><span className={`document-status-badge ${status.tone}`}>{status.label}</span><small>{document.created_at ? new Date(document.created_at).toLocaleDateString('ko-KR') : '-'}</small></button>; })}{!loading && !recentDocuments.length && <div className="mypage-empty">아직 처리한 문서가 없습니다.</div>}</div></article>

        <aside className="mypage-side-column">
        <article className="mypage-panel account-details"><header><div><h2>계정 정보</h2><p>로그인 계정과 접근 권한</p></div></header><dl><div><dt>이름</dt><dd>{profileName || '등록되지 않음'}</dd></div><div><dt>이메일</dt><dd>{user.email || '등록되지 않음'}</dd></div><div><dt>권한</dt><dd>{roleLabel}</dd></div><div><dt>인증 상태</dt><dd className="verified">인증됨</dd></div></dl></article>
          <article className="mypage-panel security-card"><header><div><h2>보안 및 개인정보</h2><p>기업 문서 보호 정책</p></div><IoLockClosedOutline /></header><div><strong>민감정보 보호 활성화</strong><p>RAG 검색과 AI 답변에서 연락처, 주소 등의 민감정보가 보호됩니다.</p><button onClick={() => navigate('/chat')}>AI 채팅 확인</button></div></article>
        </aside>
      </section>

      <section className="mypage-panel finance-history-panel">
        <header><div><h2>재무 히스토리</h2><p>재무팀에 실제 전달된 문서 내역입니다.</p></div><button onClick={() => navigate('/reports')}>재무 문서 관리</button></header>
        <div className="finance-history-table">
          <div className="finance-history-head"><span>문서 파일</span><span>금액</span><span>재무팀 상태</span><span>전달일</span><span>확인 날짜</span><span>작업</span></div>
          {data.financeHistory.map((record) => <div className="finance-history-row" key={record.id}>
            <button type="button" className="finance-file" onClick={() => downloadFinanceDocument(record)}><i>XLSX</i><span><strong>{record.document_filename}</strong><small>{record.merchant || record.expense_category || '재무 문서'}</small></span></button>
            <b>{Math.round(Number(record.total_amount || 0)).toLocaleString('ko-KR')}원</b>
            <span className={record.finance_team_status === '확인' ? 'finance-checked' : 'finance-pending'}>{record.finance_team_status}</span>
            <time>{record.submitted_at ? formatKstDate(record.submitted_at) : '-'}</time>
            <time>{record.finance_confirmed_at ? formatKstDate(record.finance_confirmed_at) : '-'}</time>
            <div className="finance-history-actions"><button type="button" onClick={() => downloadFinanceDocument(record)} title="문서 다운로드"><IoDownloadOutline /></button>{['ADMIN', 'DEVELOPER'].includes(user.role) && record.finance_team_status !== '확인' && <button type="button" className="finance-confirm-button" onClick={() => confirmFinanceDocument(record)}>확인 처리</button>}</div>
          </div>)}
          {!loading && !data.financeHistory.length && <div className="mypage-empty">재무팀에 전달한 문서가 아직 없습니다.</div>}
          {loading && <div className="mypage-empty">재무 히스토리를 불러오는 중입니다.</div>}
        </div>
      </section>

      <section className="mypage-panel subscription-management-panel">
        <header><div><h2>구독 관리</h2><p>현재 이용 중인 요금제와 결제 정보를 확인하고 관리할 수 있습니다.</p></div></header>
        <div className="subscription-management-body">
          <div className="subscription-management-grid">
            <dl><div><dt>현재 요금제</dt><dd>{planName}</dd></div><div><dt>구독 상태</dt><dd className={cancellationScheduled ? 'scheduled' : subscription.status === 'CANCELED' ? 'canceled' : 'active'}>{subscriptionStatusLabel}</dd></div><div><dt>{cancellationScheduled ? '종료 예정일' : '다음 결제 예정일'}</dt><dd>{isEnterprise ? periodEndLabel || '-' : '-'}</dd></div><div><dt>다음 결제 금액</dt><dd>{isEnterprise && !cancellationScheduled ? `₩${planAmount.toLocaleString('ko-KR')}` : '-'}</dd></div></dl>
            <div className="subscription-benefits"><strong>현재 요금제 주요 혜택</strong>{isEnterprise ? <ul>{enterpriseBenefits.map((benefit) => <li key={benefit}><IoCheckmarkOutline />{benefit}</li>)}</ul> : <p>Enterprise로 전환하면 팀 협업과 기업용 기능을 이용할 수 있습니다.</p>}</div>
            <div className="subscription-management-actions">{!isEnterprise && <button type="button" onClick={() => { setUpgradeNotice(''); setPlansOpen(true); }}>Enterprise 시작하기</button>}{isEnterprise && !cancellationScheduled && <><button type="button" className="secondary" onClick={() => { setUpgradeNotice(''); setPlansOpen(true); }}>요금제 변경</button><button type="button" className="danger" onClick={() => setCancelOpen(true)}>구독 취소</button></>}{cancellationScheduled && <button type="button" disabled={cancelSaving} onClick={revokeCancellation}>{cancelSaving ? '처리 중' : '구독 취소 철회'}</button>}</div>
          </div>
          {cancellationScheduled && <p className="subscription-end-notice">구독이 {periodEndLabel || '현재 결제 기간 종료일'}에 종료될 예정입니다. 종료일까지 Enterprise 기능을 계속 사용할 수 있습니다.</p>}
        </div>
      </section>
      {cancelOpen && <div className="plans-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !cancelSaving) setCancelOpen(false); }}><section className="subscription-cancel-dialog" role="dialog" aria-modal="true" aria-labelledby="cancel-subscription-title"><header><div><small>SUBSCRIPTION</small><h2 id="cancel-subscription-title">구독을 취소하시겠습니까?</h2><p>현재 결제 기간이 끝나는 {periodEndLabel || '이용 기간 종료일'}까지 Enterprise 기능을 계속 사용할 수 있습니다.</p></div><button type="button" disabled={cancelSaving} aria-label="닫기" onClick={() => setCancelOpen(false)}><IoCloseOutline /></button></header><div><label>취소 사유 <small>선택 사항</small><select value={cancelReason} onChange={(event) => setCancelReason(event.target.value)}><option value="">선택하지 않음</option><option value="비용 부담">비용 부담</option><option value="사용 빈도 감소">사용 빈도 감소</option><option value="필요 기능 부족">필요 기능 부족</option><option value="다른 서비스 이용">다른 서비스 이용</option></select></label><p>취소를 확정해도 즉시 FREE로 변경되지 않으며, 종료 예정일까지 현재 기능이 유지됩니다.</p></div><footer><button type="button" disabled={cancelSaving} onClick={() => setCancelOpen(false)}>취소 유지</button><button type="button" className="danger" disabled={cancelSaving} onClick={requestCancellation}>{cancelSaving ? '처리 중...' : '구독 취소 확정'}</button></footer></section></div>}
      {deleteOpen && <div className="plans-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDeleteOpen(false); }}><section className="account-delete-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-account-title"><header><div><small>ACCOUNT DELETION</small><h2 id="delete-account-title">계정을 탈퇴하시겠습니까?</h2><p>탈퇴 후에는 현재 계정으로 로그인할 수 없습니다.</p></div><button type="button" aria-label="닫기" onClick={() => setDeleteOpen(false)}><IoCloseOutline /></button></header><div><label>현재 비밀번호 <small>소셜 로그인 계정은 입력하지 않아도 됩니다.</small><input type="password" value={deleteForm.password} onChange={(event) => setDeleteForm((form) => ({ ...form, password: event.target.value }))} /></label><label>확인을 위해 <strong>계정 탈퇴</strong>를 입력하세요.<input value={deleteForm.confirmation} onChange={(event) => setDeleteForm((form) => ({ ...form, confirmation: event.target.value }))} /></label></div><footer><button type="button" onClick={() => setDeleteOpen(false)}>돌아가기</button><button type="button" className="danger" disabled={accountSaving || deleteForm.confirmation !== '계정 탈퇴'} onClick={deleteAccount}>{accountSaving ? '처리 중' : '계정 탈퇴'}</button></footer></section></div>}
      {plansOpen && <div className="plans-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !paymentSaving) setPlansOpen(false); }}><section className="plans-dialog" role="dialog" aria-modal="true" aria-label="요금제 선택"><header><div><small>WORKSPACE PLANS</small><h2>요금제를 선택하세요</h2><p>FREE와 Enterprise Workspace의 기능을 비교해보세요.</p></div><button disabled={paymentSaving} onClick={() => setPlansOpen(false)} aria-label="닫기"><IoCloseOutline /></button></header><div className="plan-choice-grid">
        <article className={`plan-choice ${!isEnterprise ? 'current' : ''}`}><div className="plan-choice-title"><span>{!isEnterprise ? '현재 요금제' : '무료'}</span><h3>FREE</h3><p>일반 사용자를 위한 기본 문서 AI 기능</p></div><div className="plan-price"><strong>₩0</strong><span>/월</span></div><button disabled={!isEnterprise} onClick={() => { setPlansOpen(false); setCancelOpen(true); }}>{!isEnterprise ? '현재 요금제' : 'FREE로 변경'}</button><ul>{['일반 사용자', '기본 OCR', '개인 RAG', 'AI 대화', '개인 문서 관리'].map((item) => <li key={item}><IoCheckmarkOutline />{item}</li>)}</ul></article>
        <article className={`plan-choice recommended ${isEnterprise ? 'current' : ''}`}><div className="recommended-label">{isEnterprise ? '현재 요금제' : '기업용 추천'}</div><div className="plan-choice-title"><span>최대 5명</span><h3>Enterprise Workspace</h3><p>기업 문서와 팀 업무를 위한 확장 플랜</p></div><div className="plan-price"><strong>₩99,000</strong><span>/월</span><small>테스트 1회 결제로 30일 활성화</small></div><button disabled={isEnterprise || paymentSaving} onClick={startEnterprisePayment}>{isEnterprise ? '현재 요금제' : paymentSaving ? '결제창 준비 중' : 'Enterprise 시작하기 · 결제하기'}</button><ul>{['최대 5명', '대량 OCR', '사내 공용 RAG', 'AI Agent', '재무 문서 자동화', '기업용 리포트', '확장된 사용량'].map((item) => <li key={item}><IoCheckmarkOutline />{item}</li>)}</ul></article>
      </div>{upgradeNotice && <p className="upgrade-notice">{upgradeNotice}</p>}</section></div>}
    </main>
  </div>;
}
