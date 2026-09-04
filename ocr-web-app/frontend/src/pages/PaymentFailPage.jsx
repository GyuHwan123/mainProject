import { useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { IoCloseCircleOutline } from 'react-icons/io5';
import apiClient from '../api/client';
import '../style/PaymentResultPage.scss';

const failureRequests = new Set();

export default function PaymentFailPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const code = params.get('code') || 'PAYMENT_CANCELED';
  const message = params.get('message') || '결제가 취소되었습니다.';
  const orderId = params.get('orderId') || sessionStorage.getItem('docunex_payment_order_id');
  const canceled = code === 'PAY_PROCESS_CANCELED' || code === 'PAYMENT_CANCELED';

  useEffect(() => {
    if (!orderId || failureRequests.has(orderId)) return;
    failureRequests.add(orderId);
    apiClient.post(`/billing/orders/${encodeURIComponent(orderId)}/failure`, { code, message, canceled })
      .finally(() => sessionStorage.removeItem('docunex_payment_order_id'));
  }, [canceled, code, message, orderId]);

  return <main className="payment-result-page">
    <section className="payment-result-card failed">
      <IoCloseCircleOutline /><small>TOSS PAYMENTS TEST</small>
      <h1>{canceled ? '결제가 취소되었습니다' : '결제에 실패했습니다'}</h1>
      <p>{message}</p>
      <button type="button" onClick={() => navigate('/mypage', { replace: true })}>마이페이지로 돌아가기</button>
    </section>
  </main>;
}
