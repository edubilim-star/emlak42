create table if not exists public.offers (
  id uuid primary key default gen_random_uuid(),
  property_id uuid not null references public.properties(id) on delete cascade,
  customer_name text not null,
  phone text,
  amount numeric not null default 0,
  status text not null default 'Beklemede',
  notes text,
  offer_date date default current_date,
  created_by uuid default auth.uid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists offers_property_id_idx on public.offers(property_id);
alter table public.offers enable row level security;
grant select, insert, update, delete on public.offers to authenticated;
create policy "offers_select" on public.offers for select to authenticated using (true);
create policy "offers_insert" on public.offers for insert to authenticated with check (auth.uid() is not null);
create policy "offers_update" on public.offers for update to authenticated using (
  created_by = auth.uid()
  or exists (select 1 from public.profiles p where p.id = auth.uid() and p.is_admin)
  or exists (select 1 from public.properties pr where pr.id = property_id and pr.owner_id = auth.uid()));
create policy "offers_delete" on public.offers for delete to authenticated using (
  created_by = auth.uid()
  or exists (select 1 from public.profiles p where p.id = auth.uid() and p.is_admin)
  or exists (select 1 from public.properties pr where pr.id = property_id and pr.owner_id = auth.uid()));
