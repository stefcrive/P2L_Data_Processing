-- Extensions
create extension if not exists "uuid-ossp";
create extension if not exists "pgcrypto";
create extension if not exists "vector";

-- Core tables
create table if not exists public.users_profile (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null unique,
  full_name text,
  role text default 'user',
  created_at timestamp with time zone default now(),
  updated_at timestamp with time zone default now()
);

create table if not exists public.clients (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  email text,
  created_by uuid references auth.users(id),
  created_at timestamp with time zone default now(),
  updated_at timestamp with time zone default now()
);

create table if not exists public.projects (
  id uuid primary key default gen_random_uuid(),
  client_id uuid references public.clients(id) on delete cascade,
  name text not null,
  status text default 'active',
  created_at timestamp with time zone default now(),
  updated_at timestamp with time zone default now()
);

create table if not exists public.agent_runs (
  id uuid primary key default gen_random_uuid(),
  type text not null,
  status text not null default 'queued',
  created_by uuid references auth.users(id),
  created_at timestamp with time zone default now(),
  started_at timestamp with time zone,
  finished_at timestamp with time zone,
  info jsonb default '{}'::jsonb
);

create table if not exists public.agent_artifacts (
  id uuid primary key default gen_random_uuid(),
  run_id uuid references public.agent_runs(id) on delete cascade,
  kind text not null,
  path text,
  metadata jsonb default '{}'::jsonb,
  created_at timestamp with time zone default now()
);

create table if not exists public.results (
  id uuid primary key default gen_random_uuid(),
  project_id uuid references public.projects(id) on delete set null,
  analysis_type text not null,
  payload jsonb not null,
  created_at timestamp with time zone default now()
);

create table if not exists public.rag_documents (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  path text,
  created_by uuid references auth.users(id),
  created_at timestamp with time zone default now()
);

create table if not exists public.rag_chunks (
  id uuid primary key default gen_random_uuid(),
  document_id uuid references public.rag_documents(id) on delete cascade,
  content text not null,
  embedding vector(1536),
  created_at timestamp with time zone default now()
);

create table if not exists public.audit_logs (
  id bigserial primary key,
  actor uuid,
  action text not null,
  entity text,
  entity_id uuid,
  data jsonb,
  created_at timestamp with time zone default now()
);

-- TODO: add remaining domain tables (instruments, runs, samples, analyses, attachments, payments, credits, invoices, labbook_entries, consumables, inventory_tx)

