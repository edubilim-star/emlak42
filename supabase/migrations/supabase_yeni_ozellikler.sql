-- Ofis Portföyü: yeni özellikler (ayarlar, duyurular, ajanda, favoriler, mesajlar,
-- fiyat geçmişi, kayıtlı aramalar, çalışan profili). Birden fazla kez çalıştırılabilir.

-- 1) Çalışan profili alanları
alter table public.profiles add column if not exists bio text;
alter table public.profiles add column if not exists phone text;
alter table public.profiles add column if not exists job_title text;

-- 2) Başlık ayarları
create table if not exists public.app_settings (
  key text primary key,
  value text,
  updated_at timestamptz not null default now()
);

-- 3) Duyurular
create table if not exists public.announcements (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  body text,
  active boolean not null default true,
  created_by uuid default auth.uid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 4) Ajanda / hatırlatmalar
create table if not exists public.reminders (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid(),
  title text not null,
  notes text,
  remind_at timestamptz not null,
  done boolean not null default false,
  property_id uuid references public.properties(id) on delete set null,
  created_at timestamptz not null default now()
);
create index if not exists reminders_user_idx on public.reminders(user_id, remind_at);

-- 5) Favoriler
create table if not exists public.favorites (
  user_id uuid not null default auth.uid(),
  property_id uuid not null references public.properties(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, property_id)
);

-- 6) Mesajlar
create table if not exists public.messages (
  id uuid primary key default gen_random_uuid(),
  sender_id uuid not null,
  recipient_id uuid not null,
  property_id uuid references public.properties(id) on delete set null,
  parent_id uuid references public.messages(id) on delete set null,
  subject text,
  body text not null,
  is_read boolean not null default false,
  deleted_by_sender boolean not null default false,
  deleted_by_recipient boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists messages_recipient_idx on public.messages(recipient_id, created_at desc);
create index if not exists messages_sender_idx on public.messages(sender_id, created_at desc);

-- 7) Fiyat geçmişi (fiyat değişince otomatik kayıt)
create table if not exists public.price_history (
  id uuid primary key default gen_random_uuid(),
  property_id uuid not null references public.properties(id) on delete cascade,
  old_price numeric,
  new_price numeric,
  changed_by uuid,
  changed_at timestamptz not null default now()
);
create index if not exists price_history_property_idx on public.price_history(property_id, changed_at desc);

create or replace function public.log_price_change() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.price is distinct from old.price then
    insert into public.price_history (property_id, old_price, new_price, changed_by)
    values (new.id, old.price, new.price, auth.uid());
  end if;
  return new;
end; $$;
drop trigger if exists properties_price_history on public.properties;
create trigger properties_price_history after update of price on public.properties
  for each row execute function public.log_price_change();

-- 8) Kayıtlı aramalar
create table if not exists public.saved_searches (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid(),
  name text not null,
  filters jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Yetkiler ve güvenlik (RLS)
grant select, insert, update, delete on public.app_settings, public.announcements, public.reminders,
  public.favorites, public.messages, public.price_history, public.saved_searches to authenticated;

alter table public.app_settings enable row level security;
alter table public.announcements enable row level security;
alter table public.reminders enable row level security;
alter table public.favorites enable row level security;
alter table public.messages enable row level security;
alter table public.price_history enable row level security;
alter table public.saved_searches enable row level security;

drop policy if exists "settings_read" on public.app_settings;
create policy "settings_read" on public.app_settings for select to authenticated using (true);
drop policy if exists "announcements_read" on public.announcements;
create policy "announcements_read" on public.announcements for select to authenticated using (true);
drop policy if exists "price_history_read" on public.price_history;
create policy "price_history_read" on public.price_history for select to authenticated using (true);

drop policy if exists "reminders_own" on public.reminders;
create policy "reminders_own" on public.reminders for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists "favorites_own" on public.favorites;
create policy "favorites_own" on public.favorites for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists "saved_searches_own" on public.saved_searches;
create policy "saved_searches_own" on public.saved_searches for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists "messages_read" on public.messages;
create policy "messages_read" on public.messages for select to authenticated
  using (auth.uid() in (sender_id, recipient_id));
drop policy if exists "messages_send" on public.messages;
create policy "messages_send" on public.messages for insert to authenticated
  with check (sender_id = auth.uid());
drop policy if exists "messages_update" on public.messages;
create policy "messages_update" on public.messages for update to authenticated
  using (auth.uid() in (sender_id, recipient_id));
