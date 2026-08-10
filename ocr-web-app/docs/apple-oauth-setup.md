# Apple OAuth 세팅 가이드

Apple OAuth는 Google보다 설정 절차가 조금 더 엄격합니다. 일반적으로 Apple Developer Console과 Supabase를 함께 연결해야 하며, `Services ID`, `Team ID`, `Key ID`, `Private Key`가 필요합니다.

## 1. 준비 항목

다음 정보를 준비해야 합니다.

- Apple Developer Team ID
- Services ID
- Key ID
- Private Key 파일 (.p8)
- Supabase 프로젝트 URL

## 2. Apple Developer Console 설정

### 1) App ID 또는 Services ID 생성
1. Apple Developer Console 접속
2. `Certificates, Identifiers & Profiles` 이동
3. `Identifiers` 선택
4. `Services ID` 생성
5. 설명과 Bundle ID 입력
6. `Sign In with Apple` 활성화

### 2) Redirect URL 준비
Apple은 보통 다음 형식의 redirect URL을 사용합니다.

```text
https://<project-ref>.supabase.co/auth/v1/callback
```

예시:
```text
https://geispzqktmloteuapnwb.supabase.co/auth/v1/callback
```

## 3. Supabase에서 Apple 설정

1. Supabase 프로젝트 대시보드 접속
2. `Authentication` > `Providers` > `Apple` 이동
3. `Enable Sign in with Apple` 활성화
4. 아래 값 입력
   - Client ID = Services ID
   - Team ID = Apple Team ID
   - Key ID = Apple Key ID
   - Private Key = .p8 파일 내용

## 4. Apple의 Redirect URL 등록

Apple이 허용하는 redirect URL은 보통 아래와 같은 형태입니다.

```text
https://<project-ref>.supabase.co/auth/v1/callback
```

Supabase Auth가 이 URL을 받아 처리하므로, Apple Developer Console의 설정과 Supabase Provider 설정을 서로 맞춰야 합니다.

## 5. 실제 로그인 흐름

프론트엔드에서 버튼 클릭
  ↓
Supabase OAuth 호출
  ↓
Apple 로그인 처리
  ↓
Supabase 세션 생성
  ↓
우리 서버 `/auth/social-login` 호출
  ↓
내부 JWT 발급
  ↓
대시보드 이동

## 6. 주의사항

### 1) `service_role`와는 별개
Apple OAuth를 설정한다고 해서 `service_role`을 프론트에 넣는 것은 아닙니다.

### 2) `p8` 키 보관
- `.p8` 파일은 서버에 안전하게 보관
- 프론트엔드 코드에 포함 금지
- git에 업로드 금지

### 3) redirect URL 정확성
- 각 환경별로 URL이 다르므로 로컬/배포 환경을 별도로 등록해야 합니다.

## 7. 현재 프로젝트의 권장 설정

### 로컬 개발
```text
https://<project-ref>.supabase.co/auth/v1/callback
```

### 배포 환경
```text
https://<your-domain>/auth/callback
```

단, Supabase Auth 기준으로는 대부분 최종 callback은 아래처럼 프로젝트 Supabase URL을 통한 callback이 가장 표준적입니다.

```text
https://<project-ref>.supabase.co/auth/v1/callback
```

## 8. 체크리스트

- [ ] Apple Developer Console 접근 가능
- [ ] Services ID 생성
- [ ] Sign In with Apple 활성화
- [ ] Team ID 확인
- [ ] Key ID 확인
- [ ] .p8 키 생성 및 보관
- [ ] Supabase Auth Provider에 값 입력
- [ ] Redirect URL 등록

---

Apple OAuth는 구현을 위해서는 Apple Developer Organization 권한이 필요하므로, 팀 계정이 아니면 설정이 제한될 수 있습니다.
