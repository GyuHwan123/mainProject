# DOCUNEX AI OCR 프로젝트: 비전공자를 위한 코드 지도

이 문서는 코딩을 처음 공부하는 사람도 현재 저장소가 무엇을 하는지 이해할 수 있도록 작성한 학습 안내서입니다. 소스 코드를 전부 외우는 것이 목적이 아닙니다. 화면에서 일어난 일이 어느 파일을 지나 어떤 서버와 데이터베이스로 전달되는지 큰 흐름을 잡는 것이 목적입니다.

> 기준 시점: 2026년 8월 12일 현재 코드
>
> 중요한 원칙: 이 프로젝트에는 로컬 DB, Supabase DB, Supabase Storage가 함께 있습니다. 세 가지를 같은 저장소라고 생각하면 디버깅이 어려워집니다.

---

## 1. 이 프로젝트를 한 문장으로 설명하면

사용자가 문서를 올리면 PDF 내부 글자를 직접 읽거나 PaddleOCR로 이미지 글자를 인식하고, 결과와 원본을 Supabase에 보관하며, Gemma2 모델로 구조화·표 변환·문서 질문을 처리하고, 개발자는 정답 데이터와 비교한 OCR 성능을 확인할 수 있는 웹 애플리케이션입니다.

## 2. 식당으로 비유한 전체 구조

처음에는 프론트엔드, 백엔드, API 같은 단어가 어렵습니다. 식당으로 비유하면 다음과 같습니다.

| 프로젝트 구성 | 식당 비유 | 실제 역할 |
|---|---|---|
| React 프론트엔드 | 손님이 보는 홀과 메뉴판 | 화면 표시, 버튼 입력, 미리보기 |
| FastAPI 백엔드 | 주문을 정리하는 직원 | 로그인 확인, 권한 검사, DB 저장 요청 |
| OCR 서버 | 글자를 판독하는 전문 요리사 | 이미지와 스캔 PDF에서 글자 인식 |
| Ollama + Gemma2 | 문서를 읽고 답하는 전문 상담원 | 채팅, 구조화, 표 변환 |
| Supabase DB | 주문 장부 | 사용자, 문서 정보, 평가 지표 저장 |
| Supabase Storage | 원본 보관 창고 | PDF와 이미지 원본 파일 저장 |
| PostgreSQL | 모든 컴퓨터가 함께 보는 사용자 장부 | 사용자와 로그인 권한 저장 |

화면이 DB에 직접 모든 작업을 시키지 않습니다. 일반적인 순서는 아래와 같습니다.

```text
사용자가 버튼 클릭
  → React가 입력 확인
  → FastAPI API 호출
  → FastAPI가 로그인과 권한 확인
  → OCR·Ollama·Supabase 중 필요한 서비스 호출
  → 결과를 JSON으로 React에 반환
  → React가 화면 갱신
```

## 3. 저장소의 큰 폴더 네 개

```text
ocr-web-app/
├─ frontend/   사용자가 보는 웹 화면
├─ backend/    인증, 권한, 저장, 서비스 연결 API
├─ ocr/        실제 문서·이미지 글자 인식 엔진
├─ ollama/     Gemma2 모델 실행 환경
└─ docs/       설정법과 학습 문서
```

### `frontend`: 화면 담당

React로 작성되었습니다. `.jsx`는 화면 구조와 동작, `.scss`와 `.css`는 색상·크기·배치를 담당합니다.

### `backend`: 업무 처리 담당

FastAPI로 작성되었습니다. 사용자가 누구인지 확인하고 OCR 서버와 Supabase, Ollama 사이를 연결합니다.

### `ocr`: 글자 판독 담당

PaddleOCR, PDF 처리, 이미지 전처리, 읽기 순서 보정이 들어 있습니다. 백엔드와 별도의 8001번 서버로 실행됩니다.

### `ollama`: LLM 담당

로컬에서 `gemma2:2b` 모델을 실행합니다. 기본 API 포트는 11434입니다.

## 4. 프로그램 실행 시 사용하는 주소

| 주소 | 프로그램 | 의미 |
|---|---|---|
| `http://localhost:3000` | React | 사용자가 보는 웹사이트 |
| `http://localhost:8000` | FastAPI 백엔드 | 로그인·문서·리포트 API |
| `http://localhost:8001` | OCR 서버 | PaddleOCR 처리 API |
| `http://localhost:11434` | Ollama | Gemma2 모델 API |
| Supabase 프로젝트 URL | Supabase | 클라우드 DB·Storage·OAuth |

포트는 한 건물 안의 호실과 같습니다. `localhost`라는 같은 컴퓨터 안에서도 8000호와 8001호는 서로 다른 프로그램입니다.

## 5. 프론트엔드를 읽는 순서

### 5.1 `frontend/src/main.jsx`

React 애플리케이션의 시작점입니다. 브라우저의 `#root` 영역에 앱을 붙입니다.

### 5.2 `frontend/src/App.jsx`

URL과 페이지를 연결하는 지도입니다.

| URL | 파일 | 화면 |
|---|---|---|
| `/login` | `LoginPage.jsx` | 로그인·회원가입 |
| `/dashboard` | `DashboardPage.jsx` | 기능 시작 화면 |
| `/ocr` | `OCRPage.jsx` | OCR 추출과 개발자 정답 입력 |
| `/chat` | `ChatPage.jsx` | Gemma 채팅과 RAG 문서 |
| `/reports` | `ReportPage.jsx` | 개발자 OCR 성능 리포트 |
| `/mypage` | `MyPage.jsx` | 사용자 정보 |

`ProtectedRoute`는 로그인 토큰이 있는지 확인합니다. `DeveloperRoute`는 추가로 개발자 역할을 확인합니다.

### 5.3 `frontend/src/api/client.js`

서버에 요청하는 공통 전화기입니다.

- 기본 백엔드 주소를 설정합니다.
- `localStorage`에서 JWT를 읽습니다.
- 모든 요청의 `Authorization: Bearer ...` 헤더에 토큰을 자동으로 넣습니다.

### 5.4 `frontend/src/features/appSession.js`

브라우저에 로그인 정보를 저장하고 지우는 파일입니다.

저장 항목:

- 앱 JWT
- 이메일
- 사용자 이름
- `USER`, `DEVELOPER`, `ADMIN` 역할

로그아웃할 때 이 정보가 삭제됩니다.

## 6. 백엔드를 읽는 순서

### 6.1 `backend/main.py`

FastAPI 서버의 시작점입니다.

- 서버 시작 시 DB 테이블 준비
- 브라우저 요청을 허용하는 CORS 설정
- `/api/v1` 아래에 기능 API 연결
- `/health` 상태 확인 API 제공

### 6.2 `backend/app/api/router.py`

기능별 API 파일을 하나로 모읍니다.

```text
/api/v1/auth      로그인과 회원가입
/api/v1/ocr       문서 업로드와 히스토리
/api/v1/chatbot   Gemma 채팅과 변환
/api/v1/reports   개발자 성능 평가
```

### 6.3 `routes`, `services`, `models`, `schemas`의 차이

| 폴더 | 쉬운 의미 | 예시 |
|---|---|---|
| `routes/` | API 입구 | POST 요청을 받음 |
| `services/` | 실제 외부 업무 담당 | Supabase 저장, Ollama 호출 |
| `models/` | DB 표의 Python 표현 | 사용자, OCR 평가 |
| `schemas/` | 요청·응답 JSON 규격 | 이메일, 페이지, bbox |
| `core/` | 공통 기반 | 설정, DB 연결, 암호, JWT |

## 7. 로그인과 권한 흐름

### 7.1 이메일 로그인

```text
LoginPage에서 이메일·비밀번호 입력
  → POST /api/v1/auth/login
  → auth.py가 로컬 DB 사용자 조회
  → 해시된 비밀번호 확인
  → JWT 발급
  → 프론트가 토큰·이름·이메일·역할 저장
  → 대시보드 이동
```

비밀번호 원문은 DB에 저장하지 않습니다. `pbkdf2_sha256` 해시로 변환합니다.

### 7.2 역할

| 역할 | 기능 |
|---|---|
| `USER` | OCR, 문서 히스토리, AI 채팅 |
| `DEVELOPER` | 일반 기능 + 정답 데이터 입력 + 성능 리포트 |
| `ADMIN` | 개발자 기능을 포함하도록 준비된 상위 역할 |

메뉴를 숨기는 것만으로는 보안이 아닙니다. 이 프로젝트는 다음 세 곳에서 확인합니다.

1. 프론트 메뉴 표시 여부
2. `/reports` 페이지 라우트 진입 여부
3. 백엔드 `/reports/evaluations` API 권한

## 8. OCR 처리 흐름

### 8.1 사용자가 보는 흐름

1. `파일 선택`을 누릅니다.
2. PDF 또는 이미지 미리보기만 준비됩니다.
3. 아직 OCR과 DB 저장은 시작되지 않습니다.
4. `OCR 텍스트 추출` 버튼을 누릅니다.
5. 파일 종류에 따라 텍스트 추출 방법이 결정됩니다.
6. 결과와 원본이 저장됩니다.
7. 오른쪽에 텍스트가 표시되고 히스토리에 문서가 생깁니다.

### 8.2 일반 PDF

PDF에 실제 문자 레이어가 있으면 브라우저의 PDF.js가 직접 읽습니다.

```text
PDF 선택
  → PDF.js getTextContent()
  → 글자의 x/y 위치와 내용을 읽음
  → buildReadingOrder()가 줄과 단 순서 정리
  → 화면 표시
  → /ocr/archive로 원본과 결과 저장
```

이 방법은 빠르고 원래 글자를 그대로 얻을 가능성이 높습니다.

### 8.3 스캔 PDF와 이미지

글자가 픽셀로만 존재하면 PaddleOCR가 필요합니다.

```text
파일 선택
  → POST /api/v1/ocr/upload
  → backend가 8001 OCR 서버 호출
  → 전처리
  → PaddleOCR 추론
  → bbox와 confidence 생성
  → 후처리와 읽기 순서 정리
  → Supabase 원본·결과 저장
```

### 8.4 관련 OCR 파일

| 파일 | 역할 |
|---|---|
| `ocr/app/services/file_classifier.py` | 확장자와 처리 방식 판별 |
| `ocr/app/services/preprocess_service.py` | 이미지 대비·노이즈 등 전처리 |
| `ocr/app/services/ocr/ocr_service.py` | PaddleOCR 실행 |
| `ocr/app/services/ocr/ocr_parser.py` | 모델 결과를 페이지·bbox 형식으로 변환 |
| `ocr/app/services/postprocess_service.py` | 줄바꿈, 단어 결합 등 후처리 |
| `ocr/app/services/pdf_service.py` | PDF 문자 추출, 스캔 판별, 다단 처리 |
| `ocr/app/services/docx_service.py` | DOCX 문자와 이미지 처리 |

## 9. bbox란 무엇인가

`bbox`는 bounding box의 줄임말로, 글자가 문서 어느 위치에 있었는지 나타내는 사각형 좌표입니다.

```json
{
  "text": "예시 문장",
  "confidence": 0.96,
  "bbox": [[120, 80], [310, 108]]
}
```

- 첫 좌표: 왼쪽 위
- 두 번째 좌표: 오른쪽 아래
- `confidence`: OCR 모델이 얼마나 확신하는지 나타내는 값

오른쪽 텍스트를 누르면 왼쪽 미리보기의 해당 사각형이 강조되는 기능에 사용됩니다.

## 10. 다단 문서 읽기 순서

영어 시험지나 신문처럼 왼쪽·오른쪽 단이 있는 문서는 단순히 y좌표만 정렬하면 같은 높이의 두 단이 한 줄로 섞입니다.

현재 로직의 기본 생각은 다음과 같습니다.

1. 페이지 중앙을 기준으로 왼쪽과 오른쪽 글자 그룹을 찾습니다.
2. 양쪽에 충분한 글자가 있으면 2단 문서로 판단합니다.
3. 페이지 전체를 가로지르는 제목은 `spanning`으로 처리합니다.
4. 왼쪽 단을 위에서 아래로 읽습니다.
5. 오른쪽 단을 위에서 아래로 읽습니다.

OCR은 문자를 찾는 문제와 읽기 순서를 결정하는 문제가 별개라는 점이 중요합니다.

## 11. 문서 저장 구조

### 11.1 Supabase Storage

원본 PDF와 이미지는 `documents` bucket에 저장됩니다.

```text
documents/{user_id}/{무작위 파일명}.pdf
```

bucket은 테이블이 아니라 파일 창고입니다.

### 11.2 Supabase `ocr_documents`

문서의 이름, 원본 파일 위치, 추출 텍스트, bbox, 처리 상태를 저장합니다.

### 11.3 문서 히스토리

```text
GET /api/v1/ocr/history
  → 현재 사용자 ID 확인
  → 사용자의 ocr_documents 조회
  → 왼쪽 히스토리 표시
```

히스토리를 누르면 DB 결과와 Storage 원본을 다시 받아 미리보기를 복원합니다.

## 12. Gemma2 기능

Ollama는 로컬 LLM 실행기이고 `gemma2:2b`는 그 위에서 실행되는 모델입니다.

### 12.1 OCR 구조화와 표 변환

OCR 오른쪽의 `구조화` 또는 `표` 탭을 누르면 현재 페이지 텍스트가 `/chatbot/transform`으로 전달됩니다.

- 구조화: 제목, 요약, 섹션 JSON
- 표: 열 이름과 행 배열 JSON
- 같은 결과는 페이지별로 프론트 메모리에 캐시
- 백엔드 연결 실패 시 로컬 Ollama 직접 호출을 보조 경로로 사용

### 12.2 AI 문서 채팅

현재 RAG 검색은 다음 방식입니다.

1. PDF/TXT/MD 내용을 작은 청크로 분할합니다.
2. 질문에 포함된 단어가 어느 청크에 많은지 계산합니다.
3. 상위 청크 네 개를 근거로 선택합니다.
4. Gemma2에 질문과 근거를 함께 보냅니다.
5. 답변과 근거를 화면에 표시합니다.

현재 검색은 키워드 방식입니다. Embedding 벡터 검색은 화면과 리포트에 준비 상태만 있으며 완전한 연결은 다음 개발 단계입니다.

## 13. Fine-tuning 화면의 정확한 의미

Fine-tuning 탭에서는 질문과 모범 답변을 작성해 JSONL 데이터셋을 만들 수 있습니다.

```json
{"instruction":"질문", "input":"", "output":"모범 답변"}
```

현재 앱 안에서 모델 가중치를 직접 학습시키지는 않습니다. 실제 LoRA 또는 QLoRA 학습에는 GPU, 학습 프레임워크, 작업 큐, 모델 산출물 저장소가 추가로 필요합니다.

## 14. 개발자 OCR 성능 평가

### 14.1 사용자 흐름

1. 개발자 계정으로 로그인합니다.
2. OCR 페이지에서 문서를 추출합니다.
3. `비교 텍스트 보기`를 누릅니다.
4. 사람이 검수한 Ground Truth를 직접 입력하거나 TXT·JSON으로 불러옵니다.
5. `정답 저장 및 성능 평가`를 누릅니다.
6. Supabase `ocr_evaluations`에 지표가 저장됩니다.
7. `성능 리포트`에서 결과를 확인합니다.

### 14.2 TXT와 JSON 정답 파일

TXT는 전체 내용을 정답으로 사용합니다. JSON은 다음 키를 순서대로 찾습니다.

1. `ground_truth`
2. `text`
3. `content`
4. `answer`

예시:

```json
{
  "ground_truth": "사람이 확인한 정확한 문서 전체 내용"
}
```

### 14.3 지표의 의미

| 지표 | 쉬운 설명 |
|---|---|
| TP | OCR과 정답에 모두 있는 토큰 |
| FP | OCR에는 있지만 정답에는 없는 토큰 |
| FN | 정답에는 있지만 OCR이 놓친 토큰 |
| Precision | OCR이 추출한 것 중 맞은 비율 |
| Recall | 실제 정답 중 OCR이 찾아낸 비율 |
| F1 | Precision과 Recall을 함께 본 점수 |
| CER | 문자 추가·삭제·교체가 얼마나 필요한지 나타내는 오류율 |

```text
Precision = TP / (TP + FP)
Recall = TP / (TP + FN)
F1 = 2 × Precision × Recall / (Precision + Recall)
```

### 14.4 평가 저장 컬럼

Supabase `ocr_evaluations`의 기존 ERD 컬럼을 그대로 사용합니다.

- `document_id`
- `confidence_score`
- `processing_time_ms`
- `cer_score`
- `precision_score`
- `recall_score`
- `evaluated_at`

F1은 Precision과 Recall에서 다시 계산할 수 있으므로 조회 시 계산합니다.

## 15. DB가 여러 개인 이유와 주의점

### Supabase PostgreSQL

- 이메일·소셜 로그인 사용자와 역할
- public 사용자 정보
- OCR 문서 메타데이터와 추출 결과
- OCR 평가 지표

### Supabase Storage

- 원본 문서 파일

문제가 생겼을 때 먼저 “어느 저장소에서 데이터를 찾고 있는가?”를 확인해야 합니다.

## 16. API 목록

### 인증

| 방식 | 주소 | 역할 |
|---|---|---|
| POST | `/api/v1/auth/signup` | 회원가입 |
| POST | `/api/v1/auth/login` | 로그인과 JWT 발급 |
| GET | `/api/v1/auth/me` | 현재 사용자와 역할 조회 |
| POST | `/api/v1/auth/social-login` | Supabase OAuth 토큰 교환 |

### OCR

| 방식 | 주소 | 역할 |
|---|---|---|
| POST | `/api/v1/ocr/upload` | OCR 서버 처리 후 저장 |
| POST | `/api/v1/ocr/archive` | PDF.js 결과와 원본 저장 |
| GET | `/api/v1/ocr/history` | 사용자 문서 목록 |
| GET | `/api/v1/ocr/documents/{id}` | 저장된 OCR 결과 |
| GET | `/api/v1/ocr/documents/{id}/file` | 원본 파일 스트리밍 |

### AI

| 방식 | 주소 | 역할 |
|---|---|---|
| POST | `/api/v1/chatbot/ask` | 문서 근거 질문 |
| POST | `/api/v1/chatbot/transform` | 구조화 또는 표 변환 |
| GET | `/api/v1/chatbot/status` | Gemma2 설치·연결 확인 |

### 개발자 리포트

| 방식 | 주소 | 역할 |
|---|---|---|
| POST | `/api/v1/reports/evaluations` | 성능 계산 및 Supabase 저장 |
| GET | `/api/v1/reports/evaluations` | 개발자 문서 평가 목록 |

## 17. 자주 만난 HTTP 오류 읽는 법

| 코드 | 의미 | 이 프로젝트의 흔한 원인 |
|---|---|---|
| 400 | 요청 내용 오류 | JSON 형식, 필수 값 누락 |
| 401 | 로그인 필요 | JWT 없음·만료·잘못된 비밀번호 |
| 403 | 권한 없음 | 일반 사용자가 개발자 API 호출 |
| 409 | 데이터 충돌 | 이미 존재하는 사용자·Storage 파일 |
| 502 | 중간 서버 연결/응답 오류 | Supabase 또는 OCR 서버 응답 오류 |
| 503 | 서비스 사용 불가 | Ollama, OCR 서버, Docker가 꺼짐 |

브라우저 콘솔의 상태 코드만 보지 말고 Network 탭의 Response에 있는 `detail` 메시지도 확인해야 합니다.

## 18. CSS와 화면 구조

| 파일 | 담당 화면 |
|---|---|
| `styles.css` | 공통 사이드바와 이전 공통 화면 |
| `style/_variables.scss` | 공통 색상 변수 |
| `style/LoginPage.scss` | 로그인 |
| `style/OCRPage.scss` | OCR 미리보기·텍스트·개발자 비교 |
| `style/ChatPage.scss` | AI 채팅·RAG·Fine-tuning |
| `style/ReportPage.scss` | 개발자 성능 리포트 |

SCSS의 `$main-color` 같은 값은 반복되는 색상을 한 곳에서 관리하기 위한 변수입니다.

## 19. 로컬 실행 순서

### 프론트엔드

```powershell
cd frontend
npm install
npm run dev
```

### 백엔드

```powershell
cd backend
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

### OCR 서버

```powershell
cd ocr
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

### Ollama 확인

```powershell
ollama list
ollama pull gemma2:2b
```

브라우저에서 `http://localhost:3000`으로 접속합니다.

## 20. 환경 변수

`.env`는 비밀번호와 연결 주소를 보관하는 파일입니다. Git에 올리면 안 됩니다.

중요한 값:

- `DATABASE_URL`: 로컬 또는 PostgreSQL DB
- `SECRET_KEY`: JWT 서명 비밀키
- `OCR_BASE_URL`: OCR 서버 주소
- `OLLAMA_BASE_URL`: Ollama 주소
- `SUPABASE_URL`: Supabase 프로젝트 주소
- `SUPABASE_ANON_KEY`: 브라우저 인증용 공개 키
- `SUPABASE_SERVICE_ROLE_KEY`: 서버 전용 강력한 키
- `VITE_API_BASE_URL`: 브라우저가 호출할 백엔드 주소

`SERVICE_ROLE_KEY`는 절대로 프론트 코드나 화면에 넣으면 안 됩니다.

## 21. 코드 공부 추천 순서

### 1단계: HTML·CSS·JavaScript 기초

- 변수와 함수
- 배열의 `map`, `filter`
- 조건문
- 이벤트
- 비동기 `async/await`
- HTML 폼과 버튼
- CSS flex와 grid

### 2단계: React

- 컴포넌트
- `useState`
- `useEffect`
- `useRef`
- props
- 조건부 렌더링
- React Router

### 3단계: HTTP와 API

- GET과 POST
- JSON
- 상태 코드
- Authorization header
- Axios
- CORS

### 4단계: Python과 FastAPI

- 함수와 클래스
- type hint
- Pydantic
- dependency injection
- route와 service 분리
- 예외와 HTTPException

### 5단계: DB

- 테이블, 행, 컬럼
- primary key와 foreign key
- SQL SELECT, INSERT, UPDATE
- SQLAlchemy ORM
- Supabase REST API
- Storage와 DB의 차이

### 6단계: OCR과 AI

- 이미지 픽셀과 전처리
- OCR confidence와 bbox
- PDF 문자 레이어
- 토큰과 프롬프트
- LLM
- 청크와 RAG
- Embedding과 벡터 검색
- Precision, Recall, F1, CER

## 22. 실습 과제

### 쉬움

1. 버튼 문구 하나 변경하기
2. SCSS 변수로 카드 색상 변경하기
3. OCR 결과 글자 수 표시 형식 바꾸기
4. 빈 상태 안내 문구 변경하기

### 보통

1. 리포트에서 CER 컬럼 표시하기
2. OCR 히스토리 검색 구현하기
3. 평가 기록 날짜 필터 추가하기
4. TXT 정답 템플릿 다운로드 추가하기

### 어려움

1. OCR API가 서버 측 처리 시간을 반환하게 하기
2. Embedding 모델을 연결해 벡터 검색 구현하기
3. 채팅 문서와 대화를 DB에 영구 저장하기
4. 평가 결과의 페이지별 점수를 저장하기
5. 실제 GPU Fine-tuning 작업 큐 구현하기

## 23. 디버깅 체크리스트

1. 어떤 버튼을 눌렀는가?
2. 브라우저 Console에 JavaScript 오류가 있는가?
3. Network 탭에서 어떤 API가 호출됐는가?
4. 상태 코드는 무엇인가?
5. Response의 `detail`은 무엇인가?
6. 8000 백엔드가 실행 중인가?
7. 8001 OCR 서버가 실행 중인가?
8. 11434 Ollama가 실행 중인가?
9. 로그인 토큰과 역할이 저장되어 있는가?
10. 데이터는 PostgreSQL 테이블과 Storage 중 어디에 있어야 하는가?
11. 서버 코드 변경 후 백엔드를 재시작했는가?
12. 프론트 변경 후 `Ctrl+F5`로 새로고침했는가?

## 24. 현재 남아 있는 중요한 개선점

- Embedding과 벡터 DB를 연결한 완전한 RAG
- 서버 기준의 정확한 OCR 처리 시간 저장
- 평가 Ground Truth 원문을 보관할 별도 정책과 컬럼 검토
- 페이지별·언어별·파일 유형별 성능 통계
- 사용자 역할을 Supabase와 로컬 DB에서 단일화
- JWT 만료 시 자동 로그아웃 처리
- 프론트 번들 코드 분할
- 자동 테스트와 CI
- 오래된 초기 골격 파일과 문서 정리

## 25. 마지막으로 기억할 핵심

코드를 공부할 때 한 파일을 처음부터 끝까지 이해하려고 하지 않아도 됩니다. 다음 질문을 반복하면 됩니다.

```text
이 화면은 어느 컴포넌트인가?
이 버튼은 어느 함수를 부르는가?
그 함수는 어느 API를 호출하는가?
백엔드의 어느 route가 받는가?
어느 service가 실제 작업을 하는가?
결과는 어느 DB나 Storage에 저장되는가?
응답이 돌아오면 어느 state가 바뀌는가?
```

이 일곱 질문을 따라가면 큰 프로젝트도 작은 연결들의 모음으로 보이기 시작합니다.
