import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { exchangeSocialSession } from '../features/appSession';
import { supabase } from '../lib/supabase';
import apiClient from '../api/client';
import LoginLoading from '../components/LoginLoading';
import '../style/LoginPage.scss';

let pendingOAuthCallback = null;

async function exchangeOAuthCallback() {
  const params = new URLSearchParams(window.location.search);
  const oauthError = params.get('error_description') || params.get('error');
  const calendarConnect = sessionStorage.getItem('docunex_google_calendar_connect') === '1';
  if (oauthError) {
    const message = /access_denied/i.test(oauthError)
      ? 'Google Calendar 권한이 승인되지 않았습니다. 테스트 중인 앱은 관리자가 Google Cloud의 테스트 사용자로 등록한 계정만 연결할 수 있습니다.'
      : oauthError;
    if (calendarConnect) {
      sessionStorage.removeItem('docunex_google_calendar_connect');
      sessionStorage.setItem('docunex_dashboard_notice', message);
      return;
    }
    throw new Error(message);
  }

  const code = params.get('code');
  const authResult = code
    ? await supabase.auth.exchangeCodeForSession(code)
    : await supabase.auth.getSession();
  if (authResult.error) throw authResult.error;
  if (!authResult.data?.session) throw new Error('소셜 로그인 세션이 생성되지 않았습니다.');
  if (calendarConnect) {
    try {
      const providerToken = authResult.data.session.provider_token;
      if (!providerToken) throw new Error('Google Calendar 접근 토큰을 받지 못했습니다. Google 계정 권한을 다시 승인해 주세요.');
      const response = await apiClient.post('/dashboard/calendar/google/import', { provider_access_token: providerToken }, { timeout: 45000 });
      sessionStorage.setItem('docunex_dashboard_notice', response.data.imported
        ? `Google Calendar 일정 ${response.data.imported}건을 가져왔습니다.`
        : 'Google Calendar가 최신 상태입니다.');
    } catch (error) {
      sessionStorage.setItem(
        'docunex_dashboard_notice',
        error?.response?.data?.detail || error?.message || 'Google Calendar를 가져오지 못했습니다. Google 계정 권한을 확인해 주세요.',
      );
    } finally {
      sessionStorage.removeItem('docunex_google_calendar_connect');
    }
    return;
  }
  await exchangeSocialSession(authResult.data.session);
}

function completeOAuthCallbackOnce() {
  if (!pendingOAuthCallback) {
    pendingOAuthCallback = exchangeOAuthCallback().finally(() => { pendingOAuthCallback = null; });
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
        if (!supabase) throw new Error('소셜 로그인 환경 변수가 설정되지 않았습니다.');
        await completeOAuthCallbackOnce();
        if (active) navigate('/dashboard', { replace: true });
      } catch (error) {
        if (active) setErrorMessage(error?.response?.data?.detail || error?.message || '소셜 로그인 처리 중 오류가 발생했습니다.');
      }
    }
    completeLogin();
    return () => { active = false; };
  }, [navigate]);

  if (errorMessage) return <main className="auth-callback" role="alert"><h1>로그인에 실패했습니다</h1><p>{errorMessage}</p><button type="button" className="primary-button" onClick={() => navigate('/login', { replace: true })}>로그인 화면으로 돌아가기</button></main>;
  return <LoginLoading />;
}
