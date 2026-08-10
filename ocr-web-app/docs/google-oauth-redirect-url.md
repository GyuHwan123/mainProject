# Google OAuth용 redirect URL 문서화

Google OAuth 설정 시 가장 중요한 부분은 Redirect URL입니다. Supabase에서 OAuth provider를 활성화할 때, 정확히 같은 URL을 등록해야 로그인 후 정상 복귀가 가능합니다.

## 1. 기본 원칙

Supabase의 OAuth는 보통 다음 흐름으로 동작합니다.

1. 사용자가 프론트엔드 버튼 클릭
2. Supabase OAuth 페이지로 이동
3. Google 로그인 완료
4. Redirect URL로 다시 돌아옴
5. 프론트엔드가 세션을 읽고 백엔드에 토큰 전달

## 2. 로컬 개발용 Redirect URL

로컬 개발 환경에서 가장 일반적인 값은 아래와 같습니다.

```text
http://localhost:3000/dashboard
```

또는 로그인 페이지로 돌려보내는 구조를 쓸 경우:

```text
http://localhost:3000/
```

현재 프로젝트는 로그인 성공 후 `/dashboard` 이동을 기본으로 하고 있으므로, 로컬 개발 시 권장값은 아래입니다.

```text
http://localhost:3000/dashboard
```

## 3. Supabase 설정에서 등록하는 방식

### 방법
1. Supabase 대시보드로 이동
2. `Authentication` > `Providers` > `Google` 선택
3. `Enable Sign in with Google` 활성화
4. 아래 값 등록
   - Authorized redirect URLs

예시:
```text
http://localhost:3000/dashboard
https://your-domain.com/dashboard
```

## 4. 현재 프로젝트에 맞는 설정 예시

### 로컬 개발
```text
http://localhost:3000/dashboard
```

### 배포 개발
```text
https://your-app-domain.com/dashboard
```

### 예외: 로그인 페이지로 리디렉션을 원할 경우
```text
http://localhost:3000/
https://your-app-domain.com/
```

## 5. 프론트엔드 코드에서의 redirectTo 설정

현재 프로젝트의 로그인 버튼은 아래와 같은 구조를 사용하고 있습니다.

```js
redirectTo: `${window.location.origin}/dashboard`
```

즉, 로컬에서는:

```text
http://localhost:3000/dashboard
```

배포 환경에서는:

```text
https://your-app-domain.com/dashboard
```

로 변환됩니다.

## 6. 자주 발생하는 문제

### 문제 1: Redirect URL이 정확히 일치하지 않음
- `http://localhost:3000/dashboard` 와 `http://localhost:3000/dashboard/` 는 다르게 인식될 수 있음
- 끝 슬래시 유무를 맞춰야 합니다.

### 문제 2: Google OAuth Client 설정과 Supabase 설정 불일치
- Supabase에서 등록한 URL과 Google Cloud Console에서 허용한 URL이 다르면 실패

### 문제 3: 프론트엔드 경로가 잘못됨
- 로그인 성공 후 `/dashboard`가 아닌 `/login`으로 다시 되돌아가는 구조면 사용자 경험이 깨집니다.

## 7. 체크리스트

- [ ] 로컬 Redirect URL 등록
- [ ] 배포 Redirect URL 등록
- [ ] `window.location.origin` 기준 경로와 일치
- [ ] `/dashboard` 경로가 실제 존재하는 페이지인지 확인
- [ ] Supabase Auth Provider에서 Google 활성화

## 8. 권장 값 요약

```text
http://localhost:3000/dashboard
https://your-domain.com/dashboard
```

이 값만 맞으면 로컬 개발과 실제 배포 환경 모두 안정적으로 구동됩니다.
