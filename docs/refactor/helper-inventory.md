# Helper Inventory

This inventory is organized by target backend module, not by the original file order.

## Data Import / Session

Target module: `services/irms_api/domain/import_session.py`

- `_safe_filename_fragment`
- `_numeric_or_none`
- `_normalize_upload_spec`
- `_build_uploaded_file_spec`
- `_upload_spec_signature`
- `_next_append_index`
- `_append_rows_preserve_existing_index`
- `_append_cycles_source`
- `_load_uploaded_workbooks`

Target module: `services/irms_api/domain/shared/dataframe.py`

- `_parse_new_table_layout`
- `_canonicalize_header_columns`
- `_coalesce_duplicate_columns`
- `_standardize_isotope_columns`
- `_find_column`
- `_extract_numeric`
- `_split_label_species`
- `extract_info_values`
- `_apply_cycle_averages`
- `_find_cycle_intensity_columns`
- `_pick_cycle1_signal_columns`
- `_ensure_cycle1_signal_difference_columns`

## Diagnostics

Target module: `services/irms_api/domain/diagnostics/core.py`

- `create_diagnostic_plots`

Target module: `services/irms_api/domain/shared/plotting.py`

- `_build_date_colorbar_ticks`
- `_prepare_color_values`
- `_build_isotope_3d_scatter`

## Calibration

Target module: `services/irms_api/domain/standards.py`

- `load_standards`
- `normalize_standards_frame`
- `StandardsRepository`

Target module: `services/irms_api/domain/calibration/core.py`

- `identify_outliers`
- `_compute_sigma_stats`
- `identify_outliers_iqr`
- `get_true_value`
- `single_point_calibration`
- `double_point_calibration`
- `_filter_standards_remove_outliers`
- `_compute_linearity_fit`
- `_apply_linearity_correction`
- `_resolve_selected_linearity_intensity_column`
- `_resolve_linearity_intensity_column_for_fits`
- `_linearity_intensity_axis_label`
- `_resolve_linearity_reference_intensity`
- `_compute_calibration_coefficients`
- `calibrate_results`
- `create_calibration_plots`

## Data Processing / Export

Target module: `services/irms_api/domain/processing/core.py`

- `_partial_saturation_isotope_masks`
- `_signal_in_range_mask`
- `_signal_out_of_range_mask`
- `_range_outlier_mask`
- `_get_isotope_columns`
- `_estimate_calibration_affine`
- `_refresh_collector_status_after_delta_edit`
- `_refresh_calibrated_after_delta_edit`
- `_interpolate_outliers_by_identifier2`

Target module: `services/irms_api/domain/processing/export.py`

- `_sanitize_filename`
- `_build_client_filename`
- `build_dataset_workbook_bytes`

## Shared Plot Metadata and Chart Support

Target module: `services/irms_api/domain/shared/plotting.py`

- `_exclusive_outlier_masks`
- `_compose_label_series`
- `_build_delta_point_customdata`
- `_build_cycle_std_lookups`
- `_build_plotly_error_bar`
- `_build_plotly_error_bar_for_df`
- `_apply_cycle_std_error_bars`

## Remaining in Streamlit Adapter for Now

These are still coupled to `streamlit.session_state` or the old rendering workflow and remain in `IRMS_output_analyzer.py` until the next extraction pass:

- autosave/session-resume helpers
- chart-selection token helpers
- in-chart point editor renderer
- cycle diagnostics drawer renderer
- manual outlier override UI handlers
- large per-species chart rendering blocks
- Streamlit download button wrapper around export
