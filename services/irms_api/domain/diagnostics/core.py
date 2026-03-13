
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

from ..shared.plotting import _build_date_colorbar_ticks, _prepare_color_values

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


    # Create a subplot with 5 rows and 3 columns
    fig = make_subplots(
        rows=7, cols=3,
        subplot_titles=(
            'Leak Rate vs d13C', 'P no Acid vs d13C', 'Total CO2 vs d13C',
            'Leak Rate vs d18O', 'P no Acid vs d18O', 'Total CO2 vs d18O',
            'Leak Rate vs Line', 'Signal Intensity vs pCO2', 'Signal Intensity vs d13C',
            'Signal Intensity vs d18O', 'd13C vs Line', 'd18O vs Line',
            'Leak Rate vs pCO2', 'd13C vs d18O', 'Total CO2 vs Line',
            'Leak Rate vs Signal Intensity', 'P no Acid vs Leak Rate', 'P Gasses vs Leak Rate',
            'PCA: Principal Components'
        ),
        vertical_spacing=0.03,
        specs=[[{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'scatter'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'scatter'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'box'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'box'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'scatter'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'scatter'}],
               [{'type': 'scatter'}, {'type': 'scatter'}, {'type': 'scatter'}]]
    )

    # Ensure the required columns are present in the DataFrame
    required_columns = ['leak_rate', 'd 13C/12C  Mean', 'p_no_acid', 'total_co2', 'd 18O/16O  Mean', 'Line',
                        '1  Cycle Int  Samp  44', 'p_gases', 'Identifier 1']
    if color_param not in df.columns:
        raise ValueError(f"Selected color parameter '{color_param}' is missing from the DataFrame.")
    for col in required_columns:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    # Set marker styles based on whether Identifier 1 is in the standards list
    marker_symbols = ['circle-open' if id in standards_list else 'circle' for id in df['Identifier 1']]
    hover_text = df['Identifier 2']

    # Build colorbar configuration for the first trace (readable dates if needed)
    colorbar_cfg = dict(
        title=dict(
            text='Date' if color_param == 'Date_ordinal' else color_param,
            side='right',
        ),
        thickness=20,
        len=0.75,  # Longer colorbar
        y=0.5,     # Center vertically
        yanchor='middle',
        x=1.15,    # Move further right
        xanchor='right'
    )
    color_values, colorbar_category_ticks = _prepare_color_values(df[color_param])
    if color_param == 'Date_ordinal' and color_param in df.columns:
        tickvals, ticktext = _build_date_colorbar_ticks(df[color_param])
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
    fig.add_trace(go.Box(x=df['Line'], y=df['d 13C/12C  Mean']), row=4, col=2)
    fig.add_trace(go.Box(x=df['Line'], y=df['d 18O/16O  Mean']), row=4, col=3)

    fig.add_trace(go.Scatter(x=df['leak_rate'], y=df['total_co2'], mode='markers', marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=5, col=1)
    fig.add_trace(go.Scatter(x=df['d 13C/12C  Mean'], y=df['d 18O/16O  Mean'], mode='markers', marker=dict(color=color_values, symbol=marker_symbols, colorscale='Viridis', showscale=False), text=hover_text,
        hoverinfo='text+x+y'), row=5, col=2)
    fig.add_trace(go.Box(x=df['Line'], y=df['total_co2']), row=5, col=3)



    # Add scatter plots with coloring by selected parameter, adjusting marker style for standards
    fig.add_trace(go.Scatter(
        x=df['leak_rate'], y=df['1  Cycle Int  Samp  44'], mode='markers',
        marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=6, col=1)

    fig.add_trace(go.Scatter(
        x=df['p_no_acid'], y=df['leak_rate'], mode='markers',
        marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=6, col=2)

    fig.add_trace(go.Scatter(
        x=df['p_gases'], y=df['leak_rate'], mode='markers',
        marker=dict(color=color_values, colorscale='Viridis', symbol=marker_symbols, showscale=False), text=hover_text,
        hoverinfo='text+x+y'
    ), row=6, col=3)

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
    if n_components == 2:
        pca_color = color_values.loc[X.index] if color_values is not None else df.loc[X.index, color_param]
        pca_hover = df.loc[X.index, 'Identifier 2']
        fig.add_trace(go.Scatter(
            x=components[:, 0], y=components[:, 1], mode='markers',
            marker=dict(color=pca_color, colorscale='Viridis', symbol=marker_symbols, showscale=False),
            text=pca_hover, hoverinfo='text+x+y'
        ), row=7, col=1)

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
                row=7, col=1
            )
            fig.add_annotation(
                x=loadings[i, 0],  # Loading for the first component (x)
                y=loadings[i, 1],  # Loading for the second component (y)
                xanchor="center",  # Center the x-axis label
                yanchor="bottom",  # Bottom-align the y-axis label
                text=feature,  # The feature name as annotation text
                yshift=5,  # Adjust the y-position to avoid overlap
                row=7, col=1
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
        (4, 2): ("Line", "d13C/12C Mean"),
        (4, 3): ("Line", "d18O/16O Mean"),
        (5, 1): ("Leak Rate", "Total CO2"),
        (5, 2): ("d13C/12C Mean", "d18O/16O Mean"),
        (5, 3): ("Line", "Total CO2"),
        (6, 1): ("Leak Rate", "Signal Intensity (Cycle 1 m/z 44)"),
        (6, 2): ("P no Acid", "Leak Rate"),
        (6, 3): ("P Gasses", "Leak Rate"),
        (7, 1): ("Principal Component 1", "Principal Component 2"),
    }
    for (row, col), (x_title, y_title) in axis_titles.items():
        fig.update_xaxes(title_text=x_title, row=row, col=col)
        fig.update_yaxes(title_text=y_title, row=row, col=col)

    # Update layout with right margin for colorbar
    fig.update_layout(
        title_text='Diagnostic Plots',
        height=2600,
        showlegend=False,
        margin=dict(r=150)  # Add right margin for colorbar
    )

    return fig
