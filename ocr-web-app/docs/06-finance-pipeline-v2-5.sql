-- Promote new receipt evaluations to finance pipeline v2.5.
-- Run once in the Supabase SQL editor after 04-finance-pipeline-version.sql.
-- This migration is idempotent and deliberately preserves historical versions.

begin;

alter table public.finance_record_evaluations
  add column if not exists pipeline_version text;

-- Only repair rows that have no usable version. Existing v1.0/v2.0 rows were
-- produced by older pipelines and must retain their original provenance.
update public.finance_record_evaluations
set pipeline_version = 'v2.5'
where pipeline_version is null or btrim(pipeline_version) = '';

alter table public.finance_record_evaluations
  alter column pipeline_version set default 'v2.5',
  alter column pipeline_version set not null;

comment on column public.finance_record_evaluations.pipeline_version is
  'Version of the complete receipt-processing pipeline used for this evaluation. Current production version: v2.5.';

commit;

-- Verification: default_version must be v2.5 and missing_version must be 0.
select
  column_default as default_version,
  is_nullable
from information_schema.columns
where table_schema = 'public'
  and table_name = 'finance_record_evaluations'
  and column_name = 'pipeline_version';

select
  count(*) as total_evaluations,
  count(*) filter (where pipeline_version = 'v1.0') as pipeline_v1_0,
  count(*) filter (where pipeline_version = 'v2.0') as pipeline_v2_0,
  count(*) filter (where pipeline_version = 'v2.5') as pipeline_v2_5,
  count(*) filter (where pipeline_version is null or btrim(pipeline_version) = '') as missing_version
from public.finance_record_evaluations;
