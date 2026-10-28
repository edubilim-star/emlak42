-- Supabase SQL Editor'da mevcut projede bir kez çalıştırın.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('property-photos', 'property-photos', false, 10485760, array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do update set public = false, file_size_limit = 10485760, allowed_mime_types = array['image/jpeg', 'image/png', 'image/webp'];

drop policy if exists "office users view property photos" on storage.objects;
create policy "office users view property photos" on storage.objects
for select to authenticated using (bucket_id = 'property-photos');

drop policy if exists "office users upload property photos" on storage.objects;
create policy "office users upload property photos" on storage.objects
for insert to authenticated with check (bucket_id = 'property-photos');
