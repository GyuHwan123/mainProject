-- Persist the exact weighted score shown immediately after receipt evaluation.
-- Apply this migration before deploying the matching backend changes.

begin;

alter table public.finance_record_evaluations
  add column if not exists selection_rubric jsonb,
  add column if not exists score_version text,
  add column if not exists extraction_score_95 double precision,
  add column if not exists json_schema_rate double precision,
  add column if not exists total_amount_correct boolean,
  add column if not exists hallucination_count integer;

alter table public.finance_record_evaluations
  drop constraint if exists finance_record_evaluations_selection_rubric_object;

alter table public.finance_record_evaluations
  add constraint finance_record_evaluations_selection_rubric_object
  check (selection_rubric is null or jsonb_typeof(selection_rubric) = 'object');

alter table public.finance_record_evaluations
  drop constraint if exists finance_record_evaluations_extraction_score_range;

alter table public.finance_record_evaluations
  add constraint finance_record_evaluations_extraction_score_range
  check (extraction_score_95 is null or extraction_score_95 between 0 and 95);

alter table public.finance_record_evaluations
  drop constraint if exists finance_record_evaluations_json_schema_rate_range;

alter table public.finance_record_evaluations
  add constraint finance_record_evaluations_json_schema_rate_range
  check (json_schema_rate is null or json_schema_rate between 0 and 1);

alter table public.finance_record_evaluations
  drop constraint if exists finance_record_evaluations_hallucination_count_nonnegative;

alter table public.finance_record_evaluations
  add constraint finance_record_evaluations_hallucination_count_nonnegative
  check (hallucination_count is null or hallucination_count >= 0);

comment on column public.finance_record_evaluations.selection_rubric is
  'Immutable weighted scoring snapshot produced at evaluation time. DB replay must prefer this over recalculation.';

comment on column public.finance_record_evaluations.score_version is
  'Version of the scoring rubric, independent from the receipt pipeline version.';

comment on column public.finance_record_evaluations.json_schema_rate is
  'Required JSON-key conformance rate. This is not workbook generation success.';

commit;

-- Existing rows intentionally remain null: their original live rubric was not
-- persisted and cannot be reconstructed exactly in SQL. The backend uses a
-- compatibility replay only for those legacy rows.
select
  count(*) as total_evaluations,
  count(*) filter (where selection_rubric is not null) as snapshotted_evaluations,
  count(*) filter (where selection_rubric is null) as legacy_evaluations
from public.finance_record_evaluations;
