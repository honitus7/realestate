-- Add optional use_animated_icons flag to panoramas (for marker icon animation)
alter table if exists public.panoramas
  add column if not exists use_animated_icons boolean default false;
