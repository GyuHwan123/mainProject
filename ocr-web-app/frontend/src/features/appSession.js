import apiClient from '../api/client';

const TOKEN_KEY = 'pic_to_text_token';
const EMAIL_KEY = 'pic_to_text_email';
const NAME_KEY = 'pic_to_text_name';
const ROLE_KEY = 'pic_to_text_role';
const SUBSCRIPTION_TIER_KEY = 'pic_to_text_subscription_tier';
const ACTIVE_CHAT_SESSION_KEY = 'docunex_active_chat_session';
const CHAT_STATE_KEY_PREFIX = 'docunex_chat_state:';
let pendingSocialExchange = null;

export function hasAppSession() {
  return Boolean(localStorage.getItem(TOKEN_KEY));
}

export function saveAppSession(session) {
  localStorage.removeItem(`${CHAT_STATE_KEY_PREFIX}${session.user_email || 'anonymous'}`);
  localStorage.removeItem(ACTIVE_CHAT_SESSION_KEY);
  localStorage.setItem(TOKEN_KEY, session.access_token);
  localStorage.setItem(EMAIL_KEY, session.user_email);
  if (session.user_name) localStorage.setItem(NAME_KEY, session.user_name);
  localStorage.setItem(ROLE_KEY, session.user_role || 'USER');
  localStorage.setItem(SUBSCRIPTION_TIER_KEY, session.user_subscription_tier || 'FREE');
}

export function clearAppSession() {
  const email = localStorage.getItem(EMAIL_KEY) || '';
  localStorage.removeItem(`${CHAT_STATE_KEY_PREFIX}${email || 'anonymous'}`);
  localStorage.removeItem(ACTIVE_CHAT_SESSION_KEY);
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(EMAIL_KEY);
  localStorage.removeItem(NAME_KEY);
  localStorage.removeItem(ROLE_KEY);
  localStorage.removeItem(SUBSCRIPTION_TIER_KEY);
}

export function getAppUser() {
  return {
    name: localStorage.getItem(NAME_KEY) || '',
    email: localStorage.getItem(EMAIL_KEY) || '',
    role: localStorage.getItem(ROLE_KEY) || 'USER',
    subscriptionTier: localStorage.getItem(SUBSCRIPTION_TIER_KEY) || 'FREE',
  };
}

export function saveAppUser(user) {
  if (user?.name) localStorage.setItem(NAME_KEY, user.name);
  if (user?.email) localStorage.setItem(EMAIL_KEY, user.email);
  if (user?.role) localStorage.setItem(ROLE_KEY, user.role);
  if (user?.subscription_tier) localStorage.setItem(SUBSCRIPTION_TIER_KEY, user.subscription_tier);
}

export async function exchangeSocialSession(session) {
  if (!session?.access_token) throw new Error('소셜 로그인 세션을 확인할 수 없습니다.');
  if (!pendingSocialExchange) {
    pendingSocialExchange = apiClient.post('/auth/oauth/exchange', {
      provider: 'supabase',
      token: session.access_token,
      provider_access_token: session.provider_token || null,
    }, { timeout: 45000 }).then((response) => {
      saveAppSession(response.data);
      if (response.data.calendar_sync_error) {
        sessionStorage.setItem('docunex_dashboard_notice', response.data.calendar_sync_error);
      } else if (response.data.calendar_imported > 0) {
        sessionStorage.setItem('docunex_dashboard_notice', `Google Calendar 일정 ${response.data.calendar_imported}건을 가져왔습니다.`);
      } else if (session.provider_token) {
        sessionStorage.setItem('docunex_dashboard_notice', 'Google Calendar가 최신 상태입니다.');
      }
      return response.data;
    }).finally(() => { pendingSocialExchange = null; });
  }
  return pendingSocialExchange;
}
