-- Preserve raw LLM outputs and validator trace alongside each receipt evaluation.
-- Apply this migration before deploying the backend that writes pipeline_trace.

begin;

alter table public.finance_record_evaluations
  add column if not exists pipeline_trace jsonb not null default '{}'::jsonb,
  add column if not exists error_analysis jsonb not null default '{}'::jsonb,
  add column if not exists error_tags jsonb not null default '[]'::jsonb,
  add column if not exists analysis_version text,
  add column if not exists needs_review boolean not null default false;

alter table public.finance_record_evaluations
  drop constraint if exists finance_record_evaluations_pipeline_trace_object;

alter table public.finance_record_evaluations
  add constraint finance_record_evaluations_pipeline_trace_object
  check (jsonb_typeof(pipeline_trace) = 'object');

alter table public.finance_record_evaluations
  drop constraint if exists finance_record_evaluations_error_analysis_object;

alter table public.finance_record_evaluations
  add constraint finance_record_evaluations_error_analysis_object
  check (jsonb_typeof(error_analysis) = 'object');

alter table public.finance_record_evaluations
  drop constraint if exists finance_record_evaluations_error_tags_array;

alter table public.finance_record_evaluations
  add constraint finance_record_evaluations_error_tags_array
  check (jsonb_typeof(error_tags) = 'array');

comment on column public.finance_record_evaluations.pipeline_trace is
  'Raw summary/items LLM responses, deterministic candidates, and pre/post validator snapshots.';

comment on column public.finance_record_evaluations.error_tags is
  'Multi-label OCR, candidate, LLM, and validation error attribution.';

commit;
