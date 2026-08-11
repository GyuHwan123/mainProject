import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { exchangeSupabaseSession } from '../features/appSession';
import { supabase } from '../lib/supabase';

export default function AuthCallbackPage() {
  const navigate = useNavigate();
  const [errorMessage, setErrorMessage] = useState('');

  useEffect(() => {
    let active = true;

    async function completeLogin() {
      try {
        if (!supabase) throw new Error('Supabase 환경변수가 설정되지 않았습니다.');

        const { data, error } = await supabase.auth.getSession();
        if (error) throw error;

        await exchangeSupabaseSession(data.session);
        if (active) navigate('/dashboard', { replace: true });
      } catch (error) {
        if (!active) return;
        setErrorMessage(
          error?.response?.data?.detail ||
            error?.message ||
            'Google 로그인 처리 중 오류가 발생했습니다.',
        );
      }
    }

    completeLogin();
    return () => {
      active = false;
    };
  }, [navigate]);

  if (errorMessage) {
    return (
      <main className="auth-callback" role="alert">
        <h1>Google 로그인에 실패했습니다</h1>
        <p>{errorMessage}</p>
        <button type="button" className="primary-button" onClick={() => navigate('/login', { replace: true })}>
          로그인 화면으로 돌아가기
        </button>
      </main>
    );
  }

  return (
    <main className="auth-callback" aria-live="polite">
      <h1>Google 로그인 처리 중</h1>
      <p>계정 정보를 확인하고 있습니다. 잠시만 기다려 주세요.</p>
    </main>
  );
}
