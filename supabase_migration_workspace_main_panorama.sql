-- Add main panorama per workspace for Full View share link.
-- Run in Supabase Dashboard -> SQL Editor.
-- Safe to run multiple times.

alter table public.workspaces
  add column if not exists main_panorama_id integer references public.panoramas(id) on delete set null;

create index if not exists idx_workspaces_main_panorama_id on public.workspaces(main_panorama_id);
