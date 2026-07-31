
from __future__ import annotations

import numpy as np
import pandas as pd
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
from ..constants import CYCLE1_SIGNAL_DIFF44_COL, CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL


def _color_param_label(color_param: str) -> str:
    return "Date" if _is_date_color_column(color_param) else str(color_param)


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

def create_diagnostic_plots(df, color_param, standards_file='standards.csv'):
    """
    Create diagnostic plots for analysis with the option to color points by a selected parameter.
    Parameters:
        - df (pd.DataFrame): DataFrame containing the data.
        - color_param (str): The column name to use for coloring the scatter plot markers.
    """

    # Load standards from CSV
    try:
        standards_df = pd.read_csv(standards_file)
        standards_list = standards_df['Standard'].unique()
    except Exception as e:
        raise ValueError(f"Error loading standards from {standards_file}: {e}")


    diff_signal_col = CYCLE1_SIGNAL_DIFF44_COL
    pressure_adjusted_diff_col = CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL

    # Create a subplot with 8 rows and 3 columns.
    # Keep diff-intensity diagnostics grouped immediately after "Signal Intensity vs d18O".
    fig = make_subplots(
        rows=8, cols=3,
        subplot_titles=(
            'Leak Rate vs d13C', 'P no Acid vs d13C', 'Total CO2 vs d13C',
            'Leak Rate vs d18O', 'P no Acid vs d18O', 'Total CO2 vs d18O',
            'Leak Rate vs Line', 'Signal Intensity vs pCO2', 'Signal Intensity vs d13C',
            'Signal Intensity vs d18O', 'd18O vs Diff Signal Intensity', 'd13C vs Diff Signal Intensity',
            'd18O vs Pressure-Adjusted Signal Intensity Diff', 'd13C vs Pressure-Adjusted Signal Intensity Diff', 'd13C vs Line',
            'd18O vs Line', 'Leak Rate vs pCO2', 'd13C vs d18O',
            'Total CO2 vs Line', 'Leak Rate vs Signal Intensity', 'P no Acid vs Leak Rate',
            'P Gasses vs Leak Rate', 'PCA: Principal Components',
        ),
        vertical_spacing=0.03,
        specs=[[{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'scatter'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'scatter'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'box'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'scatter'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'scatter'}],
               [{'type': 'box'}, {'type': 'scatter'}, {'type': 'scatter'}],
               [{'type': 'box'}, {'type': 'scatter'}, {'type': 'scatter'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, None]]
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

    # Set marker styles based on whether Identifier 1 is in the standards list
    marker_symbols = ['circle-open' if id in standards_list else 'circle' for id in df['Identifier 1']]
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
        hoverinfo='text+x+y'), row=1, col=2)
    fig.add_trace(go.Scatter(x=df['total_co2'], y=df['d 13C/12C  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=1, col=3)

    fig.add_trace(go.Scatter(x=df['leak_rate'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=2, col=1)
    fig.add_trace(go.Scatter(x=df['p_no_acid'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=2, col=2)
    fig.add_trace(go.Scatter(x=df['total_co2'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=2, col=3)

    fig.add_trace(go.Box(x=df['Line'], y=df['leak_rate']), row=3, col=1)

    fig.add_trace(go.Scatter(x=df['1  Cycle Int  Samp  44'], y=df['total_co2'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=3, col=2)

    # Prepare x_data and y_data with valid (non-NaN, non-inf) values for fitting
    x_data = df['1  Cycle Int  Samp  44']
    y_data = df['total_co2']

    # Remove NaN and infinite values from x_data and y_data
    valid_data = np.isfinite(x_data) & np.isfinite(y_data)
    x_data_clean = x_data[valid_data]
    y_data_clean = y_data[valid_data]

    # Check if there is sufficient data after cleaning for a quadratic fit
    if len(x_data_clean) >= 3:
        # Fit quadratic polynomial (2nd degree)
        coeffs = np.polyfit(x_data_clean, y_data_clean, 2)  # coeffs = [a, b, c]
        quadratic_curve = np.polyval(coeffs, x_data_clean)  # Evaluate polynomial at cleaned x_data points

        # Sort x_data_clean and quadratic_curve to ensure the line is smooth
        sorted_indices = np.argsort(x_data_clean)
        x_data_sorted = x_data_clean.iloc[sorted_indices]
        quadratic_curve_sorted = quadratic_curve[sorted_indices]

    # Plot the sorted quadratic fit as a line (only if fit succeeded)
    if len(x_data_clean) >= 3:
        fig.add_trace(go.Scatter(
            x=x_data_sorted, y=quadratic_curve_sorted, mode='lines', name='Quadratic Fit',
            line=dict(color='red', dash='dash')
        ), row=3, col=2)

    fig.add_trace(go.Scatter(x=df['1  Cycle Int  Samp  44'], y=df['d 13C/12C  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=3, col=3)

    fig.add_trace(go.Scatter(x=df['1  Cycle Int  Samp  44'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=4, col=1)
    fig.add_trace(go.Box(x=df['Line'], y=df['d 13C/12C  Mean']), row=5, col=3)
    fig.add_trace(go.Box(x=df['Line'], y=df['d 18O/16O  Mean']), row=6, col=1)

    fig.add_trace(go.Scatter(x=df['leak_rate'], y=df['total_co2'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=6, col=2)
    fig.add_trace(go.Scatter(x=df['d 13C/12C  Mean'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, symbol=marker_symbols, colorscale='Viridis', showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=6, col=3)
    fig.add_trace(go.Box(x=df['Line'], y=df['total_co2']), row=7, col=1)



    # Add scatter plots with coloring by selected parameter, adjusting marker style for standards
    fig.add_trace(go.Scatter(
        x=df['leak_rate'], y=df['1  Cycle Int  Samp  44'], mode='markers',
        marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=7, col=2)

    fig.add_trace(go.Scatter(
        x=df['p_no_acid'], y=df['leak_rate'], mode='markers',
        marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=7, col=3)

    fig.add_trace(go.Scatter(
        x=df['p_gases'], y=df['leak_rate'], mode='markers',
        marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=8, col=1)

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
            row=4,
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
            row=4,
            col=3,
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
            row=5,
            col=1,
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
            row=5,
            col=2,
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
        ("p_no_acid", "d 13C/12C  Mean", 1, 2),
        ("total_co2", "d 13C/12C  Mean", 1, 3),
        ("leak_rate", "d 18O/16O  Mean", 2, 1),
        ("p_no_acid", "d 18O/16O  Mean", 2, 2),
        ("total_co2", "d 18O/16O  Mean", 2, 3),
        ("1  Cycle Int  Samp  44", "total_co2", 3, 2),
        ("1  Cycle Int  Samp  44", "d 13C/12C  Mean", 3, 3),
        ("1  Cycle Int  Samp  44", "d 18O/16O  Mean", 4, 1),
        ("leak_rate", "total_co2", 6, 2),
        ("d 13C/12C  Mean", "d 18O/16O  Mean", 6, 3),
        ("leak_rate", "1  Cycle Int  Samp  44", 7, 2),
        ("p_no_acid", "leak_rate", 7, 3),
        ("p_gases", "leak_rate", 8, 1),
    ]
    if diff_signal_col in df.columns:
        partial_overlay_axes.extend(
            [
                (diff_signal_col, "d 18O/16O  Mean", 4, 2),
                (diff_signal_col, "d 13C/12C  Mean", 4, 3),
            ]
        )
    if pressure_adjusted_diff_col in df.columns:
        partial_overlay_axes.extend(
            [
                (pressure_adjusted_diff_col, "d 18O/16O  Mean", 5, 1),
                (pressure_adjusted_diff_col, "d 13C/12C  Mean", 5, 2),
            ]
        )
    for x_col, y_col, row, col in partial_overlay_axes:
        _add_partial_overlay(x_col, y_col, row=row, col=col)

    # Perform PCA
    features = ['leak_rate', 'd 13C/12C  Mean', 'p_no_acid', 'total_co2', 'd 18O/16O  Mean', 'Line',
                '1  Cycle Int  Samp  44']
    X = df[features].dropna()
    if X.empty:
        fig.update_layout(
            title_text='Diagnostic Plots',
            height=2600,
            showlegend=False,
            margin=dict(r=150)
        )
        return fig

    # Standardize the data
    X_scaled = StandardScaler().fit_transform(X)

    # Adjust n_components based on the data
    n_samples, n_features = X_scaled.shape
    n_components = min(2, n_samples, n_features)  # Ensure n_components <= min(n_samples, n_features)

    # Apply PCA
    pca = PCA(n_components=n_components)
    components = pca.fit_transform(X_scaled)

    # Calculate loadings
    loadings = pca.components_.T * np.sqrt(pca.explained_variance_)

    # Scatter plot for PCA components
    pca_customdata = None
    if n_components == 2:
        pca_color = color_values.loc[X.index] if color_values is not None else df.loc[X.index, color_param]
        pca_hover = df.loc[X.index, 'Identifier 2']
        pca_customdata = _build_customdata_for_index(X.index)
        fig.add_trace(go.Scatter(
            x=components[:, 0], y=components[:, 1], mode='markers',
            marker=dict(color=pca_color, colorscale='Viridis', symbol=marker_symbols, showscale=False),
            text=pca_hover, hoverinfo='text+x+y'
        ), row=8, col=2)

        # Add loadings as annotations
        for i, feature in enumerate(features):
            fig.add_annotation(
                x=loadings[i, 0],  # Loading for the first component (x)
                y=loadings[i, 1],  # Loading for the second component (y)
                ax=0, ay=0,  # Starting point for the arrow (origin)
                axref="x", ayref="y",  # Reference the x and y axes for arrow positioning
                showarrow=True,  # Display the arrow
                arrowsize=2,  # Set arrow size
                arrowhead=2,  # Set arrowhead style
                xanchor="right",  # Anchor the x-axis to the right side
                yanchor="top",  # Anchor the y-axis to the top side
                row=8, col=2
            )
            fig.add_annotation(
                x=loadings[i, 0],  # Loading for the first component (x)
                y=loadings[i, 1],  # Loading for the second component (y)
                xanchor="center",  # Center the x-axis label
                yanchor="bottom",  # Bottom-align the y-axis label
                text=feature,  # The feature name as annotation text
                yshift=5,  # Adjust the y-position to avoid overlap
                row=8, col=2
            )

    # # Position the color scale only on the first subplot, adjusting its height to match one row
    # fig.update_traces(marker=dict(colorbar=dict(len=0.2, y=0.2, yanchor="bottom")), selector=dict(row=1, col=1))

    axis_titles = {
        (1, 1): ("Leak Rate", "d13C/12C Mean"),
        (1, 2): ("P no Acid", "d13C/12C Mean"),
        (1, 3): ("Total CO2", "d13C/12C Mean"),
        (2, 1): ("Leak Rate", "d18O/16O Mean"),
        (2, 2): ("P no Acid", "d18O/16O Mean"),
        (2, 3): ("Total CO2", "d18O/16O Mean"),
        (3, 1): ("Line", "Leak Rate"),
        (3, 2): ("Signal Intensity (Cycle 1 m/z 44)", "Total CO2"),
        (3, 3): ("Signal Intensity (Cycle 1 m/z 44)", "d13C/12C Mean"),
        (4, 1): ("Signal Intensity (Cycle 1 m/z 44)", "d18O/16O Mean"),
        (4, 2): ("Diff Signal Intensity (Cycle 1 m/z 44)", "d18O/16O Mean"),
        (4, 3): ("Diff Signal Intensity (Cycle 1 m/z 44)", "d13C/12C Mean"),
        (5, 1): ("Pressure-Adjusted Signal Intensity Diff (Cycle 1 m/z 44)", "d18O/16O Mean"),
        (5, 2): ("Pressure-Adjusted Signal Intensity Diff (Cycle 1 m/z 44)", "d13C/12C Mean"),
        (5, 3): ("Line", "d13C/12C Mean"),
        (6, 1): ("Line", "d18O/16O Mean"),
        (6, 2): ("Leak Rate", "Total CO2"),
        (6, 3): ("d13C/12C Mean", "d18O/16O Mean"),
        (7, 1): ("Line", "Total CO2"),
        (7, 2): ("Leak Rate", "Signal Intensity (Cycle 1 m/z 44)"),
        (7, 3): ("P no Acid", "Leak Rate"),
        (8, 1): ("P Gasses", "Leak Rate"),
        (8, 2): ("Principal Component 1", "Principal Component 2"),
    }
    for (row, col), (x_title, y_title) in axis_titles.items():
        fig.update_xaxes(title_text=x_title, row=row, col=col)
        fig.update_yaxes(title_text=y_title, row=row, col=col)

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
    pca_count = len(X.index)
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
        elif pca_customdata is not None and point_count == pca_count:
            trace.customdata = pca_customdata
        else:
            continue
        trace.hovertemplate = unified_hover_template
        trace.hoverlabel = dict(namelength=-1)

    # Update layout with right margin for colorbar
    fig.update_layout(
        title_text='Diagnostic Plots',
        height=2600,
        showlegend=False,
        margin=dict(r=150)  # Add right margin for colorbar
    )

    return fig
