export const socialProviders = [
  {
    id: 'google',
    label: 'Google',
    enabled: true,
    status: 'Supabase OAuth 준비 완료',
  },
  {
    id: 'apple',
    label: 'Apple',
    enabled: true,
    status: 'Supabase OAuth 준비 완료',
  },
];

export function getSocialLoginConfig() {
  return socialProviders;
}
