-- Supabase SQL Editor에서 이 파일을 먼저 단독 실행하세요.
-- PostgreSQL enum에 개발자 역할을 추가합니다.

alter type public.user_role
  add value if not exists 'DEVELOPER';
