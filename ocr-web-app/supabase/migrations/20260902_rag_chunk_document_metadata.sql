begin;

alter table public.rag_chunks
  add column if not exists document_title text,
  add column if not exists section_title text,
  add column if not exists section_path text[] not null default '{}'::text[],
  add column if not exists heading_level smallint,
  add column if not exists bbox jsonb;

update public.rag_chunks as chunk
set document_title = document.title
from public.rag_documents as document
where document.id = chunk.document_id
  and (chunk.document_title is null or btrim(chunk.document_title) = '');

alter table public.rag_chunks
  alter column document_title set not null;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'rag_chunks_heading_level_check'
      and conrelid = 'public.rag_chunks'::regclass
  ) then
    alter table public.rag_chunks
      add constraint rag_chunks_heading_level_check
      check (heading_level is null or heading_level between 1 and 6);
  end if;
end $$;

create index if not exists idx_rag_chunks_document_section
  on public.rag_chunks (document_id, section_title, page_number, chunk_index);

drop function if exists public.match_rag_chunks(vector, uuid[], double precision, integer);

create function public.match_rag_chunks(
  query_embedding vector(1024),
  allowed_document_ids uuid[],
  match_threshold double precision default 0.35,
  match_count integer default 5
)
returns table (
  id uuid,
  document_id uuid,
  doc_id text,
  title text,
  owner text,
  security text,
  version text,
  effective_date text,
  filename text,
  tags text[],
  chunk_index integer,
  page_number integer,
  content text,
  document_title text,
  section_title text,
  section_path text[],
  heading_level smallint,
  bbox jsonb,
  similarity double precision
)
language sql
stable
security invoker
set search_path = public
as $$
  select
    chunk.id,
    chunk.document_id,
    document.doc_id,
    document.title,
    document.owner,
    document.security,
    document.version,
    document.effective_date,
    document.filename,
    document.tags,
    chunk.chunk_index,
    chunk.page_number,
    chunk.content,
    chunk.document_title,
    chunk.section_title,
    chunk.section_path,
    chunk.heading_level,
    chunk.bbox,
    (1 - (chunk.embedding <=> query_embedding))::double precision
  from public.rag_chunks as chunk
  join public.rag_documents as document on document.id = chunk.document_id
  where chunk.document_id = any(allowed_document_ids)
    and (1 - (chunk.embedding <=> query_embedding)) >= match_threshold
  order by chunk.embedding <=> query_embedding
  limit least(greatest(match_count, 1), 100);
$$;

commit;
