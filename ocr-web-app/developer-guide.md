# PicToText 개발자 가이드

이 문서는 새 팀원이 저장소의 구조, 실행 흐름, 구현 범위를 빠르게 이해하기 위한 코드 분석 문서입니다. 설명은 현재 코드 기준이며, 구현된 기능과 샘플·예정 기능을 구분합니다.

## 1. 프로젝트 한눈에 보기

PicToText는 PDF 텍스트 추출, 문서 기반 AI 질의응답, 사용자 인증과 문서 보관을 한 화면에서 제공하는 웹 애플리케이션입니다.

```text
브라우저(React, Vite)
  ├─ PDF.js: PDF 미리보기 및 문자 레이어 추출
  ├─ Supabase JS: OAuth 세션과 Storage 업로드
  └─ Axios: /api/v1 요청 및 앱 JWT 첨부
                │
                ▼
FastAPI
  ├─ 인증/JWT ─ SQLAlchemy ─ Supabase PostgreSQL
  ├─ Supabase Auth 토큰 검증 및 users 테이블 동기화
  └─ 문서 문맥 전달 ─ Ollama(gemma2:2b)
```

주요 기술은 React, Vite, React Router, Axios, PDF.js, FastAPI, SQLAlchemy, JWT, Supabase, PostgreSQL, Ollama입니다.

## 2. 저장소 구조

```text
ocr-web-app/
├─ .env.example                 환경 변수 예시
├─ docker-compose.yml           frontend/backend/db/ollama 통합 실행
├─ docker-compose.override.yml  개발용 소스 마운트와 hot reload
├─ developer-guide.md           구조 및 코드 분석(현재 문서)
├─ docs/                        최신 Supabase 스키마 및 점검 SQL
├─ frontend/
│  ├─ src/App.jsx               실제 라우트와 보호 라우트
│  ├─ src/api/client.js         Axios 기본 URL 및 JWT interceptor
│  ├─ src/features/appSession.js 앱 세션의 저장·삭제·OAuth 교환
│  ├─ src/lib/supabase.js       브라우저용 Supabase client
│  ├─ src/components/           공통 Sidebar, TopBar, PageTitle
│  ├─ src/pages/                로그인, 대시보드, OCR, 채팅, 리포트 등
│  └─ src/styles.css            애플리케이션 전역 스타일
├─ backend/
│  ├─ main.py                   FastAPI 진입점
│  ├─ app/api/router.py         기능별 router 조합
│  ├─ app/api/routes/           auth, ocr, chatbot, reports, users API
│  ├─ app/core/                 설정, DB, 암호/JWT
│  ├─ app/models/user.py        SQLAlchemy 사용자 모델
│  ├─ app/schemas/              요청·응답 Pydantic 모델
│  └─ app/services/             Supabase, Ollama, OCR 연결 계층
└─ ollama/Dockerfile            gemma2:2b 모델 실행 이미지
```

현재 앱의 라우트는 `frontend/src/App.jsx`에서 관리하며, 화면은 이름이 `Page`로 끝나는 컴포넌트로 구성됩니다.

## 3. 애플리케이션 시작점과 라우팅

### 프론트엔드

`frontend/src/main.jsx`가 React 앱을 마운트하고 `App.jsx`가 다음 URL을 연결합니다.

| URL | 컴포넌트 | 역할 |
|---|---|---|
| `/login` | `LoginPage` | 로컬 회원가입·로그인, Google/Apple OAuth 시작 |
| `/auth/callback` | `AuthCallbackPage` | Supabase 세션을 앱 JWT로 교환 |
| `/dashboard` | `DashboardPage` | 최근 작업, 기능 진입, Storage 업로드 |
| `/ocr` | `OCRPage` | PDF 미리보기와 텍스트 레이어 추출 |
| `/chat` | `ChatPage` | 문서 청크 검색과 Ollama 질의 |
| `/reports` | `ReportPage` | 현재는 정적 리포트 UI |
| `/mypage` | `MyPage` | 현재는 정적 요금제·히스토리 UI |

`ProtectedRoute`는 `localStorage`의 `pic_to_text_token` 유무만 확인합니다. 토큰 만료와 유효성은 페이지 진입 시점에 검증하지 않으므로 향후 보완 대상입니다.

### 백엔드

`backend/main.py`가 FastAPI 앱을 생성합니다.

- 시작 시 `init_db()`로 SQLAlchemy 테이블을 생성합니다.
- CORS 허용 origin은 환경 설정에서 읽습니다.
- 기능별 API는 `/api/v1` 아래에 연결됩니다.
- `GET /health`는 서버 상태 확인용입니다.
- 개발 중 API 명세는 `http://localhost:8000/docs`에서 확인할 수 있습니다.

## 4. 주요 기능의 실제 흐름

### 이메일 회원가입과 로그인

```text
LoginPage
  → POST /api/v1/auth/signup 또는 /auth/login
  → auth.py가 입력·비밀번호 검증
  → SQLAlchemy users 조회/저장
  → 로그인 성공 시 내부 JWT 발급
  → appSession.js가 토큰·이름·이메일을 localStorage에 저장
```

비밀번호는 `pbkdf2_sha256`으로 해시됩니다. JWT에는 사용자 이메일이 `sub`로 들어가며 기본 만료 시간은 60분입니다. 현재 로컬 로그인도 성공 과정에서 Supabase public `users` 테이블 동기화를 시도하므로, Supabase 서버 설정이 없으면 로그인 응답이 실패할 수 있습니다.

### 소셜 로그인

```text
LoginPage → Supabase OAuth(Google/Apple)
  → /auth/callback
  → Supabase access token을 POST /auth/social-login으로 전달
  → 백엔드가 /auth/v1/user에서 토큰 검증
  → 로컬 DB 사용자 생성/조회 + Supabase users 동기화
  → PicToText 내부 JWT 발급
```

브라우저에는 anon key만 두고, `SUPABASE_SERVICE_ROLE_KEY`와 `SECRET_KEY`는 반드시 백엔드 환경에만 둡니다.

### PDF 텍스트 추출

`OCRPage.jsx`가 PDF 파일을 브라우저에서 직접 읽습니다. PDF.js로 각 페이지를 렌더링하고 `getTextContent()`로 PDF 내부 문자 레이어를 추출한 뒤 `.txt`로 내려받을 수 있습니다.

현재 제한사항:

- 이미지로 스캔된 PDF에는 문자 레이어가 없으므로 텍스트를 추출하지 못합니다.
- `POST /api/v1/ocr/upload`는 업로드 파일을 실제 분석하지 않고 고정 샘플을 반환합니다.
- OCR 엔진은 `ocr/app/services/ocr/ocr_service.py`에서 PaddleOCR 기반으로 구현되어 있습니다.

따라서 현재 기능을 “PDF 문자 레이어 추출”이라고 표현하는 것이 정확하며 PaddleOCR 같은 이미지 OCR은 후속 작업입니다.

### AI 문서 채팅

`ChatPage.jsx`는 PDF/TXT/Markdown을 브라우저에서 읽어 900자 단위, 700자 간격으로 청크를 만듭니다. 질문의 키워드가 포함된 청크를 단순 비율로 정렬해 상위 4개를 백엔드로 전송합니다.

백엔드 `POST /api/v1/chatbot/ask`는 전달받은 문맥만 사용하도록 프롬프트를 구성해 Ollama의 `gemma2:2b`에 요청합니다. Ollama 연결 실패 시 프론트엔드는 가장 관련도 높은 원문 청크를 대신 표시합니다.

현재 구조는 벡터 임베딩이나 벡터 DB를 사용하는 완전한 RAG가 아니라 키워드 기반 로컬 검색 + LLM 답변 구조입니다. 문서는 서버에 영구 저장되지 않으며 새로고침하면 채팅 문서와 대화가 사라집니다.

### 대시보드와 클라우드 저장

대시보드의 최근 히스토리는 브라우저 `localStorage`에 저장됩니다. 클라우드 업로드는 Supabase Storage의 `documents` bucket에 파일을 올리지만, 업로드 이력을 백엔드 DB에 저장하지는 않습니다.

## 5. API 구현 상태

| Method | Endpoint | 현재 상태 |
|---|---|---|
| GET | `/health` | 실제 상태 응답 |
| POST | `/api/v1/auth/signup` | 로컬 DB 회원가입 |
| POST | `/api/v1/auth/login` | 비밀번호 검증, JWT 발급, Supabase 동기화 |
| GET | `/api/v1/auth/me` | Bearer JWT 검증 후 사용자 반환 |
| POST | `/api/v1/auth/social-login` | Supabase token 검증 및 내부 JWT 발급 |
| POST | `/api/v1/chatbot/ask` | Ollama `gemma2:2b` 호출 |
| GET | `/api/v1/chatbot/status` | 고정 준비 메시지 |
| POST | `/api/v1/ocr/upload` | 샘플 응답, 실제 OCR 아님 |
| GET | `/api/v1/ocr/history` | 고정 샘플 데이터 |
| GET | `/api/v1/reports` | 고정 샘플 데이터 |
| GET | `/api/v1/reports/similar` | 고정 샘플 데이터 |
| GET | `/api/v1/users/me` | 고정 샘플 사용자 |
| GET | `/api/v1/users/history` | 고정 샘플 데이터 |

## 6. 데이터와 상태 저장 위치

| 데이터 | 저장 위치 | 지속성 |
|---|---|---|
| 로컬 사용자 | SQLAlchemy `users` | DB에 유지 |
| 소셜 인증 사용자 | 로컬 DB + Supabase public `users` | DB에 유지 |
| 앱 JWT·사용자 표시 정보 | 브라우저 localStorage | 브라우저별 유지 |
| 대시보드 최근 기록 | 브라우저 localStorage | 브라우저별 유지 |
| 업로드 원본 | Supabase Storage `documents` bucket | 클라우드 유지 |
| OCR 페이지 텍스트 | React state | 새로고침 시 소멸 |
| 채팅 문서·청크·대화 | React state | 새로고침 시 소멸 |

프론트엔드 인증 상태는 `appSession.js`와 localStorage에서 관리합니다.

## 7. 로컬 실행

### Docker Compose

프로젝트 루트에서 환경 파일을 준비한 뒤 실행합니다.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

- Frontend: `http://localhost:3000`
- Backend: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Ollama: `http://localhost:11434`

Docker 구성은 PostgreSQL을 사용합니다. `ollama/Dockerfile` 빌드 과정에서 `gemma2:2b` 모델 준비 시간이 걸릴 수 있습니다.

### 프론트엔드와 백엔드 개별 실행

```powershell
cd frontend
npm install
npm run dev
```

다른 터미널에서:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

개별 실행 시에도 `DATABASE_URL`에 PostgreSQL 연결 주소가 반드시 필요합니다. 값이 없거나 PostgreSQL 주소가 아니면 백엔드는 시작되지 않습니다. AI 채팅에는 별도로 실행 중인 Ollama와 `gemma2:2b` 모델이 필요하며, 개별 실행 환경에서는 `OLLAMA_BASE_URL=http://localhost:11434`로 설정해야 합니다.

## 8. 환경 변수와 보안

`.env.example`을 복사해 `.env`를 만들고 실제 값을 입력합니다. `.env`는 커밋하지 않습니다.

- 브라우저 공개 가능: `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_API_BASE_URL`
- 서버 전용: `SECRET_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, DB 비밀번호
- 공통 연결: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_USERS_TABLE`

운영 환경에서는 기본 `SECRET_KEY=change-me`와 기본 PostgreSQL 비밀번호를 절대 사용하지 않습니다.

## 9. 새 팀원이 기능을 찾는 순서

1. 화면 경로는 `frontend/src/App.jsx`에서 찾습니다.
2. 화면 동작은 해당 `frontend/src/pages/*Page.jsx`를 확인합니다.
3. HTTP 호출은 `frontend/src/api/client.js`와 호출한 페이지를 확인합니다.
4. 백엔드 URL은 `backend/app/api/router.py`에서 기능 router를 찾습니다.
5. 실제 처리 코드는 `backend/app/api/routes/`와 `backend/app/services/`를 확인합니다.
6. 인증·DB 문제는 `backend/app/core/`와 `backend/app/models/user.py`를 확인합니다.
7. OAuth 설정 문제는 `docs/`의 제공자별 문서를 확인합니다.

## 10. 다음 개발 우선순위

1. PaddleOCR 모델과 문서 유형별 전처리 성능 검증
2. 문서·OCR 결과·처리 상태용 DB 모델과 마이그레이션 추가
3. 키워드 검색을 embedding/vector DB 기반 검색으로 교체
4. 리포트와 마이페이지의 정적 데이터를 실제 API에 연결
5. JWT 만료·401 처리와 로그아웃 흐름 강화
6. 중복된 초기 골격 컴포넌트 정리
7. 백엔드와 프론트엔드 테스트 및 CI 추가

이 문서를 수정할 때는 “예정”, “샘플”, “실제 동작”을 계속 구분해 팀원이 구현 상태를 오해하지 않도록 유지해 주세요.
