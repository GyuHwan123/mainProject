# 영수증 파이프라인 Simple v1

- 기준일: 2026-09-02
- 프롬프트 버전: `receipt-simple-v1-one-call`
- 기본 평가 모델: `gemma3:4b`
- 원칙: 영수증당 LLM 최대 1회, 재시도와 자동 복원 없음

## 1. 전체 처리 흐름

```text
이미지 업로드
  → OCR
  → OCR 사전 검사
      ├─ 판독 불가: LLM 0회, REVIEW
      └─ 판독 가능
          → OCR 텍스트 정리 및 8,000자 제한
          → Gemma JSON 추출 1회
          → 형식·필수 필드·금액·품목 산술 검증
              ├─ 전부 통과: PASS
              └─ 하나라도 실패: REVIEW
  → fingerprint/identity key 중복 검사
  → 신규 기록 저장 또는 기존 기록 반환
```

검증기는 모델 값을 추측해서 고치지 않는다. 불일치는 `review_reasons`로 남기고 사용자가 확인한다.

## 2. OCR 사전 처리

OCR 결과에는 다음과 같은 최소 정리만 적용한다.

- 앞뒤 공백과 연속 공백 정리
- 완전히 동일한 행의 중복 제거
- 금액이 없는 명백한 URL·고객 안내 문구 제거
- 원래 행 순서를 유지하면서 `L001` 형태의 행 ID 부여
- 최대 8,000자로 제한

입력이 길면 상단, 하단, 금액이 포함된 행을 우선 보존한다. 후보 그래프, semantic evidence, structured evidence, 파일명 기반 품목 힌트는 만들지 않는다.

다음 입력은 LLM을 호출하지 않고 REVIEW로 보낸다.

- 정리된 OCR 텍스트가 40자 미만
- 금액 형식의 숫자가 없음
- OCR 텍스트가 20,000자를 초과해 과도하게 복잡함

## 3. LLM 호출

Gemma는 한 번의 호출에서 다음 JSON 필드를 반환한다.

- `merchant`
- `transaction_date`
- `expense_category`
- `supply_amount`
- `tax_amount`
- `discount_amount`
- `total_amount`
- `payment_method`
- `items[]`
  - `name`
  - `quantity`
  - `unit_price`
  - `total_amount`

코드가 파생하는 값은 문서 유형, 총 품목 수량, OCR에 명시된 마스킹 카드번호와 PASS/REVIEW 판정이다.

## 4. 날짜 정규화

LLM은 OCR 문맥에서 거래일의 의미를 선택하고, 코드는 표현 형식을 `YYYY-MM-DD`로 통일한다. 운영 파이프라인과 평가가 동일한 공용 정규화 함수를 사용한다.

다음 값은 모두 `2024-12-11`로 처리한다.

```text
2024/12/11
2024-12-11
2024.12.11
20241211
2024년 12월 11일
2024-12-11 14:30:20
24/12/11
12/11/2024
```

- 두 자리 연도는 영수증 정책상 2000년대로 해석한다.
- 연도가 마지막인 슬래시 형식은 `MM/DD/YYYY`로 해석한다.
- 달력에 존재하지 않는 날짜는 값을 만들지 않고 REVIEW 처리한다.

## 5. LLM 출력 검증

다음 조건 중 하나라도 발생하면 REVIEW다.

- 상호, 날짜, 총액, 카테고리 등 필수 필드 누락
- 허용 목록에 없는 카테고리
- 정규화할 수 없는 날짜
- 총 결제액이 OCR 숫자에 존재하지 않음
- `공급가액 + 세액 - 할인액`과 총액 불일치
- 품목 합계와 총 결제액 불일치
- `수량 × 단가`와 품목 금액 불일치
- LLM timeout 또는 JSON 오류

검증 결과는 `automation_validation`과 `review_reasons`에 기록한다.

## 6. 실행 제한

| 항목 | 제한 |
|---|---:|
| 영수증당 LLM 호출 | 최대 1회 |
| 재시도 | 0회 |
| LLM timeout | 600초 |
| API 전체 예산 | 630초 |
| 최대 OCR 입력 | 8,000자 |
| 최대 생성 토큰 | 600 |

영수증 LLM은 공용 챗봇과 다른 메모리 정책을 사용한다.

| 정책 | 공용 챗봇 | 영수증 LLM |
|---|---:|---:|
| 모델 유지 시간 | 30분 | 요청 종료 즉시 해제 (`0s`) |
| context | 8,192 | 4,096 |

영수증 요청마다 모델을 해제하여 이전 영수증의 프롬프트 캐시가 배치 후반까지 누적되지 않게 한다. 모델 재로딩 비용보다 제한된 RAM·VRAM 환경에서의 swap 고갈과 장시간 timeout 방지를 우선한다. 이 값은 `RECEIPTS_LLM_KEEP_ALIVE`와 `RECEIPTS_LLM_NUM_CTX`로 변경할 수 있다.

timeout, JSON 오류 또는 서버 오류가 발생하면 즉시 REVIEW fallback을 반환한다.

## 7. 중복 방지와 저장

중복 검사는 다음 식별값을 사용한다.

- 정규화한 OCR 전체의 SHA-256 fingerprint
- 승인·거래·주문번호와 날짜·금액을 조합한 identity key
- 기존 기록 호환을 위한 파일명·날짜·공급가액·세액·총액 키

중복이면 새 보관 기록을 만들지 않고 기존 기록을 반환한다. 원본 이미지 파일명은 `source_filename`으로 보존한다.

## 8. 평가 범위

단일 LLM 응답에서 직접 생성되거나 명확히 파생되는 다음 값을 평가한다.

- 상호, 거래일, 카테고리
- 공급가액, 세액, 할인액, 총 결제액
- 결제수단
- 품목명, 단가, 수량, 금액

운영 지표로 평균·P95·최대 응답시간, JSON 성공률, PASS 비율, timeout 비율과 입출력 토큰 수를 확인한다. 오류 분석 기능은 현재 평가 API와 화면에서 사용한다.

## 9. 현재 코드 구조

| 역할 | 파일 |
|---|---|
| 단일 LLM 추출과 검증 | `backend/app/services/finance_receipt_simple.py` |
| 날짜 등 공용 정규화 | `backend/app/services/finance_normalization.py` |
| fingerprint와 중복 식별 | `backend/app/services/finance_receipt_identity.py` |
| API 요청·응답 모델 | `backend/app/models/finance_receipt.py` |
| 운영 API와 DB 저장 | `backend/app/api/routes/finance.py` |
| 평가 실행 | `backend/app/services/finance_evaluation_runner.py` |
| 평가 점수 | `backend/app/services/finance_evaluation_scoring.py` |
| 평가 API | `backend/app/api/routes/finance_evaluations.py` |
| 평가 화면 | `frontend/src/pages/FinanceEvaluationPage.jsx` |

## 10. 제거된 과거 실행 흐름

Simple v1에서는 다음 기능을 실행하지 않는다.

- 요약 LLM과 품목 LLM 분리
- 품목 LLM compact retry
- OCR 후보 생성과 후보 점수화
- semantic/structured evidence 그래프
- 후보와 모델 결과 reconcile
- 누락 품목·수량·단가 자동 복원
- 판매처별 특수 복구

과거 구현은 별도 Git 브랜치와 기존 파이프라인 문서에 보존한다.
