-- Add voice-over (audio) filename column for markers (S3-backed, non-destructive)
alter table if exists public.plot_markers
  add column if not exists voiceover_filename text;
