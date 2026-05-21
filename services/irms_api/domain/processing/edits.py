from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..calibration.core import (
    _apply_manual_linearity_offsets_to_fits,
    _apply_isotope_line_offsets,
    _linearity_correction_delta,
    _resolve_linearity_intensity_column_for_fits,
    _with_isotope_linearity_intensity_columns,
)
from ..constants import CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL, CYCLE1_SIGNAL_SYMMETRIC_MISMATCH44_COL
from ..contracts import EditAction
from ..shared.dataframe import _parse_numeric_token


def _get_isotope_columns(isotope_key: str) -> tuple[str | None, str | None, str | None]:
    key = str(isotope_key).strip()
    if key == "d13C":
        return ("d 13C/12C  Mean", "d13C_calibrated", "d13C_calibrated_linearity_corrected")
    if key == "d18O":
        return ("d 18O/16O  Mean", "d18O_calibrated", "d18O_calibrated_linearity_corrected")
    return (None, None, None)


def _get_isotope_std_column(isotope_key: str) -> str | None:
    key = str(isotope_key).strip()
    if key == "d13C":
        return "d 13C/12C  Std Dev"
    if key == "d18O":
        return "d 18O/16O  Std Dev"
    return None


def _estimate_calibration_affine(
    df: pd.DataFrame,
    raw_col: str,
    cal_col: str,
    exclude_row_label: Any | None = None,
) -> tuple[float | None, float | None]:
    if raw_col not in df.columns or cal_col not in df.columns:
        return (None, None)
    raw_series = pd.to_numeric(df[raw_col], errors="coerce")
    cal_series = pd.to_numeric(df[cal_col], errors="coerce")
    valid = raw_series.notna() & cal_series.notna()
    if exclude_row_label is not None and exclude_row_label in valid.index:
        valid.loc[exclude_row_label] = False
    x = raw_series[valid]
    y = cal_series[valid]
    if len(x) >= 2:
        x_vals = x.to_numpy(dtype=float)
        y_vals = y.to_numpy(dtype=float)
        if np.nanstd(x_vals) > 0:
            slope, intercept = np.polyfit(x_vals, y_vals, 1)
            if np.isfinite(slope) and np.isfinite(intercept):
                return (float(slope), float(intercept))
    if len(x) == 1:
        xv = float(x.iloc[0])
        yv = float(y.iloc[0])
        if np.isfinite(xv) and np.isfinite(yv):
            return (1.0, yv - xv)
    return (None, None)


def _refresh_collector_status_after_delta_edit(df: pd.DataFrame, row_label: Any) -> pd.DataFrame:
    work = df.copy()
    if row_label not in work.index or "Collector Status" not in work.columns:
        return work
    status_raw = work.at[row_label, "Collector Status"]
    status = "" if pd.isna(status_raw) else str(status_raw).strip()
    if status == "Fully Saturated Collectors":
        return work
    d13 = pd.to_numeric(pd.Series([work.at[row_label, "d 13C/12C  Mean"]]), errors="coerce").iloc[0]
    d18 = pd.to_numeric(pd.Series([work.at[row_label, "d 18O/16O  Mean"]]), errors="coerce").iloc[0]
    has_d13 = bool(pd.notna(d13))
    has_d18 = bool(pd.notna(d18))
    if not has_d13 and not has_d18:
        work.at[row_label, "Collector Status"] = "Failed Sample"
    elif status == "Failed Sample":
        work.at[row_label, "Collector Status"] = "Partially Saturated Collectors"
    return work


def _refresh_calibrated_after_delta_edit(
    df: pd.DataFrame,
    row_label: Any,
    isotope_key: str,
    calibration_coefficients: dict[str, Any] | None = None,
    linearity_fits: dict[str, Any] | None = None,
    linearity_config: dict[str, Any] | None = None,
    previous_raw: float | None = None,
    previous_calibrated: float | None = None,
) -> pd.DataFrame:
    work = df.copy()
    raw_col, cal_col, corrected_col = _get_isotope_columns(isotope_key)
    raw_corrected_col = "d13C_linearity_corrected" if isotope_key == "d13C" else "d18O_linearity_corrected" if isotope_key == "d18O" else None
    if row_label not in work.index or raw_col is None or cal_col is None:
        return work
    if raw_col not in work.columns or cal_col not in work.columns:
        return work
    new_raw = pd.to_numeric(pd.Series([work.at[row_label, raw_col]]), errors="coerce").iloc[0]
    if pd.isna(new_raw):
        work.at[row_label, cal_col] = np.nan
        if corrected_col in work.columns:
            work.at[row_label, corrected_col] = np.nan
        return work
    slope = None
    intercept = None
    linearity_cfg = linearity_config or {}
    fits = linearity_fits or {}
    fits_for_correction = (
        _apply_manual_linearity_offsets_to_fits(
            fits if isinstance(fits, dict) else {},
            enabled=bool(linearity_cfg.get("manual_override_enabled", False)),
            quadratic=bool(linearity_cfg.get("quadratic", False)),
            d13_per_10v=float(linearity_cfg.get("manual_d13_per_10v", 0.0) or 0.0),
            d18_per_10v=float(linearity_cfg.get("manual_d18_per_10v", 0.0) or 0.0),
            d13_per_10v2=float(linearity_cfg.get("manual_d13_per_10v2", 0.0) or 0.0),
            d18_per_10v2=float(linearity_cfg.get("manual_d18_per_10v2", 0.0) or 0.0),
        )
        if isinstance(fits, dict)
        else {}
    )
    fit = fits_for_correction.get(str(isotope_key).strip(), {}) if isinstance(fits_for_correction, dict) else {}
    apply_linearity_before_calibration = bool(linearity_cfg.get("apply")) and isinstance(fits, dict) and bool(fits)
    context = _apply_isotope_line_offsets(
        work,
        line_1_offset_d13=linearity_cfg.get("line_1_offset_d13"),
        line_1_offset_d18=linearity_cfg.get("line_1_offset_d18"),
        line_2_offset_d13=linearity_cfg.get("line_2_offset_d13"),
        line_2_offset_d18=linearity_cfg.get("line_2_offset_d18"),
    )
    line_adjusted_raw = (
        pd.to_numeric(pd.Series([context.at[row_label, raw_col]]), errors="coerce").iloc[0]
        if row_label in context.index and raw_col in context.columns
        else new_raw
    )
    delta = np.nan
    if apply_linearity_before_calibration:
        resolved_intensity_col = _resolve_linearity_intensity_column_for_fits(
            fits=fits,
            df=context,
            use_diff_intensity=bool(linearity_cfg.get("use_diff_intensity", False)),
            selected_intensity_col=linearity_cfg.get("intensity_col"),
        )
        context, d13_offset_intensity_col, d18_offset_intensity_col = _with_isotope_linearity_intensity_columns(
            context,
            resolved_intensity_col,
            line_1_offset=float(linearity_cfg.get("line_1_offset", 0.0) or 0.0),
            line_2_offset=float(linearity_cfg.get("line_2_offset", 0.0) or 0.0),
        )
        iso_key = str(isotope_key).strip()
        if iso_key == "d13C":
            fallback_intensity_col = d13_offset_intensity_col
            configured_intensity_col = str(fits_for_correction.get("d13_intensity_col", "")).strip()
        else:
            fallback_intensity_col = d18_offset_intensity_col
            configured_intensity_col = str(fits_for_correction.get("d18_intensity_col", "")).strip()
        intensity_col = configured_intensity_col if configured_intensity_col in context.columns else fallback_intensity_col
        if intensity_col not in context.columns:
            intensity_col = resolved_intensity_col
        intensity = (
            pd.to_numeric(pd.Series([context.at[row_label, intensity_col]]), errors="coerce").iloc[0]
            if intensity_col in context.columns
            else np.nan
        )
        if not np.isfinite(intensity) and str(fit.get("model", "")).strip() == "two_term":
            intensity = (
                pd.to_numeric(pd.Series([context.at[row_label, CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL]]), errors="coerce").iloc[0]
                if CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL in context.columns
                else np.nan
            )
        if np.isfinite(intensity):
            intensity_series = pd.Series([float(intensity)], index=[row_label], dtype=float)
            secondary_series = None
            if str(fit.get("model", "")).strip() == "two_term":
                primary_col = str(fit.get("primary_col") or CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL)
                secondary_col = str(fit.get("secondary_col") or CYCLE1_SIGNAL_SYMMETRIC_MISMATCH44_COL)
                primary_value = (
                    pd.to_numeric(pd.Series([context.at[row_label, primary_col]]), errors="coerce").iloc[0]
                    if primary_col in context.columns
                    else intensity
                )
                secondary_value = (
                    pd.to_numeric(pd.Series([context.at[row_label, secondary_col]]), errors="coerce").iloc[0]
                    if secondary_col in context.columns
                    else np.nan
                )
                intensity_series = pd.Series([float(primary_value)], index=[row_label], dtype=float)
                secondary_series = pd.Series([float(secondary_value)], index=[row_label], dtype=float)
            delta = pd.to_numeric(_linearity_correction_delta(intensity_series, fit, secondary_series), errors="coerce").iloc[0]
    effective_raw_source = line_adjusted_raw if np.isfinite(line_adjusted_raw) else new_raw
    effective_raw = (
        float(effective_raw_source - delta)
        if np.isfinite(effective_raw_source) and np.isfinite(delta)
        else float(effective_raw_source)
    )
    if raw_corrected_col and raw_corrected_col in work.columns:
        work.at[row_label, raw_corrected_col] = effective_raw if np.isfinite(effective_raw) else np.nan
    coeffs = calibration_coefficients or {}
    iso_coeff = coeffs.get(str(isotope_key).strip(), {}) if isinstance(coeffs, dict) else {}
    slope_candidate = pd.to_numeric(pd.Series([iso_coeff.get("slope")]), errors="coerce").iloc[0]
    intercept_candidate = pd.to_numeric(pd.Series([iso_coeff.get("intercept")]), errors="coerce").iloc[0]
    if np.isfinite(slope_candidate) and np.isfinite(intercept_candidate):
        slope = float(slope_candidate)
        intercept = float(intercept_candidate)
    if slope is None or intercept is None:
        slope, intercept = _estimate_calibration_affine(work, raw_col, cal_col, exclude_row_label=row_label)
    if slope is not None and intercept is not None:
        new_cal = slope * effective_raw + intercept
    else:
        prev_raw_num = pd.to_numeric(pd.Series([previous_raw]), errors="coerce").iloc[0]
        prev_cal_num = pd.to_numeric(pd.Series([previous_calibrated]), errors="coerce").iloc[0]
        if np.isfinite(prev_raw_num) and np.isfinite(delta):
            prev_raw_num = float(prev_raw_num) - float(delta)
        if np.isfinite(prev_raw_num) and np.isfinite(prev_cal_num):
            new_cal = float(prev_cal_num) + (effective_raw - float(prev_raw_num))
        else:
            # Keep calibrated curves synchronized with edits even when no calibration model is available.
            new_cal = effective_raw
    work.at[row_label, cal_col] = float(new_cal) if np.isfinite(new_cal) else np.nan
    if corrected_col in work.columns:
        work.at[row_label, corrected_col] = float(new_cal) if np.isfinite(new_cal) else np.nan
    return work


def _interpolate_outliers_by_identifier2(
    df: pd.DataFrame,
    outlier_mask: pd.Series,
    cols: list[str],
    id2_col: str = "Identifier 2",
) -> pd.DataFrame:
    if df is None or len(df) == 0 or not any(col in df.columns for col in cols):
        return df
    work = df.copy()
    if id2_col in work.columns:
        order = pd.to_numeric(work[id2_col], errors="coerce")
        if order.isna().all():
            order = pd.to_numeric(work[id2_col].map(_parse_numeric_token), errors="coerce")
    else:
        order = pd.Series(np.arange(len(work)), index=work.index)
    use_identifier2_axis = bool(id2_col in work.columns and order.notna().any())
    work["_order_irms"] = order
    work["_orig_pos_irms"] = np.arange(len(work), dtype=float)
    work_sorted = work.sort_values(["_order_irms", "_orig_pos_irms"], na_position="last")
    mask_sorted = outlier_mask.reindex(work_sorted.index).fillna(False).astype(bool)
    x_order = pd.to_numeric(work_sorted["_order_irms"], errors="coerce")
    x_pos = pd.Series(np.arange(len(work_sorted), dtype=float), index=work_sorted.index)
    x_axis = x_order if use_identifier2_axis else x_order.where(x_order.notna(), x_pos)
    x_values = pd.to_numeric(x_axis, errors="coerce").to_numpy(dtype=float)
    mask_values = mask_sorted.to_numpy(dtype=bool)
    target_positions = np.flatnonzero(mask_values)

    for col in cols:
        if col not in work_sorted.columns:
            continue
        series = pd.to_numeric(work_sorted[col], errors="coerce")
        y_values = series.to_numpy(dtype=float)
        known_positions = np.flatnonzero((~mask_values) & np.isfinite(y_values))
        if known_positions.size == 0 or target_positions.size == 0:
            continue

        updates: dict[Any, float] = {}
        for pos in target_positions:
            left = known_positions[known_positions < pos]
            right = known_positions[known_positions > pos]
            if use_identifier2_axis:
                left = left[np.isfinite(x_values[left])]
                right = right[np.isfinite(x_values[right])]
            if left.size == 0 and right.size == 0:
                continue
            xt = x_values[pos]
            if np.isfinite(xt):
                left_strict = left[np.isfinite(x_values[left]) & (x_values[left] < xt) & ~np.isclose(x_values[left], xt)]
                right_strict = right[np.isfinite(x_values[right]) & (x_values[right] > xt) & ~np.isclose(x_values[right], xt)]
                if left_strict.size > 0 and right_strict.size > 0:
                    left = left_strict
                    right = right_strict
                else:
                    left_non_equal = left[np.isfinite(x_values[left]) & ~np.isclose(x_values[left], xt)]
                    right_non_equal = right[np.isfinite(x_values[right]) & ~np.isclose(x_values[right], xt)]
                    if left_non_equal.size > 0:
                        left = left_non_equal
                    if right_non_equal.size > 0:
                        right = right_non_equal
            if left.size == 0:
                interp_val = y_values[right[0]]
            elif right.size == 0:
                interp_val = y_values[left[-1]]
            else:
                p = int(left[-1])
                n = int(right[0])
                y0 = y_values[p]
                y1 = y_values[n]
                x0 = x_values[p]
                x1 = x_values[n]
                xt = x_values[pos]
                if (
                    np.isfinite(x0)
                    and np.isfinite(x1)
                    and np.isfinite(xt)
                    and not np.isclose(float(x1), float(x0))
                ):
                    ratio = (float(xt) - float(x0)) / (float(x1) - float(x0))
                else:
                    if use_identifier2_axis:
                        # Identifier-2 interpolation should not degrade to row-position spacing.
                        interp_val = np.nan
                        ratio = np.nan
                    else:
                        denom = float(n - p)
                        ratio = (float(pos) - float(p)) / denom if denom != 0 else 0.5
                if not use_identifier2_axis and not np.isfinite(ratio):
                    ratio = 0.5
                if not use_identifier2_axis:
                    ratio = float(np.clip(ratio, 0.0, 1.0))
                    interp_val = float(y0) + ratio * (float(y1) - float(y0))
                elif np.isfinite(ratio):
                    ratio = float(np.clip(ratio, 0.0, 1.0))
                    interp_val = float(y0) + ratio * (float(y1) - float(y0))
            if np.isfinite(interp_val):
                updates[work_sorted.index[pos]] = float(interp_val)

        if updates:
            work_sorted.loc[list(updates.keys()), col] = pd.Series(updates)
    work_sorted = work_sorted.sort_values("_orig_pos_irms")
    return work_sorted.drop(columns=["_order_irms", "_orig_pos_irms"])


def _interpolate_single_target_within_identifier_group(
    df: pd.DataFrame,
    row_label: Any,
    col: str,
    id2_col: str = "Identifier 2",
    id1_col: str = "Identifier 1",
    species_col: str = "Species",
) -> float | None:
    if df is None or col not in df.columns or row_label not in df.index:
        return None

    subset = df
    if id1_col in df.columns:
        id1_value_raw = df.at[row_label, id1_col]
        id1_value = "" if pd.isna(id1_value_raw) else str(id1_value_raw).strip()
        if id1_value != "":
            group_mask = df[id1_col].astype(str).str.strip().eq(id1_value)
            if bool(group_mask.any()):
                subset = df.loc[group_mask].copy()
    if species_col in subset.columns:
        species_value_raw = subset.at[row_label, species_col] if row_label in subset.index else df.at[row_label, species_col]
        species_value = "" if pd.isna(species_value_raw) else str(species_value_raw).strip()
        if species_value != "":
            species_mask = subset[species_col].astype(str).str.strip().eq(species_value)
            if bool(species_mask.any()):
                subset = subset.loc[species_mask].copy()

    if row_label not in subset.index:
        return None

    work = subset.copy()
    if id2_col in work.columns:
        order = pd.to_numeric(work[id2_col], errors="coerce")
        if order.isna().all():
            order = pd.to_numeric(work[id2_col].map(_parse_numeric_token), errors="coerce")
    else:
        order = pd.Series(np.arange(len(work)), index=work.index, dtype=float)
    use_identifier2_axis = bool(id2_col in work.columns and order.notna().any())
    work["_order_irms"] = order
    work["_orig_pos_irms"] = np.arange(len(work), dtype=float)
    work_sorted = work.sort_values(["_order_irms", "_orig_pos_irms"], na_position="last")
    if row_label not in work_sorted.index:
        return None

    y_values = pd.to_numeric(work_sorted[col], errors="coerce")
    x_order = pd.to_numeric(work_sorted["_order_irms"], errors="coerce")
    x_pos = pd.Series(np.arange(len(work_sorted), dtype=float), index=work_sorted.index)
    x_axis = x_order if use_identifier2_axis else x_order.where(x_order.notna(), x_pos)
    row_positions = np.flatnonzero(work_sorted.index.to_numpy() == row_label)
    if row_positions.size == 0:
        return None
    pos = int(row_positions[0])

    xt = pd.to_numeric(pd.Series([x_axis.iloc[pos]]), errors="coerce").iloc[0]

    def _find_prev(require_strict_less: bool, require_not_equal: bool) -> int | None:
        for i in range(pos - 1, -1, -1):
            value = y_values.iloc[i]
            if not np.isfinite(value):
                continue
            xi = pd.to_numeric(pd.Series([x_axis.iloc[i]]), errors="coerce").iloc[0]
            if use_identifier2_axis and not np.isfinite(xi):
                continue
            if require_strict_less and np.isfinite(xt) and np.isfinite(xi):
                if not (float(xi) < float(xt) and not np.isclose(float(xi), float(xt))):
                    continue
            if require_not_equal and np.isfinite(xt) and np.isfinite(xi) and np.isclose(float(xi), float(xt)):
                continue
            return int(i)
        return None

    def _find_next(require_strict_greater: bool, require_not_equal: bool) -> int | None:
        for i in range(pos + 1, len(work_sorted)):
            value = y_values.iloc[i]
            if not np.isfinite(value):
                continue
            xi = pd.to_numeric(pd.Series([x_axis.iloc[i]]), errors="coerce").iloc[0]
            if use_identifier2_axis and not np.isfinite(xi):
                continue
            if require_strict_greater and np.isfinite(xt) and np.isfinite(xi):
                if not (float(xi) > float(xt) and not np.isclose(float(xi), float(xt))):
                    continue
            if require_not_equal and np.isfinite(xt) and np.isfinite(xi) and np.isclose(float(xi), float(xt)):
                continue
            return int(i)
        return None

    prev_pos: int | None = None
    next_pos: int | None = None
    if np.isfinite(xt):
        prev_pos = _find_prev(require_strict_less=True, require_not_equal=False)
        next_pos = _find_next(require_strict_greater=True, require_not_equal=False)
        if prev_pos is None:
            prev_pos = _find_prev(require_strict_less=False, require_not_equal=True)
        if next_pos is None:
            next_pos = _find_next(require_strict_greater=False, require_not_equal=True)
    if not use_identifier2_axis:
        if prev_pos is None:
            prev_pos = _find_prev(require_strict_less=False, require_not_equal=False)
        if next_pos is None:
            next_pos = _find_next(require_strict_greater=False, require_not_equal=False)

    prev_value: float | None = float(y_values.iloc[prev_pos]) if prev_pos is not None else None
    next_value: float | None = float(y_values.iloc[next_pos]) if next_pos is not None else None

    if prev_value is not None and next_value is not None and prev_pos is not None and next_pos is not None:
        x0 = pd.to_numeric(pd.Series([x_axis.iloc[prev_pos]]), errors="coerce").iloc[0]
        x1 = pd.to_numeric(pd.Series([x_axis.iloc[next_pos]]), errors="coerce").iloc[0]
        if (
            np.isfinite(x0)
            and np.isfinite(x1)
            and np.isfinite(xt)
            and not np.isclose(float(x1), float(x0))
        ):
            ratio = (float(xt) - float(x0)) / (float(x1) - float(x0))
        else:
            if use_identifier2_axis:
                return None
            denom = float(next_pos - prev_pos)
            ratio = (float(pos) - float(prev_pos)) / denom if denom != 0 else 0.5
        if not np.isfinite(ratio):
            if use_identifier2_axis:
                return None
            ratio = 0.5
        ratio = float(np.clip(ratio, 0.0, 1.0))
        return float(prev_value + ratio * (next_value - prev_value))
    if prev_value is not None:
        return prev_value
    if next_value is not None:
        return next_value
    return None


def _ensure_edit_state(edit_state: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(edit_state or {})
    payload.setdefault("edited_rows", [])
    payload.setdefault("original_delta_values", {})
    payload.setdefault("original_missing_delta_tokens", [])
    payload.setdefault("original_std_values", {})
    payload.setdefault("original_missing_std_tokens", [])
    payload.setdefault("manual_outlier_overrides", {})
    payload.setdefault("restored_delta_tokens", [])
    return payload


def apply_edit_action(
    df: pd.DataFrame,
    edit_state: dict[str, Any] | None,
    edit: EditAction,
    calibration_coefficients: dict[str, Any] | None = None,
    linearity_fits: dict[str, Any] | None = None,
    linearity_config: dict[str, Any] | None = None,
    interpolation_source_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    work = df.copy()
    state = _ensure_edit_state(edit_state)
    original_map = {
        str(key): float(value)
        for key, value in state.get("original_delta_values", {}).items()
        if pd.notna(pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0])
    }
    original_missing_tokens = {
        str(token)
        for token in state.get("original_missing_delta_tokens", [])
        if str(token).strip() != ""
    }
    original_std_map = {
        str(key): float(value)
        for key, value in state.get("original_std_values", {}).items()
        if pd.notna(pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0])
    }
    original_missing_std_tokens = {
        str(token)
        for token in state.get("original_missing_std_tokens", [])
        if str(token).strip() != ""
    }
    edited_rows = {str(row) for row in state.get("edited_rows", [])}
    manual_outlier_overrides = {
        str(key): bool(value)
        for key, value in state.get("manual_outlier_overrides", {}).items()
    }
    restored_tokens = {
        str(token)
        for token in state.get("restored_delta_tokens", [])
        if str(token).strip() != ""
    }
    coeffs = calibration_coefficients or {}
    fits = linearity_fits or {}

    def _resolve_target_row(target_row_label: str) -> Any:
        if target_row_label in work.index:
            return target_row_label
        try:
            numeric = int(target_row_label)
        except ValueError:
            numeric = target_row_label
        if numeric in work.index:
            return numeric
        raise KeyError(f"Unknown row label {target_row_label}")

    if edit.action == "reset_all":
        reset_tokens = sorted(set(original_map.keys()) | original_missing_tokens)
        for token in reset_tokens:
            if "|" not in token:
                continue
            isotope_key, row_label_raw = token.split("|", 1)
            raw_col, cal_col, _ = _get_isotope_columns(isotope_key)
            if raw_col is None:
                continue
            row_label = _resolve_target_row(row_label_raw)
            prev_raw = pd.to_numeric(pd.Series([work.at[row_label, raw_col]]), errors="coerce").iloc[0]
            prev_cal = (
                pd.to_numeric(pd.Series([work.at[row_label, cal_col]]), errors="coerce").iloc[0]
                if cal_col in work.columns
                else None
            )
            if token in original_map:
                work.at[row_label, raw_col] = float(original_map[token])
            else:
                work.at[row_label, raw_col] = np.nan
            work = _refresh_collector_status_after_delta_edit(work, row_label)
            work = _refresh_calibrated_after_delta_edit(
                work,
                row_label,
                isotope_key,
                coeffs,
                fits,
                linearity_config,
                previous_raw=prev_raw,
                previous_calibrated=prev_cal,
            )
        std_reset_tokens = sorted(set(original_std_map.keys()) | original_missing_std_tokens)
        for token in std_reset_tokens:
            if "|" not in token:
                continue
            isotope_key, row_label_raw = token.split("|", 1)
            std_col = _get_isotope_std_column(isotope_key)
            if std_col is None or std_col not in work.columns:
                continue
            row_label = _resolve_target_row(row_label_raw)
            if token in original_std_map:
                work.at[row_label, std_col] = float(original_std_map[token])
            else:
                work.at[row_label, std_col] = np.nan
        edited_rows.clear()
        original_map = {}
        original_missing_tokens = set()
        original_std_map = {}
        original_missing_std_tokens = set()
        restored_tokens = set()
    elif edit.action == "set_outlier_override":
        for target in edit.targets:
            manual_outlier_overrides[str(target.row_label)] = bool(edit.is_outlier)
    elif edit.action == "reset_to_original":
        for target in edit.targets:
            row_label = _resolve_target_row(target.row_label)
            raw_col, cal_col, _ = _get_isotope_columns(target.isotope_key)
            std_col = _get_isotope_std_column(target.isotope_key)
            key = f"{target.isotope_key}|{target.row_label}"
            if raw_col and (key in original_map or key in original_missing_tokens):
                prev_raw = pd.to_numeric(pd.Series([work.at[row_label, raw_col]]), errors="coerce").iloc[0]
                prev_cal = (
                    pd.to_numeric(pd.Series([work.at[row_label, cal_col]]), errors="coerce").iloc[0]
                    if cal_col in work.columns
                    else None
                )
                if key in original_map:
                    work.at[row_label, raw_col] = original_map[key]
                else:
                    work.at[row_label, raw_col] = np.nan
                work = _refresh_collector_status_after_delta_edit(work, row_label)
                work = _refresh_calibrated_after_delta_edit(
                    work,
                    row_label,
                    target.isotope_key,
                    coeffs,
                    fits,
                    linearity_config,
                    previous_raw=prev_raw,
                    previous_calibrated=prev_cal,
                )
                original_map.pop(key, None)
                original_missing_tokens.discard(key)
                restored_tokens.discard(key)
                edited_rows.discard(str(target.row_label))
            if std_col and std_col in work.columns and (key in original_std_map or key in original_missing_std_tokens):
                if key in original_std_map:
                    work.at[row_label, std_col] = float(original_std_map[key])
                else:
                    work.at[row_label, std_col] = np.nan
                original_std_map.pop(key, None)
                original_missing_std_tokens.discard(key)
    elif edit.action in {"set_value", "offset"}:
        delta = edit.offset if edit.action == "offset" else edit.value
        if delta is None:
            raise ValueError("Missing edit value")
        set_value_stdev: float | None = None
        if edit.action == "set_value" and edit.stdev is not None:
            parsed_stdev = pd.to_numeric(pd.Series([edit.stdev]), errors="coerce").iloc[0]
            if not np.isfinite(parsed_stdev):
                raise ValueError("Invalid set-value stdev")
            if float(parsed_stdev) < 0:
                raise ValueError("Set-value stdev must be non-negative")
            set_value_stdev = float(parsed_stdev)
        for target in edit.targets:
            row_label = _resolve_target_row(target.row_label)
            raw_col, cal_col, _ = _get_isotope_columns(target.isotope_key)
            std_col = _get_isotope_std_column(target.isotope_key)
            if raw_col is None:
                continue
            key = f"{target.isotope_key}|{target.row_label}"
            current_value = pd.to_numeric(pd.Series([work.at[row_label, raw_col]]), errors="coerce").iloc[0]
            if key not in original_map and key not in original_missing_tokens:
                if pd.notna(current_value):
                    original_map[key] = float(current_value)
                else:
                    original_missing_tokens.add(key)
            new_value = float(delta) if edit.action == "set_value" else float(current_value) + float(delta)
            prev_cal = (
                pd.to_numeric(pd.Series([work.at[row_label, cal_col]]), errors="coerce").iloc[0]
                if cal_col in work.columns
                else None
            )
            work.at[row_label, raw_col] = new_value
            if set_value_stdev is not None and std_col and std_col in work.columns:
                if key not in original_std_map and key not in original_missing_std_tokens:
                    current_std = pd.to_numeric(pd.Series([work.at[row_label, std_col]]), errors="coerce").iloc[0]
                    if pd.notna(current_std):
                        original_std_map[key] = float(current_std)
                    else:
                        original_missing_std_tokens.add(key)
                work.at[row_label, std_col] = float(set_value_stdev)
            work = _refresh_collector_status_after_delta_edit(work, row_label)
            work = _refresh_calibrated_after_delta_edit(
                work,
                row_label,
                target.isotope_key,
                coeffs,
                fits,
                linearity_config,
                previous_raw=current_value,
                previous_calibrated=prev_cal,
            )
            edited_rows.add(str(target.row_label))
            restored_tokens.discard(key)
    elif edit.action == "interpolate":
        failed_rows_before_interpolation: set[str] = set()
        if "Collector Status" in work.columns:
            status_series = work["Collector Status"].fillna("").astype(str).str.strip()
            failed_rows_before_interpolation = {str(idx) for idx in work.index[status_series.eq("Failed Sample")]}
        interpolation_offset = 0.0
        if edit.offset is not None:
            parsed_offset = pd.to_numeric(pd.Series([edit.offset]), errors="coerce").iloc[0]
            if not np.isfinite(parsed_offset):
                raise ValueError("Invalid interpolate offset")
            interpolation_offset = float(parsed_offset)
        interpolation_stdev: float | None = None
        if edit.stdev is not None:
            parsed_stdev = pd.to_numeric(pd.Series([edit.stdev]), errors="coerce").iloc[0]
            if not np.isfinite(parsed_stdev):
                raise ValueError("Invalid interpolate stdev")
            if float(parsed_stdev) < 0:
                raise ValueError("Interpolate stdev must be non-negative")
            interpolation_stdev = float(parsed_stdev)
        targets_by_row: dict[str, set[str]] = {}
        for target in edit.targets:
            row_key = str(target.row_label)
            targets_by_row.setdefault(row_key, set()).add(str(target.isotope_key).strip())
        preserved_non_target_values: dict[tuple[str, str], dict[str, Any]] = {}
        for row_key, iso_targets in targets_by_row.items():
            row_label = _resolve_target_row(row_key)
            for iso_key in ("d13C", "d18O"):
                if iso_key in iso_targets:
                    continue
                raw_col, cal_col, corrected_col = _get_isotope_columns(iso_key)
                std_col = _get_isotope_std_column(iso_key)
                cols = [col for col in (raw_col, cal_col, corrected_col, std_col) if col is not None and col in work.columns]
                if not cols:
                    continue
                preserved_non_target_values[(str(row_label), iso_key)] = {
                    col: work.at[row_label, col] for col in cols
                }
        for isotope_key in ("d13C", "d18O"):
            targets = [target for target in edit.targets if target.isotope_key == isotope_key]
            if not targets:
                continue
            raw_col, cal_col, _ = _get_isotope_columns(isotope_key)
            std_col = _get_isotope_std_column(isotope_key)
            source_to_raw_offset_col = "__source_to_raw_offset_d13C__" if isotope_key == "d13C" else "__source_to_raw_offset_d18O__"
            if raw_col is None:
                continue
            interpolation_base = work.copy()
            interpolation_value_base = interpolation_base.copy()
            source_to_raw_offset_by_row = pd.Series(0.0, index=interpolation_base.index, dtype=float)
            source_to_raw_offset_interp_base: pd.DataFrame | None = None
            use_interpolation_source_values = interpolation_source_df is not None and raw_col in interpolation_source_df.columns
            if use_interpolation_source_values:
                source_values = pd.to_numeric(interpolation_source_df[raw_col], errors="coerce").reindex(
                    interpolation_value_base.index
                )
                interpolation_value_base[raw_col] = source_values
                if source_to_raw_offset_col in interpolation_source_df.columns:
                    source_to_raw_offset_by_row = pd.to_numeric(
                        interpolation_source_df[source_to_raw_offset_col],
                        errors="coerce",
                    ).reindex(source_values.index)
                else:
                    raw_values = pd.to_numeric(interpolation_base[raw_col], errors="coerce").reindex(source_values.index)
                    source_to_raw_offset_by_row = pd.to_numeric(source_values - raw_values, errors="coerce")
                source_to_raw_offset_interp_base = interpolation_base.copy()
                source_to_raw_offset_interp_base["__source_to_raw_offset__"] = source_to_raw_offset_by_row
            interpolation_neighbor_base = interpolation_value_base.copy()
            # When interpolating multiple selected points, exclude all current
            # targets from the neighbor pool so they do not contaminate each
            # other's interpolation values.
            target_row_labels_for_iso = [_resolve_target_row(target.row_label) for target in targets]
            if target_row_labels_for_iso:
                interpolation_neighbor_base.loc[target_row_labels_for_iso, raw_col] = np.nan
                if source_to_raw_offset_interp_base is not None:
                    source_to_raw_offset_interp_base.loc[target_row_labels_for_iso, "__source_to_raw_offset__"] = np.nan
            prev_cal_map: dict[str, float | None] = {}
            prev_raw_map: dict[str, float | None] = {}
            interpolated_map: dict[str, float] = {}
            previously_failed_map: dict[str, bool] = {}
            for target in targets:
                row_label = _resolve_target_row(target.row_label)
                key = f"{target.isotope_key}|{target.row_label}"
                current_value = pd.to_numeric(pd.Series([interpolation_base.at[row_label, raw_col]]), errors="coerce").iloc[0]
                if key not in original_map and key not in original_missing_tokens:
                    if pd.notna(current_value):
                        original_map[key] = float(current_value)
                    else:
                        original_missing_tokens.add(key)
                prev_raw_map[str(target.row_label)] = float(current_value) if pd.notna(current_value) else None
                prev_cal_map[str(target.row_label)] = (
                    pd.to_numeric(pd.Series([interpolation_base.at[row_label, cal_col]]), errors="coerce").iloc[0]
                    if cal_col in work.columns
                    else None
                )
                previously_failed_map[str(target.row_label)] = str(row_label) in failed_rows_before_interpolation
                interpolated_value = _interpolate_single_target_within_identifier_group(
                    interpolation_neighbor_base,
                    row_label,
                    raw_col,
                )
                if interpolated_value is not None:
                    interpolated_map[str(target.row_label)] = float(interpolated_value)
            for target in targets:
                row_label = _resolve_target_row(target.row_label)
                key = f"{target.isotope_key}|{target.row_label}"
                next_raw = interpolated_map.get(str(target.row_label))
                if next_raw is not None:
                    raw_value_to_persist = float(next_raw)
                    if use_interpolation_source_values:
                        source_to_raw_offset = pd.to_numeric(
                            pd.Series([source_to_raw_offset_by_row.get(row_label)]),
                            errors="coerce",
                        ).iloc[0]
                        if not np.isfinite(source_to_raw_offset) and source_to_raw_offset_interp_base is not None:
                            source_to_raw_offset = _interpolate_single_target_within_identifier_group(
                                source_to_raw_offset_interp_base,
                                row_label,
                                "__source_to_raw_offset__",
                            )
                        if np.isfinite(source_to_raw_offset):
                            raw_value_to_persist = float(next_raw) - float(source_to_raw_offset)
                    work.at[row_label, raw_col] = raw_value_to_persist + interpolation_offset
                    if interpolation_stdev is not None and std_col and std_col in work.columns:
                        if key not in original_std_map and key not in original_missing_std_tokens:
                            current_std = pd.to_numeric(pd.Series([work.at[row_label, std_col]]), errors="coerce").iloc[0]
                            if pd.notna(current_std):
                                original_std_map[key] = float(current_std)
                            else:
                                original_missing_std_tokens.add(key)
                        work.at[row_label, std_col] = float(interpolation_stdev)
                    # Mark as restored only when this interpolation actually recovers
                    # a previously missing failed-sample isotope value.
                    if previously_failed_map.get(str(target.row_label), False) and key in original_missing_tokens:
                        restored_tokens.add(key)
                work = _refresh_collector_status_after_delta_edit(work, row_label)
                work = _refresh_calibrated_after_delta_edit(
                    work,
                    row_label,
                    isotope_key,
                    coeffs,
                    fits,
                    linearity_config,
                    previous_raw=prev_raw_map.get(str(target.row_label)),
                    previous_calibrated=prev_cal_map.get(str(target.row_label)),
                )
                edited_rows.add(str(target.row_label))
        for row_key, col_values in preserved_non_target_values.items():
            row_label = _resolve_target_row(row_key[0])
            for col, value in col_values.items():
                work.at[row_label, col] = value
    else:
        raise ValueError(f"Unsupported edit action {edit.action}")

    state["original_delta_values"] = original_map
    state["original_missing_delta_tokens"] = sorted(original_missing_tokens)
    state["original_std_values"] = original_std_map
    state["original_missing_std_tokens"] = sorted(original_missing_std_tokens)
    state["edited_rows"] = sorted(edited_rows)
    state["manual_outlier_overrides"] = manual_outlier_overrides
    state["restored_delta_tokens"] = sorted(restored_tokens)
    return work, state
