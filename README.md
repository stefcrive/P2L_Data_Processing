# IRMS Lab Monorepo

Monorepo for a lab dashboard and control surface consisting of:

- Next.js (TypeScript) frontend with Tailwind + shadcn/ui
- FastAPI backend (Python 3.11+)
- Supabase Postgres (RLS, pgvector, Storage)

Structure:

```
/apps
  /web           # Next.js app (App Router)
  /api           # FastAPI app
/packages
  /ui            # shared UI primitives (optional)
  /types         # shared TypeScript contract types
  /schemas       # JSONSchema & Pydantic model definitions
/infra
  /supabase      # db schema, policies, seed, migrations
  /docker        # docker & compose templates
```

Existing standalone scripts (e.g., `IRMS_output_analyzer.py`) will be integrated into the FastAPI service.

Quick start (dev):

1. Copy `.env.example` to `.env` and set values (Supabase URL/anon key, etc.).
2. Install API deps: `pip install -r apps/api/requirements.txt` (now includes pandas/numpy for IRMS processing).
3. Start stack: `make up` (web:3000, api:8000, redis, pgvector) or run services locally:
   - API: `make api`
   - Worker: `make worker`
   - Web: `make web`
4. Open `http://localhost:3000`, sign-in (or set `NEXT_PUBLIC_BYPASS_AUTH=true` in `apps/web/.env.local`).
5. Navigate to Analyses and upload an IRMS file (`.csv`, `.txt`, `.xls`, `.xlsx`). A background job processes the file and displays a summary.

IRMS Results Processing:
- Endpoint: `POST /v1/irms/process` (multipart form field `file`).
- Job status: `GET /v1/jobs/{task_id}` or WebSocket `/v1/ws/jobs/{task_id}`.
- Worker queue: `irms` (see `make worker`).

Run without Docker and without Celery (pure FastAPI):
- Set `USE_CELERY=false` in `.env` (API will process the upload synchronously and return the summary immediately).
- Start only the API and Web apps (`make api` and `make web`). Redis/worker are not required in this mode.
- The frontend detects inline results and displays them without polling.

Testing and CI:
- Run API tests: `make test`
- Lint/format: `make lint` / `make fmt`
- Pre-commit: `pip install pre-commit && make precommit-install`
