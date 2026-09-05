# 영수증 document_type 분류기 분석 및 검증

2026-09-05. **구현·오프라인 평가는 완료했으나 자동 분류 승인 기준은 충족하지 못했다.**
제공 artifact는 `expense_category + merchant + items[].name`을 입력으로 받는 검토용 후보다.
모든 클래스의 threshold가 null이므로 현재는 모델 예측과 확률을 기록하고 기존 매핑을 fallback으로 사용하며 REVIEW를 유지한다.
운영 서버 배포, 실제 Gemma 호출, DB 쓰기는 수행하지 않았다.

## 데이터 감사

원본: `C:/Users/CafeAlle/Desktop/my/archive/expense_classification_clean_v4.jsonl`.
파일은 수정하거나 repository로 복사하지 않았다. SHA-256, split별 source_id, 전체 수치는
[평가 JSON](../reports/document-classifier-evaluation.json)에 저장했다.

| 정답 | 전체 | train | validation | test |
|---|---:|---:|---:|---:|
| EXPENSE_REPORT | 225 | 115 | 63 | 47 |
| PURCHASE_REQUEST | 225 | 133 | 33 | 59 |
| TRAVEL_EXPENSE | 225 | 138 | 36 | 51 |
| WELFARE_BENEFIT | 225 | 130 | 60 | 35 |
| null / REVIEW | 100 | 57 | 24 | 19 |
| 합계 | 1000 | 573 | 216 | 211 |

입력 envelope는 `source`, `source_id`, `task`, `messages`다. 실제 입력은 user message의
OCR 형태 텍스트이며 상호, 사업자번호, 날짜, 품목, 금액, 결제수단 및 업무 목적 등이 들어 있다.
정답은 assistant message 안의 JSON 문자열: `doc_type`, `expense_category`, `needs_review`다.
source는 합성 경비 분류 데이터이며 원문과 source_id는 각각 1000개로 중복이 없다.
오프라인 adapter가 짧은 설명을 찾은 사례는 847건, 품목을 찾은 사례는 854건이다.

유효한 4종 라벨 900건으로 지도학습은 가능하다. null 100건은 다섯 번째 문서 유형으로 학습하지 않는다.
train의 null은 분류 손실에서 제외하고, validation/test의 null은 threshold 평가에서 자동 선택 시 오답으로 센다.
**이 데이터만으로 운영의 4종 구별이 충분히 가능하다는 결론은 낼 수 없다.**

## 운영 입력과 차이 / leakage

| 정보 | 학습 파일 | 현재 운영 | 이번 처리 |
|---|---|---|---|
| expense_category | assistant 정답의 구형 15종 | Gemma가 추출한 현재 14종 | 정답 사용 금지; 입력 상호·품목 기반 대용값으로 별도 실험 |
| merchant | OCR 형식 텍스트 | 구조화된 merchant | 오프라인 adapter만 상호 행 파싱 |
| items[].name | OCR 형식 텍스트, 일부 누락 | Gemma + 기존 grounding 결과 | 오프라인 adapter로 품목명 근사 |
| 업무 목적·메모 | adapter 기준 847건 | 현재 프롬프트에 출력 요청 없음 | 메모 실험은 참고용, 제공 모델에서는 제외 |
| description | 별도 구조화 없음 | 저장 시 merchant를 복제 | 분류 feature로 사용하지 않음 |
| 사업자번호·날짜·금액·전체 OCR | 존재 | 기존 파이프라인에 존재 | 분류기에 전달하지 않음 |

구형 정답 카테고리 15개는 각각 단 하나의 document_type과 연결된다. 예를 들어
`교통비`는 EXPENSE_REPORT 31건, `여비교통비`는 TRAVEL_EXPENSE 148건이고,
`도서인쇄비`는 EXPENSE_REPORT 20건이다. 정답 카테고리를 feature로 넣으면 target leakage다.
현재 카테고리로 정답을 단순 변환해 넣어도 정답 생성 과정의 문맥이 남으므로 사용하지 않았다.

category 대용값은 **정답·메모를 보지 않고 상호·품목만으로** 현재 카테고리 일부를 추정한다.
694건에서 대용값을 만들었고 나머지는 null이다. 이것은 실제 Gemma 출력이 아니며
전자제품/문구 같은 넓은 근사와 추출 오차를 포함한다. 운영에서는 이 대용 규칙을 실행하지 않고
이미 추출된 expense_category를 그대로 feature로 사용한다. 따라서 현재 지표는 운영 replay 지표가 아니다.

단순 랜덤 split은 금액·날짜만 바뀐 합성 샘플이나 같은 메모 문구가 양쪽에 섞이는 문제가 있다.
상호+품목의 동일 feature 또는 동일 업무 목적 문구로 연결되는 사례들을 하나의 그룹으로 묶었다.
163개 연결 그룹을 StratifiedGroupKFold(5, seed=42)로 나눠 첫 fold는 test, 두 번째는 validation,
나머지는 train으로 사용했다. 세 split의 그룹 교집합은 없다. 의미상 유사한 문구와 같은 상호의
다른 품목은 남을 수 있어 모든 합성 template leakage가 제거됐다고 주장하지 않는다.

TF-IDF vocabulary와 IDF, LogisticRegression은 train에만 fit한다. 정답 JSON, source_id,
파일명, system prompt는 feature에 넣지 않는다. threshold는 validation에서만 선택한다.
이는 [scikit-learn의 데이터 leakage 방지 원칙](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage)을 따른다.

도서의 PURCHASE_REQUEST/WELFARE_BENEFIT 구별은 코드 구조상 허용하지만, 데이터의 도서인쇄비
20건은 모두 EXPENSE_REPORT다. 따라서 이 예시의 실제 일반화 성능은 현재 데이터로 검증할 수 없다.

## 모델 / 실험 결과

scikit-learn 1.7.2, CPU, character TF-IDF 2–5 gram, min_df=2, 최대 30000 feature,
sublinear_tf=True + LogisticRegression(C=4, max_iter=1000). 별도 형태소 모델이나 LLM은 없다.
한국어 토큰화를 위한 외부 모델 없이 문자열 feature를 사용한다.

아래 accuracy/F1은 test 중 4종 정답이 있는 192건 기준이다.

| 입력 | validation accuracy | test accuracy | test macro F1 |
|---|---:|---:|---:|
| 상호 + 품목 | 60.42% | 59.38% | 0.5088 |
| 카테고리 대용값 + 상호 + 품목 | 63.02% | 63.02% | 0.5558 |
| 위 정보 + 짧은 업무 메모 | 94.79% | 71.35% | 0.6399 |

validation의 선택 coverage 기준 baseline 승자는 카테고리 없는 모델이었다.
하지만 요청된 category feature 계약을 유지하기 위해 **카테고리 포함 후보**를 REVIEW 전용으로 제공한다.
메모 모델은 현재 운영에 없는 입력이므로 운영 후보로 선택하지 않았다.
메모 실험조차 validation에서 test로 크게 하락하므로 검증셋 정확도를 운영 정확도로 해석하면 안 된다.

제공 후보의 test 클래스별 지표:

| 클래스 | precision | recall | F1 | support |
|---|---:|---:|---:|---:|
| EXPENSE_REPORT | 1.0000 | 0.0213 | 0.0417 | 47 |
| PURCHASE_REQUEST | 0.5086 | 1.0000 | 0.6743 | 59 |
| TRAVEL_EXPENSE | 0.9722 | 0.6863 | 0.8046 | 51 |
| WELFARE_BENEFIT | 0.6667 | 0.7429 | 0.7027 | 35 |

Confusion matrix: 행은 정답, 열은 예측.

| 정답 / 예측 | EXPENSE_REPORT | PURCHASE_REQUEST | TRAVEL_EXPENSE | WELFARE_BENEFIT |
|---|---:|---:|---:|---:|
| EXPENSE_REPORT | 1 | 41 | 0 | 5 |
| PURCHASE_REQUEST | 0 | 59 | 0 | 0 |
| TRAVEL_EXPENSE | 0 | 8 | 35 | 8 |
| WELFARE_BENEFIT | 0 | 8 | 1 | 26 |

**EXPENSE_REPORT는 충분히 구별되지 않는다.** precision 100%는 단 1건을 예측한 결과다.
47건 중 41건을 PURCHASE_REQUEST로 보내며 recall은 2.13%다.

추론시간: 제공 후보 TF-IDF 변환 + LR predict_proba, warm CPU, batch=1, 300회.
평균 **0.825 ms**, p50 **0.685 ms**, p95 **1.469 ms**. 모델 파일은 **60,563 bytes**.
첫 sklearn import/모델 로드와 OCR·Gemma·DB 시간은 포함하지 않는다. 프로세스별 최초 1회 로드 후 캐시한다.

## Threshold와 추천

confidence는 LR의 최대 predict_proba이며 운영 정답률로 calibration된 값이 아니다.
탐색용 목표 precision 95%, 예측 클래스별 validation 수 최소 10건을 명시적으로 사용했다.
이는 임의의 confidence cutoff를 고정한 것이 아니라 조정 가능한 탐색 목표이며 통계적 보장이 아니다.
조건을 만족하는 후보 중 coverage가 가장 높은 낮은 threshold를 고른다. 만족하는 값이 없으면 null이다.

아래는 제공 후보의 **null REVIEW까지 포함한** coverage/선택 accuracy다.
validation 분모 216건, test 분모 211건이며 null 자동 선택은 오답이다.
JSON에는 null을 제외한 표, threshold별 클래스 precision 및 표본 수도 모두 있다.

| threshold | validation coverage | validation accuracy | test coverage | test accuracy |
|---|---:|---:|---:|---:|
| 0.00 | 100.00% | 56.02% | 100.00% | 57.35% |
| 0.40 | 86.11% | 57.53% | 91.94% | 62.37% |
| 0.50 | 68.98% | 62.42% | 74.88% | 66.46% |
| 0.60 | 54.17% | 73.50% | 65.88% | 69.06% |
| 0.70 | 42.59% | 77.17% | 53.55% | 72.57% |
| 0.80 | 25.93% | 83.93% | 38.86% | 85.37% |
| 0.85 | 16.20% | 85.71% | 32.70% | 91.30% |
| 0.90 | 6.48% | 92.86% | 28.44% | 95.00% |
| 0.95 | 2.31% | 100.00% | 9.95% | 100.00% |
| 0.99 | 0% | 해당 없음 | 0% | 해당 없음 |

**운영 추천 threshold는 현재 없음.** 0.95의 validation 100%도 5건뿐이라 최소 표본 조건을 충족하지 않는다.
카테고리 포함 후보는 네 클래스 모두 null이며 실제 자동 승인 coverage는 0%다.
카테고리 없는 모델의 PURCHASE_REQUEST 0.6은 validation 11건에서 100%였지만,
test 40건에서는 80%이고 null 오승인 4건이 있어 운영 추천으로 채택하지 않는다.
test 결과를 보고 cutoff를 다시 맞추지 않았다.

## 코드와 동작

수정/추가 파일:

- `backend/app/services/receipt_document_classifier.py`: feature allowlist, 모델 캐시, 확률·threshold, fallback/REVIEW.
- `backend/app/services/finance_receipt_simple.py`: `_normalize`에서 구조화된 카테고리·상호·품목을 전달하고 기존 검증 결과에 분류 검증을 합친다.
- `backend/scripts/train_receipt_document_classifier.py`: 입력 adapter, 감사, 그룹 split, 학습, 비교·평가·artifact 생성.
- `backend/data/document-classifier/model.joblib`: 검토용 학습 결과. 배포자가 관리하는 고정 경로만 로드한다.
- `backend/requirements.txt`: scikit-learn와 joblib 의존성.
- `backend/tests/test_receipt_document_classifier.py`: 정책, 실패, 실제 artifact, 실제 정규화와 단일 추출 호출 검증.
- 본 문서, 파이프라인 문서, 평가 JSON.

최종 처리 경로: OCR → Gemma 1회 → 기존 구조화·금액/품목 후처리 → document classifier
→ 추출 검증 + 분류 validator → DB → 사용자 확인 → Excel.
Gemma prompt는 document_type을 이미 요청하지 않았으므로 변경하지 않았다.
분류 서비스는 OCR 인자를 받지 않고 추가 LLM·네트워크 호출을 하지 않는다.

confidence가 클래스 threshold 이상이면 classifier 결과를 working document_type으로 선택한다.
낮은 confidence/검증 미달 클래스/모델 실패는 기존 category 매핑으로 fallback한다.
일반 category 매핑과 모델이 다르다는 이유만으로 모델 예측을 덮어쓰지 않는다.
이미 구조화된 `transaction_description`에 명시적인 출장/직원 복지 문맥이 있으면
강한 규칙 후보로 사용하고 모델과 충돌할 때 REVIEW로 보낸다. 현재 Gemma에는 이 필드를 추가 요청하지 않는다.
모호한 문맥, 부정·취소 문구는 이 규칙으로 확정하지 않는다. 이 소규모 규칙은 업무 정책의 완전한 대체물이 아니다.

`structured_data.classification_decision`에 예측, 선택, confidence, 네 확률, threshold,
fallback, 규칙 충돌, 모델 버전과 REVIEW 사유를 남긴다. 기존 추출 REVIEW 사유는 보존한다.
합성 artifact는 `DOCUMENT_CLASSIFIER_SYNTHETIC_VALIDATION_ONLY` 사유를 추가한다.
DB payload의 최상위 document_type과 구조화 결과를 동일하게 저장하며 기존 Excel writer는
최상위 document_type으로 네 양식을 선택한다. 기존 사용자 확인·Excel 코드와 DB 스키마 변경은 필요 없다.
`finance_taxonomy.py`의 기존 매핑/수동 검증은 삭제하지 않았다.

모델 누락·의존성 누락·불일치·추론 예외는 fallback + REVIEW다. artifact 교체 후 worker 재시작이 필요하다.
배포 전 backend requirements 설치가 필요하며 모델 파일은 Docker의 기존 COPY . . 경로에 포함된다.
현재 pipeline은 기존과 마찬가지로 사용자 확인 전 record status를 REVIEW로 저장한다.

## 재현 및 다음 데이터

backend 작업 디렉터리에서:

```powershell
python scripts/train_receipt_document_classifier.py --data C:/Users/CafeAlle/Desktop/my/archive/expense_classification_clean_v4.jsonl
python -m unittest discover -s tests -p test_receipt_document_classifier.py -v
```

전체 서버 통합 환경 없이 전용 가상환경에서 classifier와 관련 순수 회귀 테스트를 실행했다.
추출/정규화 연결 테스트는 실제 함수 AST를 실행하고 외부 서버 의존성만 대체한다.
실제 Ollama·DB 연결을 사용한 end-to-end 검증은 아니다.

자동 분류를 활성화하려면 **실제 Gemma 출력**의 expense_category, merchant, items[].name과
독립적으로 사용자 확인된 document_type을 쌍으로 모아야 한다. 짧은 거래 목적을 이미 추출/입력하는
경로가 없다면 그 목적은 분류기에 없는 정보라는 점을 유지한다. 같은 상호·품목의 도서/식사/교통에서
문서 유형이 달라지는 사례와 EXPENSE_REPORT 사례를 포함하고, 확인 불가 사례도 남긴다.
새 운영 데이터로 그룹/시간 분리 평가와 threshold별 precision·coverage를 다시 측정한 뒤 artifact를 교체한다.
현재 artifact의 production_validated만 true로 바꾸거나 threshold를 임의로 낮추는 것은 권장하지 않는다.
