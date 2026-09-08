alter table public.rag_documents
  add column if not exists deleted_at timestamptz null;
