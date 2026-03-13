from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..calibration.core import _resolve_linearity_intensity_column_for_fits
from ..contracts import EditAction
from ..shared.dataframe import _parse_numeric_token


def _get_isotope_columns(isotope_key: str) -> tuple[str | None, str | None, str | None]:
    key = str(isotope_key).strip()
    if key == "d13C":
        return ("d 13C/12C  Mean", "d13C_calibrated", "d13C_calibrated_linearity_corrected")
    if key == "d18O":
        return ("d 18O/16O  Mean", "d18O_calibrated", "d18O_calibrated_linearity_corrected")
    return (None, None, None)


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
    previous_raw: float | None = None,
    previous_calibrated: float | None = None,
) -> pd.DataFrame:
    work = df.copy()
    raw_col, cal_col, corrected_col = _get_isotope_columns(isotope_key)
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
        new_cal = slope * float(new_raw) + intercept
    else:
        prev_raw_num = pd.to_numeric(pd.Series([previous_raw]), errors="coerce").iloc[0]
        prev_cal_num = pd.to_numeric(pd.Series([previous_calibrated]), errors="coerce").iloc[0]
        if np.isfinite(prev_raw_num) and np.isfinite(prev_cal_num):
            new_cal = float(prev_cal_num) + (float(new_raw) - float(prev_raw_num))
        else:
            return work
    work.at[row_label, cal_col] = float(new_cal) if np.isfinite(new_cal) else np.nan
    if corrected_col in work.columns:
        fits = linearity_fits or {}
        fit = fits.get(str(isotope_key).strip(), {}) if isinstance(fits, dict) else {}
        slope_lin = pd.to_numeric(pd.Series([fit.get("slope")]), errors="coerce").iloc[0]
        x_ref = pd.to_numeric(pd.Series([fit.get("x_ref")]), errors="coerce").iloc[0]
        intensity_col = _resolve_linearity_intensity_column_for_fits(fits=fits, df=work)
        intensity = (
            pd.to_numeric(pd.Series([work.at[row_label, intensity_col]]), errors="coerce").iloc[0]
            if intensity_col in work.columns
            else np.nan
        )
        if np.isfinite(slope_lin) and np.isfinite(x_ref) and np.isfinite(intensity):
            work.at[row_label, corrected_col] = float(new_cal - float(slope_lin) * (float(intensity) - float(x_ref)))
        else:
            work.at[row_label, corrected_col] = float(new_cal)
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
    work["_order_irms"] = order
    work["_orig_pos_irms"] = np.arange(len(work), dtype=float)
    work_sorted = work.sort_values(["_order_irms", "_orig_pos_irms"], na_position="last")
    mask_sorted = outlier_mask.reindex(work_sorted.index).fillna(False).astype(bool)
    x_order = pd.to_numeric(work_sorted["_order_irms"], errors="coerce")
    x_pos = pd.Series(np.arange(len(work_sorted), dtype=float), index=work_sorted.index)
    x_axis = x_order.where(x_order.notna(), x_pos)
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
            if left.size == 0 and right.size == 0:
                continue
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
                    denom = float(n - p)
                    ratio = (float(pos) - float(p)) / denom if denom != 0 else 0.5
                if not np.isfinite(ratio):
                    ratio = 0.5
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

    if row_label not in subset.index:
        return None

    target_mask = pd.Series(False, index=subset.index, dtype=bool)
    target_mask.loc[row_label] = True
    interpolated_subset = _interpolate_outliers_by_identifier2(subset, target_mask, [col], id2_col=id2_col)
    interpolated_value = pd.to_numeric(pd.Series([interpolated_subset.at[row_label, col]]), errors="coerce").iloc[0]
    if not np.isfinite(interpolated_value):
        return None
    return float(interpolated_value)


def _ensure_edit_state(edit_state: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(edit_state or {})
    payload.setdefault("edited_rows", [])
    payload.setdefault("original_delta_values", {})
    payload.setdefault("manual_outlier_overrides", {})
    return payload


def apply_edit_action(
    df: pd.DataFrame,
    edit_state: dict[str, Any] | None,
    edit: EditAction,
    calibration_coefficients: dict[str, Any] | None = None,
    linearity_fits: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    work = df.copy()
    state = _ensure_edit_state(edit_state)
    original_map = {
        str(key): float(value)
        for key, value in state.get("original_delta_values", {}).items()
        if pd.notna(pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0])
    }
    edited_rows = {str(row) for row in state.get("edited_rows", [])}
    manual_outlier_overrides = {
        str(key): bool(value)
        for key, value in state.get("manual_outlier_overrides", {}).items()
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
        for token, original_value in list(original_map.items()):
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
            work.at[row_label, raw_col] = float(original_value)
            work = _refresh_collector_status_after_delta_edit(work, row_label)
            work = _refresh_calibrated_after_delta_edit(
                work,
                row_label,
                isotope_key,
                coeffs,
                fits,
                previous_raw=prev_raw,
                previous_calibrated=prev_cal,
            )
        edited_rows.clear()
        original_map = {}
    elif edit.action == "set_outlier_override":
        for target in edit.targets:
            manual_outlier_overrides[str(target.row_label)] = bool(edit.is_outlier)
    elif edit.action == "reset_to_original":
        for target in edit.targets:
            row_label = _resolve_target_row(target.row_label)
            raw_col, cal_col, _ = _get_isotope_columns(target.isotope_key)
            key = f"{target.isotope_key}|{target.row_label}"
            if raw_col and key in original_map:
                prev_raw = pd.to_numeric(pd.Series([work.at[row_label, raw_col]]), errors="coerce").iloc[0]
                prev_cal = (
                    pd.to_numeric(pd.Series([work.at[row_label, cal_col]]), errors="coerce").iloc[0]
                    if cal_col in work.columns
                    else None
                )
                work.at[row_label, raw_col] = original_map[key]
                work = _refresh_collector_status_after_delta_edit(work, row_label)
                work = _refresh_calibrated_after_delta_edit(
                    work,
                    row_label,
                    target.isotope_key,
                    coeffs,
                    fits,
                    previous_raw=prev_raw,
                    previous_calibrated=prev_cal,
                )
                original_map.pop(key, None)
                edited_rows.discard(str(target.row_label))
    elif edit.action in {"set_value", "offset"}:
        delta = edit.offset if edit.action == "offset" else edit.value
        if delta is None:
            raise ValueError("Missing edit value")
        for target in edit.targets:
            row_label = _resolve_target_row(target.row_label)
            raw_col, cal_col, _ = _get_isotope_columns(target.isotope_key)
            if raw_col is None:
                continue
            key = f"{target.isotope_key}|{target.row_label}"
            current_value = pd.to_numeric(pd.Series([work.at[row_label, raw_col]]), errors="coerce").iloc[0]
            if key not in original_map and pd.notna(current_value):
                original_map[key] = float(current_value)
            new_value = float(delta) if edit.action == "set_value" else float(current_value) + float(delta)
            prev_cal = (
                pd.to_numeric(pd.Series([work.at[row_label, cal_col]]), errors="coerce").iloc[0]
                if cal_col in work.columns
                else None
            )
            work.at[row_label, raw_col] = new_value
            work = _refresh_collector_status_after_delta_edit(work, row_label)
            work = _refresh_calibrated_after_delta_edit(
                work,
                row_label,
                target.isotope_key,
                coeffs,
                fits,
                previous_raw=current_value,
                previous_calibrated=prev_cal,
            )
            edited_rows.add(str(target.row_label))
    elif edit.action == "interpolate":
        for isotope_key in {"d13C", "d18O"}:
            targets = [target for target in edit.targets if target.isotope_key == isotope_key]
            if not targets:
                continue
            raw_col, cal_col, _ = _get_isotope_columns(isotope_key)
            if raw_col is None:
                continue
            interpolation_base = work.copy()
            prev_cal_map: dict[str, float | None] = {}
            prev_raw_map: dict[str, float | None] = {}
            interpolated_map: dict[str, float] = {}
            for target in targets:
                row_label = _resolve_target_row(target.row_label)
                key = f"{target.isotope_key}|{target.row_label}"
                current_value = pd.to_numeric(pd.Series([interpolation_base.at[row_label, raw_col]]), errors="coerce").iloc[0]
                if key not in original_map and pd.notna(current_value):
                    original_map[key] = float(current_value)
                prev_raw_map[str(target.row_label)] = float(current_value) if pd.notna(current_value) else None
                prev_cal_map[str(target.row_label)] = (
                    pd.to_numeric(pd.Series([interpolation_base.at[row_label, cal_col]]), errors="coerce").iloc[0]
                    if cal_col in work.columns
                    else None
                )
                interpolated_value = _interpolate_single_target_within_identifier_group(
                    interpolation_base,
                    row_label,
                    raw_col,
                )
                if interpolated_value is not None:
                    interpolated_map[str(target.row_label)] = float(interpolated_value)
            for target in targets:
                row_label = _resolve_target_row(target.row_label)
                next_raw = interpolated_map.get(str(target.row_label))
                if next_raw is not None:
                    work.at[row_label, raw_col] = float(next_raw)
                work = _refresh_collector_status_after_delta_edit(work, row_label)
                work = _refresh_calibrated_after_delta_edit(
                    work,
                    row_label,
                    isotope_key,
                    coeffs,
                    fits,
                    previous_raw=prev_raw_map.get(str(target.row_label)),
                    previous_calibrated=prev_cal_map.get(str(target.row_label)),
                )
                edited_rows.add(str(target.row_label))
    else:
        raise ValueError(f"Unsupported edit action {edit.action}")

    state["original_delta_values"] = original_map
    state["edited_rows"] = sorted(edited_rows)
    state["manual_outlier_overrides"] = manual_outlier_overrides
    return work, state
