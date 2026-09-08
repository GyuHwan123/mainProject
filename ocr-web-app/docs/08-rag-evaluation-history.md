# RAG 평가 실행 이력

## 개발자 모니터링 시연

개발자 또는 관리자 계정의 RAG 종합 리포트는 최근 7일의 DB 시연 배치를 조회합니다. 요청 중 메모리 데이터 생성은 하지 않습니다. 이번 전환에서는 seed 도구만 구현했으며 DB INSERT와 테스트/빌드는 실행하지 않았습니다. 저장 전에는 빈 화면이 정상입니다.

`/reports?view=developer&developerReport=rag&ragReportTab=overview`

API는 `GET /api/v1/rag/evaluation/monitoring?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&demo=true&demo_batch_id=rag-demo-v1`입니다.
화면은 `demo=true`와 배치 ID를 자동 전달합니다. 기본 배치는 `rag-demo-v1`이며 프론트 환경변수 `VITE_RAG_DEMO_BATCH_ID`로 변경할 수 있습니다. 서버는 개발자 권한과 사용자 소유 범위 및 배치를 확인합니다. `demo=false` 조회와 최신 실제값 조회는 `configuration.demo=true` 행을 제외하고, demo 필드가 없는 기존 실제 행을 포함합니다.

- `rag_evaluation_demo.py`는 seed 전용 순수 payload 생성기입니다. 종료일을 명시하여 한국 시간 연속 7일의 7건을 준비합니다. 날짜는 저장 후 자동 이동하지 않습니다.
- 사용자 본인의 최신 실제 DB 이력을 기준으로 마지막 값을 고정합니다. 누락 지표는 null이며, 실제 이력이 전혀 없을 때만 그래프 5개 지표에 합성 기본값을 사용합니다. DB 조회 오류는 무시하지 않습니다.
- 사용자 UUID/배치 ID/7개 슬롯 기준 UUIDv5를 사용합니다. `[DEMO]` 데이터셋명, `configuration.demo=true`, `demo_batch_id`, 실제 기준 실행 정보, `evaluator_version=rag-monitoring-demo-v1`을 기록합니다.
- `hit_at_4`는 DB 최상위 컬럼이 없어 `summary_metrics`에만 저장합니다. 다른 지표는 최상위 컬럼과 요약 JSON을 일치시킵니다.
- 도넛은 최신 선택 DB 행의 `summary_metrics.response_distribution`에 저장된 `correct`/`incorrect`/`rejected`/`unknown`을 읽습니다. Seed는 기존 별도 100문항 예시 76/9/12/3과 예시 표식을 저장합니다. 실제 지표에서 역산하지 않으며, 실제 행에 분포가 없으면 빈 상태를 표시합니다.
- 기존 `summarize_runs()`와 UI 디자인을 유지합니다. 실제 평가 저장 함수, 평가 실행, checkpoint는 변경하지 않습니다.

### Seed 준비 및 저장

백엔드 환경(의존성 및 Supabase 설정이 있는 `ocr-web-app/backend`)에서 실행합니다. 이메일은 실제 개발자/관리자 계정으로 바꾸고 종료일은 시연 날짜로 지정합니다.

```powershell
python scripts/seed_rag_monitoring_demo.py prepare --email developer@docunex.com --batch-id rag-demo-v1 --end-date 2026-09-08 --output rag-demo-v1.json
```

`prepare`는 사용자와 최신 실제값을 SELECT하고 검토용 JSON 파일만 만듭니다. 기존 파일은 덮어쓰지 않습니다. 생성 파일에 대상 사용자, 배치, 기준 실제값과 7건의 payload가 포함됩니다.

아래는 검토한 파일을 실제 저장하는 별도 명령이며 이번 작업에서는 실행하지 않았습니다.

```powershell
python scripts/seed_rag_monitoring_demo.py insert --input rag-demo-v1.json
```

`insert`는 소유자/생성 규칙/기존 UUID의 데이터를 확인한 후 7건을 단일 POST로 INSERT합니다. `on_conflict=id`와 `resolution=ignore-duplicates`로 기존 UUID는 변경하지 않습니다. 같은 JSON 재실행은 중복을 만들지 않습니다. 같은 배치를 다른 날짜나 값으로 재사용하면 거부하므로 새 배치 ID와 새 파일을 준비하고 화면의 환경변수도 맞춰야 합니다. UPDATE/DELETE 및 checkpoint 접근은 없습니다.

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
