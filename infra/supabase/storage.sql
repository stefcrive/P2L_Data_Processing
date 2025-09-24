-- Buckets
insert into storage.buckets (id, name, public)
values
  ('raw_data', 'raw_data', false),
  ('processed', 'processed', false),
  ('reports', 'reports', false),
  ('screenshots', 'screenshots', false),
  ('docs', 'docs', false)
on conflict (id) do nothing;

