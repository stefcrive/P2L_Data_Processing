
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

try:
    from plotly.subplots import make_subplots
    import plotly.graph_objects as go
except ModuleNotFoundError:  # pragma: no cover - optional for logic-only tests
    make_subplots = None
    go = None

from ..shared.plotting import (
    _build_date_colorbar_ticks,
    _is_date_color_column,
    _prefer_datetime_color_values,
    _prepare_color_values,
)
from ..constants import (
    CYCLE1_SIGNAL_DIFF44_COL,
    CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
    CYCLE1_SIGNAL_REF44_COL,
    CYCLE1_SIGNAL_SAMP44_COL,
)


def _color_param_label(color_param: str) -> str:
    if _is_date_color_column(color_param):
        return "Date"
    if color_param == CYCLE1_SIGNAL_SAMP44_COL:
        return "Initial sample intensity"
    if color_param == CYCLE1_SIGNAL_REF44_COL:
        return "Initial reference gas intensity"
    return str(color_param)


def _format_hover_color_value(value) -> str:
    if value is None:
        return "N/A"
    try:
        if pd.isna(value):
            return "N/A"
    except Exception:
        pass
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.notna(numeric) and np.isfinite(float(numeric)):
        return f"{float(numeric):.2f}"
    return str(value)


def _resolve_species_labels(df: pd.DataFrame) -> pd.Series:
    source = df.get("Species", df.get("Identifier 1", pd.Series(index=df.index, dtype=object)))
    labels = pd.Series(source, index=df.index).fillna("").astype(str).str.strip()
    labels = labels.where(~labels.str.lower().eq("nan"), "")
    labels = labels.where(labels != "", "Unknown")
    return labels


def _partially_saturated_mask(df: pd.DataFrame) -> pd.Series:
    status_series = df.get("Collector Status", df.get("collector_status", pd.Series(index=df.index, dtype=object)))
    normalized = pd.Series(status_series, index=df.index).fillna("").astype(str).str.strip().str.lower()
    return normalized.eq("partially saturated collectors")


def _fit_saturating_co2_curve(
    x_values: pd.Series,
    y_values: pd.Series,
    *,
    grid_size: int = 200,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Fit a robust, nonnegative CO2 curve that approaches an upper plateau."""
    x_numeric = pd.to_numeric(x_values, errors="coerce").to_numpy(dtype=float)
    y_numeric = pd.to_numeric(y_values, errors="coerce").to_numpy(dtype=float)
    nonnegative_x = np.isfinite(x_numeric) & (x_numeric >= 0.0)
    valid = (
        nonnegative_x
        & np.isfinite(y_numeric)
        & (y_numeric >= 0.0)
    )
    x_clean = x_numeric[valid]
    y_clean = y_numeric[valid]
    if len(x_clean) < 3 or np.unique(x_clean).size < 2:
        return None

    positive_x = x_clean[x_clean > 0.0]
    if positive_x.size == 0 or float(np.max(y_clean)) <= 0.0:
        return None

    max_x = float(np.max(x_numeric[nonnegative_x]))
    max_y = float(np.max(y_clean))
    initial_plateau = max(float(np.percentile(y_clean, 75)), np.finfo(float).eps)
    initial_scale = max(float(np.median(positive_x)), np.finfo(float).eps)
    residual_scale = max(
        float(np.median(np.abs(y_clean - np.median(y_clean)))),
        max_y * 0.05,
        np.finfo(float).eps,
    )

    def residuals(parameters: np.ndarray) -> np.ndarray:
        plateau, scale = parameters
        predicted = plateau * (-np.expm1(-x_clean / scale))
        return predicted - y_clean

    try:
        fit = least_squares(
            residuals,
            x0=np.asarray([initial_plateau, initial_scale]),
            bounds=(
                np.asarray([np.finfo(float).eps, max_x * 1e-6]),
                np.asarray([max(max_y * 10.0, 1.0), max_x * 100.0]),
            ),
            loss="soft_l1",
            f_scale=residual_scale,
        )
    except (ValueError, FloatingPointError):
        return None
    if not fit.success or not np.isfinite(fit.x).all():
        return None

    plateau, scale = fit.x
    x_curve = np.linspace(0.0, max_x, max(2, int(grid_size)))
    y_curve = plateau * (-np.expm1(-x_curve / scale))
    return x_curve, y_curve


def _leading_variability_contributions(
    frame: pd.DataFrame,
    features: list[str],
) -> tuple[np.ndarray, np.ndarray, float] | None:
    """Rank variables by their weighted contribution to the first two principal axes."""
    numeric = frame[features].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    complete = numeric.dropna()
    if len(complete.index) < 2:
        return None

    scaled = StandardScaler().fit_transform(complete)
    if not np.isfinite(scaled).all() or np.allclose(np.var(scaled, axis=0), 0.0):
        return None

    component_count = min(2, scaled.shape[0], scaled.shape[1])
    pca = PCA(n_components=component_count)
    pca.fit(scaled)
    explained_ratio = np.nan_to_num(pca.explained_variance_ratio_, nan=0.0)
    captured_ratio = float(explained_ratio.sum())
    if captured_ratio <= 0.0:
        return None

    weighted = np.square(pca.components_.T) * explained_ratio
    component_contributions = weighted / captured_ratio * 100.0
    total_contributions = component_contributions.sum(axis=1)
    return total_contributions, component_contributions, captured_ratio * 100.0


def _explained_variance_by_component(
    frame: pd.DataFrame,
    features: list[str],
) -> tuple[np.ndarray, np.ndarray, int] | None:
    """Return explained and cumulative variance for all estimable principal components."""
    numeric = frame[features].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    complete = numeric.dropna()
    if len(complete.index) < 2:
        return None

    scaled = StandardScaler().fit_transform(complete)
    if not np.isfinite(scaled).all() or np.allclose(np.var(scaled, axis=0), 0.0):
        return None

    component_count = min(scaled.shape[0], scaled.shape[1])
    pca = PCA(n_components=component_count)
    pca.fit(scaled)
    explained = np.nan_to_num(pca.explained_variance_ratio_, nan=0.0) * 100.0
    return explained, np.cumsum(explained), len(complete.index)


def _spearman_correlation_summary(
    frame: pd.DataFrame,
    features: list[str],
) -> tuple[np.ndarray, np.ndarray] | None:
    """Return pairwise Spearman correlations and the complete-pair counts behind them."""
    numeric = frame[features].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if len(numeric.index) < 3:
        return None

    correlation = numeric.corr(method="spearman", min_periods=3)
    if not np.isfinite(correlation.to_numpy(dtype=float)).any():
        return None

    valid = numeric.notna().astype(int)
    pair_counts = valid.T.dot(valid)
    return correlation.to_numpy(dtype=float), pair_counts.to_numpy(dtype=int)

def create_diagnostic_plots(
    df,
    color_param,
    standards_file="standards.csv",
    selected_standards: list[str] | tuple[str, ...] | None = None,
):
    """
    Create diagnostic plots for analysis with the option to color points by a selected parameter.
    Parameters:
        - df (pd.DataFrame): DataFrame containing the data.
        - color_param (str): The column name to use for coloring the scatter plot markers.
    """

    if selected_standards is None:
        try:
            standards_df = pd.read_csv(standards_file)
            standards_list = standards_df["Standard"].unique()
        except Exception as e:
            raise ValueError(f"Error loading standards from {standards_file}: {e}")
    else:
        standards_list = selected_standards


    diff_signal_col = CYCLE1_SIGNAL_DIFF44_COL
    pressure_adjusted_diff_col = CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL

    # The combined figure is an internal source for the standalone diagnostic cards.
    # Card reading order and visible grouping are defined by DIAGNOSTIC_GRID_SPECS.
    fig = make_subplots(
        rows=14,
        cols=2,
        subplot_titles=(
            'd13C vs Leak Rate', 'd18O vs Leak Rate',
            'd13C vs P no Acid', 'd18O vs P no Acid',
            'd13C vs Total CO2', 'd18O vs Total CO2',
            'd13C vs Initial Sample Intensity', 'd18O vs Initial Sample Intensity',
            'd13C vs Diff Signal Intensity', 'd18O vs Diff Signal Intensity',
            'd13C vs Pressure-Adjusted Signal Intensity Diff', 'd18O vs Pressure-Adjusted Signal Intensity Diff',
            'd13C vs Line', 'd18O vs Line',
            'Total CO2 vs Initial Sample Intensity', 'Leak Rate vs Total CO2',
            'Leak Rate vs Line', 'Total CO2 vs Line',
            'Leak Rate vs Initial Sample Intensity', 'Leak Rate vs P no Acid',
            'Leak Rate vs P Gasses', 'd18O vs d13C',
            'P no Acid vs Line', 'P Gasses vs Line',
            'Initial Sample Intensity vs Line', 'Parameter Contributions to Variability',
            'Explained Variance by Component', 'Spearman Correlation Matrix',
        ),
        vertical_spacing=0.035,
        horizontal_spacing=0.12,
        specs=[
            [{'type': 'scatter'}, {'type': 'scatter'}],
            [{'type': 'scatter'}, {'type': 'scatter'}],
            [{'type': 'scatter'}, {'type': 'scatter'}],
            [{'type': 'scatter'}, {'type': 'scatter'}],
            [{'type': 'scatter'}, {'type': 'scatter'}],
            [{'type': 'scatter'}, {'type': 'scatter'}],
            [{'type': 'box'}, {'type': 'box'}],
            [{'type': 'scatter'}, {'type': 'scatter'}],
            [{'type': 'box'}, {'type': 'box'}],
            [{'type': 'scatter'}, {'type': 'scatter'}],
            [{'type': 'scatter'}, {'type': 'scatter'}],
            [{'type': 'box'}, {'type': 'box'}],
            [{'type': 'box'}, {'type': 'scatter'}],
            [{'type': 'scatter'}, {'type': 'heatmap'}],
        ],
    )

    # Ensure the required columns are present in the DataFrame
    required_columns = ['leak_rate', 'd 13C/12C  Mean', 'p_no_acid', 'total_co2', 'd 18O/16O  Mean', 'Line',
                        '1  Cycle Int  Samp  44', 'p_gases', 'Identifier 1']
    if color_param not in df.columns:
        raise ValueError(f"Selected color parameter '{color_param}' is missing from the DataFrame.")
    for col in required_columns:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    identifier1_series = df.get("Identifier 1", pd.Series("", index=df.index)).fillna("").astype(str)
    identifier2_series = df.get("Identifier 2", pd.Series("", index=df.index)).fillna("").astype(str)
    species_series = _resolve_species_labels(df)
    hover_color_label = _color_param_label(color_param)
    hover_color_series = df.get(color_param, pd.Series(index=df.index, dtype=object)).map(_format_hover_color_value)

    def _build_customdata_for_index(index: pd.Index) -> np.ndarray:
        return np.column_stack(
            (
                index.astype(str).to_numpy(),
                identifier1_series.reindex(index).fillna("").astype(str).to_numpy(),
                identifier2_series.reindex(index).fillna("").astype(str).to_numpy(),
                species_series.reindex(index).fillna("Unknown").astype(str).to_numpy(),
                hover_color_series.reindex(index).fillna("N/A").astype(str).to_numpy(),
            )
        )

    base_customdata = _build_customdata_for_index(df.index)

    selected_standard_keys = {
        str(value).strip().casefold()
        for value in standards_list
        if str(value).strip()
    }
    marker_symbols = [
        "circle-open" if str(identifier).strip().casefold() in selected_standard_keys else "circle"
        for identifier in df["Identifier 1"]
    ]
    hover_text = df['Identifier 2']
    partial_df = df.loc[_partially_saturated_mask(df)]

    # Build colorbar configuration for the first trace (readable dates if needed)
    is_date_color = _is_date_color_column(color_param)
    colorbar_cfg = dict(
        title=dict(
            text='Date' if is_date_color else color_param,
            side='right',
        ),
        thickness=20,
        len=0.75,  # Longer colorbar
        y=0.5,     # Center vertically
        yanchor='middle',
        x=1.15,    # Move further right
        xanchor='right'
    )
    color_values, colorbar_category_ticks = _prepare_color_values(
        df[color_param],
        prefer_dates=_prefer_datetime_color_values(color_param),
    )
    if is_date_color:
        tickvals, ticktext = _build_date_colorbar_ticks(color_values if color_values is not None else df[color_param])
        if tickvals and ticktext:
            colorbar_cfg.update(tickmode='array', tickvals=tickvals, ticktext=ticktext)
    elif colorbar_category_ticks is not None:
        tickvals, ticktext = colorbar_category_ticks
        if tickvals and ticktext:
            colorbar_cfg.update(tickmode='array', tickvals=tickvals, ticktext=ticktext)

    # Scatter plots with coloring by selected parameter
    # First trace with the colorbar
    fig.add_trace(go.Scatter(
        x=df['leak_rate'],
        y=df['d 13C/12C  Mean'],
        mode='markers',
        marker=dict(
            color=color_values,
            colorscale='Viridis',
            symbol=marker_symbols,
            colorbar=colorbar_cfg,
            showscale=True
        ),
        text=hover_text,
        hoverinfo='text+x+y'
    ), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['p_no_acid'], y=df['d 13C/12C  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=2, col=1)
    fig.add_trace(go.Scatter(x=df['total_co2'], y=df['d 13C/12C  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=3, col=1)

    fig.add_trace(go.Scatter(x=df['leak_rate'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=1, col=2)
    fig.add_trace(go.Scatter(x=df['p_no_acid'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=2, col=2)
    fig.add_trace(go.Scatter(x=df['total_co2'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=3, col=2)

    fig.add_trace(go.Box(x=df['Line'], y=df['leak_rate']), row=9, col=1)

    fig.add_trace(go.Scatter(x=df['1  Cycle Int  Samp  44'], y=df['total_co2'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=8, col=1)

    saturating_curve = _fit_saturating_co2_curve(
        df['1  Cycle Int  Samp  44'],
        df['total_co2'],
    )
    if saturating_curve is not None:
        x_curve, y_curve = saturating_curve
        fig.add_trace(go.Scatter(
            x=x_curve,
            y=y_curve,
            mode='lines',
            name='Asymptotic Fit',
            line=dict(color='red', dash='dash')
        ), row=8, col=1)

    fig.add_trace(go.Scatter(x=df['1  Cycle Int  Samp  44'], y=df['d 13C/12C  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=4, col=1)

    fig.add_trace(go.Scatter(x=df['1  Cycle Int  Samp  44'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=4, col=2)
    fig.add_trace(go.Box(x=df['Line'], y=df['d 13C/12C  Mean']), row=7, col=1)
    fig.add_trace(go.Box(x=df['Line'], y=df['d 18O/16O  Mean']), row=7, col=2)

    fig.add_trace(go.Scatter(x=df['total_co2'], y=df['leak_rate'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=8, col=2)
    fig.add_trace(go.Scatter(x=df['d 13C/12C  Mean'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, symbol=marker_symbols, colorscale='Viridis', showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=11, col=2)
    fig.add_trace(go.Box(x=df['Line'], y=df['total_co2']), row=9, col=2)



    # Add scatter plots with coloring by selected parameter, adjusting marker style for standards
    fig.add_trace(go.Scatter(
        x=df['1  Cycle Int  Samp  44'], y=df['leak_rate'], mode='markers',
        marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=10, col=1)

    fig.add_trace(go.Scatter(
        x=df['p_no_acid'], y=df['leak_rate'], mode='markers',
        marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=10, col=2)

    fig.add_trace(go.Scatter(
        x=df['p_gases'], y=df['leak_rate'], mode='markers',
        marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=11, col=1)

    fig.add_trace(go.Box(x=df['Line'], y=df['p_no_acid']), row=12, col=1)
    fig.add_trace(go.Box(x=df['Line'], y=df['p_gases']), row=12, col=2)
    fig.add_trace(go.Box(x=df['Line'], y=df['1  Cycle Int  Samp  44']), row=13, col=1)

    if diff_signal_col in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df[diff_signal_col],
                y=df['d 18O/16O  Mean'],
                mode='markers',
                marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False),
                text=hover_text,
                hoverinfo='text+x+y',
            ),
            row=5,
            col=2,
        )
        fig.add_trace(
            go.Scatter(
                x=df[diff_signal_col],
                y=df['d 13C/12C  Mean'],
                mode='markers',
                marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False),
                text=hover_text,
                hoverinfo='text+x+y',
            ),
            row=5,
            col=1,
        )
    if pressure_adjusted_diff_col in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df[pressure_adjusted_diff_col],
                y=df['d 18O/16O  Mean'],
                mode='markers',
                marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False),
                text=hover_text,
                hoverinfo='text+x+y',
            ),
            row=6,
            col=2,
        )
        fig.add_trace(
            go.Scatter(
                x=df[pressure_adjusted_diff_col],
                y=df['d 13C/12C  Mean'],
                mode='markers',
                marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False),
                text=hover_text,
                hoverinfo='text+x+y',
            ),
            row=6,
            col=1,
        )

    def _add_partial_overlay(x_col: str, y_col: str, *, row: int, col: int) -> None:
        if partial_df.empty or x_col not in partial_df.columns or y_col not in partial_df.columns:
            return
        x_values = pd.to_numeric(partial_df.get(x_col), errors="coerce")
        y_values = pd.to_numeric(partial_df.get(y_col), errors="coerce")
        valid = np.isfinite(x_values) & np.isfinite(y_values)
        overlay_rows = partial_df.loc[valid]
        if overlay_rows.empty:
            return
        overlay_index = overlay_rows.index
        fig.add_trace(
            go.Scatter(
                x=x_values.loc[overlay_index],
                y=y_values.loc[overlay_index],
                mode="markers",
                name="Partially Saturated Collectors",
                showlegend=False,
                marker=dict(
                    color="#ff7f0e",
                    symbol="diamond-open",
                    size=13,
                    opacity=1.0,
                    line=dict(width=2, color="#ff7f0e"),
                ),
                text=hover_text.loc[overlay_index],
                hoverinfo="text+x+y",
                customdata=_build_customdata_for_index(overlay_index),
            ),
            row=row,
            col=col,
        )

    partial_overlay_axes = [
        ("leak_rate", "d 13C/12C  Mean", 1, 1),
        ("leak_rate", "d 18O/16O  Mean", 1, 2),
        ("p_no_acid", "d 13C/12C  Mean", 2, 1),
        ("p_no_acid", "d 18O/16O  Mean", 2, 2),
        ("total_co2", "d 13C/12C  Mean", 3, 1),
        ("total_co2", "d 18O/16O  Mean", 3, 2),
        ("1  Cycle Int  Samp  44", "d 13C/12C  Mean", 4, 1),
        ("1  Cycle Int  Samp  44", "d 18O/16O  Mean", 4, 2),
        ("1  Cycle Int  Samp  44", "total_co2", 8, 1),
        ("total_co2", "leak_rate", 8, 2),
        ("1  Cycle Int  Samp  44", "leak_rate", 10, 1),
        ("p_no_acid", "leak_rate", 10, 2),
        ("p_gases", "leak_rate", 11, 1),
        ("d 13C/12C  Mean", "d 18O/16O  Mean", 11, 2),
    ]
    if diff_signal_col in df.columns:
        partial_overlay_axes.extend(
            [
                (diff_signal_col, "d 13C/12C  Mean", 5, 1),
                (diff_signal_col, "d 18O/16O  Mean", 5, 2),
            ]
        )
    if pressure_adjusted_diff_col in df.columns:
        partial_overlay_axes.extend(
            [
                (pressure_adjusted_diff_col, "d 13C/12C  Mean", 6, 1),
                (pressure_adjusted_diff_col, "d 18O/16O  Mean", 6, 2),
            ]
        )
    for x_col, y_col, row, col in partial_overlay_axes:
        _add_partial_overlay(x_col, y_col, row=row, col=col)

    # Rank parameters by their contribution to the dominant multivariate variability.
    features = ['leak_rate', 'd 13C/12C  Mean', 'p_no_acid', 'p_gases', 'total_co2',
                'd 18O/16O  Mean', 'Line', '1  Cycle Int  Samp  44']
    feature_labels = {
        'leak_rate': 'Leak rate',
        'd 13C/12C  Mean': 'd13C/12C mean',
        'p_no_acid': 'P no acid',
        'p_gases': 'P gasses',
        'total_co2': 'Total CO2',
        'd 18O/16O  Mean': 'd18O/16O mean',
        'Line': 'Line',
        '1  Cycle Int  Samp  44': 'Initial sample intensity',
    }
    contribution_result = _leading_variability_contributions(df, features)
    if contribution_result is not None:
        contributions, component_contributions, captured_variance = contribution_result
        order = np.argsort(contributions)
        ordered_contributions = contributions[order]
        ordered_component_contributions = component_contributions[order]
        ordered_labels = [feature_labels[features[index]] for index in order]
        pc1 = ordered_component_contributions[:, 0]
        pc2 = (
            ordered_component_contributions[:, 1]
            if ordered_component_contributions.shape[1] > 1
            else np.zeros_like(pc1)
        )
        contribution_customdata = np.column_stack(
            (
                pc1,
                pc2,
                np.full(len(order), captured_variance),
            )
        )
        fig.add_trace(go.Bar(
            x=ordered_contributions,
            y=ordered_labels,
            orientation='h',
            marker=dict(color='#315f8c', line=dict(color='#244968', width=1)),
            text=[f"{value:.1f}%" for value in ordered_contributions],
            textposition='auto',
            customdata=contribution_customdata,
            hovertemplate=(
                '<b>%{y}</b><br>'
                'Contribution to leading variability: %{x:.1f}%<br>'
                'Via PC1: %{customdata[0]:.1f}%<br>'
                'Via PC2: %{customdata[1]:.1f}%<br>'
                'Variance captured by PC1 + PC2: %{customdata[2]:.1f}%<extra></extra>'
            ),
        ), row=13, col=2)

    explained_variance_result = _explained_variance_by_component(df, features)
    if explained_variance_result is not None:
        explained_variance, cumulative_variance, complete_row_count = explained_variance_result
        component_labels = [f"PC{index}" for index in range(1, len(explained_variance) + 1)]
        scree_customdata = np.column_stack(
            (
                cumulative_variance,
                np.full(len(explained_variance), complete_row_count),
            )
        )
        fig.add_trace(go.Bar(
            x=component_labels,
            y=explained_variance,
            marker=dict(color='#b07a1d', line=dict(color='#7c5715', width=1)),
            text=[f"{value:.1f}%" for value in explained_variance],
            textposition='auto',
            customdata=scree_customdata,
            hovertemplate=(
                '<b>%{x}</b><br>'
                'Explained variance: %{y:.1f}%<br>'
                'Cumulative variance: %{customdata[0]:.1f}%<br>'
                'Complete measurements: %{customdata[1]:.0f}<extra></extra>'
            ),
        ), row=14, col=1)

    correlation_result = _spearman_correlation_summary(df, features)
    if correlation_result is not None:
        correlation_matrix, pair_counts = correlation_result
        correlation_labels = [
            'Leak rate',
            'd13C',
            'P no acid',
            'P gasses',
            'Total CO2',
            'd18O',
            'Line',
            'Initial intensity',
        ]
        correlation_axis_labels = [feature_labels[feature] for feature in features]
        correlation_numeric = (
            df[features]
            .apply(pd.to_numeric, errors='coerce')
            .replace([np.inf, -np.inf], np.nan)
        )

        def _finite_float_or_none(value: Any) -> float | None:
            numeric_value = pd.to_numeric(value, errors='coerce')
            if pd.isna(numeric_value) or not np.isfinite(float(numeric_value)):
                return None
            return float(numeric_value)

        color_value_list = (
            list(color_values)
            if color_values is not None
            else [None] * len(df.index)
        )
        correlation_scatter_preview = {
            'kind': 'spearman-scatter-preview',
            'featureLabels': correlation_labels,
            'axisLabels': correlation_axis_labels,
            'rowLabels': df.index.astype(str).tolist(),
            'identifier1': identifier1_series.tolist(),
            'identifier2': identifier2_series.tolist(),
            'species': species_series.tolist(),
            'colorLabel': hover_color_label,
            'colorValues': [_finite_float_or_none(value) for value in color_value_list],
            'colorDisplay': hover_color_series.tolist(),
            'markerSymbols': marker_symbols,
            'values': [
                [_finite_float_or_none(value) for value in row]
                for row in correlation_numeric.to_numpy(dtype=object)
            ],
        }
        correlation_text = np.where(
            np.isfinite(correlation_matrix),
            np.vectorize(lambda value: f"{value:.2f}")(np.nan_to_num(correlation_matrix)),
            '',
        )
        fig.add_trace(go.Heatmap(
            x=correlation_labels,
            y=correlation_labels,
            z=correlation_matrix,
            zmin=-1.0,
            zmax=1.0,
            zmid=0.0,
            colorscale=[
                [0.0, '#efaa83'],
                [0.5, '#f8fafc'],
                [1.0, '#8fb6da'],
            ],
            colorbar=dict(title=dict(text='rho'), thickness=12, len=0.78),
            text=correlation_text,
            texttemplate='%{text}',
            textfont=dict(color='#0f172a', size=10),
            customdata=pair_counts,
            meta={'correlationScatterPreview': correlation_scatter_preview},
            hovertemplate=(
                '<b>%{y} vs %{x}</b><br>'
                'Spearman rho: %{z:.2f}<br>'
                'Complete pairs: %{customdata}<extra></extra>'
            ),
        ), row=14, col=2)

    # # Position the color scale only on the first subplot, adjusting its height to match one row
    # fig.update_traces(marker=dict(colorbar=dict(len=0.2, y=0.2, yanchor="bottom")), selector=dict(row=1, col=1))

    axis_titles = {
        (1, 1): ("Leak Rate", "d13C/12C Mean"),
        (1, 2): ("Leak Rate", "d18O/16O Mean"),
        (2, 1): ("P no Acid", "d13C/12C Mean"),
        (2, 2): ("P no Acid", "d18O/16O Mean"),
        (3, 1): ("Total CO2", "d13C/12C Mean"),
        (3, 2): ("Total CO2", "d18O/16O Mean"),
        (4, 1): ("Signal Intensity (Cycle 1 m/z 44)", "d13C/12C Mean"),
        (4, 2): ("Signal Intensity (Cycle 1 m/z 44)", "d18O/16O Mean"),
        (5, 1): ("Diff Signal Intensity (Cycle 1 m/z 44)", "d13C/12C Mean"),
        (5, 2): ("Diff Signal Intensity (Cycle 1 m/z 44)", "d18O/16O Mean"),
        (6, 1): ("Pressure-Adjusted Signal Intensity Diff (Cycle 1 m/z 44)", "d13C/12C Mean"),
        (6, 2): ("Pressure-Adjusted Signal Intensity Diff (Cycle 1 m/z 44)", "d18O/16O Mean"),
        (7, 1): ("Line", "d13C/12C Mean"),
        (7, 2): ("Line", "d18O/16O Mean"),
        (8, 1): ("Signal Intensity (Cycle 1 m/z 44)", "Total CO2"),
        (8, 2): ("Total CO2", "Leak Rate"),
        (9, 1): ("Line", "Leak Rate"),
        (9, 2): ("Line", "Total CO2"),
        (10, 1): ("Signal Intensity (Cycle 1 m/z 44)", "Leak Rate"),
        (10, 2): ("P no Acid", "Leak Rate"),
        (11, 1): ("P Gasses", "Leak Rate"),
        (11, 2): ("d13C/12C Mean", "d18O/16O Mean"),
        (12, 1): ("Line", "P no Acid"),
        (12, 2): ("Line", "P Gasses"),
        (13, 1): ("Line", "Initial Sample Intensity (Cycle 1 m/z 44)"),
        (13, 2): ("Contribution to Leading Variability (%)", "Parameter"),
        (14, 1): ("Principal Component", "Explained Variance (%)"),
        (14, 2): ("Parameter", "Parameter"),
    }
    for (row, col), (x_title, y_title) in axis_titles.items():
        fig.update_xaxes(title_text=x_title, row=row, col=col)
        fig.update_yaxes(title_text=y_title, row=row, col=col)
    fig.update_yaxes(rangemode="tozero", row=14, col=1)
    fig.update_xaxes(tickangle=-35, row=14, col=2)

    unified_hover_template = (
        "Identifier 1: %{customdata[1]}<br>"
        "Identifier 2: %{customdata[2]}<br>"
        "Species: %{customdata[3]}<br>"
        "Row: %{customdata[0]}<br>"
        f"{hover_color_label}: %{{customdata[4]}}<br>"
        "X: %{x}<br>"
        "Y: %{y}<extra></extra>"
    )
    base_count = len(df.index)
    for trace in fig.data:
        if not isinstance(trace, go.Scatter):
            continue
        mode = str(getattr(trace, "mode", ""))
        if "markers" not in mode:
            continue
        x_values = getattr(trace, "x", None)
        point_count = len(x_values) if x_values is not None else 0
        if point_count == base_count:
            trace.customdata = base_customdata
        else:
            continue
        trace.hovertemplate = unified_hover_template
        trace.hoverlabel = dict(namelength=-1)

    section_specs = (
        ("SAMPLE CONDITIONS", 1, 3, "#f8fafc"),
        ("SIGNAL RESPONSE", 4, 6, "#f5f8ff"),
        ("LINE EFFECTS", 7, 7, "#f8fafc"),
        ("INSTRUMENT RELATIONSHIPS", 8, 11, "#f5f8ff"),
        ("ADDITIONAL LINE EFFECTS", 12, 13, "#f8fafc"),
    )
    for section_name, first_row, last_row, fill_color in section_specs:
        first_axis_index = (first_row - 1) * 2 + 1
        last_axis_index = (last_row - 1) * 2 + 1
        first_axis_name = "yaxis" if first_axis_index == 1 else f"yaxis{first_axis_index}"
        last_axis_name = "yaxis" if last_axis_index == 1 else f"yaxis{last_axis_index}"
        first_domain = getattr(fig.layout, first_axis_name).domain
        last_domain = getattr(fig.layout, last_axis_name).domain
        section_top = float(first_domain[1])
        section_bottom = float(last_domain[0])
        fig.add_shape(
            type="rect",
            xref="paper",
            yref="paper",
            x0=-0.055,
            x1=1.0,
            y0=max(0.0, section_bottom - 0.012),
            y1=min(1.0, section_top + 0.012),
            fillcolor=fill_color,
            line=dict(color="#e2e8f0", width=1),
            layer="below",
        )
        fig.add_annotation(
            x=-0.035,
            y=(section_top + section_bottom) / 2,
            xref="paper",
            yref="paper",
            text=section_name,
            textangle=-90,
            showarrow=False,
            font=dict(size=11, color="#475569"),
            xanchor="center",
            yanchor="middle",
        )

    left_domain = fig.layout.xaxis.domain
    right_domain = fig.layout.xaxis2.domain
    fig.add_annotation(
        x=(float(left_domain[0]) + float(left_domain[1])) / 2,
        y=1.04,
        xref="paper",
        yref="paper",
        text="<b>CARBON ISOTOPE (d13C)</b>",
        showarrow=False,
        font=dict(size=13, color="#0f172a"),
    )
    fig.add_annotation(
        x=(float(right_domain[0]) + float(right_domain[1])) / 2,
        y=1.04,
        xref="paper",
        yref="paper",
        text="<b>OXYGEN ISOTOPE (d18O)</b>",
        showarrow=False,
        font=dict(size=13, color="#0f172a"),
    )

    # Update layout with room for the group rail and shared color scale.
    fig.update_layout(
        title_text=None,
        height=4520,
        showlegend=False,
        plot_bgcolor="#f8fafc",
        paper_bgcolor="#ffffff",
        margin=dict(l=120, r=150, t=80, b=80),
    )
    fig.update_xaxes(showgrid=True, gridcolor="#cbd5e1", gridwidth=1, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="#cbd5e1", gridwidth=1, zeroline=False)

    return fig


DIAGNOSTIC_GRID_SPECS: tuple[tuple[str, str, int], ...] = (
    ("Multivariate Overview", "Parameter Contributions to Variability", 26),
    ("Multivariate Overview", "Explained Variance by Component", 27),
    ("Multivariate Overview", "Spearman Correlation Matrix", 28),
    ("d13C", "d13C vs Leak Rate", 1),
    ("d13C", "d13C vs P no Acid", 3),
    ("d13C", "d13C vs Total CO2", 5),
    ("d13C", "d13C vs Initial Sample Intensity", 7),
    ("d13C", "d13C vs Diff Signal Intensity", 9),
    ("d13C", "d13C vs Pressure-Adjusted Signal Intensity Diff", 11),
    ("d18O", "d18O vs Leak Rate", 2),
    ("d18O", "d18O vs P no Acid", 4),
    ("d18O", "d18O vs Total CO2", 6),
    ("d18O", "d18O vs Initial Sample Intensity", 8),
    ("d18O", "d18O vs Diff Signal Intensity", 10),
    ("d18O", "d18O vs Pressure-Adjusted Signal Intensity Diff", 12),
    ("Leak Rate", "Leak Rate vs Total CO2", 16),
    ("Leak Rate", "Leak Rate vs Initial Sample Intensity", 19),
    ("Leak Rate", "Leak Rate vs P no Acid", 20),
    ("Leak Rate", "Leak Rate vs P Gasses", 21),
    ("Total CO2", "Total CO2 vs Initial Sample Intensity", 15),
    ("Line", "d13C vs Line", 13),
    ("Line", "d18O vs Line", 14),
    ("Line", "Leak Rate vs Line", 17),
    ("Line", "Total CO2 vs Line", 18),
    ("Line", "P no Acid vs Line", 23),
    ("Line", "P Gasses vs Line", 24),
    ("Line", "Initial Sample Intensity vs Line", 25),
    ("Isotope Comparison", "d18O vs d13C", 22),
)

MULTIVARIATE_PLOT_SUBTITLES: dict[str, str] = {
    "Parameter Contributions to Variability": "Weighted contribution across PC1 and PC2",
    "Explained Variance by Component": "Standardized parameters, complete measurements",
    "Spearman Correlation Matrix": "Pairwise-complete measurements; hover a cell to preview the scatter",
}


def split_diagnostic_plot_grid(fig) -> list[tuple[str, str, Any]]:
    """Extract the diagnostic matrix into standalone figures for a square CSS grid."""
    if go is None or fig is None:
        return []
    grid: list[tuple[str, str, Any]] = []
    for group, title, axis_index in DIAGNOSTIC_GRID_SPECS:
        x_ref = "x" if axis_index == 1 else f"x{axis_index}"
        y_ref = "y" if axis_index == 1 else f"y{axis_index}"
        trace_payloads: list[dict[str, Any]] = []
        for trace in fig.data:
            trace_x_ref = str(getattr(trace, "xaxis", "x") or "x")
            trace_y_ref = str(getattr(trace, "yaxis", "y") or "y")
            if trace_x_ref != x_ref or trace_y_ref != y_ref:
                continue
            payload = trace.to_plotly_json()
            payload["xaxis"] = "x"
            payload["yaxis"] = "y"
            marker = payload.get("marker")
            if isinstance(marker, dict):
                marker = dict(marker)
                marker["showscale"] = False
                marker.pop("colorbar", None)
                payload["marker"] = marker
            payload["showlegend"] = False
            trace_payloads.append(payload)

        x_axis_name = "xaxis" if axis_index == 1 else f"xaxis{axis_index}"
        y_axis_name = "yaxis" if axis_index == 1 else f"yaxis{axis_index}"
        source_xaxis = getattr(fig.layout, x_axis_name, None)
        source_yaxis = getattr(fig.layout, y_axis_name, None)
        xaxis = source_xaxis.to_plotly_json() if source_xaxis is not None else {}
        yaxis = source_yaxis.to_plotly_json() if source_yaxis is not None else {}
        for axis in (xaxis, yaxis):
            axis.pop("domain", None)
            axis.pop("anchor", None)
            axis_title = axis.get("title", {})
            if isinstance(axis_title, str):
                axis_title = {"text": axis_title}
            elif not isinstance(axis_title, dict):
                axis_title = {}
            axis_title_font = axis_title.get("font", {})
            if not isinstance(axis_title_font, dict):
                axis_title_font = {}
            axis_title["font"] = {**axis_title_font, "size": 11, "color": "#475569"}
            axis_title["standoff"] = 9

            tick_font = axis.get("tickfont", {})
            if not isinstance(tick_font, dict):
                tick_font = {}
            axis.update(
                {
                    "automargin": True,
                    "title": axis_title,
                    "tickfont": {**tick_font, "size": 10, "color": "#64748b"},
                    "showgrid": True,
                    "gridcolor": "#cbd5e1",
                    "gridwidth": 1,
                    "zeroline": False,
                }
            )

        subtitle = MULTIVARIATE_PLOT_SUBTITLES.get(title)
        title_text = f"<b>{title}</b>"
        if subtitle:
            title_text += f"<br><span style='font-size:11px;color:#64748b'>{subtitle}</span>"

        standalone = go.Figure(data=trace_payloads)
        standalone.update_layout(
            title=dict(
                text=title_text,
                x=0.5,
                xanchor="center",
                font=dict(size=16, color="#172554"),
                pad=dict(b=8),
            ),
            xaxis=xaxis,
            yaxis=yaxis,
            autosize=True,
            showlegend=False,
            hovermode="closest",
            plot_bgcolor="#f8fafc",
            paper_bgcolor="#ffffff",
            margin=dict(l=54, r=18, t=70 if subtitle else 54, b=50),
        )
        grid.append((group, title, standalone))
    return grid
