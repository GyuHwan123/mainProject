import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import apiClient from '../api/client';
import { saveAppSession } from '../features/appSession';
import { supabase } from '../lib/supabase';
import '../style/LoginPage.scss';
import { RiGoogleFill } from "react-icons/ri";
import { RiKakaoTalkFill } from "react-icons/ri";
import LoginLoading from '../components/LoginLoading';

export default function LoginPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState('login');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('demo@example.com');
  const [password, setPassword] = useState('password123');
  const [confirmPassword, setConfirmPassword] = useState('password123');
  const [loading, setLoading] = useState(false);

  const handleSupabaseSocialLogin = async (provider) => {
    if (!supabase) {
      alert('Supabase 설정이 아직 준비되지 않았습니다. 환경 변수를 확인해주세요.');
      return;
    }

    setLoading(true);
    try {
      const { error } = await supabase.auth.signInWithOAuth({
        provider,
        options: {
          redirectTo: `${window.location.origin}/auth/callback`,
          queryParams: provider === 'google'
            ? { prompt: 'select_account' }
            : undefined,
        },
      });

      if (error) throw error;
    } catch (error) {
      setLoading(false);
      alert(error.message);
    }
  };

  const handleSubmit = async (event) => {
    event.preventDefault();

    if (mode === 'signup') {
      if (!name || !email || !password || !confirmPassword) {
        alert('이름, 이메일, 비밀번호, 비밀번호 확인을 모두 입력해주세요.');
        return;
      }

      if (password !== confirmPassword) {
        alert('비밀번호와 비밀번호 확인이 일치하지 않습니다.');
        return;
      }
    } else if (!email || !password) {
      alert('이메일과 비밀번호를 입력해주세요.');
      return;
    }

    setLoading(true);

    try {
      const endpoint = mode === 'login' ? '/auth/login' : '/auth/signup';
      const payload = mode === 'login'
        ? { email, password }
        : { name, email, password };

      const response = await apiClient.post(endpoint, payload, { timeout: 30000 });

      if (mode === 'login') {
        saveAppSession(response.data);
        navigate('/dashboard');
        return;
      }

      alert('회원가입이 완료되었습니다. 로그인해 주세요.');
      setMode('login');
      setName('');
      setPassword('');
      setConfirmPassword('');
    } catch (error) {
      const detail = error?.code === 'ECONNABORTED'
        ? '로그인 서버의 외부 인증 확인이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.'
        : error?.response?.data?.detail || error?.message || '요청 처리 중 오류가 발생했습니다.';
      alert(detail);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-shell">
      <div className="login-window">
        <div className="login-visual">
          <div className="login-illustration">
            <div className="cloud" />
            <div className="desktop-card" />
            <div className="person" />
            <div className="plant" />
          </div>
        </div>

        <div className="login-panel">
          {loading && <LoginLoading overlay />}
            <h1>
                <img src="/DocAI.png" alt="DocAI" />
                환영합니다.
            </h1>

          <div className="auth-toggle">
            <button
              type="button"
              className={mode === 'login' ? 'active' : ''}
              onClick={() => setMode('login')}
            >
              로그인
            </button>
            <button
              type="button"
              className={mode === 'signup' ? 'active' : ''}
              onClick={() => setMode('signup')}
            >
              회원가입
            </button>
          </div>

          <form className="login-form" onSubmit={handleSubmit}>
            {mode === 'signup' && (
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="이름"
              />
            )}

            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="계정(이메일)"
            />
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === 'login' ? '비밀번호' : '비밀번호를 입력하세요'}
            />
            {mode === 'signup' && (
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="비밀번호 확인"
              />
            )}
            <button className="primary-button" type="submit" disabled={loading}>
              {loading ? '처리 중...' : mode === 'login' ? '로그인' : '회원가입'}
            </button>
          </form>

          <p className="hint-text">
            {mode === 'login'
              ? '등록되지 않은 이메일은 회원가입 후 바로 사용할 수 있습니다.'
              : '새 계정을 만들고 DocAI를 시작해 보세요.'}
          </p>

          <button
            className="social-button google"
            type="button"
            disabled={loading}
            onClick={() => handleSupabaseSocialLogin('google')}
          >
            <RiGoogleFill className='googleIcon'/>Google 계정 계속하기
          </button>
          <button
            className="social-button apple"
            type="button"
            disabled={loading}
            onClick={() => handleSupabaseSocialLogin('apple')}
          >
            Apple 계정 계속하기
          </button>
          <button
            className="social-button kakao"
            type="button"
            disabled={loading}
            onClick={() => handleSupabaseSocialLogin("kakao")}
           >
            <RiKakaoTalkFill className='kakaoIcon'/> Kakao 계정 계속하기
           </button>
          <div className="legal-block">
            <p>기타 방법으로 계실 수 있습니다.</p>
            <p>계속을 클릭함으로써, 귀하는 우리의 라이선스 계약 및 개인정보 처리방침에 동의하게 됩니다.</p>
          </div>
        </div>
      </div>
      {/* <Link to="/dashboard" className="hidden-link">대시보드로 이동</Link> */}
    </div>
  );
}
