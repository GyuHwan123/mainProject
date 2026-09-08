# RAG 평가 실행 이력

## 고정 DEMO baseline과 실제 이력

- 최근 실행 목록은 로그인 사용자 본인의 실제 DB 이력을 날짜/50건 제한 없이 최신순으로 표시합니다. 새 평가가 저장되면 다음 조회/새로고침에 포함됩니다. DEMO가 실제 행을 밀어내지 않습니다.
- 그래프/KPI는 선택 기간의 실제 완료 실행을 기존 `summarize_runs()`로 집계합니다. 한국 시간 최초 실제 평가일 이전 30일만 고정 baseline으로 보완합니다. 실제 실험 시작 후 빈 날짜는 DEMO로 채우지 않습니다.
- `recent_runs`는 실제 이력 전용, `baseline_runs`는 문항 유형/시연 분포 카드용 별도 데이터입니다. 실제 상세 결과가 있으면 기존 문항 분석은 그대로 사용할 수 있습니다.
- 고정 식별자는 `rag-monitoring-baseline`입니다. 프론트의 `VITE_RAG_DEMO_BATCH_ID`와 요청의 배치 선택은 더 이상 사용하지 않습니다. 예전 v1/v2/v3 DEMO는 DB에 그대로 두되 baseline으로 조회하지 않습니다.
- `latest actual`은 DEMO를 제외합니다. 조회 중 데이터 생성/INSERT/UPDATE/DELETE는 없습니다. 실제 행, checkpoint, 평가 실행 로직은 변경하지 않습니다.
- 실제 이력이 없으면 baseline도 생성/표시하지 않습니다. 새 평가가 추가돼도 baseline 날짜나 UUID를 이동시키지 않습니다. 기본 최근 7일에 baseline 기간이 포함되지 않으면 실제 데이터만 표시되는 것이 정상이며, 기간을 넓히면 과거 baseline을 볼 수 있습니다.

## 일회성 baseline 준비

이번 구조 변경에서는 DB 쓰기나 seed 실행을 하지 않았습니다. baseline이 아직 DB에 없으면 기존 실제 데이터만 표시됩니다.

백엔드 환경에서 아래 명령은 최초 실제 평가를 SELECT하여 그 전날까지 30일의 검토용 JSON만 만듭니다. 이메일은 대상 개발자 계정으로 지정합니다. 배치 ID와 종료일을 입력하지 않습니다.

```powershell
python scripts/seed_rag_monitoring_demo.py prepare --email developer@docunex.com --output rag-baseline.json
```

별도 저장 명령(이번 작업에서 미실행):

```powershell
python scripts/seed_rag_monitoring_demo.py insert --input rag-baseline.json
```

UUID는 사용자와 고정 baseline 슬롯으로 결정됩니다. 최초 실제값을 기준으로 합성 추이를 준비하고 누락된 실제 지표는 null로 둡니다. 문항 유형은 `question_type/count/answer_accuracy` 집계만, 응답 분포는 명시적 시연 예시만 저장합니다. 상세 문항은 저장하지 않습니다.

동일 JSON의 재실행은 `ignore-duplicates`로 기존 행을 유지합니다. baseline이 이미 있으면 재준비를 거부하고 기존 파일을 재사용합니다. 기존 UUID의 날짜/값이 다르면 INSERT를 거부하며 새 배치 전환이나 자동 덮어쓰기를 하지 않습니다.

## 적용 상태

구현 및 모킹 테스트만 완료했습니다. 실제 Supabase에는 적용하지 않았습니다.
검토할 SQL: [20260907_rag_evaluation_runs.sql](../supabase/migrations/20260907_rag_evaluation_runs.sql)

## 저장 범위

- `rag_evaluation_runs`에 실행당 한 행을 저장합니다.
- 실행 사용자, 최초 시작/완료 시각, 데이터셋명/해시, 문항 수, 모델/설정,
  `summary_metrics`, 주요 지표, 기존 `latency.total.average_ms`를 저장합니다.
- `result_snapshot` 컬럼은 없습니다. 질문·정답·응답·근거 등 문항별 상세 결과는
  기존 checkpoint 파일에만 남습니다. 전체 200문항 결과를 DB로 보내지 않습니다.
- DB에는 진행 중 또는 일부 실패한 실행을 넣지 않습니다. 전체 문항 ID가 완료
  결과와 일치하고, 오류가 없으며 문항 수가 일치해야 합니다.

## 중복 방지와 재개

새 checkpoint에 `history.id`와 실행 메타데이터를 추가합니다. 기존 checkpoint
경로·해시·재개 조건·문항 재시도·계산 함수는 유지합니다.

같은 checkpoint를 재개하면 실행 ID를 유지합니다. 기존 loader가 설정 변경 등으로
새 checkpoint를 만들면 새 ID가 생깁니다. 데이터셋 해시만으로 중복 처리하지 않습니다.

완료 시 compact payload를 checkpoint에 먼저 고정하고, DB의 UUID 기본키와
`on_conflict=id`, `resolution=ignore-duplicates`로 중복 삽입을 방지합니다.
저장 재시도 시 완료 시각이나 최초 저장 지표를 덮어쓰지 않습니다.

## 저장 실패와 재시도 API

DB가 없거나 저장이 실패해도 평가 결과와 완료 checkpoint를 유지합니다.
평가 응답의 `history.status` 및 진행 상태의 `history`로 저장 상태를 확인할 수 있습니다.

- `waiting`: 아직 저장 가능한 전체 완료 결과가 아님
- `pending`: 완료 결과는 있으나 DB 저장 대기
- `saved`: 이력 저장 완료
- `skipped`: 출처를 확정할 수 없는 기존 checkpoint 또는 실행 중 메타데이터 불일치

DB 적용/연결 복구 후 아래 API에 **동일한 평가 데이터셋 JSON**을 보냅니다.

```http
POST /api/v1/rag/evaluate/history/retry
Authorization: Bearer <개발자 로그인 토큰>
Content-Type: application/json
```

body는 기존 `/rag/evaluate`와 같은 `dataset_name`, `question_count`, `cases`입니다.
본인 checkpoint만 허용하며, 실행 중이면 409를 반환합니다. 이 API는 OCR, 검색,
LLM, 문항 채점 없이 고정된 DB payload 저장만 재시도합니다.
별도 재시도 버튼이나 자동 백그라운드 재시도는 이번 범위에 포함하지 않았습니다.

재시도는 해당 로컬 checkpoint가 남아 있어야 합니다. 기존과 같이 동일 데이터셋의
checkpoint가 새 설정으로 교체되거나 삭제되면 이전 저장 대기 결과를 복구할 수 없습니다.
따라서 `pending` 실행은 checkpoint 교체 전에 저장 재시도를 완료해야 합니다.

기존 checkpoint에 실행 사용자·모델 메타데이터가 없으면 재개는 허용하지만
이력을 현재 사용자/모델의 결과로 추정해 저장하지 않습니다. 자동 소급 저장도 하지 않습니다.

## 권한과 후속 작업

테이블은 RLS를 활성화하고 `anon`/`authenticated`의 직접 접근을 차단합니다.
백엔드가 인증된 사용자 이메일을 `public.users.id`로 조회해 저장하며,
`service_role`에는 SELECT/INSERT만 부여합니다.

기간별 모니터링은 `GET /api/v1/rag/evaluation/monitoring?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
로 조회합니다. 로그인 사용자 소유의 실행을 한국 시간의 `evaluated_at` 기준으로 조회하며,
종료일을 포함합니다. 최대 조회 기간은 366일입니다.

`summary`와 `daily`의 비율 지표는 저장된 실행별 점수의 산술평균이며, 문항 수는 합계입니다.
실행마다 평가 대상 분모가 다를 수 있으므로 문항 수로 점수를 재가중하지 않습니다.
이력이 없는 날의 비율은 null이며 그래프에서 해당 구간을 연결하지 않습니다.
`recent_runs`는 최신 50회이고, 요약/그래프는 페이지를 순회해 조회한 전체 기간 이력을 사용합니다.
RAG 리포트의 기간 선택·KPI·sparkline·성능 그래프·새로고침·최근 이력이 이 API에 연결됩니다.
기존 평가 및 최신 결과 API는 유지합니다. 문항 상세는 동일 실행 ID의 로컬/최신 결과가
있을 때만 표시하며, 과거 문항 상세를 DB에서 복구하지 않습니다.
