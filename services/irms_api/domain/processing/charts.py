from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

try:
    import plotly.graph_objects as go
except ModuleNotFoundError:  # pragma: no cover - optional for logic-only tests
    go = None

from ..contracts import IdentifierFigureSet, SpeciesSection
from ..shared.dataframe import _get_species_series, _parse_numeric_token
from ..shared.json_compat import to_json_compatible
from ..shared.plotting import (
    _apply_cycle_std_error_bars,
    _build_date_colorbar_ticks,
    _build_cycle_std_lookups,
    _build_delta_point_customdata,
    _build_isotope_3d_scatter,
    _build_plotly_error_bar_for_df,
    _exclusive_outlier_masks,
    _prepare_color_values,
)
from .outliers import (
    RangeConfig,
    _partial_saturation_isotope_masks,
    _signal_in_range_mask,
    build_category_masks,
    build_outlier_tables,
    compute_statistical_outlier_masks,
)


def _figure_json(fig: go.Figure | None) -> dict[str, Any]:
    return to_json_compatible(fig.to_plotly_json()) if fig is not None else {}


def _scope_df(df: pd.DataFrame, selected_identifier: str) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    if selected_identifier != "All" and "Identifier 1" in df.columns:
        return df[df["Identifier 1"].astype(str) == str(selected_identifier)].copy()
    return df.copy()


def _resolve_species_labels(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=object)
    species = _get_species_series(df)
    if not isinstance(species, pd.Series):
        species = pd.Series(index=df.index, dtype=object)
    species = species.reindex(df.index)
    labels = species.fillna("").astype(str).str.strip()
    labels = labels.where(~labels.str.lower().eq("nan"), "")
    fallback = df.get("Identifier 1", pd.Series(index=df.index, dtype=object)).fillna("").astype(str).str.strip()
    labels = labels.where(labels != "", fallback)
    labels = labels.where(labels != "", "Unknown")
    return labels


def _color_series_for_plot(df: pd.DataFrame, color_col: str) -> tuple[pd.Series, tuple[list[Any], list[str]] | None, bool, float, float]:
    color_values, category_ticks = _prepare_color_values(df.get(color_col))
    if color_values is not None and len(color_values) == len(df):
        numeric_colors = pd.to_numeric(color_values, errors="coerce")
    else:
        numeric_colors = pd.Series(np.nan, index=df.index, dtype=float)
    has_numeric_colors = bool(numeric_colors.notna().any())
    if has_numeric_colors:
        cmin = float(numeric_colors.min())
        cmax = float(numeric_colors.max())
        if not np.isfinite(cmin) or not np.isfinite(cmax):
            cmin, cmax = 0.0, 1.0
        elif cmin == cmax:
            cmax = cmin + 1.0
    else:
        cmin, cmax = 0.0, 1.0
    return numeric_colors, category_ticks, has_numeric_colors, cmin, cmax


def _build_summary_figure(
    df: pd.DataFrame,
    isotope_key: str,
    x_axis_option: str,
    color_col: str,
    show_calibrated: bool = True,
) -> dict[str, Any]:
    if go is None or df is None or df.empty:
        return {}
    y_col = "d 13C/12C  Mean" if isotope_key == "d13C" else "d 18O/16O  Mean"
    cal_col = "d13C_calibrated" if isotope_key == "d13C" else "d18O_calibrated"
    fig = go.Figure()
    work = df.copy()
    work["_species_label"] = _resolve_species_labels(work)
    if x_axis_option == "By Identifier 2":
        work["x_axis"] = work.get("Identifier 2", pd.Series(index=work.index)).apply(_parse_numeric_token)
    else:
        work["x_axis"] = range(len(work))
    color_series, _, has_numeric_colors, color_min, color_max = _color_series_for_plot(work, color_col)
    work["_color_value"] = color_series
    for species, species_df in work.groupby("_species_label", dropna=False):
        plot_df = species_df.sort_values("x_axis", na_position="last")
        marker: dict[str, Any] = dict(
            size=8,
            color=plot_df["_color_value"] if has_numeric_colors else "#2563eb",
            colorscale="Viridis",
            showscale=False,
        )
        if has_numeric_colors:
            marker.update(cmin=color_min, cmax=color_max)
        fig.add_trace(
            go.Scatter(
                x=plot_df["x_axis"],
                y=pd.to_numeric(plot_df.get(y_col), errors="coerce"),
                mode="lines+markers",
                name=f"Raw {isotope_key} - {species}",
                marker=marker,
                customdata=_build_delta_point_customdata(plot_df, isotope_key),
            )
        )
        if show_calibrated and cal_col in plot_df.columns and pd.to_numeric(plot_df[cal_col], errors="coerce").notna().any():
            fig.add_trace(
                go.Scatter(
                    x=plot_df["x_axis"],
                    y=pd.to_numeric(plot_df[cal_col], errors="coerce"),
                    mode="lines",
                    line=dict(color="#f97316", width=2),
                    name=f"Calibrated {isotope_key} - {species}",
                )
            )
    fig.update_layout(
        title=f"{isotope_key} Summary",
        xaxis_title=x_axis_option,
        yaxis_title=isotope_key,
        hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0.0, xanchor="left"),
        margin=dict(l=40, r=20, t=80, b=40),
    )
    return _figure_json(fig)


def _build_overview_outlier_context(
    unfiltered_df: pd.DataFrame,
    config: Any,
    edit_state: dict[str, Any] | None,
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    if unfiltered_df is None or unfiltered_df.empty:
        empty = pd.Series(dtype=bool)
        return {}, {"d13C": empty, "d18O": empty, "any": empty}
    summary_masks = build_category_masks(
        unfiltered_df,
        RangeConfig(
            signal_range=config.signal_range,
            leak_range=config.leak_range,
            d13c_range=config.d13c_range,
            d18o_range=config.d18o_range,
            partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
        ),
        edit_state=edit_state,
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    sat_masks = _partial_saturation_isotope_masks(unfiltered_df)
    return summary_masks, sat_masks


def _numeric_axis_rows(df: pd.DataFrame | None, cols: list[str]) -> pd.DataFrame:
    if df is None or df.empty or any(col not in df.columns for col in cols):
        return pd.DataFrame(columns=cols)
    rows = df[cols].copy()
    for col in cols:
        rows[col] = pd.to_numeric(rows[col], errors="coerce")
    return rows.dropna(subset=cols)


def _overlay_rows_for_mask(overlay_df: pd.DataFrame, mask: pd.Series | None, cols: list[str]) -> pd.DataFrame:
    if overlay_df is None or overlay_df.empty or mask is None:
        return pd.DataFrame(columns=cols)
    keep = mask.reindex(overlay_df.index, fill_value=False).astype(bool)
    if not keep.any():
        return pd.DataFrame(columns=cols)
    return _numeric_axis_rows(overlay_df.loc[keep], cols)


def _visible_overlay_axis_rows(
    overlay_df: pd.DataFrame,
    summary_masks: dict[str, pd.Series],
    sat_masks: dict[str, pd.Series],
    config: Any,
    cols: list[str],
) -> pd.DataFrame:
    if overlay_df is None or overlay_df.empty:
        return pd.DataFrame(columns=cols)
    frames: list[pd.DataFrame] = []

    if getattr(config.overlays, "show_statistical_outliers", False):
        frames.append(_overlay_rows_for_mask(overlay_df, summary_masks.get("Statistical"), cols))

    if getattr(config.overlays, "show_range_outliers", False):
        range_masks = _exclusive_outlier_masks(
            [
                ("signal", summary_masks.get("Signal Intensity", pd.Series(False, index=overlay_df.index))),
                ("leak", summary_masks.get("Leak Rate", pd.Series(False, index=overlay_df.index))),
                ("d13c", summary_masks.get("d13C Range", pd.Series(False, index=overlay_df.index))),
                ("d18o", summary_masks.get("d18O Range", pd.Series(False, index=overlay_df.index))),
            ]
        )
        for mask in range_masks.values():
            frames.append(_overlay_rows_for_mask(overlay_df, mask, cols))

    if getattr(config.overlays, "show_manual_outliers", False):
        frames.append(_overlay_rows_for_mask(overlay_df, summary_masks.get("Manual Override"), cols))

    if getattr(config.overlays, "show_saturated_collectors", True):
        frames.append(_overlay_rows_for_mask(overlay_df, sat_masks.get("any"), cols))

    if getattr(config.overlays, "show_saturated_samples", True):
        frames.append(_overlay_rows_for_mask(overlay_df, summary_masks.get("Fully Saturated Collectors"), cols))

    visible_frames = [frame for frame in frames if not frame.empty]
    if not visible_frames:
        return pd.DataFrame(columns=cols)
    return pd.concat(visible_frames, axis=0, ignore_index=True)


def _axis_range(values: pd.Series, pad_fraction: float = 0.05, default_pad: float = 0.5) -> list[float] | None:
    finite = pd.to_numeric(values, errors="coerce").dropna()
    if finite.empty:
        return None
    min_val = float(finite.min())
    max_val = float(finite.max())
    span = max_val - min_val
    pad = pad_fraction * span if np.isfinite(span) and span > 0 else float(default_pad)
    return [min_val - pad, max_val + pad]


def _add_processing_crossplot_overlays(
    fig: go.Figure,
    overlay_df: pd.DataFrame,
    summary_masks: dict[str, pd.Series],
    sat_masks: dict[str, pd.Series],
    config: Any,
) -> None:
    if fig is None or overlay_df is None or overlay_df.empty:
        return

    x_vals = pd.to_numeric(overlay_df.get("d 18O/16O  Mean"), errors="coerce")
    y_vals = pd.to_numeric(overlay_df.get("d 13C/12C  Mean"), errors="coerce")
    valid_xy = x_vals.notna() & y_vals.notna()
    if not valid_xy.any():
        return

    def _rows(mask: pd.Series | None) -> pd.DataFrame:
        if mask is None:
            return pd.DataFrame(columns=overlay_df.columns)
        m = mask.reindex(overlay_df.index, fill_value=False).astype(bool) & valid_xy
        return overlay_df.loc[m].copy()

    if getattr(config.overlays, "show_statistical_outliers", False):
        statistical_rows = _rows(summary_masks.get("Statistical"))
        if not statistical_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=pd.to_numeric(statistical_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(statistical_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    mode="markers",
                    name="Statistical Outliers",
                    marker=dict(size=12, symbol="square", color="red", line=dict(width=1.5, color="black")),
                    customdata=_build_delta_point_customdata(statistical_rows, "cross"),
                )
            )

    if getattr(config.overlays, "show_range_outliers", False):
        range_masks = _exclusive_outlier_masks(
            [
                ("signal", summary_masks.get("Signal Intensity", pd.Series(False, index=overlay_df.index))),
                ("leak", summary_masks.get("Leak Rate", pd.Series(False, index=overlay_df.index))),
                ("d13c", summary_masks.get("d13C Range", pd.Series(False, index=overlay_df.index))),
                ("d18o", summary_masks.get("d18O Range", pd.Series(False, index=overlay_df.index))),
            ]
        )
        symbol_map = {"signal": "diamond", "leak": "x", "d13c": "cross", "d18o": "square-open"}
        label_map = {
            "signal": "Signal Intensity Range",
            "leak": "Leak Rate Range",
            "d13c": "d13C Range",
            "d18o": "d18O Range",
        }
        for key, mask in range_masks.items():
            rows = _rows(mask)
            if rows.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=pd.to_numeric(rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(rows.get("d 13C/12C  Mean"), errors="coerce"),
                    mode="markers",
                    name=label_map[key],
                    marker=dict(size=12, symbol=symbol_map[key], color="red", line=dict(width=1.5, color="black")),
                    customdata=_build_delta_point_customdata(rows, "cross"),
                )
            )

    if getattr(config.overlays, "show_manual_outliers", False):
        manual_rows = _rows(summary_masks.get("Manual Override"))
        if not manual_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=pd.to_numeric(manual_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(manual_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    mode="markers",
                    name="Manual Outliers",
                    marker=dict(size=13, symbol="circle-open", color="#ec4899", line=dict(width=2, color="black")),
                    customdata=_build_delta_point_customdata(manual_rows, "cross"),
                )
            )

    if getattr(config.overlays, "show_saturated_collectors", True):
        partial_rows = _rows(sat_masks.get("any"))
        if not partial_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=pd.to_numeric(partial_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(partial_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    mode="markers",
                    name="Partially Failed (Recovered Mean)",
                    marker=dict(size=15, symbol="diamond-open", color="#ff7f0e", line=dict(width=2, color="#ff7f0e")),
                    customdata=_build_delta_point_customdata(partial_rows, "cross"),
                )
            )

    if getattr(config.overlays, "show_saturated_samples", True):
        full_rows = _rows(summary_masks.get("Fully Saturated Collectors"))
        if not full_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=pd.to_numeric(full_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(full_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    mode="markers",
                    name="Failed Samples (Fully Saturated)",
                    marker=dict(size=10, symbol="triangle-down", color="#d62728", line=dict(width=1, color="black")),
                    customdata=_build_delta_point_customdata(full_rows, "cross"),
                )
            )


def _add_processing_3d_overlays(
    fig: go.Figure,
    overlay_df: pd.DataFrame,
    summary_masks: dict[str, pd.Series],
    sat_masks: dict[str, pd.Series],
    z_col: str,
    z_label: str,
    config: Any,
) -> None:
    if fig is None or overlay_df is None or overlay_df.empty:
        return

    x_vals = pd.to_numeric(overlay_df.get("d 18O/16O  Mean"), errors="coerce")
    y_vals = pd.to_numeric(overlay_df.get("d 13C/12C  Mean"), errors="coerce")
    z_vals = pd.to_numeric(overlay_df.get(z_col), errors="coerce")
    valid_xyz = x_vals.notna() & y_vals.notna() & z_vals.notna()
    if not valid_xyz.any():
        return

    def _rows(mask: pd.Series | None) -> pd.DataFrame:
        if mask is None:
            return pd.DataFrame(columns=overlay_df.columns)
        m = mask.reindex(overlay_df.index, fill_value=False).astype(bool) & valid_xyz
        return overlay_df.loc[m].copy()

    hover_template = (
        "Identifier 1: %{customdata[2]}<br>"
        "Identifier 2: %{customdata[3]}<br>"
        "d18O: %{x:.4f}<br>"
        "d13C: %{y:.4f}<br>"
        f"{z_label}: %{{z:.4f}}<extra></extra>"
    )

    if getattr(config.overlays, "show_statistical_outliers", False):
        statistical_rows = _rows(summary_masks.get("Statistical"))
        if not statistical_rows.empty:
            fig.add_trace(
                go.Scatter3d(
                    x=pd.to_numeric(statistical_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(statistical_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    z=pd.to_numeric(statistical_rows.get(z_col), errors="coerce"),
                    mode="markers",
                    name="Statistical Outliers",
                    marker=dict(size=7, symbol="square", color="red", line=dict(width=1.5, color="black"), opacity=0.95),
                    customdata=_build_delta_point_customdata(statistical_rows, "cross"),
                    hovertemplate=hover_template,
                )
            )

    if getattr(config.overlays, "show_range_outliers", False):
        range_masks = _exclusive_outlier_masks(
            [
                ("signal", summary_masks.get("Signal Intensity", pd.Series(False, index=overlay_df.index))),
                ("leak", summary_masks.get("Leak Rate", pd.Series(False, index=overlay_df.index))),
                ("d13c", summary_masks.get("d13C Range", pd.Series(False, index=overlay_df.index))),
                ("d18o", summary_masks.get("d18O Range", pd.Series(False, index=overlay_df.index))),
            ]
        )
        symbol_map = {"signal": "diamond", "leak": "x", "d13c": "cross", "d18o": "square-open"}
        label_map = {
            "signal": "Signal Intensity Range",
            "leak": "Leak Rate Range",
            "d13c": "d13C Range",
            "d18o": "d18O Range",
        }
        for key, mask in range_masks.items():
            rows = _rows(mask)
            if rows.empty:
                continue
            fig.add_trace(
                go.Scatter3d(
                    x=pd.to_numeric(rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(rows.get("d 13C/12C  Mean"), errors="coerce"),
                    z=pd.to_numeric(rows.get(z_col), errors="coerce"),
                    mode="markers",
                    name=label_map[key],
                    marker=dict(size=7, symbol=symbol_map[key], color="red", line=dict(width=1.5, color="black"), opacity=0.95),
                    customdata=_build_delta_point_customdata(rows, "cross"),
                    hovertemplate=hover_template,
                )
            )

    if getattr(config.overlays, "show_manual_outliers", False):
        manual_rows = _rows(summary_masks.get("Manual Override"))
        if not manual_rows.empty:
            fig.add_trace(
                go.Scatter3d(
                    x=pd.to_numeric(manual_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(manual_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    z=pd.to_numeric(manual_rows.get(z_col), errors="coerce"),
                    mode="markers",
                    name="Manual Outliers",
                    marker=dict(size=8, symbol="circle-open", color="#ec4899", line=dict(width=2, color="black"), opacity=0.95),
                    customdata=_build_delta_point_customdata(manual_rows, "cross"),
                    hovertemplate=hover_template,
                )
            )

    if getattr(config.overlays, "show_saturated_collectors", True):
        partial_rows = _rows(sat_masks.get("any"))
        if not partial_rows.empty:
            fig.add_trace(
                go.Scatter3d(
                    x=pd.to_numeric(partial_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(partial_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    z=pd.to_numeric(partial_rows.get(z_col), errors="coerce"),
                    mode="markers",
                    name="Partially Failed (Recovered Mean)",
                    marker=dict(size=8, symbol="diamond-open", color="#ff7f0e", line=dict(width=2, color="#ff7f0e"), opacity=1.0),
                    customdata=_build_delta_point_customdata(partial_rows, "cross"),
                    hovertemplate=hover_template,
                )
            )

    if getattr(config.overlays, "show_saturated_samples", True):
        full_rows = _rows(summary_masks.get("Fully Saturated Collectors"))
        if not full_rows.empty:
            fig.add_trace(
                go.Scatter3d(
                    x=pd.to_numeric(full_rows.get("d 18O/16O  Mean"), errors="coerce"),
                    y=pd.to_numeric(full_rows.get("d 13C/12C  Mean"), errors="coerce"),
                    z=pd.to_numeric(full_rows.get(z_col), errors="coerce"),
                    mode="markers",
                    name="Failed Samples (Fully Saturated)",
                    marker=dict(size=8, symbol="square-open", color="#d62728", line=dict(width=2, color="#d62728"), opacity=0.95),
                    customdata=_build_delta_point_customdata(full_rows, "cross"),
                    hovertemplate=hover_template,
                )
            )


def _apply_processing_3d_layout_tuning(fig: go.Figure) -> None:
    """Tune processing 3D figure spacing so the chart fills the card and colorbar stays right-aligned."""
    if fig is None:
        return

    colorbar_updated = False
    for trace in fig.data:
        marker = getattr(trace, "marker", None)
        if marker is None:
            continue
        colorbar = getattr(marker, "colorbar", None)
        if colorbar is None:
            continue
        # Push the colorbar into the right margin so the 3D scene can use the full chart domain.
        colorbar.x = 1.09
        colorbar.xanchor = "left"
        colorbar.y = 0.5
        colorbar.yanchor = "middle"
        colorbar.len = 0.78
        colorbar_updated = True
        break

    layout_updates: dict[str, Any] = {
        "scene": {"domain": {"x": [0.0, 1.0], "y": [0.0, 1.0]}},
        "margin": {"l": 8, "r": 130 if colorbar_updated else 24, "t": 56, "b": 8},
    }
    fig.update_layout(**layout_updates)


def build_overview_figures(
    filtered_df: pd.DataFrame,
    scoped_df: pd.DataFrame,
    unfiltered_scoped_df: pd.DataFrame,
    config: Any,
    edit_state: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    figures: dict[str, dict[str, Any]] = {}
    overlays_df = unfiltered_scoped_df.copy() if unfiltered_scoped_df is not None else pd.DataFrame()
    summary_masks, sat_masks = _build_overview_outlier_context(overlays_df, config, edit_state)
    stat_mask_d13, stat_mask_d18, stat_mask_combined = compute_statistical_outlier_masks(
        overlays_df,
        sigma_level=float(config.sigma_level_data),
        edit_state=edit_state,
        species_series=_get_species_series(overlays_df),
        method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    filtered_base_df = filtered_df.copy() if filtered_df is not None else pd.DataFrame()
    if not filtered_base_df.empty:
        filtered_base_df = filtered_base_df.loc[
            ~stat_mask_combined.reindex(filtered_base_df.index, fill_value=False).astype(bool)
        ].copy()
    fig_3d, _ = _build_isotope_3d_scatter(
        filtered_base_df,
        z_col=config.z_axis,
        z_label=config.z_axis,
        color_col=config.color_param,
        color_label=config.color_param,
        title="Processing 3D Chart",
    )
    if fig_3d is not None:
        _add_processing_3d_overlays(
            fig_3d,
            overlays_df,
            summary_masks,
            sat_masks,
            z_col=config.z_axis,
            z_label=config.z_axis,
            config=config,
        )
        axis_cols = ["d 18O/16O  Mean", "d 13C/12C  Mean", str(config.z_axis)]
        axis_df = pd.concat(
            [
                _numeric_axis_rows(filtered_base_df, axis_cols),
                _visible_overlay_axis_rows(overlays_df, summary_masks, sat_masks, config, axis_cols),
            ],
            axis=0,
            ignore_index=True,
        )
        if not axis_df.empty:
            x_range = _axis_range(axis_df["d 18O/16O  Mean"])
            y_range = _axis_range(axis_df["d 13C/12C  Mean"])
            z_range = _axis_range(axis_df[str(config.z_axis)])
            scene_update: dict[str, Any] = {}
            if x_range is not None:
                scene_update["xaxis"] = {"range": x_range}
            if y_range is not None:
                scene_update["yaxis"] = {"range": y_range}
            if z_range is not None:
                scene_update["zaxis"] = {"range": z_range}
            if scene_update:
                fig_3d.update_layout(scene=scene_update)
        _apply_processing_3d_layout_tuning(fig_3d)
    figures["processing_3d"] = _figure_json(fig_3d)
    scoped_base_df = scoped_df.copy() if scoped_df is not None else pd.DataFrame()
    scoped_d13 = scoped_base_df.loc[~stat_mask_d13.reindex(scoped_base_df.index, fill_value=False).astype(bool)].copy()
    scoped_d18 = scoped_base_df.loc[~stat_mask_d18.reindex(scoped_base_df.index, fill_value=False).astype(bool)].copy()
    figures["d13_summary"] = _build_summary_figure(scoped_d13, "d13C", config.x_axis_option, config.color_param)
    figures["d18_summary"] = _build_summary_figure(scoped_d18, "d18O", config.x_axis_option, config.color_param)

    if go is None or (scoped_df is None or scoped_df.empty) and (overlays_df is None or overlays_df.empty):
        figures["crossplot"] = {}
        return figures
    cross_df = scoped_df.copy() if scoped_df is not None else pd.DataFrame()
    if not cross_df.empty:
        cross_df = cross_df.loc[~stat_mask_combined.reindex(cross_df.index, fill_value=False).astype(bool)].copy()
    cross_df["_species_label"] = _resolve_species_labels(cross_df)
    color_series, colorbar_category_ticks, has_numeric_colors, color_min, color_max = _color_series_for_plot(cross_df, config.color_param)
    cross_df["_color_value"] = color_series
    fig_cross = go.Figure()
    show_colorbar = has_numeric_colors
    for species, species_df in cross_df.groupby("_species_label", dropna=False):
        plot_df = species_df.copy()
        x_vals = pd.to_numeric(plot_df.get("d 18O/16O  Mean"), errors="coerce")
        y_vals = pd.to_numeric(plot_df.get("d 13C/12C  Mean"), errors="coerce")
        valid = x_vals.notna() & y_vals.notna()
        if not valid.any():
            continue
        plot_df = plot_df.loc[valid].copy()
        marker: dict[str, Any] = dict(size=9, opacity=0.85)
        if has_numeric_colors:
            marker.update(
                color=plot_df["_color_value"],
                colorscale="Viridis",
                cmin=color_min,
                cmax=color_max,
                showscale=show_colorbar,
            )
            if show_colorbar:
                colorbar_cfg: dict[str, Any] = {
                    "title": {
                        "text": "Date" if config.color_param == "Date_ordinal" else str(config.color_param),
                        "side": "right",
                    },
                    "thickness": 16,
                    "len": 0.7,
                    "y": 0.5,
                    "yanchor": "middle",
                }
                if config.color_param == "Date_ordinal":
                    tickvals, ticktext = _build_date_colorbar_ticks(cross_df.get(config.color_param))
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
        fig_cross.add_trace(
            go.Scatter(
                x=pd.to_numeric(plot_df.get("d 18O/16O  Mean"), errors="coerce"),
                y=pd.to_numeric(plot_df.get("d 13C/12C  Mean"), errors="coerce"),
                mode="markers",
                name=str(species),
                marker=marker,
                customdata=_build_delta_point_customdata(plot_df, "cross"),
                hovertemplate=(
                    "Species: "
                    + str(species)
                    + "<br>d18O: %{x:.3f}<br>d13C: %{y:.3f}<br>Identifier 2: %{customdata[3]}<extra></extra>"
                ),
            )
        )
    _add_processing_crossplot_overlays(fig_cross, overlays_df, summary_masks, sat_masks, config)
    axis_cols = ["d 18O/16O  Mean", "d 13C/12C  Mean"]
    axis_df = pd.concat(
        [
            _numeric_axis_rows(cross_df, axis_cols),
            _visible_overlay_axis_rows(overlays_df, summary_masks, sat_masks, config, axis_cols),
        ],
        axis=0,
        ignore_index=True,
    )
    cross_x_range = _axis_range(axis_df.get("d 18O/16O  Mean", pd.Series(dtype=float)))
    cross_y_range = _axis_range(axis_df.get("d 13C/12C  Mean", pd.Series(dtype=float)))
    x_axis: dict[str, Any] = {"title": "d18O", "constrain": "domain"}
    y_axis: dict[str, Any] = {"title": "d13C", "constrain": "domain"}
    if cross_x_range is not None:
        x_axis["range"] = cross_x_range
    else:
        x_axis["autorange"] = True
    if cross_y_range is not None:
        y_axis["range"] = cross_y_range
    else:
        y_axis["autorange"] = True
    fig_cross.update_layout(
        title="d13C vs d18O",
        xaxis=x_axis,
        yaxis=y_axis,
        hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0.0, xanchor="left"),
        margin=dict(l=40, r=20, t=80, b=40),
        height=720,
    )
    figures["crossplot"] = _figure_json(fig_cross)
    return figures


def _x_axis_series(df: pd.DataFrame, x_axis_option: str) -> pd.Series:
    if x_axis_option == "By Identifier 2":
        return df.get("Identifier 2", pd.Series(index=df.index)).apply(_parse_numeric_token)
    return pd.Series(np.arange(len(df)), index=df.index)


def _build_identifier_figure(
    species_df: pd.DataFrame,
    species_unfiltered: pd.DataFrame,
    identifier: str,
    isotope_key: str,
    config: Any,
    edit_state: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    if go is None:
        return {}, False
    y_col = "d 13C/12C  Mean" if isotope_key == "d13C" else "d 18O/16O  Mean"
    cal_col = "d13C_calibrated" if isotope_key == "d13C" else "d18O_calibrated"
    species_col = "Species" if "Species" in species_unfiltered.columns else "Identifier 1"
    filtered_identifier = species_df[species_df["Identifier 1"].astype(str) == str(identifier)].copy()
    unfiltered_identifier = species_unfiltered[species_unfiltered["Identifier 1"].astype(str) == str(identifier)].copy()
    if filtered_identifier.empty and unfiltered_identifier.empty:
        return {}, False

    filtered_identifier["x_axis"] = _x_axis_series(filtered_identifier, config.x_axis_option)
    unfiltered_identifier["x_axis"] = _x_axis_series(unfiltered_identifier, config.x_axis_option)
    filtered_identifier = filtered_identifier.sort_values("x_axis", na_position="last")
    unfiltered_identifier = unfiltered_identifier.sort_values("x_axis", na_position="last")

    summary_masks = build_category_masks(
        unfiltered_identifier,
        RangeConfig(
            signal_range=config.signal_range,
            leak_range=config.leak_range,
            d13c_range=config.d13c_range,
            d18o_range=config.d18o_range,
            partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
        ),
        edit_state=edit_state,
        sigma_level=float(config.sigma_level_data),
        statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    signal_ok = _signal_in_range_mask(unfiltered_identifier.get("1  Cycle Int  Samp  44"), config.signal_range)
    leak_ok = pd.to_numeric(unfiltered_identifier.get("leak_rate"), errors="coerce").between(
        *config.leak_range,
        inclusive="both",
    )
    d13_ok = pd.to_numeric(unfiltered_identifier.get("d 13C/12C  Mean"), errors="coerce").between(
        *config.d13c_range,
        inclusive="both",
    )
    d18_ok = pd.to_numeric(unfiltered_identifier.get("d 18O/16O  Mean"), errors="coerce").between(
        *config.d18o_range,
        inclusive="both",
    )
    sat_masks_for_stats = _partial_saturation_isotope_masks(unfiltered_identifier)
    partial_keep = pd.Series(False, index=unfiltered_identifier.index, dtype=bool)
    if bool(config.overlays.show_saturated_collectors):
        partial_keep = signal_ok & leak_ok & (
            (sat_masks_for_stats["d13C"] & d13_ok) | (sat_masks_for_stats["d18O"] & d18_ok)
        )
    stat_source_mask = (signal_ok & leak_ok & d13_ok & d18_ok) | partial_keep
    stat_source = unfiltered_identifier.loc[stat_source_mask].copy()
    stat_mask_d13, stat_mask_d18, _ = compute_statistical_outlier_masks(
        stat_source,
        sigma_level=float(config.sigma_level_data),
        edit_state=edit_state,
        species_series=_get_species_series(stat_source),
        method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
        iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
    )
    statistical_mask = stat_mask_d13 if isotope_key == "d13C" else stat_mask_d18
    filtered_identifier = filtered_identifier.loc[
        ~statistical_mask.reindex(filtered_identifier.index, fill_value=False).astype(bool)
    ].copy()
    color_series, _, has_numeric_colors, color_min, color_max = _color_series_for_plot(filtered_identifier, config.color_param)
    filtered_identifier["_color_value"] = color_series
    edited_mask = pd.Series(
        [str(idx) in {str(row) for row in (edit_state or {}).get("edited_rows", [])} for idx in filtered_identifier.index],
        index=filtered_identifier.index,
        dtype=bool,
    )
    sat_masks = _partial_saturation_isotope_masks(unfiltered_identifier)
    d13_std_lookup, d18_std_lookup = _build_cycle_std_lookups(unfiltered_identifier)

    fig = go.Figure()
    if getattr(config.overlays, "show_statistical_outliers", False):
        statistical_outliers = unfiltered_identifier.loc[statistical_mask.reindex(unfiltered_identifier.index, fill_value=False)]
        if not statistical_outliers.empty:
            fig.add_trace(
                go.Scatter(
                    x=statistical_outliers["x_axis"],
                    y=pd.to_numeric(statistical_outliers.get(y_col), errors="coerce"),
                    mode="markers",
                    marker=dict(color="red", symbol="square", size=12, line=dict(width=1.5, color="black")),
                    name="Statistical Outliers",
                    customdata=_build_delta_point_customdata(statistical_outliers, isotope_key),
                )
            )
    if getattr(config.overlays, "show_range_outliers", False):
        range_masks = _exclusive_outlier_masks(
            [
                ("signal", summary_masks["Signal Intensity"]),
                ("leak", summary_masks["Leak Rate"]),
                ("d13c", summary_masks["d13C Range"]),
                ("d18o", summary_masks["d18O Range"]),
            ]
        )
        symbol_map = {
            "signal": "diamond",
            "leak": "star",
            "d13c": "cross",
            "d18o": "x",
        }
        label_map = {
            "signal": "Signal Intensity Range",
            "leak": "Leak Rate Range",
            "d13c": "d13C Range",
            "d18o": "d18O Range",
        }
        for key, mask in range_masks.items():
            rows = unfiltered_identifier.loc[mask]
            if rows.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=rows["x_axis"],
                    y=pd.to_numeric(rows.get(y_col), errors="coerce"),
                    mode="markers",
                    marker=dict(color="red", symbol=symbol_map[key], size=12, line=dict(width=1.5, color="black")),
                    name=label_map[key],
                    customdata=_build_delta_point_customdata(rows, isotope_key),
                )
            )
    if getattr(config.overlays, "show_manual_outliers", False):
        manual_mask = summary_masks.get("Manual Override", pd.Series(False, index=unfiltered_identifier.index))
        manual_rows = unfiltered_identifier.loc[manual_mask.reindex(unfiltered_identifier.index, fill_value=False).astype(bool)]
        if not manual_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=manual_rows["x_axis"],
                    y=pd.to_numeric(manual_rows.get(y_col), errors="coerce"),
                    mode="markers",
                    marker=dict(color="#ec4899", symbol="circle-open", size=13, line=dict(width=2, color="black")),
                    name="Manual Outliers",
                    customdata=_build_delta_point_customdata(manual_rows, isotope_key),
                )
            )
    if getattr(config.overlays, "show_saturated_collectors", True):
        status_rows = unfiltered_identifier.loc[sat_masks[isotope_key]]
        if not status_rows.empty:
            fig.add_trace(
                go.Scatter(
                    x=status_rows["x_axis"],
                    y=pd.to_numeric(status_rows.get(y_col), errors="coerce"),
                    mode="markers",
                    marker=dict(color="#ff7f0e", symbol="diamond-open", size=12, line=dict(width=2)),
                    name="Partially Failed (Recovered Mean)",
                    customdata=_build_delta_point_customdata(status_rows, isotope_key),
                )
            )
    if getattr(config.overlays, "show_saturated_samples", True):
        full_rows = unfiltered_identifier.loc[summary_masks["Fully Saturated Collectors"]]
        if not full_rows.empty:
            y_vals = pd.to_numeric(filtered_identifier.get(y_col), errors="coerce")
            y_min = y_vals.min() if y_vals.notna().any() else -1.0
            y_max = y_vals.max() if y_vals.notna().any() else 1.0
            y_range = y_max - y_min if np.isfinite(y_max - y_min) else 1.0
            y_failed = y_min - (0.1 * y_range if y_range > 0 else 0.5)
            fig.add_trace(
                go.Scatter(
                    x=full_rows["x_axis"],
                    y=[y_failed] * len(full_rows),
                    mode="markers",
                    marker=dict(color="#d62728", symbol="triangle-down", size=10, line=dict(width=1)),
                    name="Failed Samples (Fully Saturated)",
                    customdata=_build_delta_point_customdata(full_rows, isotope_key),
                )
            )
    if getattr(config.overlays, "show_failed_samples", True):
        failed_rows = unfiltered_identifier.loc[summary_masks["Failed Sample"]]
        if not failed_rows.empty:
            failed_values = pd.to_numeric(failed_rows.get(y_col), errors="coerce")
            failed_interp = failed_rows.loc[failed_values.notna()]
            failed_missing = failed_rows.loc[failed_values.isna()]
            if not failed_interp.empty:
                fig.add_trace(
                    go.Scatter(
                        x=failed_interp["x_axis"],
                        y=pd.to_numeric(failed_interp.get(y_col), errors="coerce"),
                        mode="markers",
                        marker=dict(color="#ff00ff", symbol="triangle-down", size=10, line=dict(width=1)),
                        name="Failed Samples (Interpolated)",
                        customdata=_build_delta_point_customdata(failed_interp, isotope_key),
                    )
                )
            if not failed_missing.empty:
                y_vals = pd.to_numeric(filtered_identifier.get(y_col), errors="coerce")
                y_min = y_vals.min() if y_vals.notna().any() else -1.0
                y_max = y_vals.max() if y_vals.notna().any() else 1.0
                y_range = y_max - y_min if np.isfinite(y_max - y_min) else 1.0
                y_failed = y_min - (0.1 * y_range if y_range > 0 else 0.5)
                fig.add_trace(
                    go.Scatter(
                        x=failed_missing["x_axis"],
                        y=[y_failed] * len(failed_missing),
                        mode="markers",
                        marker=dict(color="#7f7f7f", symbol="triangle-down", size=10, line=dict(width=1)),
                        name="Failed Samples (No Values)",
                        customdata=_build_delta_point_customdata(failed_missing, isotope_key),
                    )
                )
    if not filtered_identifier.empty:
        error_y = _build_plotly_error_bar_for_df(filtered_identifier, isotope_key, d13_std_lookup, d18_std_lookup)
        marker: dict[str, Any] = dict(size=8, color="#2563eb")
        if has_numeric_colors:
            marker = dict(
                size=8,
                color=filtered_identifier["_color_value"],
                colorscale="Viridis",
                cmin=color_min,
                cmax=color_max,
                showscale=False,
            )
        fig.add_trace(
            go.Scatter(
                x=filtered_identifier["x_axis"],
                y=pd.to_numeric(filtered_identifier.get(y_col), errors="coerce"),
                mode="lines+markers",
                line=dict(color="#2563eb", width=1.5),
                marker=marker,
                name=f"Raw {isotope_key} - {identifier}",
                error_y=error_y,
                customdata=_build_delta_point_customdata(filtered_identifier, isotope_key),
            )
        )
        if edited_mask.any():
            edited_rows = filtered_identifier.loc[edited_mask]
            fig.add_trace(
                go.Scatter(
                    x=edited_rows["x_axis"],
                    y=pd.to_numeric(edited_rows.get(y_col), errors="coerce"),
                    mode="markers",
                    marker=dict(color="#ff00ff", symbol="circle", size=12, line=dict(width=1, color="#ff00ff")),
                    name="Edited Samples",
                    customdata=_build_delta_point_customdata(edited_rows, isotope_key),
                )
            )
        has_calibrated = cal_col in filtered_identifier.columns and pd.to_numeric(filtered_identifier[cal_col], errors="coerce").notna().any()
        if has_calibrated:
            fig.add_trace(
                go.Scatter(
                    x=filtered_identifier["x_axis"],
                    y=pd.to_numeric(filtered_identifier.get(cal_col), errors="coerce"),
                    mode="lines",
                    line=dict(color="#f97316", width=2),
                    name=f"Calibrated {isotope_key} - {identifier}",
                )
            )
        fig.update_layout(
            title=f"{identifier} - {isotope_key} for Species: {species_unfiltered[species_col].iloc[0] if species_col in species_unfiltered.columns and not species_unfiltered.empty else ''}",
            xaxis_title="Sample Number" if config.x_axis_option == "By Sequence" else "Identifier 2",
            yaxis_title=isotope_key,
            hovermode="closest",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0.0, xanchor="left"),
            margin=dict(l=40, r=20, t=80, b=40),
        )
        _apply_cycle_std_error_bars(fig, d13_std_lookup, d18_std_lookup)
        return _figure_json(fig), bool(has_calibrated)
    return _figure_json(fig), False


def build_species_sections(
    filtered_df: pd.DataFrame,
    unfiltered_df: pd.DataFrame,
    config: Any,
    edit_state: dict[str, Any] | None,
) -> list[SpeciesSection]:
    if filtered_df is None or unfiltered_df is None:
        return []
    species_col = "Species" if "Species" in unfiltered_df.columns else "Identifier 1"
    species_values = _get_species_series(unfiltered_df).fillna("").astype(str)
    sections: list[SpeciesSection] = []
    if species_values.empty:
        return sections
    scoped_filtered = _scope_df(filtered_df, config.selected_identifier)
    scoped_unfiltered = _scope_df(unfiltered_df, config.selected_identifier)
    for species in sorted(value for value in species_values.unique().tolist() if str(value).strip() != ""):
        species_df = scoped_filtered[_get_species_series(scoped_filtered).fillna("").astype(str) == str(species)].copy()
        species_unfiltered = scoped_unfiltered[_get_species_series(scoped_unfiltered).fillna("").astype(str) == str(species)].copy()
        if species_df.empty and species_unfiltered.empty:
            continue
        identifier_figures: list[IdentifierFigureSet] = []
        identifiers = sorted(
            {
                str(value)
                for value in species_unfiltered.get("Identifier 1", pd.Series(dtype=object)).dropna().tolist()
                if str(value).strip() != ""
            }
        )
        for identifier in identifiers:
            d13_fig, has_cal_d13 = _build_identifier_figure(species_df, species_unfiltered, identifier, "d13C", config, edit_state)
            d18_fig, has_cal_d18 = _build_identifier_figure(species_df, species_unfiltered, identifier, "d18O", config, edit_state)
            if d13_fig or d18_fig:
                identifier_figures.append(
                    IdentifierFigureSet(
                        identifier=identifier,
                        d13c=d13_fig,
                        d18o=d18_fig,
                        has_calibrated_d13c=has_cal_d13,
                        has_calibrated_d18o=has_cal_d18,
                    )
                )
        category_masks = build_category_masks(
            species_unfiltered,
            RangeConfig(
                signal_range=config.signal_range,
                leak_range=config.leak_range,
                d13c_range=config.d13c_range,
                d18o_range=config.d18o_range,
                partial_saturated_outliers=not bool(config.overlays.show_saturated_collectors),
            ),
            edit_state=edit_state,
            sigma_level=float(config.sigma_level_data),
            statistical_outlier_method=str(getattr(config, "statistical_outlier_method", "Z-Score")),
            iqr_multiplier=float(getattr(config, "iqr_multiplier_data", 1.5)),
        )
        outlier_tables = build_outlier_tables(species_unfiltered, category_masks, species_col, scope_title=str(species))
        sections.append(
            SpeciesSection(
                species=str(species),
                identifier_figures=identifier_figures,
                outlier_tables=outlier_tables,
            )
        )
    return sections
