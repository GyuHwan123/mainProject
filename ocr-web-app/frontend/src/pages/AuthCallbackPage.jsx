import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { exchangeSupabaseSession } from '../features/appSession';
import { supabase } from '../lib/supabase';
import LoginLoading from '../components/LoginLoading';

let pendingOAuthCallback = null;

async function exchangeOAuthCallback() {
  const params = new URLSearchParams(window.location.search);
  const oauthError = params.get('error_description') || params.get('error');

  if (oauthError) {
    throw new Error(oauthError);
  }

  const code = params.get('code');
  const authResult = code
    ? await supabase.auth.exchangeCodeForSession(code)
    : await supabase.auth.getSession();

  if (authResult.error) {
    throw authResult.error;
  }

  if (!authResult.data?.session) {
    throw new Error('Supabase 로그인 세션이 생성되지 않았습니다.');
  }

  await exchangeSupabaseSession(authResult.data.session);
}

function completeOAuthCallbackOnce() {
  if (!pendingOAuthCallback) {
    pendingOAuthCallback = exchangeOAuthCallback().finally(() => {
      pendingOAuthCallback = null;
    });
  }

  return pendingOAuthCallback;
}

export default function AuthCallbackPage() {
  const navigate = useNavigate();
  const [errorMessage, setErrorMessage] = useState('');

  useEffect(() => {
    let active = true;


     async function completeLogin() {
        try {
          if (!supabase) {
            throw new Error('Supabase 환경변수가 설정되지 않았습니다.');
          }

          await completeOAuthCallbackOnce();

          if (active) {
            navigate('/dashboard', { replace: true });
          }
        } catch (error) {
          if (!active) return;

          setErrorMessage(
            error?.response?.data?.detail ||
            error?.message ||
            '소셜 로그인 처리 중 오류가 발생했습니다.'
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
        <h1>로그인에 실패했습니다</h1>
        <p>{errorMessage}</p>
        <button type="button" className="primary-button" onClick={() => navigate('/login', { replace: true })}>
          로그인 화면으로 돌아가기
        </button>
      </main>
    );
  }

  return <LoginLoading />;
}
