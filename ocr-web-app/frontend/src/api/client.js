import axios from 'axios';

const configuredBaseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const apiBaseUrl = configuredBaseUrl.endsWith('/api/v1')
  ? configuredBaseUrl
  : `${configuredBaseUrl}/api/v1`;

const apiClient = axios.create({
  baseURL: apiBaseUrl,
  timeout: 15000,
});

const SESSION_KEYS = [
  'pic_to_text_token',
  'pic_to_text_email',
  'pic_to_text_name',
  'pic_to_text_role',
];
const PUBLIC_AUTH_PATHS = [
  '/auth/login',
  '/auth/signup',
  '/auth/social-login',
];

function isPublicAuthRequest(config) {
  const requestUrl = config.url || '';
  return PUBLIC_AUTH_PATHS.some((path) => (
    requestUrl === path || requestUrl.startsWith(`${path}?`)
  ));
}

function clearExpiredSession() {
  SESSION_KEYS.forEach((key) => localStorage.removeItem(key));
}

function isTokenExpired(token) {
  try {
    const encoded = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = encoded.padEnd(Math.ceil(encoded.length / 4) * 4, '=');
    const payload = JSON.parse(atob(padded));
    return !payload.exp || payload.exp * 1000 <= Date.now();
  } catch {
    return true;
  }
}

function redirectToLogin() {
  if (window.location.pathname !== '/login') {
    const next = `${window.location.pathname}${window.location.search}`;
    window.location.replace(`/login?next=${encodeURIComponent(next)}`);
  }
}

apiClient.interceptors.request.use((config) => {
  if (isPublicAuthRequest(config)) {
    if (config.headers) delete config.headers.Authorization;
    return config;
  }

  const token = localStorage.getItem('pic_to_text_token');

  if (token) {
    if (isTokenExpired(token)) {
      clearExpiredSession();
      redirectToLogin();
      return Promise.reject(new axios.Cancel('로그인 세션이 만료되었습니다.'));
    }
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }

  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !isPublicAuthRequest(error.config || {})) {
      clearExpiredSession();
      redirectToLogin();
    }
    return Promise.reject(error);
  },
);

export default apiClient;
