-- Organizations + org_id scoping (multi-tenant)
--
-- Run in Supabase Dashboard -> SQL Editor.
-- Safe to run multiple times.

create table if not exists public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  accent_color text not null default '#c9a962',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists idx_organizations_created_at on public.organizations(created_at desc);

-- Add org_id columns
alter table public.profiles
  add column if not exists org_id uuid references public.organizations(id) on delete set null;

alter table public.panoramas
  add column if not exists org_id uuid references public.organizations(id) on delete set null;

create index if not exists idx_profiles_org_id on public.profiles(org_id);
create index if not exists idx_panoramas_org_id on public.panoramas(org_id);

-- Update role check to allow superadmin
alter table public.profiles drop constraint if exists profiles_role_check;
alter table public.profiles
  add constraint profiles_role_check check (role in ('superadmin', 'admin', 'user'));

-- Backfill: create a default org and assign existing records (optional but recommended)
insert into public.organizations (name, accent_color)
values ('Default Organization', '#c9a962')
on conflict (name) do nothing;

-- Assign all profiles without org_id to Default Organization
update public.profiles
set org_id = (
  select id from public.organizations where name = 'Default Organization' limit 1
)
where org_id is null;

-- Assign panoramas org_id from their owner's profile org_id
update public.panoramas p
set org_id = pr.org_id
from public.profiles pr
where p.org_id is null
  and pr.user_id = p.user_id
  and pr.org_id is not null;

