# Google 소셜 로그인 설정

이 프로젝트는 Google OAuth를 Supabase Auth를 통해 처리한 뒤, 백엔드의 앱 JWT로 교환합니다.

## 1. Google Cloud Console

Google OAuth 2.0 웹 클라이언트의 `승인된 리디렉션 URI`에는 Supabase 콜백 URL을 등록합니다.

```text
https://<project-ref>.supabase.co/auth/v1/callback
```

앱의 `/auth/callback` 주소가 아니라 Supabase 콜백 주소여야 합니다.

## 2. Supabase Dashboard

1. `Authentication > Providers > Google`에서 Google provider를 활성화합니다.
2. Google Client ID와 Client Secret을 입력합니다.
3. `Authentication > URL Configuration`의 Redirect URLs에 아래 주소를 추가합니다.

```text
http://localhost:3000/auth/callback
https://<production-domain>/auth/callback
```

Site URL에는 기본 앱 주소를 설정합니다(로컬: `http://localhost:3000`).

## 3. 환경변수

루트의 `.env.example`을 참고해 `.env`를 만들고 실제 값을 입력합니다. 로컬에서 Vite를 직접 실행한다면 `VITE_` 변수는 `frontend/.env`에도 설정합니다.

```env
VITE_SUPABASE_URL=https://<project-ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<anon-key>
VITE_API_BASE_URL=http://localhost:8000/api/v1

SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<anon-key>
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
SECRET_KEY=<long-random-secret>
```

`SUPABASE_SERVICE_ROLE_KEY`와 `SECRET_KEY`는 프론트엔드에 노출하면 안 됩니다.

## 4. 동작 흐름

1. 로그인 화면에서 Google 버튼 클릭
2. Google 인증 후 `/auth/callback`으로 복귀
3. Supabase access token을 `/api/v1/auth/social-login`으로 전달
4. 백엔드가 Supabase 사용자 정보를 검증하고 로컬 사용자를 생성 또는 연결
5. 앱 JWT 저장 후 `/dashboard`로 이동
