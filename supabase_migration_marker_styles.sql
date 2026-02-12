-- Marker style/icon/color support for 360 markers
-- Run this in Supabase SQL Editor for existing projects.

alter table if exists public.plot_markers
  add column if not exists marker_style text default 'glass',
  add column if not exists marker_icon text default 'mdi:map-marker-radius',
  add column if not exists marker_color text default '#4ade80';

alter table if exists public.plot_markers
  add column if not exists status text default 'available';

alter table if exists public.plot_markers
  drop constraint if exists plot_markers_status_check;

alter table if exists public.plot_markers
  add constraint plot_markers_status_check
  check (status in ('available', 'reserved', 'on_hold', 'sold'));
