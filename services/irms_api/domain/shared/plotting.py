
from __future__ import annotations

import numpy as np
import pandas as pd

try:
    import plotly.graph_objects as go
except ModuleNotFoundError:  # pragma: no cover - optional for logic-only tests
    go = None

_SPECIES_SYMBOL_SEQUENCE = (
    "circle",
    "square",
    "diamond",
    "cross",
    "x",
    "circle-open",
    "square-open",
    "diamond-open",
)


def _clean_species_label(value):
    text = '' if value is None else str(value).strip()
    if not text or text.lower() == 'nan':
        return 'Unknown'
    return text


def _build_species_symbol_map(species_values):
    series = pd.Series(species_values if species_values is not None else [], dtype=object)
    if series.empty:
        return {'Unknown': _SPECIES_SYMBOL_SEQUENCE[0]}
    labels = sorted({_clean_species_label(value) for value in series.tolist()})
    if not labels:
        labels = ['Unknown']
    return {
        label: _SPECIES_SYMBOL_SEQUENCE[idx % len(_SPECIES_SYMBOL_SEQUENCE)]
        for idx, label in enumerate(labels)
    }


def _species_symbol_for_label(label, symbol_map=None):
    key = _clean_species_label(label)
    if isinstance(symbol_map, dict):
        if key in symbol_map:
            return symbol_map[key]
        if 'Unknown' in symbol_map:
            return symbol_map['Unknown']
    return _SPECIES_SYMBOL_SEQUENCE[0]


def _normalize_color_key(value):
    return ''.join(ch.lower() for ch in str(value or '') if ch.isalnum())

def _is_date_color_column(color_col):
    return _normalize_color_key(color_col) in {'date', 'dateordinal'}

def _prefer_datetime_color_values(color_col):
    return _normalize_color_key(color_col) == 'date'

def _build_date_colorbar_ticks(values, n=6, date_format='%Y-%m-%d'):
    try:
        s = pd.to_numeric(pd.Series(values), errors='coerce').dropna()
    except Exception:
        return None, None
    if s.empty:
        return None, None
    ordinals = sorted({int(round(v)) for v in s.tolist() if np.isfinite(v)})
    if not ordinals:
        return None, None
    if len(ordinals) <= int(max(2, n)):
        selected = ordinals
    else:
        idx = np.linspace(0, len(ordinals) - 1, int(max(2, n)))
        selected = []
        seen = set()
        for i in idx:
            ordinal = ordinals[int(round(i))]
            if ordinal in seen:
                continue
            selected.append(ordinal)
            seen.add(ordinal)
        if selected[0] != ordinals[0]:
            selected = [ordinals[0], *selected]
        if selected[-1] != ordinals[-1]:
            selected = [*selected, ordinals[-1]]

    tickvals = [float(v) for v in selected]
    ticktext = []
    for v in selected:
        try:
            ts = pd.Timestamp.fromordinal(int(v))
            ticktext.append(ts.strftime(date_format))
        except Exception:
            ticktext.append(str(v))
    return tickvals, ticktext

def _prepare_color_values(values, prefer_dates=False):
    """Coerce color values to numeric, with categorical fallback + ticks."""
    if values is None:
        return None, None
    series = pd.Series(values)
    if prefer_dates:
        parsed = pd.to_datetime(series, errors='coerce')
        if parsed.notna().any():
            date_ordinals = parsed.map(lambda x: x.toordinal() if pd.notna(x) else np.nan)
            return pd.to_numeric(date_ordinals, errors='coerce'), None
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
    columns = [idx_vals, iso_vals, id1_vals, id2_vals]
    if "__hover_species" in df.columns:
        species_vals = df.get("__hover_species", pd.Series(index=df.index, dtype=object)).fillna("Unknown").astype(str).to_numpy()
        columns.append(species_vals)
    if "__hover_color_value" in df.columns:
        color_vals = df.get("__hover_color_value", pd.Series(index=df.index, dtype=object)).fillna("N/A").astype(str).to_numpy()
        columns.append(color_vals)
    return np.column_stack(columns)


def _format_hover_color_value(value):
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
            # Keep hover uncertainty display concise and consistent.
            error_vals.append(round(float(sd_val), 3))
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
        trace_name = str(getattr(trace, "name", "")).strip()
        if trace_name.startswith("Calibrated "):
            continue
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
        is_date_color = _is_date_color_column(color_col)
        color_values, colorbar_category_ticks = _prepare_color_values(
            plot_df[color_col],
            prefer_dates=_prefer_datetime_color_values(color_col),
        )
        numeric_colors = pd.to_numeric(color_values, errors='coerce') if color_values is not None else pd.Series(dtype=float)
        if color_values is not None and numeric_colors.notna().any():
            colorbar_cfg = dict(
                title=dict(
                    text='Date' if is_date_color else (color_label or color_col),
                    side='right',
                ),
                thickness=18,
                len=0.84,
                y=0.52,
                yanchor='middle'
            )
            if is_date_color:
                tickvals, ticktext = _build_date_colorbar_ticks(color_values if color_values is not None else plot_df[color_col])
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
    species_source = (
        plot_df.get('__hover_species', plot_df.get('Species', plot_df.get('Identifier 1', pd.Series(index=plot_df.index, dtype=object))))
        .fillna('')
        .astype(str)
    )
    species_series = species_source.where(species_source.str.strip() != "", "Unknown")
    species_symbol_map = _build_species_symbol_map(species_series)
    color_hover_series = plot_df.get('__hover_color_value', plot_df.get(color_col, pd.Series(index=plot_df.index, dtype=object)))
    color_hover_series = color_hover_series.map(_format_hover_color_value)
    color_hover_label = 'Date' if _is_date_color_column(color_col) else str(color_label or color_col or 'Color')
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
    species_array = species_series.to_numpy()
    color_hover_array = color_hover_series.to_numpy()
    color_array = numeric_colors.to_numpy() if has_numeric_color else None
    z_axis_title = z_label or z_col

    fig = go.Figure()

    def _add_trace(mask, *, symbol, name, show_scale, show_legend=True):
        if not np.any(mask):
            return
        symbol_value = symbol
        if isinstance(symbol, np.ndarray) and symbol.ndim == 1 and symbol.shape[0] == len(mask):
            symbol_value = symbol[mask]
        marker = dict(size=6, opacity=1.0, symbol=symbol)
        marker['symbol'] = symbol_value
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
                    species_array[mask],
                    color_hover_array[mask],
                ]
            )
            hovertemplate = (
                "Identifier 1: %{customdata[2]}<br>"
                "Identifier 2: %{customdata[3]}<br>"
                "Species: %{customdata[4]}<br>"
                "Row: %{customdata[0]}<br>"
                f"{color_hover_label}: %{{customdata[5]}}<br>"
                "d18O: %{x:.3f}<br>"
                "d13C: %{y:.3f}<br>"
                f"{z_axis_title}: %{{z:.3f}}<extra></extra>"
            )
        else:
            customdata = np.column_stack([id1_array[mask], id2_array[mask], species_array[mask], color_hover_array[mask]])
            hovertemplate = (
                "Identifier 1: %{customdata[0]}<br>"
                "Identifier 2: %{customdata[1]}<br>"
                "Species: %{customdata[2]}<br>"
                f"{color_hover_label}: %{{customdata[3]}}<br>"
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
            showlegend=show_legend,
            customdata=customdata,
            hovertemplate=hovertemplate,
            hoverlabel=dict(namelength=-1),
        ))

    colorbar_shown = False
    non_standard_species = sorted({_clean_species_label(value) for value in species_series[non_standard_mask].tolist()})
    for species_label in non_standard_species:
        species_mask = non_standard_mask & species_series.eq(species_label).to_numpy(dtype=bool)
        if not np.any(species_mask):
            continue
        _add_trace(
            species_mask,
            symbol=_species_symbol_for_label(species_label, species_symbol_map),
            name=species_label,
            show_scale=has_numeric_color and not colorbar_shown,
            show_legend=True,
        )
        if has_numeric_color and not colorbar_shown:
            colorbar_shown = True
    standard_name = str(open_circle_identifier).strip() if open_circle_identifier else 'SHP2L'
    _add_trace(
        standard_mask,
        symbol='circle-open',
        name=standard_name,
        show_scale=has_numeric_color and not colorbar_shown,
        show_legend=bool(np.any(standard_mask)),
    )
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
