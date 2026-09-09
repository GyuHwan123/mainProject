-- Additive migration for existing rag_evaluation_runs table.
-- Keeps all current rows intact, including demo and real evaluation history.
-- This does not create or recreate the table and does not touch OCR/Finance tables.

BEGIN;

ALTER TABLE public.rag_evaluation_runs
  ADD COLUMN IF NOT EXISTS run_id uuid;

-- Backfill existing rows without deleting or rewriting history.
UPDATE public.rag_evaluation_runs
   SET run_id = id
 WHERE run_id IS NULL;

-- Resolve any accidental duplicates before enforcing the unique constraint.
WITH ranked AS (
  SELECT id,
         run_id,
         row_number() OVER (
           PARTITION BY run_id
           ORDER BY id
         ) AS rn
  FROM public.rag_evaluation_runs
  WHERE run_id IS NOT NULL
)
UPDATE public.rag_evaluation_runs r
   SET run_id = gen_random_uuid()
  FROM ranked
 WHERE r.id = ranked.id
   AND ranked.rn > 1;

-- Enforce data integrity only after the backfill is complete.
ALTER TABLE public.rag_evaluation_runs
  ALTER COLUMN run_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS rag_evaluation_runs_run_id_key
  ON public.rag_evaluation_runs (run_id);

COMMIT;
