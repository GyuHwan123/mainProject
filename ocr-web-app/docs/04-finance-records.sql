-- Run once in the Supabase SQL Editor before using the finance receipt page.
-- Keep this helper here so this migration can also run independently of 02-supabase-schema.sql.
create or replace function public.current_app_user_id() returns uuid
language sql stable security definer set search_path = public as $$
  select id from public.users
  where id = auth.uid() or email = auth.jwt() ->> 'email'
  order by (id = auth.uid()) desc
  limit 1;
$$;

create table if not exists public.finance_records (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  document_id uuid not null unique references public.ocr_documents(id) on delete cascade,
  document_type text check (document_type in ('EXPENSE_REPORT', 'TRAVEL_EXPENSE', 'PURCHASE_REQUEST', 'WELFARE_BENEFIT')),
  expense_category text check (expense_category in ('교통비', '도서인쇄비', '복리후생비(간식)', '복리후생비(식대)', '비품비', '소모품비', '여비교통비', '운반비', '인쇄비', '지급수수료', '차량유지비', '출장숙박비', '출장식비', '통신비', '회의비')),
  merchant text,
  transaction_date date,
  supply_amount numeric(18,2) not null default 0 check (supply_amount >= 0),
  tax_amount numeric(18,2) not null default 0 check (tax_amount >= 0),
  total_amount numeric(18,2) not null default 0 check (total_amount >= 0),
  payment_method text,
  description text,
  structured_data jsonb not null default '{}'::jsonb,
  model_name text not null,
  status text not null default 'REVIEW' check (status in ('REVIEW', 'CONFIRMED')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_finance_records_user_type_created
  on public.finance_records(user_id, document_type, created_at desc);

alter table public.finance_records enable row level security;

drop policy if exists finance_records_own on public.finance_records;
create policy finance_records_own on public.finance_records for all
  using (user_id = public.current_app_user_id())
  with check (user_id = public.current_app_user_id());

comment on table public.finance_records is
  'LLM-classified receipt records used for four-sheet finance workbook exports';
