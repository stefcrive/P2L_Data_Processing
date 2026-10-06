# Implementation validation

Validated locally on Windows on 2026-10-05, on branch `feature/irms-metrological-platform`. Existing uncommitted Results Station changes were retained.

## Automated checks

- 33 metrology tests pass. Coverage includes numerical regression and covariance, uncertainty interpretation and double counting, two-anchor propagation versus seeded Monte Carlo, shared correction-coefficient propagation and cancellation, multi-row workbook headers and mV conversion, all sample roles, immutable source bytes, method transitions, independent QC, stale evaluations, scientific approvals, historical control signals, timezones and acquisition-time qualification, interventions, release snapshots, persistence and stored reports.
- TypeScript checking passes. The Next.js production build succeeds, including `/metrology`.
- The full backend suite ran 257 tests: 255 passed and two failed in diagnostic tab ordering. Both failures were reproduced from an archive of untouched `HEAD` at `4cc76b1c`, separately from the working tree:
  - `ProcessingApiTests.test_diagnostics_endpoint_includes_diff_signal_vs_isotope_scatter_plots`
  - `ProcessingApiTests.test_diagnostics_grid_uses_calibration_selected_standards_and_visible_gridlines`

Those assertions expect `d13C` as the first diagnostic group, while the baseline implementation returns `Multivariate Overview` first. The metrology implementation does not change that behavior.

Commands, from the repository root:

```powershell
.venv/Scripts/python.exe -m unittest services.irms_api.tests.test_metrology -v
.venv/Scripts/python.exe -m unittest discover -s services/irms_api/tests
```

From `apps/web`:

```powershell
npx.cmd tsc --noEmit
$env:NEXT_DIST_DIR = '.next-metrology-verify'
npm.cmd run build
```

## Supplied workbook reconciliation

| Workbook | Analyses | Numbered cycles | Composition | Observations |
| --- | ---: | ---: | --- | --- |
| Petrobras DGL-2024 series 11 all info.xlsx | 45 | 360 | 9 SHP2L QC; 36 unknowns | No explicit masses; exported Running status requires review; two oxygen internal SD failures |
| Qtegra_mock_metrological_carousel.xlsx | 45 | 360 | NBS18, NBS19, SHP2L; 60/100/140 µg; five aliquots per combination | Explicit mass/replicate metadata; seven oxygen internal SD failures; synthetic qualification/release block |

Both imports preserve their original files. Each analysis contains eight sample observations including Pre, with the final reference-only cycle excluded from sample-intensity averaging. Mock certificate examples in `__MOCK_INFO__` are not copied into the material library. No NBS18 or NBS19 certificate values were provisionally assigned.

The local `.data/metrology` workspace contains both imports and a draft qualification plan derived from the mock carousel. It has no active approved method, no accepted laboratory correction and no released client result. The browser verification saved the unchanged draft and re-evaluated the mock, each with an explicit Codex audit reason. An unapproved synthetic review dossier is available under Reports.

## Browser and report checks

The dedicated PowerShell launcher started the API on 8200 and frontend on 3200. Browser checks covered overview, method editing/saving, qualification, raw measurements, diagnostics, re-evaluation, duplicate upload detection and qualification report generation/download. No page errors were reported. Desktop and 390-pixel mobile layouts were inspected, including table containment and navigation.

Client, qualification and historical PDFs were generated from isolated synthetic fixtures. The rendered pages were inspected, and hashes confirmed that downloads return their archived bytes. The supplied mock's five-page review dossier was downloaded through the frontend proxy and checked for its unapproved status and synthetic-data blocker. This document is an implementation test record; laboratory scientific approval remains a separate workflow.

## Operational limits

This is a local laboratory application. Reviewer names provide audit attribution, not authenticated electronic signatures. The accepted secondary equation is one independently estimated centered linear effect per isotope. Memory correction, multiple dependent effects and nonlinear equations require additional validated measurement models. See [the operator workflow](metrology-workflow.md) for supported assumptions and the fields needed before approving a method.
# Results Station reorganization verification

The station now opens at Metrology overview and uses separate routes for the
metrology navigation. Results sessions persist client/project/group metadata and
pin their method, qualification and Qtegra processing provenance. Existing imports
are backfilled without changing their scientific records.

Verification performed on 2026-10-05:

- 42 metrology/demo/session tests passed. New session coverage exercises reopening,
  duplicate import ownership, foreign-group rejection, external normalization
  retention, uncertainty export, frozen release attribution and immutable exports.
- The plotting bridge was checked against an independently configured fixture.
  It opens the correct qualification workbook, selects NBS18/NBS19, and passes the
  session's versioned assigned values to the existing calibration components.
  Applying another calibration through that consultation copy returns HTTP 409.
- Full backend suite: 266 tests, 264 passing. The two existing diagnostic-grid
  ordering failures remain; their earlier baseline reproduction is documented below.
- Production build, TypeScript checks and five localization tests passed.
- Browser checks covered overview ordering, session summary, existing diagnostics
  and processing plots, qualification selection, editable draft-method sections,
  Portuguese navigation, and a 390-pixel viewport without horizontal page overflow.
- A stored Core A ZIP export from the demo ready-for-review session contains ten
  unknown samples with CSV results and the calculation dossier. Other sample groups
  are absent from the result rows and per-sample calculation records.

The demo remains synthetic. Reused acquisition controls are exploratory; approved
method coefficients and reportable results belong to the metrological evaluation.

