from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from ..calibration.core import (
    _apply_manual_linearity_override_to_standards,
    _apply_manual_linearity_offsets_to_fits,
    _apply_isotope_line_offsets,
    _apply_linearity_correction,
    _resolve_linearity_intensity_column_for_fits,
    _resolve_manual_linearity_override_intensity,
    _resolve_linearity_reference_intensity,
    _linearity_primary_offset_scale,
    _linearity_secondary_offset_scale,
    _with_isotope_linearity_intensity_columns,
)
from ..constants import (
    CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL,
    CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
    CYCLE1_SIGNAL_RELATIVE_MISMATCH44_COL,
    CYCLE1_SIGNAL_SAMP44_COL,
    CYCLE1_SIGNAL_SYMMETRIC_MISMATCH44_COL,
    VALID_CYCLES_COL,
)
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
from ..shared.dataframe import _ensure_cycle1_pressure_weighted_mismatch_column, _get_species_series, _parse_numeric_token
from ..standards import StandardsRepository
from .cycles import (
    apply_run_level_linearity_basis_from_cycles,
    build_target_info,
    normalize_saturation_correction_method,
    resolve_saturation_correction_value_for_target,
    saturation_correction_method_for_isotope,
)
from .charts import build_overview_figures, build_species_sections
from .outliers import (
    RangeConfig,
    _is_row_edited,
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
    legacy_saturation_method = normalize_saturation_correction_method(
        payload.get("saturation_correction_method", "reference_gas_intensity")
    )
    payload["saturation_correction_method"] = legacy_saturation_method
    payload["saturation_correction_method_d13"] = normalize_saturation_correction_method(
        payload.get("saturation_correction_method_d13", legacy_saturation_method)
    )
    payload["saturation_correction_method_d18"] = normalize_saturation_correction_method(
        payload.get("saturation_correction_method_d18", legacy_saturation_method)
    )
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
            "use_diff_intensity": bool(payload.pop("manual_linearity_use_diff_intensity", False)),
            "quadratic": bool(payload.pop("manual_linearity_quadratic", False)),
            "max_sample_signal": payload.pop("manual_linearity_max_sample_signal", None),
            "d13_per_10v": float(payload.pop("manual_linearity_d13_per_10v", 0.0) or 0.0),
            "d18_per_10v": float(payload.pop("manual_linearity_d18_per_10v", 0.0) or 0.0),
            "d13_per_10v2": float(payload.pop("manual_linearity_d13_per_10v2", 0.0) or 0.0),
            "d18_per_10v2": float(payload.pop("manual_linearity_d18_per_10v2", 0.0) or 0.0),
        }
    else:
        override_payload = payload.get("manual_linearity_override")
        if isinstance(override_payload, dict) and "use_diff_intensity" not in override_payload:
            override_payload["use_diff_intensity"] = bool(payload.pop("manual_linearity_use_diff_intensity", False))
        if isinstance(override_payload, dict) and "quadratic" not in override_payload:
            override_payload["quadratic"] = bool(payload.pop("manual_linearity_quadratic", False))
        if isinstance(override_payload, dict) and "max_sample_signal" not in override_payload:
            override_payload["max_sample_signal"] = payload.pop("manual_linearity_max_sample_signal", None)
        if isinstance(override_payload, dict) and "d13_per_10v2" not in override_payload:
            override_payload["d13_per_10v2"] = float(payload.pop("manual_linearity_d13_per_10v2", 0.0) or 0.0)
        if isinstance(override_payload, dict) and "d18_per_10v2" not in override_payload:
            override_payload["d18_per_10v2"] = float(payload.pop("manual_linearity_d18_per_10v2", 0.0) or 0.0)
    if "export" not in payload:
        payload["export"] = {
            "include_outliers": bool(payload.pop("include_outliers", False)),
            "selected_ids": payload.pop("selected_ids", ["All"]) or ["All"],
            "interpolate_outliers": bool(payload.pop("interpolate_outliers_export", False)),
            "client_name": payload.pop("client_name", None),
            "comment_map": payload.pop("comment_map", payload.pop("comment_replacements", {})) or {},
        }
    payload["species_name_map"] = _normalize_species_name_map(payload.get("species_name_map"))
    return ProcessingWorkspaceConfig.model_validate(payload)


def _normalize_species_name_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, str] = {}
    for source, target in raw.items():
        source_label = str(source).strip()
        target_label = str(target).strip()
        if not source_label or not target_label or source_label == target_label:
            continue
        normalized[source_label] = target_label
    return normalized


def _apply_species_name_map(df: pd.DataFrame, species_name_map: dict[str, str] | None) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    normalized_map = _normalize_species_name_map(species_name_map)
    if not normalized_map:
        return df
    work = df.copy()
    species = _get_species_series(work).reindex(work.index).fillna("").astype(str).map(str.strip)
    work["Species"] = species.map(lambda value: normalized_map.get(value, value))
    return work


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
        "Date",
        "Identifier 1",
        "Identifier 2",
        "Species",
        "Comment",
        "Label",
        VALID_CYCLES_COL,
        CYCLE1_SIGNAL_SAMP44_COL,
        "1  Cycle Int  Diff Samp-Ref  44",
        CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL,
        CYCLE1_SIGNAL_RELATIVE_MISMATCH44_COL,
        CYCLE1_SIGNAL_SYMMETRIC_MISMATCH44_COL,
        CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
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
        CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL,
        CYCLE1_SIGNAL_RELATIVE_MISMATCH44_COL,
        CYCLE1_SIGNAL_SYMMETRIC_MISMATCH44_COL,
        CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
        "leak_rate",
        "d 13C/12C  Mean",
        "d 18O/16O  Mean",
        "Date_ordinal",
        "Line",
    ]
    return [col for col in preferred if col in df.columns]


def _range_config_from_processing_config(config: ProcessingWorkspaceConfig) -> RangeConfig:
    return RangeConfig(
        signal_range=config.signal_range,
        leak_range=config.leak_range,
        d13c_range=config.d13c_range,
        d18o_range=config.d18o_range,
        partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
    )


def _restore_edited_raw_isotope_values(
    work: pd.DataFrame,
    source_df: pd.DataFrame,
    edit_state: dict[str, Any] | None,
    *,
    skip_partial_linearity_restore: bool = False,
) -> pd.DataFrame:
    if work is None or work.empty or source_df is None or source_df.empty:
        return work
    edited_rows = {
        str(token).strip()
        for token in (edit_state or {}).get("edited_rows", [])
        if str(token).strip() != ""
    }
    if not edited_rows:
        return work
    raw_cols = (("d13C", "d 13C/12C  Mean"), ("d18O", "d 18O/16O  Mean"))
    partial_masks = (
        _partial_saturation_isotope_masks(work)
        if bool(skip_partial_linearity_restore)
        else {}
    )
    for row_label in work.index:
        if str(row_label) not in edited_rows or row_label not in source_df.index:
            continue
        for isotope_key, col in raw_cols:
            if col in work.columns and col in source_df.columns:
                if bool(skip_partial_linearity_restore):
                    partial_mask = partial_masks.get(
                        isotope_key,
                        pd.Series(False, index=work.index, dtype=bool),
                    ).reindex(work.index, fill_value=False).astype(bool)
                    if bool(partial_mask.get(row_label, False)):
                        continue
                work.at[row_label, col] = source_df.at[row_label, col]
    return work


def _apply_manual_linearity_override(
    df: pd.DataFrame,
    override: ProcessingLinearityOverrideConfig,
    intensity_col: str = CYCLE1_SIGNAL_SAMP44_COL,
    linearity_fits: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if df is None or df.empty or not bool(override.enabled) or intensity_col not in df.columns:
        return df
    work = df.copy()
    intensity, normalized_intensity = _resolve_manual_linearity_override_intensity(
        work,
        intensity_col,
        use_diff_intensity=bool(override.use_diff_intensity),
    )
    intensity_series = pd.to_numeric(pd.Series(intensity, index=work.index), errors="coerce")
    max_sample_signal_raw = pd.to_numeric(pd.Series([override.max_sample_signal]), errors="coerce").iloc[0]
    within_max_sample_signal = pd.Series(True, index=work.index, dtype=bool)
    if np.isfinite(max_sample_signal_raw) and CYCLE1_SIGNAL_SAMP44_COL in work.columns:
        sample_signal = pd.to_numeric(work[CYCLE1_SIGNAL_SAMP44_COL], errors="coerce")
        within_max_sample_signal = np.isfinite(sample_signal) & (sample_signal <= float(max_sample_signal_raw))
    valid_intensity = intensity_series.notna() & within_max_sample_signal

    def _apply_single_column(
        column_name: str,
        isotope_key: str,
        slope_per_10v: float,
        quad_per_10v2: float,
    ) -> None:
        slope_num = pd.to_numeric(pd.Series([slope_per_10v]), errors="coerce").iloc[0]
        quad_num = pd.to_numeric(pd.Series([quad_per_10v2]), errors="coerce").iloc[0]
        if column_name not in work.columns:
            return
        if not np.isfinite(slope_num) and not np.isfinite(quad_num):
            return
        if normalized_intensity:
            finite_scope = intensity_series[valid_intensity]
            x_ref = float(finite_scope.median()) if not finite_scope.empty else 0.0
        else:
            x_ref = _resolve_linearity_reference_intensity(
                work,
                isotope_key,
                fits=linearity_fits,
                intensity_col=intensity_col,
            )
        values = pd.to_numeric(work[column_name], errors="coerce")
        if bool(override.quadratic):
            delta = pd.Series(0.0, index=work.index, dtype=float)
            if np.isfinite(slope_num):
                slope_per_v = float(slope_num) / _linearity_primary_offset_scale(intensity_col)
                delta = delta + slope_per_v * (intensity_series - float(x_ref))
            if np.isfinite(quad_num):
                # Secondary coefficient scaling depends on the selected basis.
                quad_per_v2 = float(quad_num) / _linearity_secondary_offset_scale(intensity_col)
                delta = delta + quad_per_v2 * (np.square(intensity_series) - float(x_ref) ** 2)
        else:
            slope_per_v = float(slope_num) / _linearity_primary_offset_scale(intensity_col)
            delta = slope_per_v * (intensity_series - float(x_ref))
        apply_mask = np.isfinite(values) & valid_intensity & np.isfinite(delta)
        corrected = values.copy()
        corrected.loc[apply_mask] = values.loc[apply_mask] - delta.loc[apply_mask]
        work[column_name] = corrected

    _apply_single_column("d 13C/12C  Mean", "d13C", override.d13_per_10v, override.d13_per_10v2)
    _apply_single_column("d13C_calibrated", "d13C", override.d13_per_10v, override.d13_per_10v2)
    _apply_single_column("d 18O/16O  Mean", "d18O", override.d18_per_10v, override.d18_per_10v2)
    _apply_single_column("d18O_calibrated", "d18O", override.d18_per_10v, override.d18_per_10v2)
    return work


def _effective_outlier_mask(
    df: pd.DataFrame,
    config: ProcessingWorkspaceConfig,
    edit_state: dict[str, Any] | None = None,
) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=bool)
    category_masks = build_category_masks(
        df,
        _range_config_from_processing_config(config),
        edit_state=edit_state,
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    outlier_keys = [
        "Statistical",
        "d13C Range",
        "d18O Range",
        "Signal Intensity",
        "Leak Rate",
        "Partially Saturated Collectors",
        "Fully Saturated Collectors",
        "Failed Sample",
        "Manual Override",
    ]
    outlier_mask = pd.Series(False, index=df.index, dtype=bool)
    for key in outlier_keys:
        outlier_mask = outlier_mask | category_masks.get(key, pd.Series(False, index=df.index, dtype=bool))
    return outlier_mask.fillna(False).astype(bool)


def _recompute_calibration_after_modifications(
    df: pd.DataFrame,
    config: ProcessingWorkspaceConfig,
    calibration_meta: dict[str, Any] | None = None,
    edit_state: dict[str, Any] | None = None,
) -> pd.DataFrame:
    work = df.copy()
    del config
    del edit_state
    calibration = calibration_meta or {}
    coeffs = calibration.get("coefficients", {})
    if not isinstance(coeffs, dict) or not coeffs:
        return work
    standards_repo = StandardsRepository.default()
    standards = {str(item).strip().upper() for item in standards_repo.standards_list() if str(item).strip() != ""}
    standards.update(
        str(item).strip().upper()
        for item in calibration.get("selected_standards", [])
        if str(item).strip() != ""
    )
    if "Identifier 1" in work.columns:
        identifier_labels = work["Identifier 1"].fillna("").astype(str).str.strip().str.upper()
        standards_mask = identifier_labels.isin(standards)
    else:
        standards_mask = pd.Series(False, index=work.index, dtype=bool)
    eligible_mask = ~standards_mask

    isotopes: dict[str, tuple[str, str, str]] = {
        "d13C": ("d 13C/12C  Mean", "d13C_calibrated", "d13C_calibrated_linearity_corrected"),
        "d18O": ("d 18O/16O  Mean", "d18O_calibrated", "d18O_calibrated_linearity_corrected"),
    }
    for isotope_key, (raw_col, cal_col, corrected_col) in isotopes.items():
        if raw_col not in work.columns:
            continue
        isotope_coeffs = coeffs.get(isotope_key, {}) if isinstance(coeffs, dict) else {}
        slope = pd.to_numeric(pd.Series([isotope_coeffs.get("slope")]), errors="coerce").iloc[0]
        intercept = pd.to_numeric(pd.Series([isotope_coeffs.get("intercept")]), errors="coerce").iloc[0]
        if not (np.isfinite(slope) and np.isfinite(intercept)):
            continue
        raw_values = pd.to_numeric(work[raw_col], errors="coerce")
        calibrated_values = (float(slope) * raw_values) + float(intercept)
        keep_mask = eligible_mask & np.isfinite(raw_values)
        output = pd.Series(np.nan, index=work.index, dtype=float)
        output.loc[keep_mask] = calibrated_values.loc[keep_mask]
        work[cal_col] = output
        work[corrected_col] = output
    return work


def _apply_saturation_correction(
    df: pd.DataFrame,
    config: ProcessingWorkspaceConfig,
    cycles_df: pd.DataFrame | None = None,
    edit_state: dict[str, Any] | None = None,
    row_labels: set[Any] | None = None,
) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    work = df.copy()
    row_scope = {str(label) for label in row_labels} if row_labels is not None else None
    enabled = bool(getattr(config, "enable_saturation_correction", False))
    sat_masks = _partial_saturation_isotope_masks(work)
    isotope_columns = {
        "d13C": ("d 13C/12C  Mean", "d 13C/12C  Std Dev", "__d13C_current_method"),
        "d18O": ("d 18O/16O  Mean", "d 18O/16O  Std Dev", "__d18O_current_method"),
    }
    for isotope_key, (raw_col, std_col, method_col) in isotope_columns.items():
        if raw_col not in work.columns:
            continue
        if method_col not in work.columns:
            work[method_col] = "imported"
        partial_mask = sat_masks.get(isotope_key, pd.Series(False, index=work.index, dtype=bool)).reindex(
            work.index,
            fill_value=False,
        ).astype(bool)
        if row_scope is not None:
            partial_mask = partial_mask & work.index.to_series().astype(str).isin(row_scope)
        work.loc[partial_mask, method_col] = "first_valid_cycle"
        for row_label in work.index[partial_mask]:
            if _is_row_edited(row_label, edit_state):
                work.at[row_label, method_col] = "edited"
                continue
            if not enabled or cycles_df is None or cycles_df.empty:
                continue
            target = build_target_info(work, row_label, isotope_key, edit_state)
            if target is None:
                continue
            method = saturation_correction_method_for_isotope(config, isotope_key)
            value, resolved_method, std_dev = resolve_saturation_correction_value_for_target(work, cycles_df, target, method)
            if value is not None:
                work.at[row_label, raw_col] = float(value)
                work.at[row_label, method_col] = resolved_method
                if std_dev is not None and std_col in work.columns:
                    work.at[row_label, std_col] = float(std_dev)
    return work


def _derive_working_frame(
    df: pd.DataFrame,
    config: ProcessingWorkspaceConfig,
    calibration_meta: dict[str, Any] | None = None,
    edit_state: dict[str, Any] | None = None,
    cycles_df: pd.DataFrame | None = None,
    saturation_row_labels: set[Any] | None = None,
) -> pd.DataFrame:
    work = _ensure_cycle1_pressure_weighted_mismatch_column(df.copy())
    if "Identifier 2" in work.columns:
        work["Sequence"] = work["Identifier 2"].apply(_parse_numeric_token)
    calibration = calibration_meta or {}
    fits = calibration.get("linearity_fits", {})
    linearity_cfg = calibration.get("config", {}).get("linearity", {}) if isinstance(calibration.get("config"), dict) else {}
    linearity_enabled = bool(linearity_cfg.get("apply", False))
    if linearity_enabled:
        work = apply_run_level_linearity_basis_from_cycles(
            work,
            cycles_df,
            row_labels=saturation_row_labels,
            cycle_intensity_aggregation=linearity_cfg.get("cycle_intensity_aggregation", "run_median"),
        )
    work = _apply_saturation_correction(
        work,
        config,
        cycles_df=cycles_df,
        edit_state=edit_state,
        row_labels=saturation_row_labels,
    )
    work = _apply_isotope_line_offsets(
        work,
        line_1_offset_d13=linearity_cfg.get("line_1_offset_d13"),
        line_1_offset_d18=linearity_cfg.get("line_1_offset_d18"),
        line_2_offset_d13=linearity_cfg.get("line_2_offset_d13"),
        line_2_offset_d18=linearity_cfg.get("line_2_offset_d18"),
    )
    calibration_use_diff_intensity = bool(linearity_cfg.get("use_diff_intensity", False))
    calibration_intensity_col = _resolve_linearity_intensity_column_for_fits(
        fits=fits if isinstance(fits, dict) else None,
        df=work,
        use_diff_intensity=calibration_use_diff_intensity,
        selected_intensity_col=linearity_cfg.get("intensity_col"),
    )
    work, d13_offset_intensity_col, d18_offset_intensity_col = _with_isotope_linearity_intensity_columns(
        work,
        calibration_intensity_col,
        line_1_offset=float(linearity_cfg.get("line_1_offset", 0.0) or 0.0),
        line_2_offset=float(linearity_cfg.get("line_2_offset", 0.0) or 0.0),
    )
    calibration_override_scope = (
        sorted(
            {
                str(value).strip()
                for value in work.get("Identifier 1", pd.Series(dtype=object)).dropna().tolist()
                if str(value).strip() != ""
            }
        )
        if "Identifier 1" in work.columns
        else []
    )
    calibration_override_intensity_col = (
        d13_offset_intensity_col
        if d13_offset_intensity_col == d18_offset_intensity_col
        else calibration_intensity_col
    )
    work = _apply_manual_linearity_override_to_standards(
        work,
        calibration_override_scope,
        enabled=linearity_enabled and bool(linearity_cfg.get("manual_override_enabled", False)),
        d13_per_10v=float(linearity_cfg.get("manual_d13_per_10v", 0.0) or 0.0),
        d18_per_10v=float(linearity_cfg.get("manual_d18_per_10v", 0.0) or 0.0),
        d13_per_10v2=float(linearity_cfg.get("manual_d13_per_10v2", 0.0) or 0.0),
        d18_per_10v2=float(linearity_cfg.get("manual_d18_per_10v2", 0.0) or 0.0),
        quadratic=bool(linearity_cfg.get("quadratic", False)),
        use_diff_intensity=calibration_use_diff_intensity,
        selected_intensity_col=calibration_override_intensity_col,
    )
    if linearity_enabled and isinstance(fits, dict) and fits:
        fit_payload = dict(fits)
        fit_payload.setdefault("d13_intensity_col", d13_offset_intensity_col)
        fit_payload.setdefault("d18_intensity_col", d18_offset_intensity_col)
        fit_payload = _apply_manual_linearity_offsets_to_fits(
            fit_payload,
            enabled=bool(linearity_cfg.get("manual_override_enabled", False)),
            quadratic=bool(linearity_cfg.get("quadratic", False)),
            d13_per_10v=float(linearity_cfg.get("manual_d13_per_10v", 0.0) or 0.0),
            d18_per_10v=float(linearity_cfg.get("manual_d18_per_10v", 0.0) or 0.0),
            d13_per_10v2=float(linearity_cfg.get("manual_d13_per_10v2", 0.0) or 0.0),
            d18_per_10v2=float(linearity_cfg.get("manual_d18_per_10v2", 0.0) or 0.0),
        )
        corrected = _apply_linearity_correction(work, calibration_intensity_col, fit_payload)
        apply_to_partial = bool(getattr(config, "apply_shared_linearity_to_partially_saturated", True))
        partial_masks = _partial_saturation_isotope_masks(corrected) if not apply_to_partial else {}
        if "d13C_linearity_corrected" in corrected.columns:
            corrected_values = pd.to_numeric(corrected["d13C_linearity_corrected"], errors="coerce")
            apply_mask = corrected_values.notna()
            if not apply_to_partial:
                apply_mask = apply_mask & ~partial_masks.get(
                    "d13C",
                    pd.Series(False, index=corrected.index, dtype=bool),
                ).reindex(corrected.index, fill_value=False).astype(bool)
            if bool(apply_mask.any()):
                raw_values = pd.to_numeric(corrected["d 13C/12C  Mean"], errors="coerce")
                raw_values.loc[apply_mask] = corrected_values.loc[apply_mask]
                corrected["d 13C/12C  Mean"] = raw_values
        if "d18O_linearity_corrected" in corrected.columns:
            corrected_values = pd.to_numeric(corrected["d18O_linearity_corrected"], errors="coerce")
            apply_mask = corrected_values.notna()
            if not apply_to_partial:
                apply_mask = apply_mask & ~partial_masks.get(
                    "d18O",
                    pd.Series(False, index=corrected.index, dtype=bool),
                ).reindex(corrected.index, fill_value=False).astype(bool)
            if bool(apply_mask.any()):
                raw_values = pd.to_numeric(corrected["d 18O/16O  Mean"], errors="coerce")
                raw_values.loc[apply_mask] = corrected_values.loc[apply_mask]
                corrected["d 18O/16O  Mean"] = raw_values
        work = corrected
    work = _recompute_calibration_after_modifications(
        work,
        config,
        calibration_meta=calibration,
        edit_state=edit_state,
    )
    # Keep edited raw values stable in charts/tables even when calibration-side
    # linearity correction is enabled.
    work = _restore_edited_raw_isotope_values(
        work,
        df,
        edit_state,
        skip_partial_linearity_restore=linearity_enabled
        and bool(getattr(config, "apply_shared_linearity_to_partially_saturated", True)),
    )
    work = _recompute_calibration_after_modifications(
        work,
        config,
        calibration_meta=calibration,
        edit_state=edit_state,
    )
    return _apply_species_name_map(work, config.species_name_map)


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
    species_section_filter: set[str] | None = None,
) -> ProcessingWorkspace:
    config = normalize_processing_config(metadata.get("processing", {}).get("config", {}))
    edit_state = dict(
        metadata.get(
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
    )
    calibration = metadata.get("calibration", {})
    processing_meta = metadata.get("processing", {})
    apply_calibration = bool(processing_meta.get("apply_calibration", True)) if isinstance(processing_meta, dict) else True
    calibration_for_processing = calibration if apply_calibration else {}
    standards_repo = StandardsRepository.default()
    selected_standards = [str(item) for item in calibration.get("selected_standards", [])]
    all_standards = sorted(set(standards_repo.standards_list()) | set(selected_standards))
    source_species_values = sorted(
        {
            str(value).strip()
            for value in _get_species_series(df).dropna().astype(str).tolist()
            if str(value).strip() != ""
        }
    )

    working_df = _derive_working_frame(
        df,
        config,
        calibration_meta=calibration_for_processing,
        edit_state=edit_state,
        cycles_df=cycles_df,
    )
    available_color_params = _candidate_color_columns(working_df)
    if available_color_params and config.color_param not in available_color_params:
        config.color_param = "Date" if "Date" in available_color_params else available_color_params[0]
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
    outlier_tables = build_outlier_tables(data_to_process, category_masks, species_col, scope_title="Data")

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
    species_sections = build_species_sections(
        filtered_df,
        unfiltered_df,
        config,
        edit_state,
        species_section_filter=species_section_filter,
    )

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
        species=source_species_values,
        color_params=available_color_params,
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
