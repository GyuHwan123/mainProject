-- 영수증 기록 보관함
-- finance_records의 영수증 분석 결과와 Storage 원본을 사용자별로 조회하기 위한 테이블입니다.

create table if not exists public.receipt_archive (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  document_id uuid not null references public.ocr_documents(id) on delete cascade,
  finance_record_id uuid not null references public.finance_records(id) on delete cascade,
  source_file_name text not null,
  source_storage_path text not null,
  receipt_fingerprint text,
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

-- OCR 본문으로 만든 지문을 보관하여 같은 이미지가 다시 분석되어도
-- 보관함에는 한 번만 저장되게 합니다.
alter table public.receipt_archive
  add column if not exists receipt_fingerprint text;

update public.receipt_archive archive
set receipt_fingerprint = coalesce(
  record.structured_data ->> 'receipt_identity_key',
  record.structured_data ->> 'receipt_fingerprint'
)
from public.finance_records record
where record.id = archive.finance_record_id
  and archive.receipt_fingerprint is null;

-- 기존 중복은 최초 보관 건만 남깁니다. OCR 문서와 재무 기록은 삭제하지 않습니다.
with ranked_archive as (
  select
    id,
    row_number() over (
      partition by user_id, receipt_fingerprint
      order by created_at asc, id asc
    ) as duplicate_rank
  from public.receipt_archive
  where receipt_fingerprint is not null
)
delete from public.receipt_archive archive
using ranked_archive ranked
where archive.id = ranked.id
  and ranked.duplicate_rank > 1;

create unique index if not exists receipt_archive_user_fingerprint_unique_idx
  on public.receipt_archive (user_id, receipt_fingerprint);

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
