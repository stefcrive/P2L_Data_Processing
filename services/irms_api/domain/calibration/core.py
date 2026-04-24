from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

try:
    from scipy.stats import linregress
except ModuleNotFoundError:  # pragma: no cover - optional for logic-only tests
    def linregress(x, y):  # type: ignore[override]
        slope, intercept = np.polyfit(np.asarray(x, dtype=float), np.asarray(y, dtype=float), 1)
        corr = np.corrcoef(np.asarray(x, dtype=float), np.asarray(y, dtype=float))[0, 1]

        class _Result:
            def __init__(self, slope: float, intercept: float, rvalue: float) -> None:
                self.slope = slope
                self.intercept = intercept
                self.rvalue = rvalue

        return _Result(float(slope), float(intercept), float(corr))

try:
    import plotly.graph_objects as go
except ModuleNotFoundError:  # pragma: no cover - optional for logic-only tests
    go = None

from ..constants import (
    CYCLE1_SIGNAL_DIFF44_COL,
    CYCLE1_SIGNAL_DIFF45_COL,
    CYCLE1_SIGNAL_DIFF46_COL,
    CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
    CYCLE1_SIGNAL_REF44_COL,
    CYCLE1_SIGNAL_SAMP44_COL,
    ISOTYPE_D13C,
    ISOTYPE_D18O,
)
from ..shared.dataframe import _find_column
from ..shared.plotting import (
    _build_date_colorbar_ticks,
    _is_date_color_column,
    _prefer_datetime_color_values,
    _prepare_color_values,
)
from ..standards import StandardsRepository


def identify_outliers(data: pd.DataFrame, column: str, sigma_level: float) -> pd.Series:
    if column not in data.columns:
        return pd.Series(False, index=data.index, dtype=bool)
    values = pd.to_numeric(data[column], errors="coerce")
    mean_val, std_val, valid = _compute_sigma_stats(values, sigma_level)
    if not valid or pd.isna(std_val) or float(std_val) == 0.0:
        return pd.Series(False, index=data.index, dtype=bool)
    lower = mean_val - float(sigma_level) * std_val
    upper = mean_val + float(sigma_level) * std_val
    return (values < lower) | (values > upper)


def _compute_sigma_stats(series: pd.Series, sigma_level: float) -> tuple[float, float, bool]:
    values = pd.to_numeric(series, errors="coerce")
    values = values[np.isfinite(values)]
    if values.empty:
        return (float("nan"), float("nan"), False)
    mean_val = float(values.mean())
    std_val = float(values.std())
    if not np.isfinite(std_val):
        return (mean_val, float("nan"), False)
    return (mean_val, std_val, True)


def identify_outliers_iqr(
    data: pd.DataFrame,
    column: str,
    iqr_multiplier: float = 1.5,
) -> pd.Series:
    if column not in data.columns:
        return pd.Series(False, index=data.index, dtype=bool)
    values = pd.to_numeric(data[column], errors="coerce")
    finite = values[np.isfinite(values)]
    if finite.empty:
        return pd.Series(False, index=data.index, dtype=bool)
    q1 = float(finite.quantile(0.25))
    q3 = float(finite.quantile(0.75))
    iqr = q3 - q1
    lower = q1 - float(iqr_multiplier) * iqr
    upper = q3 + float(iqr_multiplier) * iqr
    return (values < lower) | (values > upper)


def get_true_value(
    standard_name: str,
    isotopic_type: str,
    repository: StandardsRepository | None = None,
) -> float:
    repo = repository or StandardsRepository.default()
    return repo.get_true_value(standard_name, isotopic_type)


def single_point_calibration(raw_sample: float, raw_std: float, true_std: float) -> float:
    return ((raw_sample + 1000) * (true_std + 1000)) / (raw_std + 1000) - 1000


def double_point_calibration(
    raw_sample: float,
    raw_rm1: float,
    true_rm1: float,
    raw_rm2: float,
    true_rm2: float,
) -> float:
    slope = (true_rm2 - true_rm1) / (raw_rm2 - raw_rm1)
    intercept = true_rm1 - slope * raw_rm1
    return slope * raw_sample + intercept


def _filter_standards_remove_outliers(
    df: pd.DataFrame,
    standards: list[str],
    method: str,
    sigma: float,
    iqr_mult: float,
    independent_isotope_outliers: bool = True,
    outlier_reference_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if not standards:
        return pd.DataFrame(columns=df.columns)
    reference_df = outlier_reference_df if outlier_reference_df is not None else df
    parts: list[pd.DataFrame] = []
    for standard in standards:
        std_df = df[df["Identifier 1"] == standard].copy()
        if std_df.empty:
            continue
        ref_std_df = reference_df.reindex(std_df.index).copy()
        if method == "Z-Score":
            out13 = identify_outliers(ref_std_df, "d 13C/12C  Mean", sigma)
            out18 = identify_outliers(ref_std_df, "d 18O/16O  Mean", sigma)
        else:
            out13 = identify_outliers_iqr(ref_std_df, "d 13C/12C  Mean", iqr_mult)
            out18 = identify_outliers_iqr(ref_std_df, "d 18O/16O  Mean", iqr_mult)
        out13 = out13.reindex(std_df.index, fill_value=False)
        out18 = out18.reindex(std_df.index, fill_value=False)
        if independent_isotope_outliers:
            std_df.loc[out13, "d 13C/12C  Mean"] = np.nan
            std_df.loc[out18, "d 18O/16O  Mean"] = np.nan
            parts.append(std_df.loc[~(out13 & out18)])
        else:
            parts.append(std_df.loc[~(out13 | out18)])
    if not parts:
        return pd.DataFrame(columns=df.columns)
    # Keep original row labels so chart clicks can map back to editable workspace rows.
    return pd.concat(parts, axis=0, ignore_index=False)


def _compute_linearity_fit(
    clean_df: pd.DataFrame,
    y_col: str,
    x_col: str,
    quadratic: bool = False,
) -> dict[str, float | int]:
    result: dict[str, float | int] = {
        "slope": float("nan"),
        "intercept": float("nan"),
        "quad": 0.0,
        "degree": 1,
        "r2": float("nan"),
        "x_ref": float("nan"),
        "n": 0,
    }
    if clean_df is None or clean_df.empty or x_col not in clean_df.columns or y_col not in clean_df.columns:
        return result
    x = pd.to_numeric(clean_df[x_col], errors="coerce")
    y = pd.to_numeric(clean_df[y_col], errors="coerce")
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]
    if len(x) < 2:
        return result
    x_values = np.asarray(x.values, dtype=float)
    y_values = np.asarray(y.values, dtype=float)
    x_span = float(np.nanmax(x_values) - np.nanmin(x_values)) if x_values.size else 0.0
    if not np.isfinite(x_span) or x_span <= 1e-12:
        result["intercept"] = float(np.nanmean(y_values)) if y_values.size else float("nan")
        result["x_ref"] = float(np.median(x.values))
        result["n"] = int(len(x))
        return result
    y_pred = np.full_like(y_values, np.nan, dtype=float)
    if bool(quadratic) and len(x_values) >= 3:
        try:
            quad, slope, intercept = np.polyfit(x_values, y_values, 2)
            result["quad"] = float(quad)
            result["slope"] = float(slope)
            result["intercept"] = float(intercept)
            result["degree"] = 2
            y_pred = np.polyval([quad, slope, intercept], x_values)
        except Exception:
            # Fall back to linear fit when quadratic solve is ill-conditioned.
            try:
                lr = linregress(x_values, y_values)
                result["slope"] = float(lr.slope)
                result["intercept"] = float(lr.intercept)
                y_pred = result["intercept"] + result["slope"] * x_values
            except ValueError:
                result["intercept"] = float(np.nanmean(y_values))
    else:
        try:
            lr = linregress(x_values, y_values)
            result["slope"] = float(lr.slope)
            result["intercept"] = float(lr.intercept)
            y_pred = result["intercept"] + result["slope"] * x_values
        except ValueError:
            result["intercept"] = float(np.nanmean(y_values))
    if np.isfinite(y_pred).all():
        ss_res = float(np.sum((y_values - y_pred) ** 2))
        ss_tot = float(np.sum((y_values - np.mean(y_values)) ** 2))
        result["r2"] = float(1.0 - (ss_res / ss_tot)) if ss_tot > 0 else float("nan")
    result["x_ref"] = float(np.median(x.values))
    result["n"] = int(len(x))
    return result


def _filter_linearity_fit_input_by_max_intensity(
    df: pd.DataFrame,
    intensity_col: str,
    max_intensity: float | None = None,
) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if intensity_col not in df.columns:
        return df
    max_value = pd.to_numeric(pd.Series([max_intensity]), errors="coerce").iloc[0]
    if not np.isfinite(max_value):
        return df
    intensity = pd.to_numeric(df[intensity_col], errors="coerce")
    mask = np.isfinite(intensity) & (intensity <= float(max_value))
    return df.loc[mask].copy()


def _resolve_linearity_fit_degree(fit: dict[str, Any] | None) -> int:
    if not isinstance(fit, dict):
        return 1
    degree_raw = pd.to_numeric(pd.Series([fit.get("degree")]), errors="coerce").iloc[0]
    if np.isfinite(degree_raw) and int(degree_raw) >= 2:
        return 2
    if bool(fit.get("quadratic")):
        return 2
    quad_raw = pd.to_numeric(pd.Series([fit.get("quad")]), errors="coerce").iloc[0]
    if np.isfinite(quad_raw) and abs(float(quad_raw)) > 1e-15:
        return 2
    return 1


def _linearity_correction_delta(intensity: pd.Series, fit: dict[str, Any]) -> pd.Series:
    slope = pd.to_numeric(pd.Series([fit.get("slope")]), errors="coerce").iloc[0]
    x_ref = pd.to_numeric(pd.Series([fit.get("x_ref")]), errors="coerce").iloc[0]
    if not (np.isfinite(slope) and np.isfinite(x_ref)):
        return pd.Series(np.nan, index=intensity.index, dtype=float)
    delta = float(slope) * (intensity - float(x_ref))
    if _resolve_linearity_fit_degree(fit) >= 2:
        quad = pd.to_numeric(pd.Series([fit.get("quad")]), errors="coerce").iloc[0]
        if np.isfinite(quad):
            delta = delta + float(quad) * (np.square(intensity) - float(x_ref) ** 2)
    return pd.to_numeric(delta, errors="coerce")


def _apply_manual_linearity_offsets_to_fits(
    fits: dict[str, Any] | None,
    *,
    enabled: bool = False,
    quadratic: bool = False,
    d13_per_10v: float = 0.0,
    d18_per_10v: float = 0.0,
    d13_per_10v2: float = 0.0,
    d18_per_10v2: float = 0.0,
) -> dict[str, Any]:
    """Apply manual coefficient offsets directly to linearity fits.

    This keeps manual offsets effective for downstream correction paths instead
    of letting them be fully absorbed by pre-fit value transforms.
    """
    if not isinstance(fits, dict):
        return {}
    adjusted: dict[str, Any] = dict(fits)
    if not bool(enabled):
        return adjusted

    config_by_isotope = {
        "d13C": {"linear": d13_per_10v, "quadratic": d13_per_10v2},
        "d18O": {"linear": d18_per_10v, "quadratic": d18_per_10v2},
    }
    for isotope_key, coeffs in config_by_isotope.items():
        fit_payload = adjusted.get(isotope_key, {})
        fit = dict(fit_payload) if isinstance(fit_payload, dict) else {}
        x_ref_num = pd.to_numeric(pd.Series([fit.get("x_ref")]), errors="coerce").iloc[0]
        x_ref = float(x_ref_num) if np.isfinite(x_ref_num) else 0.0

        if bool(quadratic):
            offset_raw = pd.to_numeric(pd.Series([coeffs["quadratic"]]), errors="coerce").iloc[0]
            if not np.isfinite(offset_raw):
                offset_raw = pd.to_numeric(pd.Series([coeffs["linear"]]), errors="coerce").iloc[0]
            if np.isfinite(offset_raw):
                quad_offset = float(offset_raw) / 100.0
                base_quad = pd.to_numeric(pd.Series([fit.get("quad")]), errors="coerce").iloc[0]
                base_quad_value = float(base_quad) if np.isfinite(base_quad) else 0.0
                fit["quad"] = base_quad_value + quad_offset
                base_intercept = pd.to_numeric(pd.Series([fit.get("intercept")]), errors="coerce").iloc[0]
                if np.isfinite(base_intercept):
                    # Keep the reference-intensity anchor stable after offsetting curvature.
                    fit["intercept"] = float(base_intercept) - quad_offset * (x_ref**2)
                fit["degree"] = max(int(fit.get("degree", 1) or 1), 2)
        else:
            offset_raw = pd.to_numeric(pd.Series([coeffs["linear"]]), errors="coerce").iloc[0]
            if np.isfinite(offset_raw):
                slope_offset = float(offset_raw) / 10.0
                base_slope = pd.to_numeric(pd.Series([fit.get("slope")]), errors="coerce").iloc[0]
                base_slope_value = float(base_slope) if np.isfinite(base_slope) else 0.0
                fit["slope"] = base_slope_value + slope_offset
                base_intercept = pd.to_numeric(pd.Series([fit.get("intercept")]), errors="coerce").iloc[0]
                if np.isfinite(base_intercept):
                    # Keep the reference-intensity anchor stable after offsetting slope.
                    fit["intercept"] = float(base_intercept) - slope_offset * x_ref
        adjusted[isotope_key] = fit
    return adjusted


def _offset_number(value: Any, fallback: float = 0.0) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if np.isfinite(parsed):
        return float(parsed)
    fallback_num = pd.to_numeric(pd.Series([fallback]), errors="coerce").iloc[0]
    return float(fallback_num) if np.isfinite(fallback_num) else 0.0


def _has_effective_line_offsets(line_1_offset: float, line_2_offset: float) -> bool:
    line1 = _offset_number(line_1_offset, 0.0)
    line2 = _offset_number(line_2_offset, 0.0)
    return abs(float(line1)) > 1e-15 or abs(float(line2)) > 1e-15


def _resolve_isotope_line_offsets(
    line_1_offset: float = 0.0,
    line_2_offset: float = 0.0,
    line_1_offset_d13: float | None = None,
    line_1_offset_d18: float | None = None,
    line_2_offset_d13: float | None = None,
    line_2_offset_d18: float | None = None,
) -> dict[str, tuple[float, float]]:
    base_line_1 = _offset_number(line_1_offset, 0.0)
    base_line_2 = _offset_number(line_2_offset, 0.0)
    return {
        "d13C": (
            _offset_number(line_1_offset_d13, base_line_1),
            _offset_number(line_2_offset_d13, base_line_2),
        ),
        "d18O": (
            _offset_number(line_1_offset_d18, base_line_1),
            _offset_number(line_2_offset_d18, base_line_2),
        ),
    }


def _resolve_isotope_specific_line_offsets(
    line_1_offset_d13: float | None = None,
    line_1_offset_d18: float | None = None,
    line_2_offset_d13: float | None = None,
    line_2_offset_d18: float | None = None,
) -> dict[str, tuple[float, float]]:
    return {
        "d13C": (
            _offset_number(line_1_offset_d13, 0.0),
            _offset_number(line_2_offset_d13, 0.0),
        ),
        "d18O": (
            _offset_number(line_1_offset_d18, 0.0),
            _offset_number(line_2_offset_d18, 0.0),
        ),
    }


def _apply_isotope_line_offsets(
    df: pd.DataFrame,
    line_1_offset_d13: float | None = None,
    line_1_offset_d18: float | None = None,
    line_2_offset_d13: float | None = None,
    line_2_offset_d18: float | None = None,
) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    line_col = _find_column(df, "Line")
    if not line_col:
        return df
    offsets = _resolve_isotope_specific_line_offsets(
        line_1_offset_d13=line_1_offset_d13,
        line_1_offset_d18=line_1_offset_d18,
        line_2_offset_d13=line_2_offset_d13,
        line_2_offset_d18=line_2_offset_d18,
    )
    has_any_offset = any(_has_effective_line_offsets(pair[0], pair[1]) for pair in offsets.values())
    if not has_any_offset:
        return df

    work = df.copy()
    line_values = pd.to_numeric(work[line_col], errors="coerce")
    col_map = {"d13C": "d 13C/12C  Mean", "d18O": "d 18O/16O  Mean"}
    for isotope_key, value_col in col_map.items():
        if value_col not in work.columns:
            continue
        line1, line2 = offsets[isotope_key]
        if not _has_effective_line_offsets(line1, line2):
            continue
        values = pd.to_numeric(work[value_col], errors="coerce")
        adjustment = pd.Series(0.0, index=work.index, dtype=float)
        if abs(float(line1)) > 1e-15:
            adjustment = adjustment + float(line1) * line_values.eq(1)
        if abs(float(line2)) > 1e-15:
            adjustment = adjustment + float(line2) * line_values.eq(2)
        adjusted = values + adjustment
        work[value_col] = adjusted.where(np.isfinite(values), values)
    return work


def _linearity_adjusted_intensity_series(
    df: pd.DataFrame,
    intensity_col: str,
    line_1_offset: float = 0.0,
    line_2_offset: float = 0.0,
) -> pd.Series:
    if intensity_col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    intensity = pd.to_numeric(df[intensity_col], errors="coerce")
    line1 = _offset_number(line_1_offset, 0.0)
    line2 = _offset_number(line_2_offset, 0.0)
    if not _has_effective_line_offsets(line1, line2):
        return intensity
    line_col = _find_column(df, "Line")
    if not line_col:
        return intensity
    line_values = pd.to_numeric(df[line_col], errors="coerce")
    adjustment = pd.Series(0.0, index=df.index, dtype=float)
    if abs(float(line1)) > 1e-15:
        adjustment = adjustment + float(line1) * line_values.eq(1)
    if abs(float(line2)) > 1e-15:
        adjustment = adjustment + float(line2) * line_values.eq(2)
    adjusted = intensity + adjustment
    return adjusted.where(np.isfinite(intensity), intensity)


def _with_isotope_linearity_intensity_columns(
    df: pd.DataFrame,
    intensity_col: str,
    line_1_offset: float = 0.0,
    line_2_offset: float = 0.0,
    line_1_offset_d13: float | None = None,
    line_1_offset_d18: float | None = None,
    line_2_offset_d13: float | None = None,
    line_2_offset_d18: float | None = None,
) -> tuple[pd.DataFrame, str, str]:
    if df is None or df.empty:
        return df, intensity_col, intensity_col
    d13_base_col = intensity_col
    d18_base_col = intensity_col
    if intensity_col == CYCLE1_SIGNAL_DIFF44_COL:
        if CYCLE1_SIGNAL_DIFF45_COL in df.columns:
            d13_vals = pd.to_numeric(df[CYCLE1_SIGNAL_DIFF45_COL], errors="coerce")
            if d13_vals.notna().any():
                d13_base_col = CYCLE1_SIGNAL_DIFF45_COL
        if CYCLE1_SIGNAL_DIFF46_COL in df.columns:
            d18_vals = pd.to_numeric(df[CYCLE1_SIGNAL_DIFF46_COL], errors="coerce")
            if d18_vals.notna().any():
                d18_base_col = CYCLE1_SIGNAL_DIFF46_COL
    if d13_base_col not in df.columns and d18_base_col not in df.columns:
        return df, intensity_col, intensity_col
    offsets = _resolve_isotope_line_offsets(
        line_1_offset=line_1_offset,
        line_2_offset=line_2_offset,
        line_1_offset_d13=line_1_offset_d13,
        line_1_offset_d18=line_1_offset_d18,
        line_2_offset_d13=line_2_offset_d13,
        line_2_offset_d18=line_2_offset_d18,
    )
    d13_offsets = offsets["d13C"]
    d18_offsets = offsets["d18O"]
    d13_has_offsets = _has_effective_line_offsets(*d13_offsets)
    d18_has_offsets = _has_effective_line_offsets(*d18_offsets)
    if not d13_has_offsets and not d18_has_offsets:
        return df, d13_base_col, d18_base_col

    work = df.copy()
    d13_intensity_col = d13_base_col
    d18_intensity_col = d18_base_col
    if (
        d13_has_offsets
        and d18_has_offsets
        and d13_offsets == d18_offsets
        and d13_base_col == d18_base_col
        and d13_base_col in work.columns
    ):
        shared_col = f"{d13_base_col}__linearity_line_offset"
        work[shared_col] = _linearity_adjusted_intensity_series(work, d13_base_col, d13_offsets[0], d13_offsets[1])
        return work, shared_col, shared_col
    if d13_has_offsets and d13_base_col in work.columns:
        d13_intensity_col = f"{d13_base_col}__linearity_d13"
        work[d13_intensity_col] = _linearity_adjusted_intensity_series(work, d13_base_col, d13_offsets[0], d13_offsets[1])
    if d18_has_offsets and d18_base_col in work.columns:
        d18_intensity_col = f"{d18_base_col}__linearity_d18"
        work[d18_intensity_col] = _linearity_adjusted_intensity_series(work, d18_base_col, d18_offsets[0], d18_offsets[1])
    return work, d13_intensity_col, d18_intensity_col


def _apply_linearity_correction(
    df: pd.DataFrame,
    intensity_col: str,
    fits: dict[str, Any],
) -> pd.DataFrame:
    work = df.copy()
    fallback_intensity = (
        pd.to_numeric(work[intensity_col], errors="coerce")
        if intensity_col in work.columns
        else pd.Series(np.nan, index=work.index, dtype=float)
    )
    d13_intensity_col = (
        str(fits.get("d13_intensity_col", "")).strip()
        if isinstance(fits, dict)
        else ""
    )
    d18_intensity_col = (
        str(fits.get("d18_intensity_col", "")).strip()
        if isinstance(fits, dict)
        else ""
    )
    if d13_intensity_col == "" or d13_intensity_col not in work.columns:
        d13_intensity_col = intensity_col
    if d18_intensity_col == "" or d18_intensity_col not in work.columns:
        d18_intensity_col = intensity_col
    d13_intensity = (
        pd.to_numeric(work[d13_intensity_col], errors="coerce")
        if d13_intensity_col in work.columns
        else fallback_intensity
    )
    d18_intensity = (
        pd.to_numeric(work[d18_intensity_col], errors="coerce")
        if d18_intensity_col in work.columns
        else fallback_intensity
    )
    d13_fit = fits.get("d13C", {}) if isinstance(fits, dict) else {}
    d18_fit = fits.get("d18O", {}) if isinstance(fits, dict) else {}
    d13_delta = _linearity_correction_delta(d13_intensity, d13_fit)
    d18_delta = _linearity_correction_delta(d18_intensity, d18_fit)
    if "d 13C/12C  Mean" in work.columns and d13_delta.notna().any():
        values = pd.to_numeric(work["d 13C/12C  Mean"], errors="coerce")
        work["d13C_linearity_corrected"] = (values - d13_delta).where(np.isfinite(values) & np.isfinite(d13_delta))
    if "d 18O/16O  Mean" in work.columns and d18_delta.notna().any():
        values = pd.to_numeric(work["d 18O/16O  Mean"], errors="coerce")
        work["d18O_linearity_corrected"] = (values - d18_delta).where(np.isfinite(values) & np.isfinite(d18_delta))
    if "d13C_calibrated" in work.columns and d13_delta.notna().any():
        values = pd.to_numeric(work["d13C_calibrated"], errors="coerce")
        work["d13C_calibrated_linearity_corrected"] = (values - d13_delta).where(
            np.isfinite(values) & np.isfinite(d13_delta)
        )
    if "d18O_calibrated" in work.columns and d18_delta.notna().any():
        values = pd.to_numeric(work["d18O_calibrated"], errors="coerce")
        work["d18O_calibrated_linearity_corrected"] = (values - d18_delta).where(
            np.isfinite(values) & np.isfinite(d18_delta)
        )
    return work


def _apply_linearity_line_offsets(
    df: pd.DataFrame,
    intensity_col: str,
    line_1_offset: float = 0.0,
    line_2_offset: float = 0.0,
) -> pd.DataFrame:
    if df is None or df.empty or intensity_col not in df.columns:
        return df
    line1 = _offset_number(line_1_offset, 0.0)
    line2 = _offset_number(line_2_offset, 0.0)
    if not _has_effective_line_offsets(line1, line2):
        return df
    if _find_column(df, "Line") is None:
        return df
    work = df.copy()
    work[intensity_col] = _linearity_adjusted_intensity_series(work, intensity_col, line1, line2)
    return work


def _promote_linearity_corrected_raw_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    work = df.copy()
    replacements = (
        ("d13C_linearity_corrected", "d 13C/12C  Mean"),
        ("d18O_linearity_corrected", "d 18O/16O  Mean"),
    )
    for corrected_col, raw_col in replacements:
        if corrected_col not in work.columns or raw_col not in work.columns:
            continue
        corrected = pd.to_numeric(work[corrected_col], errors="coerce")
        raw = pd.to_numeric(work[raw_col], errors="coerce")
        work[raw_col] = corrected.where(corrected.notna(), raw)
    return work


def _resolve_manual_linearity_override_intensity(
    df: pd.DataFrame | None,
    intensity_col: str,
    use_diff_intensity: bool = False,
    selected_intensity_col: str | None = None,
) -> tuple[pd.Series, bool]:
    """Resolve manual-override intensity axis.

    In Samp-Ref mode, use a pressure-weighted mismatch driver:
    ``10 * (Samp-Ref) / Ref * (Samp / Samp_ref)`` where ``Samp_ref`` is the
    median sample intensity. This captures both mismatch and initial intensity
    while preserving per-10V coefficient scaling.
    """
    basis_col = _resolve_selected_linearity_intensity_column(
        df=df,
        use_diff_intensity=use_diff_intensity,
        selected_intensity_col=selected_intensity_col,
    )
    if df is None or basis_col not in df.columns:
        return (pd.Series(dtype=float), False)

    intensity = pd.to_numeric(df[basis_col], errors="coerce")
    if basis_col == CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL:
        return (intensity, True)
    if not bool(use_diff_intensity) or basis_col != CYCLE1_SIGNAL_DIFF44_COL:
        return (intensity, False)
    if CYCLE1_SIGNAL_REF44_COL not in df.columns:
        return (intensity, False)

    ref_intensity = pd.to_numeric(df[CYCLE1_SIGNAL_REF44_COL], errors="coerce")
    valid_ref = np.isfinite(ref_intensity) & (np.abs(ref_intensity) > 1e-12)
    sample_intensity = pd.to_numeric(df[CYCLE1_SIGNAL_SAMP44_COL], errors="coerce") if CYCLE1_SIGNAL_SAMP44_COL in df.columns else None
    with np.errstate(divide="ignore", invalid="ignore"):
        mismatch_10v = (intensity / ref_intensity) * 10.0
    mismatch_10v = mismatch_10v.where(np.isfinite(intensity) & valid_ref)
    if sample_intensity is not None:
        finite_sample = sample_intensity[np.isfinite(sample_intensity)]
        if not finite_sample.empty:
            sample_ref = float(finite_sample.median())
            if np.isfinite(sample_ref) and abs(sample_ref) > 1e-12:
                with np.errstate(divide="ignore", invalid="ignore"):
                    sample_scale = sample_intensity / sample_ref
                weighted = mismatch_10v * sample_scale
                weighted = weighted.where(np.isfinite(mismatch_10v) & np.isfinite(sample_scale))
                if weighted.notna().any():
                    return (weighted, True)
    if mismatch_10v.notna().any():
        return (mismatch_10v, True)
    return (intensity, False)


def _apply_manual_linearity_override_to_standards(
    df: pd.DataFrame,
    selected_standards: list[str],
    enabled: bool = False,
    d13_per_10v: float = 0.0,
    d18_per_10v: float = 0.0,
    d13_per_10v2: float = 0.0,
    d18_per_10v2: float = 0.0,
    quadratic: bool = False,
    use_diff_intensity: bool = False,
    selected_intensity_col: str | None = None,
    fits: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if df is None or df.empty or not bool(enabled) or "Identifier 1" not in df.columns:
        return df
    standards = {str(item).strip() for item in selected_standards if str(item).strip()}
    if not standards:
        return df
    work = df.copy()
    standards_mask = work["Identifier 1"].astype(str).isin(standards)
    if not bool(standards_mask.any()):
        return work
    intensity_col = _resolve_selected_linearity_intensity_column(df=work, use_diff_intensity=use_diff_intensity)
    if selected_intensity_col:
        intensity_col = _resolve_selected_linearity_intensity_column(
            df=work,
            use_diff_intensity=use_diff_intensity,
            selected_intensity_col=selected_intensity_col,
        )
    if intensity_col not in work.columns:
        return work
    intensity, normalized_intensity = _resolve_manual_linearity_override_intensity(
        work,
        intensity_col,
        use_diff_intensity=use_diff_intensity,
        selected_intensity_col=selected_intensity_col,
    )
    valid_intensity = np.isfinite(intensity)
    standards_df = work.loc[standards_mask].copy()
    standards_intensity = intensity.loc[standards_mask]

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
            finite_scope = standards_intensity[np.isfinite(standards_intensity)]
            x_ref = float(finite_scope.median()) if not finite_scope.empty else 0.0
        else:
            x_ref = _resolve_linearity_reference_intensity(
                standards_df,
                isotope_key,
                fits=fits,
                intensity_col=intensity_col,
            )
        values = pd.to_numeric(work[column_name], errors="coerce")
        if bool(quadratic):
            if np.isfinite(quad_num) and (abs(float(quad_num)) > 1e-15 or not np.isfinite(slope_num) or abs(float(slope_num)) <= 1e-15):
                quad_coeff_num = quad_num
            else:
                quad_coeff_num = slope_num
            quad_per_v2 = float(quad_coeff_num) / 100.0
            delta = quad_per_v2 * (np.square(intensity) - float(x_ref) ** 2)
        else:
            slope_per_v = float(slope_num) / 10.0
            delta = slope_per_v * (intensity - float(x_ref))
        corrected = (values - delta).where(np.isfinite(values) & valid_intensity & np.isfinite(delta))
        work.loc[standards_mask, column_name] = corrected.loc[standards_mask]

    _apply_single_column("d 13C/12C  Mean", "d13C", d13_per_10v, d13_per_10v2)
    _apply_single_column("d13C_calibrated", "d13C", d13_per_10v, d13_per_10v2)
    _apply_single_column("d 18O/16O  Mean", "d18O", d18_per_10v, d18_per_10v2)
    _apply_single_column("d18O_calibrated", "d18O", d18_per_10v, d18_per_10v2)
    return work


def _resolve_selected_linearity_intensity_column(
    df: pd.DataFrame | None = None,
    use_diff_intensity: bool = False,
    selected_intensity_col: str | None = None,
) -> str:
    valid_columns = {
        CYCLE1_SIGNAL_SAMP44_COL,
        CYCLE1_SIGNAL_DIFF44_COL,
        CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
    }
    selected = str(selected_intensity_col or "").strip()
    preferred = selected if selected in valid_columns else (CYCLE1_SIGNAL_DIFF44_COL if use_diff_intensity else CYCLE1_SIGNAL_SAMP44_COL)
    fallback_candidates = [
        CYCLE1_SIGNAL_SAMP44_COL,
        CYCLE1_SIGNAL_DIFF44_COL,
        CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
    ]
    candidate_order = [preferred, *[col for col in fallback_candidates if col != preferred]]
    if df is None:
        return preferred
    for candidate in candidate_order:
        if candidate not in df.columns:
            continue
        vals = pd.to_numeric(df[candidate], errors="coerce")
        if vals.notna().any():
            return candidate
    for candidate in candidate_order:
        if candidate in df.columns:
            return candidate
    return preferred


def _resolve_linearity_intensity_column_for_fits(
    fits: dict[str, Any] | None = None,
    df: pd.DataFrame | None = None,
    use_diff_intensity: bool = False,
    selected_intensity_col: str | None = None,
) -> str:
    if isinstance(fits, dict):
        stored_col = fits.get("intensity_col")
        if stored_col in (
            CYCLE1_SIGNAL_SAMP44_COL,
            CYCLE1_SIGNAL_DIFF44_COL,
            CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
        ):
            if df is None:
                return str(stored_col)
            if stored_col in df.columns:
                vals = pd.to_numeric(df[stored_col], errors="coerce")
                if vals.notna().any():
                    return str(stored_col)
    return _resolve_selected_linearity_intensity_column(
        df=df,
        use_diff_intensity=use_diff_intensity,
        selected_intensity_col=selected_intensity_col,
    )


def _linearity_intensity_axis_label(intensity_col: str) -> str:
    return f"Signal Intensity (V) - {intensity_col}"


def _resolve_linearity_reference_intensity(
    df: pd.DataFrame | None,
    isotope_key: str,
    fits: dict[str, Any] | None = None,
    intensity_col: str = CYCLE1_SIGNAL_SAMP44_COL,
    default_value: float = 15.0,
) -> float:
    if isinstance(fits, dict):
        fit = fits.get(str(isotope_key).strip(), {})
        x_ref = pd.to_numeric(pd.Series([fit.get("x_ref")]), errors="coerce").iloc[0]
        if np.isfinite(x_ref):
            return float(x_ref)
    if df is not None and intensity_col in df.columns:
        vals = pd.to_numeric(df[intensity_col], errors="coerce")
        vals = vals[np.isfinite(vals)]
        if not vals.empty:
            median_val = float(vals.median())
            if np.isfinite(median_val):
                return median_val
    return float(default_value)


def _compute_calibration_coefficients(
    standards_df: pd.DataFrame,
    selected_standards: list[str],
    repository: StandardsRepository | None = None,
) -> dict[str, dict[str, float]]:
    repo = repository or StandardsRepository.default()
    coeffs: dict[str, dict[str, float]] = {}
    if standards_df is None or len(selected_standards) not in (1, 2):
        return coeffs
    isotopic_types = {
        "d13C": (ISOTYPE_D13C, "d 13C/12C  Mean"),
        "d18O": (ISOTYPE_D18O, "d 18O/16O  Mean"),
    }
    for iso_key, (iso_type_name, raw_col) in isotopic_types.items():
        slope = np.nan
        intercept = np.nan
        if raw_col not in standards_df.columns:
            continue
        if len(selected_standards) == 1:
            standard = selected_standards[0]
            raw_std = pd.to_numeric(
                standards_df.loc[standards_df["Identifier 1"] == standard, raw_col],
                errors="coerce",
            ).mean()
            true_std = repo.get_true_value(standard, iso_type_name)
            if np.isfinite(raw_std) and np.isfinite(true_std) and abs(raw_std + 1000.0) > 1e-12:
                slope = (true_std + 1000.0) / (raw_std + 1000.0)
                intercept = (1000.0 * slope) - 1000.0
        else:
            standard1, standard2 = selected_standards
            raw_rm1 = pd.to_numeric(
                standards_df.loc[standards_df["Identifier 1"] == standard1, raw_col],
                errors="coerce",
            ).mean()
            raw_rm2 = pd.to_numeric(
                standards_df.loc[standards_df["Identifier 1"] == standard2, raw_col],
                errors="coerce",
            ).mean()
            true_rm1 = repo.get_true_value(standard1, iso_type_name)
            true_rm2 = repo.get_true_value(standard2, iso_type_name)
            denom = raw_rm1 - raw_rm2
            if np.isfinite(raw_rm1) and np.isfinite(raw_rm2) and np.isfinite(denom) and abs(denom) > 1e-12:
                slope = (true_rm1 - true_rm2) / denom
                intercept = true_rm1 - slope * raw_rm1
        if np.isfinite(slope) and np.isfinite(intercept):
            coeffs[iso_key] = {"slope": float(slope), "intercept": float(intercept)}
    return coeffs


def calibrate_results(
    standards_df: pd.DataFrame,
    full_df: pd.DataFrame,
    selected_standards: list[str],
    repository: StandardsRepository | None = None,
) -> pd.DataFrame:
    repo = repository or StandardsRepository.default()
    calibrated_df = full_df.copy()
    isotopic_types = {
        ISOTYPE_D13C: ("d 13C/12C  Mean", "d13C_calibrated"),
        ISOTYPE_D18O: ("d 18O/16O  Mean", "d18O_calibrated"),
    }
    for isotopic_type, (raw_column, calibrated_column) in isotopic_types.items():
        if raw_column not in calibrated_df.columns:
            continue
        if len(selected_standards) == 1:
            standard = selected_standards[0]
            raw_std = standards_df.loc[standards_df["Identifier 1"] == standard, raw_column].mean()
            true_std = repo.get_true_value(standard, isotopic_type)
            calibrated_df[calibrated_column] = pd.to_numeric(
                calibrated_df[raw_column], errors="coerce"
            ).apply(
                lambda raw_sample: single_point_calibration(raw_sample, raw_std, true_std)
                if pd.notna(raw_sample)
                else np.nan
            )
        elif len(selected_standards) == 2:
            standard1, standard2 = selected_standards
            raw_rm1 = standards_df.loc[standards_df["Identifier 1"] == standard1, raw_column].mean()
            raw_rm2 = standards_df.loc[standards_df["Identifier 1"] == standard2, raw_column].mean()
            true_rm1 = repo.get_true_value(standard1, isotopic_type)
            true_rm2 = repo.get_true_value(standard2, isotopic_type)
            calibrated_df[calibrated_column] = pd.to_numeric(
                calibrated_df[raw_column], errors="coerce"
            ).apply(
                lambda raw_sample: double_point_calibration(
                    raw_sample, raw_rm1, true_rm1, raw_rm2, true_rm2
                )
                if pd.notna(raw_sample)
                else np.nan
            )
        else:
            raise ValueError("Please select either one or two standards for calibration.")
    return calibrated_df


def create_calibration_plots(
    standards_reference_df: pd.DataFrame,
    measurement_df: pd.DataFrame,
    selected_standards: list[str],
    color_param: str,
) -> dict[str, go.Figure]:
    def _color_param_label(param: str) -> str:
        return "Date" if _is_date_color_column(param) else str(param)

    def _format_hover_color_value(value: Any) -> str:
        if value is None:
            return "N/A"
        try:
            if pd.isna(value):
                return "N/A"
        except Exception:
            pass
        numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        if pd.notna(numeric) and np.isfinite(float(numeric)):
            return f"{float(numeric):.2f}"
        return str(value)

    figs: dict[str, go.Figure] = {}
    isotopes = {
        ISOTYPE_D13C: {"y_label": "d13C", "measurement_col": "d 13C/12C  Mean"},
        ISOTYPE_D18O: {"y_label": "d18O", "measurement_col": "d 18O/16O  Mean"},
    }
    for isotope_type, isotope_data in isotopes.items():
        isotope_key = "d13C" if isotope_type == ISOTYPE_D13C else "d18O"
        is_date_color = _is_date_color_column(color_param)
        fig = go.Figure()
        hover_color_label = _color_param_label(color_param)
        true_values: list[float] = []
        measured_values: list[float] = []
        coloraxis_cfg: dict[str, Any] = {
            "colorscale": "Viridis",
            "colorbar": {
                "title": {
                    "text": "Date" if is_date_color else color_param,
                    "side": "right",
                },
                "thickness": 20,
                "len": 0.75,
                "y": 0.5,
                "yanchor": "middle",
                "x": 1.15,
                "xanchor": "right",
            },
        }
        color_values_all, colorbar_category_ticks = _prepare_color_values(
            measurement_df[color_param] if color_param in measurement_df.columns else None,
            prefer_dates=_prefer_datetime_color_values(color_param),
        )
        if color_values_all is not None:
            cdata = pd.to_numeric(color_values_all, errors="coerce")
            if cdata.notna().any():
                coloraxis_cfg["cmin"] = float(np.nanmin(cdata))
                coloraxis_cfg["cmax"] = float(np.nanmax(cdata))
        if is_date_color:
            tickvals, ticktext = _build_date_colorbar_ticks(
                color_values_all if color_values_all is not None else measurement_df.get(color_param)
            )
            if tickvals and ticktext:
                coloraxis_cfg["colorbar"].update(
                    tickmode="array",
                    tickvals=tickvals,
                    ticktext=ticktext,
                )
        elif colorbar_category_ticks is not None:
            tickvals, ticktext = colorbar_category_ticks
            if tickvals and ticktext:
                coloraxis_cfg["colorbar"].update(
                    tickmode="array",
                    tickvals=tickvals,
                    ticktext=ticktext,
                )
        for standard in selected_standards:
            match = standards_reference_df[
                (standards_reference_df["Standard"] == standard)
                & (standards_reference_df["Isotopic_Value_Type"] == isotope_type)
            ]
            if match.empty:
                continue
            true_value = float(match["Value"].iloc[0])
            standard_rows = measurement_df.loc[measurement_df["Identifier 1"] == standard].copy()
            if standard_rows.empty:
                continue
            measured_series = pd.to_numeric(standard_rows[isotope_data["measurement_col"]], errors="coerce")
            valid_mask = measured_series.notna() & np.isfinite(measured_series)
            measured_values_for_standard = measured_series.loc[valid_mask].values
            if len(measured_values_for_standard) == 0:
                continue
            color_values_for_standard = None
            if color_values_all is not None:
                color_values_for_standard = color_values_all.loc[standard_rows.index]
                color_values_for_standard = color_values_for_standard.loc[valid_mask].values
            valid_rows = standard_rows.loc[valid_mask]
            id1_values = valid_rows.get("Identifier 1", pd.Series("", index=valid_rows.index)).fillna("").astype(str).to_numpy()
            id2_values = valid_rows.get("Identifier 2", pd.Series("", index=valid_rows.index)).fillna("").astype(str).to_numpy()
            species_values = (
                valid_rows.get("Species", valid_rows.get("Identifier 1", pd.Series("", index=valid_rows.index)))
                .fillna("")
                .astype(str)
                .replace("", "Unknown")
                .to_numpy()
            )
            color_hover_values = (
                valid_rows.get(color_param, pd.Series(index=valid_rows.index, dtype=object))
                .map(_format_hover_color_value)
                .to_numpy()
            )
            customdata = np.column_stack(
                (
                    valid_rows.index.astype(str).to_numpy(),
                    np.full(len(valid_rows), isotope_key, dtype=object),
                    id1_values,
                    id2_values,
                    species_values,
                    color_hover_values,
                )
            )
            true_values.extend([true_value] * len(measured_values_for_standard))
            measured_values.extend(measured_values_for_standard.tolist())
            marker_kwargs: dict[str, Any] = {"size": 10}
            if color_values_for_standard is not None and pd.notna(color_values_for_standard).any():
                marker_kwargs.update(color=color_values_for_standard, coloraxis="coloraxis")
            else:
                marker_kwargs.update(color="rgba(150,150,150,0.8)")
            fig.add_trace(
                go.Scatter(
                    x=[true_value] * len(measured_values_for_standard),
                    y=measured_values_for_standard,
                    mode="markers",
                    name=standard,
                    marker=marker_kwargs,
                    customdata=customdata,
                    hovertemplate=(
                        "Identifier 1: %{customdata[2]}<br>"
                        "Identifier 2: %{customdata[3]}<br>"
                        "Species: %{customdata[4]}<br>"
                        "Row: %{customdata[0]}<br>"
                        f"{hover_color_label}: %{{customdata[5]}}<br>"
                        f"True {isotope_data['y_label']}: %{{x:.3f}}<br>"
                        f"Measured {isotope_data['y_label']}: %{{y:.3f}}<extra></extra>"
                    ),
                )
            )
        true_arr = np.array(true_values, dtype=float)
        measured_arr = np.array(measured_values, dtype=float)
        valid = np.isfinite(true_arr) & np.isfinite(measured_arr)
        true_arr = true_arr[valid]
        measured_arr = measured_arr[valid]
        if len(selected_standards) == 1:
            if len(true_arr) > 0:
                offset = float(np.mean(measured_arr - true_arr))
                annotation_text = f"Offset = {offset:.3f}"
                x_min, x_max = float(np.min(true_arr)) - 1, float(np.max(true_arr)) + 1
                fig.add_trace(
                    go.Scatter(
                        x=[x_min, x_max],
                        y=[x_min + offset, x_max + offset],
                        mode="lines",
                        name="Offset Line",
                        line={"color": "orange", "dash": "dash"},
                    )
                )
            else:
                annotation_text = "No valid points for calibration"
        else:
            if len(true_arr) >= 2:
                true_span = float(np.nanmax(true_arr) - np.nanmin(true_arr))
                if not np.isfinite(true_span) or true_span <= 1e-12:
                    offset = float(np.mean(measured_arr - true_arr))
                    annotation_text = "Regression undefined (identical true values); showing offset"
                    x_min, x_max = float(np.min(true_arr)) - 1, float(np.max(true_arr)) + 1
                    fig.add_trace(
                        go.Scatter(
                            x=[x_min, x_max],
                            y=[x_min + offset, x_max + offset],
                            mode="lines",
                            name="Offset Line",
                            line={"color": "orange", "dash": "dash"},
                        )
                    )
                else:
                    try:
                        slope, intercept, _, _, _ = linregress(true_arr, measured_arr)
                        annotation_text = f"y = {slope:.3f}x + {intercept:.3f}"
                        x_min, x_max = float(np.min(true_arr)) - 1, float(np.max(true_arr)) + 1
                        fig.add_trace(
                            go.Scatter(
                                x=[x_min, x_max],
                                y=[slope * x_min + intercept, slope * x_max + intercept],
                                mode="lines",
                                name="Calibration Line",
                                line={"color": "blue"},
                            )
                        )
                    except ValueError:
                        annotation_text = "Insufficient variation for linear regression"
            else:
                annotation_text = "Insufficient data for linear regression"
        fig.update_layout(
            title=f"{'Single' if len(selected_standards) == 1 else 'Double'} Anchor Calibration for {isotope_type}",
            xaxis_title=f"True {isotope_data['y_label']} value",
            yaxis_title=f"Raw/Measured {isotope_data['y_label']} value",
            showlegend=True,
            width=900,
            height=600,
            margin={"r": 150},
            coloraxis=coloraxis_cfg,
            annotations=[
                {
                    "x": 0.05,
                    "y": 0.85,
                    "xref": "paper",
                    "yref": "paper",
                    "text": annotation_text,
                    "showarrow": False,
                    "font": {"size": 12, "color": "black"},
                    "align": "left",
                    "bordercolor": "black",
                    "borderwidth": 1,
                    "borderpad": 4,
                    "bgcolor": "white",
                }
            ],
        )
        figs[isotope_type] = fig
    return figs
