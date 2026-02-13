-- Add linked panorama support for plots + 360 markers (hotspots)
--
-- Run in Supabase Dashboard -> SQL Editor.
-- Safe to run multiple times.

alter table public.plots
  add column if not exists linked_panorama_id bigint;

alter table public.plot_markers
  add column if not exists linked_panorama_id bigint;

create index if not exists idx_plots_linked_panorama_id on public.plots(linked_panorama_id);
create index if not exists idx_plot_markers_linked_panorama_id on public.plot_markers(linked_panorama_id);

