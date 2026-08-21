from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import threading
import tempfile
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..domain.calibration.core import (
    _apply_isotope_line_offsets,
    _apply_linearity_correction,
    _apply_manual_linearity_offsets_to_fits,
    _apply_manual_linearity_override_to_standards,
    _compute_calibration_coefficients,
    _compute_standard_linearity_fit,
    _filter_linearity_fit_input_by_max_intensity,
    _filter_standards_remove_outliers,
    _promote_linearity_corrected_raw_columns,
    _resolve_selected_linearity_intensity_column,
    _with_isotope_linearity_intensity_columns,
    _with_standard_linearity_residual_columns,
    calibrate_results,
    create_calibration_plots,
    identify_outliers,
    identify_outliers_iqr,
)
from ..domain.contracts import (
    AutosaveSettingsUpdate,
    CalibrationConfig,
    CalibrationLinearityUpdateRequest,
    CalibrationOfficialValue,
    CalibrationOfficialValueDeleteResult,
    CalibrationOfficialValueUpsertRequest,
    CalibrationWorkspace,
    ClientOutputDuplicateCheckResponse,
    ClientOutputPreviewResponse,
    ChartBundle,
    CycleDiagnosticsPayload,
    CycleDiagnosticsRequest,
    EditAction,
    EditBatchRequest,
    ExportRequest,
    ImportNamingUpdate,
    ImportNamingWorkspace,
    ImportParsingConfig,
    ImportPreviewResponse,
    ImportResult,
    JobSnapshot,
    LinearityConfig,
    ProcessingConfig,
    ProcessingExportConfig,
    ProcessingLinearityPreviewData,
    ProcessingWorkspace,
    OpenAIApiKeyStatus,
    OpenAIApiKeyUpdate,
    SessionSnapshot,
    ScientificChatRequest,
    ScientificChatResponse,
    SpeciesSection,
)
from ..domain.constants import (
    CYCLE1_SIGNAL_DIFF44_COL,
    CYCLE1_SIGNAL_DIFF45_COL,
    CYCLE1_SIGNAL_DIFF46_COL,
    CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL,
    CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
    CYCLE1_SIGNAL_RELATIVE_MISMATCH44_COL,
    CYCLE1_SIGNAL_REF44_COL,
    CYCLE1_SIGNAL_SAMP44_COL,
    CYCLE1_SIGNAL_SYMMETRIC_MISMATCH44_COL,
    SAMPLE_SEQUENCE_COL,
    SESSION_RECORD_DIRNAME,
    SESSION_STATE_FILENAME,
    VALID_CYCLES_COL,
)
from ..domain.diagnostics.core import create_diagnostic_plots, split_diagnostic_plot_grid
from ..domain.calibration.workspace import build_calibration_workspace, normalize_calibration_config
from ..domain.import_session import (
    _append_cycles_source,
    _append_rows_preserve_existing_index,
    _build_session_name_from_source_files,
    _load_uploaded_workbooks,
    preview_uploaded_workbooks,
)
from ..domain.processing.core import RangeConfig, _interpolate_outliers_by_identifier2
from ..domain.processing.cycles import (
    apply_run_level_linearity_basis_from_cycles,
    build_cycle_diagnostics_payload,
    build_target_info,
    resolve_saturation_correction_value_for_target,
    saturation_correction_method_for_isotope,
)
from ..domain.processing.edits import apply_edit_action
from ..domain.processing.export import (
    CLIENT_OUTPUT_NUMERIC_COLUMNS,
    DUPLICATE_QUALITY_KEY_COLUMN,
    _build_client_output_frame,
    _build_client_filename,
    _round_client_output_columns,
    build_client_email_subject,
    build_client_output_workbook_bytes,
    build_dataset_workbook_bytes,
    format_academic_species_name,
    is_raw_client_output_source,
    summarize_client_output_duplicates,
)
from ..domain.processing.outliers import (
    _is_row_edited,
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
    ProcessingWorkspaceContext,
    build_processing_context,
    build_processing_species_section,
    build_processing_workspace_from_context,
    normalize_processing_config,
)
from ..domain.shared.dataframe import (
    _ensure_cycle1_signal_difference_columns,
    _ensure_sample_sequence_column,
    _get_species_series,
)
from ..domain.shared.json_compat import to_json_compatible
from ..domain.shared.naming import apply_identifier1_name_map
from ..domain.shared.plotting import _build_isotope_3d_scatter
from ..domain.standards import StandardsRepository
from ..session_store import FileSessionStore
from ..jobs import JobContext, JobQueueFullError, JobRegistry, TERMINAL_JOB_STATES
from ..scientific_chat_assistant import run_scientific_chat
from ..runtime_secrets import (
    clear_persistent_openai_api_key,
    get_openai_api_key_status,
    set_persistent_openai_api_key,
)

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
    expose_headers=["Content-Disposition", "Server-Timing", "X-Process-Time-Ms"],
)


@app.middleware("http")
async def add_performance_headers(request: Request, call_next):
    """Expose end-to-end server work so UI latency can be measured in place."""

    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000.0
    response.headers["Server-Timing"] = f"app;dur={duration_ms:.1f}"
    response.headers["X-Process-Time-Ms"] = f"{duration_ms:.1f}"
    return response

store = FileSessionStore()
job_registry = JobRegistry()


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


_PROCESSING_FOUR_SIGMA_COLUMNS: dict[str, tuple[str, float]] = {
    "signal_range": (CYCLE1_SIGNAL_SAMP44_COL, 0.1),
    "leak_range": ("leak_rate", 1.0),
    "d13c_range": ("d 13C/12C  Mean", 0.001),
    "d18o_range": ("d 18O/16O  Mean", 0.001),
}
_LEGACY_PROCESSING_RANGES: dict[str, tuple[float, float]] = {
    "signal_range": (0.0, 50.0),
    "leak_range": (0.0, 1000.0),
    "d13c_range": (-10.0, 10.0),
    "d18o_range": (-10.0, 10.0),
}


def _four_sigma_range(
    values: pd.Series | None,
    fallback: tuple[float, float],
    *,
    minimum_half_width: float,
) -> tuple[float, float]:
    if values is None:
        return fallback
    numeric = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if numeric.empty:
        return fallback
    mean = float(numeric.mean())
    standard_deviation = float(numeric.std(ddof=1)) if len(numeric) > 1 else 0.0
    half_width = 4.0 * standard_deviation
    if not np.isfinite(half_width) or half_width <= 0.0:
        half_width = max(float(minimum_half_width), abs(mean) * 0.01)
    return mean - half_width, mean + half_width


def _processing_config_with_four_sigma_ranges(
    df: pd.DataFrame,
    raw_config: dict[str, Any] | None = None,
) -> ProcessingConfig:
    config = ProcessingConfig.model_validate(normalize_processing_config(raw_config or {}).model_dump())
    for field_name, (column_name, minimum_half_width) in _PROCESSING_FOUR_SIGMA_COLUMNS.items():
        fallback = tuple(float(value) for value in getattr(config, field_name))
        setattr(
            config,
            field_name,
            _four_sigma_range(
                df.get(column_name),
                fallback,
                minimum_half_width=minimum_half_width,
            ),
        )
    return config


def _has_legacy_processing_ranges(raw_config: dict[str, Any] | None) -> bool:
    if not isinstance(raw_config, dict) or not raw_config:
        return True
    config = normalize_processing_config(raw_config)
    return all(
        np.allclose(
            np.asarray(getattr(config, field_name), dtype=float),
            np.asarray(expected, dtype=float),
            rtol=0.0,
            atol=1e-12,
        )
        for field_name, expected in _LEGACY_PROCESSING_RANGES.items()
    )


def _initialize_processing_ranges_from_data(
    metadata: dict[str, Any],
    df: pd.DataFrame,
) -> ProcessingConfig:
    processing = metadata.setdefault("processing", {})
    raw_config = processing.get("config", {})
    config = _processing_config_with_four_sigma_ranges(
        df,
        raw_config if isinstance(raw_config, dict) else {},
    )
    processing["config"] = config.model_dump()
    processing["ranges_source"] = "four_sigma_import"
    return config


def _initialize_legacy_processing_ranges_if_needed(session_id: str) -> tuple[dict[str, Any] | None, pd.DataFrame | None]:
    metadata = store.load_metadata(session_id)
    processing = metadata.setdefault("processing", {})
    if processing.get("ranges_source") or not metadata.get("source_files"):
        return None, None
    raw_config = processing.get("config", {})
    if not _has_legacy_processing_ranges(raw_config if isinstance(raw_config, dict) else {}):
        return None, None
    df = store.load_frame(session_id)
    config = _initialize_processing_ranges_from_data(metadata, df)
    _persist_session_update(
        session_id,
        action="processing_ranges_initialized",
        payload={
            "method": "mean_plus_minus_4_sigma",
            "ranges": {
                field_name: list(getattr(config, field_name))
                for field_name in _PROCESSING_FOUR_SIGMA_COLUMNS
            },
        },
        metadata=metadata,
    )
    return metadata, df


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


def _processing_target_linearity_corrected_value(
    df: pd.DataFrame,
    target: dict[str, Any],
    calibration_meta: dict[str, Any] | None,
    config: ProcessingConfig | None = None,
    edit_state: dict[str, Any] | None = None,
    cycles_df: pd.DataFrame | None = None,
) -> float | None:
    if df is None or df.empty or not isinstance(target, dict):
        return None
    calibration = calibration_meta if isinstance(calibration_meta, dict) else {}
    linearity_cfg = calibration.get("config", {}).get("linearity", {}) if isinstance(calibration.get("config"), dict) else {}
    if not isinstance(linearity_cfg, dict) or not bool(linearity_cfg.get("apply")):
        return None
    fits = calibration.get("linearity_fits", {})
    if not isinstance(fits, dict) or not fits:
        return None
    row_label = target.get("row_label")
    isotope_key = str(target.get("isotope_key", "")).strip()
    if row_label not in df.index:
        return None
    if isotope_key == "d13C":
        working_col = "d 13C/12C  Mean"
    elif isotope_key == "d18O":
        working_col = "d 18O/16O  Mean"
    else:
        return None
    if config is not None and not bool(getattr(config, "apply_shared_linearity_to_partially_saturated", True)):
        sat_masks = _partial_saturation_isotope_masks(df.loc[[row_label]])
        isotope_mask = sat_masks.get(isotope_key, sat_masks.get("any", pd.Series(False, index=[row_label], dtype=bool)))
        if bool(isotope_mask.reindex([row_label], fill_value=False).astype(bool).iloc[0]):
            return None

    if config is None:
        return None

    working_df = _derive_working_frame(
        df,
        config,
        calibration_meta=calibration,
        edit_state=edit_state,
        cycles_df=cycles_df,
    )
    if row_label not in working_df.index or working_col not in working_df.columns:
        return None
    corrected_raw = pd.to_numeric(pd.Series([working_df.at[row_label, working_col]]), errors="coerce").iloc[0]
    if pd.notna(corrected_raw) and np.isfinite(corrected_raw):
        return float(corrected_raw)
    return None


def _processing_target_current_value_and_method(
    df: pd.DataFrame,
    target: dict[str, Any],
    config: ProcessingConfig,
    edit_state: dict[str, Any] | None = None,
    cycles_df: pd.DataFrame | None = None,
) -> tuple[float | None, str]:
    isotope_key = str(target.get("isotope_key", "")).strip()
    row_label = target.get("row_label")
    if isotope_key == "d13C":
        raw_col = "d 13C/12C  Mean"
    elif isotope_key == "d18O":
        raw_col = "d 18O/16O  Mean"
    else:
        return None, ""
    if df is None or row_label not in df.index:
        return None, ""
    if raw_col not in df.columns:
        return None, ""
    value = pd.to_numeric(pd.Series([df.at[row_label, raw_col]]), errors="coerce").iloc[0]
    method = "imported"
    target_df = df.loc[[row_label]]
    sat_masks = _partial_saturation_isotope_masks(target_df)
    partial_mask = sat_masks.get(isotope_key, pd.Series(False, index=target_df.index, dtype=bool)).reindex(
        target_df.index,
        fill_value=False,
    ).astype(bool)
    is_partial = bool(partial_mask.iloc[0]) if not partial_mask.empty else False
    if is_partial:
        method = "first_valid_cycle"
        if _is_row_edited(row_label, edit_state):
            method = "edited"
        elif bool(getattr(config, "enable_saturation_correction", False)) and cycles_df is not None and not cycles_df.empty:
            selected_method = saturation_correction_method_for_isotope(config, isotope_key)
            corrected_value, resolved_method, _ = resolve_saturation_correction_value_for_target(
                df,
                cycles_df,
                target,
                selected_method,
            )
            if corrected_value is not None:
                value = corrected_value
                method = resolved_method
    return (float(value) if pd.notna(value) and np.isfinite(value) else None), method


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
    cycles_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    working_df = _derive_working_frame(
        df,
        config,
        calibration_meta=calibration_meta,
        edit_state=edit_state,
        cycles_df=cycles_df,
    )
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
            probe_working = _derive_working_frame(
                probe_df,
                config,
                calibration_meta=calibration_meta,
                edit_state=edit_state,
                cycles_df=cycles_df,
            )
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
        SAMPLE_SEQUENCE_COL,
        CYCLE1_SIGNAL_SAMP44_COL,
        CYCLE1_SIGNAL_REF44_COL,
        "p_no_acid",
        "total_co2",
        "p_gases",
        VALID_CYCLES_COL,
        CYCLE1_SIGNAL_DIFF44_COL,
        CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL,
        CYCLE1_SIGNAL_RELATIVE_MISMATCH44_COL,
        CYCLE1_SIGNAL_SYMMETRIC_MISMATCH44_COL,
        CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
        "leak_rate",
        "Line",
        "d 13C/12C  Mean",
        "d 18O/16O  Mean",
    ]
    return [col for col in preferred if col in df.columns]


def _candidate_diagnostics_z_columns(df: pd.DataFrame) -> list[str]:
    preferred = [
        CYCLE1_SIGNAL_DIFF44_COL,
        CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL,
        CYCLE1_SIGNAL_RELATIVE_MISMATCH44_COL,
        CYCLE1_SIGNAL_SYMMETRIC_MISMATCH44_COL,
        CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
        CYCLE1_SIGNAL_SAMP44_COL,
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
    payload = {
        "save_dir": str(paths.root),
        "log_path": str(paths.log_path),
        "snapshot_path": str(paths.snapshot_path),
        "meta_path": str(paths.metadata_path),
        "session_state_path": str(paths.session_state_path),
        "session_token": session_id,
    }
    external_path = store._external_session_state_path(paths.root)
    if external_path is not None:
        payload["source_session_state_path"] = str(external_path)
    return payload


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
    autosave.setdefault("enabled", True)
    if resumed is not None:
        autosave["resumed"] = bool(resumed)
    should_record_autosave = bool(autosave["enabled"]) or action in {
        "autosave_enabled",
        "autosave_disabled",
        "session_saved_manual",
        "session_closed",
    }
    if should_record_autosave:
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

    store.write_metadata(session_id, meta, write_session_state=should_record_autosave)
    if should_record_autosave:
        store.append_log(session_id, action, payload or {})
    _clear_processing_workspace_cache(session_id)
    return meta


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "application": "irms-results-analyzer", "version": app.version}


@app.get("/settings/openai-api-key", response_model=OpenAIApiKeyStatus)
def openai_api_key_status() -> OpenAIApiKeyStatus:
    return OpenAIApiKeyStatus.model_validate(get_openai_api_key_status())


@app.put("/settings/openai-api-key", response_model=OpenAIApiKeyStatus)
def configure_openai_api_key(request: OpenAIApiKeyUpdate) -> OpenAIApiKeyStatus:
    set_persistent_openai_api_key(request.api_key.get_secret_value())
    return OpenAIApiKeyStatus.model_validate(get_openai_api_key_status())


@app.delete("/settings/openai-api-key", response_model=OpenAIApiKeyStatus)
def remove_openai_api_key_override() -> OpenAIApiKeyStatus:
    clear_persistent_openai_api_key()
    return OpenAIApiKeyStatus.model_validate(get_openai_api_key_status())


@app.post("/chat/scientific-assistant", response_model=ScientificChatResponse)
async def scientific_chat(request: ScientificChatRequest) -> ScientificChatResponse:
    if not request.message.strip():
        raise HTTPException(status_code=422, detail="Message must not be blank")
    if request.current_session_id is not None:
        _session_exists_or_404(request.current_session_id)
    try:
        result = await run_in_threadpool(run_scientific_chat, request, store)
    except ModuleNotFoundError as exc:
        if exc.name == "openai":
            raise HTTPException(
                status_code=503,
                detail="The OpenAI SDK is not installed in the IRMS backend environment. Restart the app so requirements can be synchronized.",
            ) from exc
        raise
    except RuntimeError as exc:
        if "OPENAI_API_KEY" in str(exc):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        raise
    return ScientificChatResponse.model_validate(result)


async def _read_chat_workbooks(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    maximum_files = max(1, int(os.getenv("IRMS_CHAT_MAX_FILES", "5")))
    maximum_bytes = max(1, int(os.getenv("IRMS_CHAT_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))))
    if len(files) > maximum_files:
        raise HTTPException(
            status_code=413,
            detail=f"The scientific assistant accepts at most {maximum_files} workbook(s) per request.",
        )
    uploaded: list[tuple[str, bytes]] = []
    total = 0
    for file in files:
        filename = Path(file.filename or "upload.xlsx").name
        if Path(filename).suffix.casefold() not in {".xls", ".xlsx"}:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported attachment {filename!r}; upload an .xls or .xlsx workbook.",
            )
        chunks: list[bytes] = []
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "Excel attachments exceed the scientific assistant's combined "
                        f"{maximum_bytes // (1024 * 1024)} MB limit."
                    ),
                )
            chunks.append(chunk)
        uploaded.append((filename, b"".join(chunks)))
    return uploaded


@app.post("/chat/scientific-assistant-with-files", response_model=ScientificChatResponse)
async def scientific_chat_with_files(
    message: str = Form(...),
    history: str = Form(default="[]"),
    current_session_id: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
) -> ScientificChatResponse:
    try:
        parsed_history = json.loads(history)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Chat history must be valid JSON.") from exc
    try:
        request = ScientificChatRequest.model_validate(
            {
                "message": message,
                "history": parsed_history,
                "current_session_id": current_session_id or None,
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid scientific chat request: {exc}") from exc
    if request.current_session_id is not None:
        _session_exists_or_404(request.current_session_id)
    uploaded = await _read_chat_workbooks(files)
    try:
        result = await run_in_threadpool(
            run_scientific_chat, request, store, None, uploaded
        )
    except ModuleNotFoundError as exc:
        if exc.name == "openai":
            raise HTTPException(
                status_code=503,
                detail=(
                    "The OpenAI SDK is not installed in the IRMS backend environment. "
                    "Restart the app so requirements can be synchronized."
                ),
            ) from exc
        raise
    except RuntimeError as exc:
        if "OPENAI_API_KEY" in str(exc):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        raise
    return ScientificChatResponse.model_validate(result)


def _job_or_404(job_id: str) -> JobSnapshot:
    try:
        return job_registry.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown job") from exc


def _submit_job(kind: str, runner, *, session_id: str | None = None) -> JobSnapshot:
    try:
        return job_registry.submit(kind, runner, session_id=session_id)
    except JobQueueFullError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "2"}) from exc


@app.get("/jobs/{job_id}", response_model=JobSnapshot)
def get_job(job_id: str) -> JobSnapshot:
    return _job_or_404(job_id)


@app.delete("/jobs/{job_id}", response_model=JobSnapshot)
def cancel_job(job_id: str) -> JobSnapshot:
    _job_or_404(job_id)
    return job_registry.cancel(job_id)


@app.get("/jobs/{job_id}/events")
async def stream_job_events(job_id: str, request: Request) -> StreamingResponse:
    _job_or_404(job_id)

    async def _events():
        last_revision = -1
        last_keepalive = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            snapshot = _job_or_404(job_id)
            if snapshot.revision != last_revision:
                event_name = (
                    "complete"
                    if snapshot.state == "succeeded"
                    else "error"
                    if snapshot.state == "failed"
                    else "cancelled"
                    if snapshot.state == "cancelled"
                    else "progress"
                )
                payload = json.dumps(snapshot.model_dump(mode="json"), separators=(",", ":"))
                yield f"id: {snapshot.revision}\nevent: {event_name}\ndata: {payload}\n\n"
                last_revision = snapshot.revision
                last_keepalive = time.monotonic()
            if snapshot.state in TERMINAL_JOB_STATES:
                return
            if time.monotonic() - last_keepalive >= 15.0:
                yield ": keepalive\n\n"
                last_keepalive = time.monotonic()
            await asyncio.sleep(0.2)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/jobs/{job_id}/download")
def download_job_artifact(job_id: str) -> Response:
    snapshot = _job_or_404(job_id)
    if snapshot.state != "succeeded":
        raise HTTPException(status_code=409, detail="Job output is not ready")
    artifact = job_registry.get_artifact(job_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Job has no downloadable output")
    return Response(
        content=artifact.path.read_bytes(),
        media_type=artifact.media_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'},
    )


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
    _clear_processing_workspace_cache()
    return CalibrationOfficialValue.model_validate(record)


@app.delete("/standards/official-values/{standard}", response_model=CalibrationOfficialValueDeleteResult)
def delete_standard_official_values(standard: str) -> CalibrationOfficialValueDeleteResult:
    repo = StandardsRepository.default()
    deleted_rows = repo.delete_standard(standard)
    _clear_processing_workspace_cache()
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
    _clear_processing_workspace_cache()
    return CalibrationOfficialValueDeleteResult(
        standard=standard,
        isotopic_value_type=isotopic_value_type,
        deleted_rows=deleted_rows,
    )


async def _read_uploaded_workbook_bytes(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    maximum_bytes = max(1, int(os.getenv("IRMS_JOB_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024))))
    total_bytes = 0
    raw_uploads: list[tuple[str, bytes]] = []
    for file in files:
        content = await file.read()
        total_bytes += len(content)
        if total_bytes > maximum_bytes:
            raise HTTPException(status_code=413, detail="Uploaded workbooks exceed the configured size limit")
        raw_uploads.append((file.filename or "upload.xlsx", content))
    return raw_uploads


def _parse_import_parsing_config(raw: str | None) -> ImportParsingConfig | None:
    if raw is None or not isinstance(raw, (str, bytes, bytearray)) or str(raw).strip() == "":
        return None
    try:
        return ImportParsingConfig.model_validate_json(raw)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid import parsing configuration: {exc}",
        ) from exc


async def _stage_uploaded_workbooks(files: list[UploadFile]) -> tuple[Path, list[tuple[str, Path]]]:
    """Spool queued job inputs to disk so the bounded queue does not retain upload bytes in RAM."""

    maximum_bytes = max(1, int(os.getenv("IRMS_JOB_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024))))
    stage_root = Path(tempfile.mkdtemp(prefix="irms-job-upload-"))
    staged: list[tuple[str, Path]] = []
    total_bytes = 0
    try:
        for index, file in enumerate(files):
            filename = file.filename or "upload.xlsx"
            target = stage_root / f"{index:05d}.workbook"
            with target.open("wb") as handle:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > maximum_bytes:
                        raise HTTPException(status_code=413, detail="Uploaded workbooks exceed the configured size limit")
                    handle.write(chunk)
            staged.append((filename, target))
        return stage_root, staged
    except Exception:
        shutil.rmtree(stage_root, ignore_errors=True)
        raise


def _read_staged_workbooks(staged: list[tuple[str, Path]]) -> list[tuple[str, bytes]]:
    return [(filename, path.read_bytes()) for filename, path in staged]


def _import_session_from_bytes(
    raw_uploads: list[tuple[str, bytes]],
    parsing_config: ImportParsingConfig | None = None,
    context: JobContext | None = None,
) -> ImportResult:
    uploaded = []
    for filename, content in raw_uploads:
        uploaded.append(_NamedBytesIO(content, filename))
    if context is not None:
        context.report(15, "parsing_workbooks", f"Parsing {len(uploaded)} workbook(s)")
    df, cycles_df, specs, errors = _load_uploaded_workbooks(uploaded, parsing_config=parsing_config)
    if df is None:
        raise HTTPException(status_code=400, detail={"errors": errors or ["No workbook rows were loaded"]})
    if context is not None:
        context.raise_if_cancelled()
        context.begin_commit(70, "saving_session", "Saving imported session")
    session_name = _build_session_name_from_source_files(specs)
    session_id = store.create_session({"session_name": session_name, "source_files": specs, "errors": errors})
    for filename, content in raw_uploads:
        store.save_upload(session_id, filename, content)
    metadata = store.load_metadata(session_id)
    metadata["session_name"] = session_name
    metadata["source_files"] = specs
    metadata["errors"] = errors
    metadata["import_parsing"] = (
        parsing_config.model_dump(mode="json")
        if parsing_config is not None
        else {"files": [spec.get("identity_parsing", {}) for spec in specs]}
    )
    _initialize_processing_ranges_from_data(metadata, df)
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


def _append_session_from_bytes(
    session_id: str,
    raw_uploads: list[tuple[str, bytes]],
    parsing_config: ImportParsingConfig | None = None,
    context: JobContext | None = None,
) -> ImportResult:
    _session_exists_or_404(session_id)
    current_df = store.load_frame(session_id)
    current_cycles = store.load_cycles_frame(session_id)
    metadata = store.load_metadata(session_id)
    uploaded = []
    for filename, content in raw_uploads:
        uploaded.append(_NamedBytesIO(content, filename))
    if context is not None:
        context.report(15, "parsing_workbooks", f"Parsing {len(uploaded)} workbook(s)")
    append_df, append_cycles, specs, errors = _load_uploaded_workbooks(
        uploaded,
        parsing_config=parsing_config,
    )
    if append_df is None:
        raise HTTPException(status_code=400, detail={"errors": errors or ["No workbook rows were appended"]})
    merged = _append_rows_preserve_existing_index(current_df, append_df)
    merged_cycles = _append_cycles_source(current_cycles, append_cycles)
    if context is not None:
        context.raise_if_cancelled()
        context.begin_commit(70, "saving_session", "Saving appended workbooks")
    for filename, content in raw_uploads:
        store.save_upload(session_id, filename, content)
    metadata["source_files"] = list(metadata.get("source_files", [])) + specs
    metadata["session_name"] = _build_session_name_from_source_files(metadata["source_files"])
    metadata["errors"] = list(metadata.get("errors", [])) + errors
    metadata.setdefault("import_parsing_history", []).append(
        parsing_config.model_dump(mode="json")
        if parsing_config is not None
        else {"files": [spec.get("identity_parsing", {}) for spec in specs]}
    )
    if metadata.get("processing", {}).get("ranges_source") == "four_sigma_import":
        _initialize_processing_ranges_from_data(metadata, merged)
    _persist_session_update(
        session_id,
        action="session_appended",
        payload={"appended_files": specs, "errors": errors},
        metadata=metadata,
        df=merged,
        cycles_df=merged_cycles,
    )
    return ImportResult(session=_to_session_snapshot(session_id))


@app.post("/sessions/import/preview", response_model=ImportPreviewResponse)
async def preview_session_import(files: list[UploadFile] = File(...)) -> ImportPreviewResponse:
    raw_uploads = await _read_uploaded_workbook_bytes(files)
    uploaded = [_NamedBytesIO(content, filename) for filename, content in raw_uploads]
    preview = preview_uploaded_workbooks(uploaded)
    if not preview.files:
        raise HTTPException(
            status_code=400,
            detail={"errors": preview.errors or ["No workbook could be inspected"]},
        )
    return preview


@app.post("/sessions/import", response_model=ImportResult)
async def import_session(
    files: list[UploadFile] = File(...),
    parsing_config: str | None = Form(default=None),
) -> ImportResult:
    return _import_session_from_bytes(
        await _read_uploaded_workbook_bytes(files),
        parsing_config=_parse_import_parsing_config(parsing_config),
    )


@app.post("/sessions/import/jobs", response_model=JobSnapshot, status_code=202)
async def submit_import_session_job(
    files: list[UploadFile] = File(...),
    parsing_config: str | None = Form(default=None),
) -> JobSnapshot:
    parsed_config = _parse_import_parsing_config(parsing_config)
    stage_root, staged = await _stage_uploaded_workbooks(files)

    def _runner(context: JobContext) -> dict[str, Any]:
        try:
            return _import_session_from_bytes(
                _read_staged_workbooks(staged),
                parsing_config=parsed_config,
                context=context,
            ).model_dump(mode="json")
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)

    try:
        return _submit_job("session_import", _runner)
    except Exception:
        shutil.rmtree(stage_root, ignore_errors=True)
        raise


@app.post("/sessions/{session_id}/append", response_model=ImportResult)
async def append_session(
    session_id: str,
    files: list[UploadFile] = File(...),
    parsing_config: str | None = Form(default=None),
) -> ImportResult:
    return _append_session_from_bytes(
        session_id,
        await _read_uploaded_workbook_bytes(files),
        parsing_config=_parse_import_parsing_config(parsing_config),
    )


@app.post("/sessions/{session_id}/append/jobs", response_model=JobSnapshot, status_code=202)
async def submit_append_session_job(
    session_id: str,
    files: list[UploadFile] = File(...),
    parsing_config: str | None = Form(default=None),
) -> JobSnapshot:
    _session_exists_or_404(session_id)
    parsed_config = _parse_import_parsing_config(parsing_config)
    stage_root, staged = await _stage_uploaded_workbooks(files)

    def _runner(context: JobContext) -> dict[str, Any]:
        try:
            return _append_session_from_bytes(
                session_id,
                _read_staged_workbooks(staged),
                parsing_config=parsed_config,
                context=context,
            ).model_dump(mode="json")
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)

    try:
        return _submit_job("session_append", _runner, session_id=session_id)
    except Exception:
        shutil.rmtree(stage_root, ignore_errors=True)
        raise


@app.post("/sessions/{session_id}/exclude-file", response_model=ImportResult)
def exclude_session_file(session_id: str, file_index: int = Query(..., ge=0)) -> ImportResult:
    """Remove one imported workbook and rebuild the session from the remaining uploads."""
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    source_files = list(metadata.get("source_files", []))
    if file_index >= len(source_files):
        raise HTTPException(status_code=404, detail="Session file was not found")

    removed = source_files[file_index]
    remaining = source_files[:file_index] + source_files[file_index + 1:]
    paths = store._paths(session_id)
    uploaded = []
    parsing_specs = []
    for spec in remaining:
        name = str(spec.get("raw_name") or spec.get("name") or "").replace("\\", "/")
        candidates = [paths.uploads_dir / name, paths.uploads_dir / Path(name).name]
        source_path = next((candidate for candidate in candidates if candidate.exists()), None)
        if source_path is None:
            matches = list(paths.uploads_dir.rglob(Path(name).name))
            source_path = matches[0] if matches else None
        if source_path is None:
            raise HTTPException(status_code=409, detail=f"Stored workbook is unavailable: {name}")
        uploaded.append(_NamedBytesIO(source_path.read_bytes(), str(spec.get("name") or Path(name).name)))
        parsing_specs.append(spec.get("identity_parsing", {}))

    parsing_config = ImportParsingConfig(files=parsing_specs)
    df, cycles_df, rebuilt_specs, errors = _load_uploaded_workbooks(uploaded, parsing_config=parsing_config)
    if df is None:
        raise HTTPException(status_code=400, detail={"errors": errors or ["No workbook rows remain"]})
    metadata["source_files"] = rebuilt_specs
    metadata["errors"] = errors
    metadata["session_name"] = _build_session_name_from_source_files(rebuilt_specs)
    _persist_session_update(
        session_id,
        action="session_file_excluded",
        payload={"excluded_file": removed},
        metadata=metadata,
        df=df,
        cycles_df=cycles_df,
    )
    return ImportResult(session=_to_session_snapshot(session_id))


def _import_naming_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _import_source_file_key(value: Any) -> str:
    text = _import_naming_text(value).replace("\\", "/")
    return Path(text).name.casefold() if text else ""


def _build_species_source_details(
    df: pd.DataFrame,
    metadata: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    software_by_file: dict[str, str] = {}
    for source_file in metadata.get("source_files", []):
        if not isinstance(source_file, dict):
            continue
        identity_parsing = source_file.get("identity_parsing", {})
        if not isinstance(identity_parsing, dict):
            identity_parsing = {}
        software = _import_naming_text(source_file.get("software") or identity_parsing.get("software")).lower()
        if software not in {"qtegra", "isodat"}:
            software = "generic"
        for candidate in (source_file.get("name"), source_file.get("raw_name")):
            file_key = _import_source_file_key(candidate)
            if file_key:
                software_by_file[file_key] = software

    known_software = {value for value in software_by_file.values() if value != "generic"}
    default_software = next(iter(known_software)) if len(known_software) == 1 else "generic"
    details_by_species: dict[str, list[dict[str, Any]]] = {}
    detail_index_by_species: dict[str, dict[tuple[str, ...], int]] = {}
    species_series = _get_species_series(df)

    for position, species_value in enumerate(species_series.tolist()):
        source = _import_naming_text(species_value)
        if not source:
            continue
        row = df.iloc[position]
        source_file = _import_naming_text(row.get("Excel File"))
        software = software_by_file.get(_import_source_file_key(source_file), default_software)
        raw_label = _import_naming_text(row.get("Raw Label")) or _import_naming_text(row.get("Label"))
        raw_identifier1 = _import_naming_text(row.get("Raw Identifier 1")) or _import_naming_text(row.get("Identifier 1"))
        raw_identifier2 = _import_naming_text(row.get("Raw Identifier 2")) or _import_naming_text(row.get("Identifier 2"))
        raw_comment = _import_naming_text(row.get("Raw Comment")) or _import_naming_text(row.get("Comment"))
        if software == "generic":
            if raw_identifier1 or raw_identifier2:
                software = "isodat"
            elif raw_label:
                software = "qtegra"

        token = (software, source_file, raw_label, raw_identifier1, raw_identifier2, raw_comment)
        source_indexes = detail_index_by_species.setdefault(source, {})
        source_details = details_by_species.setdefault(source, [])
        existing_index = source_indexes.get(token)
        if existing_index is not None:
            source_details[existing_index]["occurrences"] += 1
            continue
        if len(source_details) >= 3:
            continue
        source_indexes[token] = len(source_details)
        source_details.append(
            {
                "software": software,
                "source_file": source_file,
                "raw_label": raw_label,
                "raw_identifier1": raw_identifier1,
                "raw_identifier2": raw_identifier2,
                "raw_comment": raw_comment,
                "occurrences": 1,
            }
        )
    return details_by_species


def _build_import_naming_workspace(
    session_id: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> ImportNamingWorkspace:
    meta = metadata if metadata is not None else store.load_metadata(session_id)
    df = store.load_frame(session_id)
    config = _load_processing_config(meta)
    identifier1_sources = sorted(
        {
            str(value).strip()
            for value in df.get("Identifier 1", pd.Series(dtype=object)).dropna().tolist()
            if str(value).strip()
        }
    )
    species_sources = sorted(
        {
            str(value).strip()
            for value in _get_species_series(df).dropna().tolist()
            if str(value).strip()
        }
    )
    return ImportNamingWorkspace(
        species_name_map=dict(config.species_name_map),
        identifier1_name_map=dict(config.identifier1_name_map),
        identifier1_sources=identifier1_sources,
        species_sources=species_sources,
        species_source_details=_build_species_source_details(df, meta),
    )


@app.get(
    "/sessions/{session_id}/import/naming",
    response_model=ImportNamingWorkspace,
)
def get_import_naming_workspace(session_id: str) -> ImportNamingWorkspace:
    _session_exists_or_404(session_id)
    return _build_import_naming_workspace(session_id)


@app.post(
    "/sessions/{session_id}/import/naming",
    response_model=ImportNamingWorkspace,
)
def set_import_naming_workspace(
    session_id: str,
    update: ImportNamingUpdate,
) -> ImportNamingWorkspace:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    previous_config = _load_processing_config(metadata)
    current = previous_config.model_dump()
    current["species_name_map"] = update.species_name_map
    current["identifier1_name_map"] = update.identifier1_name_map
    normalized_update = normalize_processing_config(current)
    identifier_translations: dict[str, str] = {}
    for source in set(previous_config.identifier1_name_map) | set(normalized_update.identifier1_name_map):
        previous_target = previous_config.identifier1_name_map.get(source, source)
        next_target = normalized_update.identifier1_name_map.get(source, source)
        if previous_target != next_target:
            identifier_translations[previous_target] = next_target
    current["selected_identifier"] = identifier_translations.get(
        previous_config.selected_identifier,
        previous_config.selected_identifier,
    )
    current_export = dict(current.get("export", {}))
    current_export["selected_ids"] = list(
        dict.fromkeys(
            identifier_translations.get(value, value)
            for value in previous_config.export.selected_ids
        )
    )
    current["export"] = current_export
    config = ProcessingConfig.model_validate(normalize_processing_config(current).model_dump())
    metadata.setdefault("processing", {})
    metadata["processing"]["config"] = config.model_dump()
    _persist_session_update(
        session_id,
        action="import_names_updated",
        payload=update.model_dump(),
        metadata=metadata,
    )
    return _build_import_naming_workspace(session_id, metadata=metadata)


@app.get("/sessions", response_model=list[SessionSnapshot])
def list_sessions(limit: int = Query(50, ge=1, le=500)) -> list[SessionSnapshot]:
    return [SessionSnapshot.model_validate(item) for item in store.list_sessions(limit=limit)]


@app.get("/sessions/{session_id}", response_model=SessionSnapshot)
def get_session(session_id: str) -> SessionSnapshot:
    _session_exists_or_404(session_id)
    return _to_session_snapshot(session_id)


@app.patch("/sessions/{session_id}/autosave", response_model=SessionSnapshot)
def update_autosave(session_id: str, request: AutosaveSettingsUpdate) -> SessionSnapshot:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    autosave = dict(metadata.get("autosave", {}))
    autosave.update(_autosave_paths_payload(session_id))
    autosave["enabled"] = bool(request.enabled)
    metadata["autosave"] = autosave
    _persist_session_update(
        session_id,
        action="autosave_enabled" if request.enabled else "autosave_disabled",
        payload={"enabled": bool(request.enabled)},
        metadata=metadata,
    )
    return _to_session_snapshot(session_id)


@app.get("/sessions/{session_id}/artifacts/{artifact_kind}")
def get_session_artifact(session_id: str, artifact_kind: str) -> dict[str, Any]:
    _session_exists_or_404(session_id)
    paths = store._paths(session_id)
    artifact_labels = {
        "events": "Event log",
        "snapshot": "Data snapshot",
        "cycles": "Cycle snapshot",
        "metadata": "Session metadata",
        "state": "Session state",
    }
    if artifact_kind not in artifact_labels:
        raise HTTPException(status_code=404, detail="Unknown session artifact")

    if artifact_kind == "events":
        if not paths.log_path.exists():
            raise HTTPException(status_code=404, detail="Event log is not available")
        items: list[dict[str, Any]] = []
        for line in paths.log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                item = {"timestamp": None, "action": "unparsed_event", "payload": {"text": line}}
            if isinstance(item, dict):
                items.append(item)
        visible_items = items[-250:]
        return {
            "kind": artifact_kind,
            "label": artifact_labels[artifact_kind],
            "format": "events",
            "items": to_json_compatible(visible_items),
            "row_count": len(items),
            "truncated": len(items) > len(visible_items),
        }

    if artifact_kind in {"snapshot", "cycles"}:
        csv_path = paths.snapshot_path if artifact_kind == "snapshot" else paths.cycles_snapshot_path
        if not csv_path.exists():
            raise HTTPException(status_code=404, detail=f"{artifact_labels[artifact_kind]} is not available")
        frame = pd.read_csv(csv_path, nrows=100, low_memory=False)
        return {
            "kind": artifact_kind,
            "label": artifact_labels[artifact_kind],
            "format": "table",
            "columns": [str(column) for column in frame.columns],
            "rows": to_json_compatible(frame.to_dict(orient="records")),
            "row_count": store._csv_row_count(csv_path),
            "truncated": store._csv_row_count(csv_path) > len(frame),
        }

    json_path = paths.metadata_path if artifact_kind == "metadata" else paths.session_state_path
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"{artifact_labels[artifact_kind]} is not available")
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"{artifact_labels[artifact_kind]} is invalid JSON") from exc
    return {
        "kind": artifact_kind,
        "label": artifact_labels[artifact_kind],
        "format": "object",
        "data": to_json_compatible(data),
    }


def _clean_uploaded_relative_path(filename: str | None) -> list[str]:
    normalized = str(filename or "").replace("\\", "/")
    result: list[str] = []
    for raw_part in normalized.split("/"):
        part = raw_part.strip()
        if part in ("", ".", "..") or part.endswith(":"):
            continue
        result.append(part)
    return result


def _record_relative_parts(path_parts: list[str], session_id: str) -> list[str] | None:
    session_id_text = str(session_id)
    for index in range(0, max(len(path_parts) - 2, 0)):
        if path_parts[index].lower() != SESSION_RECORD_DIRNAME.lower():
            continue
        if path_parts[index + 1] != session_id_text:
            continue
        relative = path_parts[index + 2 :]
        return relative if relative else None
    if len(path_parts) >= 2 and path_parts[-2] == session_id_text:
        return [path_parts[-1]]
    if len(path_parts) == 1 and path_parts[0].lower() in {
        "metadata.json",
        "snapshot.csv",
        "cycles_snapshot.csv",
        "events.jsonl",
        SESSION_STATE_FILENAME.lower(),
    }:
        return [path_parts[0]]
    return None


@app.post("/sessions/open-file", response_model=SessionSnapshot)
async def open_session_file(file: UploadFile = File(...)) -> SessionSnapshot:
    content = await file.read()
    try:
        payload = json.loads(content.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Session file must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Session file JSON must be an object")
    try:
        session_id = store.register_session_from_state(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    metadata = store.load_metadata(session_id)
    _persist_session_update(
        session_id,
        action="session_file_opened",
        payload={"filename": file.filename or "session_state.json", "resumed": True},
        metadata=metadata,
        resumed=True,
    )
    return _to_session_snapshot(session_id)


@app.post("/sessions/open-folder", response_model=SessionSnapshot)
async def open_session_folder(files: list[UploadFile] = File(...)) -> SessionSnapshot:
    uploaded: list[tuple[list[str], bytes]] = []
    session_state_payload: dict[str, Any] | None = None
    metadata_payload: dict[str, Any] | None = None
    for file in files:
        content = await file.read()
        path_parts = _clean_uploaded_relative_path(file.filename)
        if not path_parts:
            continue
        uploaded.append((path_parts, content))
        if path_parts[-1].lower() == SESSION_STATE_FILENAME.lower() and session_state_payload is None:
            try:
                parsed = json.loads(content.decode("utf-8"))
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                session_state_payload = parsed
        if path_parts[-1].lower() == "metadata.json" and metadata_payload is None:
            try:
                parsed = json.loads(content.decode("utf-8"))
            except Exception:
                parsed = None
            if isinstance(parsed, dict) and str(parsed.get("session_id") or "").strip():
                metadata_payload = parsed

    session_id = str(
        (session_state_payload or {}).get("session_id")
        or (metadata_payload or {}).get("session_id")
        or ""
    ).strip()
    if session_id == "":
        raise HTTPException(status_code=400, detail="Selected folder does not contain a valid session record")

    target_root = (store.root_dir / session_id).resolve()
    record_files: list[tuple[list[str], bytes]] = []
    for path_parts, content in uploaded:
        relative_parts = _record_relative_parts(path_parts, session_id)
        if relative_parts:
            record_files.append((relative_parts, content))

    if record_files:
        target_root.mkdir(parents=True, exist_ok=True)
        for relative_parts, content in record_files:
            target = target_root.joinpath(*relative_parts).resolve()
            if target != target_root and target_root not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    try:
        if (target_root / "metadata.json").exists():
            store.register_existing_session_root(session_id, target_root)
        elif session_state_payload is not None:
            session_id = store.register_session_from_state(session_state_payload)
        else:
            raise FileNotFoundError("Selected folder does not include session metadata")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    metadata = store.load_metadata(session_id)
    _persist_session_update(
        session_id,
        action="session_folder_opened",
        payload={"resumed": True},
        metadata=metadata,
        resumed=True,
    )
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
    _clear_processing_workspace_cache(session_id)
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
    cycles_df = store.load_cycles_frame(session_id)
    metadata = store.load_metadata(session_id)
    processing_config = normalize_processing_config(metadata.get("processing", {}).get("config", {}))
    diagnostics_df = _derive_working_frame(
        df,
        processing_config,
        calibration_meta=metadata.get("calibration", {}),
        edit_state=metadata.get("edit_state", {}),
        cycles_df=cycles_df,
    )
    available_color_params = _candidate_diagnostics_color_columns(diagnostics_df)
    available_z_axes = _candidate_diagnostics_z_columns(diagnostics_df)
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
    filtered_df = _apply_diagnostics_filters(diagnostics_df, identifier_filter, d13_min, d13_max, d18_min, d18_max)
    calibration_meta = metadata.get("calibration", {}) if isinstance(metadata.get("calibration"), dict) else {}
    calibration_config = calibration_meta.get("config", {}) if isinstance(calibration_meta.get("config"), dict) else {}
    selected_standards_raw = calibration_config.get(
        "selected_standards",
        calibration_meta.get("selected_standards", []),
    )
    selected_standards = [
        str(value).strip()
        for value in selected_standards_raw
        if str(value).strip()
    ] if isinstance(selected_standards_raw, list) else []
    fig = create_diagnostic_plots(filtered_df, color_param, selected_standards=selected_standards)
    diagnostic_grid = split_diagnostic_plot_grid(fig)
    fig_3d, _ = _build_isotope_3d_scatter(
        diagnostics_df,
        z_col=z_axis,
        z_label=z_axis,
        color_col=color_param,
        color_label=color_param,
        title="Diagnostics 3D Chart",
        open_circle_identifier="SHP2L",
    )
    identifiers = (
        sorted(
            {
                str(value)
                for value in diagnostics_df.get("Identifier 1", pd.Series(dtype=object)).dropna().tolist()
                if str(value).strip()
            }
        )
        if "Identifier 1" in diagnostics_df.columns
        else []
    )
    figures = {"diagnostics": _figure_json(fig), "diagnostics_3d": _figure_json(fig_3d)}
    diagnostic_grid_meta: list[dict[str, str]] = []
    for index, (group, title, grid_figure) in enumerate(diagnostic_grid):
        key = f"diagnostic_grid_{index}"
        figures[key] = _figure_json(grid_figure)
        diagnostic_grid_meta.append({"key": key, "group": group, "title": title})
    return ChartBundle(
        session_id=session_id,
        figures=figures,
        summary={
            "available_color_params": available_color_params,
            "available_z_axis_options": available_z_axes,
            "available_identifiers": identifiers,
            "d13_bounds": _column_bounds(diagnostics_df, "d 13C/12C  Mean"),
            "d18_bounds": _column_bounds(diagnostics_df, "d 18O/16O  Mean"),
            "active_filters": {
                "color_param": color_param,
                "z_axis": z_axis,
                "identifier_filter": [str(item).strip() for item in identifier_filter if str(item).strip()],
                "d13_min": d13_min,
                "d13_max": d13_max,
                "d18_min": d18_min,
                "d18_max": d18_max,
            },
            "row_count_before": int(len(diagnostics_df)),
            "row_count_after": int(len(filtered_df)),
            "diagnostic_grid": diagnostic_grid_meta,
            "selected_standards": selected_standards,
        },
    )


def _run_calibration_sync(
    session_id: str,
    config: CalibrationConfig,
    context: JobContext | None = None,
) -> SessionSnapshot:
    _session_exists_or_404(session_id)
    if context is not None:
        context.report(5, "loading_session", "Loading calibration data")
    config = normalize_calibration_config(config.model_dump())
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    cycles_df = store.load_cycles_frame(session_id)
    working_df = _ensure_cycle1_signal_difference_columns(df.copy())
    if bool(config.linearity.apply):
        working_df = apply_run_level_linearity_basis_from_cycles(
            working_df,
            cycles_df,
            cycle_intensity_aggregation=getattr(config.linearity, "cycle_intensity_aggregation", "run_median"),
        )
    working_df = _apply_isotope_line_offsets(
        working_df,
        line_1_offset_d13=getattr(config.linearity, "line_1_offset_d13", None),
        line_1_offset_d18=getattr(config.linearity, "line_1_offset_d18", None),
        line_2_offset_d13=getattr(config.linearity, "line_2_offset_d13", None),
        line_2_offset_d18=getattr(config.linearity, "line_2_offset_d18", None),
    )
    identifier1_name_map = _load_processing_config(metadata).identifier1_name_map
    working_df = apply_identifier1_name_map(working_df, identifier1_name_map)
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
    linearity_enabled = bool(config.linearity.apply)
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
        enabled=linearity_enabled and bool(config.linearity.manual_override_enabled),
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
    standards_for_linearity_fit = _with_standard_linearity_residual_columns(
        standards_for_calibration,
        config.selected_standards,
        standards_repo,
        carbonate_material=config.carbonate_material,
    )
    if context is not None:
        context.report(30, "fitting_linearity", "Computing linearity fits")
    outlier_reference_df = outlier_input_df
    fits: dict[str, Any] = {}
    if linearity_enabled:
        fit_input = standards_for_linearity_fit
        intensity_col = _resolve_selected_linearity_intensity_column(
            df=fit_input if fit_input is not None and not fit_input.empty else outlier_input_df,
            use_diff_intensity=config.linearity.use_diff_intensity,
            selected_intensity_col=selected_linearity_intensity_col,
        )
        fit13_intensity_col = d13_offset_intensity_col if d13_offset_intensity_col in fit_input.columns else intensity_col
        fit18_intensity_col = d18_offset_intensity_col if d18_offset_intensity_col in fit_input.columns else intensity_col
        fit13 = (
            _compute_standard_linearity_fit(
                _filter_linearity_fit_input_by_max_intensity(
                    fit_input,
                    fit13_intensity_col,
                    max_sample_intensity,
                ),
                "d13C",
                "d 13C/12C  Mean",
                fit13_intensity_col,
                quadratic=bool(config.linearity.quadratic),
            )
            if fit_input is not None and not fit_input.empty
            else {}
        )
        fit18 = (
            _compute_standard_linearity_fit(
                _filter_linearity_fit_input_by_max_intensity(
                    fit_input,
                    fit18_intensity_col,
                    max_sample_intensity,
                ),
                "d18O",
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
        effective_fits = _apply_manual_linearity_offsets_to_fits(
            fits,
            enabled=bool(config.linearity.manual_override_enabled),
            quadratic=bool(config.linearity.quadratic),
            d13_per_10v=float(config.linearity.manual_d13_per_10v),
            d18_per_10v=float(config.linearity.manual_d18_per_10v),
            d13_per_10v2=float(config.linearity.manual_d13_per_10v2),
            d18_per_10v2=float(config.linearity.manual_d18_per_10v2),
        )
        outlier_reference_df = _promote_linearity_corrected_raw_columns(
            _apply_linearity_correction(outlier_input_df, intensity_col, effective_fits)
        )
    else:
        effective_fits = fits
    clean_stds = _filter_standards_remove_outliers(
        outlier_input_df,
        config.selected_standards,
        config.calibration_type,
        config.sigma_level,
        config.iqr_multiplier,
        config.independent_isotope_outliers,
        outlier_reference_df=outlier_reference_df,
    )
    if context is not None:
        context.report(60, "calibrating", "Applying calibration coefficients")
    standards_source = clean_stds if not clean_stds.empty else standards_for_calibration
    calibration_source = outlier_input_df
    if linearity_enabled and fits:
        standards_source = _promote_linearity_corrected_raw_columns(
            _apply_linearity_correction(
                standards_source,
                effective_fits.get("intensity_col", selected_linearity_intensity_col),
                effective_fits,
            )
        )
        calibration_source = _promote_linearity_corrected_raw_columns(
            _apply_linearity_correction(
                outlier_input_df,
                effective_fits.get("intensity_col", selected_linearity_intensity_col),
                effective_fits,
            )
        )
    calibrated = calibrate_results(
        standards_source if standards_source is not None and not standards_source.empty else calibration_source,
        calibration_source,
        config.selected_standards,
        standards_repo,
        carbonate_material=config.carbonate_material,
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
    # Import-page aliases are a reversible view configuration. Keep the source
    # identifier in the stored snapshot so the naming workspace can still edit
    # or remove the alias after calibration.
    if "Identifier 1" in df.columns and "Identifier 1" in calibrated_for_storage.columns:
        calibrated_for_storage["Identifier 1"] = df["Identifier 1"].reindex(calibrated_for_storage.index)

    metadata["calibration"] = {
        "config": config.model_dump(),
        "coefficients": _compute_calibration_coefficients(
            standards_source,
            config.selected_standards,
            standards_repo,
            carbonate_material=config.carbonate_material,
        ),
        "linearity_fits": fits,
        "selected_standards": config.selected_standards,
    }
    _set_processing_apply_calibration(metadata, True)
    if context is not None:
        context.begin_commit(90, "saving_calibration", "Saving calibrated session")
    _persist_session_update(
        session_id,
        action="calibration_run",
        payload=metadata["calibration"],
        metadata=metadata,
        df=calibrated_for_storage,
        cycles_df=store.load_cycles_frame(session_id),
    )
    return _to_session_snapshot(session_id)


@app.post("/sessions/{session_id}/calibration/run", response_model=SessionSnapshot)
def run_calibration(session_id: str, config: CalibrationConfig) -> SessionSnapshot:
    return _run_calibration_sync(session_id, config)


@app.post("/sessions/{session_id}/calibration/run/jobs", response_model=JobSnapshot, status_code=202)
def submit_calibration_job(session_id: str, config: CalibrationConfig) -> JobSnapshot:
    _session_exists_or_404(session_id)
    config_payload = config.model_dump()

    def _runner(context: JobContext) -> dict[str, Any]:
        snapshot = _run_calibration_sync(
            session_id,
            CalibrationConfig.model_validate(config_payload),
            context,
        )
        return snapshot.model_dump(mode="json")

    return _submit_job("calibration_run", _runner, session_id=session_id)


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
def remove_processing_calibration(
    session_id: str,
    include_all_species_sections: bool = Query(True),
    species_section: list[str] | None = Query(None),
) -> ProcessingWorkspace:
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
    return _build_processing_workspace_response(
        session_id,
        metadata=metadata,
        df=cleaned_df,
        species_section_filter=_processing_species_section_filter(
            include_all_species_sections,
            species_section,
        ),
    )


@app.get("/sessions/{session_id}/calibration/workspace", response_model=CalibrationWorkspace)
def calibration_workspace(session_id: str) -> CalibrationWorkspace:
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return build_calibration_workspace(
        session_id=session_id,
        df=store.load_frame(session_id),
        metadata=store.load_metadata(session_id),
        cycles_df=store.load_cycles_frame(session_id),
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
        cycles_df=store.load_cycles_frame(session_id),
    )


def _compute_preview_coefficients_for_calibration_linearity(
    df: pd.DataFrame,
    config: CalibrationConfig,
    cycles_df: pd.DataFrame | None = None,
    identifier1_name_map: dict[str, str] | None = None,
) -> dict[str, dict[str, float]]:
    if len(config.selected_standards) not in (1, 2):
        return {}

    working_df = _ensure_cycle1_signal_difference_columns(df.copy())
    if bool(config.linearity.apply):
        working_df = apply_run_level_linearity_basis_from_cycles(
            working_df,
            cycles_df,
            cycle_intensity_aggregation=getattr(config.linearity, "cycle_intensity_aggregation", "run_median"),
        )
    working_df = _apply_isotope_line_offsets(
        working_df,
        line_1_offset_d13=getattr(config.linearity, "line_1_offset_d13", None),
        line_1_offset_d18=getattr(config.linearity, "line_1_offset_d18", None),
        line_2_offset_d13=getattr(config.linearity, "line_2_offset_d13", None),
        line_2_offset_d18=getattr(config.linearity, "line_2_offset_d18", None),
    )
    working_df = apply_identifier1_name_map(working_df, identifier1_name_map)

    standards_repo = StandardsRepository.default()
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
    linearity_enabled = bool(config.linearity.apply)
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
        enabled=linearity_enabled and bool(config.linearity.manual_override_enabled),
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
    standards_for_linearity_fit = _with_standard_linearity_residual_columns(
        standards_for_calibration,
        config.selected_standards,
        standards_repo,
        carbonate_material=config.carbonate_material,
    )

    fits: dict[str, Any] = {}
    outlier_reference_df = outlier_input_df
    if linearity_enabled and standards_for_calibration is not None and not standards_for_calibration.empty:
        fit_input = standards_for_linearity_fit
        intensity_col = _resolve_selected_linearity_intensity_column(
            df=fit_input,
            use_diff_intensity=config.linearity.use_diff_intensity,
            selected_intensity_col=selected_linearity_intensity_col,
        )
        fit13_intensity_col = d13_offset_intensity_col if d13_offset_intensity_col in fit_input.columns else intensity_col
        fit18_intensity_col = d18_offset_intensity_col if d18_offset_intensity_col in fit_input.columns else intensity_col
        fits = {
            "d13C": _compute_standard_linearity_fit(
                _filter_linearity_fit_input_by_max_intensity(
                    fit_input,
                    fit13_intensity_col,
                    max_sample_intensity,
                ),
                "d13C",
                "d 13C/12C  Mean",
                fit13_intensity_col,
                quadratic=bool(config.linearity.quadratic),
            ),
            "d18O": _compute_standard_linearity_fit(
                _filter_linearity_fit_input_by_max_intensity(
                    fit_input,
                    fit18_intensity_col,
                    max_sample_intensity,
                ),
                "d18O",
                "d 18O/16O  Mean",
                fit18_intensity_col,
                quadratic=bool(config.linearity.quadratic),
            ),
            "intensity_col": intensity_col,
            "d13_intensity_col": d13_offset_intensity_col,
            "d18_intensity_col": d18_offset_intensity_col,
        }
        effective_fits = _apply_manual_linearity_offsets_to_fits(
            fits,
            enabled=bool(config.linearity.manual_override_enabled),
            quadratic=bool(config.linearity.quadratic),
            d13_per_10v=float(config.linearity.manual_d13_per_10v),
            d18_per_10v=float(config.linearity.manual_d18_per_10v),
            d13_per_10v2=float(config.linearity.manual_d13_per_10v2),
            d18_per_10v2=float(config.linearity.manual_d18_per_10v2),
        )
        outlier_reference_df = _promote_linearity_corrected_raw_columns(
            _apply_linearity_correction(outlier_input_df, intensity_col, effective_fits)
        )
    else:
        effective_fits = fits

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
    if linearity_enabled and fits and standards_source is not None and not standards_source.empty:
        standards_source = _promote_linearity_corrected_raw_columns(
            _apply_linearity_correction(
                standards_source,
                effective_fits.get("intensity_col", selected_linearity_intensity_col),
                effective_fits,
            )
        )
    return _compute_calibration_coefficients(
        standards_source,
        config.selected_standards,
        standards_repo,
        carbonate_material=config.carbonate_material,
    )


@app.post("/sessions/{session_id}/calibration/linearity", response_model=CalibrationWorkspace)
def set_calibration_linearity_config(
    session_id: str,
    payload: CalibrationLinearityUpdateRequest | LinearityConfig,
    summary_only: bool = Query(False),
) -> CalibrationWorkspace:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    source_df = store.load_frame(session_id)
    cycles_df = store.load_cycles_frame(session_id)
    if isinstance(payload, CalibrationLinearityUpdateRequest):
        linearity = payload.linearity
        selected_standards_override = payload.selected_standards
    else:
        linearity = payload
        selected_standards_override = None
    calibration_meta = metadata.setdefault("calibration", {})
    if not isinstance(calibration_meta, dict):
        calibration_meta = {}
        metadata["calibration"] = calibration_meta
    raw_config = calibration_meta.get("config", {})
    config_payload = dict(raw_config) if isinstance(raw_config, dict) else {}
    if (
        (
            "selected_standards" not in config_payload
            or not isinstance(config_payload.get("selected_standards"), list)
            or len(config_payload.get("selected_standards", [])) == 0
        )
        and isinstance(calibration_meta.get("selected_standards"), list)
    ):
        config_payload["selected_standards"] = list(calibration_meta.get("selected_standards", []))
    if selected_standards_override is not None:
        config_payload["selected_standards"] = [
            str(item).strip() for item in selected_standards_override if str(item).strip() != ""
        ]
    config_payload["linearity"] = linearity.model_dump()
    normalized_config = normalize_calibration_config(config_payload)
    preview_workspace = build_calibration_workspace(
        session_id=session_id,
        df=source_df,
        metadata=metadata,
        config_override=normalized_config,
        cycles_df=cycles_df,
        include_figures=not summary_only,
        include_standard_sections=not summary_only,
    )
    calibration_meta["config"] = normalized_config.model_dump()
    calibration_meta["linearity_fits"] = to_json_compatible(preview_workspace.linearity_fits)
    calibration_meta["coefficients"] = _compute_preview_coefficients_for_calibration_linearity(
        source_df,
        normalized_config,
        cycles_df=cycles_df,
        identifier1_name_map=_load_processing_config(metadata).identifier1_name_map,
    )
    calibration_meta["selected_standards"] = list(normalized_config.selected_standards)
    _persist_session_update(
        session_id,
        action="calibration_linearity_config_updated",
        payload={
            "linearity": normalized_config.linearity.model_dump(),
            "selected_standards": list(normalized_config.selected_standards),
        },
        metadata=metadata,
    )
    return preview_workspace


@app.get("/sessions/{session_id}/calibration/charts", response_model=ChartBundle)
def calibration_charts(session_id: str, color_param: str = Query("Date")) -> ChartBundle:
    _session_exists_or_404(session_id)
    workspace = build_calibration_workspace(
        session_id=session_id,
        df=store.load_frame(session_id),
        metadata=store.load_metadata(session_id),
        cycles_df=store.load_cycles_frame(session_id),
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
            cycles_df=store.load_cycles_frame(session_id),
        )
        payload = dict(workspace.figures)
    return ChartBundle(session_id=session_id, figures=payload)


_PROCESSING_WORKSPACE_CACHE_MAX_ENTRIES = 16
_PROCESSING_CONTEXT_CACHE_MAX_ENTRIES = 4
_PROCESSING_SPECIES_CACHE_MAX_ENTRIES = 24
_processing_workspace_cache: OrderedDict[tuple[Any, ...], ProcessingWorkspace] = OrderedDict()
_processing_context_cache: OrderedDict[tuple[Any, ...], ProcessingWorkspaceContext] = OrderedDict()
_processing_species_cache: OrderedDict[tuple[Any, ...], SpeciesSection] = OrderedDict()
_processing_workspace_cache_lock = threading.RLock()


def _processing_workspace_source_signature(session_id: str) -> tuple[Any, ...]:
    paths = store._paths(session_id)

    def _file_signature(path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return int(stat.st_mtime_ns), int(stat.st_size)

    return (
        str(paths.root),
        _file_signature(paths.metadata_path),
        _file_signature(paths.snapshot_path),
        _file_signature(paths.cycles_snapshot_path),
    )


def _processing_workspace_cache_key(
    session_id: str,
    species_section_filter: set[str] | None,
) -> tuple[Any, ...]:
    normalized_filter = None if species_section_filter is None else tuple(sorted(species_section_filter))
    return (str(session_id), _processing_workspace_source_signature(session_id), normalized_filter)


def _clear_processing_workspace_cache(session_id: str | None = None) -> None:
    """Drop cached workspaces after a mutation while retaining a small bounded LRU."""

    with _processing_workspace_cache_lock:
        if session_id is None:
            _processing_workspace_cache.clear()
            _processing_context_cache.clear()
            _processing_species_cache.clear()
            return
        session_key = str(session_id)
        for cache in (
            _processing_workspace_cache,
            _processing_context_cache,
            _processing_species_cache,
        ):
            for key in list(cache):
                if key[0] == session_key:
                    cache.pop(key, None)


def _get_processing_context(
    session_id: str,
    *,
    metadata: dict[str, Any] | None = None,
    df: pd.DataFrame | None = None,
    cycles_df: pd.DataFrame | None = None,
) -> ProcessingWorkspaceContext:
    cache_key = (str(session_id), _processing_workspace_source_signature(session_id))
    with _processing_workspace_cache_lock:
        cached = _processing_context_cache.get(cache_key)
        if cached is not None:
            _processing_context_cache.move_to_end(cache_key)
            return cached

    meta = metadata if metadata is not None else store.load_metadata(session_id)
    frame = df if df is not None else store.load_frame(session_id)
    cycles_frame = cycles_df if cycles_df is not None else store.load_cycles_frame(session_id)
    context = build_processing_context(frame, cycles_frame, meta)
    with _processing_workspace_cache_lock:
        _processing_context_cache[cache_key] = context
        _processing_context_cache.move_to_end(cache_key)
        while len(_processing_context_cache) > _PROCESSING_CONTEXT_CACHE_MAX_ENTRIES:
            _processing_context_cache.popitem(last=False)
    return context


def _build_processing_workspace_response(
    session_id: str,
    metadata: dict[str, Any] | None = None,
    df: pd.DataFrame | None = None,
    cycles_df: pd.DataFrame | None = None,
    species_section_filter: set[str] | None = None,
) -> ProcessingWorkspace:
    if metadata is None and df is None:
        initialized_metadata, initialized_df = _initialize_legacy_processing_ranges_if_needed(session_id)
        if initialized_metadata is not None:
            metadata = initialized_metadata
            df = initialized_df
    cache_key = _processing_workspace_cache_key(session_id, species_section_filter)
    with _processing_workspace_cache_lock:
        cached = _processing_workspace_cache.get(cache_key)
        if cached is not None:
            _processing_workspace_cache.move_to_end(cache_key)
            return cached.model_copy(deep=True)

    context = _get_processing_context(
        session_id,
        metadata=metadata,
        df=df,
        cycles_df=cycles_df,
    )
    workspace = build_processing_workspace_from_context(
        session_id,
        context,
        species_section_filter=species_section_filter,
    )
    with _processing_workspace_cache_lock:
        _processing_workspace_cache[cache_key] = workspace.model_copy(deep=True)
        _processing_workspace_cache.move_to_end(cache_key)
        while len(_processing_workspace_cache) > _PROCESSING_WORKSPACE_CACHE_MAX_ENTRIES:
            _processing_workspace_cache.popitem(last=False)
    return workspace


def _build_processing_species_section_response(session_id: str, species: str) -> SpeciesSection:
    _initialize_legacy_processing_ranges_if_needed(session_id)
    normalized_species = str(species).strip()
    cache_key = (
        str(session_id),
        _processing_workspace_source_signature(session_id),
        normalized_species,
    )
    with _processing_workspace_cache_lock:
        cached = _processing_species_cache.get(cache_key)
        if cached is not None:
            _processing_species_cache.move_to_end(cache_key)
            return cached.model_copy(deep=True)

    context = _get_processing_context(session_id)
    section = build_processing_species_section(context, normalized_species)
    if section is None:
        raise HTTPException(status_code=404, detail=f"Unknown species section: {normalized_species}")
    with _processing_workspace_cache_lock:
        _processing_species_cache[cache_key] = section.model_copy(deep=True)
        _processing_species_cache.move_to_end(cache_key)
        while len(_processing_species_cache) > _PROCESSING_SPECIES_CACHE_MAX_ENTRIES:
            _processing_species_cache.popitem(last=False)
    return section


def _processing_species_section_filter(
    include_all_species_sections: bool,
    species_section: list[str] | None,
) -> set[str] | None:
    if include_all_species_sections:
        return None
    return {
        str(value).strip()
        for value in (species_section or [])
        if str(value).strip() != ""
    }


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


def _numeric_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if np.isfinite(parsed):
        return parsed
    return None


def _processing_linearity_preview_rows(
    df: pd.DataFrame,
    fits: dict[str, Any],
    selected_intensity_col: str,
) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    intensity_cols = {
        CYCLE1_SIGNAL_SAMP44_COL,
        CYCLE1_SIGNAL_REF44_COL,
        CYCLE1_SIGNAL_DIFF44_COL,
        CYCLE1_SIGNAL_DIFF45_COL,
        CYCLE1_SIGNAL_DIFF46_COL,
        CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL,
        CYCLE1_SIGNAL_SYMMETRIC_MISMATCH44_COL,
        CYCLE1_SIGNAL_RELATIVE_MISMATCH44_COL,
        CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
        str(selected_intensity_col or "").strip(),
    }
    if isinstance(fits, dict):
        for key in ("intensity_col", "d13_intensity_col", "d18_intensity_col"):
            value = str(fits.get(key, "")).strip()
            if value:
                intensity_cols.add(value)
        for isotope_key in ("d13C", "d18O"):
            fit = fits.get(isotope_key, {})
            if isinstance(fit, dict):
                for key in ("primary_col", "secondary_col"):
                    value = str(fit.get(key, "")).strip()
                    if value:
                        intensity_cols.add(value)
    attribute_cols = {
        "Date",
        "Identifier 1",
        "Identifier 2",
        "Species",
        "Comment",
        "Label",
        "Raw Comment",
        "Raw Label",
        SAMPLE_SEQUENCE_COL,
        VALID_CYCLES_COL,
        CYCLE1_SIGNAL_SAMP44_COL,
        CYCLE1_SIGNAL_REF44_COL,
        CYCLE1_SIGNAL_DIFF44_COL,
        CYCLE1_SIGNAL_DIFF45_COL,
        CYCLE1_SIGNAL_DIFF46_COL,
        CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL,
        CYCLE1_SIGNAL_RELATIVE_MISMATCH44_COL,
        CYCLE1_SIGNAL_SYMMETRIC_MISMATCH44_COL,
        CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
        "p_no_acid",
        "total_co2",
        "p_gases",
        "leak_rate",
        "Line",
        "d 13C/12C  Mean",
        "d 18O/16O  Mean",
        *intensity_cols,
    }

    def _preview_attribute_value(value: Any) -> str | float | None:
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
        if isinstance(value, pd.Timestamp):
            return value.date().isoformat()
        if isinstance(value, (np.integer, np.floating)):
            numeric = float(value)
            return numeric if np.isfinite(numeric) else None
        if isinstance(value, (int, float)):
            numeric = float(value)
            return numeric if np.isfinite(numeric) else None
        if hasattr(value, "isoformat"):
            try:
                return str(value.isoformat())
            except Exception:
                pass
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = None
        if numeric is not None and np.isfinite(numeric):
            return numeric
        return str(value)

    def _preview_text(value: Any, fallback: Any = "") -> str:
        """Return clean identity text without leaking pandas null sentinels."""
        for candidate in (value, fallback):
            if candidate is None:
                continue
            try:
                if pd.isna(candidate):
                    continue
            except (TypeError, ValueError):
                pass
            return str(candidate).strip()
        return ""

    line_col = next((col for col in df.columns if str(col).strip().lower() == "line"), None)
    rows: list[dict[str, Any]] = []
    for row_label, row in df.iterrows():
        intensities = {
            col: _numeric_or_none(row.get(col))
            for col in sorted(intensity_cols)
            if col and col in df.columns
        }
        attributes = {
            col: _preview_attribute_value(row.get(col))
            for col in sorted(attribute_cols)
            if col and col in df.columns
        }
        rows.append(
            {
                "row_label": str(row_label),
                "identifier1": _preview_text(row.get("Identifier 1")),
                "identifier2": _preview_text(row.get("Identifier 2")),
                "species": _preview_text(row.get("Species"), row.get("Identifier 1")),
                "collector_status": _preview_text(row.get("Collector Status")),
                "line": _numeric_or_none(row.get(line_col)) if line_col else None,
                "d13_raw": _numeric_or_none(row.get("d 13C/12C  Mean")),
                "d18_raw": _numeric_or_none(row.get("d 18O/16O  Mean")),
                "d13_calibrated": _numeric_or_none(row.get("d13C_calibrated")),
                "d18_calibrated": _numeric_or_none(row.get("d18O_calibrated")),
                "signal": _numeric_or_none(row.get(CYCLE1_SIGNAL_SAMP44_COL)),
                "leak_rate": _numeric_or_none(row.get("leak_rate")),
                "d13_cycles_excluded": _numeric_or_none(row.get("d13C Cycles Excluded")),
                "d18_cycles_excluded": _numeric_or_none(row.get("d18O Cycles Excluded")),
                "intensities": intensities,
                "attributes": attributes,
            }
        )
    return rows


@app.get("/sessions/{session_id}/processing/workspace", response_model=ProcessingWorkspace)
def processing_workspace(
    session_id: str,
    include_all_species_sections: bool = Query(True),
    species_section: list[str] | None = Query(None),
) -> ProcessingWorkspace:
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return _build_processing_workspace_response(
        session_id,
        species_section_filter=_processing_species_section_filter(
            include_all_species_sections,
            species_section,
        ),
    )


@app.get("/sessions/{session_id}/processing/species-section", response_model=SpeciesSection)
def processing_species_section(
    session_id: str,
    species: str = Query(min_length=1),
) -> SpeciesSection:
    _session_exists_or_404(session_id)
    return _build_processing_species_section_response(session_id, species)


@app.get("/sessions/{session_id}/processing/linearity-preview-data", response_model=ProcessingLinearityPreviewData)
def processing_linearity_preview_data(session_id: str) -> ProcessingLinearityPreviewData:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    cycles_df = store.load_cycles_frame(session_id)
    calibration = _processing_calibration_meta(metadata)
    linearity_cfg = calibration.get("config", {}).get("linearity", {}) if isinstance(calibration.get("config"), dict) else {}
    fits = calibration.get("linearity_fits", {}) if isinstance(calibration.get("linearity_fits", {}), dict) else {}
    selected_intensity_col = _resolve_selected_linearity_intensity_column(
        df=df,
        use_diff_intensity=bool(linearity_cfg.get("use_diff_intensity", False)),
        selected_intensity_col=linearity_cfg.get("intensity_col"),
    )
    base_df = _ensure_cycle1_signal_difference_columns(_ensure_sample_sequence_column(df.copy()))
    if cycles_df is not None and not cycles_df.empty:
        base_df = apply_run_level_linearity_basis_from_cycles(
            base_df,
            cycles_df,
            cycle_intensity_aggregation=str(linearity_cfg.get("cycle_intensity_aggregation", "run_median")),
        )
    return ProcessingLinearityPreviewData(
        session_id=session_id,
        intensity_col=selected_intensity_col,
        fits=to_json_compatible(fits),
        coefficients=to_json_compatible(calibration.get("coefficients", {})),
        rows=_processing_linearity_preview_rows(base_df, fits, selected_intensity_col),
    )


@app.post("/sessions/{session_id}/processing/config", response_model=ProcessingWorkspace)
def set_processing_config(
    session_id: str,
    config: ProcessingConfig,
    include_all_species_sections: bool = Query(True),
    species_section: list[str] | None = Query(None),
) -> ProcessingWorkspace:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    metadata.setdefault("processing", {})
    metadata["processing"]["config"] = config.model_dump()
    metadata["processing"]["ranges_source"] = "user"
    _persist_session_update(
        session_id,
        action="processing_config_updated",
        payload=config.model_dump(),
        metadata=metadata,
    )
    return _build_processing_workspace_response(
        session_id,
        metadata=metadata,
        species_section_filter=_processing_species_section_filter(
            include_all_species_sections,
            species_section,
        ),
    )


@app.post("/sessions/{session_id}/processing/config/jobs", response_model=JobSnapshot, status_code=202)
def submit_processing_config_job(session_id: str, config: ProcessingConfig) -> JobSnapshot:
    _session_exists_or_404(session_id)
    config_payload = config.model_dump()

    def _runner(context: JobContext) -> dict[str, Any]:
        context.begin_commit(10, "saving_config", "Saving processing configuration")
        workspace = set_processing_config(
            session_id,
            ProcessingConfig.model_validate(config_payload),
            include_all_species_sections=False,
            species_section=None,
        )
        return workspace.model_dump(mode="json")

    return _submit_job("processing_config", _runner, session_id=session_id)


def _apply_processing_edit_batch(
    session_id: str,
    edits: list[EditAction],
    *,
    species_section_filter: set[str] | None,
    context: JobContext | None = None,
) -> ProcessingWorkspace:
    _session_exists_or_404(session_id)
    if context is not None:
        context.report(5, "loading_session", "Loading processing data")
    metadata = store.load_metadata(session_id)
    updated_df = store.load_frame(session_id)
    cycles_df = store.load_cycles_frame(session_id)
    config = _load_processing_config(metadata)
    updated_edit_state = metadata.setdefault(
        "edit_state",
        {
            "edited_rows": [],
            "original_delta_values": {},
            "original_missing_delta_tokens": [],
            "original_std_values": {},
            "original_missing_std_tokens": [],
            "original_identifier1_values": {},
            "original_identifier2_values": {},
            "original_species_values": {},
            "manual_outlier_overrides": {},
            "restored_delta_tokens": [],
        },
    )
    calibration = _processing_calibration_meta(metadata)
    coeffs = calibration.get("coefficients", {})
    fits = calibration.get("linearity_fits", {})
    linearity_cfg = calibration.get("config", {}).get("linearity", {}) if isinstance(calibration.get("config"), dict) else {}
    try:
        for index, edit in enumerate(edits):
            if context is not None:
                progress = 15.0 + (55.0 * index / max(1, len(edits)))
                context.report(progress, "applying_edits", f"Applying edit {index + 1} of {len(edits)}")
            interpolation_source_df = (
                _build_interpolation_source_frame(
                    updated_df,
                    config,
                    calibration_meta=calibration,
                    edit_state=updated_edit_state,
                    target_row_tokens={str(target.row_label) for target in edit.targets},
                    cycles_df=cycles_df,
                )
                if edit.action == "interpolate"
                else None
            )
            updated_df, updated_edit_state = apply_edit_action(
                updated_df,
                updated_edit_state,
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
    if context is not None:
        context.begin_commit(75, "saving_edits", "Saving processing edits")
    _persist_session_update(
        session_id,
        action="processing_edit" if len(edits) == 1 else "processing_edit_batch",
        payload=edits[0].model_dump() if len(edits) == 1 else {"edits": [edit.model_dump() for edit in edits]},
        metadata=metadata,
        df=updated_df,
        cycles_df=cycles_df,
    )
    return _build_processing_workspace_response(
        session_id,
        metadata=metadata,
        df=updated_df,
        cycles_df=cycles_df,
        species_section_filter=species_section_filter,
    )


@app.post("/sessions/{session_id}/processing/edit", response_model=ProcessingWorkspace)
def edit_processing(
    session_id: str,
    edit: EditAction,
    include_all_species_sections: bool = Query(True),
    species_section: list[str] | None = Query(None),
) -> ProcessingWorkspace:
    return _apply_processing_edit_batch(
        session_id,
        [edit],
        species_section_filter=_processing_species_section_filter(
            include_all_species_sections,
            species_section,
        ),
    )


@app.post("/sessions/{session_id}/processing/edits", response_model=ProcessingWorkspace)
def edit_processing_batch(
    session_id: str,
    request: EditBatchRequest,
    include_all_species_sections: bool = Query(True),
    species_section: list[str] | None = Query(None),
) -> ProcessingWorkspace:
    """Apply several UI draft edits with one dataframe write and workspace rebuild."""

    return _apply_processing_edit_batch(
        session_id,
        request.edits,
        species_section_filter=_processing_species_section_filter(
            include_all_species_sections,
            species_section,
        ),
    )


@app.post("/sessions/{session_id}/processing/edits/jobs", response_model=JobSnapshot, status_code=202)
def submit_processing_edit_batch_job(session_id: str, request: EditBatchRequest) -> JobSnapshot:
    _session_exists_or_404(session_id)
    edits_payload = [edit.model_dump() for edit in request.edits]

    def _runner(context: JobContext) -> dict[str, Any]:
        workspace = _apply_processing_edit_batch(
            session_id,
            [EditAction.model_validate(payload) for payload in edits_payload],
            species_section_filter=set(),
            context=context,
        )
        return workspace.model_dump(mode="json")

    return _submit_job("processing_edits", _runner, session_id=session_id)


@app.post("/sessions/{session_id}/processing/cycle-diagnostics", response_model=CycleDiagnosticsPayload)
def processing_cycle_diagnostics(session_id: str, request: CycleDiagnosticsRequest) -> CycleDiagnosticsPayload:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    cycles_df = store.load_cycles_frame(session_id)
    calibration = _processing_calibration_meta(metadata)
    config = _load_processing_config(metadata)
    row_label = _coerce_row_label(request.target.row_label, df)
    target = build_target_info(df, row_label, request.target.isotope_key, metadata.get("edit_state", {}))
    if target is None:
        raise HTTPException(status_code=404, detail="Unknown processing target")
    current_value, current_method = _processing_target_current_value_and_method(
        df,
        target,
        config,
        metadata.get("edit_state", {}),
        cycles_df,
    )
    if current_value is not None:
        target["current_value"] = current_value
    target["current_method"] = current_method
    target["linearity_corrected_value"] = _processing_target_linearity_corrected_value(
        df,
        target,
        calibration,
        config,
        metadata.get("edit_state", {}),
        cycles_df,
    )
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
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )


@app.get("/sessions/{session_id}/processing/charts", response_model=ChartBundle)
def processing_charts(session_id: str) -> ChartBundle:
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return _workspace_to_chart_bundle(_build_processing_workspace_response(session_id))


def _build_final_client_output_frame(
    client_source: pd.DataFrame,
    request: ExportRequest,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    client_df = _round_client_output_columns(
        _build_client_output_frame(
            client_source,
            comment_map=request.comment_map,
            identifier_source=request.client_output_identifier_source,
            sample_source=request.client_output_sample_source,
            species_source=request.client_output_species_source,
            show_sequence=bool(request.show_sequence),
        )
    )
    client_df["__identifier_2_key"] = client_source.get(
        "Identifier 2",
        pd.Series(index=client_source.index, dtype=object),
    ).fillna("").astype(str).to_numpy()
    if "Sequence" in client_df.columns:
        client_df = client_df.sort_values(
            by=["Sequence", "Identifier", "Sample #"],
            ascending=[True, True, True],
            na_position="last",
            kind="mergesort",
        ).reset_index(drop=True)

    if request.client_output_rows is not None:
        visible_columns = [column for column in client_df.columns if not str(column).startswith("__")]
        reviewed_rows: list[dict[str, Any]] = []
        for row in request.client_output_rows:
            reviewed = {column: row.get(column) for column in visible_columns}
            reviewed["__identifier_2_key"] = row.get("__identifier_2_key", row.get("Sample #", ""))
            reviewed[DUPLICATE_QUALITY_KEY_COLUMN] = bool(
                row.get(DUPLICATE_QUALITY_KEY_COLUMN, False)
            )
            reviewed_rows.append(reviewed)
        client_df = pd.DataFrame(
            reviewed_rows,
            columns=[
                *visible_columns,
                "__identifier_2_key",
                DUPLICATE_QUALITY_KEY_COLUMN,
            ],
        )
        for column in CLIENT_OUTPUT_NUMERIC_COLUMNS:
            if column in client_df.columns:
                client_df[column] = pd.to_numeric(client_df[column], errors="coerce").round(2)
        if "Species" in client_df.columns and not is_raw_client_output_source(request.client_output_species_source):
            client_df["Species"] = client_df["Species"].map(format_academic_species_name)

    client_df = client_df.reset_index(drop=True)
    duplicate_summary = summarize_client_output_duplicates(client_df)
    return client_df, duplicate_summary


def _prepare_client_output_preview(
    session_id: str,
    request: ExportRequest,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    _session_exists_or_404(session_id)
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    cycles_df = store.load_cycles_frame(session_id)
    config = normalize_processing_config(metadata.get("processing", {}).get("config", {}))
    config.export = ProcessingExportConfig.model_validate(
        request.model_dump(exclude={"output_type", "restore_stdev", "restore_stdev_cap", "client_output_rows", "email_language"})
    )

    calibration = _processing_calibration_meta(metadata)
    working_df = _derive_working_frame(
        df,
        config,
        calibration_meta=calibration,
        edit_state=metadata.get("edit_state", {}),
        cycles_df=cycles_df,
    )
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

    if request.restore_stdev:
        restore_stdev_cap = float(request.restore_stdev_cap)
        if not np.isfinite(restore_stdev_cap):
            raise HTTPException(status_code=400, detail="restore_stdev_cap must be a finite number")
        client_source = _cap_stdev_columns_for_client_output(client_source, restore_stdev_cap)

    return _build_final_client_output_frame(client_source, request)


@app.post("/sessions/{session_id}/exports/client-output/preview", response_model=ClientOutputPreviewResponse)
def preview_client_output(session_id: str, request: ExportRequest) -> ClientOutputPreviewResponse:
    client_df, duplicate_summary = _prepare_client_output_preview(session_id, request)
    export_df = client_df.drop(
        columns=[column for column in client_df.columns if str(column).startswith("__")],
        errors="ignore",
    )
    preview_df = client_df.astype(object).where(pd.notna(client_df), None)
    client_name = str(request.client_name or "")
    return ClientOutputPreviewResponse(
        columns=[str(column) for column in export_df.columns],
        numeric_columns=[column for column in CLIENT_OUTPUT_NUMERIC_COLUMNS if column in export_df.columns],
        rows=[dict(row) for row in preview_df.to_dict(orient="records")],
        total_rows=int(len(export_df)),
        email_subject=build_client_email_subject(client_name, export_df, language=request.email_language),
        filename=_build_client_filename(client_name, export_df),
        duplicate_row_count=int(duplicate_summary["duplicate_row_count"]),
        duplicate_identifier1_identifier2_species_values=[
            str(value)
            for value in duplicate_summary["duplicate_identifier1_identifier2_species_values"]
        ],
        duplicate_rows=[dict(row) for row in duplicate_summary["duplicate_rows"]],
        duplicate_row_indexes=[
            int(index)
            for index, is_duplicate in duplicate_summary["duplicate_row_mask"].items()
            if bool(is_duplicate)
        ],
    )


@app.post("/sessions/{session_id}/exports/client-output/duplicates", response_model=ClientOutputDuplicateCheckResponse)
def check_client_output_duplicates(session_id: str, request: ExportRequest) -> ClientOutputDuplicateCheckResponse:
    _, duplicate_summary = _prepare_client_output_preview(session_id, request)
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


def _export_dataset_sync(
    session_id: str,
    request: ExportRequest,
    context: JobContext | None = None,
) -> Response:
    _session_exists_or_404(session_id)
    if context is not None:
        context.report(5, "loading_session", "Loading export data")
    metadata = store.load_metadata(session_id)
    df = store.load_frame(session_id)
    cycles_df = store.load_cycles_frame(session_id)
    config = normalize_processing_config(metadata.get("processing", {}).get("config", {}))
    config.export = ProcessingExportConfig.model_validate(
        request.model_dump(exclude={"output_type", "restore_stdev", "restore_stdev_cap", "client_output_rows", "email_language"})
    )
    metadata.setdefault("processing", {})
    metadata["processing"]["config"] = config.model_dump()

    calibration = _processing_calibration_meta(metadata)
    working_df = _derive_working_frame(
        df,
        config,
        calibration_meta=calibration,
        edit_state=metadata.get("edit_state", {}),
        cycles_df=cycles_df,
    )
    data_to_process = _selected_processing_rows(working_df, list(config.export.selected_ids))
    if context is not None:
        context.report(30, "filtering_rows", "Filtering export rows")
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
    if context is not None:
        context.report(55, "preparing_workbook", "Preparing workbook tables")

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
    if context is not None:
        context.report(70, "writing_workbook", "Writing Excel workbook")
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
        if selected_standards and "Identifier 1" in client_source.columns:
            client_source = client_source.loc[~client_source["Identifier 1"].isin(selected_standards)].copy()
        final_client_df, _ = _build_final_client_output_frame(client_source, request)
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
            species_source=request.client_output_species_source,
            client_output_df=final_client_df,
        )
    else:
        workbook, filename = build_dataset_workbook_bytes(
            main_data,
            outliers=outliers_df,
            selected_standards=selected_standards,
            client_name=config.export.client_name,
            statistics_rows=statistics_rows,
        )
    if context is not None:
        context.begin_commit(95, "finalizing_export", "Finalizing export")
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


@app.post("/sessions/{session_id}/exports/dataset")
def export_dataset(session_id: str, request: ExportRequest) -> Response:
    return _export_dataset_sync(session_id, request)


@app.post("/sessions/{session_id}/exports/dataset/jobs", response_model=JobSnapshot, status_code=202)
def submit_export_job(session_id: str, request: ExportRequest) -> JobSnapshot:
    _session_exists_or_404(session_id)
    request_payload = request.model_dump()

    def _runner(context: JobContext) -> dict[str, Any]:
        response = _export_dataset_sync(
            session_id,
            ExportRequest.model_validate(request_payload),
            context,
        )
        disposition = response.headers.get("Content-Disposition", "")
        filename_match = re.search(r'filename="?([^";]+)"?', disposition, flags=re.IGNORECASE)
        filename = filename_match.group(1) if filename_match else "irms_export.xlsx"
        context.set_artifact(bytes(response.body), filename, response.media_type or "application/octet-stream")
        return {
            "filename": filename,
            "download_url": f"/jobs/{context.job_id}/download",
        }

    return _submit_job("dataset_export", _runner, session_id=session_id)
