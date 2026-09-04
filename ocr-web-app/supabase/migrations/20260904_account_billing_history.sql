create table if not exists public.billing_history (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  subscription_id uuid references public.subscriptions(id) on delete set null,
  amount integer not null check (amount >= 0),
  currency text not null default 'KRW',
  status text not null default 'PAID' check (status in ('PAID', 'PENDING', 'FAILED', 'REFUNDED')),
  payment_method text,
  invoice_number text,
  receipt_url text,
  paid_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create index if not exists billing_history_user_paid_idx on public.billing_history (user_id, paid_at desc);
alter table public.billing_history enable row level security;
