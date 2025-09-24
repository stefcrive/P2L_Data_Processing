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
2. Start stack: `make up` (web:3000, api:8000, redis, pgvector).
3. Open `http://localhost:3000`, sign-in via magic link (Supabase Email OTP).
4. Upload an IRMS file from Dashboard to trigger a background job (stubbed).

Testing and CI:
- Run API tests: `make test`
- Lint/format: `make lint` / `make fmt`
- Pre-commit: `pip install pre-commit && make precommit-install`
