-- Allow the email/password provider used by this application.
alter table public.users
  drop constraint if exists chk_users_social_provider;

alter table public.users
  add constraint chk_users_social_provider
  check (social_provider in ('local', 'google', 'github', 'kakao'));
