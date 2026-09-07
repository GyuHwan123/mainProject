# RAG 평가 실행 이력

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

기간별 모니터링은 향후 `evaluated_at` 기준으로 연결합니다. 날짜/모델 인덱스는
준비했지만, 그래프·목록 조회 API·기존 최신 결과 조회 경로는 이번에 변경하지 않았습니다.
