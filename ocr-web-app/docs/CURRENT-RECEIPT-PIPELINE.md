# 현재 영수증 처리 파이프라인

이 문서는 현재 코드 기준으로 영수증 업로드부터 OCR, 품목 추출, 재무 분류, 사용자 검토, 저장 및 Excel 생성까지의 흐름을 설명한다.

- 기준일: 2026-09-01
- 대상: 단일 영수증 자동 문서화와 단일·일괄 평가
- 현재 prompt version: `receipt-v14-grounded-category-measured-items`
- 원칙: LLM 출력을 그대로 확정하지 않고 OCR 근거, 결정 규칙, validator와 사용자 검토를 함께 사용한다.

## 1. 전체 흐름

```text
영수증 파일 업로드
  ↓
OCR 서비스 호출 (processing_mode=receipt)
  ↓
OCR 문서 저장
  - extracted_text
  - pages / bounding_boxes
  - 원본 파일 정보
  ↓
규칙 기반 deterministic hints 생성
  - 거래일
  - 공급가액 / 부가세 / 합계 / 할인
  - 결제수단
  - 명시된 품목 수량
  - 파일명 기반 문서 유형·카테고리 힌트
  - 명시적 상호·가맹점 라벨
  - 강한 승차권·택시 문맥 기반 교통 힌트
  ↓
LLM 1차 호출: 영수증 요약·재무 분류
  - document_type
  - expense_category
  - merchant
  - 금액·결제 정보
  ↓
OCR 구조에서 품목 candidate 생성 및 필터링
  - candidates
  - rejected_candidates
  - structured_evidence
  - item_structure
  ↓
grounded fast path 가능 여부 판단
  ├─ 가능: candidate로 품목 확정, 품목 LLM 호출 생략
  └─ 불가능: LLM 2차 호출로 품목 추출
                 ↓ 실패 시 compact retry
  ↓
candidate와 model item 조정·복구
  ↓
정규화 및 validator
  - OCR 근거 없는 품목·메타데이터 제거
  - 날짜·금액·카드번호 grounding
  - 분류 contract 검증
  ↓
중복 영수증 검사
  ↓
finance record 저장 (status=REVIEW)
  ↓
사용자 검토·수정·확정
  ↓
재무팀 전달
  ↓
Excel workbook 생성
```

## 2. 업로드와 OCR

프런트엔드는 영수증을 다음 endpoint로 업로드한다.

```http
POST /ocr/upload?processing_mode=receipt
```

백엔드는 별도 OCR 서비스에 파일을 전달하고 결과를 검증한 뒤 OCR 문서로 저장한다. 이후 프런트엔드는 저장된 `document_id`를 사용해 재무 분류를 요청한다.

```http
POST /finance/records/classify

{
  "document_id": "..."
}
```

분류 입력에는 저장된 OCR 문서의 다음 값이 사용된다.

- `extracted_text`: 전체 OCR 텍스트
- `bounding_boxes`: 행·영역·표 구조를 포함한 페이지 evidence
- `file_name`: 일부 deterministic hint에 사용하는 파일명

OCR 텍스트가 없거나 영수증 LLM 모델이 설정되지 않았으면 분류 요청은 실패한다.

## 3. deterministic hints

`_receipt_hints()`는 LLM 호출 전후에 사용할 규칙 기반 근거를 만든다.

주요 추출 대상은 다음과 같다.

- 라벨 근처의 거래·결제·승인 날짜
- 공급가액, 부가세, 최종 결제액
- 할인 전 금액, 할인액, 결제액의 산술 관계
- 카드·현금 결제 근거
- 영수증에 명시된 품목 수와 총수량
- 파일명에 포함된 출장·교통·복지·구매 관련 힌트
- `승차권`과 출발지·도착지·총매수·예매번호가 결합된 교통 근거
- `택시`와 차량번호·탑승시간·미터요금 등이 결합된 교통 근거

이 값은 최종 답을 무조건 결정하는 별도 classifier라기보다 LLM 결과를 검증하고 명백한 OCR 근거를 우선하는 데 사용한다. 다만 라벨이 명확한 날짜와 최종 결제액 등 일부 evidence는 충돌하는 LLM 값보다 우선한다.

## 4. LLM 처리

### 4.1 요약·분류 호출

첫 번째 LLM 호출은 전체 영수증 수준의 필드를 생성한다.

- 문서 유형
- 비용 카테고리
- 상호
- 거래일
- 공급가액·부가세·합계금액
- 결제수단과 카드번호
- 설명 등
- 카테고리 근거 OCR line ID 최대 3개

이 호출에서 반환된 `items`는 사용하지 않는다. 품목은 OCR 구조와 candidate를 사용하는 별도 단계에서 처리한다.

### 4.2 품목 candidate 생성

OCR 페이지 구조에서 품목으로 볼 수 있는 행과 블록을 생성한 뒤 신뢰 가능한 후보와 거절 후보를 분리한다.

진단 정보에는 다음 값이 보존된다.

```json
{
  "item_extraction_diagnostics": {
    "structure": {},
    "structured_evidence": {},
    "candidates": [],
    "rejected_candidates": [],
    "model_items": [],
    "resolved_items": [],
    "fallback_used": null
  }
}
```

현재 `structure`는 candidate 해석을 보조하는 내부 품목 구조 프로필이며, 영수증 업종을 고정 분류하는 별도 taxonomy는 아니다.

### 4.3 grounded fast path

OCR candidate만으로 품목이 충분히 확정되고 합계·개수 등의 검증 조건을 만족하면 두 번째 LLM 호출을 생략한다.

이 경우:

- candidate에서 품목을 확정한다.
- `model_items`는 빈 배열이다.
- `items_call_status`는 `skipped_grounded_fast_path`가 된다.
- 생략 이유가 trace에 저장된다.

### 4.4 품목 LLM 호출과 retry

fast path를 사용할 수 없으면 품목 전용 prompt로 두 번째 LLM 호출을 수행한다.

1. 전체 품목 prompt인 `full` 시도
2. candidate가 있고 JSON 파싱·형식 검증에 실패하면 candidate 중심의 `compact_retry` 시도

신뢰 가능한 candidate가 0개이면 대형 `evidence_bundles` 대신 OCR 앞부분과 최대 12개 표 행만 포함하는 `recovery_full` prompt를 사용한다. 이 경로가 실패해도 사실이 없는 빈 compact retry는 실행하지 않는다. `full` 호출이 timeout된 경우에도 이미 안전 처리 예산을 소비했으므로 compact retry를 실행하지 않는다.

성공한 model item은 중복을 제거한 뒤 OCR candidate와 reconcile한다. model item이 candidate보다 부족하거나 단일 서비스 추론과 충돌하면, OCR 근거가 충분한 경우 candidate 기반 복구를 시도한다.

주유처럼 소수 측정량이 있는 품목은 유종명·`L/ℓ` 수량·단가·합계의 물리적 출력 순서를 고정하지 않는다. 같은 근거 구역에서 값을 수집한 뒤 `수량 × 단가 ≈ 합계`가 반올림 허용 범위 안에서 성립할 때 `fuel_sale_item` 고신뢰도 후보로 만든다. 전화번호·협회·신고 안내·고객센터처럼 식별자 숫자가 포함된 행은 산술 관계가 성립하지 않으면 품목 후보에서 차단한다.

두 호출이 모두 실패해도 전체 영수증 처리를 즉시 중단하지 않는다. grounded candidate로 복구할 수 있는 품목을 저장하고 실패 유형과 fallback 이유를 진단 정보에 남긴다.

## 5. 정규화와 검증

`_normalize()`는 LLM 및 candidate 결과를 실제 finance record 형식으로 바꾼다.

주요 처리 내용은 다음과 같다.

- 합계, 할인, 승인번호 등 비품목 행 제거
- 할인·쿠폰·캐시백 행을 독립 품목으로 저장하지 않도록 제거
- 품목명의 옵션·메타데이터 분리
- 수량·단가·공급가액·세액·품목금액 정규화
- 카드 매출표에 OCR 품목 candidate가 없으면 모델이 만든 품목 제거
- OCR에 존재하는 마스킹 카드번호만 허용
- 결제수단의 정책·안내 문구 오인 방지
- `카드 거래 영수증` 또는 카드번호와 승인정보 조합을 카드 결제 근거로 사용
- `상호:`, `가맹점명:`, `업체명:`, `매장명:` 같은 명시적 OCR 라벨에서 상호 복구
- 강한 승차권·택시 증거가 있으면 LLM의 누락·오분류보다 `교통` 근거를 우선
- 유종명·리터 수량·단가·결제 합계가 함께 검증되는 주유 거래는 `교통` 근거를 우선
- 카테고리 근거 line ID가 실제 OCR line인지 검증하고 유효한 ID만 보존
- 명확한 OCR 날짜와 금액 evidence 우선
- `YYYYMMDD` 형태의 유효한 날짜를 일반 금액·산술 후보에서 제외
- 품목 수량 합산 및 영수증 표기 수량 보존

정규화 결과는 기본적으로 다음 상태로 저장된다.

```json
{
  "status": "REVIEW",
  "document_type": "...",
  "expense_category": "...",
  "structured_data": {
    "items": [],
    "semantic_evidence": {},
    "item_extraction_diagnostics": {},
    "llm_trace": {},
    "validator_trace": {}
  }
}
```

## 6. 재무 문서 유형과 카테고리

### 6.1 문서 유형

허용되는 문서 유형은 네 가지다.

| 코드 | Excel 양식 |
|---|---|
| `EXPENSE_REPORT` | 경비지출결의서 |
| `PURCHASE_REQUEST` | 구매품의요청서 |
| `TRAVEL_EXPENSE` | 출장여비교통비정산서 |
| `WELFARE_BENEFIT` | 복리후생비신청서 |

### 6.2 현재 카테고리 매핑

| 카테고리 | 기본 문서 유형 |
|---|---|
| 취미/쇼핑 | `PURCHASE_REQUEST` |
| 전자제품/문구 | `PURCHASE_REQUEST` |
| 교통 | `TRAVEL_EXPENSE` |
| 미용 | `WELFARE_BENEFIT` |
| 도서 | `WELFARE_BENEFIT` |
| 식비 | `WELFARE_BENEFIT` |
| 레저 | `WELFARE_BENEFIT` |
| 의료 | `WELFARE_BENEFIT` |
| 문화 | `WELFARE_BENEFIT` |

과거 중복 카테고리는 입력 alias로만 유지한다.

| 과거 값 | 현재 canonical 값 |
|---|---|
| `전자제품` | `전자제품/문구` |
| `미용/생활` | `미용` |
| `주유/교통` | `교통` |
| `식비/주류` | `식비` |

### 6.3 분류 검증 규칙

분류 contract는 다음 순서로 적용된다.

1. 카테고리는 canonical label 또는 안전한 legacy alias로 정규화한다.
2. 일반적으로 LLM의 카테고리를 주 분류로 사용하고 deterministic 카테고리는 fallback으로 사용한다.
3. 단, 승차권·노선·매수 또는 택시·운행정보처럼 복수의 강한 OCR 교통 근거가 결합되면 deterministic `교통` 분류를 우선한다.
4. 유효한 카테고리에서 기본 `document_type`을 계산한다.
5. LLM의 `document_type`과 deterministic 문서 힌트를 독립 신호로 비교한다.
6. 신호가 일치하면 채택하고, 충돌하면 `needs_review=true`와 `classification_decision` trace를 남긴다.
7. 명시적인 출장·여비 등 강한 파일명 업무 문맥은 문서 유형을 선택할 수 있지만 카테고리 충돌은 검토 대상으로 유지한다.
8. 카테고리가 유효하지 않으면 자동 확정하지 않고 검토 대상으로 둔다.
9. 사용자가 검토 화면에서 저장한 문서 유형·카테고리 조합은 명시적 업무 결정으로 허용한다.

따라서 다음 입력은 `TRAVEL_EXPENSE`로 유도된다.

```json
{
  "document_type": null,
  "expense_category": "교통"
}
```

판단 근거는 `classification_decision`에 카테고리 기본값, 모델 의견, deterministic 힌트, 최종 선택 및 충돌 사유로 보존된다.

## 7. 중복 검사와 저장

분류 전후에 기존 finance record와 비교한다.

- OCR 전체 텍스트 fingerprint
- 거래를 식별할 수 있는 identity key
- 기존 데이터와 호환하기 위한 legacy key

중복으로 판단되어도 새 모델 결과의 저장 자체를 막지는 않는다. 새 record에 이전 record ID와 중복 진단 정보를 기록한다.

저장되는 주요 추적 정보는 다음과 같다.

- `prompt_version`
- `model_name`
- `processed_at`
- `receipt_fingerprint`
- `receipt_identity_key`
- `duplicate_detection`
- `llm_trace`
- `validator_trace`

## 8. 사용자 검토 UI

사용자는 `REVIEW` 상태의 결과에서 문서 유형, 카테고리, 상호, 날짜, 금액 및 결제수단을 수정할 수 있다.

검토 UI는 `/finance/taxonomy`의 다음 정보를 사용한다.

```json
{
  "document_types": [],
  "expense_categories": [],
  "category_to_document_type": {}
}
```

카테고리 선택 목록은 모든 canonical 카테고리를 표시한다. 현재 문서 유형의 기본 매핑과 다른 카테고리에는 별도 검토 안내가 표시되므로, 출장 중 식비처럼 category와 document type이 의도적으로 다른 업무 조합도 사용자가 확정할 수 있다.

문서 유형을 바꾸면 기존 카테고리는 비워지며, 새 문서 유형에 맞는 카테고리를 다시 선택해야 한다. 두 값은 저장과 최종 확정 시 모두 필수다.

사용자 흐름은 다음과 같다.

```text
AI 분석 결과 저장 (REVIEW)
  ↓
사용자 수정 저장
  ↓
사용자 최종 확정 (CONFIRMED)
  ↓
재무팀 전달 (submitted_at 기록)
  ↓
재무팀 확인 (finance_confirmed_at 기록)
```

## 9. Excel 생성

Excel 생성기는 카테고리가 아니라 최종 `document_type`을 기준으로 데이터를 배치한다.

하나의 workbook에는 항상 다음 시트가 생성된다.

1. 경비지출결의서
2. 출장여비교통비정산서
3. 구매품의요청서
4. 복리후생비신청서
5. 영수증요약

각 record는 자신의 `document_type`과 일치하는 시트에 들어간다. 첫 번째 record의 문서 유형에 해당하는 시트가 Excel을 열었을 때 활성 시트가 된다.

문서 유형에 따라 품목을 행으로 변환하는 방법도 달라진다.

- `EXPENSE_REPORT`: 결제일시, 상호, 지출용도, 공급가액, 부가세, 합계 중심
- `TRAVEL_EXPENSE`: 구분, 일자, 출발·도착지, 교통·숙박 수단, 금액 중심
- `PURCHASE_REQUEST`: 품목명, 옵션, 수량, 단위, 단가, 공급가액, 부가세 중심
- `WELFARE_BENEFIT`: 지원 항목, 결제일자, 내용, 결제처, 신청 금액 중심

단건, 선택한 복수 record, 전체 확정 record를 각각 export할 수 있다.

## 10. 장애 및 fallback

| 상황 | 현재 동작 |
|---|---|
| OCR 서비스 장애 | 업로드 실패 응답 |
| OCR 텍스트 없음 | 분류 요청 거부 |
| 영수증 LLM 미설정 | 분류 요청 거부 |
| 요약 LLM 호출 실패 | deterministic hints로 record 생성 시도 |
| 품목 LLM 호출 실패 | grounded candidate 복구 시도 |
| 카테고리 불명확 | `needs_review=true` 및 사용자 검토 |
| 문서 유형 누락 + 유효 카테고리 | 카테고리에서 문서 유형 유도 |
| 중복 영수증 | 새 분석은 저장하고 이전 record를 참조 |

## 11. 주요 코드 위치

| 역할 | 파일 |
|---|---|
| OCR 업로드·저장 | `backend/app/api/routes/ocr.py` |
| 재무 API와 저장 흐름 | `backend/app/api/routes/finance.py` |
| LLM 요약·품목 처리 orchestration | `backend/app/services/finance_receipt_pipeline.py` |
| candidate·품목 reconcile·fallback | `backend/app/services/finance_receipt_items.py` |
| deterministic hints·정규화·validator | `backend/app/services/finance_receipt_evidence.py` |
| 카테고리와 문서 유형 taxonomy | `backend/app/constants/finance_taxonomy.py` |
| Excel workbook 생성 | `backend/app/services/finance_workbook_service.py` |
| 사용자 검토·다운로드 UI | `frontend/src/pages/OCRPage.jsx` |

## 12. 현재 구조의 주의점

- 카테고리 하나는 기본적으로 문서 유형 하나에만 매핑된다.
- 승차·운송과 주유·차량 유지 분류는 모두 `교통`으로 통합되며 기본 문서 유형은 `TRAVEL_EXPENSE`다.
- 일반 교통비와 출장 교통비를 카테고리만으로 구분하지는 않는다.
- 모델의 `document_type`은 보조 신호로 비교하고, 사용자가 검토 화면에서 직접 지정한 조합만 명시적 업무 결정으로 우선한다.
- workbook은 양식 파일 하나를 선택하는 방식이 아니라 네 재무 시트를 모두 만들고 record를 해당 시트에 배치하는 방식이다.
- `item_extraction_diagnostics`와 trace는 보존되지만 candidate 단계별 정답 precision/recall 평가는 별도의 평가 데이터와 평가기가 필요하다.

## 13. 호출 제한 시간과 LLM 실행 특성

현재 시간 제한은 다품목 영수증과 장시간 일괄 평가에서 Ollama 생성이 일시적으로 느려지는 경우까지 수용하도록 설정되어 있다. 내부 단계별 제한과 전체 HTTP 요청 제한은 서로 다른 값이며, 전체 제한은 요약·품목·재시도를 모두 포함할 수 있도록 더 길게 잡는다.

| 구간 | 제한 시간 | 비고 |
|---|---:|---|
| 프런트엔드 `/finance/records/classify` 요청 | 1,560초(26분) | 브라우저가 전체 분류 응답을 기다리는 시간 |
| 서버 전체 분류 예산 | 1,560초(26분) | lock 대기와 모든 LLM 단계를 포함 |
| 요약·분류 Ollama 호출 | 600초(10분) | 거래 요약, 카테고리, 상호, 결제 정보 |
| 품목 `full` 또는 `recovery_full` 호출 | 600초(10분) | candidate와 OCR 구조를 이용한 품목 추출 |
| 품목 `compact_retry` | 300초(5분) | full 호출의 JSON 파싱·형식 검증 실패 시 축약 재시도 |

영수증 분류는 프로세스 내 단일 lock으로 직렬화하며 lock 대기시간도 1,560초 예산에 포함한다. 요약 600초, 품목 600초, compact retry 300초의 이론상 최대 합계 1,500초에 60초의 애플리케이션 여유 시간을 더한 값이다.

timeout 처리 원칙은 다음과 같다.

- 요약 호출이 실패하면 `_classify_receipt()`가 deterministic hint 기반 `rules-fallback` record 생성을 시도한다.
- 품목 호출이 timeout되면 같은 요청에서 compact retry를 시작하지 않고 grounded candidate 복구로 이동한다.
- `full` 호출이 timeout이 아닌 JSON 파싱·형식 오류로 실패했고 candidate가 있으면 compact retry를 실행한다.
- 전체 1,560초를 넘으면 API는 `504 Gateway Timeout`을 반환하며, 해당 파일은 평가 record 저장 단계에 도달하지 못한다.
- 일괄 평가에서는 실패 파일의 메시지를 수집하고 다음 파일 처리를 계속한 뒤 batch를 finalize한다.

Ollama 요청은 `keep_alive=30m`을 사용한다. 첫 호출에는 모델 적재 시간이 포함될 수 있으며, 이후 호출은 모델이 메모리에 유지되어 더 빨라질 수 있다.

각 요약·품목 호출은 Ollama가 반환하는 실제 실행 지표도 `llm_trace`에 저장한다.

- 입력 토큰 수: `prompt_eval_count`
- 출력 토큰 수: `eval_count`
- 전체·모델 적재·입력 평가·출력 생성 시간: 각각 밀리초 단위의 `*_duration_ms`
- 종료 사유: `done_reason`
- 요약 호출: `summary_ollama`
- 품목 호출: `items_ollama`, 재시도별 세부 값은 `items_attempts[].ollama`

기존의 `*_latency_ms`는 애플리케이션에서 관측한 전체 HTTP 대기 시간이고, Ollama 지표는 모델 내부 처리 시간이다. 두 값을 함께 비교하면 모델 적재, 프롬프트 처리, 출력 생성, 네트워크·대기 오버헤드 중 실제 병목을 구분할 수 있다.

품목 LLM 출력에는 자유 서술형 `note`를 요청하지 않는다. 후보 복원·단가 복원처럼 코드가 확정한 근거 note만 후처리에서 추가한다. 또한 품목 프롬프트는 후보 `raw_cells`와 완전히 같은 OCR 줄을 다시 전송하지 않고, 비어 있는 선택 필드를 제거하여 입력 토큰 중복을 줄인다.

단일·일괄 평가 화면의 `응답시간`은 전체 OCR·저장·Excel 처리 시간이 아니라 주로 `/finance/records/classify` 호출 경과시간이다. 일괄 평가는 현재 병렬이 아니라 파일별 순차 처리다.

## 14. 평가 파이프라인

자동 문서화 결과를 정답 JSON과 비교하는 흐름은 다음과 같다.

```text
정답 JSON + 영수증 이미지
  ↓
OCR 업로드
  ↓
실제 자동 문서화 실행 (`/finance/records/classify`)
  ↓
생성된 finance record 저장
  ↓
저장된 record 평가 (`/finance-evaluations/record`)
  ↓
필드 점수·오류 태그·trace·workbook 검증 저장
  ↓
단일 평가 이력 또는 일괄 batch 집계
```

평가 대상에는 카테고리, 상호, 거래일, 합계, 결제수단, 수량과 품목 필드가 포함된다. `card`와 `카드`처럼 의미가 같은 표현은 평가 정규화 후 비교한다.

일괄 평가는 다음 순서로 동작한다.

1. `finance_evaluation_batches`에 `BULK` batch를 만든다.
2. 각 파일을 순서대로 OCR·문서화·평가한다.
3. 각 입력은 `finance_evaluation_items`에 저장한다.
4. 결과는 `finance_record_evaluations`에 저장한다.
5. 마지막에 batch를 finalize하고 평균 정확도, 지연시간, 오류 필드 수 등을 집계한다.

실패하거나 등록되지 않은 파일은 성공 결과 평균에서 제외될 수 있으므로 `requested_count`, `registered_count`, `successful_count`, `failed_count`를 함께 확인해야 한다.

## 15. 평가 화면의 OCR-LLM 판단 과정

평가 페이지의 2번 영역은 전체 OCR 표를 기본으로 보여주지 않고 필드별 실패 원인을 먼저 보여준다.

### 오류 분석

- 확인 필요 필드 수
- 자동 보정된 필드 수
- 정상 필드 수
- 오류 발생 단계
- 실제값과 정답
- 저장된 오류 태그와 설명

### 필드 흐름

```text
관련 OCR 근거
  → LLM 요약 출력
  → validator·정규화 결과
  → 최종 finance record 값
```

### OCR 근거

`semantic_evidence.lines`와 section 정보를 사용해 해당 필드와 관련된 OCR 행만 표시한다. 상호는 발행처·사업자 영역, 결제수단은 payment·settlement 영역, 카테고리는 품목·서비스 문맥을 우선한다.

### 원본 표

`원본 표` 탭을 선택하면 추가 하위 탭 없이 OCR 위치를 재구성한 표만 표시한다. 원본 이미지의 OCR 박스는 1번 영역에서 별도로 확인한다.

## 16. 영수증 기록 보관함

문서화된 영수증의 보관 메타데이터는 `receipt_archive`에 저장한다.

주요 연결은 다음과 같다.

```text
receipt_archive
  ├─ user_id → users
  ├─ document_id → ocr_documents
  └─ finance_record_id → finance_records
```

보관함에는 원본 파일명, Storage 경로, 최신 카테고리, 상호, 거래일과 합계금액이 저장된다. 원본 이미지는 Supabase Storage에 있으며, 보관함을 새로고침해도 DB에서 다시 조회한다. 보관함 항목을 선택하면 1번 영수증 원본 영역에서 이미지만 미리볼 수 있고, 사용자가 원하면 해당 이미지로 OCR·문서화를 다시 실행할 수 있다.

동일 `finance_record_id`는 upsert되므로 카테고리 같은 보관 메타데이터는 최신 분석값으로 갱신된다. 일괄 평가 이력은 영수증 보관함과 분리되어 평가 batch 테이블에 저장된다.

## 17. 변경 시 확인해야 할 테스트

```powershell
cd backend
python -m unittest tests.test_finance_classification
python -m unittest tests.test_finance_taxonomy tests.test_finance_evaluation_service tests.test_finance_error_analysis_service

cd ../frontend
npm.cmd run build
```

분류 규칙을 추가할 때는 성공 사례뿐 아니라 다음 부정 사례도 함께 추가한다.

- 안내·환불 문구의 `카드`를 실제 카드 결제로 오인하지 않는지
- 상품명에 포함된 `승차권` 한 단어만으로 교통 분류하지 않는지
- 명시적 상호 라벨이 없는 영수증의 기존 LLM 상호를 임의로 덮어쓰지 않는지
- 특정 업체명에 종속된 규칙이 아니라 라벨과 증거 조합을 사용하는지

## 18. 현재 Ollama 호출 설정

영수증 파이프라인은 별도의 system prompt 없이 Ollama `/api/generate`에 아래 옵션과 사용자 prompt를 직접 전달한다.

```json
{
  "stream": false,
  "keep_alive": "30m",
  "format": "json",
  "options": {
    "temperature": 0.05,
    "num_ctx": 8192,
    "repeat_penalty": 1.08
  }
}
```

호출별 `num_predict`와 timeout은 다음과 같다.

| 호출 | `num_predict` | timeout |
|---|---:|---:|
| 요약·분류 | 500 | 600초 |
| 품목 full | `min(max(400, 220 + candidate 수 × 75), 750)` | 600초 |
| 품목 compact retry | `min(max(240, 120 + candidate 수 × 55), 600)` | 300초 |

품목 full 호출은 JSON 파싱 실패나 `items` 배열 누락으로 실패하면 compact retry로 넘어간다. 요약 호출 자체가 실패하면 `_classify_receipt()`가 deterministic hints 기반 fallback record를 만든다.

예외적으로 timeout은 compact retry로 넘어가지 않는다. candidate가 0개인 `recovery_full` 실패도 빈 candidate retry를 만들지 않고 deterministic recovery로 종료한다.

## 19. 요약·분류 프롬프트 전문

코드 위치는 `backend/app/services/finance_receipt_items.py`의 `_receipt_prompt()`다. 아래 `{...}`는 실행 시 실제 JSON 또는 문자열로 치환된다.

```text
OCR 영수증의 요약 정보만 JSON 객체 하나로 반환하세요. items는 추출하지 마세요.
OCR에 없는 값은 추측하지 말고 null로 작성하세요.

doc_type: EXPENSE_REPORT(일반 경비), TRAVEL_EXPENSE(출장·교통·숙박),
PURCHASE_REQUEST(물품·장비·소프트웨어), WELFARE_BENEFIT(도서·교육·의료·복리후생) 중 하나.

반환 키: image, doc_type, expense_category, category_evidence_line_ids, needs_review, merchant,
transaction_date, supply_amount, tax_amount, discount_amount, total_amount, payment_method, card_number, description.

expense_category는 아래 고정 목록 중 하나를 선택하되 실제 결제 대상과 가장 잘 부합하는 값을 우선하세요.
["취미/쇼핑", "미용", "도서", "전자제품/문구", "교통", "식비", "레저", "의료", "문화"]

카테고리 정책:
- 취미/쇼핑: 취미·의류·선물·일반 쇼핑 거래. 다른 전용 카테고리의 대상이 명확하면 제외
- 미용: 외모·모발·피부·손발 관리 거래. 관련 상품만 보이면 상호 업종만으로 확정하지 않음
- 도서: 책·서적·출판물 구매 거래
- 전자제품/문구: 전자기기·컴퓨터 주변기기·사무용품·문구 구매 거래
- 교통: 승객 운송·승차 또는 차량 연료·유지 거래
- 식비: 식사·식품·간식·음료·주류 구매 거래
- 레저: 스포츠·여가 활동·숙박 이용 거래
- 의료: 진료·검사·치료·의약품 등 의료 목적 거래
- 문화: 공연·영화·전시 등 문화 콘텐츠 이용 거래

판단 규칙:
1. OCR 근거만 사용합니다. 날짜는 YYYY-MM-DD, 금액은 숫자이며 확인할 수 없는 개별 값은 null입니다.
2. 카테고리는 실제 결제 품목·서비스를 우선하고, 거래를 직접 설명하는 문구, 판매·서비스 주체 순서로 판단합니다. 이 중 하나가 정책에 합리적으로 부합하면 해당 카테고리를 선택하고, 근거가 완벽하지 않다는 이유만으로 null을 선택하지 마세요. null은 거래 대상 정보가 거의 없거나 둘 이상의 정책이 비슷하게 충돌하거나 어느 정책에도 합리적으로 부합하지 않을 때만 사용합니다.
3. expense_category를 먼저 판단한 뒤 사용한 OCR line id가 있으면 category_evidence_line_ids에 최대 3개 반환하세요. line id를 자신 있게 고르지 못해도 유효한 expense_category를 null로 바꾸지 말고 빈 배열을 허용합니다. doc_type은 보조 의견이며 카테고리와 문서 목적이 충돌하거나 업무 목적이 불명확하면 needs_review=true로 작성하세요.
4. merchant는 issuer/business_info에서 실제 판매·발행 주체를 고릅니다. 카드사·PG사·쇼핑몰·URL은 판매자가 아니면 제외하고 `(과세)/(면세)`는 제거합니다.
5. total_amount는 settlement의 최종 결제·받을·승인·청구 금액을 우선하고 tax_summary, adjustments, item_summary는 검산에만 씁니다. 품목 단가·소계는 최종금액이 아닙니다.
6. sections는 line id를 참조하며 한 행은 여러 section에 속할 수 있습니다. 코드 힌트보다 명시적 OCR 라벨을 우선합니다.
7. card_number는 명시적인 카드번호 라벨과 마스킹된 값이 함께 보일 때만 작성하고, 그렇지 않으면 null입니다.

[파일명]
{filename}

[코드 확인값]
{_receipt_hints(text, filename)의 JSON}

[의미별 OCR 근거]
{_semantic_prompt_payload(..., item_pass=False)의 압축 JSON}
```

### 19.1 요약 prompt의 동적 OCR payload

`item_pass=False`인 payload에는 전체 품목 원문 대신 카테고리 판단용 품목 행을 최대 8개까지만 포함한 다음 구조가 들어간다.

```json
{
  "lines": [
    {"id": "L001", "text": "OCR 행"}
  ],
  "sections": {
    "issuer": ["L001"],
    "business_info": [],
    "transaction": [],
    "service_detail": [],
    "adjustments": [],
    "tax_summary": [],
    "settlement": [],
    "payment": [],
    "auxiliary": [],
    "unknown": [],
    "items": ["L010", "L011"]
  },
  "item_summary": {
    "candidate_count": 0,
    "candidate_amount_sum": null
  }
}
```

실제로는 값이 없는 section은 제외된다. `lines`의 좌표는 LLM prompt에서는 제외하지만 전체 `semantic_evidence`에는 진단용으로 보존한다.

### 19.2 카테고리 근거 validator

LLM이 반환한 `category_evidence_line_ids`는 중복을 제거하고 최대 3개까지만 사용한다. 실제 `semantic_evidence.lines`에 존재하지 않는 ID는 제거하고 다음 진단을 `category_evidence_validation`에 저장한다.

- `valid`: 모든 ID가 유효함
- `partially_invalid`: 일부 ID만 유효함
- `invalid`: 반환한 ID가 모두 존재하지 않음
- `missing`: 카테고리를 선택했지만 빈 배열을 반환함
- `not_provided`: 이전 모델 응답처럼 키 자체가 없음
- `not_applicable`: 카테고리가 선택되지 않음

현재 enforcement는 `advisory`다. 잘못된 ID는 제거하고 검토 권고를 trace에 남기지만, 카테고리나 기존 `needs_review`를 강제로 변경하지 않는다. 동일 평가 세트에서 정확도 영향을 확인한 후 강제 정책으로 전환할 수 있다.

## 20. 품목 full 프롬프트 전문

코드 위치는 `_receipt_items_prompt()`다.

```text
영수증의 실제 구매 품목만 JSON으로 반환하세요.
형식: {"items":[{"name":...,"specification":...,"quantity":...,"unit":...,"unit_price":...,"supply_amount":...,"tax_amount":...,"total_amount":...}]}

규칙:
1. 근거 행 하나는 원칙적으로 품목 하나입니다. 확인되는 품목을 누락하지 마세요.
2. 합계·소계·공급가액·부가세·할인·결제·승인·안내 행은 품목이 아닙니다.
3. 후보의 name/quantity/unit_price/amount는 코드 추정값입니다. raw_cells가 다르면 raw_cells를 우선합니다.
4. 확인할 수 없는 필드는 null로 두고 상품 자체가 확인되면 품목을 삭제하지 마세요.
5. 영수증에 명시된 품목 수: {stated_count 또는 미확인}
6. 열 순서는 영수증마다 다릅니다. raw_cells의 순서를 수량-단가로 고정하지 말고 column_resolution과 산술 관계를 확인하세요.
7. 판매번호·거래번호·주문번호·승인번호·사업자번호는 품목명이 아닙니다.
8. candidate_type이 incomplete_item이어도 상품명과 주가격이 있으면 품목을 삭제하지 말고 불명확한 필드만 null로 두세요.
9. alternate_price_candidates는 괄호로 표시된 회원가·할인가·참고가격 후보입니다. 명확한 라벨이 없으면 주가격을 대체하지 마세요.
10. 영수증에 명시된 총수량: {stated_quantity 또는 미확인}. quantity_resolution이 receipt_total_remainder이면 다른 품목 수량과 총수량으로 복원한 값입니다.
11. product_code는 재고·상품 식별 코드이며 품목명이 아닙니다. name_candidate를 품목명으로 사용하고 필요한 코드는 specification에만 보존하세요.
12. raw_name_candidate와 name_cleanup이 있으면 거래일시·POS·판매번호·상품 열 제목을 제거한 name_candidate를 사용하세요. 상품명에 날짜, POS 번호, `상품코드/단가/수량/금액` 헤더를 포함하지 마세요.
13. alias_candidates는 다른 언어로 반복 표기된 같은 상품명, specification_candidates는 중량·크기·묶음 규격, option_candidates는 SKU·색상 옵션입니다. 이 값들은 name에 다시 합치지 말고 specification에 보존하세요.
14. item_candidates가 행 근거와 충돌하거나 품목을 누락하면 lines와 recovery_excerpt(있는 경우)를 사용해 복원하세요. 정가 다음 행에 SKU·색상·할인액·할인 후 금액이 이어지면 같은 품목입니다.
15. sections는 line id 참조이며 items가 주요 품목 근거입니다. adjustments·settlement·tax_summary 행은 품목으로 만들지 말고 검산과 제외 근거로만 사용하세요.
16. rel은 H=높음, M=검토 필요입니다. why는 T=표, R=품목영역, F=주유블록, D=도메인 서비스 추론, A=산술일치, C=품목수일치, S=합계일치, E=기타근거입니다. H 후보를 우선하고 M은 lines로 확인하세요.
17. candidate_type이 fuel_sale_item이면 분리된 유종명·리터 수량·리터당 단가·결제금액을 하나의 주유 품목으로 유지하세요. arithmetic_tolerance 안의 반올림 차이는 허용하고 결제·세금·할인 행을 별도 품목으로 만들지 마세요.
18. candidate_type이 single_service_charge이면 명확히 식별된 서비스 사업자와 단일 결제 총액을 한 건의 서비스 품목으로 해석하세요. 같은 서비스를 일반 후보로 중복 생성하지 말고 quantity_resolution이 single_service_default인 수량 1은 추론값으로 보존하세요.
19. candidate_type이 measured_quantity_item이면 명시된 단가 × 측정량 ≈ 금액 관계를 한 품목으로 유지하세요. 소수 측정량을 개수로 반올림하지 말고, 단위가 OCR에 없으면 새로 추측하지 마세요.

[품목 근거]
{품목 evidence payload의 압축 JSON}
```

자유 서술형 `note`는 LLM 출력 스키마에 없다. 모델이 임의로 반환해도 `_items_without_model_notes()`가 제거한다. 단가 복원이나 candidate fallback처럼 코드가 검증한 시스템 note는 이후 정규화 단계에서 별도로 생성될 수 있다.

### 20.0 candidate 0개 전용 recovery prompt

신뢰 가능한 candidate가 없으면 아래 축약 prompt가 full prompt를 대체한다.

```text
신뢰 가능한 품목 candidate가 없습니다. 아래 OCR에서 명시적으로 확인되는 실제 구매 품목만 JSON으로 복구하세요.
형식: {"items":[{"name":...,"specification":...,"quantity":...,"unit":...,"unit_price":...,"supply_amount":...,"tax_amount":...,"total_amount":...}]}

규칙:
1. OCR에 직접 보이는 품목만 반환하고 추측하지 마세요.
2. 합계·소계·세금·할인·결제·승인·카드·사업자 정보는 품목이 아닙니다.
3. 품목명이나 품목 금액을 확인할 수 없으면 items=[]를 반환하세요.
4. 불명확한 개별 필드는 null로 두고 OCR 행 순서를 유지하세요.
5. 표 행과 OCR 본문이 충돌하면 표 행을 우선하되 숫자의 의미를 임의로 바꾸지 마세요.

[복구 근거]
{
  "printed_item_count": "있는 경우",
  "printed_total_quantity": "있는 경우",
  "ocr_excerpt": "최대 1,800자",
  "table_rows": "최대 12행, 행당 최대 6칸·칸당 100자"
}
```

### 20.1 품목 evidence payload 전문 구조

품목 prompt의 `{품목 evidence payload}`는 아래 정보를 조합한다.

```json
{
  "lines": [{"id": "L001", "text": "후보로 구조화되지 않은 보조 OCR 행"}],
  "sections": {
    "items": ["L001"],
    "adjustments": [],
    "settlement": [],
    "tax_summary": []
  },
  "item_summary": {
    "candidate_count": 1,
    "candidate_amount_sum": 10000
  },
  "structured_evidence": {
    "receipt_structure": "STANDARD_ROW",
    "rows": [],
    "item_blocks": []
  },
  "evidence_bundles": [
    {
      "bundle_id": "I001",
      "parser_profile": "...",
      "applicable_rules": [],
      "raw_cells": [],
      "normalized_numbers": [],
      "source_observation": {
        "source": "table",
        "page": 1,
        "columns": [],
        "candidate_type": "...",
        "structure_type": "...",
        "service_type": "...",
        "inferred": false
      },
      "text_observations": {
        "name_fragment": "...",
        "raw_name_fragment": "...",
        "aliases": [],
        "specifications": [],
        "options": []
      },
      "arithmetic_relations": [
        {
          "operands": [1, 10000],
          "operator": "multiply",
          "observed_result": 10000,
          "difference": 0,
          "tolerance": 0.01,
          "matched": true
        }
      ],
      "alternative_price_observations": [],
      "support_signals": {
        "reliability": "H",
        "reasons": ["T", "A"],
        "uncertainty": []
      },
      "parser_hypothesis": {
        "column_resolution": "header",
        "quantity": 1,
        "unit_price": 10000,
        "total_amount": 10000,
        "unit": null,
        "name_resolution": "...",
        "quantity_resolution": "...",
        "arithmetic_tolerance": 0.01,
        "is_binding": false
      }
    }
  ],
  "structure_hypothesis": {
    "column_schema": "...",
    "layout": "...",
    "relationship": "...",
    "confidence": "...",
    "is_binding": false
  },
  "common_rules": [
    "Use only OCR evidence from this payload.",
    "Do not emit totals, subtotals, tax, payment, approval/order numbers, discounts, or headers as products.",
    "Keep receipt item order and return null for fields that the applicable bundle rules cannot establish.",
    "Apply only each bundle's applicable_rules; do not borrow a column rule from another bundle."
  ],
  "evidence_policy": {
    "raw_cells_are_authoritative": true,
    "parser_hypotheses_are_refutable": true,
    "preserve_conflicting_observations": true,
    "missing_values_remain_null": true
  },
  "recovery_excerpt": "..."
}
```

실제 전송 전 다음 축약이 적용된다.

1. `raw_cells`와 완전히 같은 OCR line은 `lines` 및 `structured_evidence.rows`에서 제거한다.
2. `null`, 빈 문자열, 빈 배열, 빈 객체는 재귀적으로 제거한다. 단, `false`와 숫자 `0`은 유지한다.
3. `recovery_excerpt`는 candidate가 없거나, 영수증 표기 품목 수와 candidate 수가 충돌할 때만 OCR 앞부분 최대 1,600자를 넣는다.
4. 원래의 `item_candidates` 배열은 제거하고 필요한 정보만 `evidence_bundles`로 재구성한다.

### 20.2 `applicable_rules`에 삽입되는 동적 규칙 전문

각 candidate에는 유형 또는 관측 열 구조에 맞는 규칙만 들어간다.

`fuel_sale_item`:

```text
Treat the fuel name, litre volume, price per litre, and paid total as one fuel item even when they occur on separate OCR lines.
Use quantity_candidate as litres, unit_price_candidate as price per litre, and amount_candidate as the item total.
Accept the documented arithmetic_tolerance for receipt rounding; do not require exact floating-point multiplication.
Do not emit tax, approval, cashback, discount, QR, or settlement lines as additional items.
```

`measured_quantity_item`:

```text
Treat the explicitly multiplied unit price, measured quantity, and observed amount as one item.
Preserve the decimal quantity and its unit when observed; do not round it to a count.
Accept the documented arithmetic_tolerance for receipt rounding.
Do not turn tax, discount, settlement, or membership rows into additional items.
```

`single_service_charge`:

```text
Treat the strongly identified merchant/service and single paid total as one service item.
Use quantity 1 only as the documented single-service default, not as directly printed quantity evidence.
Do not emit approval, card, tax, settlement, or generic merchant fragments as additional items.
Prefer this specific service candidate over a duplicate generic candidate for the same service.
```

일반 candidate는 아래 세 그룹에서 현재 구조에 맞는 규칙을 조합한다.

열 구조 규칙:

```text
[4-column]
Evaluate the four observed cells as name, quantity, unit_price, and total_amount.
Use the multiplication relation as supporting evidence; if it conflicts, preserve OCR values and leave the ambiguous mapping null.

[3-column]
Evaluate the observed cells as name, quantity, and total_amount.
Set unit_price only when it is explicitly observed; do not derive it merely by division.

[2-column]
Extract only the observed name and total_amount.
Do not default quantity to 1 and do not copy total_amount into unit_price; leave both null unless explicitly observed elsewhere in this bundle.

[unknown]
Do not assume a column order; map only fields directly supported by raw_cells and nearby referenced lines.
```

행 배치 규칙:

```text
[single-line]
Treat this bundle as one item row and do not merge unrelated neighboring bundles.

[multi-line]
Join only adjacent fragments that share item evidence; never cross a new-item, subtotal, tax, or settlement boundary.

[unknown]
Keep fragments separate unless adjacency and shared numeric evidence clearly establish one item.
```

관계 규칙:

```text
[flat]
Treat the row as an independent item; do not invent a parent-child relation.

[parent-child]
Determine whether option/addition evidence belongs to the immediately preceding parent item.
Store a supported child as specification; emit it separately only with evidence that it was independently sold.
```

## 21. 품목 compact retry 프롬프트 전문

코드 위치는 `_receipt_items_retry_prompt()`다. 아래 문장은 현재 코드상 영문 그대로 전송된다.

```text
Return one JSON object with an items array only. Use only the supplied OCR candidates. Do not add totals, tax, payment, approval, discount-summary, or metadata rows as items. Preserve candidate order and use null for unknown values. Printed item count: {stated_count 또는 unknown}. Item keys: name,specification,quantity,unit,unit_price,supply_amount,tax_amount,total_amount.
Candidates:{compact_candidates JSON}
```

`compact_candidates JSON`의 각 원소는 다음 키를 가진다.

```json
{
  "id": "I001",
  "name": "...",
  "quantity": 1,
  "unit": "...",
  "unit_price": 10000,
  "list_price": null,
  "discount_amount": null,
  "paid_price": null,
  "amount": 10000,
  "product_code": null,
  "options": [],
  "raw": ["최대 120자", "최대 3개 cell"]
}
```

compact retry는 full prompt의 긴 규칙, 전체 semantic lines, structured evidence를 보내지 않고 이미 생성된 OCR candidate만 사용한다.

## 22. 프롬프트 이후 품목 확정 순서

LLM 응답은 바로 저장되지 않고 아래 단계를 거친다.

```text
JSON 파싱 및 items 배열 검증
  ↓
모델 자유 서술 note 제거
  ↓
동일 품목 반복 제거
  ↓
OCR candidate와 name·금액·수량·단가 기준 reconcile
  ↓
H 등급 candidate의 수량·단가·금액 보호
  ↓
누락 또는 단일 서비스 충돌 시 grounded recovery
  ↓
품목 산술 및 영수증 합계 검증
  ↓
정규화·validator
```

따라서 이 문서의 prompt는 모델이 받는 요청 전문이지만, 모델 응답 자체가 최종 finance record를 의미하지는 않는다.
