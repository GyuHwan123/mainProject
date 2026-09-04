import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { RiGoogleFill, RiKakaoTalkFill } from 'react-icons/ri';
import { SiNaver } from 'react-icons/si';
import apiClient from '../api/client';
import LoginLoading from '../components/LoginLoading';
import { saveAppSession } from '../features/appSession';
import { authErrorMessage, isValidEmail, MAX_PASSWORD_LENGTH } from '../features/authValidation';
import { supabase } from '../lib/supabase';
import '../style/LoginPage.scss';

const MAX_LOGIN_FAILURES = 5;
const LOGIN_WINDOW_MS = 15 * 60 * 1000;
const failureKey = (email) => `docunex_login_failures:${email.trim().toLowerCase()}`;

function readFailureState(email) {
  if (!email.trim()) return { count: 0, lockedUntil: 0 };
  try {
    const state = JSON.parse(localStorage.getItem(failureKey(email)) || '{}');
    if (!state.firstFailureAt || Date.now() - state.firstFailureAt > LOGIN_WINDOW_MS) return { count: 0, lockedUntil: 0 };
    return { count: Number(state.count || 0), lockedUntil: Number(state.lockedUntil || 0), firstFailureAt: state.firstFailureAt };
  } catch { return { count: 0, lockedUntil: 0 }; }
}

function recordFailure(email) {
  const previous = readFailureState(email);
  const count = previous.count + 1;
  const firstFailureAt = previous.firstFailureAt || Date.now();
  const lockedUntil = count >= MAX_LOGIN_FAILURES ? Date.now() + LOGIN_WINDOW_MS : 0;
  const state = { count, firstFailureAt, lockedUntil };
  localStorage.setItem(failureKey(email), JSON.stringify(state));
  return state;
}

export default function LoginPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState('login');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [formError, setFormError] = useState('');
  const [formSuccess, setFormSuccess] = useState('');
  const [recovering, setRecovering] = useState(false);
  const [emailTouched, setEmailTouched] = useState(false);
  const [now, setNow] = useState(Date.now());
  const failureState = readFailureState(email);
  const lockedSeconds = Math.max(0, Math.ceil((failureState.lockedUntil - now) / 1000));

  useEffect(() => {
    if (!lockedSeconds) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [lockedSeconds]);

  const changeMode = (nextMode) => {
    if (loading) return;
    setMode(nextMode); setRecovering(false); setEmailTouched(false); setFormError(''); setFormSuccess(''); setPassword(''); setConfirmPassword('');
  };

  const showPasswordRecovery = () => {
    if (loading) return;
    setRecovering(true); setFormError(''); setFormSuccess(''); setPassword('');
  };

  const handleSocialLogin = async (provider) => {
    setFormError('');
    if (!supabase) return setFormError('소셜 로그인 환경 변수를 확인할 수 없습니다.');
    setLoading(true);
    try {
      const { error } = await supabase.auth.signInWithOAuth({
        provider,
        options: {
          redirectTo: `${window.location.origin}/auth/callback`,
          queryParams: provider === 'google' ? { prompt: 'select_account' } : undefined,
        },
      });
      if (error) throw error;
    } catch (error) {
      setLoading(false);
      setFormError(authErrorMessage(error));
    }
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (loading) return;
    setFormError('');
    setEmailTouched(true);
    const normalizedEmail = email.trim().toLowerCase();
    const normalizedName = name.trim();
    if (!normalizedEmail) return setFormError('이메일을 입력해 주세요.');
    if (!isValidEmail(normalizedEmail)) return setFormError('올바른 이메일 형식을 입력해 주세요. 예: name@company.com');
    if (recovering) {
      setLoading(true);
      try {
        const { data } = await apiClient.post('/auth/password-reset/request', { email: normalizedEmail });
        setFormSuccess(data.message);
      } catch (error) {
        setFormError(authErrorMessage(error));
      } finally { setLoading(false); }
      return;
    }
    if (mode === 'signup' && !normalizedName) return setFormError('이름을 입력해 주세요.');

    if (!password) return setFormError('비밀번호를 입력해 주세요.');
    if (password.length < 8) return setFormError('비밀번호는 8자 이상이어야 합니다.');
    if (password.length > MAX_PASSWORD_LENGTH) return setFormError(`비밀번호는 ${MAX_PASSWORD_LENGTH}자 이하로 입력해 주세요.`);
    if (mode === 'signup' && !confirmPassword) return setFormError('비밀번호 확인을 입력해 주세요.');
    if (mode === 'signup' && password !== confirmPassword) return setFormError('비밀번호와 비밀번호 확인이 일치하지 않습니다.');
    if (mode === 'login' && lockedSeconds) return setFormError(`로그인이 일시 제한되었습니다. ${Math.ceil(lockedSeconds / 60)}분 후 다시 시도해 주세요.`);

    setLoading(true);
    let credentialsAccepted = false;
    try {
      if (mode === 'signup') {
        const { data } = await apiClient.post('/auth/signup', {
          name: normalizedName, email: normalizedEmail, password,
        });
        saveAppSession(data);
        navigate('/dashboard', { replace: true });
        return;
      }

      const { data } = await apiClient.post('/auth/login', { email: normalizedEmail, password });
      credentialsAccepted = true;
      localStorage.removeItem(failureKey(normalizedEmail));
      saveAppSession(data);
      navigate('/dashboard', { replace: true });
    } catch (error) {
      if (mode === 'login' && !credentialsAccepted && error?.response?.status === 401) {
        const state = recordFailure(normalizedEmail);
        setNow(Date.now());
        if (state.lockedUntil) setFormError('로그인에 5회 실패하여 15분 동안 로그인이 제한됩니다.');
        else setFormError(`${authErrorMessage(error)} (${MAX_LOGIN_FAILURES - state.count}회 더 실패하면 15분간 제한됩니다.)`);
      } else setFormError(authErrorMessage(error));
    } finally { setLoading(false); }
  };

  const update = (setter) => (event) => { setter(event.target.value); setFormError(''); setFormSuccess(''); };
  const emailError = emailTouched && email.trim() && !isValidEmail(email)
    ? '올바른 이메일 형식을 입력해 주세요. 예: name@company.com'
    : '';
  const submitLabel = mode === 'login' ? '로그인' : '회원가입';

  return <div className="login-shell"><div className="login-window">
    <div className="login-visual" aria-hidden="true"><div className="login-illustration"><div className="cloud" /><div className="desktop-card" /><div className="person" /><div className="plant" /></div></div>
    <div className="login-panel">{loading && <LoginLoading overlay />}<h1><img src="/DocAI.png" alt="DocAI" />환영합니다.</h1>
      {!recovering && <div className="auth-toggle" role="group" aria-label="인증 방식 선택"><button type="button" className={mode === 'login' ? 'active' : ''} aria-pressed={mode === 'login'} disabled={loading} onClick={() => changeMode('login')}>로그인</button><button type="button" className={mode === 'signup' ? 'active' : ''} aria-pressed={mode === 'signup'} disabled={loading} onClick={() => changeMode('signup')}>회원가입</button></div>}
      {recovering && <div className="recover-heading">
        <strong>비밀번호 찾기</strong>
        <p>이메일과 비밀번호로 가입한 계정에 재설정 링크를 보내드립니다.</p>
        <p className="social-password-notice">Google, Naver, Kakao 계정은 해당 서비스에서 비밀번호를 변경해 주세요.</p>
      </div>}
      <form className="login-form" onSubmit={handleSubmit} noValidate>
        {formError && <p className="auth-message error" role="alert">{formError}</p>}
        {formSuccess && <p className="auth-message success" role="status">{formSuccess}</p>}
        {!recovering && mode === 'signup' && <label><span>이름</span><input type="text" value={name} onChange={update(setName)} placeholder="이름을 입력하세요" autoComplete="name" disabled={loading} maxLength={80} /></label>}
        <label><span>이메일</span><input type="email" value={email} onChange={update(setEmail)} onBlur={() => setEmailTouched(true)} placeholder="name@company.com" autoComplete="email" inputMode="email" disabled={loading} autoFocus aria-invalid={Boolean(emailError)} aria-describedby={emailError ? 'email-format-error' : undefined} />{emailError && <small id="email-format-error" className="field-error" role="alert">{emailError}</small>}</label>
        {!recovering && <label><span>비밀번호</span><input type="password" value={password} onChange={update(setPassword)} placeholder="8자 이상 입력하세요" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} disabled={loading} minLength={8} maxLength={MAX_PASSWORD_LENGTH} /></label>}
        {!recovering && mode === 'login' && <button className="auth-text-button" type="button" disabled={loading} onClick={showPasswordRecovery}>비밀번호를 잊으셨나요?</button>}
        {!recovering && mode === 'signup' && <label><span>비밀번호 확인</span><input type="password" value={confirmPassword} onChange={update(setConfirmPassword)} placeholder="비밀번호를 다시 입력하세요" autoComplete="new-password" disabled={loading} minLength={8} maxLength={MAX_PASSWORD_LENGTH} /></label>}
        <button className="primary-button" type="submit" disabled={loading || (!recovering && mode === 'login' && Boolean(lockedSeconds))}>{loading ? '처리 중...' : recovering ? '재설정 메일 보내기' : submitLabel}</button>
        {recovering && <button className="auth-text-button" type="button" disabled={loading} onClick={() => { setRecovering(false); setFormError(''); setFormSuccess(''); }}>로그인으로 돌아가기</button>}
      </form>
      {!recovering && <><p className="hint-text">{mode === 'login' ? '가입한 이메일과 비밀번호로 로그인해 주세요.' : '이메일과 비밀번호로 계정을 만들고 바로 로그인합니다.'}</p>
      <div className="auth-divider"><span>또는</span></div>
      <button className="social-button google" type="button" disabled={loading} onClick={() => handleSocialLogin('google')}><RiGoogleFill className="googleIcon" />Google 계정으로 계속하기</button>
      <button className="social-button naver" type="button" disabled={loading} onClick={() => handleSocialLogin('custom:naver')}><SiNaver className="naverIcon" />Naver 계정으로 계속하기</button>
      <button className="social-button kakao" type="button" disabled={loading} onClick={() => handleSocialLogin('kakao')}><RiKakaoTalkFill className="kakaoIcon" />Kakao 계정으로 계속하기</button>
      </>}
      <div className="legal-block"><p>계속 진행하면 서비스 이용약관 및 개인정보 처리방침에 동의한 것으로 간주됩니다.</p></div>
    </div>
  </div></div>;
}
