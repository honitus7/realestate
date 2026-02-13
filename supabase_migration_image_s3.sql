-- Add S3-backed image filename columns for plots and markers (non-destructive)
alter table if exists public.plots
  add column if not exists image_filename text;

alter table if exists public.plot_markers
  add column if not exists image_filename text;
