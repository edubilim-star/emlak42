-- Supabase SQL Editor'da tek seferde çalıştırın.
create extension if not exists pgcrypto;

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  name text not null,
  is_admin boolean not null default false,
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

alter table public.profiles add column if not exists is_active boolean not null default true;

create table public.properties (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  city text not null,
  district text not null,
  deal_type text not null check (deal_type in ('Kiralık', 'Satılık')),
  price bigint not null default 0,
  rooms text,
  area_m2 integer,
  net_area_m2 integer,
  property_category text not null default 'Daire',
  building_age text,
  floor text,
  total_floors integer,
  heating text,
  bathroom_count integer not null default 1,
  kitchen text,
  balcony boolean not null default false,
  elevator boolean not null default false,
  parking text,
  furnished boolean not null default false,
  usage_status text,
  within_complex boolean not null default false,
  complex_name text,
  monthly_dues bigint not null default 0,
  title_deed_status text,
  loan_eligible boolean not null default false,
  swap_allowed boolean not null default false,
  address text,
  description text,
  status text not null default 'Aktif' check (status in ('Aktif', 'Opsiyonlu', 'Kiralandı/Satıldı', 'Pasif')),
  notes text,
  owner_id uuid not null references public.profiles(id),
  created_by uuid not null references public.profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.requests (
  id uuid primary key default gen_random_uuid(),
  customer_name text not null,
  phone text,
  deal_type text not null check (deal_type in ('Kiralık', 'Satılık')),
  city text,
  district text,
  rooms text,
  min_price bigint not null default 0,
  max_price bigint not null default 0,
  target_date date,
  status text not null default 'Yeni' check (status in ('Yeni', 'İletişimde', 'Gösterim', 'Tamamlandı', 'İptal')),
  notes text,
  owner_id uuid not null references public.profiles(id),
  created_by uuid not null references public.profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index properties_owner_idx on public.properties(owner_id);
create index properties_location_idx on public.properties(city, district);
create index requests_owner_idx on public.requests(owner_id);
create index requests_status_idx on public.requests(status);

alter table public.profiles enable row level security;
alter table public.properties enable row level security;
alter table public.requests enable row level security;

-- Ofis içindeki tüm oturum açmış çalışanlar ortak portföyü görebilir.
-- İlk kurulum yöneticisi dahil, ekipteki herkes ofis kayıtlarını yönetebilir.
create policy "signed-in users read profiles" on public.profiles for select to authenticated using (true);
create policy "signed-in users read properties" on public.properties for select to authenticated using (true);
create policy "signed-in users add properties" on public.properties for insert to authenticated with check (created_by = auth.uid());
create policy "signed-in users update properties" on public.properties for update to authenticated using (true) with check (true);
create policy "signed-in users delete properties" on public.properties for delete to authenticated using (true);
create policy "signed-in users read requests" on public.requests for select to authenticated using (true);
create policy "signed-in users add requests" on public.requests for insert to authenticated with check (created_by = auth.uid());
create policy "signed-in users update requests" on public.requests for update to authenticated using (true) with check (true);
create policy "signed-in users delete requests" on public.requests for delete to authenticated using (true);

-- İlan fotoğrafları: özel bucket, yalnızca giriş yapmış ofis çalışanları.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('property-photos', 'property-photos', false, 10485760, array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do update set public = false, file_size_limit = 10485760, allowed_mime_types = array['image/jpeg', 'image/png', 'image/webp'];

drop policy if exists "office users view property photos" on storage.objects;
create policy "office users view property photos" on storage.objects
for select to authenticated using (bucket_id = 'property-photos');

drop policy if exists "office users upload property photos" on storage.objects;
create policy "office users upload property photos" on storage.objects
for insert to authenticated with check (bucket_id = 'property-photos');
