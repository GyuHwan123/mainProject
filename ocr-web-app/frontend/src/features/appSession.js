import apiClient from '../api/client';

const TOKEN_KEY = 'pic_to_text_token';
const EMAIL_KEY = 'pic_to_text_email';
const NAME_KEY = 'pic_to_text_name';
const ROLE_KEY = 'pic_to_text_role';
const SUBSCRIPTION_TIER_KEY = 'pic_to_text_subscription_tier';
let pendingExchange = null;

export function hasAppSession() {
  return Boolean(localStorage.getItem(TOKEN_KEY));
}

export function saveAppSession(session) {
  localStorage.setItem(TOKEN_KEY, session.access_token);
  localStorage.setItem(EMAIL_KEY, session.user_email);
  if (session.user_name) localStorage.setItem(NAME_KEY, session.user_name);
  localStorage.setItem(ROLE_KEY, session.user_role || 'USER');
  localStorage.setItem(SUBSCRIPTION_TIER_KEY, session.user_subscription_tier || 'PERSONAL');
}

export function clearAppSession() {
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
    subscriptionTier: localStorage.getItem(SUBSCRIPTION_TIER_KEY) || 'PERSONAL',
  };
}

export function saveAppUser(user) {
  if (user?.name) localStorage.setItem(NAME_KEY, user.name);
  if (user?.email) localStorage.setItem(EMAIL_KEY, user.email);
  if (user?.role) localStorage.setItem(ROLE_KEY, user.role);
  if (user?.subscription_tier) localStorage.setItem(SUBSCRIPTION_TIER_KEY, user.subscription_tier);
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
      }, { timeout: 45000 })
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
