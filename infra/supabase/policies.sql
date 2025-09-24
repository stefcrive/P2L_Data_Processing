-- Enable RLS
alter table public.users_profile enable row level security;
alter table public.clients enable row level security;
alter table public.projects enable row level security;
alter table public.agent_runs enable row level security;
alter table public.agent_artifacts enable row level security;
alter table public.results enable row level security;
alter table public.rag_documents enable row level security;
alter table public.rag_chunks enable row level security;
alter table public.audit_logs enable row level security;

-- Basic policies: authenticated read, owner write (example)
create policy if not exists "read_authenticated_users_profile"
  on public.users_profile for select using (auth.uid() = user_id);

create policy if not exists "modify_own_profile"
  on public.users_profile for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy if not exists "clients_read_authenticated"
  on public.clients for select using (auth.role() = 'authenticated');

create policy if not exists "clients_insert_authenticated"
  on public.clients for insert with check (auth.role() = 'authenticated');

create policy if not exists "projects_rw_authenticated"
  on public.projects for all using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');

create policy if not exists "agent_runs_rw_authenticated"
  on public.agent_runs for all using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');

create policy if not exists "agent_artifacts_rw_authenticated"
  on public.agent_artifacts for all using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');

create policy if not exists "results_ro_authenticated"
  on public.results for select using (auth.role() = 'authenticated');

create policy if not exists "rag_documents_rw_authenticated"
  on public.rag_documents for all using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');

create policy if not exists "rag_chunks_ro_authenticated"
  on public.rag_chunks for select using (auth.role() = 'authenticated');

create policy if not exists "audit_logs_ro_admin"
  on public.audit_logs for select using (auth.jwt()->>'role' = 'admin');

