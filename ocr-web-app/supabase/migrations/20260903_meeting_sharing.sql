-- Account-based meeting invitations and access levels.
alter table public.meeting_participants
  add column if not exists invited_email text,
  add column if not exists permission text not null default 'VIEWER',
  add column if not exists invitation_status text not null default 'ACCEPTED',
  add column if not exists invited_by uuid references public.users(id) on delete set null,
  add column if not exists invited_at timestamptz not null default now(),
  add column if not exists accepted_at timestamptz;

update public.meeting_participants
set invitation_status = 'ACCEPTED'
where invitation_status is null;

alter table public.meeting_participants
  drop constraint if exists meeting_participants_permission_check,
  add constraint meeting_participants_permission_check
    check (permission in ('VIEWER', 'EDITOR')),
  drop constraint if exists meeting_participants_invitation_status_check,
  add constraint meeting_participants_invitation_status_check
    check (invitation_status in ('PENDING', 'ACCEPTED', 'DECLINED'));

create unique index if not exists meeting_participants_meeting_user_uidx
  on public.meeting_participants (meeting_id, user_id)
  where user_id is not null;

create index if not exists meeting_participants_user_status_idx
  on public.meeting_participants (user_id, invitation_status);

create index if not exists meeting_participants_invited_email_idx
  on public.meeting_participants (lower(invited_email));

-- Browser clients must not access meeting data directly. The application backend
-- authenticates its own JWT and performs owner/member checks before using the
-- Supabase service role. Service-role requests bypass RLS by design.
alter table public.meetings enable row level security;
alter table public.meeting_participants enable row level security;
