# Responsiveness and real-time review

## Scope and current architecture

The active application is a Next.js 15 / React 19 dashboard using TanStack Query and Plotly. It calls a FastAPI service that keeps session metadata and pandas dataframes on disk. Interactive reads are computed on request; long-running mutations use a bounded background job protocol.

The code already contained useful first steps: cached CSV reads, lazy Plotly loading, deferred chart rendering, React Query caching, and selective species chart generation. The main remaining constraint was that many UI interactions still rebuilt and transferred a complete processing workspace.

## Measured baseline

The review used saved session `28bcaa0878ca4ec5854ffb31f4ddf8c5` (771 result rows and 12,281 cycle rows). Before this pass:

| Operation | Server time | JSON payload |
| --- | ---: | ---: |
| Base processing workspace | 0.777 s | 0.383 MB |
| One open species section | 0.999 s | 0.641 MB |
| Repeated base workspace | 0.595 s | 0.383 MB |

Profiling attributed most cold request time to Plotly workspace construction. Per-point hover formatting alone created thousands of temporary one-row pandas Series. Opening or closing a species section also changed the frontend query key and recomputed overview figures, summaries, outlier tables, and species figures together.

## Foundation implemented

- Per-point hover formatting now uses scalar numeric coercion. This removes the largest avoidable allocation hotspot without changing chart content.
- Processing workspace responses use a bounded 16-entry LRU keyed by the session root, metadata/snapshot revisions, cycle snapshot revision, and requested species sections. Every session mutation invalidates its entries.
- Processing section queries retain the last workspace while a new section loads and pass React Query's abort signal to `fetch`, preventing stale browser responses from replacing current state.
- Draft edits use a batch endpoint. Up to 100 edits are applied in memory, then persisted and rebuilt once. The existing single-edit contract remains available.
- Workbook uploads expose live byte progress. After transfer completes, the UI explicitly changes to a server-processing state.
- Every HTTP response includes `Server-Timing` and `X-Process-Time-Ms`, making server latency visible in browser developer tools and suitable for later telemetry collection.
- `scripts/benchmark_processing.py` provides a repeatable local benchmark against real saved sessions.
- Base workspace and expanded species data now have separate response paths. The base response carries section metadata only; each open section fetches its charts independently.
- A revision-scoped `ProcessingWorkspaceContext` caches normalized configuration plus derived working, filtered, and unfiltered frames. Base overview and species chart builders reuse that context.
- Section queries are independently cached in React Query and FastAPI. Opening or closing one section no longer retransfers or replaces overview figures and other open sections.
- Imports/appends, calibration, processing configuration/edit batches, and exports now run through a bounded worker pool. Jobs for the same session are serialized to protect file-backed state.
- `/jobs/{id}/events` streams revisioned progress through SSE. The dashboard falls back to status polling if the stream is unavailable and presents operation phase, percentage, failures, and cancellation controls.
- Cancellation is cooperative until a job enters its explicitly non-cancellable persistence phase, preventing a cancelled label from being shown after durable state was already changed.
- Queued workbook uploads are staged to temporary disk instead of retained in process memory. Queue size, worker count, history, retention, and upload size are configurable.
- Export workbooks are retained as bounded, temporary disk artifacts rather than process-memory blobs and downloaded through `/jobs/{id}/download` after completion.

On the same session after these changes:

| Operation | Cold time | Cached time | Change |
| --- | ---: | ---: | ---: |
| Base processing workspace | 0.602 s | 0.0065 s | 22% faster cold; 99% faster repeated |
| One open species section | 0.599 s | 0.0112 s | 40% faster cold; 98% faster repeated |

After separating species responses, the focused BTS section is 0.258 MB instead of the former 0.641 MB combined workspace. It takes about 0.212 s when the base context is already available and 0.004 s from the section cache. A section requested before the base workspace takes about 0.345 s because it must also construct the shared context.

These are local measurements, not a production service-level objective. Run the benchmark on representative large workbooks before and after each optimization.

## Bottlenecks still present

1. Plotly payload size grows with every sample point and identifier chart. Add a payload budget, consider `scattergl`, and downsample display traces while preserving full-resolution edit targets server-side.
2. The job registry and artifacts are process-local. Multi-process or multi-host deployment requires a durable shared queue, persistent job records, and external artifact storage behind the existing job API.
3. Some scientific library calls cannot be interrupted mid-call, so cancellation occurs at safe checkpoints. Add deeper progress/cancellation checkpoints as domain functions are decomposed.
4. Session persistence rewrites complete CSV snapshots and then refreshes the dataframe cache. Move snapshots to Parquet/Arrow or another typed columnar format, and write atomically. Keep CSV only as an export format.
5. The processing, calibration, and diagnostics pages are very large client components. Split chart panels and controls into memoized feature components, then measure React commits with the Profiler before adding more state.
6. Outlier masks and derived frames are still recalculated several times while assembling a cold base workspace. Extend `ProcessingWorkspaceContext` with reusable category masks and lookup tables.
7. Add load tests at 1x, 5x, 10x, and 25x representative data volume. Track cold p50/p95, cached p50/p95, job queue wait, response bytes, browser long tasks, and peak backend memory.

## Suggested performance targets

- Cached UI interactions: p95 under 100 ms server time.
- Cold base workspace: p95 under 500 ms for 5x the current example dataset.
- Section expansion: visible loading state within one animation frame; first useful chart under 750 ms.
- No browser main-thread task longer than 100 ms during workspace hydration.
- All operations expected to exceed 1 second use the job/SSE progress protocol.
- Processing workspace responses stay below an explicit payload budget (start with 1 MB and revise from measurements).

## Verification commands

```powershell
python scripts/benchmark_processing.py --repeats 5
python -m pytest services/irms_api/tests -q
cd apps/web
npm.cmd run build
```
