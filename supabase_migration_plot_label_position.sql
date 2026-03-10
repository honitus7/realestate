-- Store plot label position on sphere (like markers) so labels can be moved and persisted.
-- Run in Supabase Dashboard -> SQL Editor. Safe to run multiple times.

alter table public.plots
  add column if not exists label_longitude double precision,
  add column if not exists label_latitude double precision;
