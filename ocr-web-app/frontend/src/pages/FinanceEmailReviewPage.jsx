import { useEffect, useState } from 'react';
import apiClient from '../api/client';

export default function FinanceEmailReviewPage() {
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get('token') || '');
  const [review, setReview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    apiClient.post('/finance/email-review/inspect', { token })
      .then(({ data }) => { if (active) setReview(data); })
      .catch((err) => { if (active) setError(err.response?.data?.detail || '검토 링크를 확인하지 못했습니다.'); });
    return () => { active = false; };
  }, [token]);
  async function confirm() {
    setBusy(true); setError('');
    try {
      const { data } = await apiClient.post('/finance/email-review/confirm', { token });
      setReview((current) => ({ ...current, confirmed_at: data.confirmed_at }));
    } catch (err) { setError(err.response?.data?.detail || '처리 완료를 저장하지 못했습니다.'); }
    finally { setBusy(false); }
  }
  return <main style={{ maxWidth: 760, margin: '48px auto', padding: 24, background: 'white', borderRadius: 12 }}>
    <h1>재무팀 검토</h1>
    {error && <p role="alert">{typeof error === 'string' ? error : '유효하지 않은 링크입니다.'}</p>}
    {!review && !error && <p>발송 내역을 불러오는 중입니다.</p>}
    {review && <><p>제출자: {review.sender_email}</p><p>첨부 Excel과 아래 내역을 검토한 후 전체 처리 완료를 눌러 주세요.</p>
      <table style={{ width: '100%', textAlign: 'left', borderSpacing: '8px 12px' }}><thead><tr><th>거래일</th><th>상호</th><th>금액</th></tr></thead><tbody>{review.records.map((r) => <tr key={r.id}><td>{r.transaction_date || '미확인'}</td><td>{r.merchant || '미확인'}</td><td>{r.total_amount?.toLocaleString()}원</td></tr>)}</tbody></table>
      {review.confirmed_at ? <p role="status">처리 완료되었습니다. 제출자의 마이페이지에 반영되었습니다.</p> : <button disabled={busy} onClick={confirm} style={{ padding: '12px 20px', border: 0, borderRadius: 8, background: '#208060', color: 'white', cursor: 'pointer' }}>{busy ? '저장 중…' : `${review.records.length}건 전체 처리 완료`}</button>}
    </>}
  </main>;
}
