# IRMS Output Analyzer → FastAPI Backend for Next.js

This codex explains how to migrate the data preparation, calibration, diagnostics, outlier detection, charting, and Excel export logic from `IRMS_output_analyzer.py` into a Python backend (FastAPI) that serves a Next.js frontend.

It inventories every major feature in the Streamlit script, provides backend-oriented functions, and shows FastAPI endpoint examples. The intent is to reproduce outputs (plots and data products) with a stateless API that the web app can drive.

- Source reference: `IRMS_output_analyzer.py`
- Standards file: `standards.csv` (CSV columns: `Standard, Isotopic_Value_Type, Value`)
- Existing backend stub: `apps/api/app/services/irms_processor.py`

Note on symbols: The repository encodes δ strings as `��` (e.g., `��VPDB(13C)`). Keep these exact strings in code where used for matching against `standards.csv`.


## Data Model & Required Columns

Core columns expected in uploaded data, based on Streamlit app usage:
- `Identifier 1` (sample or standard family name; also used to match standards)
- `Identifier 2` (sequence/sample label containing numeric portion used for ordering)
- `Information` (free text with key-value pairs parsed into numeric columns below)
- `d 13C/12C  Mean`, `d 18O/16O  Mean` (isotope results)
- `1  Cycle Int  Samp  44` (signal intensity)
- `Line` (line id / sample position)
- `Date`, `Time`, `Comment` (metadata used in UI, ordering, grouping)

Columns parsed from `Information` via regex and added to the dataframe:
- `acid_temp`, `leak_rate`, `p_no_acid`, `p_gases`, `total_co2`, `co2_after_exp`, `left_mbar`, `right_mbar`, `left_pos`, `right_pos`, `vm1_after_transfer`

Derived columns used in the app:
- `Date_ordinal` (ordinal date for coloring)
- `Sequence` (numeric portion of `Identifier 2`)


## Core Parsing & Utilities (Backend-Friendly)

These are Streamlit-free utilities to mirror the script’s core logic. Use them inside service modules and endpoints.

### Parse Information column

Equivalent of Streamlit version in `IRMS_output_analyzer.py:58`.

```python
import pandas as pd
import numpy as np

def extract_info_values(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in [
        "acid_temp", "leak_rate", "p_no_acid", "p_gases", "total_co2",
        "co2_after_exp", "left_mbar", "right_mbar", "left_pos", "right_pos",
        "vm1_after_transfer",
    ]:
        if col not in df.columns:
            df[col] = np.nan

    patterns = {
        "acid_temp": r"Acid:\s*([\d.]+)",
        "leak_rate": r"LeakRate.*?:\s*([\d.]+)",
        "p_no_acid": r"P no Acid\s*:\s*([\d.]+)",
        "p_gases": r"P gases:\s*([\d.]+)",
        "total_co2": r"Total CO2\s*:\s*([\d.]+)",
        "co2_after_exp": r"CO2 after Exp\.:\s*([\d.]+)",
        "left_mbar": r"RefRe skipped: L mBar\s*([\d.]+)",
        "right_mbar": r"RefRe skipped: R mBar\s*([\d.]+)",
        "left_pos": r"L.*?Pos\s*([\d.]+)",
        "right_pos": r"R.*?Pos\s*([\d.]+)",
        "vm1_after_transfer": r"VM1 aftr Trfr\.:\s*([-\d.]+)",
    }

    info_series = df.get("Information", pd.Series(index=df.index, dtype="object")).astype(str)
    for col, pat in patterns.items():
        df[col] = info_series.str.extract(pat, expand=False).astype(float)
    return df
```

## Data Processing (Filters, Outliers, Summary, Figures)

The Streamlit Data Processing tab implements two categories of outliers:
- Range filters (user-tunable): signal intensity (`1  Cycle Int  Samp  44`), `leak_rate`, d13C, d18O
- Statistical outliers (grouped per Identifier 1 and `Comment`) based on Z-score thresholds

It also:
- Builds `Sequence` from `Identifier 2` (numeric extraction)
- Supports coloring by a selectable field
- Creates summary figures for δ13C and δ18O per species (`Comment`) with optional overlays of outlier categories
- Computes a statistics table (unique samples, total measurements, counts per outlier category, final analyses)

Backend-oriented implementation sketch:

```python
import re
import pandas as pd
import numpy as np
import plotly.graph_objects as go

def add_sequence(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out['Sequence'] = out['Identifier 2'].apply(
        lambda x: int(re.search(r'\d+', str(x)).group()) if pd.notnull(x) and re.search(r'\d+', str(x)) else None
    )
    return out

def build_range_masks(df: pd.DataFrame, signal_range: tuple[float, float], leak_range: tuple[float, float],
                      d13c_range: tuple[float, float], d18o_range: tuple[float, float]) -> dict[str, pd.Series]:
    s = pd.to_numeric(df['1  Cycle Int  Samp  44'], errors='coerce')
    l = pd.to_numeric(df['leak_rate'], errors='coerce')
    c13 = pd.to_numeric(df['d 13C/12C  Mean'], errors='coerce')
    c18 = pd.to_numeric(df['d 18O/16O  Mean'], errors='coerce')
    return {
        'signal': (s >= signal_range[0]) & (s <= signal_range[1]),
        'leak': (l >= leak_range[0]) & (l <= leak_range[1]),
        'd13c': (c13 >= d13c_range[0]) & (c13 <= d13c_range[1]),
        'd18o': (c18 >= d18o_range[0]) & (c18 <= d18o_range[1]),
    }

def build_statistical_outliers(df: pd.DataFrame, sigma_level: float) -> pd.Series:
    # group by Identifier 1 and Comment, flag rows exceeding the sigma threshold in either isotope
    flags = pd.Series(False, index=df.index)
    for id1, g1 in df.groupby('Identifier 1'):
        for cmt, g in g1.groupby('Comment'):
            c13 = pd.to_numeric(g['d 13C/12C  Mean'], errors='coerce')
            c18 = pd.to_numeric(g['d 18O/16O  Mean'], errors='coerce')
            o13 = identify_outliers_sigma(c13, sigma_level)
            o18 = identify_outliers_sigma(c18, sigma_level)
            flags.loc[g.index] = o13 | o18
    return flags

def summarize_outlier_counts(df: pd.DataFrame, masks: dict[str, pd.Series], stat_mask: pd.Series) -> dict:
    # assumes df already excluded standards if desired
    total = int(len(df))
    counts = {
        'statistical': int(stat_mask.sum()),
        'd13c_range': int((~masks['d13c']).sum()),
        'd18o_range': int((~masks['d18o']).sum()),
        'signal_range': int((~masks['signal']).sum()),
        'leak_range': int((~masks['leak']).sum()),
    }
    counts['final_analyses'] = total - sum(counts.values())
    return {'total_measurements': total, **counts}

def build_species_summary_figures(df: pd.DataFrame, color_param: str,
                                  show_stat_outliers: bool, show_range_outliers: bool,
                                  ranges: dict[str, tuple[float, float]]) -> dict[str, str]:
    # Builds δ13C and δ18O summary figures per Comment; mirrors Streamlit style at high level
    figs = {}
    d13c_fig = go.Figure(); d18o_fig = go.Figure()
    for species, sub in df.groupby('Comment'):
        # extract x-axis: numeric from Identifier 2 (or fallback to positional)
        x = sub['Identifier 2'].apply(lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(r'\d+\.?\d*', str(x)) else None)
        d13c_fig.add_trace(go.Scatter(x=x, y=sub['d 13C/12C  Mean'], mode='lines+markers', name=str(species),
                                      marker=dict(size=8, color=sub[color_param], colorscale='Viridis', showscale=False)))
        d18o_fig.add_trace(go.Scatter(x=x, y=sub['d 18O/16O  Mean'], mode='lines+markers', name=str(species),
                                      marker=dict(size=8, color=sub[color_param], colorscale='Viridis', showscale=False)))
        # Optional overlays could be added mirroring Streamlit logic
    d13c_fig.update_layout(title='δ13C Summary by Species', height=500)
    d18o_fig.update_layout(title='δ18O Summary by Species', height=500)
    figs['d13c'] = d13c_fig.to_json(); figs['d18o'] = d18o_fig.to_json()
    return figs
```

## Excel Export (Multi-sheet)

The Streamlit app’s `download_excel` writes an in-memory Excel with:
- `Statistics` sheet
- `Data` sheet (optionally excluding standards or outliers)
- Outlier sheets by category (if present)
- `Standards Measurements` sheet
- `Standards Results` sheet (summary with precision/averages)

Backend function to return bytes (suitable for `StreamingResponse`):

```python
import io
import pandas as pd

def build_export_excel_bytes(df: pd.DataFrame,
                             outliers: pd.DataFrame | None = None,
                             selected_standards: list[str] | None = None) -> bytes:
    towrite = io.BytesIO()
    with pd.ExcelWriter(towrite, engine='xlsxwriter') as writer:
        standards_mask = df['Identifier 1'].isin(selected_standards) if selected_standards else pd.Series(False, index=df.index)
        main_data = df[~standards_mask].copy()

        # Statistics sheet
        total_samples = len(df)
        stats_rows = [['Total Samples', total_samples]]
        if outliers is not None and not outliers.empty:
            oc = outliers.groupby('Category').size()
            stats_rows.append(['', ''])
            stats_rows.append(['Outliers Statistics:', ''])
            for cat, cnt in oc.items():
                stats_rows.append([f'{cat} Outliers', f'{cnt} ({(cnt/total_samples)*100:.1f}%)'])
        pd.DataFrame(stats_rows, columns=['Metric', 'Value']).to_excel(writer, index=False, sheet_name='Statistics')

        # Data sheet
        main_data.to_excel(writer, index=False, sheet_name='Data')

        # Outliers by category
        if outliers is not None and not outliers.empty:
            if 'Category' not in outliers.columns:
                outliers = outliers.copy(); outliers['Category'] = 'Statistical'
            for cat in outliers['Category'].unique():
                sub = outliers[outliers['Category'] == cat]
                if not sub.empty:
                    sheet = f'Outliers - {cat}'
                    pd.DataFrame(sub).to_excel(writer, index=False, sheet_name=sheet[:31])

        # Standards sheets
        if selected_standards:
            std_meas = df[standards_mask].copy()
            if not std_meas.empty:
                std_meas.to_excel(writer, index=False, sheet_name='Standards Measurements')
            rows = []
            for std in selected_standards:
                s = df[df['Identifier 1'] == std]
                if s.empty: continue
                rows.append({
                    'Standard': std,
                    'δ13C Precision': s['d 13C/12C  Mean'].std(),
                    'δ13C Average': s['d 13C/12C  Mean'].mean(),
                    'δ18O Precision': s['d 18O/16O  Mean'].std(),
                    'δ18O Average': s['d 18O/16O  Mean'].mean(),
                    'Sample Count': len(s),
                })
            if rows:
                pd.DataFrame(rows).to_excel(writer, index=False, sheet_name='Standards Results')
    towrite.seek(0)
    return towrite.getvalue()
```

### Outlier detection

Port of `identify_outliers` and `identify_outliers_iqr` (`IRMS_output_analyzer.py:99,125`).

```python
import pandas as pd

def identify_outliers_sigma(series: pd.Series, sigma_level: float) -> pd.Series:
    mu = series.mean(); sd = series.std()
    if pd.isna(mu) or pd.isna(sd) or sd == 0:
        return pd.Series(False, index=series.index)
    upper = mu + sigma_level * sd
    lower = mu - sigma_level * sd
    return (series > upper) | (series < lower)


def identify_outliers_iqr(series: pd.Series, iqr_multiplier: float = 1.5) -> pd.Series:
    q1 = series.quantile(0.25); q3 = series.quantile(0.75); iqr = q3 - q1
    if pd.isna(iqr) or iqr == 0:
        return pd.Series(False, index=series.index)
    lower = q1 - iqr_multiplier * iqr
    upper = q3 + iqr_multiplier * iqr
    base = pd.Series(False, index=series.index)
    mask = series.notna()
    base.loc[mask] = (series.loc[mask] < lower) | (series.loc[mask] > upper)
    return base
```

### Standards utilities and calibration

Core logic from `IRMS_output_analyzer.py:173–247, 196–238`.

```python
import pandas as pd
import numpy as np

ISOTOPIC_TYPES = {
    '��VPDB(13C)': ('d 13C/12C  Mean', 'd13C_calibrated'),
    '��VSMOW(18O)': ('d 18O/16O  Mean', 'd18O_calibrated'),
}

def get_true_value(standards_df: pd.DataFrame, standard_name: str, isotopic_type: str) -> float:
    row = standards_df[(standards_df['Standard'] == standard_name) &
                       (standards_df['Isotopic_Value_Type'] == isotopic_type)]
    if row.empty:
        raise ValueError(f"True value not found for {standard_name} / {isotopic_type}")
    return float(row['Value'].iloc[0])


def single_point_calibration(raw_sample: float, raw_std: float, true_std: float) -> float:
    return ((raw_sample + 1000.0) * (true_std + 1000.0)) / (raw_std + 1000.0) - 1000.0


def double_point_calibration(raw_sample: float, raw_rm1: float, true_rm1: float,
                             raw_rm2: float, true_rm2: float) -> float:
    if raw_rm2 == raw_rm1:
        # Avoid division by zero; return raw_sample as fallback
        return raw_sample
    m = (true_rm2 - true_rm1) / (raw_rm2 - raw_rm1)
    b = true_rm1 - m * raw_rm1
    return m * raw_sample + b


def calibrate_results(standards_df: pd.DataFrame, full_df: pd.DataFrame, selected_standards: list[str]) -> pd.DataFrame:
    """Apply single- or double-anchor calibration to both isotopic types.
    Adds columns: 'd13C_calibrated', 'd18O_calibrated'.
    """
    calibrated_df = full_df.copy()
    for isotopic_type, (raw_col, cal_col) in ISOTOPIC_TYPES.items():
        if len(selected_standards) == 1:
            std = selected_standards[0]
            raw_std = pd.to_numeric(standards_df.loc[standards_df['Identifier 1'] == std, raw_col], errors='coerce').mean()
            true_std = get_true_value(standards_df, std, isotopic_type)
            calibrated_df[cal_col] = pd.to_numeric(calibrated_df[raw_col], errors='coerce').apply(
                lambda x: single_point_calibration(x, raw_std, true_std) if pd.notna(x) else np.nan
            )
        elif len(selected_standards) == 2:
            std1, std2 = selected_standards
            raw_rm1 = pd.to_numeric(standards_df.loc[standards_df['Identifier 1'] == std1, raw_col], errors='coerce').mean()
            raw_rm2 = pd.to_numeric(standards_df.loc[standards_df['Identifier 1'] == std2, raw_col], errors='coerce').mean()
            true_rm1 = get_true_value(standards_df, std1, isotopic_type)
            true_rm2 = get_true_value(standards_df, std2, isotopic_type)
            calibrated_df[cal_col] = pd.to_numeric(calibrated_df[raw_col], errors='coerce').apply(
                lambda x: double_point_calibration(x, raw_rm1, true_rm1, raw_rm2, true_rm2) if pd.notna(x) else np.nan
            )
        else:
            raise ValueError("Select one or two standards for calibration")
    return calibrated_df
```

## Diagnostics (Plots and Data)

The Streamlit app builds a 7×3 grid of plots including scatter plots, box plots, PCA, and multiple relationships. In the backend, return Plotly Figure JSON (for React Plotly on the frontend) or return the raw series so the frontend builds charts.

Plots implemented in `create_diagnostic_plots` (`IRMS_output_analyzer.py:406`) include (typical x → y):
- Row 1: `leak_rate → d13C`, `p_no_acid → d13C`, `total_co2 → d13C`
- Row 2: `leak_rate → d18O`, `p_no_acid → d18O`, `total_co2 → d18O`
- Row 3: `Line → leak_rate` (box), `signal → total_co2` with quadratic fit, box
- Row 4: more signal/intensity vs d13C/d18O boxes
- Row 5–6: isotope vs Line; leak_rate vs pCO2; d13C vs d18O comparisons
- Row 7: PCA (2D components + loadings)

Backend builder example (returns Plotly JSON):

```python
import numpy as np
import pandas as pd
from plotly.subplots import make_subplots
import plotly.graph_objects as go
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

def build_diagnostic_figure_json(df: pd.DataFrame, color_param: str) -> str:
    # Sanity checks
    req = ['leak_rate', 'd 13C/12C  Mean', 'p_no_acid', 'total_co2', 'd 18O/16O  Mean',
           'Line', '1  Cycle Int  Samp  44', 'p_gases', 'Identifier 1']
    for c in [color_param, *req]:
        if c not in df.columns:
            raise ValueError(f"Missing required column: {c}")

    # Marker symbol: open circle for known standards (optional)
    marker_symbols = ['circle' for _ in range(len(df))]

    fig = make_subplots(
        rows=7, cols=3,
        subplot_titles=(
            'Leak Rate vs δ13C', 'P no Acid vs δ13C', 'Total CO2 vs δ13C',
            'Leak Rate vs δ18O', 'P no Acid vs δ18O', 'Total CO2 vs δ18O',
            'Leak Rate vs Line', 'Signal Intensity vs pCO2', 'Signal Intensity vs δ13C',
            'Signal Intensity vs δ18O', 'δ13C vs Line', 'δ18O vs Line',
            'Leak Rate vs pCO2', 'δ13C vs δ18O', 'Total CO2 vs Line',
            'Leak Rate vs Signal Intensity', 'P no Acid vs Leak Rate', 'P Gases vs Leak Rate',
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

    # Example trace (others mirror Streamlit code)
    def add_scatter(r, c, x, y):
        fig.add_trace(go.Scatter(
            x=x, y=y, mode='markers',
            marker=dict(color=df[color_param], colorscale='Viridis', symbol=marker_symbols, showscale=False)
        ), row=r, col=c)

    add_scatter(1, 1, df['leak_rate'], df['d 13C/12C  Mean'])
    add_scatter(1, 2, df['p_no_acid'], df['d 13C/12C  Mean'])
    add_scatter(1, 3, df['total_co2'], df['d 13C/12C  Mean'])
    add_scatter(2, 1, df['leak_rate'], df['d 18O/16O  Mean'])
    # ... replicate remaining traces as in Streamlit function ...

    # PCA (if enough rows)
    features = ['leak_rate', 'd 13C/12C  Mean', 'p_no_acid', 'total_co2', 'd 18O/16O  Mean', 'Line', '1  Cycle Int  Samp  44']
    X = df[features].dropna()
    if len(X) >= 2:
        comps = min(2, X.shape[0], X.shape[1])
        Xs = StandardScaler().fit_transform(X)
        pca = PCA(n_components=comps)
        tr = pca.fit_transform(Xs)
        fig.add_trace(go.Scatter(x=tr[:,0], y=tr[:,1], mode='markers',
                                 marker=dict(color=df.loc[X.index, color_param], colorscale='Viridis', showscale=False)),
                      row=7, col=1)
        # loadings (arrows) could be added as annotations similar to Streamlit code

    fig.update_layout(title_text='Diagnostic Plots', height=2000, showlegend=False, margin=dict(r=150))
    return fig.to_json()
```

## Calibration (Outliers, Plots, and Values)

The Streamlit tab combines:
- Choice of standards (1 or 2)
- Outlier method and parameter: Z-score `sigma_level` or IQR `iqr_multiplier`
- Filter standards’ observations, build calibration plots, perform calibration for d13C and d18O

Key code in `IRMS_output_analyzer.py`:
- Outliers on standards: `identify_outliers` / `identify_outliers_iqr` (`:99, :125`)
- Plot builder: `create_calibration_plots` (`:247`)
- Apply calibration: `calibrate_results` (`:196`)

Backend builder example to get both a filtered standards dataframe and plot JSON:

```python
import pandas as pd
import numpy as np
from scipy.stats import linregress
import plotly.graph_objects as go

def build_calibration_plots_json(standards_reference: pd.DataFrame,
                                 measurements: pd.DataFrame,
                                 selected_standards: list[str],
                                 color_param: str) -> dict[str, str]:
    figs: dict[str, str] = {}
    isotopes = {
        '��VPDB(13C)': {'y_label': 'δ13C', 'measurement_col': 'd 13C/12C  Mean'},
        '��VSMOW(18O)': {'y_label': 'δ18O', 'measurement_col': 'd 18O/16O  Mean'},
    }
    for iso, meta in isotopes.items():
        fig = go.Figure()
        true_vals, measured_vals = [], []
        for std in selected_standards:
            # true (from standards.csv)
            row = standards_reference[(standards_reference['Standard'] == std) &
                                      (standards_reference['Isotopic_Value_Type'] == iso)]
            if row.empty:
                continue
            tv = float(row['Value'].iloc[0])
            mv = measurements.loc[measurements['Identifier 1'] == std, meta['measurement_col']].values
            cv = measurements.loc[measurements['Identifier 1'] == std, color_param].values
            if len(mv) != len(cv) or pd.isna(cv).any():
                continue
            true_vals.extend([tv] * len(mv)); measured_vals.extend(mv)
            fig.add_trace(go.Scatter(x=[tv] * len(mv), y=mv, mode='markers',
                                     marker=dict(size=10, color=cv, colorscale='Viridis', showscale=False),
                                     name=std))
        if len(selected_standards) == 1 and measured_vals:
            offset = float(np.mean(np.array(measured_vals) - np.array(true_vals)))
            x_min, x_max = min(true_vals) - 1, max(true_vals) + 1
            fig.add_trace(go.Scatter(x=[x_min, x_max], y=[x_min + offset, x_max + offset],
                                     mode='lines', line=dict(color='orange', dash='dash'), name='Offset Line'))
        elif len(selected_standards) == 2 and measured_vals:
            slope, intercept, _, _, _ = linregress(true_vals, measured_vals)
            x_min, x_max = min(true_vals) - 1, max(true_vals) + 1
            fig.add_trace(go.Scatter(x=[x_min, x_max], y=[slope*x_min+intercept, slope*x_max+intercept],
                                     mode='lines', line=dict(color='blue'), name='Calibration Line'))
        fig.update_layout(title=f"{'Single' if len(selected_standards)==1 else 'Double'} Anchor Calibration for {iso}",
                          xaxis_title=f"True {meta['y_label']} value", yaxis_title=f"Raw/Measured {meta['y_label']} value",
                          width=900, height=600, margin=dict(r=150))
        figs[iso] = fig.to_json()
    return figs
```

Applying outlier filters on standards before calibration:

```python
def filter_standards_outliers(df: pd.DataFrame, selected_standards: list[str],
                              method: str, sigma_level: float = 1.0, iqr_multiplier: float = 1.5) -> pd.DataFrame:
    out = df.copy()
    for std in selected_standards:
        m = out['Identifier 1'] == std
        sub = out[m]
        if sub.empty:
            continue
        if method == 'Z-Score':
            d13c_mask = identify_outliers_sigma(pd.to_numeric(sub['d 13C/12C  Mean'], errors='coerce'), sigma_level)
            d18o_mask = identify_outliers_sigma(pd.to_numeric(sub['d 18O/16O  Mean'], errors='coerce'), sigma_level)
        else:
            d13c_mask = identify_outliers_iqr(pd.to_numeric(sub['d 13C/12C  Mean'], errors='coerce'), iqr_multiplier)
            d18o_mask = identify_outliers_iqr(pd.to_numeric(sub['d 18O/16O  Mean'], errors='coerce'), iqr_multiplier)
        keep = ~(d13c_mask | d18o_mask)
        out.loc[m, :] = sub.loc[keep, :]
    return out
```
