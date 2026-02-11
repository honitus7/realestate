-- Add marker support for 360 panorama plot points

create table if not exists public.plot_markers (
  id uuid primary key default gen_random_uuid(),
  plot_id text not null,
  name text not null,
  description text default '',
  image_base64 text default null,
  longitude double precision not null,
  latitude double precision not null,
  created_at timestamptz default now()
);

create index if not exists idx_plot_markers_plot_id on public.plot_markers(plot_id);
create index if not exists idx_plot_markers_created_at on public.plot_markers(created_at desc);
