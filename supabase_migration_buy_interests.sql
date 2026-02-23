-- Buy interest capture + CRM
-- Run in Supabase Dashboard -> SQL Editor.

create table if not exists public.buy_interests (
  id uuid primary key default gen_random_uuid(),
  panorama_id bigint not null references public.panoramas(id) on delete cascade,
  submitted_by uuid references auth.users(id) on delete set null,
  customer_name text not null,
  customer_email text not null,
  customer_phone text not null,
  category text not null default '',
  plots jsonb not null default '[]',
  status text not null default 'new' check (status in ('new', 'contacted', 'qualified', 'won', 'lost')),
  notes text not null default '',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists idx_buy_interests_panorama_id on public.buy_interests(panorama_id);
create index if not exists idx_buy_interests_created_at on public.buy_interests(created_at desc);
create index if not exists idx_buy_interests_status on public.buy_interests(status);

