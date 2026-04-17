from __future__ import annotations

import io
import os
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from ..domain.calibration.core import (
    _apply_isotope_line_offsets,
    _apply_linearity_correction,
    _apply_manual_linearity_override_to_standards,
    _compute_calibration_coefficients,
    _compute_linearity_fit,
    _filter_linearity_fit_input_by_max_intensity,
    _filter_standards_remove_outliers,
    _promote_linearity_corrected_raw_columns,
    _resolve_selected_linearity_intensity_column,
    _with_isotope_linearity_intensity_columns,
    calibrate_results,
    create_calibration_plots,
    identify_outliers,
    identify_outliers_iqr,
)
from ..domain.contracts import (
    CalibrationConfig,
    CalibrationOfficialValue,
    CalibrationOfficialValueDeleteResult,
    CalibrationOfficialValueUpsertRequest,
    CalibrationWorkspace,
    ClientOutputDuplicateCheckResponse,
    ChartBundle,
    CycleDiagnosticsPayload,
    CycleDiagnosticsRequest,
    EditAction,
    ExportRequest,
    ImportResult,
    ProcessingConfig,
    ProcessingExportConfig,
    ProcessingWorkspace,
    SessionSnapshot,
)
from ..domain.constants import CYCLE1_SIGNAL_SAMP44_COL
from ..domain.diagnostics.core import create_diagnostic_plots
from ..domain.calibration.workspace import build_calibration_workspace, normalize_calibration_config
from ..domain.import_session import (
    _append_cycles_source,
    _append_rows_preserve_existing_index,
    _build_session_name_from_source_files,
    _load_uploaded_workbooks,
)
from ..domain.processing.core import RangeConfig, _interpolate_outliers_by_identifier2
from ..domain.processing.cycles import build_cycle_diagnostics_payload, build_target_info
from ..domain.processing.edits import apply_edit_action
from ..domain.processing.export import (
    _build_client_output_frame,
    _round_client_output_columns,
    build_client_output_workbook_bytes,
    build_dataset_workbook_bytes,
    summarize_client_output_duplicates,
)
from ..domain.processing.outliers import (
    _partial_saturation_isotope_masks,
    _signal_in_range_mask,
    build_category_masks,
    build_outlier_type_labels,
    build_processing_summary,
    compute_statistical_outlier_masks,
)
from ..domain.processing.workspace import (
    _build_plot_frames,
    _derive_working_frame,
    _exclude_outliers_from_plot_base,
    _selected_processing_rows,
    build_processing_workspace,
    normalize_processing_config,
)
from ..domain.shared.dataframe import _ensure_cycle1_signal_difference_columns
from ..domain.shared.json_compat import to_json_compatible
from ..domain.shared.plotting import _build_isotope_3d_scatter
from ..domain.standards import StandardsRepository
from ..session_store import FileSessionStore

app = FastAPI(title="IRMS API", version="0.1.0")


def _cors_origins() -> list[str]:
    # Accept a comma-separated allow list via env var for deploys.
    raw = os.getenv("IRMS_API_CORS_ORIGINS", "").strip()
    if raw:
        origins = [value.strip() for value in raw.split(",") if value.strip()]
        if origins:
            return origins
    return ["http://localhost:3000", "http://127.0.0.1:3000"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

store = FileSessionStore()


class _NamedBytesIO(io.BytesIO):
    def __init__(self, content: bytes, name: str) -> None:
        super().__init__(content)
        self.name = name
        self.size = len(content)


def _to_session_snapshot(session_id: str) -> SessionSnapshot:
    return SessionSnapshot.model_validate(store.build_snapshot(session_id))


def _figure_json(fig: go.Figure | None) -> dict[str, Any]:
    return to_json_compatible(fig.to_plotly_json()) if fig is not None else {}


def _coerce_row_label(row_label: str, df: pd.DataFrame) -> Any:
    if row_label in df.index:
        return row_label
    try:
        numeric = int(row_label)
    except ValueError:
        numeric = row_label
    if numeric in df.index:
        return numeric
    raise HTTPException(status_code=404, detail=f"Unknown row label {row_label}")


def _load_processing_config(metadata: dict[str, Any]) -> ProcessingConfig:
    raw = metadata.get("processing", {}).get("config", {})
    return ProcessingConfig.model_validate(normalize_processing_config(raw if isinstance(raw, dict) else {}).model_dump())


_CALIBRATION_DERIVED_COLUMNS = (
    "d13C_calibrated",
    "d18O_calibrated",
    "d13C_calibrated_linearity_corrected",
    "d18O_calibrated_linearity_corrected",
    "d13C_linearity_corrected",
    "d18O_linearity_corrected",
)


def _drop_calibration_derived_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    removable_columns = [column for column in _CALIBRATION_DERIVED_COLUMNS if column in df.columns]
    return df.drop(columns=removable_columns, errors="ignore"), removable_columns


def _processing_apply_calibration_enabled(metadata: dict[str, Any]) -> bool:
    processing_meta = metadata.get("processing", {})
    if not isinstance(processing_meta, dict):
        return True
    return bool(processing_meta.get("apply_calibration", True))


def _set_processing_apply_calibration(metadata: dict[str, Any], enabled: bool) -> None:
    processing_meta = metadata.setdefault("processing", {})
    if not isinstance(processing_meta, dict):
        processing_meta = {}
        metadata["processing"] = processing_meta
    processing_meta["apply_calibration"] = bool(enabled)


def _processing_calibration_meta(metadata: dict[str, Any]) -> dict[str, Any]:
    if not _processing_apply_calibration_enabled(metadata):
        return {}
    calibration_meta = metadata.get("calibration", {})
    return calibration_meta if isinstance(calibration_meta, dict) else {}


def _processing_subset(df: pd.DataFrame, config: ProcessingConfig) -> pd.DataFrame:
    subset = df.copy()
    if config.selected_identifier != "All" and "Identifier 1" in subset.columns:
        subset = subset[subset["Identifier 1"] == config.selected_identifier].copy()
    return subset


def _build_interpolation_source_frame(
    df: pd.DataFrame,
    config: ProcessingConfig,
    calibration_meta: dict[str, Any] | None,
    edit_state: dict[str, Any] | None,
    target_row_tokens: set[str],
) -> pd.DataFrame:
    working_df = _derive_working_frame(df, config, calibration_meta=calibration_meta, edit_state=edit_state)
    if working_df.empty:
        return working_df

    def _source_to_raw_offset_with_missing_fill(raw_col: str) -> pd.Series:
        if raw_col not in working_df.columns or raw_col not in df.columns:
            return pd.Series(np.nan, index=working_df.index, dtype=float)
        raw_values = pd.to_numeric(df[raw_col], errors="coerce").reindex(working_df.index)
        source_values = pd.to_numeric(working_df[raw_col], errors="coerce").reindex(working_df.index)
        offsets = pd.to_numeric(source_values - raw_values, errors="coerce")
        missing_raw = raw_values.isna()
        if bool(missing_raw.any()):
            probe_df = df.copy()
            probe_df.loc[missing_raw, raw_col] = 0.0
            probe_working = _derive_working_frame(probe_df, config, calibration_meta=calibration_meta, edit_state=edit_state)
            if raw_col in probe_working.columns:
                probe_source = pd.to_numeric(probe_working[raw_col], errors="coerce").reindex(working_df.index)
                offsets.loc[missing_raw] = probe_source.loc[missing_raw]
        return offsets

    range_config = RangeConfig(
        signal_range=config.signal_range,
        leak_range=config.leak_range,
        d13c_range=config.d13c_range,
        d18o_range=config.d18o_range,
        partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
    )
    sigma_level = float(config.sigma_level_data)
    statistical_method = str(getattr(config, "statistical_outlier_method", "Z-Score"))
    iqr_multiplier = float(getattr(config, "iqr_multiplier_data", 1.5))
    category_masks = build_category_masks(
        working_df,
        range_config,
        edit_state=edit_state,
        sigma_level=sigma_level,
        statistical_outlier_method=statistical_method,
        iqr_multiplier=iqr_multiplier,
    )
    stat_mask_d13, stat_mask_d18, _ = compute_statistical_outlier_masks(
        working_df,
        sigma_level=sigma_level,
        edit_state=edit_state,
        method=statistical_method,
        iqr_multiplier=iqr_multiplier,
    )
    excluded_common = pd.Series(False, index=working_df.index, dtype=bool)
    for key in [
        "d13C Range",
        "d18O Range",
        "Signal Intensity",
        "Leak Rate",
        "Partially Saturated Collectors",
        "Fully Saturated Collectors",
        "Failed Sample",
    ]:
        excluded_common = excluded_common | category_masks.get(key, pd.Series(False, index=working_df.index, dtype=bool))
    target_mask = working_df.index.to_series().astype(str).isin(target_row_tokens)
    source_df = working_df.copy()
    if "d 13C/12C  Mean" in source_df.columns:
        d13_excluded = (excluded_common | stat_mask_d13.reindex(source_df.index, fill_value=False).astype(bool)) & ~target_mask
        source_df.loc[d13_excluded, "d 13C/12C  Mean"] = np.nan
    if "d 18O/16O  Mean" in source_df.columns:
        d18_excluded = (excluded_common | stat_mask_d18.reindex(source_df.index, fill_value=False).astype(bool)) & ~target_mask
        source_df.loc[d18_excluded, "d 18O/16O  Mean"] = np.nan
    source_df["__source_to_raw_offset_d13C__"] = _source_to_raw_offset_with_missing_fill("d 13C/12C  Mean")
    source_df["__source_to_raw_offset_d18O__"] = _source_to_raw_offset_with_missing_fill("d 18O/16O  Mean")
    return source_df


def _candidate_diagnostics_color_columns(df: pd.DataFrame) -> list[str]:
    preferred = [
        "Date",
        "Identifier 1",
        "Identifier 2",
        "Species",
        "Comment",
        "Label",
        "1  Cycle Int  Samp  44",
        "1  Cycle Int  Diff Samp-Ref  44",
        "leak_rate",
        "Line",
        "d 13C/12C  Mean",
        "d 18O/16O  Mean",
    ]
    return [col for col in preferred if col in df.columns]


def _candidate_diagnostics_z_columns(df: pd.DataFrame) -> list[str]:
    preferred = [
        "1  Cycle Int  Diff Samp-Ref  44",
        "1  Cycle Int  Samp  44",
        "total_co2",
        "p_no_acid",
        "p_gases",
        "leak_rate",
        "Line",
        "Date_ordinal",
        "d 13C/12C  Mean",
        "d 18O/16O  Mean",
    ]
    ordered = [col for col in preferred if col in df.columns]
    ordered.extend(col for col in df.columns if col not in ordered)
    result: list[str] = []
    for col in ordered:
        finite = pd.to_numeric(df[col], errors="coerce")
        if np.isfinite(finite).any():
            result.append(col)
    return result


def _column_bounds(df: pd.DataFrame, column: str) -> list[float] | None:
    if column not in df.columns:
        return None
    values = pd.to_numeric(df[column], errors="coerce")
    finite = values[np.isfinite(values)]
    if finite.empty:
        return None
    return [float(finite.min()), float(finite.max())]


def _apply_diagnostics_filters(
    df: pd.DataFrame,
    identifier_filter: list[str],
    d13_min: float | None,
    d13_max: float | None,
    d18_min: float | None,
    d18_max: float | None,
) -> pd.DataFrame:
    filtered = df.copy()
    cleaned_identifiers = [str(item).strip() for item in identifier_filter if str(item).strip()]
    if cleaned_identifiers and "Identifier 1" in filtered.columns:
        filtered = filtered[filtered["Identifier 1"].astype(str).isin(cleaned_identifiers)].copy()
    if "d 13C/12C  Mean" in filtered.columns and (d13_min is not None or d13_max is not None):
        d13_vals = pd.to_numeric(filtered["d 13C/12C  Mean"], errors="coerce")
        low = float(d13_min) if d13_min is not None else float("-inf")
        high = float(d13_max) if d13_max is not None else float("inf")
        if low > high:
            low, high = high, low
        filtered = filtered[d13_vals.ge(low) & d13_vals.le(high)].copy()
    if "d 18O/16O  Mean" in filtered.columns and (d18_min is not None or d18_max is not None):
        d18_vals = pd.to_numeric(filtered["d 18O/16O  Mean"], errors="coerce")
        low = float(d18_min) if d18_min is not None else float("-inf")
        high = float(d18_max) if d18_max is not None else float("inf")
        if low > high:
            low, high = high, low
        filtered = filtered[d18_vals.ge(low) & d18_vals.le(high)].copy()
    return filtered


def _simple_processing_figures(df: pd.DataFrame, config: ProcessingConfig) -> dict[str, dict[str, Any]]:
    subset = _processing_subset(df, config)
    figures: dict[str, dict[str, Any]] = {}
    fig_3d, _ = _build_isotope_3d_scatter(
        subset,
        z_col=config.z_axis,
        z_label=config.z_axis,
        color_col=config.color_param,
        color_label=config.color_param,
        title="Processing 3D Chart",
    )
    figures["processing_3d"] = _figure_json(fig_3d)
    if subset.empty:
        return figures
    species_col = "Species" if "Species" in subset.columns else "Identifier 1"
    d13 = go.Figure()
    d18 = go.Figure()
    cross = go.Figure()
    for species, species_df in subset.groupby(species_col):
        plot_df = species_df.copy()
        if config.x_axis_option == "By Identifier 2":
            plot_df["x_axis"] = pd.to_numeric(plot_df.get("Identifier 2"), errors="coerce")
        else:
            plot_df["x_axis"] = range(len(plot_df))
        plot_df = plot_df.sort_values("x_axis", na_position="last")
        d13.add_trace(
            go.Scatter(
                x=plot_df["x_axis"],
                y=pd.to_numeric(plot_df.get("d 13C/12C  Mean"), errors="coerce"),
                mode="lines+markers",
                name=str(species),
            )
        )
        d18.add_trace(
            go.Scatter(
                x=plot_df["x_axis"],
                y=pd.to_numeric(plot_df.get("d 18O/16O  Mean"), errors="coerce"),
                mode="lines+markers",
                name=str(species),
            )
        )
        cross.add_trace(
            go.Scatter(
                x=pd.to_numeric(plot_df.get("d 18O/16O  Mean"), errors="coerce"),
                y=pd.to_numeric(plot_df.get("d 13C/12C  Mean"), errors="coerce"),
                mode="markers",
                name=str(species),
            )
        )
    d13.update_layout(title="d13C Summary", xaxis_title=config.x_axis_option, yaxis_title="d13C")
    d18.update_layout(title="d18O Summary", xaxis_title=config.x_axis_option, yaxis_title="d18O")
    cross.update_layout(title="d13C vs d18O", xaxis_title="d18O", yaxis_title="d13C")
    figures["d13_summary"] = _figure_json(d13)
    figures["d18_summary"] = _figure_json(d18)
    figures["crossplot"] = _figure_json(cross)
    return figures


def _build_processing_summary(df: pd.DataFrame, config: ProcessingConfig) -> dict[str, Any]:
    range_config = RangeConfig(
        signal_range=config.signal_range,
        leak_range=config.leak_range,
        d13c_range=config.d13c_range,
        d18o_range=config.d18o_range,
        partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
    )
    subset = _processing_subset(df, config)
    range_mask = _range_outlier_mask(subset, range_config)
    partial_masks = _partial_saturation_isotope_masks(subset)
    return {
        "rows": int(len(subset)),
        "range_outliers": int(range_mask.sum()),
        "partial_saturation": int(partial_masks["any"].sum()) if "any" in partial_masks else 0,
    }


def _session_exists_or_404(session_id: str) -> None:
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")


def _compute_export_shp2l_precision(
    source_df: pd.DataFrame,
    metadata: dict[str, Any] | None = None,
    calibration_meta: dict[str, Any] | None = None,
    working_df: pd.DataFrame | None = None,
) -> tuple[float, float, int, int]:
    # Prefer calibration workspace precision summaries so client export mirrors
    # the exact values shown on the calibration page (including date-window and
    # linearity-corrected precision when enabled).
    try:
        if source_df is not None and not source_df.empty:
            calibration_only_meta: dict[str, Any] = {"calibration": calibration_meta or {}}
            workspace_meta = metadata if isinstance(metadata, dict) else calibration_only_meta
            calibration_workspace = build_calibration_workspace(
                "__export_precision__",
                source_df,
                workspace_meta,
            )
            shp_summary = next(
                (
                    summary
                    for summary in calibration_workspace.precision_summaries
                    if str(summary.standard).strip().upper() == "SHP2L"
                ),
                None,
            )
            if shp_summary is not None:
                use_corrected = bool(getattr(calibration_workspace.config.linearity, "apply", False))
                d13_value = shp_summary.d13_linearity_corrected_precision if use_corrected else shp_summary.d13_precision
                d18_value = shp_summary.d18_linearity_corrected_precision if use_corrected else shp_summary.d18_precision
                if use_corrected and d13_value is None:
                    d13_value = shp_summary.d13_precision
                if use_corrected and d18_value is None:
                    d18_value = shp_summary.d18_precision
                d13_num = pd.to_numeric(pd.Series([d13_value]), errors="coerce").iloc[0]
                d18_num = pd.to_numeric(pd.Series([d18_value]), errors="coerce").iloc[0]
                return (
                    float(d13_num) if np.isfinite(d13_num) else 0.0,
                    float(d18_num) if np.isfinite(d18_num) else 0.0,
                    int(shp_summary.included_d13),
                    int(shp_summary.included_d18),
                )
    except Exception:
        # Fallback to legacy direct computation below.
        pass

    work = working_df if working_df is not None else source_df
    if work is None:
        return (0.0, 0.0, 0, 0)
    if work.empty or "Identifier 1" not in work.columns:
        return (0.0, 0.0, 0, 0)
    shp = work[work["Identifier 1"].astype(str).str.strip().str.upper() == "SHP2L"].copy()
    if shp.empty:
        return (0.0, 0.0, 0, 0)

    cfg = calibration_meta.get("config", {}) if isinstance(calibration_meta, dict) else {}
    if isinstance(cfg, dict):
        date_range = cfg.get("precision_date_range")
        if (
            isinstance(date_range, (list, tuple))
            and len(date_range) == 2
            and "Date" in shp.columns
            and date_range[0]
            and date_range[1]
        ):
            start_ts = pd.to_datetime(date_range[0], errors="coerce")
            end_ts = pd.to_datetime(date_range[1], errors="coerce")
            if pd.notna(start_ts) and pd.notna(end_ts):
                date_series = pd.to_datetime(shp["Date"], errors="coerce")
                mask = (date_series >= start_ts) & (date_series <= (end_ts + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)))
                shp = shp.loc[mask].copy()
                if shp.empty:
                    return (0.0, 0.0, 0, 0)

    method = str(cfg.get("calibration_type", "IQR")) if isinstance(cfg, dict) else "IQR"
    sigma = float(cfg.get("sigma_level", 1.0)) if isinstance(cfg, dict) else 1.0
    iqr = float(cfg.get("iqr_multiplier", 1.5)) if isinstance(cfg, dict) else 1.5
    independent = bool(cfg.get("independent_isotope_outliers", True)) if isinstance(cfg, dict) else True

    if method == "Z-Score":
        out13 = identify_outliers(shp, "d 13C/12C  Mean", sigma).reindex(shp.index, fill_value=False)
        out18 = identify_outliers(shp, "d 18O/16O  Mean", sigma).reindex(shp.index, fill_value=False)
    else:
        out13 = identify_outliers_iqr(shp, "d 13C/12C  Mean", iqr).reindex(shp.index, fill_value=False)
        out18 = identify_outliers_iqr(shp, "d 18O/16O  Mean", iqr).reindex(shp.index, fill_value=False)

    if independent:
        clean_d13 = shp.loc[~out13].copy()
        clean_d18 = shp.loc[~out18].copy()
    else:
        combined = out13 | out18
        clean_d13 = shp.loc[~combined].copy()
        clean_d18 = shp.loc[~combined].copy()

    d13_col = "d13C_linearity_corrected" if "d13C_linearity_corrected" in clean_d13.columns else "d 13C/12C  Mean"
    d18_col = "d18O_linearity_corrected" if "d18O_linearity_corrected" in clean_d18.columns else "d 18O/16O  Mean"
    d13_values = pd.to_numeric(clean_d13.get(d13_col), errors="coerce").dropna()
    d18_values = pd.to_numeric(clean_d18.get(d18_col), errors="coerce").dropna()
    d13_std = float(d13_values.std()) if not d13_values.empty and pd.notna(d13_values.std()) else 0.0
    d18_std = float(d18_values.std()) if not d18_values.empty and pd.notna(d18_values.std()) else 0.0
    return (d13_std, d18_std, int(d13_values.shape[0]), int(d18_values.shape[0]))


def _cap_stdev_columns_for_client_output(df: pd.DataFrame, cap_value: float) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    capped = df.copy()
    max_stdev = float(cap_value)
    for column in ("d 13C/12C  Std Dev", "d 18O/16O  Std Dev"):
        if column in capped.columns:
            numeric_values = pd.to_numeric(capped[column], errors="coerce")
            capped[column] = numeric_values.clip(upper=max_stdev)
    return capped


def _chart_visible_client_output_frame(
    working_df: pd.DataFrame,
    config: ProcessingConfig,
    selected_ids: list[str],
    selected_standards: list[str],
    edit_state: dict[str, Any] | None = None,
) -> pd.DataFrame:
    filtered_df, unfiltered_df = _build_plot_frames(working_df, config, standards_to_exclude=selected_standards)
    range_config = RangeConfig(
        signal_range=config.signal_range,
        leak_range=config.leak_range,
        d13c_range=config.d13c_range,
        d18o_range=config.d18o_range,
        partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
    )
    filtered_df = _exclude_outliers_from_plot_base(
        filtered_df,
        unfiltered_df,
        range_config,
        edit_state=edit_state,
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    scoped_filtered = _selected_processing_rows(filtered_df, selected_ids)
    scoped_unfiltered = _selected_processing_rows(unfiltered_df, selected_ids)
    if scoped_filtered.empty or scoped_unfiltered.empty:
        return scoped_filtered

    # Mirror identifier-chart statistical context: compute statistical outliers from
    # rows that are within range and leak/signal constraints (plus optional partial keep).
    signal_ok = _signal_in_range_mask(scoped_unfiltered.get("1  Cycle Int  Samp  44"), config.signal_range)
    leak_ok = pd.to_numeric(scoped_unfiltered.get("leak_rate"), errors="coerce").between(*config.leak_range, inclusive="both")
    d13_ok = pd.to_numeric(scoped_unfiltered.get("d 13C/12C  Mean"), errors="coerce").between(*config.d13c_range, inclusive="both")
    d18_ok = pd.to_numeric(scoped_unfiltered.get("d 18O/16O  Mean"), errors="coerce").between(*config.d18o_range, inclusive="both")
    sat_masks_for_stats = _partial_saturation_isotope_masks(scoped_unfiltered)
    partial_keep = pd.Series(False, index=scoped_unfiltered.index, dtype=bool)
    if bool(config.overlays.show_saturated_collectors):
        partial_keep = signal_ok & leak_ok & (
            (sat_masks_for_stats["d13C"] & d13_ok) | (sat_masks_for_stats["d18O"] & d18_ok)
        )
    stat_source_mask = (signal_ok & leak_ok & d13_ok & d18_ok) | partial_keep
    stat_source = scoped_unfiltered.loc[stat_source_mask].copy()
    if stat_source.empty:
        return scoped_filtered
    stat_mask_d13, stat_mask_d18, _ = compute_statistical_outlier_masks(
        stat_source,
        sigma_level=float(config.sigma_level_data),
        edit_state=edit_state,
        method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    stat_mask_combined = (
        stat_mask_d13.reindex(scoped_filtered.index, fill_value=False).astype(bool)
        | stat_mask_d18.reindex(scoped_filtered.index, fill_value=False).astype(bool)
    )
    return scoped_filtered.loc[~stat_mask_combined].copy()


def _autosave_paths_payload(session_id: str) -> dict[str, Any]:
    paths = store._paths(session_id)
    return {
        "save_dir": str(paths.root),
        "log_path": str(paths.log_path),
        "snapshot_path": str(paths.snapshot_path),
        "meta_path": str(paths.metadata_path),
        "session_token": session_id,
    }


def _persist_session_update(
    session_id: str,
    *,
    action: str,
    payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    df: pd.DataFrame | None = None,
    cycles_df: pd.DataFrame | None = None,
    resumed: bool | None = None,
) -> dict[str, Any]:
    meta = metadata if metadata is not None else store.load_metadata(session_id)
    autosave = dict(meta.get("autosave", {}))
    autosave.update(_autosave_paths_payload(session_id))
    autosave.setdefault("initialized_at", datetime.now(timezone.utc).isoformat())
    if resumed is not None:
        autosave["resumed"] = bool(resumed)
    autosave["last_action"] = action
    autosave["last_saved_at"] = datetime.now(timezone.utc).isoformat()
    autosave["event_count"] = int(autosave.get("event_count", 0)) + 1
    meta["autosave"] = autosave

    frame = df
    cycle_frame = cycles_df
    if frame is None and cycles_df is not None:
        frame = store.load_frame(session_id)
    if frame is not None and cycles_df is None:
        cycle_frame = store.load_cycles_frame(session_id)

    if frame is not None:
        store.save_frames(session_id, frame, cycle_frame)
        meta["row_count"] = int(len(frame))
        meta["cycles_row_count"] = int(len(cycle_frame)) if cycle_frame is not None else 0

    store.write_metadata(session_id, meta)
    store.append_log(session_id, action, payload or {})
    return meta


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/standards/official-values", response_model=list[CalibrationOfficialValue])
def list_official_standard_values() -> list[CalibrationOfficialValue]:
    repo = StandardsRepository.default()
    return [CalibrationOfficialValue.model_validate(item) for item in repo.all_official_values()]


@app.post("/standards/official-values", response_model=CalibrationOfficialValue)
def upsert_official_standard_value(payload: CalibrationOfficialValueUpsertRequest) -> CalibrationOfficialValue:
    repo = StandardsRepository.default()
    record = repo.upsert_official_value(
        standard=payload.standard,
        isotopic_value_type=payload.isotopic_value_type,
        value=payload.value,
        source=payload.source,
    )
    return CalibrationOfficialValue.model_validate(record)


@app.delete("/standards/official-values/{standard}", response_model=CalibrationOfficialValueDeleteResult)
def delete_standard_official_values(standard: str) -> CalibrationOfficialValueDeleteResult:
    repo = StandardsRepository.default()
    deleted_rows = repo.delete_standard(standard)
    return CalibrationOfficialValueDeleteResult(standard=standard, deleted_rows=deleted_rows)


@app.delete(
    "/standards/official-values/{standard}/{isotopic_value_type}",
    response_model=CalibrationOfficialValueDeleteResult,
)
def delete_single_official_value(
    standard: str,
    isotopic_value_type: str,
) -> CalibrationOfficialValueDeleteResult:
    repo = StandardsRepository.default()
    deleted_rows = repo.delete_official_value(standard, isotopic_value_type)
    return CalibrationOfficialValueDeleteResult(
        standard=standard,
        isotopic_value_type=isotopic_value_type,
        deleted_rows=deleted_rows,
    )


@app.post("/sessions/import", response_model=ImportResult)
async def import_session(files: list[UploadFile] = File(...)) -> ImportResult:
    uploaded = []
    raw_uploads: list[tuple[str, bytes]] = []
    for file in files:
        content = await file.read()
        raw_uploads.append((file.filename or "upload.xlsx", content))
        uploaded.append(_NamedBytesIO(content, file.filename or "upload.xlsx"))
    df, cycles_df, specs, errors = _load_uploaded_workbooks(uploaded)
    if df is None:
        raise HTTPException(status_code=400, detail={"errors": errors or ["No workbook rows were loaded"]})
    session_name = _build_session_name_from_source_files(specs)
    session_id = store.create_session({"session_name": session_name, "source_files": specs, "errors": errors})
    for filename, content in raw_uploads:
        store.save_upload(session_id, filename, content)
    metadata = store.load_metadata(session_id)
    metadata["session_name"] = session_name
    metadata["source_files"] = specs
    metadata["errors"] = errors
    _persist_session_update(
        session_id,
        action="session_loaded",
        payload={"source_files": specs, "errors": errors},
        metadata=metadata,
        df=df,
        cycles_df=cycles_df,
        resumed=False,
    )
    return ImportResult(session=_to_session_snapshot(session_id))


@app.post("/sessions/{session_id}/append", response_model=ImportResult)
async def append_session(session_id: str, files: list[UploadFile] = File(...)) -> ImportResult:
    _session_exists_or_404(session_id)
    current_df = store.load_frame(session_id)
    current_cycles = store.load_cycles_frame(session_id)
    metadata = store.load_metadata(session_id)
    uploaded = []
    raw_uploads: list[tuple[str, bytes]] = []
    for file in files:
        content = await file.read()
        raw_uploads.append((file.filename or "upload.xlsx", content))
        uploaded.append(_NamedBytesIO(content, file.filename or "upload.xlsx"))
    append_df, append_cycles, specs, errors = _load_uploaded_workbooks(uploaded)
    if append_df is None:
        raise HTTPException(status_code=400, detail={"errors": errors or ["No workbook rows were appended"]})
    merged = _append_rows_preserve_existing_index(current_df, append_df)
    merged_cycles = _append_cycles_source(current_cycles, append_cycles)
    for filename, content in raw_uploads:
        store.save_upload(session_id, filename, content)
    metadata["source_files"] = list(metadata.get("source_files", [])) + specs
    metadata["session_name"] = _build_session_name_from_source_files(metadata["source_files"])
    metadata["errors"] = list(metadata.get("errors", [])) + errors
    _persist_session_update(
        session_id,
        action="session_appended",
        payload={"appended_files": specs, "errors": errors},
        metadata=metadata,
        df=merged,
        cycles_df=merged_cycles,
    )
    return ImportResult(session=_to_session_snapshot(session_id))


@app.get("/sessions", response_model=list[SessionSnapshot])
def list_sessions(limit: int = Query(50, ge=1, le=500)) -> list[SessionSnapshot]:
    return [SessionSnapshot.model_validate(item) for item in store.list_sessions(limit=limit)]


@app.get("/sessions/{session_id}", response_model=SessionSnapshot)
def get_session(session_id: str) -> SessionSnapshot:
    _session_exists_or_404(session_id)
    return _to_session_snapshot(session_id)


@app.post("/sessions/{session_id}/open", response_model=SessionSnapshot)
def open_session(session_id: str) -> SessionSnapshot:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    _persist_session_update(
        session_id,
        action="session_opened",
        payload={"resumed": True},
        metadata=metadata,
        resumed=True,
    )
    return _to_session_snapshot(session_id)


@app.post("/sessions/{session_id}/save", response_model=SessionSnapshot)
def save_session(session_id: str) -> SessionSnapshot:
    _session_exists_or_404(session_id)
    _persist_session_update(
        session_id,
        action="session_saved_manual",
        payload={"trigger": "manual"},
        metadata=store.load_metadata(session_id),
        df=store.load_frame(session_id),
        cycles_df=store.load_cycles_frame(session_id),
    )
    return _to_session_snapshot(session_id)


@app.post("/sessions/{session_id}/close", response_model=SessionSnapshot)
def close_session(session_id: str) -> SessionSnapshot:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    _persist_session_update(
        session_id,
        action="session_closed",
        payload={"closed": True},
        metadata=metadata,
        resumed=False,
    )
    return _to_session_snapshot(session_id)


@app.delete("/sessions/{session_id}")
def discard_session(session_id: str) -> dict[str, Any]:
    if not store.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return {"session_id": session_id, "deleted": True}


@app.get("/sessions/{session_id}/diagnostics", response_model=ChartBundle)
def diagnostics(
    session_id: str,
    color_param: str = Query("Date"),
    z_axis: str | None = Query(None),
    identifier_filter: list[str] = Query([]),
    d13_min: float | None = Query(None),
    d13_max: float | None = Query(None),
    d18_min: float | None = Query(None),
    d18_max: float | None = Query(None),
) -> ChartBundle:
    _session_exists_or_404(session_id)
    df = store.load_frame(session_id)
    available_color_params = _candidate_diagnostics_color_columns(df)
    available_z_axes = _candidate_diagnostics_z_columns(df)
    if available_color_params and color_param not in available_color_params:
        color_param = available_color_params[0]
    if z_axis not in available_z_axes:
        if "1  Cycle Int  Diff Samp-Ref  44" in available_z_axes:
            z_axis = "1  Cycle Int  Diff Samp-Ref  44"
        elif "1  Cycle Int  Samp  44" in available_z_axes:
            z_axis = "1  Cycle Int  Samp  44"
        elif available_z_axes:
            z_axis = available_z_axes[0]
        else:
            z_axis = "1  Cycle Int  Samp  44"
    filtered_df = _apply_diagnostics_filters(df, identifier_filter, d13_min, d13_max, d18_min, d18_max)
    fig = create_diagnostic_plots(filtered_df, color_param)
    fig_3d, _ = _build_isotope_3d_scatter(
        df,
        z_col=z_axis,
        z_label=z_axis,
        color_col=color_param,
        color_label=color_param,
        title="Diagnostics 3D Chart",
        open_circle_identifier="SHP2L",
    )
    identifiers = (
        sorted({str(value) for value in df.get("Identifier 1", pd.Series(dtype=object)).dropna().tolist() if str(value).strip()})
        if "Identifier 1" in df.columns
        else []
    )
    return ChartBundle(
        session_id=session_id,
        figures={"diagnostics": _figure_json(fig), "diagnostics_3d": _figure_json(fig_3d)},
        summary={
            "available_color_params": available_color_params,
            "available_z_axis_options": available_z_axes,
            "available_identifiers": identifiers,
            "d13_bounds": _column_bounds(df, "d 13C/12C  Mean"),
            "d18_bounds": _column_bounds(df, "d 18O/16O  Mean"),
            "active_filters": {
                "color_param": color_param,
                "z_axis": z_axis,
                "identifier_filter": [str(item).strip() for item in identifier_filter if str(item).strip()],
                "d13_min": d13_min,
                "d13_max": d13_max,
                "d18_min": d18_min,
                "d18_max": d18_max,
            },
            "row_count_before": int(len(df)),
            "row_count_after": int(len(filtered_df)),
        },
    )


@app.post("/sessions/{session_id}/calibration/run", response_model=SessionSnapshot)
def run_calibration(session_id: str, config: CalibrationConfig) -> SessionSnapshot:
    _session_exists_or_404(session_id)
    config = normalize_calibration_config(config.model_dump())
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    working_df = _ensure_cycle1_signal_difference_columns(df.copy())
    working_df = _apply_isotope_line_offsets(
        working_df,
        line_1_offset_d13=getattr(config.linearity, "line_1_offset_d13", None),
        line_1_offset_d18=getattr(config.linearity, "line_1_offset_d18", None),
        line_2_offset_d13=getattr(config.linearity, "line_2_offset_d13", None),
        line_2_offset_d18=getattr(config.linearity, "line_2_offset_d18", None),
    )
    standards_repo = StandardsRepository.default()
    if len(config.selected_standards) not in (1, 2):
        raise HTTPException(status_code=400, detail="Please select either one or two standards for calibration.")
    override_scope = (
        sorted(
            {
                str(value).strip()
                for value in working_df.get("Identifier 1", pd.Series(dtype=object)).dropna().tolist()
                if str(value).strip() != ""
            }
        )
        if "Identifier 1" in working_df.columns
        else list(config.selected_standards)
    )
    selected_linearity_intensity_col = _resolve_selected_linearity_intensity_column(
        df=working_df,
        use_diff_intensity=config.linearity.use_diff_intensity,
        selected_intensity_col=getattr(config.linearity, "intensity_col", None),
    )
    max_sample_intensity = (
        getattr(config.linearity, "max_sample_intensity", None)
        if selected_linearity_intensity_col == CYCLE1_SIGNAL_SAMP44_COL
        else None
    )
    line_adjusted_df, d13_offset_intensity_col, d18_offset_intensity_col = _with_isotope_linearity_intensity_columns(
        working_df,
        selected_linearity_intensity_col,
        line_1_offset=config.linearity.line_1_offset,
        line_2_offset=config.linearity.line_2_offset,
    )
    manual_override_intensity_col = (
        d13_offset_intensity_col
        if d13_offset_intensity_col == d18_offset_intensity_col
        else selected_linearity_intensity_col
    )
    standards_adjusted_df = _apply_manual_linearity_override_to_standards(
        line_adjusted_df,
        override_scope,
        enabled=config.linearity.manual_override_enabled,
        d13_per_10v=config.linearity.manual_d13_per_10v,
        d18_per_10v=config.linearity.manual_d18_per_10v,
        d13_per_10v2=config.linearity.manual_d13_per_10v2,
        d18_per_10v2=config.linearity.manual_d18_per_10v2,
        quadratic=bool(config.linearity.quadratic),
        use_diff_intensity=config.linearity.use_diff_intensity,
        selected_intensity_col=manual_override_intensity_col,
    )
    outlier_input_df = standards_adjusted_df
    selected_mask = (
        outlier_input_df["Identifier 1"].astype(str).isin({str(item) for item in config.selected_standards})
        if "Identifier 1" in outlier_input_df.columns
        else pd.Series(False, index=outlier_input_df.index, dtype=bool)
    )
    standards_for_calibration = outlier_input_df.loc[selected_mask].copy() if bool(selected_mask.any()) else pd.DataFrame()
    outlier_reference_df = outlier_input_df
    fits: dict[str, Any] = {}
    if bool(config.linearity.apply):
        fit_input = standards_for_calibration
        intensity_col = _resolve_selected_linearity_intensity_column(
            df=fit_input if fit_input is not None and not fit_input.empty else outlier_input_df,
            use_diff_intensity=config.linearity.use_diff_intensity,
            selected_intensity_col=selected_linearity_intensity_col,
        )
        fit13_intensity_col = d13_offset_intensity_col if d13_offset_intensity_col in fit_input.columns else intensity_col
        fit18_intensity_col = d18_offset_intensity_col if d18_offset_intensity_col in fit_input.columns else intensity_col
        fit13 = (
            _compute_linearity_fit(
                _filter_linearity_fit_input_by_max_intensity(
                    fit_input,
                    fit13_intensity_col,
                    max_sample_intensity,
                ),
                "d 13C/12C  Mean",
                fit13_intensity_col,
                quadratic=bool(config.linearity.quadratic),
            )
            if fit_input is not None and not fit_input.empty
            else {}
        )
        fit18 = (
            _compute_linearity_fit(
                _filter_linearity_fit_input_by_max_intensity(
                    fit_input,
                    fit18_intensity_col,
                    max_sample_intensity,
                ),
                "d 18O/16O  Mean",
                fit18_intensity_col,
                quadratic=bool(config.linearity.quadratic),
            )
            if fit_input is not None and not fit_input.empty
            else {}
        )
        fits = {
            "d13C": fit13,
            "d18O": fit18,
            "intensity_col": intensity_col,
            "d13_intensity_col": d13_offset_intensity_col,
            "d18_intensity_col": d18_offset_intensity_col,
        }
        outlier_reference_df = _promote_linearity_corrected_raw_columns(
            _apply_linearity_correction(outlier_input_df, intensity_col, fits)
        )
    clean_stds = _filter_standards_remove_outliers(
        outlier_input_df,
        config.selected_standards,
        config.calibration_type,
        config.sigma_level,
        config.iqr_multiplier,
        config.independent_isotope_outliers,
        outlier_reference_df=outlier_reference_df,
    )
    standards_source = clean_stds if not clean_stds.empty else standards_for_calibration
    calibration_source = outlier_input_df
    if bool(config.linearity.apply) and fits:
        standards_source = _promote_linearity_corrected_raw_columns(
            _apply_linearity_correction(standards_source, fits.get("intensity_col", selected_linearity_intensity_col), fits)
        )
        calibration_source = _promote_linearity_corrected_raw_columns(
            _apply_linearity_correction(outlier_input_df, fits.get("intensity_col", selected_linearity_intensity_col), fits)
        )
    calibrated = calibrate_results(
        standards_source if standards_source is not None and not standards_source.empty else calibration_source,
        calibration_source,
        config.selected_standards,
        standards_repo,
    )
    for calibrated_col, corrected_col in (
        ("d13C_calibrated", "d13C_calibrated_linearity_corrected"),
        ("d18O_calibrated", "d18O_calibrated_linearity_corrected"),
    ):
        if calibrated_col in calibrated.columns:
            calibrated_values = pd.to_numeric(calibrated[calibrated_col], errors="coerce")
            calibrated[corrected_col] = calibrated_values

    # Keep stored imported isotope measurements untouched; calibration writes derived columns only.
    calibrated_for_storage = calibrated.copy()
    for raw_col in ("d 13C/12C  Mean", "d 18O/16O  Mean"):
        if raw_col in df.columns and raw_col in calibrated_for_storage.columns:
            calibrated_for_storage[raw_col] = df[raw_col]

    def _normalize_identifier(value: Any) -> str:
        return str(value).strip().upper()

    standards_for_storage = {
        _normalize_identifier(item)
        for item in standards_repo.standards_list()
        if str(item).strip()
    }
    standards_for_storage.update(
        _normalize_identifier(item)
        for item in config.selected_standards
        if str(item).strip()
    )
    if standards_for_storage and "Identifier 1" in calibrated_for_storage.columns:
        identifier_labels = calibrated_for_storage["Identifier 1"].fillna("").astype(str).str.strip().str.upper()
        standards_mask = identifier_labels.isin(standards_for_storage)
        for cal_col in (
            "d13C_calibrated",
            "d18O_calibrated",
            "d13C_calibrated_linearity_corrected",
            "d18O_calibrated_linearity_corrected",
        ):
            if cal_col in calibrated_for_storage.columns:
                calibrated_for_storage.loc[standards_mask, cal_col] = np.nan

    metadata["calibration"] = {
        "config": config.model_dump(),
        "coefficients": _compute_calibration_coefficients(standards_source, config.selected_standards, standards_repo),
        "linearity_fits": fits,
        "selected_standards": config.selected_standards,
    }
    _set_processing_apply_calibration(metadata, True)
    _persist_session_update(
        session_id,
        action="calibration_run",
        payload=metadata["calibration"],
        metadata=metadata,
        df=calibrated_for_storage,
        cycles_df=store.load_cycles_frame(session_id),
    )
    return _to_session_snapshot(session_id)


@app.post("/sessions/{session_id}/calibration/reset", response_model=SessionSnapshot)
def reset_calibration(session_id: str) -> SessionSnapshot:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    cleaned_df, removable_columns = _drop_calibration_derived_columns(df)
    metadata["calibration"] = {}
    _set_processing_apply_calibration(metadata, False)
    _persist_session_update(
        session_id,
        action="calibration_reset",
        payload={"removed_columns": removable_columns},
        metadata=metadata,
        df=cleaned_df,
        cycles_df=store.load_cycles_frame(session_id),
    )
    return _to_session_snapshot(session_id)


@app.post("/sessions/{session_id}/processing/calibration/remove", response_model=ProcessingWorkspace)
def remove_processing_calibration(session_id: str) -> ProcessingWorkspace:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    cleaned_df, removed_columns = _drop_calibration_derived_columns(df)
    _set_processing_apply_calibration(metadata, False)
    _persist_session_update(
        session_id,
        action="processing_calibration_removed",
        payload={"removed_columns": removed_columns},
        metadata=metadata,
        df=cleaned_df,
        cycles_df=store.load_cycles_frame(session_id),
    )
    return _build_processing_workspace_response(session_id, metadata=metadata, df=cleaned_df)


@app.get("/sessions/{session_id}/calibration/workspace", response_model=CalibrationWorkspace)
def calibration_workspace(session_id: str) -> CalibrationWorkspace:
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return build_calibration_workspace(
        session_id=session_id,
        df=store.load_frame(session_id),
        metadata=store.load_metadata(session_id),
    )


@app.post("/sessions/{session_id}/calibration/workspace", response_model=CalibrationWorkspace)
def calibration_workspace_preview(session_id: str, config: CalibrationConfig) -> CalibrationWorkspace:
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return build_calibration_workspace(
        session_id=session_id,
        df=store.load_frame(session_id),
        metadata=store.load_metadata(session_id),
        config_override=config,
    )


@app.get("/sessions/{session_id}/calibration/charts", response_model=ChartBundle)
def calibration_charts(session_id: str, color_param: str = Query("Date")) -> ChartBundle:
    _session_exists_or_404(session_id)
    workspace = build_calibration_workspace(
        session_id=session_id,
        df=store.load_frame(session_id),
        metadata=store.load_metadata(session_id),
    )
    payload = dict(workspace.figures)
    if color_param and color_param != workspace.config.color_param:
        # Preserve the legacy endpoint signature without adding a second config store path.
        metadata = store.load_metadata(session_id)
        config_payload = dict(metadata.get("calibration", {}).get("config", {}))
        config_payload["color_param"] = color_param
        workspace = build_calibration_workspace(
            session_id=session_id,
            df=store.load_frame(session_id),
            metadata={"calibration": {**metadata.get("calibration", {}), "config": config_payload}},
        )
        payload = dict(workspace.figures)
    return ChartBundle(session_id=session_id, figures=payload)


def _build_processing_workspace_response(
    session_id: str,
    metadata: dict[str, Any] | None = None,
    df: pd.DataFrame | None = None,
    cycles_df: pd.DataFrame | None = None,
) -> ProcessingWorkspace:
    meta = metadata if metadata is not None else store.load_metadata(session_id)
    frame = df if df is not None else store.load_frame(session_id)
    cycles_frame = cycles_df if cycles_df is not None else store.load_cycles_frame(session_id)
    return build_processing_workspace(session_id, frame, cycles_frame, meta)


def _workspace_to_chart_bundle(workspace: ProcessingWorkspace) -> ChartBundle:
    return ChartBundle(
        session_id=workspace.session_id,
        figures=dict(workspace.overview_figures),
        tables={
            table.name: list(table.rows)
            for table in workspace.outlier_tables
        },
        summary=workspace.summary.model_dump(),
    )


@app.get("/sessions/{session_id}/processing/workspace", response_model=ProcessingWorkspace)
def processing_workspace(session_id: str) -> ProcessingWorkspace:
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return _build_processing_workspace_response(session_id)


@app.post("/sessions/{session_id}/processing/config", response_model=ProcessingWorkspace)
def set_processing_config(session_id: str, config: ProcessingConfig) -> ProcessingWorkspace:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    metadata.setdefault("processing", {})
    metadata["processing"]["config"] = config.model_dump()
    _persist_session_update(
        session_id,
        action="processing_config_updated",
        payload=config.model_dump(),
        metadata=metadata,
    )
    return _build_processing_workspace_response(session_id, metadata=metadata)


@app.post("/sessions/{session_id}/processing/edit", response_model=ProcessingWorkspace)
def edit_processing(session_id: str, edit: EditAction) -> ProcessingWorkspace:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    config = _load_processing_config(metadata)
    edit_state = metadata.setdefault(
        "edit_state",
        {
            "edited_rows": [],
            "original_delta_values": {},
            "original_missing_delta_tokens": [],
            "original_std_values": {},
            "original_missing_std_tokens": [],
            "manual_outlier_overrides": {},
            "restored_delta_tokens": [],
        },
    )
    calibration = _processing_calibration_meta(metadata)
    coeffs = calibration.get("coefficients", {})
    fits = calibration.get("linearity_fits", {})
    linearity_cfg = calibration.get("config", {}).get("linearity", {}) if isinstance(calibration.get("config"), dict) else {}
    interpolation_source_df = (
        _build_interpolation_source_frame(
            df,
            config,
            calibration_meta=calibration,
            edit_state=edit_state,
            target_row_tokens={str(target.row_label) for target in edit.targets},
        )
        if edit.action == "interpolate"
        else None
    )
    try:
        updated_df, updated_edit_state = apply_edit_action(
            df,
            edit_state,
            edit,
            calibration_coefficients=coeffs,
            linearity_fits=fits,
            linearity_config=linearity_cfg,
            interpolation_source_df=interpolation_source_df,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    metadata["edit_state"] = updated_edit_state
    _persist_session_update(
        session_id,
        action="processing_edit",
        payload=edit.model_dump(),
        metadata=metadata,
        df=updated_df,
        cycles_df=store.load_cycles_frame(session_id),
    )
    return _build_processing_workspace_response(session_id, metadata=metadata, df=updated_df)


@app.post("/sessions/{session_id}/processing/cycle-diagnostics", response_model=CycleDiagnosticsPayload)
def processing_cycle_diagnostics(session_id: str, request: CycleDiagnosticsRequest) -> CycleDiagnosticsPayload:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    cycles_df = store.load_cycles_frame(session_id)
    config = _load_processing_config(metadata)
    row_label = _coerce_row_label(request.target.row_label, df)
    target = build_target_info(df, row_label, request.target.isotope_key, metadata.get("edit_state", {}))
    if target is None:
        raise HTTPException(status_code=404, detail="Unknown processing target")
    return build_cycle_diagnostics_payload(
        session_id=session_id,
        df=df,
        cycles_df=cycles_df,
        target=target,
        config=RangeConfig(
            signal_range=config.signal_range,
            leak_range=config.leak_range,
            d13c_range=config.d13c_range,
            d18o_range=config.d18o_range,
            partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
        ),
        edit_state=metadata.get("edit_state", {}),
        target_intensity=request.target_intensity,
        correct_linearity=bool(request.correct_linearity),
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )


@app.get("/sessions/{session_id}/processing/charts", response_model=ChartBundle)
def processing_charts(session_id: str) -> ChartBundle:
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return _workspace_to_chart_bundle(_build_processing_workspace_response(session_id))


@app.post("/sessions/{session_id}/exports/client-output/duplicates", response_model=ClientOutputDuplicateCheckResponse)
def check_client_output_duplicates(session_id: str, request: ExportRequest) -> ClientOutputDuplicateCheckResponse:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    config = normalize_processing_config(metadata.get("processing", {}).get("config", {}))
    config.export = ProcessingExportConfig.model_validate(
        request.model_dump(exclude={"output_type", "restore_stdev", "restore_stdev_cap"})
    )

    calibration = _processing_calibration_meta(metadata)
    working_df = _derive_working_frame(df, config, calibration_meta=calibration, edit_state=metadata.get("edit_state", {}))
    data_to_process = _selected_processing_rows(working_df, list(config.export.selected_ids))
    range_config = RangeConfig(
        signal_range=config.signal_range,
        leak_range=config.leak_range,
        d13c_range=config.d13c_range,
        d18o_range=config.d18o_range,
        partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
    )
    category_masks = build_category_masks(
        data_to_process,
        range_config,
        edit_state=metadata.get("edit_state", {}),
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    outlier_types = build_outlier_type_labels(data_to_process, category_masks)
    effective_outlier_mask = outlier_types.astype(str).str.strip().replace({"": np.nan}).notna()
    selected_standards = metadata.get("calibration", {}).get("selected_standards", [])

    if bool(config.export.include_outliers):
        client_source = data_to_process.copy()
        if bool(config.export.interpolate_outliers):
            columns_to_interp = [
                "1  Cycle Int  Samp  44",
                "d 13C/12C  Mean",
                "d 13C/12C  Std Dev",
                "d 18O/16O  Mean",
                "d 18O/16O  Std Dev",
                "d13C_calibrated",
                "d18O_calibrated",
            ]
            present_cols = [col for col in columns_to_interp if col in client_source.columns]
            if present_cols:
                client_source = _interpolate_outliers_by_identifier2(client_source, effective_outlier_mask, present_cols)
    else:
        client_source = _chart_visible_client_output_frame(
            working_df,
            config,
            list(config.export.selected_ids),
            list(selected_standards),
            edit_state=metadata.get("edit_state", {}),
        )

    if selected_standards and "Identifier 1" in client_source.columns:
        standards_mask = client_source["Identifier 1"].isin(selected_standards)
        client_source = client_source.loc[~standards_mask].copy()
    else:
        client_source = client_source.copy()

    client_df = _round_client_output_columns(_build_client_output_frame(client_source, comment_map=config.export.comment_map))
    client_df["__identifier_2_key"] = client_source.get(
        "Identifier 2",
        pd.Series(index=client_source.index, dtype=object),
    )
    if "Sequence" in client_df.columns:
        client_df = client_df.sort_values(
            by=["Sequence", "Identifier", "Sample #"],
            ascending=[True, True, True],
            na_position="last",
            kind="mergesort",
        ).reset_index(drop=True)
    duplicate_summary = summarize_client_output_duplicates(client_df)
    return ClientOutputDuplicateCheckResponse(
        duplicate_row_count=int(duplicate_summary["duplicate_row_count"]),
        duplicate_identifier1_identifier2_species_values=[
            str(value)
            for value in duplicate_summary["duplicate_identifier1_identifier2_species_values"]
        ],
        duplicate_rows=[
            dict(row)
            for row in duplicate_summary["duplicate_rows"]
        ],
    )


@app.post("/sessions/{session_id}/exports/dataset")
def export_dataset(session_id: str, request: ExportRequest) -> Response:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    config = normalize_processing_config(metadata.get("processing", {}).get("config", {}))
    config.export = ProcessingExportConfig.model_validate(request.model_dump(exclude={"output_type"}))
    metadata.setdefault("processing", {})
    metadata["processing"]["config"] = config.model_dump()

    calibration = _processing_calibration_meta(metadata)
    working_df = _derive_working_frame(df, config, calibration_meta=calibration, edit_state=metadata.get("edit_state", {}))
    data_to_process = _selected_processing_rows(working_df, list(config.export.selected_ids))
    range_config = RangeConfig(
        signal_range=config.signal_range,
        leak_range=config.leak_range,
        d13c_range=config.d13c_range,
        d18o_range=config.d18o_range,
        partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
    )
    selected_standards = metadata.get("calibration", {}).get("selected_standards", [])
    all_standards = StandardsRepository.default().standards_list() + list(selected_standards)
    summary = build_processing_summary(
        data_to_process,
        range_config,
        edit_state=metadata.get("edit_state", {}),
        standards_to_exclude=all_standards,
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    category_masks = build_category_masks(
        data_to_process,
        range_config,
        edit_state=metadata.get("edit_state", {}),
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    outlier_types = build_outlier_type_labels(data_to_process, category_masks)
    effective_outlier_mask = outlier_types.astype(str).str.strip().replace({"": np.nan}).notna()

    if bool(config.export.include_outliers):
        main_data = data_to_process.copy()
        main_data["Outlier Types"] = outlier_types
        outliers_df = pd.DataFrame()
        if bool(config.export.interpolate_outliers):
            columns_to_interp = [
                "1  Cycle Int  Samp  44",
                "d 13C/12C  Mean",
                "d 13C/12C  Std Dev",
                "d 18O/16O  Mean",
                "d 18O/16O  Std Dev",
                "d13C_calibrated",
                "d18O_calibrated",
            ]
            present_cols = [col for col in columns_to_interp if col in main_data.columns]
            original_cols: list[str] = []
            for col in present_cols:
                original_name = f"Original {col}"
                main_data[original_name] = main_data[col]
                original_cols.append(original_name)
            if present_cols:
                main_data = _interpolate_outliers_by_identifier2(main_data, effective_outlier_mask, present_cols)
                if "Outlier Types" in main_data.columns and original_cols:
                    cols = list(main_data.columns)
                    pos = cols.index("Outlier Types")
                    for original_name in original_cols:
                        if original_name in cols:
                            cols.remove(original_name)
                    cols = cols[: pos + 1] + original_cols + cols[pos + 1 :]
                    main_data = main_data[cols]
    else:
        main_data = data_to_process.loc[~effective_outlier_mask].copy()
        outliers_df = data_to_process.loc[effective_outlier_mask].copy()
        if not outliers_df.empty:
            outliers_df["Category"] = outlier_types.loc[outliers_df.index]

    statistics_rows = [
        {"Metric": metric.metric, "Value": metric.value, "Details": metric.details}
        for metric in summary.metrics
    ]
    output_type = request.output_type
    if output_type == "client_output":
        restore_stdev_cap = float(request.restore_stdev_cap)
        if request.restore_stdev and not np.isfinite(restore_stdev_cap):
            raise HTTPException(status_code=400, detail="restore_stdev_cap must be a finite number")
        client_source = main_data.copy()
        if not bool(config.export.include_outliers):
            client_source = _chart_visible_client_output_frame(
                working_df,
                config,
                list(config.export.selected_ids),
                list(selected_standards),
                edit_state=metadata.get("edit_state", {}),
            )
        if request.restore_stdev:
            client_source = _cap_stdev_columns_for_client_output(client_source, restore_stdev_cap)
        precision_override = _compute_export_shp2l_precision(
            df,
            metadata=metadata,
            calibration_meta=calibration,
            working_df=working_df,
        )
        workbook, filename = build_client_output_workbook_bytes(
            client_source,
            selected_standards=selected_standards,
            client_name=config.export.client_name,
            comment_map=config.export.comment_map,
            precision_source_df=working_df,
            precision_override=precision_override,
        )
    else:
        workbook, filename = build_dataset_workbook_bytes(
            main_data,
            outliers=outliers_df,
            selected_standards=selected_standards,
            client_name=config.export.client_name,
            statistics_rows=statistics_rows,
        )
    _persist_session_update(
        session_id,
        action="dataset_exported",
        payload=config.export.model_dump(),
        metadata=metadata,
    )
    return Response(
        content=workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
