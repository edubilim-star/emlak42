-- Mevcut profiles tablosuna çalışan hesabı etkin/pasif alanı ekler.
-- Supabase SQL Editor'da bir kez çalıştırın.
alter table public.profiles
  add column if not exists is_active boolean not null default true;
