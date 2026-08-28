-- Apply manually in the Supabase SQL Editor.
-- This changes only the current BGE-M3 vector(1024) search RPC.

begin;

drop function if exists public.match_rag_chunks(vector, double precision, integer);

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
    (1 - (chunk.embedding <=> query_embedding))::double precision as similarity
  from public.rag_chunks as chunk
  join public.rag_documents as document
    on document.id = chunk.document_id
  where chunk.document_id = any(allowed_document_ids)
    and (1 - (chunk.embedding <=> query_embedding)) >= match_threshold
  order by chunk.embedding <=> query_embedding
  limit least(greatest(match_count, 1), 100);
$$;

commit;
