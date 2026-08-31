-- DOCUNEX Supabase schema v2026.08
-- Idempotent upgrade for the current OCR, RAG, chat, scrapbook, report and subscription code.
-- Prerequisite: run 01-supabase-enums.sql in a separate SQL Editor execution.

create extension if not exists vector;

-- ---------------------------------------------------------------------------
-- Users and authentication metadata
-- ---------------------------------------------------------------------------
create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  name varchar(100),
  password_hash varchar(255),
  social_provider varchar(30) not null default 'local',
  social_id varchar(255),
  role text not null default 'USER',
  subscription_tier text not null default 'PERSONAL',
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.users
  add column if not exists name varchar(100),
  add column if not exists password_hash varchar(255),
  add column if not exists is_active boolean not null default true,
  add column if not exists subscription_tier text not null default 'PERSONAL',
  add column if not exists updated_at timestamptz not null default now();

update public.users set name = coalesce(nullif(name, ''), email) where name is null or name = '';
alter table public.users alter column name set not null;
alter table public.users drop constraint if exists chk_users_social_provider;
alter table public.users add constraint chk_users_social_provider
  check (social_provider in ('local', 'google', 'github', 'apple', 'kakao', 'supabase'));
alter table public.users drop constraint if exists users_subscription_tier_check;
alter table public.users add constraint users_subscription_tier_check
  check (subscription_tier in ('PERSONAL', 'ENTERPRISE'));

-- ---------------------------------------------------------------------------
-- OCR documents and evaluation
-- ---------------------------------------------------------------------------
create table if not exists public.ocr_documents (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  group_id uuid,
  file_name text not null,
  file_url text not null,
  extracted_text text,
  summary_text text,
  translated_text text,
  bounding_boxes jsonb not null default '[]'::jsonb,
  status text not null default 'COMPLETED',
  upload_origin text not null default 'OCR',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.ocr_documents add column if not exists upload_origin text;
update public.ocr_documents set upload_origin = 'OCR' where upload_origin is null;
alter table public.ocr_documents alter column upload_origin set default 'OCR';
alter table public.ocr_documents alter column upload_origin set not null;
alter table public.ocr_documents drop constraint if exists chk_ocr_documents_upload_origin;
alter table public.ocr_documents add constraint chk_ocr_documents_upload_origin check (upload_origin in ('OCR', 'RAG'));
create index if not exists idx_ocr_documents_user_origin_created
  on public.ocr_documents(user_id, upload_origin, created_at desc);

create table if not exists public.ocr_evaluations (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.ocr_documents(id) on delete cascade,
  confidence_score double precision not null default 0,
  processing_time_ms integer not null default 0,
  cer_score double precision not null default 0,
  precision_score double precision not null default 0,
  recall_score double precision not null default 0,
  evaluated_at timestamptz not null default now()
);
create index if not exists idx_ocr_evaluations_document_time
  on public.ocr_evaluations(document_id, evaluated_at desc);

-- ---------------------------------------------------------------------------
-- Finance receipt classification and export history
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- RAG vector index
-- ---------------------------------------------------------------------------
create table if not exists public.rag_documents (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  document_id uuid not null references public.ocr_documents(id) on delete cascade,
  status text not null default 'INDEXING' check (status in ('INDEXING', 'RAG_READY', 'FAILED')),
  embedding_model text not null default 'embeddinggemma',
  chunk_count integer not null default 0 check (chunk_count >= 0),
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(user_id, document_id)
);

create table if not exists public.rag_chunks (
  id uuid primary key default gen_random_uuid(),
  rag_document_id uuid not null references public.rag_documents(id) on delete cascade,
  document_id uuid not null references public.ocr_documents(id) on delete cascade,
  user_id uuid not null references public.users(id) on delete cascade,
  chunk_index integer not null check (chunk_index >= 0),
  page_number integer not null check (page_number > 0),
  content text not null check (length(btrim(content)) > 0),
  bbox jsonb,
  embedding vector(768) not null,
  created_at timestamptz not null default now(),
  unique(rag_document_id, chunk_index)
);
update public.ocr_documents document set upload_origin = 'RAG'
where exists (select 1 from public.rag_documents rag where rag.document_id = document.id);
create index if not exists idx_rag_documents_user_status on public.rag_documents(user_id, status, updated_at desc);
create index if not exists idx_rag_chunks_document_page on public.rag_chunks(document_id, page_number, chunk_index);

create or replace function public.match_rag_chunks(
  query_embedding vector(768), filter_user_id uuid,
  filter_rag_document_id uuid default null, match_threshold float default 0.35,
  match_count integer default 5
) returns table (
  id uuid, rag_document_id uuid, document_id uuid, chunk_index integer,
  page_number integer, content text, bbox jsonb, similarity float
) language sql stable as $$
  select chunk.id, chunk.rag_document_id, chunk.document_id, chunk.chunk_index,
    chunk.page_number, chunk.content, chunk.bbox,
    (1 - (chunk.embedding <=> query_embedding))::float
  from public.rag_chunks chunk
  where chunk.user_id = filter_user_id
    and (filter_rag_document_id is null or chunk.rag_document_id = filter_rag_document_id)
    and chunk.embedding <=> query_embedding < 1 - match_threshold
  order by chunk.embedding <=> query_embedding
  limit least(greatest(match_count, 1), 20);
$$;

-- Run the following optional index later through a direct database connection
-- when rag_chunks becomes large; it can time out in the Dashboard SQL Editor.
-- create index idx_rag_chunks_embedding_hnsw
--   on public.rag_chunks using hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- Chat history and knowledge scrapbook (column names match backend code)
-- ---------------------------------------------------------------------------
create table if not exists public.chat_sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  document_id uuid references public.ocr_documents(id) on delete set null,
  group_id uuid,
  title varchar(120) not null check (length(btrim(title)) > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.chat_sessions
  add column if not exists document_id uuid references public.ocr_documents(id) on delete set null,
  add column if not exists group_id uuid;

create table if not exists public.chat_messages (
  id bigint primary key,
  session_id uuid not null references public.chat_sessions(id) on delete cascade,
  sender text not null check (sender in ('USER', 'ASSISTANT')),
  message text not null check (length(btrim(message)) > 0),
  top_k_chunks jsonb not null default '[]'::jsonb check (jsonb_typeof(top_k_chunks) = 'array'),
  created_at timestamptz not null default now()
);
create index if not exists idx_chat_sessions_user_updated on public.chat_sessions(user_id, updated_at desc);
create index if not exists idx_chat_messages_session_created on public.chat_messages(session_id, created_at asc);

create table if not exists public.knowledge_scraps (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  document_id uuid references public.ocr_documents(id) on delete set null,
  question text not null check (length(btrim(question)) > 0),
  answer text not null check (length(btrim(answer)) > 0),
  document_name text,
  source_count integer not null default 0 check (source_count >= 0),
  sources jsonb not null default '[]'::jsonb check (jsonb_typeof(sources) = 'array'),
  model_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_knowledge_scraps_user_created on public.knowledge_scraps(user_id, created_at desc);

-- ---------------------------------------------------------------------------
-- Model evaluation and subscriptions
-- ---------------------------------------------------------------------------
create table if not exists public.model_evaluation_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  run_name text not null,
  status text not null default 'PENDING' check (status in ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')),
  embedding_model text not null,
  embedding_dimensions integer not null check (embedding_dimensions > 0),
  llm_model text not null,
  rerank_model text,
  prompt_version text not null,
  chunk_target_chars integer not null check (chunk_target_chars > 0),
  top_k integer not null check (top_k > 0),
  dataset_version text not null,
  quality_metrics jsonb not null default '{}'::jsonb,
  latency_metrics jsonb not null default '{}'::jsonb,
  error_message text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  subscription_tier text not null default 'PERSONAL',
  status text not null default 'ACTIVE',
  billing_provider text not null default 'MANUAL',
  provider_subscription_id text,
  current_period_start timestamptz,
  current_period_end timestamptz not null default (now() + interval '30 days'),
  cancel_at_period_end boolean not null default false,
  cancellation_reason text,
  cancellation_requested_at timestamptz,
  canceled_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.subscriptions
  add column if not exists subscription_tier text not null default 'PERSONAL',
  add column if not exists billing_provider text not null default 'MANUAL',
  add column if not exists provider_subscription_id text,
  add column if not exists current_period_start timestamptz,
  add column if not exists current_period_end timestamptz,
  add column if not exists cancel_at_period_end boolean not null default false,
  add column if not exists cancellation_reason text,
  add column if not exists cancellation_requested_at timestamptz,
  add column if not exists canceled_at timestamptz,
  add column if not exists updated_at timestamptz not null default now();
update public.subscriptions set current_period_end = now() + interval '30 days' where current_period_end is null;
alter table public.subscriptions alter column current_period_end set not null;
drop index if exists public.idx_subscriptions_unique_user;
create unique index idx_subscriptions_unique_user on public.subscriptions(user_id);
create index if not exists idx_subscriptions_status_period_end on public.subscriptions(status, current_period_end);

-- ---------------------------------------------------------------------------
-- RLS. The backend uses service-role and also filters every request by user_id.
-- These policies support public.users.id = auth.uid() and email-mapped OAuth rows.
-- ---------------------------------------------------------------------------
create or replace function public.current_app_user_id() returns uuid
language sql stable security definer set search_path = public as $$
  select id from public.users
  where id = auth.uid() or email = auth.jwt() ->> 'email'
  order by (id = auth.uid()) desc limit 1;
$$;

alter table public.ocr_documents enable row level security;
alter table public.ocr_evaluations enable row level security;
alter table public.finance_records enable row level security;
alter table public.rag_documents enable row level security;
alter table public.rag_chunks enable row level security;
alter table public.chat_sessions enable row level security;
alter table public.chat_messages enable row level security;
alter table public.knowledge_scraps enable row level security;
alter table public.model_evaluation_runs enable row level security;
alter table public.subscriptions enable row level security;

drop policy if exists ocr_documents_own on public.ocr_documents;
create policy ocr_documents_own on public.ocr_documents for all
  using (user_id = public.current_app_user_id()) with check (user_id = public.current_app_user_id());
drop policy if exists finance_records_own on public.finance_records;
create policy finance_records_own on public.finance_records for all
  using (user_id = public.current_app_user_id()) with check (user_id = public.current_app_user_id());
drop policy if exists rag_documents_own on public.rag_documents;
create policy rag_documents_own on public.rag_documents for all
  using (user_id = public.current_app_user_id()) with check (user_id = public.current_app_user_id());
drop policy if exists rag_chunks_read_own on public.rag_chunks;
create policy rag_chunks_read_own on public.rag_chunks for select using (user_id = public.current_app_user_id());
drop policy if exists chat_sessions_own on public.chat_sessions;
create policy chat_sessions_own on public.chat_sessions for all
  using (user_id = public.current_app_user_id()) with check (user_id = public.current_app_user_id());
drop policy if exists chat_messages_own on public.chat_messages;
create policy chat_messages_own on public.chat_messages for all using (exists (
  select 1 from public.chat_sessions session where session.id = session_id
    and session.user_id = public.current_app_user_id()
)) with check (exists (
  select 1 from public.chat_sessions session where session.id = session_id
    and session.user_id = public.current_app_user_id()
));
drop policy if exists knowledge_scraps_own on public.knowledge_scraps;
create policy knowledge_scraps_own on public.knowledge_scraps for all
  using (user_id = public.current_app_user_id()) with check (user_id = public.current_app_user_id());
drop policy if exists model_evaluation_runs_own on public.model_evaluation_runs;
create policy model_evaluation_runs_own on public.model_evaluation_runs for select
  using (user_id = public.current_app_user_id());
drop policy if exists subscriptions_read_own on public.subscriptions;
create policy subscriptions_read_own on public.subscriptions for select
  using (user_id = public.current_app_user_id());

-- Private source document bucket. The backend service-role performs file access.
insert into storage.buckets(id, name, public)
values ('documents', 'documents', false)
on conflict (id) do update set public = false;

comment on table public.rag_chunks is 'RAG chunks with page and bbox evidence';
comment on table public.knowledge_scraps is 'User-approved AI answer cards';
comment on table public.finance_records is 'LLM-classified receipt records used for four-sheet finance workbook exports';
comment on column public.ocr_documents.upload_origin is 'OCR or RAG upload source';
