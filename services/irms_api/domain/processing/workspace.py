from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from ..calibration.core import _resolve_selected_linearity_intensity_column, _resolve_linearity_reference_intensity
from ..constants import CYCLE1_SIGNAL_SAMP44_COL
from ..contracts import (
    ProcessingAvailableValues,
    ProcessingConfig,
    ProcessingEditState,
    ProcessingExportState,
    ProcessingLinearityOverrideConfig,
    ProcessingOverlayConfig,
    ProcessingWorkspace,
    ProcessingWorkspaceConfig,
)
from ..shared.dataframe import _get_species_series, _parse_numeric_token
from ..standards import StandardsRepository
from .charts import build_overview_figures, build_species_sections
from .outliers import (
    RangeConfig,
    _partial_saturation_isotope_masks,
    _signal_in_range_mask,
    build_category_masks,
    build_outlier_tables,
    build_processing_summary,
)


def normalize_processing_config(raw: dict[str, Any] | None) -> ProcessingWorkspaceConfig:
    payload = dict(raw or {})
    if "iqr_multiplier_data" not in payload and "irq_multiplier_data" in payload:
        payload["iqr_multiplier_data"] = payload.pop("irq_multiplier_data")
    method_raw = str(payload.get("statistical_outlier_method", "")).strip().upper()
    if method_raw == "IRQ":
        payload["statistical_outlier_method"] = "IQR"
    if "overlays" not in payload:
        payload["overlays"] = {
            "show_statistical_outliers": bool(payload.pop("show_statistical_outliers", False)),
            "show_range_outliers": bool(payload.pop("show_range_outliers", False)),
            "show_manual_outliers": bool(payload.pop("show_manual_outliers", False)),
            "show_saturated_collectors": bool(payload.pop("show_saturated_collectors", True)),
            "show_saturated_samples": bool(payload.pop("show_saturated_samples", True)),
            "show_failed_samples": bool(payload.pop("show_failed_samples", True)),
        }
    if "manual_linearity_override" not in payload:
        payload["manual_linearity_override"] = {
            "enabled": bool(payload.pop("manual_linearity_override_enabled", False)),
            "d13_per_10v": float(payload.pop("manual_linearity_d13_per_10v", 0.0) or 0.0),
            "d18_per_10v": float(payload.pop("manual_linearity_d18_per_10v", 0.0) or 0.0),
        }
    if "export" not in payload:
        payload["export"] = {
            "include_outliers": bool(payload.pop("include_outliers", False)),
            "selected_ids": payload.pop("selected_ids", ["All"]) or ["All"],
            "interpolate_outliers": bool(payload.pop("interpolate_outliers_export", False)),
            "client_name": payload.pop("client_name", None),
            "comment_map": payload.pop("comment_map", payload.pop("comment_replacements", {})) or {},
        }
    return ProcessingWorkspaceConfig.model_validate(payload)


def _build_export_filename(config: ProcessingWorkspaceConfig) -> str:
    selected_ids = list(config.export.selected_ids)
    include_outliers = bool(config.export.include_outliers)
    parts: list[str] = []
    if "All" not in selected_ids:
        if len(selected_ids) <= 3:
            parts.append(f"ID{'_'.join(str(item) for item in selected_ids)}")
        else:
            parts.append(f"ID{len(selected_ids)}selected")
    parts.append(f"{'with' if include_outliers else 'without'}_outliers")
    filename = f"dataset_{'_'.join(parts)}.xlsx"
    if include_outliers and bool(config.export.interpolate_outliers):
        filename = re.sub(r"\.xlsx$", "_interpolated.xlsx", filename, flags=re.IGNORECASE)
    return filename


def _candidate_color_columns(df: pd.DataFrame) -> list[str]:
    preferred = [
        "Date_ordinal",
        "Date",
        "Identifier 1",
        "Identifier 2",
        "Species",
        "Comment",
        "Label",
        CYCLE1_SIGNAL_SAMP44_COL,
        "leak_rate",
        "d 13C/12C  Mean",
        "d 18O/16O  Mean",
        "Line",
    ]
    return [col for col in preferred if col in df.columns]


def _candidate_z_columns(df: pd.DataFrame) -> list[str]:
    preferred = [
        CYCLE1_SIGNAL_SAMP44_COL,
        "1  Cycle Int  Diff Samp-Ref  44",
        "leak_rate",
        "d 13C/12C  Mean",
        "d 18O/16O  Mean",
        "Date_ordinal",
        "Line",
    ]
    return [col for col in preferred if col in df.columns]


def _apply_manual_linearity_override(
    df: pd.DataFrame,
    override: ProcessingLinearityOverrideConfig,
    intensity_col: str = CYCLE1_SIGNAL_SAMP44_COL,
    linearity_fits: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if df is None or df.empty or not bool(override.enabled) or intensity_col not in df.columns:
        return df
    work = df.copy()
    intensity = pd.to_numeric(work[intensity_col], errors="coerce")
    valid_intensity = np.isfinite(intensity)

    def _apply_single_column(column_name: str, isotope_key: str, slope_per_10v: float) -> None:
        slope_num = pd.to_numeric(pd.Series([slope_per_10v]), errors="coerce").iloc[0]
        if column_name not in work.columns or not np.isfinite(slope_num):
            return
        x_ref = _resolve_linearity_reference_intensity(
            work,
            isotope_key,
            fits=linearity_fits,
            intensity_col=intensity_col,
        )
        slope_per_v = float(slope_num) / 10.0
        values = pd.to_numeric(work[column_name], errors="coerce")
        work[column_name] = (values - slope_per_v * (intensity - float(x_ref))).where(np.isfinite(values) & valid_intensity)

    _apply_single_column("d 13C/12C  Mean", "d13C", override.d13_per_10v)
    _apply_single_column("d13C_calibrated", "d13C", override.d13_per_10v)
    _apply_single_column("d 18O/16O  Mean", "d18O", override.d18_per_10v)
    _apply_single_column("d18O_calibrated", "d18O", override.d18_per_10v)
    return work


def _derive_working_frame(
    df: pd.DataFrame,
    config: ProcessingWorkspaceConfig,
    calibration_meta: dict[str, Any] | None = None,
) -> pd.DataFrame:
    work = df.copy()
    if "Identifier 2" in work.columns:
        work["Sequence"] = work["Identifier 2"].apply(_parse_numeric_token)
    calibration = calibration_meta or {}
    fits = calibration.get("linearity_fits", {})
    use_diff_intensity = bool(calibration.get("config", {}).get("linearity", {}).get("use_diff_intensity", False))
    intensity_col = _resolve_selected_linearity_intensity_column(df=work, use_diff_intensity=use_diff_intensity)
    return _apply_manual_linearity_override(
        work,
        config.manual_linearity_override,
        intensity_col=intensity_col,
        linearity_fits=fits,
    )


def _build_plot_frames(
    working_df: pd.DataFrame,
    config: ProcessingWorkspaceConfig,
    standards_to_exclude: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = working_df.copy()
    signal_mask = _signal_in_range_mask(base.get(CYCLE1_SIGNAL_SAMP44_COL), config.signal_range)
    leak_mask = pd.to_numeric(base.get("leak_rate"), errors="coerce").between(*config.leak_range, inclusive="both")
    d13c_mask = pd.to_numeric(base.get("d 13C/12C  Mean"), errors="coerce").between(*config.d13c_range, inclusive="both")
    d18o_mask = pd.to_numeric(base.get("d 18O/16O  Mean"), errors="coerce").between(*config.d18o_range, inclusive="both")
    sat_masks = _partial_saturation_isotope_masks(base)
    partial_recovered_keep = pd.Series(False, index=base.index, dtype=bool)
    if bool(config.overlays.show_saturated_collectors):
        partial_recovered_keep = signal_mask & leak_mask & (
            (sat_masks["d13C"] & d13c_mask) | (sat_masks["d18O"] & d18o_mask)
        )
    filtered = base.loc[(signal_mask & leak_mask & d13c_mask & d18o_mask) | partial_recovered_keep].copy()
    unfiltered = base.copy()
    if standards_to_exclude and "Identifier 1" in base.columns:
        filtered = filtered[~filtered["Identifier 1"].astype(str).isin({str(item) for item in standards_to_exclude})].copy()
        unfiltered = unfiltered[~unfiltered["Identifier 1"].astype(str).isin({str(item) for item in standards_to_exclude})].copy()
    return filtered, unfiltered


def _exclude_outliers_from_plot_base(
    filtered_df: pd.DataFrame,
    unfiltered_df: pd.DataFrame,
    range_config: RangeConfig,
    edit_state: dict[str, Any] | None,
    sigma_level: float,
    statistical_outlier_method: str = "Z-Score",
    iqr_multiplier: float = 1.5,
) -> pd.DataFrame:
    """Keep base traces free of non-statistical outliers.

    Statistical filtering is applied per-isotope at chart-build time.
    """
    if filtered_df is None or filtered_df.empty or unfiltered_df is None or unfiltered_df.empty:
        return filtered_df.copy()

    category_masks = build_category_masks(
        unfiltered_df,
        range_config,
        edit_state=edit_state,
        sigma_level=float(sigma_level),
        statistical_outlier_method=statistical_outlier_method,
        iqr_multiplier=float(iqr_multiplier),
    )
    outlier_keys = [
        "d13C Range",
        "d18O Range",
        "Signal Intensity",
        "Leak Rate",
        "Partially Saturated Collectors",
        "Fully Saturated Collectors",
        "Failed Sample",
    ]
    outlier_mask = pd.Series(False, index=unfiltered_df.index, dtype=bool)
    for key in outlier_keys:
        outlier_mask = outlier_mask | category_masks.get(key, pd.Series(False, index=unfiltered_df.index, dtype=bool))
    keep_mask = ~outlier_mask.reindex(filtered_df.index, fill_value=False).astype(bool)
    return filtered_df.loc[keep_mask].copy()


def _selected_processing_rows(df: pd.DataFrame, selected_ids: list[str]) -> pd.DataFrame:
    if "All" in selected_ids or "Identifier 1" not in df.columns:
        return df.copy()
    return df[df["Identifier 1"].astype(str).isin({str(item) for item in selected_ids})].copy()


def build_processing_workspace(
    session_id: str,
    df: pd.DataFrame,
    cycles_df: pd.DataFrame | None,
    metadata: dict[str, Any],
) -> ProcessingWorkspace:
    del cycles_df  # available for future workspace extensions; diagnostics use the dedicated endpoint
    config = normalize_processing_config(metadata.get("processing", {}).get("config", {}))
    edit_state = dict(
        metadata.get(
            "edit_state",
            {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
        )
    )
    calibration = metadata.get("calibration", {})
    standards_repo = StandardsRepository.default()
    selected_standards = [str(item) for item in calibration.get("selected_standards", [])]
    all_standards = sorted(set(standards_repo.standards_list()) | set(selected_standards))

    working_df = _derive_working_frame(df, config, calibration_meta=calibration)
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
    data_to_process = _selected_processing_rows(working_df, list(config.export.selected_ids))
    summary = build_processing_summary(
        data_to_process,
        range_config,
        edit_state=edit_state,
        standards_to_exclude=all_standards,
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    species_col = "Species" if "Species" in data_to_process.columns else "Identifier 1"
    category_masks = build_category_masks(
        data_to_process,
        range_config,
        edit_state=edit_state,
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    outlier_tables = build_outlier_tables(data_to_process, category_masks, species_col, scope_title="Selected Data")

    scoped_filtered = filtered_df.copy()
    if config.selected_identifier != "All" and "Identifier 1" in scoped_filtered.columns:
        scoped_filtered = scoped_filtered[scoped_filtered["Identifier 1"].astype(str) == str(config.selected_identifier)].copy()
    scoped_unfiltered = unfiltered_df.copy()
    if config.selected_identifier != "All" and "Identifier 1" in scoped_unfiltered.columns:
        scoped_unfiltered = scoped_unfiltered[scoped_unfiltered["Identifier 1"].astype(str) == str(config.selected_identifier)].copy()
    overview_figures = build_overview_figures(
        scoped_filtered,
        scoped_filtered,
        scoped_unfiltered,
        config,
        edit_state=edit_state,
    )
    species_sections = build_species_sections(filtered_df, unfiltered_df, config, edit_state)

    identifiers = sorted(
        {
            str(value)
            for value in filtered_df.get("Identifier 1", pd.Series(dtype=object)).dropna().tolist()
            if str(value).strip() != ""
        }
    )
    export_identifiers = sorted(
        {
            str(value)
            for value in working_df.get("Identifier 1", pd.Series(dtype=object)).dropna().tolist()
            if str(value).strip() != ""
        }
    )
    available_values = ProcessingAvailableValues(
        identifiers=["All", *identifiers],
        export_identifiers=["All", *export_identifiers],
        species=sorted(
            {
                str(value)
                for value in _get_species_series(unfiltered_df).dropna().astype(str).tolist()
                if str(value).strip() != ""
            }
        ),
        color_params=_candidate_color_columns(working_df),
        z_axis_options=_candidate_z_columns(working_df),
    )
    export_state = ProcessingExportState(
        filename=_build_export_filename(config),
        client_name=config.export.client_name,
        selected_ids=list(config.export.selected_ids),
        include_outliers=bool(config.export.include_outliers),
        interpolate_outliers=bool(config.export.interpolate_outliers),
    )
    return ProcessingWorkspace(
        session_id=session_id,
        config=config,
        summary=summary,
        available_values=available_values,
        overview_figures=overview_figures,
        species_sections=species_sections,
        outlier_tables=outlier_tables,
        edit_state=ProcessingEditState.model_validate(edit_state),
        export_state=export_state,
    )
