-- step1 실행이 성공한 후, 새 SQL Editor 탭에서 이 파일을 실행하세요.

-- 기존 public.users를 이메일/비밀번호 로그인에도 사용할 수 있도록 확장합니다.
alter table public.users
  add column if not exists name varchar(100),
  add column if not exists password_hash varchar(255),
  add column if not exists is_active boolean not null default true,
  add column if not exists updated_at timestamptz not null default now();

-- 기존 소셜 로그인 사용자는 이메일을 표시 이름의 초기값으로 사용합니다.
update public.users
set name = coalesce(nullif(name, ''), email)
where name is null or name = '';

alter table public.users
  alter column name set not null;

-- 로컬 이메일 로그인을 social_provider 제약조건에 허용합니다.
alter table public.users
  drop constraint if exists chk_users_social_provider;

alter table public.users
  add constraint chk_users_social_provider
  check (social_provider in ('local', 'google', 'apple', 'kakao', 'supabase'));

-- 모든 컴퓨터에서 사용할 공통 개발자 계정입니다.
-- 비밀번호: DevOCR!2026
insert into public.users (
  email,
  name,
  password_hash,
  social_provider,
  social_id,
  role,
  is_active,
  updated_at
)
values (
  'developer@docunex.com',
  'OCR Developer',
  '$pbkdf2-sha256$29000$8L7Xutd6753T.l9r7b2XMg$bcxLauGcZmH5HNj.1PK88t6LrDDJ7/MxA0GLHeAz2So',
  'local',
  'developer@docunex.com',
  'DEVELOPER'::public.user_role,
  true,
  now()
)
on conflict (email) do update
set
  name = excluded.name,
  password_hash = excluded.password_hash,
  social_provider = excluded.social_provider,
  social_id = excluded.social_id,
  role = excluded.role,
  is_active = excluded.is_active,
  updated_at = now();
