# 영수증 공급가액 / VAT 후처리

`finance_receipt_simple._reconcile_amounts()`에서 OCR 명시값 → 과세/면세 구성 → 확실한 거래의 결정론적 파생 순서로 처리한다. 근거 없는 모델 공급가액/VAT는 제거한다. public 필드는 그대로이며 진단은 기존 `amount_resolution`에 추가한다.

- 명시 공급가액/VAT는 작은 합계 차이 때문에 덮어쓰지 않는다. 서로 다른 명시 후보나 큰 합계 충돌은 REVIEW한다.
- 최종 카드결제/카드전표 구간이 명확하면 그 구간의 금액을 선택한다. 할인 전 요약을 최종 전표와 합치지 않는다.
- 확정 총액과 구체적 과세 교통 근거가 있고 혼합 구성·추가 세금/수수료·할인 과세표준 불확실성이 없을 때만 `round(total / 11)`을 계산한다. 명시 VAT가 있으면 공급가액만 `total - VAT`로 계산한다.
- 명확한 도서 단독/면세 거래는 공급가액을 총액, VAT를 0으로 처리한다. 카테고리만으로 확정하지 않는다.
- 혼합 영수증은 명시 과세 공급가액과 면세액을 합산할 수 있지만 전체 총액의 1/11로 계산하지 않는다.
- UNKNOWN, 불확실한 총액, 구성 누락, 추가 세금/기금/봉사료/수수료, 할인 후 VAT 근거 부재, OCR 충돌에서는 없는 금액을 null로 남기고 REVIEW 사유를 전달한다. 사용 가능한 명시값은 부분적으로 보존한다.

추가 trace: `tax_treatment`, `supply_source`, `tax_source`, `changes`, `review_reason`(사유 코드 배열). 명시 VAT의 차액으로 파생한 공급가액도 `DERIVED_TAXABLE_TOTAL`로 표시하며 VAT는 `EXPLICIT_OCR`로 구분한다.

LLM 프롬프트와 호출 수는 변경하지 않는다. 추가 문맥 추출은 Python 후처리에서만 실행한다. 품목 및 결제수단 후처리와 카테고리 규칙은 그대로다. 추가 비용은 OCR 문자열 정규식 검색이며 실제 서비스 지연시간은 측정하지 않았다.

독립 회귀 테스트: `python -m unittest discover -s tests -p test_receipt_tax_policy.py` (backend 디렉터리). 서버 의존성 없이 실제 소스의 순수 함수 AST를 로드하여 금액 보정과 REVIEW 전달을 검증한다. 전체 통합 테스트는 backend 의존성이 설치된 환경에서 실행해야 한다.
