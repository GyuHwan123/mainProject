# Supabase 프로젝트 OAuth 설정값 정리

이 문서는 PicToText 프로젝트에서 Supabase OAuth를 사용할 때 필요한 설정 값과 보안 기준을 정리한 문서입니다.

## 1. 필요한 값

### 프로젝트 공용 값
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`

### 프론트엔드용 값
- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_ANON_KEY`

### 백엔드용 값
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SECRET_KEY`

## 2. 어디에 넣는가

### 프론트엔드 (.env)
```env
VITE_SUPABASE_URL=https://<project-ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<anon-key>
```

### 백엔드 (.env)
```env
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<anon-key>
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
SECRET_KEY=your-secret-key
```

## 3. 보안 규칙

### 반드시 서버에서만 사용
`SUPABASE_SERVICE_ROLE_KEY`는 관리자 권한 키입니다. 브라우저, React, Vite, Vue, Next.js 클라이언트 코드에 절대 노출하지 마십시오.

### 공개 가능한 값
- `SUPABASE_ANON_KEY`
- `VITE_SUPABASE_ANON_KEY`

### 비공개 값
- `SUPABASE_SERVICE_ROLE_KEY`
- `SECRET_KEY`

## 4. Supabase 대시보드에서 확인하는 위치

1. Supabase 프로젝트 대시보드 접속
2. 좌측 메뉴에서 `Settings` > `API` 진입
3. 아래 값 확인
   - Project URL
   - anon/public key
   - service_role key
4. `Auth` > `Providers`에서 OAuth provider 활성화

## 5. Google / Apple 연동을 위한 추가 값

소셜 로그인을 실제로 쓰려면 각 제공자별 Client ID / Client Secret 값 또는 provider 설정이 필요합니다.

- Google
  - Client ID
  - Client Secret
- Apple
  - Services ID
  - Team ID
  - Key ID
  - Private Key

프로젝트에서는 서버에서 별도 검증 로직을 두고, 프론트엔드는 Supabase OAuth만 호출하는 구조를 권장합니다.

## 6. 프로젝트에서 권장 구조

- 프론트엔드
  - `supabase.auth.signInWithOAuth()` 호출
  - `session.access_token` 전달
- 백엔드
  - `/auth/social-login`에서 Supabase token 검증
  - 사용자 생성/조회
  - 내부 JWT 발급

## 7. 체크리스트

- [ ] Supabase URL 저장
- [ ] anon key 저장
- [ ] service_role key는 서버만 보유
- [ ] Google provider 활성화
- [ ] Apple provider 활성화
- [ ] redirect URL 등록
- [ ] 프론트엔드 `.env`에 공개 값만 추가

---

필요하면 다음 단계로 바로 이어서 실제 Google / Apple Provider 설정 절차를 상세 문서로 정리해 드릴 수 있습니다.
