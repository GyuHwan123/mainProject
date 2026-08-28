import '../style/LoginLoading.scss';

export default function LoginLoading({ overlay = false }) {
  return (
    <div className={`login-loading ${overlay ? 'overlay' : 'page'}`} role="status" aria-live="polite" aria-label="로그인 처리 중">
      <div className="login-loading-content">
        <img src="/DocAI.png" alt="DocAI" />
        <span className="login-loading-indicator" aria-hidden="true"><i /><i /><i /></span>
        <strong>로그인 중입니다</strong>
        <p>잠시만 기다려 주세요.</p>
      </div>
    </div>
  );
}
