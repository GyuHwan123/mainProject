-- 영수증 기록 보관함
-- finance_records의 영수증 분석 결과와 Storage 원본을 사용자별로 조회하기 위한 테이블입니다.

create table if not exists public.receipt_archive (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  document_id uuid not null references public.ocr_documents(id) on delete cascade,
  finance_record_id uuid not null references public.finance_records(id) on delete cascade,
  source_file_name text not null,
  source_storage_path text not null,
  expense_category text,
  merchant text,
  transaction_date date,
  total_amount numeric(18, 2) not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (finance_record_id)
);

-- 이전 버전의 보관함 테이블을 이미 만든 환경도 원본 Storage 경로를 추가합니다.
alter table public.receipt_archive
  add column if not exists source_storage_path text;

create index if not exists receipt_archive_user_created_idx
  on public.receipt_archive (user_id, created_at desc);
create index if not exists receipt_archive_user_category_idx
  on public.receipt_archive (user_id, expense_category);
alter table public.receipt_archive enable row level security;

drop policy if exists "receipt_archive_select_own" on public.receipt_archive;
create policy "receipt_archive_select_own"
  on public.receipt_archive for select
  using (user_id = auth.uid());

drop policy if exists "receipt_archive_insert_own" on public.receipt_archive;
create policy "receipt_archive_insert_own"
  on public.receipt_archive for insert
  with check (user_id = auth.uid());

drop policy if exists "receipt_archive_update_own" on public.receipt_archive;
create policy "receipt_archive_update_own"
  on public.receipt_archive for update
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

drop policy if exists "receipt_archive_delete_own" on public.receipt_archive;
create policy "receipt_archive_delete_own"
  on public.receipt_archive for delete
  using (user_id = auth.uid());
