from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

try:
    import plotly.graph_objects as go
except ModuleNotFoundError:  # pragma: no cover
    go = None

from ..constants import (
    CYCLE1_SIGNAL_DIFF44_COL,
    CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
    CYCLE1_SIGNAL_SAMP44_COL,
    ISOTYPE_D13C,
    ISOTYPE_D18O,
)
from ..contracts import (
    CalibrationAvailableValues,
    CalibrationConfig,
    CalibrationOfficialValue,
    CalibrationPrecisionSummary,
    CalibrationStandardSection,
    CalibrationWorkspace,
)
from ..shared.dataframe import _ensure_cycle1_signal_difference_columns, _find_column, _parse_numeric_token
from ..shared.json_compat import to_json_compatible
from ..shared.plotting import (
    _build_date_colorbar_ticks,
    _build_isotope_3d_scatter,
    _is_date_color_column,
    _prefer_datetime_color_values,
    _prepare_color_values,
)
from ..standards import StandardsRepository
from .core import (
    _apply_isotope_line_offsets,
    _apply_linearity_correction,
    _apply_manual_linearity_override_to_standards,
    _compute_linearity_fit,
    _filter_linearity_fit_input_by_max_intensity,
    _filter_standards_remove_outliers,
    _linearity_correction_delta,
    _promote_linearity_corrected_raw_columns,
    _resolve_linearity_intensity_column_for_fits,
    _resolve_selected_linearity_intensity_column,
    _with_isotope_linearity_intensity_columns,
    create_calibration_plots,
    identify_outliers,
    identify_outliers_iqr,
)


def normalize_calibration_config(raw: dict[str, Any] | None) -> CalibrationConfig:
    payload = dict(raw or {})
    config = CalibrationConfig.model_validate(payload)
    config.linearity.intensity_col = _resolve_selected_linearity_intensity_column(
        use_diff_intensity=bool(config.linearity.use_diff_intensity),
        selected_intensity_col=getattr(config.linearity, "intensity_col", None),
    )
    config.linearity.use_diff_intensity = config.linearity.intensity_col == CYCLE1_SIGNAL_DIFF44_COL
    return config


def _figure_json(fig: go.Figure | None) -> dict[str, Any]:
    return to_json_compatible(fig.to_plotly_json()) if fig is not None else {}


def _candidate_color_columns(df: pd.DataFrame) -> list[str]:
    preferred = [
        "Date",
        "Identifier 1",
        "Identifier 2",
        "Species",
        "Comment",
        "Label",
        CYCLE1_SIGNAL_SAMP44_COL,
        CYCLE1_SIGNAL_DIFF44_COL,
        CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
        "leak_rate",
        "Line",
        "d 13C/12C  Mean",
        "d 18O/16O  Mean",
    ]
    return [col for col in preferred if col in df.columns]


def _candidate_z_columns(df: pd.DataFrame) -> list[str]:
    preferred = [
        CYCLE1_SIGNAL_SAMP44_COL,
        CYCLE1_SIGNAL_DIFF44_COL,
        CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
        "leak_rate",
        "Line",
        "d 13C/12C  Mean",
        "d 18O/16O  Mean",
    ]
    return [col for col in preferred if col in df.columns]


def _date_bounds(df: pd.DataFrame) -> tuple[str | None, str | None, str | None]:
    date_col = _find_column(df, "Date")
    if not date_col:
        return (None, None, None)
    date_series = pd.to_datetime(df[date_col], errors="coerce").dropna()
    if date_series.empty:
        return (None, None, date_col)
    return (
        date_series.min().date().isoformat(),
        date_series.max().date().isoformat(),
        date_col,
    )


def _apply_precision_date_range(df: pd.DataFrame, config: CalibrationConfig) -> pd.DataFrame:
    if df is None or df.empty or not config.precision_date_range:
        return df.copy()
    start_raw, end_raw = config.precision_date_range
    date_col = _find_column(df, "Date")
    if not date_col or not start_raw or not end_raw:
        return df.copy()
    start_ts = pd.to_datetime(start_raw, errors="coerce")
    end_ts = pd.to_datetime(end_raw, errors="coerce")
    if pd.isna(start_ts) or pd.isna(end_ts):
        return df.copy()
    date_series = pd.to_datetime(df[date_col], errors="coerce")
    mask = (date_series >= start_ts) & (date_series <= (end_ts + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)))
    return df.loc[mask].copy()


def _standard_outlier_masks(
    std_df: pd.DataFrame,
    config: CalibrationConfig,
    outlier_reference_df: pd.DataFrame | None = None,
) -> tuple[pd.Series, pd.Series]:
    if std_df is None or std_df.empty:
        empty = pd.Series(dtype=bool)
        return empty, empty
    source = outlier_reference_df.reindex(std_df.index) if outlier_reference_df is not None else std_df
    if config.calibration_type == "Z-Score":
        out13 = identify_outliers(source, "d 13C/12C  Mean", config.sigma_level)
        out18 = identify_outliers(source, "d 18O/16O  Mean", config.sigma_level)
    else:
        out13 = identify_outliers_iqr(source, "d 13C/12C  Mean", config.iqr_multiplier)
        out18 = identify_outliers_iqr(source, "d 18O/16O  Mean", config.iqr_multiplier)
    return out13.reindex(std_df.index, fill_value=False), out18.reindex(std_df.index, fill_value=False)


def _sequence_axis(df: pd.DataFrame) -> pd.Series:
    if "Identifier 2" in df.columns:
        values = df["Identifier 2"].apply(_parse_numeric_token)
        if values.notna().any():
            return values
    return pd.Series(np.arange(1, len(df) + 1), index=df.index, dtype=float)


def _outlier_rows(std_df: pd.DataFrame, mask: pd.Series, value_col: str) -> list[dict[str, Any]]:
    if std_df is None or std_df.empty or value_col not in std_df.columns:
        return []
    rows = std_df.loc[mask.fillna(False)].copy()
    if rows.empty:
        return []
    table = pd.DataFrame(
        {
            "Row": rows.index.astype(str),
            "Identifier 2": rows.get("Identifier 2", pd.Series("", index=rows.index)).astype(str),
            "Value": pd.to_numeric(rows[value_col], errors="coerce"),
        }
    )
    return to_json_compatible(table.to_dict(orient="records"))


def _color_param_label(color_param: str) -> str:
    return "Date" if _is_date_color_column(color_param) else str(color_param)


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


def _resolve_species_labels(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=object)
    species = df.get("Species", df.get("Identifier 1", pd.Series(index=df.index, dtype=object)))
    labels = pd.Series(species, index=df.index).fillna("").astype(str).str.strip()
    labels = labels.where(~labels.str.lower().eq("nan"), "")
    labels = labels.where(labels != "", "Unknown")
    return labels


def _attach_hover_context(df: pd.DataFrame, color_param: str) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    work = df.copy()
    if work.empty:
        work["__hover_species"] = pd.Series(index=work.index, dtype=object)
        work["__hover_color_value"] = pd.Series(index=work.index, dtype=object)
        return work
    work["__hover_species"] = _resolve_species_labels(work)
    source = work.get(color_param, pd.Series(index=work.index, dtype=object))
    work["__hover_color_value"] = source.reindex(work.index).map(_format_hover_color_value)
    return work


def _build_point_customdata(df: pd.DataFrame, isotope_key: str) -> np.ndarray:
    row_labels = df.index.astype(str).to_numpy()
    id1_values = df.get("Identifier 1", pd.Series("", index=df.index)).fillna("").astype(str).to_numpy()
    id2_values = df.get("Identifier 2", pd.Series("", index=df.index)).fillna("").astype(str).to_numpy()
    species_values = (
        df.get("__hover_species", df.get("Species", df.get("Identifier 1", pd.Series(index=df.index, dtype=object))))
        .fillna("Unknown")
        .astype(str)
        .to_numpy()
    )
    color_values = df.get("__hover_color_value", pd.Series("N/A", index=df.index)).fillna("N/A").astype(str).to_numpy()
    return np.column_stack((row_labels, np.full(len(df), isotope_key, dtype=object), id1_values, id2_values, species_values, color_values))


def _build_standard_outlier_figure(
    std_df: pd.DataFrame,
    outlier_mask: pd.Series,
    value_col: str,
    y_label: str,
    title: str,
    color_param: str,
    true_value: float | None = None,
) -> dict[str, Any]:
    if go is None or std_df is None or std_df.empty or value_col not in std_df.columns:
        return {}
    work = _attach_hover_context(std_df, color_param)
    work["x_axis"] = _sequence_axis(work)
    values = pd.to_numeric(work[value_col], errors="coerce")
    inlier_mask = ~outlier_mask.reindex(work.index, fill_value=False)
    isotope_key = "d13C" if "13" in str(value_col) else "d18O"
    color_label = _color_param_label(color_param)
    fig = go.Figure()
    is_date_color = _is_date_color_column(color_param)
    color_values, colorbar_category_ticks = _prepare_color_values(
        work[color_param] if color_param in work.columns else None,
        prefer_dates=_prefer_datetime_color_values(color_param),
    )
    color_numeric = pd.to_numeric(color_values, errors="coerce") if color_values is not None else pd.Series(np.nan, index=work.index)
    has_color = bool(color_numeric.notna().any())
    coloraxis_cfg: dict[str, Any] = {
        "colorscale": "Viridis",
        "colorbar": {
            "title": {
                "text": "Date" if is_date_color else color_param,
                "side": "right",
            },
            "thickness": 16,
            "len": 0.75,
            "y": 0.5,
            "yanchor": "middle",
            "x": 1.04,
            "xanchor": "left",
        },
    }
    if has_color:
        finite_color = color_numeric[np.isfinite(color_numeric)]
        if not finite_color.empty:
            cmin = float(finite_color.min())
            cmax = float(finite_color.max())
            if np.isfinite(cmin) and np.isfinite(cmax):
                if cmin == cmax:
                    cmin = cmin - 0.5
                    cmax = cmax + 0.5
                coloraxis_cfg["cmin"] = cmin
                coloraxis_cfg["cmax"] = cmax
        if is_date_color:
            tickvals, ticktext = _build_date_colorbar_ticks(color_values if color_values is not None else work.get(color_param))
            if tickvals and ticktext:
                coloraxis_cfg["colorbar"].update(tickmode="array", tickvals=tickvals, ticktext=ticktext)
        elif colorbar_category_ticks is not None:
            tickvals, ticktext = colorbar_category_ticks
            if tickvals and ticktext:
                coloraxis_cfg["colorbar"].update(tickmode="array", tickvals=tickvals, ticktext=ticktext)
    inliers = work.loc[inlier_mask]
    outliers = work.loc[~inlier_mask]
    if not inliers.empty:
        inlier_marker: dict[str, Any] = {"size": 8}
        if has_color:
            inlier_marker.update(color=color_numeric.loc[inliers.index], coloraxis="coloraxis")
        else:
            inlier_marker.update(color="#222222")
        fig.add_trace(
            go.Scatter(
                x=inliers["x_axis"],
                y=pd.to_numeric(inliers[value_col], errors="coerce"),
                mode="markers",
                name="Included",
                marker=inlier_marker,
                customdata=_build_point_customdata(inliers, isotope_key),
                hovertemplate=(
                    "Identifier 1: %{customdata[2]}<br>"
                    "Identifier 2: %{customdata[3]}<br>"
                    "Species: %{customdata[4]}<br>"
                    "Row: %{customdata[0]}<br>"
                    f"{color_label}: %{{customdata[5]}}<br>"
                    f"{y_label}: %{{y:.3f}}<extra></extra>"
                ),
            )
        )
    if not outliers.empty:
        outlier_marker: dict[str, Any] = {"size": 10, "symbol": "x", "line": {"color": "#dc2626", "width": 2}}
        if has_color:
            outlier_marker.update(color=color_numeric.loc[outliers.index], coloraxis="coloraxis")
        else:
            outlier_marker.update(color="#ef4444")
        fig.add_trace(
            go.Scatter(
                x=outliers["x_axis"],
                y=pd.to_numeric(outliers[value_col], errors="coerce"),
                mode="markers",
                name="Outliers",
                marker=outlier_marker,
                customdata=_build_point_customdata(outliers, isotope_key),
                hovertemplate=(
                    "Identifier 1: %{customdata[2]}<br>"
                    "Identifier 2: %{customdata[3]}<br>"
                    "Species: %{customdata[4]}<br>"
                    "Row: %{customdata[0]}<br>"
                    f"{color_label}: %{{customdata[5]}}<br>"
                    f"{y_label}: %{{y:.3f}}<extra></extra>"
                ),
            )
        )
    stats_series = pd.to_numeric(inliers[value_col], errors="coerce").dropna()
    if stats_series.empty:
        stats_series = values.dropna()
    if not stats_series.empty:
        x_min = float(work["x_axis"].min()) if work["x_axis"].notna().any() else 0.0
        x_max = float(work["x_axis"].max()) if work["x_axis"].notna().any() else 1.0
        mean_val = float(stats_series.mean())
        std_val = float(stats_series.std()) if len(stats_series) > 1 else float("nan")
        fig.add_trace(
            go.Scatter(
                x=[x_min, x_max],
                y=[mean_val, mean_val],
                mode="lines",
                name="Average",
                line=dict(color="#7e22ce", width=2),
            )
        )
        if np.isfinite(std_val) and std_val > 0:
            upper = mean_val + std_val
            lower = mean_val - std_val
            fig.add_trace(
                go.Scatter(
                    x=[x_min, x_max],
                    y=[upper, upper],
                    mode="lines",
                    name="+1 SD",
                    line=dict(color="#15803d", dash="dot", width=2),
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=[x_min, x_max],
                    y=[lower, lower],
                    mode="lines",
                    name="-1 SD",
                    line=dict(color="#15803d", dash="dot", width=2),
                )
            )
    if true_value is not None and np.isfinite(true_value):
        x_min = float(work["x_axis"].min()) if work["x_axis"].notna().any() else 0.0
        x_max = float(work["x_axis"].max()) if work["x_axis"].notna().any() else 1.0
        fig.add_trace(
            go.Scatter(
                x=[x_min, x_max],
                y=[float(true_value), float(true_value)],
                mode="lines",
                name="True Value",
                line=dict(color="#0f172a", dash="dash", width=3),
            )
        )
    fig.update_layout(
        title=title,
        xaxis_title="Sequence",
        yaxis_title=y_label,
        hovermode="closest",
        coloraxis=coloraxis_cfg if has_color else None,
    )
    return _figure_json(fig)


def _build_calibration_crossplot(df: pd.DataFrame, color_param: str) -> dict[str, Any]:
    if go is None or df is None or df.empty:
        return {}

    x_vals = pd.to_numeric(df.get("d 18O/16O  Mean"), errors="coerce")
    y_vals = pd.to_numeric(df.get("d 13C/12C  Mean"), errors="coerce")
    valid = x_vals.notna() & y_vals.notna()
    if not valid.any():
        return {}

    plot_df = _attach_hover_context(df.loc[valid].copy(), color_param)
    plot_df["_group"] = (
        plot_df.get("Identifier 1", pd.Series("Standards", index=plot_df.index))
        .fillna("Unknown")
        .astype(str)
        .str.strip()
        .replace("", "Unknown")
    )

    is_date_color = _is_date_color_column(color_param)
    color_values, colorbar_category_ticks = _prepare_color_values(
        plot_df[color_param] if color_param in plot_df.columns else None,
        prefer_dates=_prefer_datetime_color_values(color_param),
    )
    color_numeric = pd.to_numeric(color_values, errors="coerce") if color_values is not None else pd.Series(np.nan, index=plot_df.index)
    has_color = bool(color_numeric.notna().any())
    color_min: float | None = None
    color_max: float | None = None
    if has_color:
        finite_colors = color_numeric[np.isfinite(color_numeric)]
        if finite_colors.empty:
            has_color = False
        else:
            color_min = float(finite_colors.min())
            color_max = float(finite_colors.max())
            if color_min == color_max:
                color_min -= 0.5
                color_max += 0.5

    fig = go.Figure()
    color_label = _color_param_label(color_param)
    show_colorbar = has_color
    for group, group_df in plot_df.groupby("_group", dropna=False):
        marker: dict[str, Any] = {"size": 9, "opacity": 0.85}
        if has_color and color_min is not None and color_max is not None:
            marker.update(
                color=color_numeric.loc[group_df.index],
                colorscale="Viridis",
                cmin=color_min,
                cmax=color_max,
                showscale=show_colorbar,
            )
            if show_colorbar:
                colorbar_cfg: dict[str, Any] = {
                    "title": {
                        "text": "Date" if is_date_color else str(color_param),
                        "side": "right",
                    },
                    "thickness": 16,
                    "len": 0.7,
                    "y": 0.5,
                    "yanchor": "middle",
                    "x": 1.04,
                    "xanchor": "left",
                }
                if is_date_color:
                    tickvals, ticktext = _build_date_colorbar_ticks(color_values if color_values is not None else plot_df.get(color_param))
                    if tickvals and ticktext:
                        colorbar_cfg.update(tickmode="array", tickvals=tickvals, ticktext=ticktext)
                elif colorbar_category_ticks is not None:
                    tickvals, ticktext = colorbar_category_ticks
                    if tickvals and ticktext:
                        colorbar_cfg.update(tickmode="array", tickvals=tickvals, ticktext=ticktext)
                marker["colorbar"] = colorbar_cfg
                show_colorbar = False
        else:
            marker["color"] = "#1d4ed8"

        customdata = _build_point_customdata(group_df, "cross")
        fig.add_trace(
            go.Scatter(
                x=pd.to_numeric(group_df.get("d 18O/16O  Mean"), errors="coerce"),
                y=pd.to_numeric(group_df.get("d 13C/12C  Mean"), errors="coerce"),
                mode="markers",
                name=str(group),
                marker=marker,
                customdata=customdata,
                hovertemplate=(
                    "Identifier 1: %{customdata[2]}<br>"
                    "Identifier 2: %{customdata[3]}<br>"
                    "Species: %{customdata[4]}<br>"
                    "Row: %{customdata[0]}<br>"
                    f"{color_label}: %{{customdata[5]}}<br>"
                    "d18O: %{x:.3f}<br>"
                    "d13C: %{y:.3f}<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        title="d13C vs d18O",
        xaxis={"title": "d18O", "constrain": "domain"},
        yaxis={"title": "d13C", "constrain": "domain"},
        hovermode="closest",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0.0, "xanchor": "left"},
        margin={"l": 40, "r": 20, "t": 80, "b": 40},
        height=720,
    )
    return _figure_json(fig)


def _build_linearity_figure(
    df_src: pd.DataFrame,
    y_col: str,
    fit: dict[str, Any],
    intensity_col: str,
    color_param: str,
    corrected: bool = False,
) -> dict[str, Any]:
    if go is None or df_src is None or df_src.empty or y_col not in df_src.columns or intensity_col not in df_src.columns:
        return {}
    intensity = pd.to_numeric(df_src[intensity_col], errors="coerce")
    y = pd.to_numeric(df_src[y_col], errors="coerce")
    color_values, _ = _prepare_color_values(
        df_src[color_param] if color_param in df_src.columns else None,
        prefer_dates=_prefer_datetime_color_values(color_param),
    )
    work = pd.DataFrame({"intensity": intensity, "y": y}, index=df_src.index)
    work["identifier_1"] = df_src.get("Identifier 1", pd.Series("", index=df_src.index)).fillna("").astype(str)
    work["identifier_2"] = df_src.get("Identifier 2", pd.Series("", index=df_src.index)).fillna("").astype(str)
    if color_values is not None and len(color_values) == len(work):
        work["color"] = pd.to_numeric(color_values, errors="coerce")
    else:
        work["color"] = np.nan
    work["__hover_species"] = _resolve_species_labels(df_src).reindex(work.index)
    work["__hover_color_value"] = df_src.get(color_param, pd.Series(index=df_src.index, dtype=object)).reindex(work.index).map(_format_hover_color_value)
    slope = pd.to_numeric(pd.Series([fit.get("slope")]), errors="coerce").iloc[0]
    intercept = pd.to_numeric(pd.Series([fit.get("intercept")]), errors="coerce").iloc[0]
    quad = pd.to_numeric(pd.Series([fit.get("quad")]), errors="coerce").iloc[0]
    fit_degree_raw = pd.to_numeric(pd.Series([fit.get("degree")]), errors="coerce").iloc[0]
    fit_degree = int(fit_degree_raw) if np.isfinite(fit_degree_raw) and int(fit_degree_raw) >= 2 else 1
    if fit_degree >= 2:
        work["x"] = np.square(work["intensity"])
        xaxis_title = f"Selected Coefficient Axis (I^2) - {intensity_col}"
    else:
        work["x"] = work["intensity"]
        xaxis_title = f"Selected Coefficient Axis (I) - {intensity_col}"
    x_ref = pd.to_numeric(pd.Series([fit.get("x_ref")]), errors="coerce").iloc[0]
    if corrected and np.isfinite(slope) and np.isfinite(x_ref):
        delta = _linearity_correction_delta(work["intensity"], fit)
        work["y"] = (work["y"] - delta).where(np.isfinite(work["intensity"]) & np.isfinite(work["y"]) & np.isfinite(delta))
    work = work[np.isfinite(work["x"]) & np.isfinite(work["intensity"]) & np.isfinite(work["y"])].copy()
    fig = go.Figure()
    if not work.empty:
        marker_kwargs: dict[str, Any] = {"size": 8}
        if work["color"].notna().any():
            marker_kwargs.update(color=work["color"], colorscale="Viridis", showscale=False)
        else:
            marker_kwargs.update(color="#2563eb")
        isotope_key = "d13C" if "13" in str(y_col) else "d18O"
        color_label = _color_param_label(color_param)
        customdata = np.column_stack(
            (
                work.index.astype(str).to_numpy(),
                np.full(len(work), isotope_key, dtype=object),
                work["identifier_1"].to_numpy(),
                work["identifier_2"].to_numpy(),
                work["__hover_species"].fillna("Unknown").astype(str).to_numpy(),
                work["__hover_color_value"].fillna("N/A").astype(str).to_numpy(),
                work["intensity"].to_numpy(),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=work["x"],
                y=work["y"],
                mode="markers",
                name="Standards",
                marker=marker_kwargs,
                customdata=customdata,
                hovertemplate=(
                    "Identifier 1: %{customdata[2]}<br>"
                    "Identifier 2: %{customdata[3]}<br>"
                    "Species: %{customdata[4]}<br>"
                    "Row: %{customdata[0]}<br>"
                    f"{color_label}: %{{customdata[5]}}<br>"
                    "Axis value: %{x:.3f}<br>"
                    "Intensity: %{customdata[6]:.3f}<br>"
                    "Value: %{y:.3f}<extra></extra>"
                ),
            )
        )
    eq_text = "Insufficient data for regression"
    if not corrected and int(fit.get("n", 0) or 0) >= 2 and np.isfinite(slope) and np.isfinite(intercept) and not work.empty:
        intensity_grid = np.linspace(float(work["intensity"].min()), float(work["intensity"].max()), 100)
        xs = np.square(intensity_grid) if fit_degree >= 2 else intensity_grid
        if fit_degree >= 2 and np.isfinite(quad):
            ys = float(intercept) + float(slope) * intensity_grid + float(quad) * np.square(intensity_grid)
        else:
            ys = float(intercept) + float(slope) * intensity_grid
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name="Fit", line=dict(color="#f59e0b")))
        r2 = pd.to_numeric(pd.Series([fit.get("r2")]), errors="coerce").iloc[0]
        if fit_degree >= 2 and np.isfinite(quad):
            equation = f"y = {float(intercept):.3f} + {float(slope):.6f}*I + {float(quad):.8f}*I^2"
        else:
            equation = f"y = {float(intercept):.3f} + {float(slope):.6f}*I"
        eq_text = f"{equation} | R^2={float(r2):.3f}" if np.isfinite(r2) else equation
    if corrected and np.isfinite(slope) and np.isfinite(x_ref) and not work.empty:
        corr_fit = _compute_linearity_fit(
            pd.DataFrame({"x": work["intensity"], "y": work["y"]}),
            "y",
            "x",
            quadratic=fit_degree >= 2,
        )
        corr_slope = pd.to_numeric(pd.Series([corr_fit.get("slope")]), errors="coerce").iloc[0]
        corr_intercept = pd.to_numeric(pd.Series([corr_fit.get("intercept")]), errors="coerce").iloc[0]
        corr_quad = pd.to_numeric(pd.Series([corr_fit.get("quad")]), errors="coerce").iloc[0]
        corr_degree_raw = pd.to_numeric(pd.Series([corr_fit.get("degree")]), errors="coerce").iloc[0]
        corr_degree = int(corr_degree_raw) if np.isfinite(corr_degree_raw) and int(corr_degree_raw) >= 2 else 1
        corr_r2 = pd.to_numeric(pd.Series([corr_fit.get("r2")]), errors="coerce").iloc[0]
        if int(corr_fit.get("n", 0) or 0) >= 2 and np.isfinite(corr_slope) and np.isfinite(corr_intercept):
            intensity_grid = np.linspace(float(work["intensity"].min()), float(work["intensity"].max()), 100)
            xs = np.square(intensity_grid) if fit_degree >= 2 else intensity_grid
            if corr_degree >= 2 and np.isfinite(corr_quad):
                ys = float(corr_intercept) + float(corr_slope) * intensity_grid + float(corr_quad) * np.square(intensity_grid)
                equation = f"y = {float(corr_intercept):.3f} + {float(corr_slope):.6f}*I + {float(corr_quad):.8f}*I^2"
            else:
                ys = float(corr_intercept) + float(corr_slope) * intensity_grid
                equation = f"y = {float(corr_intercept):.3f} + {float(corr_slope):.6f}*I"
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name="Post-correction Fit", line=dict(color="#16a34a", dash="dash")))
            eq_text = f"{equation} | R^2={float(corr_r2):.3f}" if np.isfinite(corr_r2) else equation
    fig.update_layout(
        title=f"{y_col} vs Selected Coefficient Axis{' (Corrected)' if corrected else ''}",
        xaxis_title=xaxis_title,
        yaxis_title=f"{y_col}{' corrected' if corrected else ''}",
        annotations=[
            dict(
                x=0.02,
                y=0.98,
                xref="paper",
                yref="paper",
                text=eq_text,
                showarrow=False,
                bgcolor="white",
                bordercolor="black",
                borderwidth=1,
                font=dict(size=12),
            )
        ],
        height=520,
    )
    return _figure_json(fig)


def _compute_precision_summary(
    standard: str,
    std_df: pd.DataFrame,
    config: CalibrationConfig,
    fits: dict[str, Any],
    outlier_reference_df: pd.DataFrame | None = None,
) -> CalibrationPrecisionSummary:
    if std_df is None or std_df.empty:
        return CalibrationPrecisionSummary(standard=standard)
    out13, out18 = _standard_outlier_masks(std_df, config, outlier_reference_df=outlier_reference_df)
    if config.independent_isotope_outliers:
        clean_d13 = std_df.loc[~out13].copy()
        clean_d18 = std_df.loc[~out18].copy()
    else:
        combined = out13 | out18
        clean_d13 = std_df.loc[~combined].copy()
        clean_d18 = std_df.loc[~combined].copy()
    total_rows = int(len(std_df))
    included_d13 = int(len(clean_d13))
    included_d18 = int(len(clean_d18))
    intensity_col = _resolve_linearity_intensity_column_for_fits(
        fits=fits,
        df=std_df,
        use_diff_intensity=config.linearity.use_diff_intensity,
    )
    d13_lin = None
    d18_lin = None
    d13_corrected_series: pd.Series | None = None
    d18_corrected_series: pd.Series | None = None
    fit13 = fits.get("d13C", {}) if isinstance(fits, dict) else {}
    fit18 = fits.get("d18O", {}) if isinstance(fits, dict) else {}
    if intensity_col in clean_d13.columns:
        x = pd.to_numeric(clean_d13[intensity_col], errors="coerce")
        y = pd.to_numeric(clean_d13["d 13C/12C  Mean"], errors="coerce")
        delta = _linearity_correction_delta(x, fit13)
        d13_corrected_series = (y - delta).where(np.isfinite(x) & np.isfinite(y) & np.isfinite(delta))
        if d13_corrected_series.notna().any():
            d13_lin = float(d13_corrected_series.std())
    if intensity_col in clean_d18.columns:
        x = pd.to_numeric(clean_d18[intensity_col], errors="coerce")
        y = pd.to_numeric(clean_d18["d 18O/16O  Mean"], errors="coerce")
        delta = _linearity_correction_delta(x, fit18)
        d18_corrected_series = (y - delta).where(np.isfinite(x) & np.isfinite(y) & np.isfinite(delta))
        if d18_corrected_series.notna().any():
            d18_lin = float(d18_corrected_series.std())

    line_precisions: dict[str, dict[str, float | None]] = {}
    line_col = _find_column(std_df, "Line")
    if line_col:
        line_values_d13 = pd.to_numeric(clean_d13[line_col], errors="coerce")
        line_values_d18 = pd.to_numeric(clean_d18[line_col], errors="coerce")

        def _std_or_none(series: pd.Series) -> float | None:
            numeric = pd.to_numeric(series, errors="coerce")
            if not numeric.notna().any():
                return None
            return float(numeric.std())

        line_values = sorted(
            {
                int(value)
                for value in pd.to_numeric(std_df[line_col], errors="coerce").dropna().tolist()
                if np.isfinite(value)
            }
        )
        for line_value in line_values:
            d13_mask = line_values_d13 == line_value
            d18_mask = line_values_d18 == line_value
            line_precisions[str(line_value)] = {
                "d13_precision": _std_or_none(clean_d13.loc[d13_mask, "d 13C/12C  Mean"]),
                "d18_precision": _std_or_none(clean_d18.loc[d18_mask, "d 18O/16O  Mean"]),
                "d13_linearity_corrected_precision": _std_or_none(d13_corrected_series.where(d13_mask)) if d13_corrected_series is not None else None,
                "d18_linearity_corrected_precision": _std_or_none(d18_corrected_series.where(d18_mask)) if d18_corrected_series is not None else None,
            }

    return CalibrationPrecisionSummary(
        standard=standard,
        total_rows=total_rows,
        included_d13=included_d13,
        included_d18=included_d18,
        included_pct_d13=(included_d13 / total_rows * 100.0) if total_rows else 0.0,
        included_pct_d18=(included_d18 / total_rows * 100.0) if total_rows else 0.0,
        d13_precision=pd.to_numeric(clean_d13["d 13C/12C  Mean"], errors="coerce").std(),
        d18_precision=pd.to_numeric(clean_d18["d 18O/16O  Mean"], errors="coerce").std(),
        d13_average=pd.to_numeric(clean_d13["d 13C/12C  Mean"], errors="coerce").mean(),
        d18_average=pd.to_numeric(clean_d18["d 18O/16O  Mean"], errors="coerce").mean(),
        d13_linearity_corrected_precision=d13_lin,
        d18_linearity_corrected_precision=d18_lin,
        line_precisions=to_json_compatible(line_precisions),
    )


def build_calibration_workspace(
    session_id: str,
    df: pd.DataFrame,
    metadata: dict[str, Any],
    config_override: CalibrationConfig | dict[str, Any] | None = None,
) -> CalibrationWorkspace:
    work_df = _ensure_cycle1_signal_difference_columns(df.copy())
    calibration_meta = metadata.get("calibration", {})
    config_payload: dict[str, Any] | CalibrationConfig
    if config_override is None:
        config_payload = calibration_meta.get("config", {})
    elif isinstance(config_override, CalibrationConfig):
        config_payload = config_override
    else:
        config_payload = dict(config_override)
    config = normalize_calibration_config(
        config_payload.model_dump() if isinstance(config_payload, CalibrationConfig) else config_payload
    )
    work_df = _apply_isotope_line_offsets(
        work_df,
        line_1_offset_d13=getattr(config.linearity, "line_1_offset_d13", None),
        line_1_offset_d18=getattr(config.linearity, "line_1_offset_d18", None),
        line_2_offset_d13=getattr(config.linearity, "line_2_offset_d13", None),
        line_2_offset_d18=getattr(config.linearity, "line_2_offset_d18", None),
    )
    standards_repo = StandardsRepository.default()
    standards_reference = standards_repo.frame
    selected_standards = list(config.selected_standards)
    selected_standard_official_values = [
        CalibrationOfficialValue.model_validate(item)
        for item in standards_repo.official_values_for_standards(selected_standards)
    ]
    all_identifier_labels = (
        sorted(
            {
                str(value).strip()
                for value in work_df.get("Identifier 1", pd.Series(dtype=object)).dropna().tolist()
                if str(value).strip() != ""
            }
        )
        if "Identifier 1" in work_df.columns
        else []
    )
    override_scope = all_identifier_labels if all_identifier_labels else selected_standards
    selected_linearity_intensity_col = _resolve_selected_linearity_intensity_column(
        df=work_df,
        use_diff_intensity=config.linearity.use_diff_intensity,
        selected_intensity_col=getattr(config.linearity, "intensity_col", None),
    )
    max_sample_intensity = (
        getattr(config.linearity, "max_sample_intensity", None)
        if selected_linearity_intensity_col == CYCLE1_SIGNAL_SAMP44_COL
        else None
    )
    line_adjusted_df, d13_offset_intensity_col, d18_offset_intensity_col = _with_isotope_linearity_intensity_columns(
        work_df,
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
    standards_for_outliers_df = standards_adjusted_df
    outlier_reference_df = standards_adjusted_df
    if bool(config.linearity.apply) and not standards_adjusted_df.empty and "Identifier 1" in standards_adjusted_df.columns:
        selected_mask = standards_adjusted_df["Identifier 1"].astype(str).isin({str(item) for item in selected_standards})
        fit_input = standards_adjusted_df.loc[selected_mask].copy() if bool(selected_mask.any()) else pd.DataFrame()
        if not fit_input.empty:
            intensity_col = _resolve_selected_linearity_intensity_column(
                df=fit_input,
                use_diff_intensity=config.linearity.use_diff_intensity,
                selected_intensity_col=selected_linearity_intensity_col,
            )
            pre_outlier_d13_col = d13_offset_intensity_col if d13_offset_intensity_col in fit_input.columns else intensity_col
            pre_outlier_d18_col = d18_offset_intensity_col if d18_offset_intensity_col in fit_input.columns else intensity_col
            pre_outlier_fit13 = _compute_linearity_fit(
                _filter_linearity_fit_input_by_max_intensity(
                    fit_input,
                    pre_outlier_d13_col,
                    max_sample_intensity,
                ),
                "d 13C/12C  Mean",
                pre_outlier_d13_col,
                quadratic=bool(config.linearity.quadratic),
            )
            pre_outlier_fit18 = _compute_linearity_fit(
                _filter_linearity_fit_input_by_max_intensity(
                    fit_input,
                    pre_outlier_d18_col,
                    max_sample_intensity,
                ),
                "d 18O/16O  Mean",
                pre_outlier_d18_col,
                quadratic=bool(config.linearity.quadratic),
            )
            pre_outlier_fits: dict[str, Any] = {
                "d13C": pre_outlier_fit13,
                "d18O": pre_outlier_fit18,
                "intensity_col": intensity_col,
                "d13_intensity_col": d13_offset_intensity_col,
                "d18_intensity_col": d18_offset_intensity_col,
            }
            corrected_for_outliers = _apply_linearity_correction(
                standards_adjusted_df,
                intensity_col,
                pre_outlier_fits,
            )
            outlier_reference_df = _promote_linearity_corrected_raw_columns(corrected_for_outliers)
    available_color_params = _candidate_color_columns(work_df)
    if available_color_params and config.color_param not in available_color_params:
        config.color_param = "Date" if "Date" in available_color_params else available_color_params[0]
    min_date, max_date, _ = _date_bounds(work_df)
    available_values = CalibrationAvailableValues(
        standards=standards_repo.standards_list(),
        color_params=available_color_params,
        z_axis_options=_candidate_z_columns(work_df),
        min_date=min_date,
        max_date=max_date,
    )

    if not selected_standards:
        return CalibrationWorkspace(
            session_id=session_id,
            config=config,
            available_values=available_values,
            selected_standard_official_values=selected_standard_official_values,
            linearity_fits=to_json_compatible(calibration_meta.get("linearity_fits", {})),
        )

    clean_stds = _filter_standards_remove_outliers(
        standards_for_outliers_df,
        selected_standards,
        config.calibration_type,
        config.sigma_level,
        config.iqr_multiplier,
        config.independent_isotope_outliers,
        outlier_reference_df=outlier_reference_df,
    )
    chart_src = _apply_precision_date_range(clean_stds, config) if clean_stds is not None and not clean_stds.empty else pd.DataFrame(columns=work_df.columns)
    main_figures: dict[str, dict[str, Any]] = {}
    if chart_src is not None and not chart_src.empty:
        calibration_figs = create_calibration_plots(standards_reference, chart_src, selected_standards, config.color_param)
        for key, value in calibration_figs.items():
            main_figures[key] = _figure_json(value)
        fig_3d, _ = _build_isotope_3d_scatter(
            chart_src,
            z_col=config.z_axis,
            z_label=config.z_axis,
            color_col=config.color_param,
            color_label=config.color_param,
            title=f"Calibration 3D Chart (Z-axis: {config.z_axis})",
            include_row_metadata=True,
            isotope_key="cross",
        )
        main_figures["calibration_3d"] = _figure_json(fig_3d)
        main_figures["crossplot"] = _build_calibration_crossplot(chart_src, config.color_param)

    linearity_src = chart_src if chart_src is not None and not chart_src.empty else clean_stds
    calculation_fits: dict[str, Any] = {}
    display_fit13: dict[str, Any] = {}
    display_fit18: dict[str, Any] = {}
    linearity_figures: dict[str, dict[str, Any]] = {}
    if linearity_src is not None and not linearity_src.empty:
        calculation_intensity_col = _resolve_selected_linearity_intensity_column(
            df=linearity_src,
            use_diff_intensity=config.linearity.use_diff_intensity,
            selected_intensity_col=selected_linearity_intensity_col,
        )
        calculation_d13_intensity_col = (
            d13_offset_intensity_col if d13_offset_intensity_col in linearity_src.columns else calculation_intensity_col
        )
        calculation_d18_intensity_col = (
            d18_offset_intensity_col if d18_offset_intensity_col in linearity_src.columns else calculation_intensity_col
        )
        calculation_fit13 = _compute_linearity_fit(
            _filter_linearity_fit_input_by_max_intensity(
                linearity_src,
                calculation_d13_intensity_col,
                max_sample_intensity,
            ),
            "d 13C/12C  Mean",
            calculation_d13_intensity_col,
            quadratic=bool(config.linearity.quadratic),
        )
        calculation_fit18 = _compute_linearity_fit(
            _filter_linearity_fit_input_by_max_intensity(
                linearity_src,
                calculation_d18_intensity_col,
                max_sample_intensity,
            ),
            "d 18O/16O  Mean",
            calculation_d18_intensity_col,
            quadratic=bool(config.linearity.quadratic),
        )
        calculation_fits = {
            "d13C": calculation_fit13,
            "d18O": calculation_fit18,
            "intensity_col": calculation_intensity_col,
            "d13_intensity_col": calculation_d13_intensity_col,
            "d18_intensity_col": calculation_d18_intensity_col,
        }

        display_intensity_col = _resolve_selected_linearity_intensity_column(
            df=linearity_src,
            use_diff_intensity=config.linearity.use_diff_intensity,
            selected_intensity_col=selected_linearity_intensity_col,
        )
        display_d13_intensity_col = (
            d13_offset_intensity_col if d13_offset_intensity_col in linearity_src.columns else display_intensity_col
        )
        display_d18_intensity_col = (
            d18_offset_intensity_col if d18_offset_intensity_col in linearity_src.columns else display_intensity_col
        )
        display_fit13 = _compute_linearity_fit(
            _filter_linearity_fit_input_by_max_intensity(
                linearity_src,
                display_d13_intensity_col,
                max_sample_intensity,
            ),
            "d 13C/12C  Mean",
            display_d13_intensity_col,
            quadratic=bool(config.linearity.quadratic),
        )
        display_fit18 = _compute_linearity_fit(
            _filter_linearity_fit_input_by_max_intensity(
                linearity_src,
                display_d18_intensity_col,
                max_sample_intensity,
            ),
            "d 18O/16O  Mean",
            display_d18_intensity_col,
            quadratic=bool(config.linearity.quadratic),
        )
        display_fits = {
            "d13C": display_fit13,
            "d18O": display_fit18,
            "intensity_col": display_intensity_col,
            "d13_intensity_col": display_d13_intensity_col,
            "d18_intensity_col": display_d18_intensity_col,
        }
        linearity_figures = {
            "d13_raw": _build_linearity_figure(
                linearity_src,
                "d 13C/12C  Mean",
                display_fit13,
                display_d13_intensity_col,
                config.color_param,
                corrected=False,
            ),
            "d13_corrected": _build_linearity_figure(
                linearity_src,
                "d 13C/12C  Mean",
                display_fit13,
                display_d13_intensity_col,
                config.color_param,
                corrected=True,
            ),
            "d18_raw": _build_linearity_figure(
                linearity_src,
                "d 18O/16O  Mean",
                display_fit18,
                display_d18_intensity_col,
                config.color_param,
                corrected=False,
            ),
            "d18_corrected": _build_linearity_figure(
                linearity_src,
                "d 18O/16O  Mean",
                display_fit18,
                display_d18_intensity_col,
                config.color_param,
                corrected=True,
            ),
        }
    else:
        display_fits = {}
        calculation_fits = {}

    if bool(config.linearity.apply) and chart_src is not None and not chart_src.empty and calculation_fits:
        correction_intensity_col = str(calculation_fits.get("intensity_col") or selected_linearity_intensity_col)
        corrected_chart_src = _promote_linearity_corrected_raw_columns(
            _apply_linearity_correction(chart_src, correction_intensity_col, calculation_fits)
        )
        corrected_calibration_figs = create_calibration_plots(
            standards_reference,
            corrected_chart_src,
            selected_standards,
            config.color_param,
        )
        for isotope_key in (ISOTYPE_D13C, ISOTYPE_D18O):
            fig = corrected_calibration_figs.get(isotope_key)
            if fig is not None:
                main_figures[isotope_key] = _figure_json(fig)

    precision_summaries: list[CalibrationPrecisionSummary] = []
    standard_sections: list[CalibrationStandardSection] = []
    for standard in selected_standards:
        std_df = standards_for_outliers_df[standards_for_outliers_df["Identifier 1"].astype(str) == str(standard)].copy()
        std_df = _apply_precision_date_range(std_df, config)
        std_outlier_ref_df = outlier_reference_df[
            outlier_reference_df["Identifier 1"].astype(str) == str(standard)
        ].copy()
        std_outlier_ref_df = _apply_precision_date_range(std_outlier_ref_df, config)
        std_display_df = std_outlier_ref_df if bool(config.linearity.apply) else std_df
        precision_summaries.append(
            _compute_precision_summary(
                standard,
                std_df,
                config,
                calculation_fits,
                outlier_reference_df=std_outlier_ref_df,
            )
        )
        out13, out18 = _standard_outlier_masks(std_df, config, outlier_reference_df=std_outlier_ref_df)
        true_d13 = standards_repo.get_true_value(standard, ISOTYPE_D13C) if standard in standards_repo.standards_list() else None
        true_d18 = standards_repo.get_true_value(standard, ISOTYPE_D18O) if standard in standards_repo.standards_list() else None
        standard_sections.append(
            CalibrationStandardSection(
                standard=standard,
                d13_outliers=_outlier_rows(std_display_df, out13, "d 13C/12C  Mean"),
                d18_outliers=_outlier_rows(std_display_df, out18, "d 18O/16O  Mean"),
                d13_figure=_build_standard_outlier_figure(
                    std_display_df,
                    out13,
                    "d 13C/12C  Mean",
                    "d13C",
                    f"{standard} d13C Calibration Values ({config.calibration_type} Method)",
                    config.color_param,
                    true_d13,
                ),
                d18_figure=_build_standard_outlier_figure(
                    std_display_df,
                    out18,
                    "d 18O/16O  Mean",
                    "d18O",
                    f"{standard} d18O Calibration Values ({config.calibration_type} Method)",
                    config.color_param,
                    true_d18,
                ),
            )
        )

    stored_fits = calibration_meta.get("linearity_fits", {}) or calculation_fits
    return CalibrationWorkspace(
        session_id=session_id,
        config=config,
        available_values=available_values,
        figures=main_figures,
        linearity_figures=linearity_figures,
        precision_summaries=precision_summaries,
        standard_sections=standard_sections,
        selected_standard_official_values=selected_standard_official_values,
        linearity_fits=to_json_compatible(stored_fits),
    )
