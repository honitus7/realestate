-- Plot and panorama images in Postgres (base64 text)
-- Run in Supabase Dashboard → SQL Editor if tables already existed

alter table public.plots
  add column if not exists image_data text default null,
  add column if not exists image_content_type text default 'image/jpeg';

alter table public.panoramas
  add column if not exists image_data text default null,
  add column if not exists image_content_type text default 'image/jpeg';
