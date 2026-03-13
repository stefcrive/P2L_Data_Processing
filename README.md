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

The frontend expects the API at `http://localhost:8000` unless `NEXT_PUBLIC_IRMS_API_URL` is set.

## One-Command Startup

Use `start_app.bat` from repo root.

- Default (`start_app.bat`): development mode (`uvicorn --reload` + `next dev`).
- Faster runtime (`start_app.bat --prod`): production mode (`uvicorn` + `next start`, auto-builds once if needed).
- Optional ports: `start_app.bat --backend-port 8100 --frontend-port 3100`.
- If a requested/default port is already in use, the script automatically selects the next free port and prints the final URLs.

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
