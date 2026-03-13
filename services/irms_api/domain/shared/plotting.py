
from __future__ import annotations

import numpy as np
import pandas as pd

try:
    import plotly.graph_objects as go
except ModuleNotFoundError:  # pragma: no cover - optional for logic-only tests
    go = None

def _build_date_colorbar_ticks(values, n=6, date_format='%Y-%m-%d'):
    try:
        s = pd.to_numeric(pd.Series(values), errors='coerce').dropna()
    except Exception:
        return None, None
    if s.empty:
        return None, None
    vmin, vmax = float(s.min()), float(s.max())
    if not np.isfinite(vmin) or not np.isfinite(vmax):
        return None, None
    # Evenly spaced tick locations across the ordinal range
    tickvals = np.linspace(vmin, vmax, int(max(2, n)))
    # Convert ordinal numbers back to date strings
    ticktext = []
    for v in tickvals:
        try:
            # Round to the nearest day to avoid fractional ordinals
            ts = pd.Timestamp.fromordinal(int(round(v)))
            ticktext.append(ts.strftime(date_format))
        except Exception:
            ticktext.append(str(v))
    return tickvals.tolist(), ticktext

def _prepare_color_values(values):
    """Coerce color values to numeric, with categorical fallback + ticks."""
    if values is None:
        return None, None
    series = pd.Series(values)
    numeric = pd.to_numeric(series, errors='coerce')
    if numeric.notna().any():
        return numeric, None
    categories = series.where(series.notna(), 'Unknown').astype(str)
    codes, uniques = pd.factorize(categories, sort=True)
    ticks = (list(range(len(uniques))), [str(u) for u in uniques])
    return pd.Series(codes, index=series.index), ticks

def _exclusive_outlier_masks(mask_items):
    """Return mutually-exclusive boolean masks in priority order."""
    exclusive = {}
    assigned = None
    for name, mask in mask_items:
        mask_series = pd.Series(mask).fillna(False).astype(bool)
        if assigned is None:
            exclusive[name] = mask_series
            assigned = mask_series.copy()
        else:
            exclusive[name] = mask_series & ~assigned
            assigned = assigned | mask_series
    return exclusive

def _compose_label_series(identifier_series, species_series):
    """Compose labels as 'Identifier 1 - Species' when species exists."""
    ids = pd.Series(identifier_series).fillna('').astype(str).str.strip()
    species = pd.Series(species_series).fillna('').astype(str).str.strip()
    labels = ids
    has_species = species != ''
    labels = labels.where(~has_species, ids + ' - ' + species)
    labels = labels.replace({'': 'Unknown'})
    return labels

def _build_delta_point_customdata(df, isotope_key):
    """Attach row/index metadata to chart points so clicks can edit source rows."""
    if df is None or df.empty:
        return None
    idx_vals = pd.Series(df.index, index=df.index).astype(str).to_numpy()
    id1_vals = df.get('Identifier 1', pd.Series(index=df.index, dtype=object)).fillna('').astype(str).to_numpy()
    id2_vals = df.get('Identifier 2', pd.Series(index=df.index, dtype=object)).fillna('').astype(str).to_numpy()
    iso_vals = np.full(len(df), str(isotope_key), dtype=object)
    return np.column_stack((idx_vals, iso_vals, id1_vals, id2_vals))

def _build_cycle_std_lookups(df):
    """Build per-row lookups for cycle-derived d13C/d18O standard deviations."""
    if df is None or getattr(df, "empty", True):
        return {}, {}

    def _lookup_for(col_name, used_col_name):
        values = pd.to_numeric(df.get(col_name, pd.Series(index=df.index, dtype=float)), errors='coerce')
        used = pd.to_numeric(df.get(used_col_name, pd.Series(index=df.index, dtype=float)), errors='coerce')
        valid_cycle_sd = used >= 2
        status = df.get('Collector Status', pd.Series(index=df.index, dtype=object)).fillna('').astype(str).str.strip()
        valid_cycle_sd = valid_cycle_sd & ~status.isin(['Fully Saturated Collectors', 'Failed Sample'])
        return {
            str(row_label): float(sd_val)
            for row_label, sd_val in values.items()
            if (
                pd.notna(sd_val)
                and np.isfinite(sd_val)
                and float(sd_val) >= 0.0
                and bool(valid_cycle_sd.get(row_label, False))
            )
        }

    return (
        _lookup_for('d 13C/12C  Std Dev', 'd13C Cycles Used'),
        _lookup_for('d 18O/16O  Std Dev', 'd18O Cycles Used'),
    )

def _build_plotly_error_bar(row_tokens, std_lookup):
    """Create Plotly error bar settings from row-label -> std lookup."""
    if row_tokens is None or std_lookup is None:
        return None
    error_vals = []
    has_value = False
    for row_token in row_tokens:
        sd_val = std_lookup.get(str(row_token), np.nan)
        if pd.notna(sd_val) and np.isfinite(sd_val):
            error_vals.append(float(sd_val))
            has_value = True
        else:
            error_vals.append(np.nan)
    if not has_value:
        return None
    return dict(
        type='data',
        array=error_vals,
        visible=True,
        thickness=1.5,
        width=3,
        color='rgba(40,40,40,0.9)',
    )

def _build_plotly_error_bar_for_df(df, isotope_key, d13_std_lookup, d18_std_lookup):
    """Create an isotope-specific error bar dict for the provided dataframe rows."""
    if df is None or getattr(df, "empty", True):
        return None
    row_tokens = pd.Series(df.index, index=df.index).astype(str).tolist()
    if isotope_key == 'd13C':
        return _build_plotly_error_bar(row_tokens, d13_std_lookup)
    if isotope_key == 'd18O':
        return _build_plotly_error_bar(row_tokens, d18_std_lookup)
    return None

def _apply_cycle_std_error_bars(fig, d13_std_lookup, d18_std_lookup):
    """Attach cycle-derived std-dev error bars to traces that carry row customdata."""
    if fig is None:
        return

    def _coerce_customdata_2d(customdata):
        if customdata is None:
            return None
        if hasattr(customdata, 'tolist'):
            customdata = customdata.tolist()
        if isinstance(customdata, np.ndarray) and customdata.ndim == 2:
            return customdata.astype(object, copy=False)
        if not isinstance(customdata, (list, tuple, np.ndarray)):
            return None
        if len(customdata) == 0:
            return None

        first = customdata[0]
        if isinstance(first, (list, tuple, np.ndarray)):
            rows = []
            for item in customdata:
                if not isinstance(item, (list, tuple, np.ndarray)):
                    continue
                row = list(item)
                if len(row) >= 2:
                    rows.append(row)
            if not rows:
                return None
            return np.asarray(rows, dtype=object)

        row = list(customdata)
        if len(row) < 2:
            return None
        return np.asarray([row], dtype=object)

    for trace in fig.data:
        customdata = getattr(trace, 'customdata', None)
        custom_arr = _coerce_customdata_2d(customdata)
        if custom_arr is None:
            continue
        if custom_arr.ndim != 2 or custom_arr.shape[0] == 0:
            continue

        row_tokens = custom_arr[:, 0]
        iso_vals = (
            {str(v).strip() for v in custom_arr[:, 1]}
            if custom_arr.shape[1] > 1
            else set()
        )
        if len(iso_vals) != 1:
            continue
        iso_key = next(iter(iso_vals))

        if iso_key == 'd13C':
            err_y = _build_plotly_error_bar(row_tokens, d13_std_lookup)
            if err_y:
                trace.error_y = err_y
        elif iso_key == 'd18O':
            err_y = _build_plotly_error_bar(row_tokens, d18_std_lookup)
            if err_y:
                trace.error_y = err_y
        elif iso_key == 'cross':
            err_x = _build_plotly_error_bar(row_tokens, d18_std_lookup)
            err_y = _build_plotly_error_bar(row_tokens, d13_std_lookup)
            if err_x:
                trace.error_x = err_x
            if err_y:
                trace.error_y = err_y

def _build_isotope_3d_scatter(
    df,
    z_col,
    z_label=None,
    color_col=None,
    color_label=None,
    title=None,
    open_circle_identifier=None,
    include_row_metadata=False,
    isotope_key="cross",
):
    """Build a 3D scatter chart with d18O, d13C, and a selectable Z-axis parameter."""
    if df is None or df.empty:
        return None, "No data available for 3D plotting."

    required = ['d 18O/16O  Mean', 'd 13C/12C  Mean', z_col]
    missing = [col for col in required if col not in df.columns]
    if missing:
        return None, f"Missing required column(s): {', '.join(missing)}"

    x_vals = pd.to_numeric(df['d 18O/16O  Mean'], errors='coerce')
    y_vals = pd.to_numeric(df['d 13C/12C  Mean'], errors='coerce')
    z_vals = pd.to_numeric(df[z_col], errors='coerce')
    valid = np.isfinite(x_vals) & np.isfinite(y_vals) & np.isfinite(z_vals)
    if not valid.any():
        return None, "No rows with finite d18O, d13C, and selected Z-axis values."

    plot_df = df.loc[valid].copy()
    x_vals = pd.to_numeric(plot_df['d 18O/16O  Mean'], errors='coerce')
    y_vals = pd.to_numeric(plot_df['d 13C/12C  Mean'], errors='coerce')
    z_vals = pd.to_numeric(plot_df[z_col], errors='coerce')

    has_numeric_color = False
    numeric_colors = pd.Series(index=plot_df.index, dtype=float)
    colorbar_cfg = None
    color_min = None
    color_max = None
    solid_color = 'rgba(70, 130, 180, 0.85)'
    if color_col and color_col in plot_df.columns:
        color_values, colorbar_category_ticks = _prepare_color_values(plot_df[color_col])
        numeric_colors = pd.to_numeric(color_values, errors='coerce') if color_values is not None else pd.Series(dtype=float)
        if color_values is not None and numeric_colors.notna().any():
            colorbar_cfg = dict(
                title=dict(
                    text='Date' if color_col == 'Date_ordinal' else (color_label or color_col),
                    side='right',
                ),
                thickness=18,
                len=0.7,
                y=0.5,
                yanchor='middle'
            )
            if color_col == 'Date_ordinal':
                tickvals, ticktext = _build_date_colorbar_ticks(plot_df[color_col])
                if tickvals and ticktext:
                    colorbar_cfg.update(tickmode='array', tickvals=tickvals, ticktext=ticktext)
            elif colorbar_category_ticks is not None:
                tickvals, ticktext = colorbar_category_ticks
                if tickvals and ticktext:
                    colorbar_cfg.update(tickmode='array', tickvals=tickvals, ticktext=ticktext)
            has_numeric_color = True
            color_min = float(numeric_colors.min())
            color_max = float(numeric_colors.max())
        else:
            numeric_colors = pd.Series(index=plot_df.index, dtype=float)

    id1_series = plot_df.get('Identifier 1', pd.Series(plot_df.index, index=plot_df.index)).fillna('').astype(str)
    id2_series = plot_df.get('Identifier 2', pd.Series(plot_df.index, index=plot_df.index)).fillna('').astype(str)
    standard_mask = (
        id1_series.str.strip().str.upper().eq(str(open_circle_identifier).strip().upper()).to_numpy(dtype=bool)
        if open_circle_identifier is not None
        else np.zeros(len(plot_df), dtype=bool)
    )
    non_standard_mask = ~standard_mask
    x_array = x_vals.to_numpy()
    y_array = y_vals.to_numpy()
    z_array = z_vals.to_numpy()
    row_array = plot_df.index.astype(str).to_numpy()
    id1_array = id1_series.to_numpy()
    id2_array = id2_series.to_numpy()
    color_array = numeric_colors.to_numpy() if has_numeric_color else None
    z_axis_title = z_label or z_col

    fig = go.Figure()

    def _add_trace(mask, *, symbol, name, show_scale):
        if not np.any(mask):
            return
        marker = dict(size=5, opacity=0.85, symbol=symbol)
        if has_numeric_color and color_array is not None:
            marker.update(
                color=color_array[mask],
                colorscale='Viridis',
                showscale=show_scale,
                cmin=color_min,
                cmax=color_max,
            )
            if show_scale and colorbar_cfg is not None:
                marker['colorbar'] = colorbar_cfg
        else:
            marker.update(color=solid_color, showscale=False)
        if include_row_metadata:
            customdata = np.column_stack(
                [
                    row_array[mask],
                    np.full(int(np.sum(mask)), str(isotope_key), dtype=object),
                    id1_array[mask],
                    id2_array[mask],
                ]
            )
            hovertemplate = (
                "Identifier 1: %{customdata[2]}<br>"
                "Identifier 2: %{customdata[3]}<br>"
                "Row: %{customdata[0]}<br>"
                "d18O: %{x:.3f}<br>"
                "d13C: %{y:.3f}<br>"
                f"{z_axis_title}: %{{z:.3f}}<extra></extra>"
            )
        else:
            customdata = np.column_stack([id1_array[mask], id2_array[mask]])
            hovertemplate = (
                "Identifier 1: %{customdata[0]}<br>"
                "Identifier 2: %{customdata[1]}<br>"
                "d18O: %{x:.3f}<br>"
                "d13C: %{y:.3f}<br>"
                f"{z_axis_title}: %{{z:.3f}}<extra></extra>"
            )
        fig.add_trace(go.Scatter3d(
            x=x_array[mask],
            y=y_array[mask],
            z=z_array[mask],
            mode='markers',
            name=name,
            marker=marker,
            showlegend=bool(np.any(standard_mask)),
            customdata=customdata,
            hovertemplate=hovertemplate
        ))

    if has_numeric_color and np.any(non_standard_mask):
        _add_trace(non_standard_mask, symbol='circle', name='Samples', show_scale=True)
        _add_trace(standard_mask, symbol='circle-open', name='SHP2L', show_scale=False)
    elif has_numeric_color:
        _add_trace(standard_mask, symbol='circle-open', name='SHP2L', show_scale=True)
    else:
        _add_trace(non_standard_mask, symbol='circle', name='Samples', show_scale=False)
        _add_trace(standard_mask, symbol='circle-open', name='SHP2L', show_scale=False)
    fig.update_layout(
        title=title or "d18O vs d13C (3D)",
        scene=dict(
            xaxis_title="d18O (per mil)",
            yaxis_title="d13C (per mil)",
            zaxis_title=z_axis_title
        ),
        height=850,
        margin=dict(l=10, r=80, t=60, b=10)
    )
    return fig, None
