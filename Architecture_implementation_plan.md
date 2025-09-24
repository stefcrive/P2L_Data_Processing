awesome—this is a meaty, multi-module lab platform. below is (1) a ready-to-paste “kickoff” prompt for your coding agent to bootstrap the repo and scaffolding, and (2) a detailed implementation plan your team can use as a reference while building.

---

# 1) initial prompt for a coding agent (copy-paste)

**Goal:** bootstrap a monorepo for a Next.js (TypeScript) + Tailwind + shadcn/ui frontend, a Python FastAPI backend, and a Supabase Postgres (with RLS, pgvector, Storage) data layer. The app is a lab dashboard + control surface for:

* AI agents that can control a remote workstation (“computer use”), capture screenshots of valves/logs, run multimodal LLM analysis → structured output → technical reports & dashboard widgets.
* IRMS results processing (existing Python logic will be ported/adapted).
* Future ICP-OES and GC-IRMS processing.
* Commercial client management (CRM-lite): clients, quotes/orders, analyses, results, payments/credits, reports.
* Lab book & inventory of consumables integrated with events.
* A master consultant LLM chatbot (RAG over PDFs + lab data; OpenAI API).

**Non-negotiables**

* Frontend: Next.js (App Router), TypeScript, TailwindCSS, shadcn/ui, TanStack Query, Zod, Zustand (or Redux Toolkit if needed), React Hook Form, Recharts (or similar) for plots.
* Backend: Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.x (for any direct DB work), Celery/RQ for background jobs, Redis for queues, Playwright/PyAutoGUI (or similar) for computer-use agents, OpenAI API client.
* Data: Supabase (Postgres + RLS + pgvector + Storage). Use Supabase Auth for user management and JWTs. Service-role key only server-side.
* Observability & QA: structured logging, unit/integration tests (pytest), Playwright e2e, pre-commit hooks, mypy.
* Packaging/Infra: Docker + docker-compose dev; `.env` for local; GitHub Actions CI.

**What to do now**

1. **Create monorepo layout**:

   ```
   /apps
     /web           # Next.js app
     /api           # FastAPI app
   /packages
     /ui            # shared UI primitives (optional)
     /types         # shared TypeScript contract types (OpenAPI-generated)
     /schemas       # JSONSchema & Pydantic models definitions
   /infra
     /supabase      # db schema, policies, seed, migrations
     /docker        # docker & compose templates
   .tool-versions or .node-version / .python-version
   ```
2. **Scaffold Next.js** (App Router) with Tailwind + shadcn/ui; add auth context with Supabase Auth; protected routes; basic layout with sidebar: Dashboard, Clients, Analyses, Lab Book, Inventory, Agents, Knowledge (RAG), Admin.
3. **Scaffold FastAPI** with:

   * `/internal` routes (service role) and `/v1` user routes (JWT).
   * OpenAPI schema + JSON mode responses (pydantic).
   * Celery/RQ worker + Redis for long-running tasks (file processing, agent runs, LLM pipelines).
   * WebSocket/SSE endpoint for live job status.
4. **Supabase setup**:

   * Tables: users\_profile, clients, projects, instruments, runs, samples, analyses, results, attachments, payments, credits, invoices, labbook\_entries, consumables, inventory\_tx, agent\_runs, agent\_artifacts, rag\_documents, rag\_chunks (pgvector), audit\_logs.
   * Storage buckets: `raw_data/`, `processed/`, `reports/`, `screenshots/`, `docs/`.
   * RLS policies (read/write separated by role).
   * Triggers to write to `audit_logs` and to notify channels (postgres NOTIFY) for real-time UI updates.
5. **Contracts**:

   * Define shared DTOs (Zod on FE, Pydantic on BE).
   * Use OpenAPI codegen to produce `/packages/types` for strict typing on the FE.
6. **Implement core flows**:

   * **IRMS**: wrap the provided Python routines into a FastAPI service (`/v1/irms/process`) + background job; upload raw input → returns a job id; on completion, persist `results` + generated plots in Storage; expose a `/v1/jobs/{id}` status.
   * **Agents (computer use)**: create a job type that (a) establishes remote control (Playwright for browser, or RDP/VNC integration), (b) captures screenshots of valve states/logs, (c) sends images + text instructions to OpenAI multimodal endpoint, (d) returns structured JSON per schema. Persist images to Storage, JSON to DB, and surface via dashboard cards.
   * **CRM-lite**: clients, projects, quotes/orders, analyses linked to results and invoices/payments; views & filters; export PDF reports.
   * **Lab book & Inventory**: form to log events; inventory transactions auto-generated from lab book entries; threshold alerts; CSV import/export.
   * **RAG**: document upload → chunk → embed (pgvector) → retrieval pipeline; chat endpoint that blends client/machine context.
7. **Developer UX**: devcontainers or Makefile; one-command `docker compose up` for full stack; seed data; demo fixtures; Playwright e2e skeleton.
8. **Security**: do not expose service role; server-side only. Enforce RLS. Manage secrets via env files (sample `.env.example`) and GitHub Secrets.
9. **Deliver a minimal vertical slice**: Auth → upload IRMS file → background processing → result chart appears on dashboard → downloadable PDF report. Include tests.

**Deliverables**:

* Running dockerized dev stack
* README with bootstrap, env, scripts
* OpenAPI JSON & generated TS client
* Example agent run with dummy screenshots + structured JSON

Proceed step-by-step, committing after each major scaffold with clear messages.

---

# 2) implementation plan & technical reference

## A) high-level architecture

* **Frontend (apps/web)**: Next.js App Router, TS, Tailwind, shadcn/ui, TanStack Query, Zod, Recharts. Uses Supabase Auth (client) and calls FastAPI for privileged ops via Next.js API routes (server components or route handlers) to keep secrets safe.
* **Backend (apps/api)**: FastAPI REST + WebSocket/SSE; Celery/RQ workers for long tasks; Redis for queues; communicates with Supabase Postgres; handles LLM and “computer use” agents; produces structured outputs & artifacts.
* **Data (Supabase)**: Postgres + RLS + pgvector + Storage for files; realtime channels for UI updates; SQL migrations under version control.

## B) monorepo layout

```
/apps/web               # Next.js app
  app/                  # App Router pages & routes
  components/           # UI & forms (shadcn/ui)
  lib/                  # supabase client, fetchers, utils
  hooks/
  features/             # slices: clients, analyses, labbook, inventory, agents, knowledge
  tests/e2e/            # Playwright
/apps/api               # FastAPI app
  api/main.py
  api/routers/
  api/schemas/          # Pydantic
  api/services/         # IRMS | Agents | RAG | Reports
  api/db/               # SQLAlchemy models (if used), queries
  workers/              # Celery/RQ tasks
  tests/                # pytest
/packages/types         # OpenAPI-generated TS clients & DTOs
/packages/schemas       # JSONSchema, shared prompt & response specs
/infra/supabase         # SQL schema, policies, seeds, triggers
/infra/docker           # Dockerfiles, compose, devcontainer.json
```

## C) key database entities (Supabase)

* **users\_profile**(id, role, display\_name, email, …)
* **clients**(id, org\_name, contact, billing\_info, status)
* **projects**(id, client\_id, title, scope, status)
* **instruments**(id, type: ‘IRMS’|‘ICP-OES’|‘GC-IRMS’, serial, location, status)
* **runs**(id, instrument\_id, operator\_id, started\_at, completed\_at, metadata)
* **samples**(id, project\_id, code, matrix, received\_at, chain\_of\_custody)
* **analyses**(id, sample\_id, method, params\_json, status)
* **results**(id, analysis\_id, summary\_json, stats\_json, report\_url, created\_at)
* **attachments**(id, owner\_type, owner\_id, path, mime, meta)
* **payments**(id, client\_id, amount, currency, paid\_at, invoice\_id)
* **credits**(id, client\_id, amount, reason, issued\_at)
* **invoices**(id, client\_id, project\_id, total, status, pdf\_url)
* **labbook\_entries**(id, author\_id, date, category, text, related\_ids, attachments\[])
* **consumables**(id, sku, name, unit, min\_threshold, current\_qty)
* **inventory\_tx**(id, consumable\_id, delta, source, linked\_entry\_id, at)
* **agent\_runs**(id, type, status, params\_json, result\_json, started\_at, ended\_at)
* **agent\_artifacts**(id, run\_id, path, type, meta)
* **rag\_documents**(id, client\_id?, title, storage\_path, meta)
* **rag\_chunks**(id, doc\_id, chunk\_text, embedding vector(1536))
* **audit\_logs**(id, actor\_id, action, target, diff\_json, at)

**RLS starter guidance**

* `users_profile`: self-read; admin read-all.
* `clients/projects/samples/analyses/results`: role-based (analyst can read/write for their projects; clients read only their results if you later expose a client portal).
* `attachments`: readable by owners or project teammates; write via backend only.
* `payments/credits/invoices`: finance role + admin; analysts read summary but not PII billing where needed.
* `labbook_entries`: lab team read; author write; admin all.
* `inventory_tx/consumables`: lab team read; inventory manager write.
* `agent_runs/artifacts`: creator + admin.
* `rag_*`: restricted by role; embeddings table read via secure RPC.

## D) security & auth

* Supabase Auth (email/password or SSO). Server routes validate JWT; **never** expose service role key to the browser.
* All DB writes that require elevated privileges occur via FastAPI using server-side Supabase client (service role), with **additional** checks on `actor_id` + role.
* CSRF for form posts where applicable; strict CORS between web and api.
* Audit every mutation to `audit_logs`.

## E) background processing & realtime

* **Queue**: Celery (Redis broker) or RQ. Define job types: `irms_process`, `icp_oes_process`, `gc_irms_process`, `agent_run`, `rag_ingest`, `report_generate`.
* **Progress**: push job status to Redis pubsub or Postgres NOTIFY → Next.js consumes via a lightweight server proxy → TanStack Query invalidation for live UI.
* **Files**: upload to Supabase Storage; signed URLs for downloads; lifecycle policies for cold storage if needed.

## F) AI & “computer use” agents

* **Multimodal LLM** via OpenAI API. Enforce structured JSON outputs with a **strict JSON schema** (Pydantic model exported to JSONSchema). Validate on server; reject if not compliant.
* **Computer use**:

  * Start with Playwright (browser automation) to fetch remote SCADA/DCS web UIs safely.
  * If Windows desktop is required, add an RDP/VNC agent on the remote machine that captures screenshots on demand and saves to a shared folder (watched by the worker) or streams via a tiny agent service (Flask/FastAPI) authenticated with mTLS or signed tokens.
  * Normalize screenshots; OCR (Tesseract or PaddleOCR) to extract valve states/log text; send images+text context to the LLM.
* **Safety**: dry-run mode, whitelisted actions, role prompts, and reversible commands; store action logs + screenshots per step.

**Structured output example (Pydantic/JSONSchema)**

```json
{
  "instrument": "IRMS",
  "timestamp": "2025-09-23T18:21:00Z",
  "observations": [
    {"name": "Valve_A", "state": "open", "confidence": 0.98},
    {"name": "Valve_B", "state": "closed", "confidence": 0.95}
  ],
  "alerts": [{"level": "warning", "message": "Carrier gas pressure low"}],
  "recommendations": ["Inspect regulator; schedule calibration check"]
}
```

## G) IRMS processing port

* Wrap your existing Python IRMS pipeline as a pure library module (no I/O in core functions).
* Expose a FastAPI route that:
  (1) accepts files + params → stores in Storage → enqueues `irms_process(job_id)`.
  (2) worker downloads file, runs pipeline, writes `results` (JSON) and generated plots (PNG/PDF) → updates `results` + `attachments`.
  (3) emits progress events and final status.
* Plotting: matplotlib + exported PNGs for dashboard thumbnails + vector PDF for reports.
* Calibration: persist calibration sets & versioning; tie results to a calibration snapshot for reproducibility.

## H) ICP-OES & GC-IRMS (future-proofing)

* Define `method` templates and per-instrument parsers; abstract an “Analysis” interface: `parse_raw() → normalize() → compute() → QC() → serialize()`.
* Store raw vendor files in Storage; normalization tables accommodate instrument differences.

## I) RAG knowledge & consultant chatbot

* **Ingestion**: PDF → chunk (e.g., semantic/heading-aware) → embed (pgvector) → store.
* **Context mixers**: retrieval combines: doc chunks, relevant client/project notes, instrument logs (last N hours), and agent latest run summary.
* **Policies**: filter context by user role & project access before retrieval.
* **Chat endpoint**: server-side only; streams tokens; caches last turn contexts for continuity; attach citations (doc ids + page ranges).

## J) frontend UX notes (shadcn/ui)

* Global shell: left sidebar (sections), top bar (search, quick actions, user), toasts.
* **Dashboard**: cards for instrument status, latest runs, inventory alerts, unpaid invoices, recent agent alerts; clickthrough to details.
* **Clients**: table with search/filter, inline status, drilldown to projects, results, invoices, payments.
* **Analyses**: upload → processing timeline → QC flags → charts (Zoomable), export menu (CSV, PDF).
* **Lab Book**: journal timeline; filters by category; create entries; attach photos/files; link to inventory transactions.
* **Inventory**: consumables table; delta actions; low-stock badges; import/export CSV.
* **Agents**: run form (task preset, parameters), live log, screenshots carousel, final JSON view + “create report” button.
* **Knowledge**: doc manager (upload, parse status), search, chat with citations.

## K) reporting

* Server-side PDF generation (WeasyPrint/ReportLab)—templated with metadata, plots, tables, signatures.
* Persist report PDFs to Storage; link to invoices if billable.

## L) CI/CD & quality

* **GitHub Actions**: lint (ruff/mypy/eslint), test (pytest, Playwright), type-gen (OpenAPI → TS), Docker build, push.
* Pre-commit hooks: black/ruff/mypy/isort + eslint/prettier.
* Version your DB with SQL migration files; use `supabase db push` locally.

## M) observability

* Structured logs (loguru or stdlib JSON); request ids; error reporting (Sentry).
* Health endpoints for web/api/workers.
* Metrics: Prometheus endpoints in FastAPI; simple counters for jobs, durations, failures.

## N) data governance & compliance

* Access controls via RLS and roles.
* PII minimization in logs.
* Backups: daily DB & Storage; test restores quarterly.
* Reproducibility: versioned calibrations & method configs stored per run.

## O) milestone plan

1. **Week 1–2**: Monorepo, Docker dev, Auth, DB schema v1, OpenAPI contracts, basic pages.
2. **Week 3–4**: IRMS vertical slice (upload → process → charts → PDF).
3. **Week 5–6**: Clients/Projects/Invoices + payments/credits; RLS hardening; audit logs.
4. **Week 7–8**: Agents MVP (web UI capture + LLM structured output) + dashboard widgets.
5. **Week 9–10**: Lab Book + Inventory integration & alerts.
6. **Week 11–12**: RAG consultant, doc ingestion, chat with citations.
7. **Hardening**: tests, perf, security review, backup/restore runbook.

## P) recommendations & gotchas

* Keep **all** LLM calls server-side; validate outputs against JSONSchema before persisting.
* Treat remote “computer use” as hazardous: implement a simulation mode, action allow-lists, and mandatory screenshots + logs.
* Start simple with Playwright scraping of known dashboards before full desktop control.
* For IRMS/ICP/GC data, push for **idempotent** parsers: same input → same output & hash.
* Prefer **signed Storage URLs** with short TTL; never leak bucket paths without auth.
* Generate your TS client from FastAPI OpenAPI so FE/BE stay in lockstep.
* Add “seed project” with dummy data so demos work on day 1.

---

if you want, i can also produce: (a) the first SQL migration with base tables & RLS skeleton, (b) the docker-compose for web/api/redis/supabase-local, and (c) the base FastAPI routers + Next.js routes with example forms.
