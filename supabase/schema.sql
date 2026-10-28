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
