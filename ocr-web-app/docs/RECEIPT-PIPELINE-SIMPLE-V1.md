# 영수증 파이프라인 Simple v1.1

- 기준일: 2026-09-03
- 프롬프트 버전: `receipt-simple-v1.1-one-call-amount-evidence`
- 운영 모델: `RECEIPTS_LLM_MODEL` 환경 변수로 지정
- 원칙: 영수증당 LLM 최대 1회, 불확실한 값은 자동 확정보다 REVIEW 우선

이 문서는 현재 실행되는 단일 호출 영수증 파이프라인을 설명한다. 과거 파이프라인 문서인 `CURRENT-RECEIPT-PIPELINE.md`는 변경하지 않는다.

## 1. 전체 흐름

```text
영수증 업로드
  → 영수증 전용 이미지 전처리
  → 한국어 PaddleOCR
  → OCR 텍스트·좌표·영역 저장
  → OCR 사전 검사
      ├─ 판독 불가: LLM 호출 생략, REVIEW
      └─ 판독 가능
          → OCR 행 정리 및 8,000자 제한
          → 명확한 금액 라벨 근거 추출
          → LLM JSON 추출 1회
          → 규칙 기반 금액 조정
          → 형식·근거·산술 검증
  → 중복 영수증 검사
  → finance record 저장(status=REVIEW)
  → 사용자 검토·수정·확정
  → Excel 생성
```

## 2. OCR 처리

프런트엔드는 다음 endpoint를 사용한다.

```http
POST /ocr/upload?processing_mode=receipt
```

이미지에는 원근 보정, 기울기 보정, 내용 영역 자르기, 작은 이미지 확대, 조명 보정, 국소 대비 향상, 획 닫힘 연산과 선명화를 순서대로 시도한다.

PaddleOCR은 한국어 PP-OCRv5 모바일 검출·인식 모델을 사용한다. 영수증 모드에서는 위에서 아래, 같은 행에서는 왼쪽에서 오른쪽 순서로 텍스트를 정렬한다. 전처리 좌표는 원본 이미지 좌표로 복원해 저장한다.

주요 코드:

- `ocr/app/services/receipt_preprocess_service.py`
- `ocr/app/services/ocr/ocr_service.py`
- `ocr/app/services/ocr/ocr_parser.py`
- `ocr/app/services/receipt_table_service.py`

## 3. 분류 입력과 사전 검사

저장된 OCR 문서는 다음 endpoint로 분류한다.

```http
POST /finance/records/classify

{"document_id": "..."}
```

다음 입력은 LLM을 호출하지 않고 REVIEW로 처리한다.

- 공백을 제거한 OCR 텍스트가 40자 미만
- 금액 형식의 숫자가 없음
- OCR 텍스트가 20,000자를 초과함

LLM 입력용 OCR은 공백과 중복 행을 정리하고 명백한 안내문을 일부 제거한다. 원래 행 순서에 `L001` 형식의 ID를 붙이며 최대 8,000자로 제한한다. 길이를 초과하면 문서 상단·하단과 금액 포함 행을 우선 보존한다.

## 4. LLM 전 금액 근거 추출

LLM 호출 전에 라벨과 금액이 같은 OCR 행에 명확히 연결된 값만 추출한다.

| OCR 표현 | 내부 의미 |
|---|---|
| 공급가액, 공급액 | `supply_amount` |
| 과세액, 과세물품가액, 과세금액, 과세합계, 과세매출 | `taxable_supply_amount` |
| 면세물품가액, 면세상품금액, 면세합계 | `tax_exempt_amount` |
| 부가세, 부가세액, 부가가치세, VAT | `tax_amount` |
| 부가세포함 `(금액)` | `tax_amount` |
| 결제액, 결제금액, 결제요금, 승인금액, 받을금액, 구매금액 | `total_amount` |
| 총할인액, 총할인금액, 할인금액 | `discount_amount` |
| 절사금액, 절삭금액, 반올림 | `rounding_adjustment` |

안전 정책:

- `과세액` 안의 `세액`을 VAT로 오인하지 않는다.
- 카드번호 뒤의 숫자와 마스킹 기호가 붙은 값은 결제액으로 확정하지 않는다.
- 라벨과 숫자가 다른 OCR 행이면 규칙 기반 확정값으로 사용하지 않는다.
- 줄이 분리된 원문은 LLM에 그대로 제공하고 결과가 불확실하면 REVIEW로 남긴다.

확정 근거는 프롬프트의 `[규칙 기반 금액 근거]` 블록에 JSON으로 제공한다.

## 5. LLM 호출

현재 프롬프트 버전은 `receipt-simple-v1.1-one-call-amount-evidence`다.

LLM은 한 번의 호출에서 다음 구조를 반환한다.

```json
{
  "merchant": null,
  "transaction_date": null,
  "expense_category": null,
  "supply_amount": null,
  "tax_amount": null,
  "discount_amount": null,
  "total_amount": null,
  "payment_method": null,
  "items": [
    {"name": null, "quantity": null, "unit_price": null, "total_amount": null}
  ]
}
```

프롬프트의 금액 정책:

- 알 수 없는 값은 `null`
- `total_amount`는 실제 최종 결제·승인·받을 금액
- `supply_amount`는 과세상품 세전 금액과 면세상품 금액의 합
- 면세상품만 있으면 공급가액은 면세상품 금액이고 부가세는 0
- 과세액·과세물품가액은 부가세가 아니라 세전 공급액
- 부가세·부가세액·VAT만 `tax_amount`
- 할인·쿠폰·소계·부가세·결제 행은 품목으로 만들지 않음
- 쇼핑백·포장비·배달비처럼 실제 대가를 지불한 유상 거래 행은 품목으로 유지
- 공급가액·부가세가 할인 전 세금 요약으로 명시됐으면 최종 결제액과 다르더라도 OCR 값을 유지

## 6. LLM 후 금액 조정

`_reconcile_amounts()`는 다음 순서로 금액을 조정한다.

```text
1. 명확한 공급가액 OCR 값
2. 과세물품가액 + 면세물품가액
3. 면세 전용 영수증의 면세물품가액
4. 명확한 총액·부가세·절사값으로 조건부 산술 복구
5. 근거가 부족하면 모델 값 또는 null 유지
```

산술 복구식:

```text
supply_amount = total_amount - tax_amount - rounding_adjustment
```

절사금액이 `-3`이면 `47,110 - 3,621 - (-3) = 43,492`로 계산한다.

안전 조건:

- 총액과 부가세가 명확한 OCR 근거여야 함
- 과세·면세 라벨 중 일부 금액이 누락되면 산술 복구하지 않음
- 면세금액 미검출을 0으로 간주하지 않음
- 근거 없는 부가세 후보는 모델 값을 덮어쓰지 않음
- 명시된 0원과 정보 없음 `null`을 구분

결정 과정은 `structured_data.amount_resolution`에 저장한다.

```json
{
  "policy": "explicit_ocr_then_components_then_guarded_arithmetic",
  "explicit": {},
  "changes": ["tax_from_explicit_ocr", "supply_from_guarded_arithmetic"]
}
```

## 7. 금액 적용 예시

### 복합과세와 절사

```text
면세물품가액  7,280
과세물품가액 36,212
부가세         3,621
절사금액          -3
결제금액       47,110
```

```json
{"supply_amount": 43492, "tax_amount": 3621, "total_amount": 47110}
```

### 부가세 포함

```text
결제금액 23,200
(부가세포함) (2,109)
```

```json
{"supply_amount": 21091, "tax_amount": 2109, "total_amount": 23200}
```

### 할인과 절사

```text
총합계액 36,012
총할인액 -6,380
절사금액     -2
결제금액 29,630
```

할인액은 `6,380`으로 유지하고 2원은 절사 허용 오차로 처리한다. 할인액과 절사액을 합쳐 `6,382`로 바꾸지 않는다.

세금 요약에는 두 가지 정상 표기 방식이 있으므로 어느 한쪽만 강제하지 않는다.

```text
1. 할인 후 세금 요약: 공급가액 + 부가세 ≈ 최종 결제액
2. 할인 전 세금 요약: 공급가액 + 부가세 - 할인액 ≈ 최종 결제액
```

할인액의 OCR 부호가 `1,200` 또는 `-1,200`인 경우 모두 1,200원의 차감으로 검산하되, 저장된 OCR 값의 부호는 변경하지 않는다.

예를 들어 다음 영수증은 할인 전 세금 요약형이므로 정상이다.

```text
공급가액 10,910
부가세    1,090
할인액    1,200
총 결제액 10,800
```

## 8. 최종 검증

`_simple_validation()`은 값을 새로 추정하지 않고 PASS 또는 REVIEW를 결정한다.

주요 REVIEW 사유:

- 필수 필드 누락
- 허용되지 않은 비용 카테고리
- 정규화할 수 없는 날짜
- 총 결제액이 OCR 숫자에 없음
- 공급가액·부가세·할인액·총액의 두 정상 관계에 모두 해당하지 않음
- 품목 합계와 총액 불일치
- 수량 × 단가와 품목 금액 불일치
- 세금 라벨은 있으나 값을 해석하지 못함
- 교차 확인되지 않은 부가세 근거
- LLM timeout, JSON 오류 또는 서버 오류

금액 관계와 할인 후 품목 합계에는 10원의 절사·반올림 허용 오차를 적용한다.

```text
직접 일치:
abs(supply_amount + tax_amount - total_amount) <= 10

할인 전 세금 요약 일치:
abs(supply_amount + tax_amount - abs(discount_amount) - total_amount) <= 10
```

검증 우선순위:

1. 직접 일치하면 `post_discount_tax_summary`로 PASS
2. 할인액 차감 후 일치하면 `pre_discount_tax_summary`로 PASS
3. 둘 다 아니지만 공급가액과 부가세가 명확한 OCR 근거이면 값을 유지하고 REVIEW
4. OCR 근거가 없는 추론값이며 관계도 불일치하면 REVIEW

판정 근거는 `automation_validation.checks.amount_relation_basis`에 기록한다. 가능한 값은 `post_discount_tax_summary`, `pre_discount_tax_summary`, `explicit_ocr_mismatch`, `unresolved_mismatch`, `not_checkable_partial_amounts`다.

공급가액과 부가세가 원본에 없으면 `null`을 허용하고 임의의 10% 역산을 하지 않는다.
공급가액 또는 부가세 중 한쪽만 존재하면 금액 관계 검산을 건너뛰며, 존재하지 않는 반대쪽을 자동 복원하지 않는다.

## 9. null과 0

```text
null = 영수증에 없거나 안전하게 확인할 수 없음
0    = 영수증에 0원으로 명시됐거나 전액 면세임이 명확함
```

최상위 finance record, `structured_data`, API 수정 요청과 검토 화면에서 `supply_amount`와 `tax_amount`의 `null`을 보존한다. 화면에서는 `null`을 `0원`이 아닌 `-`로 표시하고 금액 검산을 `검산 불가`로 표시한다.

## 10. 저장과 중복 검사

분류 결과는 기본적으로 `status=REVIEW`로 저장하며 사용자가 확인 후 확정한다.

중복 검사는 다음 값을 사용한다.

- 정규화한 OCR 전체의 SHA-256 fingerprint
- 승인·거래·주문번호, 날짜와 금액을 조합한 identity key
- 기존 기록 호환용 legacy key

동일한 실물 영수증은 보관함에 중복 카드를 만들지 않는다.

## 11. 실행 제한

| 항목 | 현재 기본값 |
|---|---:|
| 영수증당 LLM 호출 | 최대 1회 |
| 자동 재시도 | 0회 |
| OCR 프롬프트 최대 길이 | 8,000자 |
| 최대 생성 토큰 | 800 |
| LLM timeout | 600초 |
| 전체 분류 예산 | 630초 |
| context | 4,096 |
| keep-alive | `0s` |

환경 변수로 운영 값을 변경할 수 있다.

## 12. 현재 한계

- 라벨과 숫자가 다른 OCR 행이면 좌표 기반으로 자동 연결하지 않는다.
- OCR 숫자 자체가 틀리면 산술 검증만으로 항상 복원할 수 없다.
- 할인 전 합계는 별도 최상위 필드로 저장하지 않지만, 할인 전 세금 요약 여부는 검증 근거에 기록한다.
- 절사금액은 진단에 사용하지만 공통 finance record 필드는 아니다.
- 품목별 과세·면세 유형은 저장하지 않는다.
- 불확실한 결과는 REVIEW를 우선한다.

## 13. 코드 위치

| 역할 | 파일 |
|---|---|
| OCR API | `ocr/app/api/routes/ocr.py` |
| OCR 실행 | `ocr/app/services/ocr/ocr_service.py` |
| 영수증 이미지 전처리 | `ocr/app/services/receipt_preprocess_service.py` |
| OCR 행 정렬 | `ocr/app/services/ocr/ocr_parser.py` |
| 단일 LLM·금액 근거·조정·검증 | `backend/app/services/finance_receipt_simple.py` |
| 날짜 정규화 | `backend/app/services/finance_normalization.py` |
| 중복 식별 | `backend/app/services/finance_receipt_identity.py` |
| API 모델 | `backend/app/models/finance_receipt.py` |
| 분류·저장 API | `backend/app/api/routes/finance.py` |
| 오류 분석 | `backend/app/services/finance_error_analysis_service.py` |
| Excel 생성 | `backend/app/services/finance_workbook_service.py` |
| 검토 UI | `frontend/src/pages/OCRPage.jsx` |
| 평가 UI | `frontend/src/pages/FinanceEvaluationPage.jsx` |

## 14. 테스트와 배포

금액 회귀 테스트는 `backend/tests/test_finance_classification.py`에 있다. 과세액/부가세액 구분, 복합과세, 괄호형 부가세, 카드번호 오인 방지, 면세 미검출, 조건부 산술 복구, 절사 허용과 `null` 보존을 검사한다.

```bash
cd backend
python -m unittest discover -s tests
```

소스 변경을 실행 중인 Docker 서비스에 반영하려면 백엔드를 재빌드해야 한다.
