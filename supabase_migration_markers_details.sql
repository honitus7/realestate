-- Add plot-like detail fields to markers (same as plots: area, price, status, etc.)
-- Run in Supabase Dashboard → SQL Editor after supabase_migration_markers.sql

alter table public.markers
  add column if not exists area text default '',
  add column if not exists price text default '',
  add column if not exists status text default 'available',
  add column if not exists color text default 'emerald',
  add column if not exists media_photo text default '',
  add column if not exists media_video text default '';

comment on column public.markers.area is 'Same as plots: e.g. sq ft';
comment on column public.markers.price is 'Same as plots';
comment on column public.markers.status is 'Same as plots: available, on_hold, sold';
