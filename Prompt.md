**Goal:** bootstrap a monorepo for a Next.js (TypeScript) + Tailwind + shadcn/ui frontend, a Python FastAPI backend, and a Supabase Postgres (with RLS, pgvector, Storage) data layer. The app is a lab dashboard + control surface for:

* AI agents that can control a remote workstation via multimodal llm agent that can perform computeruse, capture screenshots of valves/logs, run multimodal LLM analysis on the taken screenshots → structured output → technical reports & dashboard widgets. 
* IRMS results processing (existing Python logic will be ported/adapted).
* Future ICP-OES and GC-IRMS results processing sections.
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


Youll have access to supabase for database manipulation and sql script execution, and git MPC servers for version control. Proceed step-by-step, committing after each major scaffold with clear messages.