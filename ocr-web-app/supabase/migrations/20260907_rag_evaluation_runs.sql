-- Review before applying. Completed RAG runs only; case details stay in checkpoints.
begin;

create table public.rag_evaluation_runs (
  id uuid primary key,
  user_id uuid not null references public.users(id) on delete cascade,
  dataset_name text not null,
  dataset_hash text not null check (dataset_hash ~ '^[0-9a-f]{64}$'),
  question_count integer not null check (question_count > 0),
  completed_count integer not null check (completed_count = question_count),
  started_at timestamptz not null,
  evaluated_at timestamptz not null,
  created_at timestamptz not null default now(),
  model_name text not null,
  prompt_version text not null,
  configuration jsonb not null check (jsonb_typeof(configuration) = 'object'),
  evaluator_version text not null,
  summary_metrics jsonb not null check (jsonb_typeof(summary_metrics) = 'object'),
  average_latency_ms double precision check (average_latency_ms >= 0 and average_latency_ms < 'Infinity'::float8),
  answer_accuracy double precision check (answer_accuracy between 0 and 1),
  faithfulness double precision check (faithfulness between 0 and 1),
  hit_at_1 double precision check (hit_at_1 between 0 and 1),
  hit_at_k double precision check (hit_at_k between 0 and 1),
  hit_at_3 double precision check (hit_at_3 between 0 and 1),
  hit_at_5 double precision check (hit_at_5 between 0 and 1),
  recall_at_k double precision check (recall_at_k between 0 and 1),
  mrr double precision check (mrr between 0 and 1),
  ndcg_at_k double precision check (ndcg_at_k between 0 and 1),
  context_precision double precision check (context_precision between 0 and 1),
  hallucination_rate double precision check (hallucination_rate between 0 and 1),
  unanswerable_rejection_rate double precision check (unanswerable_rejection_rate between 0 and 1)
);

create index rag_evaluation_runs_user_evaluated_idx
  on public.rag_evaluation_runs (user_id, evaluated_at desc);
create index rag_evaluation_runs_user_model_evaluated_idx
  on public.rag_evaluation_runs (user_id, model_name, evaluated_at desc);

-- Custom application auth resolves public.users on the backend.
-- Do not expose direct browser writes or rely on auth.uid() matching that identity.
alter table public.rag_evaluation_runs enable row level security;
revoke all on public.rag_evaluation_runs from anon, authenticated, service_role;
grant select, insert on public.rag_evaluation_runs to service_role;

comment on table public.rag_evaluation_runs is
  'Immutable completed RAG runs. Checkpoint UUID is the idempotency key; no per-question payload is stored.';

commit;
