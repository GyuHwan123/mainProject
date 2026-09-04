create table if not exists public.password_reset_tokens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  token_hash text not null unique,
  expires_at timestamptz not null,
  used_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists password_reset_tokens_user_created_idx
  on public.password_reset_tokens (user_id, created_at desc);

create unique index if not exists password_reset_tokens_one_unused_per_user_idx
  on public.password_reset_tokens (user_id)
  where used_at is null;

alter table public.password_reset_tokens enable row level security;

create or replace function public.confirm_password_reset(
  p_token_hash text,
  p_password_hash text
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  reset_row public.password_reset_tokens%rowtype;
begin
  select *
    into reset_row
    from public.password_reset_tokens
   where token_hash = p_token_hash
     and used_at is null
     and expires_at > now()
   order by created_at desc
   limit 1
   for update;

  if not found then
    return false;
  end if;

  update public.users
     set password_hash = p_password_hash
   where id = reset_row.user_id;

  if not found then
    return false;
  end if;

  update public.password_reset_tokens
     set used_at = now()
   where id = reset_row.id;

  return true;
end;
$$;

revoke all on function public.confirm_password_reset(text, text) from public, anon, authenticated;
grant execute on function public.confirm_password_reset(text, text) to service_role;
