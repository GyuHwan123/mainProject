# PicToText

## Docker Compose 실행

이 프로젝트는 외부 Supabase PostgreSQL/Storage만 사용합니다. Compose에는 로컬
DB 서비스가 없으므로 `.env`에 Supabase 프로젝트 값을 설정해야 합니다.

```powershell
cd ocr-web-app
Copy-Item .env.example .env
# .env에서 SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY,
# VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY, SECRET_KEY를 실제 값으로 교체
docker compose up --build
```

접속 주소는 frontend `http://localhost:3000`, backend health
`http://localhost:8000/health`, OCR health `http://localhost:8001/health`,
Ollama `http://localhost:11434`입니다. 브라우저의 frontend는
`VITE_API_BASE_URL=http://localhost:8000/api/v1`로 backend에 연결하고,
backend 컨테이너는 `http://ocr:8001`, `http://ollama:11434` 내부 주소를
사용합니다.

기본 RAG 모델은 `gemma2:2b`이며 첫 기동 시 Ollama가 이를 내려받습니다.
`ollama_data` named volume에 모델 데이터가 보존되므로 이후 기동에서는 다시
받지 않습니다. 상태는 다음으로 확인할 수 있습니다.

```powershell
docker compose ps
docker compose exec ollama ollama list
```

## QLoRA GGUF RAG 모델 설치

1. 변환한 GGUF 파일을 `models/rag/my-qlora-rag.gguf`에 둡니다. 이 파일은 Git에
   포함되지 않습니다.
2. `.env`에 아래처럼 별도 Ollama 모델명과 경로를 지정합니다.

```env
RAG_LLM_MODEL=my-qlora-rag:latest
OLLAMA_RAG_MODEL=my-qlora-rag:latest
OLLAMA_RAG_GGUF_PATH=/models/rag/my-qlora-rag.gguf
```

3. `docker compose up --build`를 실행합니다. Ollama 시작 스크립트가 GGUF를
   `my-qlora-rag:latest`로 한 번 등록합니다. `ollama_data` volume을 유지하는 한
   재등록하지 않습니다. 기본 `gemma2:2b`로 돌아가려면 위 세 값을 비우고
   `RAG_LLM_MODEL=gemma2:2b`로 설정합니다.

보안상 `.env`, `.safetensors`, `.gguf`, Hugging Face 인증 정보는 Git에 추가하지
마세요.

## 영수증 전체 GGUF 모델 설치

전체 모델 GGUF는 `models/receipts/`에 두고, 해당 폴더의 `Modelfile`에서 직접
참조합니다. 현재 구성은 `gemma3-4b-trained-v2.gguf`를
`gemma3-4b-trained:latest`로 등록합니다. `.env`에 다음 값을 설정한 뒤
`docker compose up --build`를 실행합니다.

```env
RECEIPTS_LLM_MODEL=gemma3-4b-trained:latest
OLLAMA_RECEIPTS_MODELFILE=/models/receipts/Modelfile
```

GGUF는 Git에 포함되지 않으며, Ollama의 named volume에 등록 결과가 보존됩니다.

PicToText는 OCR 기반 문서 처리 웹 애플리케이션입니다. React + FastAPI 구조이며, 영구 데이터는 Supabase PostgreSQL과 Storage에 저장합니다.

## 문서

- [개발자 가이드](developer-guide.md)
- [Supabase enum 준비](docs/01-supabase-enums.sql)
- [Supabase 최신 통합 스키마](docs/02-supabase-schema.sql)
- [Supabase 적용 결과 점검](docs/03-supabase-inspection.sql)
- [재무 파이프라인 v2.5 적용](docs/06-finance-pipeline-v2-5.sql)

## 1. 프로젝트 구성

```text
ocr-web-app/
├── .env.example
├── docker-compose.yml
├── docker-compose.override.yml
├── README.md
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   ├── venv/
│   └── app/
│       ├── api/
│       │   ├── router.py
│       │       ├── auth.py
│       │       ├── ocr.py
│       │       ├── chatbot.py
│       │       ├── reports.py
│       │       └── users.py
│       ├── core/
│       │   ├── config.py
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
│       ├── features/
│       ├── lib/
│       │   └── supabase.js
│       ├── pages/
│       │   ├── LoginPage.jsx
│       │   ├── DashboardPage.jsx
│       │   ├── OCRPage.jsx
│       │   ├── ReportPage.jsx
│       │   └── MyPage.jsx
│       ├── styles.css
├── ollama/
│   ├── Dockerfile
│   └── ...
├── docs/
│   ├── 01-supabase-enums.sql
│   ├── 02-supabase-schema.sql
│   └── 03-supabase-inspection.sql
└── README.md
```

## 2. 기술 스택

### Frontend
- React
- Vite
- React Router
- Axios
- Supabase JS SDK

### Backend
- FastAPI
- Supabase REST API
- Pydantic
- JWT (python-jose)
- Passlib + pbkdf2_sha256
- HTTPX

### Database
- 모든 환경: Supabase PostgreSQL + Storage

### OCR
- 사용 예정: PaddleOCR
- 현재 구조는 서버 API 레이어 준비 단계

## 3. 현재 구현된 기능

### 인증
- 회원가입
- 로그인
- JWT 토큰 발급
- 보호 라우트
- 로컬 저장 기반 로그인 유지
- Supabase OAuth 연동 준비

### 사용자 관리
- `users` 테이블 저장
- 이메일 기반 사용자 조회
- 이름/이메일 표시

### 화면 구조
- 로그인 페이지
- 대시보드
- OCR 페이지
- 리포트 페이지
- 마이페이지

## 4. 현재 동작 방식

### 로그인 구조
- 사용자가 이메일/비밀번호 입력
- Frontend가 `/api/v1/auth/login` 호출
- Backend에서 사용자 조회 및 비밀번호 검증
- JWT 발급
- 프론트에서 `pic_to_text_token` 저장
- `/dashboard` 로 이동

### 회원가입 구조
- Frontend가 `/api/v1/auth/signup` 호출
- Backend가 `users` 테이블에 사용자 저장
- 중복 이메일 체크
- 비밀번호 길이 검증

### 소셜 로그인 구조
- Frontend가 Supabase OAuth 시작
- Supabase 세션 수신
- `provider: "supabase"` 로 서버 `/auth/social-login` 호출
- 서버는 Supabase 토큰 검증 후 내부 JWT 발급

## 5. 환경 변수

프로젝트 루트의 `.env.example` 을 참고하세요.

### 핵심 값

```env
SECRET_KEY=change-this-secret-key
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your-anon-key
RAG_EMBEDDING_MODEL=embeddinggemma
RAG_EMBEDDING_DIMENSIONS=768
RAG_LLM_MODEL=gemma2:2b
RAG_RERANK_MODEL=
RAG_PROMPT_VERSION=baseline-v1
RAG_TOP_K=8
RAG_CHUNK_TARGET_CHARS=380
```

RAG 모델은 위 환경변수로 교체합니다. 임베딩 모델의 출력 차원이 바뀌면
`RAG_EMBEDDING_DIMENSIONS`와 Supabase `rag_chunks.embedding`의 `vector(N)`
차원을 함께 변경하고 기존 문서를 다시 인덱싱해야 합니다.

## 6. 로컬 실행 방법

### Backend
```powershell
cd "c:\Users\2Class_13\Desktop\main-ocr-project\ocr-web-app\backend"
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

### Frontend
```powershell
cd "c:\Users\2Class_13\Desktop\main-ocr-project\ocr-web-app\frontend"
npm install
npm run dev
```

### 브라우저 접속
```text
http://localhost:3000
```

## 7. API 구조

기본 prefix:
```text
/api/v1
```

주요 엔드포인트:
```text
POST /api/v1/auth/signup
POST /api/v1/auth/login
GET  /api/v1/auth/me
POST /api/v1/auth/social-login
```

## 8. DB 구조

현재 사용자 모델은 `users` 테이블 기준으로 동작합니다.

필드 예시:
- id
- name
- email
- password_hash
- provider
- provider_id
- is_active
- created_at
- updated_at

## 9. 보안 규칙

### 브라우저에 공개 가능
- `anon key`
- `VITE_SUPABASE_ANON_KEY`

### 서버에서만 보관
- `service_role key`
- `SECRET_KEY`

## 10. 지금 상태 요약

현재 프로젝트는 다음 단계까지 구현되어 있습니다.

- ✅ 프로젝트 구조 생성
- ✅ 로그인/회원가입 UI 구성
- ✅ DB 기반 사용자 저장
- ✅ JWT 발급 및 보호 라우트
- ✅ Supabase OAuth 준비 구조
- ✅ 로컬 개발용 실행 검증
- ✅ 대시보드 기반 화면 구성
- ⏳ PaddleOCR 실 OCR 엔진 연동
- ⏳ Supabase 실제 프로덕션 DB 최종 연결

## 11. 다음 작업 우선순위

1. PaddleOCR 서버 API 연결
2. OCR 파일 업로드 엔드포인트 구현
3. 대시보드 OCR 업로드 UI 완성
4. 추출 결과 저장 및 화면 표시
5. Supabase PostgreSQL 전환 검증

## 12. 팀 협업 팁

- 모든 개발 컴퓨터는 동일한 PostgreSQL 연결 설정 사용
- 실제 운영 환경은 Supabase/PostgreSQL로 전환
- 인증과 OCR 로직은 서버 쪽에서 일원화
- 프론트는 토큰과 UI 상태만 관리

---

필요하면 다음 단계로 이어서, 팀원용 발표용 5분 설명 스크립트 버전도 만들어드릴 수 있습니다.
