import '../style/LoginLoading.scss';

export default function LoginLoading({
  overlay = false,
  title = '로그인 중입니다',
  description = '잠시만 기다려 주세요.',
  ariaLabel = '로그인 처리 중',
}) {
  return (
    <div className={`login-loading ${overlay ? 'overlay' : 'page'}`} role="status" aria-live="polite" aria-label={ariaLabel}>
      <div className="login-loading-content">
        <img src="/DocAI.png" alt="DocAI" />
        <span className="login-loading-indicator" aria-hidden="true"><i /><i /><i /></span>
        <strong>{title}</strong>
        <p>{description}</p>
      </div>
    </div>
  );
}
