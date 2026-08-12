-- Step 1: Run this statement first in Supabase SQL Editor.
alter type public.user_role add value if not exists 'DEVELOPER';

-- Commit Step 1, then run Step 2 separately. PostgreSQL requires a newly added
-- enum value to be committed before it can be used.
-- update public.users
-- set role = 'DEVELOPER'::public.user_role
-- where email = 'developer@docunex.com';
