-- Read-only verification after applying 01 and 02.
select extname, extversion, extnamespace::regnamespace as installed_schema
from pg_extension where extname = 'vector';

select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in (
    'users', 'ocr_documents', 'ocr_evaluations', 'rag_documents', 'rag_chunks',
    'chat_sessions', 'chat_messages', 'knowledge_scraps',
    'model_evaluation_runs', 'subscriptions'
  )
order by table_name;

select tablename, indexname, indexdef
from pg_indexes
where schemaname = 'public'
  and tablename in ('rag_chunks', 'subscriptions', 'chat_sessions', 'chat_messages')
order by tablename, indexname;

select schemaname, tablename, policyname, cmd
from pg_policies
where schemaname = 'public'
order by tablename, policyname;

select proname from pg_proc
where pronamespace = 'public'::regnamespace
  and proname in ('match_rag_chunks', 'current_app_user_id')
order by proname;
