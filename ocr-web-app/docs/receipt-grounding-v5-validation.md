# 영수증 후처리 1차 개선 — 2026-09-06

## 변경 파일

| 파일 | 변경 내용 |
| --- | --- |
| `backend/app/services/finance_receipt_simple.py` | 명시 세액 보존 진단, total에서 명시 supply/tax를 빼는 파생, OCR 외식 근거 기반 1/11 계산, 전체 품목 합계 및 REVIEW 연결, grounding에 영수증 총액 전달 |
| `backend/app/services/receipt_item_grounding.py` | 요약 숫자 bbox 예약, 숫자 중복 사용 차단, 전체 총액을 초과하는 산술 일치 품목의 OCR 재검토, 계층형 옵션 제거, 헤더 없는 표 추론, 완전한 고신뢰 표의 OCR 품목 목록 우선 |
| `backend/tests/test_receipt_global_consistency.py` | 요청한 핵심 사례를 포함한 회귀 테스트 13개 추가 |
| `backend/tests/test_receipt_item_grounding.py` | 헤더 없는 표와 계층형 전용 후처리에 맞게 기존 기대값 갱신 |
| `backend/tests/test_receipt_document_classifier.py` | 총액 전달용 선택 키워드를 받도록 mock 수정; LLM 1회 호출 검증 유지 |
| `backend/tests/test_finance_classification.py` | 기존 코드의 v1.3 프롬프트 버전 및 분류 문구와 달랐던 테스트 기대값 갱신; 실제 프롬프트는 변경하지 않음 |
| `docs/receipt-grounding-v5-validation.md` | 변경 정책, 검증 결과, 22개 재평가 지표 기록 |

## 공급가액/부가세

- OCR 명시값과 기존 과세·면세 구성요소를 우선한다. 명시 tax는 교차검증이 부족해도 EXPLICIT_OCR로 보존하며 REVIEW를 남긴다. 기존 `rejected_tax_amount` 키는 null로 유지하고 `uncorroborated_tax_amount`에 보존 사실을 기록한다.
- 확정된 total과 명시 tax/supply로 반대쪽 금액을 계산한다. 혼합·추가 세금·충돌·할인 과세표준 불명확 등 기존 보호 조건은 유지한다.
- 음식점/식당/레스토랑/카페 등 업종 OCR과 메뉴/주문/음식·음료 OCR 근거를 함께 요구한다. 카테고리만으로 세금을 계산하지 않는다.
- 일반 과세로 판정되고 양쪽 세액이 없으면 `tax=round(total/11)`, `supply=total-tax`를 적용한다. 면세·도서·불명확한 교통·별도 세금·혼합·할인 등은 계산을 보류한다. 기존 명확한 과세 교통 키워드 정책은 유지한다.
- 명확한 면세 거래의 supply=total, tax=0 및 기존 허용 오차를 유지한다. 불확실한 값은 null과 REVIEW로 표시한다.
- 계산 출처 및 이유는 `amount_resolution`에 기록한다. 기존 `supply_from_guarded_arithmetic` 진단도 유지한다.

## 전체 품목 일관성과 bbox

- 유효한 비음수 품목 금액 합계와 최종 총액을 비교한다. 합계 또는 합계에서 할인 절댓값을 뺀 값이 기존 오차 이내인지 확인한다.
- 품목 금액 일부가 없으면 전체 일관성을 true로 처리하지 않는다. 알려진 품목 합계만으로 총액을 초과해도 REVIEW한다.
- 2×13,900=27,800 / 영수증 총액 13,900 사례는 OCR 수량 1 근거가 있으면 교정한다. bbox가 없으면 quantity=1 후보만 기록하고 REVIEW한다.
- 요약 라벨과 같은 OCR 행에 있는 숫자를 page/index/bbox/value로 예약한다. 이 bbox는 표 및 근접 숫자 후보에서 제외한다. 같은 값이라는 이유만으로 다른 품목 bbox를 제외하지 않는다.
- 모델 품목의 인근 요약 bbox가 금액 출처로 의심되고 독립 품목 숫자가 없으면 SUMMARY_AMOUNT_USED_AS_ITEM로 REVIEW한다. 출처가 확정되지 않은 모델 값을 임의로 삭제하지 않는다.
- 교정 후보의 숫자 ID 중복과 이미 일관된 품목에 연결된 숫자 ID를 검사한다. 중복 후보의 교정을 차단하고 NUMERIC_BBOX_REUSED를 기록한다. 전역 최적화 assignment 알고리즘은 추가하지 않았다.

## 레이아웃

HIERARCHICAL은 유상 부모 뒤 5행 높이 이내의 들여쓰기된 무가격/0원 옵션을 OCR 이름으로 확인해 제거한다. DrinkSwap·사이드 변경·음료 변경도 같은 규칙을 따른다. 유상 옵션은 유지한다. 이름 중복으로 위치를 확정하지 못하면 HIERARCHICAL_ITEM_AMBIGUOUS로 보류하며 `removed_items`와 `removal_candidates`를 유지한다.

HEADERLESS_COLUMN_TABLE은 API의 기존 COLUMN_TABLE을 유지하면서 `table_schema`의 subtype으로 기록한다. 조건은 다음과 같다.

1. 이름 왼쪽 / 숫자 오른쪽의 logical row가 최소 2개 반복된다.
2. 세 숫자의 수량·단가·금액 산술 해가 유일하고, 행 간 열 역할이 호환된다.
3. 숫자 열 중심은 중앙값에서 행 높이의 0.6배 이내이며, 이름 시작 x 편차는 행 높이 이내다.
4. 사용 bbox 신뢰도가 모두 0.95 이상이다. 총액 요약 이후 행은 제외한다.
5. 정렬·신뢰도가 부족하거나 일부 행을 해석하지 못하면 HEADERLESS_TABLE_LOW_CONFIDENCE로 REVIEW한다. 불완전한 표를 완전한 품목 목록으로 간주하지 않는다.

완전한 고신뢰 표는 OCR logical row의 숫자를 우선하고 누락 품목을 추가한다. 대응 OCR 행이 없는 일반 모델 품목은 완전한 고신뢰 표에서만 제거한다. 이름 매칭이 모호하거나 표가 불완전하면 기존 보수적 경로를 유지한다.

## validation / 호환성

`automation_validation.checks`에 `item_sum_vs_total`, `item_sum_difference`, `reserved_summary_amount_count`, `reused_numeric_bbox_detected`, `global_item_consistency_pass`, `hierarchical_collapsed_count`, `headerless_table_detected`를 추가했다.

추가 REVIEW 코드는 ITEM_SUM_TOTAL_MISMATCH, SUMMARY_AMOUNT_USED_AS_ITEM, NUMERIC_BBOX_REUSED, HIERARCHICAL_ITEM_AMBIGUOUS, HEADERLESS_TABLE_LOW_CONFIDENCE, ITEM_TOTALS_INCOMPLETE다. 기존 ITEM_SUM_MISMATCH와 진단 필드를 유지한다. global consistency의 null은 검증 불가이며 true와 구별해야 한다.

OCR → Gemma 4B 1회 → 금액 후처리 → grounding → validation 순서와 저장/API 기본 필드는 유지한다. 모델·OCR 엔진·프롬프트·DB 스키마는 변경하지 않았다. grounding 버전은 `bbox-item-grounding-v5-global-layout`이다. 품목 숫자·개수 및 REVIEW 비율은 의도적으로 달라질 수 있다.

## 실행 검증

기존 backend 컨테이너의 마운트된 소스로 실행했다.

```text
python -m unittest discover -s tests -p 'test_receipt*.py'
80 tests: OK
python -m unittest discover -s tests -p 'test_finance*.py'
100 tests: OK
```

동일 22개 실제 영수증의 OCR/LLM end-to-end 재실행은 이번 검증에 포함하지 않았다. 위 결과는 결정적 후처리 및 관련 통합 회귀 결과이며 실제 정확도 개선 수치를 의미하지 않는다.

## 동일 22개 재평가 지표

- total/supply/tax 각각의 정답 일치율, 명시 tax의 불필요한 null 비율, 세금 보류 정확성, 과세/면세 판정 오류.
- 품목 수량·단가·금액 정확도, 품목 precision/recall, 누락 및 과다 생성 개수.
- 잘못된 품목 산술이 PASS된 건수, 품목 합계 차이와 할인 적용 기준, global consistency false/null 건수.
- summary bbox 침입 및 숫자 중복 사용 건수; 교정 전후 실제 bbox ID를 확인한다.
- HIERARCHICAL 무가격 옵션 축약의 정밀도와 유상 옵션 오삭제 건수.
- UNKNOWN→HEADERLESS_COLUMN_TABLE 전환 수, 오탐, 미해석 행 수, OCR 추가/삭제 품목의 정답 여부.
- PASS/REVIEW 비율과 이유별 건수, 특히 잘못된 PASS 감소 및 정상 영수증의 과도한 REVIEW 증가 여부.
- LLM call_count=1 유지, grounding elapsed_ms 및 전체 지연시간 변화.
- 동일 이미지·ground truth·모델·설정으로 전후 결과를 비교하고, null을 정답으로 간주할 항목을 고정한다.
