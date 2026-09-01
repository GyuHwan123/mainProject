# 현재 영수증 처리 파이프라인

이 문서는 현재 코드 기준으로 영수증 업로드부터 OCR, 품목 추출, 재무 분류, 사용자 검토, 저장 및 Excel 생성까지의 흐름을 설명한다.

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
2. 실패하면 candidate 중심의 `compact_retry` 시도

성공한 model item은 중복을 제거한 뒤 OCR candidate와 reconcile한다. model item이 candidate보다 부족하거나 단일 서비스 추론과 충돌하면, OCR 근거가 충분한 경우 candidate 기반 복구를 시도한다.

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
- 명확한 OCR 날짜와 금액 evidence 우선
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
| 전자제품 | `PURCHASE_REQUEST` |
| 교통 | `TRAVEL_EXPENSE` |
| 주유/교통 | `EXPENSE_REPORT` |
| 미용 | `WELFARE_BENEFIT` |
| 도서 | `WELFARE_BENEFIT` |
| 미용/생활 | `WELFARE_BENEFIT` |
| 식비 | `WELFARE_BENEFIT` |
| 레저 | `WELFARE_BENEFIT` |
| 식비/주류 | `WELFARE_BENEFIT` |
| 의료 | `WELFARE_BENEFIT` |
| 문화 | `WELFARE_BENEFIT` |

### 6.3 분류 검증 규칙

분류 contract는 다음 순서로 적용된다.

1. 카테고리는 canonical label 또는 안전한 legacy alias로 정규화한다.
2. 카테고리가 유효하지 않으면 자동 확정하지 않고 검토 대상으로 둔다.
3. `document_type`이 유효하면 카테고리의 기본 매핑과 달라도 해당 문서 유형을 유지한다.
4. `document_type`이 없거나 유효하지 않고 카테고리는 유효하면 카테고리 매핑에서 문서 유형을 유도한다.
5. 모델이 `needs_review`를 요청하면 자동 확정하지 않는다.

따라서 다음 입력은 `TRAVEL_EXPENSE`로 유도된다.

```json
{
  "document_type": null,
  "expense_category": "교통"
}
```

반면 유효한 문서 유형이 직접 제공되면 카테고리가 다른 기본 유형에 연결되어 있어도 문서 유형을 덮어쓰지 않는다.

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

카테고리 선택 목록은 현재 선택한 문서 유형에 매핑된 값만 표시한다. 현재는 `TRAVEL_EXPENSE`를 선택하면 `교통`이 표시된다.

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
- `교통`은 현재 `TRAVEL_EXPENSE`, `주유/교통`은 `EXPENSE_REPORT`로 연결된다.
- 일반 교통비와 출장 교통비를 카테고리만으로 구분하지는 않는다.
- 유효한 `document_type`을 모델이나 사용자가 직접 지정하면 카테고리 기본 매핑보다 우선한다.
- workbook은 양식 파일 하나를 선택하는 방식이 아니라 네 재무 시트를 모두 만들고 record를 해당 시트에 배치하는 방식이다.
- `item_extraction_diagnostics`와 trace는 보존되지만 candidate 단계별 정답 precision/recall 평가는 별도의 평가 데이터와 평가기가 필요하다.

