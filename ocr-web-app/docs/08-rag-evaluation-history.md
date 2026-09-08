# RAG 평가 실행 이력

## 개발자 모니터링 시연

개발자 또는 관리자 계정으로 개발자 모드의 RAG 종합 리포트를 열면 기본으로 최근 7일의 연속 메모리 시연 이력을 표시합니다. 별도 URL 파라미터 입력은 필요하지 않습니다. 메모리 생성 범위는 30일로 유지하여 기간 선택을 확장할 수 있습니다.

`/reports?view=developer&developerReport=rag&ragReportTab=overview`

API는 `GET /api/v1/rag/evaluation/monitoring?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&demo=true`입니다.
화면이 자동으로 `demo=true`를 전달합니다. API 자체의 기본값은 `demo=false`이며 기존 DB 이력 조회 동작을 유지합니다. 서버의 개발자 권한 검사는 시연에도 동일하게 적용합니다. 일반 사용자 화면에는 적용하지 않습니다.

- 한국 시간 오늘까지 최근 30일의 연속 데이터를 생성하고, 요청 기간으로 필터링합니다.
- 시연 응답에는 더미 행만 집계합니다. 실제 행과 섞어 실제 기간 평균을 변경하지 않습니다.
- 사용자 본인의 최신 DB 평가를 읽기 전용으로 조회하고 마지막 시연 날짜의 기준으로 사용합니다.
  원본 완료일은 `configuration.demo_anchor_evaluated_at`, 원본 ID는 `demo_anchor_id`에 기록합니다.
- 실제 평가가 있으면 누락된 지표는 `null`로 유지합니다. 실제 이력이 전혀 없으면 그래프의 5개 지표만 합성 기본값을 사용합니다.
  DB 조회 실패는 기존 오류 처리로 전달하며 실제 이력이 없는 것으로 간주하지 않습니다.
- 행 ID는 `demo-rag-날짜`, 데이터셋명은 `[DEMO]` 접두사, 평가기 버전은 `rag-monitoring-demo-v1`로 구분합니다.
- 기존 `summarize_runs()`와 차트를 그대로 사용합니다. DB 저장, checkpoint 접근, 평가 실행은 하지 않습니다.
- `ragDemo` URL 파라미터는 사용하지 않습니다. 실제 이력은 개발자 권한으로 API에 `demo=false`를 전달하여 조회할 수 있습니다. 시연 옵션은 저장하지 않습니다.
- 응답 유형 도넛은 시연 모드에서 별도의 100문항 예시(정답 76, 오답 9, 답변 거절 12, 판정 정보 없음 3)를 표시하며 실제 지표에서 역산하지 않습니다. 실제 모드에서는 실행 ID가 일치하는 최신 문항 결과의 `rejected`와 `answer_correct`만 집계하고, 문항 결과가 없으면 빈 상태를 표시합니다. 답변 거절 여부를 먼저 분류하므로 유형은 서로 중복되지 않습니다.

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
