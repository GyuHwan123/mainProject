-- Add a dedicated version column for the receipt-processing pipeline.
-- Run this once in the Supabase SQL editor after the finance evaluation tables exist.

begin;

alter table public.finance_record_evaluations
  add column if not exists pipeline_version text;

-- Preserve existing evaluation versions. Rows created before explicit version
-- metadata remain v1.0; new semantic-line pipeline evaluations are v2.0.
update public.finance_record_evaluations
set pipeline_version = coalesce(
  nullif(btrim(pipeline_version), ''),
  nullif(btrim(pipeline_trace ->> 'version'), ''),
  'v1.0'
);

-- The dedicated column is now the sole source of truth.
update public.finance_record_evaluations
set pipeline_trace = coalesce(pipeline_trace, '{}'::jsonb) - 'version'
where coalesce(pipeline_trace, '{}'::jsonb) ? 'version';

alter table public.finance_record_evaluations
  alter column pipeline_version set default 'v2.0',
  alter column pipeline_version set not null;

comment on column public.finance_record_evaluations.pipeline_version is
  'Version of the complete receipt-processing pipeline used for this evaluation.';

commit;

-- Verification: both missing_version and trace_version_remaining must be 0.
select
  count(*) as total_evaluations,
  count(*) filter (where pipeline_version = 'v1.0') as pipeline_v1_0,
  count(*) filter (where pipeline_version = 'v2.0') as pipeline_v2_0,
  count(*) filter (where pipeline_version is null or btrim(pipeline_version) = '') as missing_version,
  count(*) filter (where coalesce(pipeline_trace, '{}'::jsonb) ? 'version') as trace_version_remaining
from public.finance_record_evaluations;
