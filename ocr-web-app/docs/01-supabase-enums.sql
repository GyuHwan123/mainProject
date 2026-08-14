-- DOCUNEX Supabase schema v2026.08
-- Run this file by itself, wait for success, and then run 02-supabase-schema.sql.
-- PostgreSQL requires newly added enum values to be committed before use.

do $$
begin
  if exists (select 1 from pg_type where typnamespace = 'public'::regnamespace and typname = 'user_role') then
    alter type public.user_role add value if not exists 'DEVELOPER';
  end if;
  if exists (select 1 from pg_type where typnamespace = 'public'::regnamespace and typname = 'subscription_status') then
    alter type public.subscription_status add value if not exists 'CANCEL_SCHEDULED';
    alter type public.subscription_status add value if not exists 'PAST_DUE';
    alter type public.subscription_status add value if not exists 'CANCELED';
  end if;
end $$;
