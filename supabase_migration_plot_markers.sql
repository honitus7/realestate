-- Add marker support for 360 panorama plot points

create table if not exists public.plot_markers (
  id uuid primary key default gen_random_uuid(),
  plot_id text not null,
  name text not null,
  description text default '',
  status text default 'available',
  marker_style text default 'glass',
  marker_icon text default 'mdi:map-marker-radius',
  marker_color text default '#4ade80',
  image_base64 text default null,
  longitude double precision not null,
  latitude double precision not null,
  created_at timestamptz default now()
);

alter table public.plot_markers
  add column if not exists status text default 'available',
  add column if not exists marker_style text default 'glass',
  add column if not exists marker_icon text default 'mdi:map-marker-radius',
  add column if not exists marker_color text default '#4ade80';

do $$
begin
  begin
    alter table public.plot_markers drop constraint if exists plot_markers_status_check;
    alter table public.plot_markers add constraint plot_markers_status_check check (status in ('available', 'reserved', 'on_hold', 'sold'));
  exception when undefined_table then
    null;
  end;
end $$;

create index if not exists idx_plot_markers_plot_id on public.plot_markers(plot_id);
create index if not exists idx_plot_markers_created_at on public.plot_markers(created_at desc);
