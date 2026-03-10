-- Copy display_name and email from auth.users into profiles when a new user is created.
-- Run in Supabase Dashboard → SQL Editor. Safe to run multiple times.

create or replace function public.handle_new_user()
returns trigger as $$
declare
  dn text;
begin
  dn := nullif(trim(coalesce(new.raw_user_meta_data->>'display_name', '')), '');
  insert into public.profiles (user_id, role, display_name, email)
  values (new.id, 'user', dn, new.email)
  on conflict (user_id) do nothing;
  return new;
end;
$$ language plpgsql security definer;

-- Trigger already exists from schema; ensure it uses the new function
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
