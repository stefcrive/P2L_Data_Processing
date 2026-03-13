# Session State Map

This file is the current migration map from `st.session_state` to the backend session store plus the web client state.

## Server-Owned Scientific State

These values must live in `metadata.json` and/or the stored session CSVs because they affect calculations, exports, or resume behavior.

| Streamlit key | Current role | Backend target | Reset rule |
| --- | --- | --- | --- |
| `df` | Active processed dataframe used by all tabs | `snapshot.csv` | Replaced on new session/discard |
| `df_cycles_source` | Canonical cycle-level dataframe | `cycles_snapshot.csv` | Replaced on new session/discard |
| `selected_ids` | Export identifier scope | `processing.config.export.selected_ids` | Reset on new session |
| `comment_replacements` | Species rename map for client output | `processing.config.export.comment_map` | Reset on new session |
| `client_name` | Client output naming | `processing.config.export.client_name` | Reset on new session |
| `edited_delta_rows` | Edited-row registry | `edit_state.edited_rows` | Cleared by reset flows |
| `original_delta_values` | Original raw isotope values for edited points | `edit_state.original_delta_values` | Cleared row-by-row or by reset-all |
| `manual_outlier_overrides` | Explicit force-outlier / force-keep map | `edit_state.manual_outlier_overrides` | Reset on new session or discard |
| `include_outliers` | Export include/exclude toggle | `processing.config.export.include_outliers` | Reset on new session |
| `interpolate_outliers_export` | Export interpolation toggle | `processing.config.export.interpolate_outliers` | Reset on new session |
| `selected_standards` | Active calibration standards | `calibration.selected_standards` | Reset on new session |
| `calibration_type` | Standard outlier method | `calibration.config.calibration_type` | Reset on new session |
| `sigma_level` | Calibration sigma threshold | `calibration.config.sigma_level` | Reset on new session |
| `irq_multiplier` | Calibration IQR multiplier | `calibration.config.iqr_multiplier` | Reset on new session |
| `outliers_independence` | Independent isotope handling | `calibration.config.independent_isotope_outliers` | Reset on new session |
| `calibration_coefficients` | Affine calibration coefficients | `calibration.coefficients` | Recomputed on calibration run |
| `linearity_fits` | Stored linearity fit parameters | `calibration.linearity_fits` | Recomputed on calibration run |
| `apply_linearity_toggle` | Calibration linearity enable | `calibration.config.linearity.apply` | Reset on new session |
| `linearity_use_diff_intensity_44` | Canonical intensity basis for linearity | `calibration.config.linearity.use_diff_intensity` | Reset on new session |
| `tab3_manual_linearity_override_enabled` | Processing manual linearity enable | `processing.config.manual_linearity_override.enabled` | Reset on new session |
| `tab3_manual_linearity_d13_per10v` | Processing d13 slope override | `processing.config.manual_linearity_override.d13_per_10v` | Reset on new session |
| `tab3_manual_linearity_d18_per10v` | Processing d18 slope override | `processing.config.manual_linearity_override.d18_per_10v` | Reset on new session |
| `signal_range` | Processing signal filter | `processing.config.signal_range` | Reset on new session |
| `leak_range` | Processing leak filter | `processing.config.leak_range` | Reset on new session |
| `d13c_range` | Processing d13 filter | `processing.config.d13c_range` | Reset on new session |
| `d18o_range` | Processing d18 filter | `processing.config.d18o_range` | Reset on new session |
| `sigma_level_data` | Processing sigma threshold | `processing.config.sigma_level_data` | Reset on new session |
| `autosave_source_files` | Uploaded workbook spec list | `source_files` | Recomputed on import/append |
| `imported_file_specs` | Workbook metadata list | `source_files` | Recomputed on import/append |

## Server-Owned Operational State

| Streamlit key | Current role | Backend target | Reset rule |
| --- | --- | --- | --- |
| `file_processed` | Gate for upload vs append flow | Derived from session existence | Derived only |
| `autosave_log_path` | Autosave log location | `events.jsonl` | Recreated per session |
| `autosave_snapshot_path` | Snapshot location | `snapshot.csv` | Recreated per session |
| `autosave_save_dir` | Autosave directory | Session root directory | Recreated per session |
| `autosave_error` | Last autosave warning | `autosave.error` | Cleared on successful write |
| `autosave_event_count` | Event log count | `autosave.event_count` | Recomputed |
| `autosave_initialized_at` | Autosave creation timestamp | `autosave.initialized_at` | Recreated per session |
| `autosave_meta_path` | Metadata location | `metadata.json` | Recreated per session |
| `autosave_resumed` | Resume flag | `autosave.resumed` | Derived on session load |
| `autosave_session_token` | Stable resume token | `session_id` | Recreated per session |

## Client-Only Display State

These values stay in the Next.js app and must not be persisted into scientific metadata.

| Streamlit/UI state | Web target | Persistence |
| --- | --- | --- |
| active chart selections | React state in processing page | In-memory |
| active selection index / prev-next navigation | React state in processing page | In-memory |
| raw-line-only toggles | `sessionStorage` per session | Client only |
| hide-calibrated toggles | `sessionStorage` per session | Client only |
| species accordion open/closed state | React state or native `<details>` state | Client only |
| transient config edits before Apply | React state | In-memory |
| upload chooser nonce | Local component state | In-memory |
| confirm-reset / confirm-discard dialogs | Local component state | In-memory |
| chart rerender nonces | Local component state | In-memory |

## Compatibility Wrappers Still in Streamlit

The Streamlit adapter now delegates these names to the extracted backend processing package through compatibility wrappers:

- `_get_isotope_columns`
- `_partial_saturation_isotope_masks`
- `_range_outlier_mask`
- `_interpolate_outliers_by_identifier2`
- `_apply_manual_outlier_overrides`
- `_get_cycles_for_selected_point`
- `_compute_cycle_mean_for_target`

## Migration Rules

1. If a value changes scientific results, export content, or resume behavior, it belongs on the server.
2. If a value only changes layout, expansion state, selection state, or display toggles, it stays client-only.
3. Streamlit-only keys should only remain where a compatibility wrapper is still required during the strangler migration.
