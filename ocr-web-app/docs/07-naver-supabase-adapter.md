# NAVER Custom OAuth adapter

This project keeps the existing Supabase OAuth callback and app-session exchange flow.
The Edge Function below only converts NAVER's nested profile payload into the standard user-info shape required by Supabase.

## Supabase Custom OAuth provider

Deploy the `naver-userinfo` Edge Function, then set the `custom:naver` provider's **UserInfo URL** to:

```text
https://YOUR_PROJECT_REF.supabase.co/functions/v1/naver-userinfo
```

Keep NAVER's application callback URL set to the Supabase callback URL shown on the provider configuration screen. Keep the frontend callback URL (`https://YOUR-WEB-HOST/auth/callback`) in Supabase Authentication > URL Configuration > Redirect URLs.

The adapter requires the NAVER profile API to return an email address. Enable email consent for NAVER Login in NAVER Developers. It forwards the access token only to `https://openapi.naver.com/v1/nid/me`; it does not store or log the token.

Deploy with:

```bash
supabase login
supabase functions deploy naver-userinfo --project-ref YOUR_PROJECT_REF
```
