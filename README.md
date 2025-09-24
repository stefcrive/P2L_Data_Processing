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

Getting started steps will be added as scaffolding progresses.

