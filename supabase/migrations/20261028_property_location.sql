-- Mevcut ilanlara Google Maps bağlantısı ve koordinat alanları ekler.
alter table public.properties
  add column if not exists maps_url text,
  add column if not exists latitude double precision,
  add column if not exists longitude double precision;
