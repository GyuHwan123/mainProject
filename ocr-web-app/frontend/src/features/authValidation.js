export const MAX_PASSWORD_LENGTH = 200;

export function isValidEmail(value) {
  const email = value.trim();
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email);
}

export function authErrorMessage(error, fallback = '인증 처리 중 오류가 발생했습니다.') {
  if (error?.response?.status === 429) return '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.';
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length) {
    const message = detail[0]?.msg || '';
    if (/email address/i.test(message)) return '올바른 이메일 형식을 입력해 주세요. 예: name@company.com';
    if (message) return message.replace(/^Value error,\s*/i, '');
  }
  return error?.message || fallback;
}
