# PicToText Developer Guide

이 문서는 동료 개발자가 레포지토리를 빠르게 이해할 수 있도록, 프로젝트 구조와 핵심 코드 분석을 함께 정리한 문서입니다.

## 1. 프로젝트 개요

PicToText는 OCR 기반 문서 처리 서비스를 제공하는 웹 애플리케이션입니다. 사용자 인증, 문서 업로드, OCR 추출, 리포트 관리 흐름을 중심으로 구성되어 있으며, 현재는 로컬 개발 검증 단계와 Supabase 기반 확장 단계가 함께 진행되고 있습니다.

### 핵심 기술
- Frontend: React + Vite
- Backend: FastAPI
- Database: SQLite(로컬), PostgreSQL/Supabase(운영 예정)
- Auth: JWT + Supabase OAuth 준비
- OCR: PaddleOCR 예정

---

## 2. 레포지토리 구조

```text
ocr-web-app/
├── .env.example
├── README.md
├── team-intro-script.md
├── developer-guide.md
├── docker-compose.yml
├── docker-compose.override.yml
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   ├── venv/
│   └── app/
│       ├── api/
│       │   ├── router.py
│       │   └── routes/
│       │       ├── auth.py
│       │       ├── ocr.py
│       │       ├── chatbot.py
│       │       ├── reports.py
│       │       └── users.py
│       ├── core/
│       │   ├── config.py
│       │   ├── database.py
│       │   └── security.py
│       ├── models/
│       │   └── user.py
│       ├── schemas/
│       │   └── auth.py
│       ├── services/
│       │   └── supabase_service.py
│       └── utils/
│           └── ...
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── App.jsx
│       ├── main.jsx
│       ├── api/
│       │   └── client.js
│       ├── components/
│       │   ├── Sidebar.jsx
│       │   ├── TopBar.jsx
│       │   └── PageTitle.jsx
│       ├── features/
│       │   └── socialAuth.js
│       ├── lib/
│       │   └── supabase.js
│       ├── pages/
│       │   ├── LoginPage.jsx
│       │   ├── DashboardPage.jsx
│       │   ├── OCRPage.jsx
│       │   ├── ReportPage.jsx
│       │   └── MyPage.jsx
│       ├── stores/
│       │   └── useAuthStore.js
│       ├── styles.css
│       └── routes/
│           └── AppRoutes.jsx
├── ollama/
│   ├── Dockerfile
│   └── ...
├── docs/
│   ├── supabase-oauth-settings.md
│   ├── google-oauth-redirect-url.md
│   ├── apple-oauth-setup.md
│   └── social-login-test-guide.md
└── .gitignore
```

---

## 3. 핵심 코드 분석

### 3.1 Backend 시작점

파일: `backend/main.py`

핵심 역할:
- FastAPI 앱 생성
- 시작 시 DB 초기화
- CORS 설정
- `/api/v1` 라우터 연결
- `/health` 체크 엔드포인트 제공

코드 의도:
- 애플리케이션 시작과 초기 데이터베이스 초기화가 중앙에서 관리됨
- API 모듈 분리 구조로 유지보수 쉬움

### 3.2 DB 연결 설정

파일: `backend/app/core/database.py`

핵심 역할:
- SQLAlchemy 엔진 생성
- SessionLocal 생성
- `Base.metadata.create_all()` 호출

중요 포인트:
- 로컬 개발에서는 SQLite 사용
- 이후 PostgreSQL/Supabase로 전환 가능
- 앱 시작 시 테이블을 자동 생성하는 구조

### 3.3 환경 설정 관리

파일: `backend/app/core/config.py`

핵심 역할:
- `.env` 값을 읽어 settings 객체 구성
- DB URL, JWT 비밀키, Supabase 값 관리

중요 포인트:
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` 분리 관리
- 프론트엔드에 노출하면 안 되는 값을 서버에서만 사용

### 3.4 인증 로직

파일: `backend/app/core/security.py`

핵심 역할:
- 비밀번호 해시 생성
- 비밀번호 검증
- JWT 발급/복호화

중요 포인트:
- `pbkdf2_sha256` 사용
- 로컬/서버 환경에서 비교적 안정적으로 동작

### 3.5 사용자 모델

파일: `backend/app/models/user.py`

중요 필드:
- `id`
- `name`
- `email`
- `password_hash`
- `provider`
- `provider_id`
- `is_active`
- `created_at`
- `updated_at`

의도:
- 로컬 로그인 + 소셜 로그인 모두 같은 사용자 테이블에서 관리
- 향후 OAuth provider별 고유 ID를 저장할 수 있음

### 3.6 Auth API

파일: `backend/app/api/routes/auth.py`

핵심 엔드포인트:
- `POST /auth/signup`
- `POST /auth/login`
- `GET /auth/me`
- `POST /auth/social-login`

동작 요약:
- 회원가입: 사용자 생성
- 로그인: 비밀번호 검증 → JWT 발급
- 소셜 로그인: Supabase 토큰 검증 → user 조회/생성 → 내부 JWT 발급

### 3.7 Supabase 연동 서비스

파일: `backend/app/services/supabase_service.py`

핵심 역할:
- Supabase Access Token을 검증
- 사용자 정보를 가져옴
- `email`, `id`, `name` 추출

중요 포인트:
- `anon key`를 사용해 공개적으로 접근 가능한 사용자 정보 검증
- `service_role key`는 이 파일에서 설정되더라도 서버 밖으로 노출하면 안 됨

---

## 4. Frontend 구조 분석

### 4.1 App 라우팅

파일: `frontend/src/App.jsx`

핵심 역할:
- `/login`, `/dashboard`, `/ocr`, `/reports`, `/mypage` 라우팅
- 보호 라우트 설정
- localStorage token 기반 인증 상태 확인
- Supabase 세션과 앱 JWT 동기화

### 4.2 로그인 페이지

파일: `frontend/src/pages/LoginPage.jsx`

핵심 기능:
- 로그인 모드/회원가입 모드 전환
- 이름, 이메일, 비밀번호, 비밀번호 확인 입력
- 로그인 성공 시 JWT 저장 후 `/dashboard` 이동
- Google/Apple 버튼 준비

### 4.3 API 클라이언트

파일: `frontend/src/api/client.js`

핵심 역할:
- Axios 인스턴스 생성
- `Authorization: Bearer <token>` 자동 첨부
- 로그인 상태를 API 호출에 연결

### 4.4 Supabase 클라이언트

파일: `frontend/src/lib/supabase.js`

핵심 역할:
- `createClient` 초기화
- `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` 사용
- 브라우저 세션 지속화 설정

### 4.5 Social Auth 구성

파일: `frontend/src/features/socialAuth.js`

핵심 역할:
- provider 리스트 구성
- Google/Apple provider 상태를 추적
- 향후 실제 provider 설정 확장 가능

---

## 5. 인증 흐름 정리

### 로컬 이메일/비밀번호 로그인
```text
사용자 입력 → POST /auth/login → 비밀번호 검증 → JWT 발급 → localStorage 저장 → /dashboard 이동
```

### 소셜 로그인
```text
버튼 클릭 → Supabase OAuth → 세션 획득 → POST /auth/social-login → Supabase 토큰 검증 → 내부 JWT 발급 → localStorage 저장 → /dashboard 이동
```

### 보호 페이지 접근
```text
ProtectedRoute → localStorage token 확인 → 없으면 /login 이동
```

---

## 6. DB 동작 방식

### 로컬 개발
- SQLite 파일 기반으로 동작
- `DATABASE_URL=sqlite:///./pic_to_text_dev.db`
- 빠른 테스트와 개발 편의성

### 운영 환경
- Supabase/PostgreSQL 전환 가능
- `users` 테이블을 중심으로 사용자 관리
- provider 기반 사용자 구분 가능

---

## 7. 중요한 보안 포인트

### 공개 가능
- `VITE_SUPABASE_ANON_KEY`
- `SUPABASE_ANON_KEY`

### 서버에서만 사용
- `SUPABASE_SERVICE_ROLE_KEY`
- `SECRET_KEY`

### 실수 방지
- 프론트엔드 코드에 `service_role` 키를 넣지 않기
- 브라우저에서 실행되는 JS에 비밀값 보관 금지

---

## 8. 현재 진행 상황

### 완료된 영역
- 프로젝트 구조 구성
- 로그인/회원가입 UI
- JWT 기반 인증
- 보호 라우트
- DB 기반 사용자 저장
- Supabase OAuth 준비 구조
- 로컬 검증 완료

### 다음 우선순위
1. PaddleOCR 연결
2. OCR 업로드 API 구현
3. 대시보드 OCR UI 연결
4. 결과 저장 및 표시
5. Supabase 운영 DB 전환

---

## 9. 협업 팁

- 로컬 개발은 SQLite로 빠르게 검증
- 기능별로 서버/프론트 분리 개발
- 인증 관련 이슈는 auth.py와 security.py 중심으로 확인
- Supabase 관련 설정은 docs 폴더 문서를 우선 참조
- DB 문제는 `DATABASE_URL`과 provider/host 구분을 가장 먼저 점검

---

## 10. 가장 빠르게 확인하는 방법

### Backend 확인
```powershell
cd "c:\Users\2Class_13\Desktop\main-ocr-project\ocr-web-app\backend"
$env:DATABASE_URL='sqlite:///./pic_to_text_dev.db'
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

### Frontend 확인
```powershell
cd "c:\Users\2Class_13\Desktop\main-ocr-project\ocr-web-app\frontend"
npm install
npm run dev
```

### 브라우저 접근
```text
http://localhost:3000
```

---

이 문서는 팀원들이 프로젝트를 이해하고, 기능 위치를 빠르게 찾을 수 있도록 정리한 가이드입니다. 필요한 경우 각 기능별 세부 설명 문서나 API 문서로도 확장할 수 있습니다.
