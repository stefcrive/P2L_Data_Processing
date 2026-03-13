from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

try:
    import plotly.graph_objects as go
except ModuleNotFoundError:  # pragma: no cover
    go = None

from ..constants import CYCLE1_SIGNAL_DIFF44_COL, CYCLE1_SIGNAL_SAMP44_COL, ISOTYPE_D13C, ISOTYPE_D18O
from ..contracts import (
    CalibrationAvailableValues,
    CalibrationConfig,
    CalibrationPrecisionSummary,
    CalibrationStandardSection,
    CalibrationWorkspace,
)
from ..shared.dataframe import _ensure_cycle1_signal_difference_columns, _find_column, _parse_numeric_token
from ..shared.json_compat import to_json_compatible
from ..shared.plotting import _build_date_colorbar_ticks, _build_isotope_3d_scatter, _prepare_color_values
from ..standards import StandardsRepository
from .core import (
    _compute_linearity_fit,
    _filter_standards_remove_outliers,
    _linearity_intensity_axis_label,
    _resolve_linearity_intensity_column_for_fits,
    _resolve_selected_linearity_intensity_column,
    create_calibration_plots,
    identify_outliers,
    identify_outliers_iqr,
)


def normalize_calibration_config(raw: dict[str, Any] | None) -> CalibrationConfig:
    payload = dict(raw or {})
    return CalibrationConfig.model_validate(payload)


def _figure_json(fig: go.Figure | None) -> dict[str, Any]:
    return to_json_compatible(fig.to_plotly_json()) if fig is not None else {}


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
        CYCLE1_SIGNAL_DIFF44_COL,
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


def _standard_outlier_masks(std_df: pd.DataFrame, config: CalibrationConfig) -> tuple[pd.Series, pd.Series]:
    if std_df is None or std_df.empty:
        empty = pd.Series(dtype=bool)
        return empty, empty
    if config.calibration_type == "Z-Score":
        out13 = identify_outliers(std_df, "d 13C/12C  Mean", config.sigma_level)
        out18 = identify_outliers(std_df, "d 18O/16O  Mean", config.sigma_level)
    else:
        out13 = identify_outliers_iqr(std_df, "d 13C/12C  Mean", config.iqr_multiplier)
        out18 = identify_outliers_iqr(std_df, "d 18O/16O  Mean", config.iqr_multiplier)
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
    work = std_df.copy()
    work["x_axis"] = _sequence_axis(work)
    values = pd.to_numeric(work[value_col], errors="coerce")
    inlier_mask = ~outlier_mask.reindex(work.index, fill_value=False)
    fig = go.Figure()
    color_values, colorbar_category_ticks = _prepare_color_values(work[color_param] if color_param in work.columns else None)
    color_numeric = pd.to_numeric(color_values, errors="coerce") if color_values is not None else pd.Series(np.nan, index=work.index)
    has_color = bool(color_numeric.notna().any())
    coloraxis_cfg: dict[str, Any] = {
        "colorscale": "Viridis",
        "colorbar": {
            "title": {
                "text": "Date" if color_param == "Date_ordinal" else color_param,
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
        if color_param == "Date_ordinal" and color_param in work.columns:
            tickvals, ticktext = _build_date_colorbar_ticks(work[color_param])
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
    x = pd.to_numeric(df_src[intensity_col], errors="coerce")
    y = pd.to_numeric(df_src[y_col], errors="coerce")
    color_values, _ = _prepare_color_values(df_src[color_param] if color_param in df_src.columns else None)
    work = pd.DataFrame({"x": x, "y": y}, index=df_src.index)
    if color_values is not None and len(color_values) == len(work):
        work["color"] = pd.to_numeric(color_values, errors="coerce")
    else:
        work["color"] = np.nan
    slope = pd.to_numeric(pd.Series([fit.get("slope")]), errors="coerce").iloc[0]
    intercept = pd.to_numeric(pd.Series([fit.get("intercept")]), errors="coerce").iloc[0]
    x_ref = pd.to_numeric(pd.Series([fit.get("x_ref")]), errors="coerce").iloc[0]
    if corrected and np.isfinite(slope) and np.isfinite(x_ref):
        work["y"] = (work["y"] - float(slope) * (work["x"] - float(x_ref))).where(
            np.isfinite(work["x"]) & np.isfinite(work["y"])
        )
    work = work[np.isfinite(work["x"]) & np.isfinite(work["y"])].copy()
    fig = go.Figure()
    if not work.empty:
        marker_kwargs: dict[str, Any] = {"size": 8}
        if work["color"].notna().any():
            marker_kwargs.update(color=work["color"], colorscale="Viridis", showscale=False)
        else:
            marker_kwargs.update(color="#2563eb")
        fig.add_trace(go.Scatter(x=work["x"], y=work["y"], mode="markers", name="Standards", marker=marker_kwargs))
    eq_text = "Insufficient data for regression"
    if not corrected and int(fit.get("n", 0) or 0) >= 2 and np.isfinite(slope) and np.isfinite(intercept) and not work.empty:
        xs = np.linspace(float(work["x"].min()), float(work["x"].max()), 100)
        ys = float(intercept) + float(slope) * xs
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name="Fit", line=dict(color="#f59e0b")))
        r2 = pd.to_numeric(pd.Series([fit.get("r2")]), errors="coerce").iloc[0]
        eq_text = f"y = {float(intercept):.3f} + {float(slope):.6f}*I | R^2={float(r2):.3f}" if np.isfinite(r2) else f"y = {float(intercept):.3f} + {float(slope):.6f}*I"
    if corrected and np.isfinite(slope) and np.isfinite(x_ref) and not work.empty:
        corr_fit = _compute_linearity_fit(pd.DataFrame({"x": work["x"], "y": work["y"]}), "y", "x")
        corr_slope = pd.to_numeric(pd.Series([corr_fit.get("slope")]), errors="coerce").iloc[0]
        corr_intercept = pd.to_numeric(pd.Series([corr_fit.get("intercept")]), errors="coerce").iloc[0]
        corr_r2 = pd.to_numeric(pd.Series([corr_fit.get("r2")]), errors="coerce").iloc[0]
        if int(corr_fit.get("n", 0) or 0) >= 2 and np.isfinite(corr_slope) and np.isfinite(corr_intercept):
            xs = np.linspace(float(work["x"].min()), float(work["x"].max()), 100)
            ys = float(corr_intercept) + float(corr_slope) * xs
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name="Post-correction Fit", line=dict(color="#16a34a", dash="dash")))
            eq_text = f"y = {float(corr_intercept):.3f} + {float(corr_slope):.6f}*I | R^2={float(corr_r2):.3f}" if np.isfinite(corr_r2) else eq_text
    fig.update_layout(
        title=f"{y_col} vs Intensity{' (Corrected)' if corrected else ''}",
        xaxis_title=_linearity_intensity_axis_label(intensity_col),
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
) -> CalibrationPrecisionSummary:
    if std_df is None or std_df.empty:
        return CalibrationPrecisionSummary(standard=standard)
    out13, out18 = _standard_outlier_masks(std_df, config)
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
    fit13 = fits.get("d13C", {}) if isinstance(fits, dict) else {}
    fit18 = fits.get("d18O", {}) if isinstance(fits, dict) else {}
    if intensity_col in clean_d13.columns and np.isfinite(pd.to_numeric(pd.Series([fit13.get("slope")]), errors="coerce").iloc[0]):
        x = pd.to_numeric(clean_d13[intensity_col], errors="coerce")
        y = pd.to_numeric(clean_d13["d 13C/12C  Mean"], errors="coerce")
        slope = float(fit13["slope"])
        x_ref = float(fit13["x_ref"])
        d13_lin = float((y - slope * (x - x_ref)).where(np.isfinite(x) & np.isfinite(y)).std())
    if intensity_col in clean_d18.columns and np.isfinite(pd.to_numeric(pd.Series([fit18.get("slope")]), errors="coerce").iloc[0]):
        x = pd.to_numeric(clean_d18[intensity_col], errors="coerce")
        y = pd.to_numeric(clean_d18["d 18O/16O  Mean"], errors="coerce")
        slope = float(fit18["slope"])
        x_ref = float(fit18["x_ref"])
        d18_lin = float((y - slope * (x - x_ref)).where(np.isfinite(x) & np.isfinite(y)).std())

    line_precisions: dict[str, dict[str, float | None]] = {}
    line_col = _find_column(std_df, "Line")
    if line_col:
        line_values = sorted(
            {
                int(value)
                for value in pd.to_numeric(std_df[line_col], errors="coerce").dropna().tolist()
                if np.isfinite(value)
            }
        )
        for line_value in line_values:
            line_precisions[str(line_value)] = {
                "d13_precision": pd.to_numeric(clean_d13.loc[pd.to_numeric(clean_d13[line_col], errors="coerce") == line_value, "d 13C/12C  Mean"], errors="coerce").std(),
                "d18_precision": pd.to_numeric(clean_d18.loc[pd.to_numeric(clean_d18[line_col], errors="coerce") == line_value, "d 18O/16O  Mean"], errors="coerce").std(),
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
    standards_repo = StandardsRepository.default()
    standards_reference = standards_repo.frame
    selected_standards = list(config.selected_standards)
    min_date, max_date, _ = _date_bounds(work_df)
    available_values = CalibrationAvailableValues(
        standards=standards_repo.standards_list(),
        color_params=_candidate_color_columns(work_df),
        z_axis_options=_candidate_z_columns(work_df),
        min_date=min_date,
        max_date=max_date,
    )

    if not selected_standards:
        return CalibrationWorkspace(
            session_id=session_id,
            config=config,
            available_values=available_values,
            linearity_fits=to_json_compatible(calibration_meta.get("linearity_fits", {})),
        )

    clean_stds = _filter_standards_remove_outliers(
        work_df,
        selected_standards,
        config.calibration_type,
        config.sigma_level,
        config.iqr_multiplier,
        config.independent_isotope_outliers,
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
        )
        main_figures["calibration_3d"] = _figure_json(fig_3d)

    linearity_src = chart_src if chart_src is not None and not chart_src.empty else clean_stds
    display_fit13: dict[str, Any] = {}
    display_fit18: dict[str, Any] = {}
    linearity_figures: dict[str, dict[str, Any]] = {}
    if linearity_src is not None and not linearity_src.empty:
        intensity_col = _resolve_selected_linearity_intensity_column(df=linearity_src, use_diff_intensity=config.linearity.use_diff_intensity)
        display_fit13 = _compute_linearity_fit(linearity_src, "d 13C/12C  Mean", intensity_col)
        display_fit18 = _compute_linearity_fit(linearity_src, "d 18O/16O  Mean", intensity_col)
        display_fits = {"d13C": display_fit13, "d18O": display_fit18, "intensity_col": intensity_col}
        linearity_figures = {
            "d13_raw": _build_linearity_figure(linearity_src, "d 13C/12C  Mean", display_fit13, intensity_col, config.color_param, corrected=False),
            "d13_corrected": _build_linearity_figure(linearity_src, "d 13C/12C  Mean", display_fit13, intensity_col, config.color_param, corrected=True),
            "d18_raw": _build_linearity_figure(linearity_src, "d 18O/16O  Mean", display_fit18, intensity_col, config.color_param, corrected=False),
            "d18_corrected": _build_linearity_figure(linearity_src, "d 18O/16O  Mean", display_fit18, intensity_col, config.color_param, corrected=True),
        }
    else:
        display_fits = {}

    precision_summaries: list[CalibrationPrecisionSummary] = []
    standard_sections: list[CalibrationStandardSection] = []
    for standard in selected_standards:
        std_df = work_df[work_df["Identifier 1"].astype(str) == str(standard)].copy()
        std_df = _apply_precision_date_range(std_df, config)
        precision_summaries.append(_compute_precision_summary(standard, std_df, config, display_fits))
        out13, out18 = _standard_outlier_masks(std_df, config)
        true_d13 = standards_repo.get_true_value(standard, ISOTYPE_D13C) if standard in standards_repo.standards_list() else None
        true_d18 = standards_repo.get_true_value(standard, ISOTYPE_D18O) if standard in standards_repo.standards_list() else None
        standard_sections.append(
            CalibrationStandardSection(
                standard=standard,
                d13_outliers=_outlier_rows(std_df, out13, "d 13C/12C  Mean"),
                d18_outliers=_outlier_rows(std_df, out18, "d 18O/16O  Mean"),
                d13_figure=_build_standard_outlier_figure(
                    std_df,
                    out13,
                    "d 13C/12C  Mean",
                    "d13C",
                    f"{standard} d13C Calibration Values ({config.calibration_type} Method)",
                    config.color_param,
                    true_d13,
                ),
                d18_figure=_build_standard_outlier_figure(
                    std_df,
                    out18,
                    "d 18O/16O  Mean",
                    "d18O",
                    f"{standard} d18O Calibration Values ({config.calibration_type} Method)",
                    config.color_param,
                    true_d18,
                ),
            )
        )

    stored_fits = calibration_meta.get("linearity_fits", {}) or display_fits
    return CalibrationWorkspace(
        session_id=session_id,
        config=config,
        available_values=available_values,
        figures=main_figures,
        linearity_figures=linearity_figures,
        precision_summaries=precision_summaries,
        standard_sections=standard_sections,
        linearity_fits=to_json_compatible(stored_fits),
    )
