# Parity Checklist

Use this checklist before deleting any Streamlit behavior. The goal is exact functionality parity, not UI parity.

## Data Import

- Import a legacy-layout workbook.
- Import a multi-row "New Table" workbook.
- Import multiple workbooks into one session.
- Append additional workbooks without losing existing session state.
- Preserve cycle-level rows for downstream diagnostics and editing.
- Preserve identifier, species, label, collector status, and cycle-derived fields.
- Persist source workbook metadata and the session event log.

## Diagnostics

- Render the diagnostics subplot matrix.
- Render the diagnostics 3D chart.
- Support all color-by options, including date-derived coloring.
- Preserve standards vs non-standards marker behavior.

## Calibration

- Run single-point calibration.
- Run double-point calibration.
- Run Z-score standards filtering.
- Run IQR standards filtering.
- Preserve independent isotope handling.
- Preserve linearity correction using sample intensity.
- Preserve linearity correction using sample-reference difference intensity.
- Persist calibration coefficients and linearity fits.

## Processing Capability Matrix

| Capability | Backend workspace/API | Web UI | Streamlit compatibility oracle |
| --- | --- | --- | --- |
| signal, leak, d13C, d18O filters | `ProcessingWorkspaceConfig` | sidebar filters | yes |
| sigma outlier threshold | `sigma_level_data` | sidebar input | yes |
| summary counts and metrics | `ProcessingSummary` | summary cards + metric table | yes |
| overview 3D chart | `overview_figures.processing_3d` | overview grid | yes |
| d13 summary chart | `overview_figures.d13_summary` | overview grid | yes |
| d18 summary chart | `overview_figures.d18_summary` | overview grid | yes |
| crossplot | `overview_figures.crossplot` | overview grid | yes |
| per-species d13/d18 charts | `species_sections[].identifier_figures[]` | species accordions | yes |
| statistical/range/status overlays | `charts.py` + `overlays` config | chart toggles | yes |
| raw-only display | client-only display state | chart toggle | yes |
| hide-calibrated display | client-only display state | chart toggle | yes |
| manual linearity override | derived working frame | sidebar controls | yes |
| single-point set value | `POST /processing/edit` | editor panel | yes |
| single-point offset | `POST /processing/edit` | editor panel | yes |
| single-point interpolate | `POST /processing/edit` | editor panel | yes |
| multi-point offset | `POST /processing/edit` | editor panel | yes |
| multi-point interpolate | `POST /processing/edit` | editor panel | yes |
| reset selected | `POST /processing/edit` | editor panel | yes |
| reset all | `POST /processing/edit` | controls panel | yes |
| outlier force keep / force outlier | `set_outlier_override` | editor panel | yes |
| cycle diagnostics | `POST /processing/cycle-diagnostics` | diagnostics cards | yes |
| linearity preview in diagnostics | `POST /processing/cycle-diagnostics` | diagnostics controls | yes |
| outlier tables | `outlier_tables` + species tables | table panels | yes |
| dataset export without outliers | `POST /exports/dataset` | export button | yes |
| dataset export with outliers | `POST /exports/dataset` | export button | yes |
| interpolation-before-export | `POST /exports/dataset` | export toggle | yes |
| client output mapping | `comment_map` + `client_name` | export controls | yes |

## Editing and Recovery

- Persist edited-row tracking and original-value storage.
- Recompute calibrated columns after set/offset/interpolate/reset actions.
- Recompute collector status after edits.
- Exclude edited rows from automatic statistical/range outliering.
- Ensure manual outlier overrides affect charts, tables, diagnostics, and export.

## Export

- Export workbook without outliers and produce separate `Outliers` sheet.
- Export workbook including outliers and add `Outlier Types` labels.
- Export workbook with interpolation-before-export and preserve `Original ...` columns.
- Preserve `Statistics`, `Data`, and `Client Output` sheets.
- Preserve species rename mapping for client output.
- Exclude selected standards from client output.

## Session Behavior

- Resume a server-managed session after reload.
- Preserve calibration config and processing config after resume.
- Preserve edit state after append, calibrate, edit, and export.
- Preserve uploaded source-file metadata and event log.

## Acceptance Gate

- `python -m unittest discover services\irms_api\tests`
- `npm.cmd run build` in `apps/web`
- no remaining processing-only scientific logic exists solely in the web app
- Streamlit remains runnable and delegates compatible processing helpers to the extracted backend package
