# API and Data Contracts

## Session Storage Layout

Each server-managed session lives under the backend session root:

- `metadata.json`: canonical session metadata, calibration state, processing config, edit state
- `snapshot.csv`: canonical stored measurement dataframe
- `cycles_snapshot.csv`: canonical stored cycle dataframe
- `events.jsonl`: append-only event log
- `uploads/`: raw uploaded workbooks

## Primary API Endpoints

| Endpoint | Purpose | Payload | Returns |
| --- | --- | --- | --- |
| `POST /sessions/import` | Create a session from uploaded workbooks | multipart file upload | `ImportResult` |
| `POST /sessions/{sessionId}/append` | Append additional workbooks into the current session | multipart file upload | `ImportResult` |
| `GET /sessions/{sessionId}` | Session summary, counts, preview rows | none | `SessionSnapshot` |
| `GET /sessions/{sessionId}/diagnostics` | Diagnostics plots | `color_param` query | `ChartBundle` |
| `GET /sessions/{sessionId}/calibration/workspace` | Read the saved calibration workspace | none | `CalibrationWorkspace` |
| `POST /sessions/{sessionId}/calibration/workspace` | Build a preview calibration workspace without mutating the session | `CalibrationConfig` | `CalibrationWorkspace` |
| `POST /sessions/{sessionId}/calibration/run` | Run calibration and store coefficients/linearity fits | `CalibrationConfig` | `SessionSnapshot` |
| `GET /sessions/{sessionId}/calibration/charts` | Calibration figures | `color_param` query | `ChartBundle` |
| `GET /sessions/{sessionId}/processing/workspace` | Canonical processing workspace read endpoint | none | `ProcessingWorkspace` |
| `POST /sessions/{sessionId}/processing/config` | Persist the full processing workspace config and refresh workspace | `ProcessingWorkspaceConfig` | `ProcessingWorkspace` |
| `POST /sessions/{sessionId}/processing/edit` | Apply a processing edit and refresh workspace | `EditAction` | `ProcessingWorkspace` |
| `POST /sessions/{sessionId}/processing/cycle-diagnostics` | Load cycle diagnostics for one selected target | `CycleDiagnosticsRequest` | `CycleDiagnosticsPayload` |
| `GET /sessions/{sessionId}/processing/charts` | Compatibility endpoint for legacy chart consumers | none | `ChartBundle` |
| `POST /sessions/{sessionId}/exports/dataset` | Build the dataset/client-output workbook from the processing pipeline | `ExportRequest` | Excel binary |

## Core Models

### `SessionSnapshot`

- `session_id`
- `created_at`
- `updated_at`
- `source_files`
- `row_count`
- `cycles_row_count`
- `errors`
- `calibration`
- `processing`
- `autosave`
- `preview`

### `ProcessingWorkspaceConfig`

- `selected_identifier`
- `x_axis_option`
- `color_param`
- `z_axis`
- `signal_range`
- `leak_range`
- `d13c_range`
- `d18o_range`
- `sigma_level_data`
- `overlays.show_statistical_outliers`
- `overlays.show_range_outliers`
- `overlays.show_saturated_collectors`
- `overlays.show_saturated_samples`
- `overlays.show_failed_samples`
- `manual_linearity_override.enabled`
- `manual_linearity_override.d13_per_10v`
- `manual_linearity_override.d18_per_10v`
- `export.include_outliers`
- `export.selected_ids`
- `export.interpolate_outliers`
- `export.client_name`
- `export.comment_map`

### `CalibrationConfig`

- `selected_standards`
- `calibration_type`
- `sigma_level`
- `iqr_multiplier`
- `independent_isotope_outliers`
- `color_param`
- `z_axis`
- `precision_date_range`
- `linearity.apply`
- `linearity.use_diff_intensity`
- `linearity.manual_override_enabled`
- `linearity.manual_d13_per_10v`
- `linearity.manual_d18_per_10v`

### `CalibrationWorkspace`

- `session_id`
- `config`
- `available_values`
  - `standards`
  - `color_params`
  - `z_axis_options`
  - `min_date`
  - `max_date`
- `figures`
  - `VPDB(13C)`
  - `VSMOW(18O)`
  - `calibration_3d`
- `linearity_figures`
  - `d13_raw`
  - `d13_corrected`
  - `d18_raw`
  - `d18_corrected`
- `precision_summaries[]`
- `standard_sections[]`
- `linearity_fits`

### `EditAction`

- `action`
  - `set_value`
  - `offset`
  - `interpolate`
  - `reset_to_original`
  - `reset_all`
  - `set_outlier_override`
- `targets[]`
  - `row_label`
  - `isotope_key`
- `value`
- `offset`
- `is_outlier`

### `ProcessingWorkspace`

- `session_id`
- `config`
- `summary`
- `available_values`
- `overview_figures`
  - `processing_3d`
  - `d13_summary`
  - `d18_summary`
  - `crossplot`
- `species_sections[]`
  - `species`
  - `identifier_figures[]`
    - `identifier`
    - `d13c`
    - `d18o`
    - `has_calibrated_d13c`
    - `has_calibrated_d18o`
  - `outlier_tables[]`
- `outlier_tables[]`
- `edit_state`
- `export_state`

### `CycleDiagnosticsPayload`

- `session_id`
- `target`
  - row identity, isotope, identifier info, current/original value, effective outlier flag
- `inline_summary`
- `figure`
- `table`
- `cycle_mean`
  - `mean`
  - `valid_mean`
  - `valid_cycles`
  - `method`
  - `prev_neighbor`
  - `next_neighbor`
  - `reason`

### `ExportRequest`

- `include_outliers`
- `selected_ids`
- `interpolate_outliers`
- `client_name`
- `comment_map`

## Processing Contract Rules

1. `snapshot.csv` is the only canonical stored measurement dataframe.
2. Manual linearity override is a derived processing/export transform and must never overwrite `snapshot.csv`.
3. Edit actions mutate stored raw isotope values in `snapshot.csv`, then recompute collector status and calibrated columns using persisted calibration metadata.
4. Edited rows are excluded from automatic statistical/range outliering exactly as the backend `edit_state` rules define.
5. Manual outlier overrides must affect charts, outlier tables, diagnostics, and export.
6. Frontend chart display toggles such as raw-only and hide-calibrated are client-only and must not be written into session metadata.

## Frontend State Boundary

Server-owned state:

- dataframe mutations
- calibration outputs
- processing config
- export config
- manual linearity override config
- edited rows and original-value map
- manual outlier overrides
- cycle diagnostics payloads
- export workbook generation

Client-only state:

- active chart selections
- active selection index / navigation
- open/closed species accordions
- raw-only toggles
- hide-calibrated toggles
- transient form edits before saving config
