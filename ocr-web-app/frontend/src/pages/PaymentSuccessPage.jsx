import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { IoCheckmarkCircleOutline, IoCloseCircleOutline } from 'react-icons/io5';
import apiClient from '../api/client';
import { saveAppUser } from '../features/appSession';
import '../style/PaymentResultPage.scss';

const confirmationRequests = new Map();

export default function PaymentSuccessPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [state, setState] = useState({ loading: true, error: '' });
  const paymentKey = params.get('paymentKey');
  const orderId = params.get('orderId');
  const amount = Number(params.get('amount'));

  useEffect(() => {
    if (!paymentKey || !orderId || !Number.isInteger(amount)) {
      setState({ loading: false, error: '결제 승인 정보를 확인할 수 없습니다.' });
      return;
    }
    const requestKey = `${orderId}:${paymentKey}`;
    if (!confirmationRequests.has(requestKey)) {
      confirmationRequests.set(requestKey, apiClient.post('/billing/payments/confirm', { paymentKey, orderId, amount }));
    }
    confirmationRequests.get(requestKey)
      .then(({ data }) => {
        saveAppUser({ subscription_tier: data.subscription_tier || 'ENTERPRISE' });
        sessionStorage.removeItem('docunex_payment_order_id');
        setState({ loading: false, error: '' });
      })
      .catch((error) => setState({ loading: false, error: error.response?.data?.detail || '결제 승인에 실패했습니다.' }));
  }, [amount, orderId, paymentKey]);

  return <main className="payment-result-page">
    <section className={`payment-result-card ${state.error ? 'failed' : ''}`}>
      {state.loading ? <div className="payment-result-spinner" /> : state.error ? <IoCloseCircleOutline /> : <IoCheckmarkCircleOutline />}
      <small>TOSS PAYMENTS TEST</small>
      <h1>{state.loading ? '결제를 승인하고 있습니다' : state.error ? '결제를 완료하지 못했습니다' : 'Enterprise가 활성화되었습니다'}</h1>
      <p>{state.loading ? '창을 닫거나 새로고침하지 말고 잠시 기다려 주세요.' : state.error || '테스트 결제가 승인되었으며 30일 이용 기간이 시작되었습니다.'}</p>
      {!state.loading && <button type="button" onClick={() => navigate('/mypage', { replace: true })}>마이페이지로 이동</button>}
    </section>
  </main>;
}
