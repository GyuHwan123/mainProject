import apiClient from '../api/client';

const TOKEN_KEY = 'pic_to_text_token';
const EMAIL_KEY = 'pic_to_text_email';
const NAME_KEY = 'pic_to_text_name';
let pendingExchange = null;

export function hasAppSession() {
  return Boolean(localStorage.getItem(TOKEN_KEY));
}

export function saveAppSession(session) {
  localStorage.setItem(TOKEN_KEY, session.access_token);
  localStorage.setItem(EMAIL_KEY, session.user_email);
  if (session.user_name) localStorage.setItem(NAME_KEY, session.user_name);
}

export function clearAppSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(EMAIL_KEY);
  localStorage.removeItem(NAME_KEY);
}

export function getAppUser() {
  return {
    name: localStorage.getItem(NAME_KEY) || '',
    email: localStorage.getItem(EMAIL_KEY) || '',
  };
}

export function saveAppUser(user) {
  if (user?.name) localStorage.setItem(NAME_KEY, user.name);
  if (user?.email) localStorage.setItem(EMAIL_KEY, user.email);
}

export async function exchangeSupabaseSession(session) {
  if (!session?.access_token) {
    throw new Error('Google 로그인 세션을 확인할 수 없습니다.');
  }

  if (!pendingExchange) {
    pendingExchange = apiClient
      .post('/auth/social-login', {
        provider: 'supabase',
        token: session.access_token,
      })
      .then((response) => {
        saveAppSession(response.data);
        return response.data;
      })
      .finally(() => {
        pendingExchange = null;
      });
  }

  return pendingExchange;
}
