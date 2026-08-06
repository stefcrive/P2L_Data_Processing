# IRMS Output Analyzer

The repository now contains two parallel application surfaces:

- `IRMS_output_analyzer.py`: the existing Streamlit application, now acting as the adapter during the refactor.
- `services/irms_api`: the extracted Python backend package and FastAPI entrypoint.
- `apps/web`: the new Next.js dashboard shell with sidebar navigation.

## Python Backend

Install backend dependencies:

```bash
pip install -r requirements.txt
```

Run the FastAPI app:

```bash
python -m uvicorn services.irms_api.api.main:app --reload
```

## Next.js Dashboard

Install frontend dependencies:

```bash
cd apps/web
npm install
```

Run the dashboard:

```bash
npm run dev
```

The frontend calls the same-origin `/api/irms` route. Next.js proxies that route to `http://127.0.0.1:8000` unless `IRMS_API_PROXY_TARGET` is set.

## Background Jobs

Workbook imports/appends, calibration runs, processing configuration/edit batches, and workbook exports use a bounded background worker pool. Progress is delivered through server-sent events, with polling fallback in the web client. Jobs that target the same session are serialized.

Optional environment settings:

- `IRMS_JOB_WORKERS` (default `2`): concurrent worker threads.
- `IRMS_JOB_QUEUE_SIZE` (default `24`): queued jobs beyond active workers.
- `IRMS_JOB_HISTORY_SIZE` (default `100`): retained terminal job records/artifacts.
- `IRMS_JOB_RETENTION_SECONDS` (default `3600`): completed-job retention.
- `IRMS_JOB_MAX_UPLOAD_BYTES` (default `536870912`): combined workbook upload limit per job.

The in-process registry is intended for the current single-backend deployment. A multi-instance deployment should replace it with a shared durable queue and artifact store while retaining the `/jobs` API contract.

## One-Command Startup

Use `start_app.bat` from repo root.

- Default (`start_app.bat`): development mode (`uvicorn --reload` + `next dev`).
- Production runtime (`start_app.bat --prod`): production mode (`uvicorn` + `next start`); the frontend rebuilds so its embedded API port always matches the backend.
- Optional ports: `start_app.bat --backend-port 8100 --frontend-port 3100`.
- If a requested/default port is already in use, the script automatically selects the next free port and prints the final URLs.
- Stop every process launched for this repository with `kill_app.bat`. Use `kill_app.bat --DryRun` to preview the targeted processes without stopping them.

## Streamlit Adapter

The legacy UI is still available while parity work continues:

```bash
streamlit run IRMS_output_analyzer.py
```

## Refactor Docs

Navigation docs for the ongoing extraction live in `docs/refactor`:

- `helper-inventory.md`
- `session-state-map.md`
- `contracts.md`
- `parity-checklist.md`
