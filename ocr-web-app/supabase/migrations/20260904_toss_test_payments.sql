-- Toss Payments test checkout orders and atomic subscription activation.
-- Run after the existing users, subscriptions, and billing_history migrations.

alter table public.users drop constraint if exists users_subscription_tier_check;
update public.users set subscription_tier = 'FREE' where subscription_tier is null or subscription_tier = 'PERSONAL';
alter table public.users alter column subscription_tier set default 'FREE';
alter table public.users add constraint users_subscription_tier_check
  check (subscription_tier in ('FREE', 'ENTERPRISE'));

alter table public.subscriptions drop constraint if exists subscriptions_subscription_tier_check;
update public.subscriptions set subscription_tier = 'FREE' where subscription_tier is null or subscription_tier = 'PERSONAL';
alter table public.subscriptions alter column subscription_tier set default 'FREE';
alter table public.subscriptions add constraint subscriptions_subscription_tier_check
  check (subscription_tier in ('FREE', 'ENTERPRISE'));

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

create index if not exists billing_history_user_paid_idx
  on public.billing_history (user_id, paid_at desc);
alter table public.billing_history enable row level security;

create table if not exists public.payment_orders (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  order_id text not null unique,
  plan text not null check (plan in ('ENTERPRISE')),
  amount integer not null check (amount > 0),
  currency text not null default 'KRW',
  status text not null default 'PENDING' check (status in ('PENDING', 'PAID', 'FAILED', 'CANCELED')),
  payment_key text unique,
  failure_code text,
  failure_message text,
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists payment_orders_user_created_idx
  on public.payment_orders (user_id, created_at desc);
create unique index if not exists payment_orders_one_pending_per_user_idx
  on public.payment_orders (user_id) where status = 'PENDING';
alter table public.payment_orders enable row level security;

alter table public.billing_history add column if not exists order_id text;
alter table public.billing_history add column if not exists provider text;
alter table public.billing_history add column if not exists payment_key text;
alter table public.billing_history add column if not exists plan text;
create unique index if not exists billing_history_order_id_uidx
  on public.billing_history (order_id) where order_id is not null;

create or replace function public.complete_toss_payment(
  p_user_id uuid,
  p_order_id text,
  p_payment_key text,
  p_amount integer,
  p_payment_method text,
  p_receipt_url text,
  p_approved_at timestamptz
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_order public.payment_orders%rowtype;
  v_subscription public.subscriptions%rowtype;
  v_payment public.billing_history%rowtype;
  v_period_end timestamptz := p_approved_at + interval '30 days';
begin
  select * into v_order
  from public.payment_orders
  where order_id = p_order_id and user_id = p_user_id
  for update;

  if not found then
    raise exception 'PAYMENT_ORDER_NOT_FOUND';
  end if;
  if v_order.amount <> p_amount or v_order.plan <> 'ENTERPRISE' then
    raise exception 'PAYMENT_AMOUNT_MISMATCH';
  end if;

  if v_order.status = 'PAID' then
    select * into v_payment from public.billing_history where order_id = p_order_id;
    select * into v_subscription from public.subscriptions where user_id = p_user_id;
    return jsonb_build_object('order', to_jsonb(v_order), 'payment', to_jsonb(v_payment), 'subscription', to_jsonb(v_subscription));
  end if;
  if v_order.status <> 'PENDING' then
    raise exception 'PAYMENT_ORDER_NOT_PENDING';
  end if;

  update public.payment_orders
  set status = 'PAID', payment_key = p_payment_key, approved_at = p_approved_at,
      failure_code = null, failure_message = null, updated_at = now()
  where id = v_order.id
  returning * into v_order;

  insert into public.subscriptions (
    user_id, subscription_tier, status, billing_provider,
    current_period_start, current_period_end, cancel_at_period_end,
    cancellation_reason, cancellation_requested_at, updated_at
  ) values (
    p_user_id, 'ENTERPRISE', 'ACTIVE', 'TOSS_TEST',
    p_approved_at, v_period_end, false, null, null, now()
  )
  on conflict (user_id) do update set
    subscription_tier = excluded.subscription_tier,
    status = excluded.status,
    billing_provider = excluded.billing_provider,
    current_period_start = excluded.current_period_start,
    current_period_end = excluded.current_period_end,
    cancel_at_period_end = false,
    cancellation_reason = null,
    cancellation_requested_at = null,
    updated_at = now()
  returning * into v_subscription;

  update public.users set subscription_tier = 'ENTERPRISE' where id = p_user_id;

  insert into public.billing_history (
    user_id, subscription_id, order_id, provider, payment_key, plan,
    amount, currency, status, payment_method, invoice_number, receipt_url, paid_at
  ) values (
    p_user_id, v_subscription.id, p_order_id, 'TOSS_PAYMENTS_TEST', p_payment_key, 'ENTERPRISE',
    p_amount, 'KRW', 'PAID', p_payment_method,
    'INV-' || to_char(p_approved_at, 'YYYYMMDD') || '-' || upper(right(p_order_id, 8)),
    p_receipt_url, p_approved_at
  )
  on conflict (order_id) where order_id is not null do update set
    payment_key = excluded.payment_key,
    status = 'PAID',
    payment_method = excluded.payment_method,
    receipt_url = excluded.receipt_url,
    paid_at = excluded.paid_at
  returning * into v_payment;

  return jsonb_build_object('order', to_jsonb(v_order), 'payment', to_jsonb(v_payment), 'subscription', to_jsonb(v_subscription));
end;
$$;

revoke all on function public.complete_toss_payment(uuid, text, text, integer, text, text, timestamptz) from public;
grant execute on function public.complete_toss_payment(uuid, text, text, integer, text, text, timestamptz) to service_role;

create or replace function public.expire_scheduled_subscription(p_user_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_subscription public.subscriptions%rowtype;
begin
  update public.subscriptions
  set subscription_tier = 'FREE', status = 'CANCELED', cancel_at_period_end = false, updated_at = now()
  where user_id = p_user_id
    and status = 'CANCEL_SCHEDULED'
    and current_period_end <= now()
  returning * into v_subscription;

  if found then
    update public.users set subscription_tier = 'FREE' where id = p_user_id;
    return to_jsonb(v_subscription);
  end if;
  select * into v_subscription from public.subscriptions where user_id = p_user_id;
  return to_jsonb(v_subscription);
end;
$$;

revoke all on function public.expire_scheduled_subscription(uuid) from public;
grant execute on function public.expire_scheduled_subscription(uuid) to service_role;
