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
python -m uvicorn services.irms_api.api.main:app --reload --port 8100
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

The frontend calls the same-origin `/api/irms` route. Next.js proxies that route to `http://127.0.0.1:8100` unless `IRMS_API_PROXY_TARGET` is set. The dedicated default avoids collisions with other local applications that commonly use port `8000`.

## Scientific Results Assistant

The dashboard includes a full-page assistant at `/assistant` and a floating assistant on the other dashboard pages. It can inspect session metadata, measurement snapshots, cycle-level observations, processed-result fields, diagnostic flags, calibration/processing configuration, and session event logs through bounded read-only tools. The chat composer also accepts `.xls` and `.xlsx` attachments for temporary, read-only inspection and deterministic key/column comparisons against the active platform session; attachments are not imported into the session.

Set the OpenAI API key persistently for your Windows user before starting the app:

```powershell
setx OPENAI_API_KEY "your-api-key"
```

Alternatively, open **Settings → OpenAI connection** and enter the key there. On Windows, the backend saves it as the current user's `OPENAI_API_KEY` environment variable, so it remains available after app restarts. The key is never returned by the status API or written to browser storage, application files, or chat history.

Optional settings:

- `IRMS_CHAT_MODEL` selects the server-side allowlisted model name (default `gpt-5.6-terra`).
- `IRMS_CHAT_MAX_FILES` limits Excel attachments per assistant request (default `5`).
- `IRMS_CHAT_MAX_UPLOAD_BYTES` limits the combined assistant attachment size (default `26214400`, or 25 MB).
- `IRMS_PROCESSING_ENVIRONMENT` labels responses with the current processing mode (default `local`).
- `IRMS_API_PROXY_TARGET` points the Next.js chat route and IRMS proxy at a non-default backend URL.

Chat requests use the OpenAI Responses API with strict function schemas, sequential tool calls, a six-round loop bound, per-tool and per-request evidence budgets, and provider storage disabled. The browser receives the same redacted evidence supplied to the model. Conversation messages are shared between the two chat surfaces through local browser storage.

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
