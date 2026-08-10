# 실제 소셜 로그인 테스트용 튜토리얼

이 문서는 PicToText 프로젝트에서 소셜 로그인 기능을 실제로 테스트하는 절차를 정리한 문서입니다.

## 1. 사전 준비

다음 항목이 준비되어 있어야 합니다.

- Supabase 프로젝트 생성 완료
- Google 또는 Apple provider 활성화
- 프론트엔드 `.env`에 공개 키 값 입력
- 백엔드 `.env`에 서버용 값 입력
- 로컬 백엔드 실행 중
- 로컬 프론트엔드 실행 중

## 2. 필수 환경 변수 예시

### 프론트엔드
```env
VITE_SUPABASE_URL=https://<project-ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<anon-key>
VITE_API_BASE_URL=http://localhost:8001
```

### 백엔드
```env
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<anon-key>
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
SECRET_KEY=change-this-secret-key
DATABASE_URL=sqlite:///./pic_to_text_dev.db
```

## 3. 서버 실행

### 백엔드 실행
```powershell
cd "c:\Users\2Class_13\Desktop\main-ocr-project\ocr-web-app\backend"
$env:DATABASE_URL='sqlite:///./pic_to_text_dev.db'
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

### 프론트엔드 실행
```powershell
cd "c:\Users\2Class_13\Desktop\main-ocr-project\ocr-web-app\frontend"
npm install
npm run dev
```

## 4. 브라우저에서 테스트

1. 브라우저에서 `http://localhost:3000` 접속
2. 로그인 페이지에서 `Google 계정 계속하기` 또는 `Apple 계정 계속하기` 클릭
3. Supabase 인증 페이지로 이동
4. 계정 선택 또는 로그인
5. Redirect URL로 복귀
6. 대시보드 페이지로 이동
7. 브라우저의 localStorage를 확인

확인할 키:
```text
pic_to_text_token
pic_to_text_email
```

## 5. 서버 검증

백엔드가 정상적으로 토큰을 받았는지 확인하려면 아래 경로를 확인합니다.

```text
POST /api/v1/auth/social-login
```

예시 body:
```json
{
  "provider": "supabase",
  "token": "...supabase-access-token..."
}
```

정상 응답 예시:
```json
{
  "access_token": "...jwt...",
  "token_type": "bearer",
  "user_email": "user@example.com",
  "user_name": "User Name"
}
```

## 6. 로그 확인 포인트

### 프론트엔드 확인
- `supabase.auth.getSession()`이 세션을 받는지
- `localStorage.setItem('pic_to_text_token', ...)`가 실행되는지
- `/dashboard`로 이동하는지

### 백엔드 확인
- `/auth/social-login`의 `payload.provider` 값
- Supabase token 검증 성공 여부
- 사용자 생성 또는 기존 사용자 조회 여부
- 최종 JWT 발급 여부

## 7. 자주 나는 오류

### 1) `AuthApiError`
- Supabase URL 또는 anon key가 잘못됨
- Redirect URL 불일치

### 2) `/social-login` 401 오류
- Supabase access token이 만료되었거나 잘못됨
- 토큰 전달이 누락됨

### 3) 브라우저에서 대시보드 접근 불가
- `pic_to_text_token` 저장 실패
- 로그인 후 redirect 경로 문제

## 8. 체크리스트

- [ ] Supabase 프로젝트 설정 완료
- [ ] Google/Apple provider 활성화
- [ ] Redirect URL 등록
- [ ] `.env` 값 입력
- [ ] 백엔드 실행
- [ ] 프론트엔드 실행
- [ ] 로그인 버튼 클릭
- [ ] 대시보드 이동 확인
- [ ] 토큰 저장 확인

## 9. 최종 목표

이 테스트가 완료되면 다음 흐름이 모두 정상입니다.

```text
버튼 클릭 -> Supabase OAuth -> 토큰 수신 -> 서버에서 앱 JWT 발급 -> 대시보드 진입
```

이 상태가 되면 실제 서비스 운영 단계로 들어갈 준비가 완료됩니다.
