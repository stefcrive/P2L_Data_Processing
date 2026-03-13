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
    CYCLE1_SIGNAL_SAMP44_COL,
    ISOTYPE_D13C,
    ISOTYPE_D18O,
)
from ..shared.plotting import _build_date_colorbar_ticks, _prepare_color_values
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
) -> pd.DataFrame:
    if not standards:
        return pd.DataFrame(columns=df.columns)
    parts: list[pd.DataFrame] = []
    for standard in standards:
        std_df = df[df["Identifier 1"] == standard].copy()
        if std_df.empty:
            continue
        if method == "Z-Score":
            out13 = identify_outliers(std_df, "d 13C/12C  Mean", sigma)
            out18 = identify_outliers(std_df, "d 18O/16O  Mean", sigma)
        else:
            out13 = identify_outliers_iqr(std_df, "d 13C/12C  Mean", iqr_mult)
            out18 = identify_outliers_iqr(std_df, "d 18O/16O  Mean", iqr_mult)
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


def _compute_linearity_fit(clean_df: pd.DataFrame, y_col: str, x_col: str) -> dict[str, float | int]:
    result: dict[str, float | int] = {
        "slope": float("nan"),
        "intercept": float("nan"),
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
    lr = linregress(x, y)
    result["slope"] = float(lr.slope)
    result["intercept"] = float(lr.intercept)
    result["r2"] = float(lr.rvalue**2)
    result["x_ref"] = float(np.median(x.values))
    result["n"] = int(len(x))
    return result


def _apply_linearity_correction(
    df: pd.DataFrame,
    intensity_col: str,
    fits: dict[str, Any],
) -> pd.DataFrame:
    work = df.copy()
    if intensity_col not in work.columns:
        return work
    intensity = pd.to_numeric(work[intensity_col], errors="coerce")
    if "d 13C/12C  Mean" in work.columns and np.isfinite(fits.get("d13C", {}).get("slope", np.nan)):
        slope = fits["d13C"]["slope"]
        x_ref = fits["d13C"]["x_ref"]
        values = pd.to_numeric(work["d 13C/12C  Mean"], errors="coerce")
        work["d13C_linearity_corrected"] = (values - slope * (intensity - x_ref)).where(
            np.isfinite(values) & np.isfinite(intensity)
        )
    if "d 18O/16O  Mean" in work.columns and np.isfinite(fits.get("d18O", {}).get("slope", np.nan)):
        slope = fits["d18O"]["slope"]
        x_ref = fits["d18O"]["x_ref"]
        values = pd.to_numeric(work["d 18O/16O  Mean"], errors="coerce")
        work["d18O_linearity_corrected"] = (values - slope * (intensity - x_ref)).where(
            np.isfinite(values) & np.isfinite(intensity)
        )
    if "d13C_calibrated" in work.columns and np.isfinite(fits.get("d13C", {}).get("slope", np.nan)):
        slope = fits["d13C"]["slope"]
        x_ref = fits["d13C"]["x_ref"]
        values = pd.to_numeric(work["d13C_calibrated"], errors="coerce")
        work["d13C_calibrated_linearity_corrected"] = (
            values - slope * (intensity - x_ref)
        ).where(np.isfinite(values) & np.isfinite(intensity))
    if "d18O_calibrated" in work.columns and np.isfinite(fits.get("d18O", {}).get("slope", np.nan)):
        slope = fits["d18O"]["slope"]
        x_ref = fits["d18O"]["x_ref"]
        values = pd.to_numeric(work["d18O_calibrated"], errors="coerce")
        work["d18O_calibrated_linearity_corrected"] = (
            values - slope * (intensity - x_ref)
        ).where(np.isfinite(values) & np.isfinite(intensity))
    return work


def _resolve_selected_linearity_intensity_column(
    df: pd.DataFrame | None = None,
    use_diff_intensity: bool = False,
) -> str:
    preferred = CYCLE1_SIGNAL_DIFF44_COL if use_diff_intensity else CYCLE1_SIGNAL_SAMP44_COL
    fallback = CYCLE1_SIGNAL_SAMP44_COL if preferred != CYCLE1_SIGNAL_SAMP44_COL else None
    if df is None:
        return preferred
    if preferred in df.columns:
        vals = pd.to_numeric(df[preferred], errors="coerce")
        if vals.notna().any():
            return preferred
    if fallback and fallback in df.columns:
        vals = pd.to_numeric(df[fallback], errors="coerce")
        if vals.notna().any():
            return fallback
    if preferred in df.columns:
        return preferred
    if fallback and fallback in df.columns:
        return fallback
    return preferred


def _resolve_linearity_intensity_column_for_fits(
    fits: dict[str, Any] | None = None,
    df: pd.DataFrame | None = None,
    use_diff_intensity: bool = False,
) -> str:
    if isinstance(fits, dict):
        stored_col = fits.get("intensity_col")
        if stored_col in (CYCLE1_SIGNAL_SAMP44_COL, CYCLE1_SIGNAL_DIFF44_COL):
            if df is None:
                return str(stored_col)
            if stored_col in df.columns:
                vals = pd.to_numeric(df[stored_col], errors="coerce")
                if vals.notna().any():
                    return str(stored_col)
    return _resolve_selected_linearity_intensity_column(df=df, use_diff_intensity=use_diff_intensity)


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
    figs: dict[str, go.Figure] = {}
    isotopes = {
        ISOTYPE_D13C: {"y_label": "d13C", "measurement_col": "d 13C/12C  Mean"},
        ISOTYPE_D18O: {"y_label": "d18O", "measurement_col": "d 18O/16O  Mean"},
    }
    for isotope_type, isotope_data in isotopes.items():
        isotope_key = "d13C" if isotope_type == ISOTYPE_D13C else "d18O"
        fig = go.Figure()
        true_values: list[float] = []
        measured_values: list[float] = []
        coloraxis_cfg: dict[str, Any] = {
            "colorscale": "Viridis",
            "colorbar": {
                "title": {
                    "text": "Date" if color_param == "Date_ordinal" else color_param,
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
            measurement_df[color_param] if color_param in measurement_df.columns else None
        )
        if color_values_all is not None:
            cdata = pd.to_numeric(color_values_all, errors="coerce")
            if cdata.notna().any():
                coloraxis_cfg["cmin"] = float(np.nanmin(cdata))
                coloraxis_cfg["cmax"] = float(np.nanmax(cdata))
        if color_param == "Date_ordinal" and color_param in measurement_df.columns:
            tickvals, ticktext = _build_date_colorbar_ticks(measurement_df[color_param])
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
            customdata = np.column_stack(
                (
                    valid_rows.index.astype(str).to_numpy(),
                    np.full(len(valid_rows), isotope_key, dtype=object),
                    id1_values,
                    id2_values,
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
                        "Row: %{customdata[0]}<br>"
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
