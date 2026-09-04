import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import apiClient from '../api/client';
import LoginLoading from '../components/LoginLoading';
import { authErrorMessage, MAX_PASSWORD_LENGTH } from '../features/authValidation';
import '../style/LoginPage.scss';

export default function ResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') || '';
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const submit = async (event) => {
    event.preventDefault();
    setError('');
    if (!token) return setError('유효하지 않은 비밀번호 재설정 링크입니다.');
    if (password.length < 8) return setError('비밀번호는 최소 8자 이상이어야 합니다.');
    if (password.length > MAX_PASSWORD_LENGTH) return setError(`비밀번호는 ${MAX_PASSWORD_LENGTH}자 이하로 입력해 주세요.`);
    if (!confirmation) return setError('새 비밀번호 확인을 입력해 주세요.');
    if (password !== confirmation) return setError('비밀번호와 비밀번호 확인이 일치하지 않습니다.');
    setLoading(true);
    try {
      const { data } = await apiClient.post('/auth/password-reset/confirm', { token, new_password: password });
      setSuccess(data.message);
      setPassword('');
      setConfirmation('');
    } catch (requestError) {
      setError(authErrorMessage(requestError, '비밀번호를 변경하지 못했습니다. 링크를 다시 확인해 주세요.'));
    } finally { setLoading(false); }
  };

  return <div className="login-shell"><div className="login-window reset-password-window"><div className="login-panel">
    {loading && <LoginLoading overlay />}
    <h1><img src="/DocAI.png" alt="DocAI" /></h1>
    <div className="recover-heading"><strong>새 비밀번호 설정</strong><p>앞으로 사용할 새 비밀번호를 입력해 주세요.</p></div>
    <form className="login-form" onSubmit={submit} noValidate>
      {error && <p className="auth-message error" role="alert">{error}</p>}
      {success && <p className="auth-message success" role="status">{success}</p>}
      {!success && <>
        <label><span>새 비밀번호</span><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" minLength={8} maxLength={MAX_PASSWORD_LENGTH} disabled={loading} /></label>
        <label><span>새 비밀번호 확인</span><input type="password" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} autoComplete="new-password" minLength={8} maxLength={MAX_PASSWORD_LENGTH} disabled={loading} /></label>
        <p className="password-hint">비밀번호는 최소 8자 이상이어야 합니다.</p>
        <button className="primary-button" type="submit" disabled={loading}>비밀번호 변경</button>
      </>}
      <Link className="auth-text-button" to="/login">로그인으로 이동</Link>
    </form>
  </div></div></div>;
}
