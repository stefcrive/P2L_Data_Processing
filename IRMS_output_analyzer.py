import streamlit as st
import pandas as pd
import numpy as np
import io

# Enable pandas copy-on-write mode to prevent SettingWithCopyWarning
pd.options.mode.copy_on_write = True
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
import re
import plotly.express as px
from plotly.subplots import make_subplots
import plotly.graph_objects as go
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from scipy.stats import linregress
from io import BytesIO
from reportlab.lib.styles import *
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle, Image
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

# Optional helper to interpolate outliers during export
try:
    from interpolate_outliers import interpolate_columns
except Exception:
    interpolate_columns = None


st.set_page_config(layout="wide")

# Constants for isotopic type keys (canonical)
# standards.csv uses the plain VPDB/VSMOW values (no leading delta)
ISOTYPE_D13C = 'VPDB(13C)'
ISOTYPE_D18O = 'VSMOW(18O)'

# Helper: build readable date ticks for colorbars when coloring by date ordinals
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

def _compose_label_series(identifier_series, species_series):
    """Compose labels as 'Identifier 1 - Species' when species exists."""
    ids = pd.Series(identifier_series).fillna('').astype(str).str.strip()
    species = pd.Series(species_series).fillna('').astype(str).str.strip()
    labels = ids
    has_species = species != ''
    labels = labels.where(~has_species, ids + ' - ' + species)
    labels = labels.replace({'': 'Unknown'})
    return labels

# Initialize session state variables if they don't exist
if 'df' not in st.session_state:
    st.session_state.df = None
if 'file_processed' not in st.session_state:
    st.session_state.file_processed = False
if 'include_outliers' not in st.session_state:
    st.session_state.include_outliers = "No"
if 'selected_ids' not in st.session_state:
    st.session_state.selected_ids = ["All"]
if 'interpolate_outliers_export' not in st.session_state:
    st.session_state.interpolate_outliers_export = False

# Initialize range variables in session state with safe defaults
if 'signal_range' not in st.session_state:
    st.session_state.signal_range = (1.0, 50.0)  # Signal intensity in volts (default low cutoff 1V)
if 'leak_range' not in st.session_state:
    st.session_state.leak_range = (0.0, 1000.0)  # Conservative default range
if 'd13c_range' not in st.session_state:
    st.session_state.d13c_range = (-50.0, 50.0)  # Wide default range
if 'd18o_range' not in st.session_state:
    st.session_state.d18o_range = (-50.0, 50.0)  # Wide default range


def extract_number(text):
    """Extract the first number from a string."""
    if pd.isna(text):
        return None
    matches = re.findall(r'\d+', str(text))
    return int(matches[0]) if matches else None

def _parse_numeric_token(token):
    """Parse a numeric token with optional thousands/decimal separators."""
    if token is None or pd.isna(token):
        return None
    text = str(token).strip()
    if text == "":
        return None
    # Find the first number-like chunk (digits with optional separators/sign).
    match = re.search(r'[-+]?[\d.,]+', text)
    if not match:
        return None
    num = match.group(0)
    # Remove spaces (including non-breaking/thin spaces) used as thousands separators.
    num = re.sub(r"[\s\u00A0\u2009]", "", num)

    if "," in num and "." in num:
        # Decide decimal separator by last occurrence; other is thousands separator.
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "")
            num = num.replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        if num.count(",") > 1:
            num = num.replace(",", "")
        else:
            left, right = num.split(",", 1)
            if right.isdigit() and len(right) in (1, 2):
                num = left + "." + right
            else:
                num = left + right
    elif "." in num:
        if num.count(".") > 1:
            num = num.replace(".", "")
        else:
            left, right = num.split(".", 1)
            # If it looks like a thousands separator (e.g., 1.234), collapse it.
            if right.isdigit() and len(right) == 3 and left.isdigit() and len(left) <= 3:
                num = left + right

    try:
        return float(num)
    except Exception:
        return None


def _extract_numeric(series):
    """Extract numeric values from a mixed/unit string series."""
    if series is None:
        return pd.Series(dtype='float64')
    ser = series if isinstance(series, pd.Series) else pd.Series(series)
    parsed = ser.map(_parse_numeric_token)
    return pd.to_numeric(parsed, errors='coerce')

def _normalize_signal_intensity(series):
    """Normalize signal intensity to volts when values appear to be in mV."""
    numeric = _extract_numeric(series)
    max_val = numeric.max(skipna=True)
    # Treat clearly mV-scale values as millivolts (e.g., 48000 mV -> 48 V).
    # Keep normal volt-scale cycle values (e.g., 52 V) unchanged.
    if pd.notna(max_val) and max_val > 1000:
        numeric = numeric / 1000.0
    return numeric

def _coalesce_duplicate_columns(df):
    """Resolve duplicate column names while preserving independent collector columns."""
    if df is None or df.columns.is_unique:
        return df
    canonical_merge_cols = {
        'd 13C/12C  Mean',
        'd 13C/12C  Std Dev',
        'd 18O/16O  Mean',
        'd 18O/16O  Std Dev',
        'Identifier 1',
        'Identifier 2',
        'Cycle Number',
    }
    result_parts = []
    cols = pd.Index(df.columns)
    for col in cols.unique():
        subset = df.loc[:, cols == col]
        if isinstance(subset, pd.Series):
            result_parts.append(subset.to_frame(name=col))
            continue
        if subset.shape[1] == 1:
            result_parts.append(subset.iloc[:, [0]].rename(columns={subset.columns[0]: col}))
            continue
        if col in canonical_merge_cols:
            merged_col = subset.bfill(axis=1).iloc[:, 0].to_frame(name=col)
            result_parts.append(merged_col)
        else:
            renamed = subset.copy()
            renamed.columns = [col if i == 0 else f"{col}__dup{i+1}" for i in range(subset.shape[1])]
            result_parts.append(renamed)
    return pd.concat(result_parts, axis=1)

def _find_cycle_intensity_columns(df):
    """Find per-cycle intensity columns in the dataset."""
    if df is None:
        return []
    cols = []
    for col in df.columns:
        if not isinstance(col, str):
            continue
        low = _normalize_column_key(col)
        has_mass = bool(re.search(r'\b4[4-6](?:\.\d+)?\b', low) or 'm/z' in low or 'mz' in low)
        is_signal_named = bool('intensit' in low or re.search(r'\bint\b', low) or 'signal' in low)
        looks_delta = bool('delta' in low or re.search(r'\bd4[5-6]co2\b', low) or low.startswith('d45') or low.startswith('d46'))
        if (is_signal_named and has_mass) or (has_mass and not looks_delta):
            cols.append(col)
    if cols:
        return cols
    # Fallback: accept intensity columns without explicit sample label
    for col in df.columns:
        if not isinstance(col, str):
            continue
        low = _normalize_column_key(col)
        if ('intensit' in low or re.search(r'\bint\b', low) or 'signal' in low):
            if ('m/z' in low or 'mz' in low or re.search(r'\b4[4-6](?:\.\d+)?\b', low) or 'cycle' in low):
                cols.append(col)
    return cols

def _pick_intensity_column(cols, masses=None):
    """Pick the best intensity column, preferring specific masses."""
    if not cols:
        return None
    if masses:
        for mass in masses:
            pattern = rf'(?<!\\d){mass}(?!\\d)'
            for col in cols:
                if re.search(pattern, str(col)):
                    return col
    return cols[0]

def _extract_cycle_order(value):
    """Extract cycle order as integer (Pre -> 0, Cycle N -> N)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    text = str(value).strip().lower()
    if text == '' or text == 'nan':
        return np.nan
    if text == 'pre':
        return 0
    match = re.search(r'(\d+)', text)
    if match:
        return int(match.group(1))
    return np.nan

def _detect_saturated_prefix(series, min_tail=3, sigma=3.0, min_abs_shift=1.0):
    """Detect a saturated prefix in cycle values using a robust tail-based threshold.

    Returns a list of cycle orders to exclude (prefix only).
    """
    if series is None:
        return []
    s = pd.Series(series).dropna()
    if s.empty or len(s) < max(3, min_tail):
        return []
    s = s.sort_index()
    tail = s.tail(min(min_tail, len(s)))
    med = float(tail.median())
    mad = float((tail - med).abs().median())
    # Convert MAD to sigma-like; fallback to std when MAD is zero
    spread = 1.4826 * mad
    if not np.isfinite(spread) or spread == 0:
        spread = float(tail.std())
    if not np.isfinite(spread) or spread == 0:
        # Final fallback: small tolerance scaled to signal magnitude
        spread = max(float(tail.abs().median()) * 0.02, 0.05)
    tol = sigma * spread
    inlier = (s - med).abs() <= tol
    window = min(min_tail, len(s))
    for i in range(len(s) - window + 1):
        if bool(inlier.iloc[i:i + window].all()):
            excluded = s.iloc[:i]
            if excluded.empty:
                return []
            # Guard against over-detection on normal drift when no intensity columns exist.
            if (excluded - med).abs().max() < max(tol, float(min_abs_shift)):
                return []
            return list(s.index[:i])
    # No stable window detected -> treat all cycles as saturated
    if (s - med).abs().max() < max(tol, float(min_abs_shift)):
        return []
    return list(s.index)

def _apply_cycle_averages(df):
    """Compute per-sample d13C/d18O means from cycle rows, excluding saturated cycles."""
    if df is None or 'Cycle Number' not in df.columns:
        return df

    work = df.copy()

    # Coalesce duplicate isotope columns (keep first non-null across duplicates)
    for col in ['d 13C/12C  Mean', 'd 18O/16O  Mean', 'd 13C/12C  Std Dev', 'd 18O/16O  Std Dev']:
        dup_positions = [i for i, c in enumerate(work.columns) if c == col]
        if len(dup_positions) > 1:
            subset = work.iloc[:, dup_positions]
            combined = subset.bfill(axis=1).iloc[:, 0]
            work = work.drop(columns=[col])
            work[col] = combined
    cycle_order = work['Cycle Number'].apply(_extract_cycle_order)
    is_pre = work['Cycle Number'].astype(str).str.strip().str.lower().eq('pre')
    cycle_order = cycle_order.where(~is_pre, 0)
    work['_cycle_order'] = cycle_order

    # Build group id for sequences starting at Pre
    group_id = is_pre.cumsum()
    group_id = group_id.where(is_pre | cycle_order.notna(), np.nan)
    work['_cycle_group'] = group_id

    # Forward-fill key identifiers within each group to attach cycle rows to samples
    id_cols = [
        'Identifier 1', 'Identifier 2', 'Label', 'Species', 'Comment', 'Run ID',
        'Line', 'Date', 'Date_ordinal', 'Sample Type', 'Reference'
    ]
    for col in id_cols:
        if col in work.columns:
            work[col] = work.groupby('_cycle_group')[col].ffill()

    pre_rows = work[is_pre].copy()
    cycle_rows = work[(work['_cycle_order'] > 0) & work['_cycle_group'].notna()].copy()

    if pre_rows.empty:
        return df

    # Ensure numeric cycle values
    for col in ['d 13C/12C  Mean', 'd 18O/16O  Mean', 'd 13C/12C  Std Dev', 'd 18O/16O  Std Dev']:
        if col in work.columns:
            col_positions = [i for i, c in enumerate(work.columns) if c == col]
            for pos in col_positions:
                work.iloc[:, pos] = pd.to_numeric(work.iloc[:, pos], errors='coerce')

    # Initialize status columns
    pre_rows['Collector Status'] = 'OK'
    pre_rows['Cycles Total'] = 0
    pre_rows['d13C Cycles Used'] = 0
    pre_rows['d18O Cycles Used'] = 0
    pre_rows['d13C Cycles Excluded'] = 0
    pre_rows['d18O Cycles Excluded'] = 0

    intensity_cols = _find_cycle_intensity_columns(work)
    saturation_threshold = 48.0
    low_signal_threshold = 0.2

    def _pick_sample_intensity_columns(source_df, cols):
        labeled_sample = []
        for c in cols:
            low = _normalize_column_key(c)
            if 'standard' in low:
                continue
            if 'sample' in low or 'samp' in low:
                labeled_sample.append(c)
        if labeled_sample:
            return labeled_sample
        if len(cols) >= 4:
            # If sample/reference labels are missing, sample cups are typically the higher-voltage set.
            medians = []
            for c in cols:
                vals = _normalize_signal_intensity(source_df[c]) if c in source_df.columns else pd.Series(dtype='float64')
                medians.append((c, float(vals.median(skipna=True)) if vals.notna().any() else -np.inf))
            medians.sort(key=lambda t: t[1], reverse=True)
            top = [c for c, _ in medians[:3]]
            if top:
                return top
        return cols

    sample_intensity_cols = _pick_sample_intensity_columns(work, intensity_cols)

    def _pick_cycle_value_source(col_main, patterns):
        # Prefer per-cycle isotope columns (e.g., d45CO2/d46CO2) over mean columns.
        for col in work.columns:
            if not isinstance(col, str):
                continue
            low = _normalize_column_key(col)
            if 'standard' in low:
                continue
            if any(term in low for term in ('std', 'sd', 'se')):
                continue
            if 'mean' in low:
                continue
            if any(re.search(pat, low) for pat in patterns):
                vals = pd.to_numeric(cycle_rows[col], errors='coerce')
                if vals.notna().any():
                    return col
        # Fallback to canonical mean column only when no cycle-specific value source exists.
        if col_main in work.columns:
            vals = pd.to_numeric(cycle_rows[col_main], errors='coerce')
            if vals.notna().any():
                return col_main
        for col in work.columns:
            if not isinstance(col, str):
                continue
            low = _normalize_column_key(col)
            if 'standard' in low:
                continue
            if any(term in low for term in ('std', 'sd', 'se')):
                continue
            if any(re.search(pat, low) for pat in patterns):
                vals = pd.to_numeric(cycle_rows[col], errors='coerce')
                if vals.notna().any():
                    return col
        return col_main if col_main in work.columns else None

    d13_value_col = _pick_cycle_value_source(
        'd 13C/12C  Mean',
        [r'd13', r'd ?13c', r'd45co2', r'\bd45\b']
    )
    d18_value_col = _pick_cycle_value_source(
        'd 18O/16O  Mean',
        [r'd18', r'd ?18o', r'd46co2', r'\bd46\b']
    )

    def _extract_col_mass(col_name):
        low = _normalize_column_key(col_name)
        m = re.search(r'(?<!\d)(44|45|46)(?:\.0+)?(?!\d)', low)
        if m:
            return int(m.group(1))
        return None

    def _compute_cycle_intensity_frame(cycles_df):
        cols = [c for c in intensity_cols if c in cycles_df.columns]
        intensity_df = None
        if cols:
            intensity_df = pd.DataFrame({
                col: _normalize_signal_intensity(cycles_df[col])
                for col in cols
            })
        else:
            # Fallback: use any numeric columns in cycle rows (excluding known isotope/ID fields)
            exclude = {
                'Cycle Number', 'Identifier 1', 'Identifier 2', 'Label', 'Species', 'Comment',
                'Run ID', 'Line', 'Date', 'Date_ordinal', 'Sample Type', 'Reference',
                'd 13C/12C  Mean', 'd 13C/12C  Std Dev',
                'd 18O/16O  Mean', 'd 18O/16O  Std Dev'
            }
            data = {}
            for col in cycles_df.columns:
                if col in exclude or not isinstance(col, str):
                    continue
                low = _normalize_column_key(col)
                if not ('intensit' in low or re.search(r'\bint\b', low) or 'signal' in low):
                    continue
                if not ('m/z' in low or 'mz' in low or re.search(r'\b4[4-6](?:\.\d+)?\b', low) or 'cycle' in low):
                    continue
                vals = _normalize_signal_intensity(cycles_df[col])
                if vals.notna().any():
                    data[col] = vals
            if data:
                intensity_df = pd.DataFrame(data)
        if intensity_df is None or intensity_df.empty:
            return None
        valid = intensity_df.notna().any(axis=1)
        if valid.any():
            return intensity_df
        return None

    def _build_saturation_mask(intensity_df, required_masses):
        if intensity_df is None or intensity_df.empty:
            return None
        sat_mask = pd.Series(False, index=intensity_df.index, dtype=bool)
        has_mass_cols = False
        for mass in required_masses:
            mass_cols = [c for c in intensity_df.columns if _extract_col_mass(c) == mass]
            if not mass_cols:
                continue
            has_mass_cols = True
            # For each required mass, if any collector (sample/reference) is saturated, exclude the cycle.
            mass_sat = (intensity_df[mass_cols] > saturation_threshold).any(axis=1)
            sat_mask = sat_mask | mass_sat
        if not has_mass_cols:
            return None
        return sat_mask

    for group in pre_rows['_cycle_group'].dropna().unique():
        sample_mask = pre_rows['_cycle_group'] == group
        sample_idx = pre_rows.index[sample_mask][0]
        cycles = cycle_rows[cycle_rows['_cycle_group'] == group]
        # Only true analysis cycles contribute to recovered means (exclude "Pre").
        sample_cycles = cycles.copy()

        total_cycles = int(cycles.shape[0])
        pre_rows.at[sample_idx, 'Cycles Total'] = total_cycles

        saturated_any = False
        has_cycle_intensity = False
        sat_mask_d13 = None
        sat_mask_d18 = None
        intensity_df = _compute_cycle_intensity_frame(sample_cycles)
        if intensity_df is not None:
            has_cycle_intensity = True
            sat_mask_d13 = _build_saturation_mask(intensity_df, [44, 45])
            sat_mask_d18 = _build_saturation_mask(intensity_df, [44, 45, 46])
        low_signal_failed = False
        pre_intensity_cols = [c for c in sample_intensity_cols if c in pre_rows.columns]
        if pre_intensity_cols:
            pre_vals = _normalize_signal_intensity(pre_rows.loc[sample_idx, pre_intensity_cols])
            pre_max = pre_vals.max(skipna=True)
            if pd.notna(pre_max) and pre_max < low_signal_threshold:
                low_signal_failed = True
        elif '1  Cycle Int  Samp  44' in pre_rows.columns:
            pre_val = _parse_numeric_token(pre_rows.at[sample_idx, '1  Cycle Int  Samp  44'])
            if pre_val is not None and pre_val < low_signal_threshold:
                low_signal_failed = True
        # d13C
        d13_mean = np.nan
        d13_std = np.nan
        d13_used = 0
        d13_excl = 0
        d13_has_cycles = False
        if d13_value_col and d13_value_col in sample_cycles.columns:
            d13_vals = pd.to_numeric(sample_cycles[d13_value_col], errors='coerce')
            d13_cycles = sample_cycles.assign(_d13=d13_vals)
            d13_cycles = d13_cycles[d13_cycles['_d13'].notna()]
            if not d13_cycles.empty:
                d13_has_cycles = True
                d13_filtered = pd.Series(dtype='float64')
                if has_cycle_intensity and sat_mask_d13 is not None:
                    sat_mask = sat_mask_d13.reindex(d13_cycles.index).fillna(False)
                    d13_excl = int(sat_mask.sum())
                    if d13_excl > 0:
                        saturated_any = True
                    d13_filtered = d13_cycles.loc[~sat_mask, '_d13']
                else:
                    # No cycle intensity available: keep all valid cycles.
                    d13_filtered = d13_cycles['_d13']
                if not d13_filtered.empty:
                    d13_mean = float(d13_filtered.mean())
                    d13_std = float(d13_filtered.std()) if len(d13_filtered) > 1 else np.nan
                    d13_used = int(d13_filtered.shape[0])

        # d18O
        d18_mean = np.nan
        d18_std = np.nan
        d18_used = 0
        d18_excl = 0
        d18_has_cycles = False
        if d18_value_col and d18_value_col in sample_cycles.columns:
            d18_vals = pd.to_numeric(sample_cycles[d18_value_col], errors='coerce')
            d18_cycles = sample_cycles.assign(_d18=d18_vals)
            d18_cycles = d18_cycles[d18_cycles['_d18'].notna()]
            if not d18_cycles.empty:
                d18_has_cycles = True
                d18_filtered = pd.Series(dtype='float64')
                if has_cycle_intensity and sat_mask_d18 is not None:
                    sat_mask = sat_mask_d18.reindex(d18_cycles.index).fillna(False)
                    d18_excl = int(sat_mask.sum())
                    if d18_excl > 0:
                        saturated_any = True
                    d18_filtered = d18_cycles.loc[~sat_mask, '_d18']
                else:
                    # No cycle intensity available: keep all valid cycles.
                    d18_filtered = d18_cycles['_d18']
                if not d18_filtered.empty:
                    d18_mean = float(d18_filtered.mean())
                    d18_std = float(d18_filtered.std()) if len(d18_filtered) > 1 else np.nan
                    d18_used = int(d18_filtered.shape[0])

        # Apply cycle-derived means when available; otherwise keep existing pre values
        if np.isfinite(d13_mean):
            pre_rows.at[sample_idx, 'd 13C/12C  Mean'] = d13_mean
        if np.isfinite(d13_std):
            pre_rows.at[sample_idx, 'd 13C/12C  Std Dev'] = d13_std
        if np.isfinite(d18_mean):
            pre_rows.at[sample_idx, 'd 18O/16O  Mean'] = d18_mean
        if np.isfinite(d18_std):
            pre_rows.at[sample_idx, 'd 18O/16O  Std Dev'] = d18_std

        # Isotope-specific failure handling:
        # If one isotope has cycle data but all those cycles are excluded (e.g., persistent cup saturation),
        # keep the other isotope and force this isotope to NaN so it is not included in results.
        if d13_has_cycles and d13_used == 0:
            pre_rows.at[sample_idx, 'd 13C/12C  Mean'] = np.nan
            pre_rows.at[sample_idx, 'd 13C/12C  Std Dev'] = np.nan
        if d18_has_cycles and d18_used == 0:
            pre_rows.at[sample_idx, 'd 18O/16O  Mean'] = np.nan
            pre_rows.at[sample_idx, 'd 18O/16O  Std Dev'] = np.nan

        pre_rows.at[sample_idx, 'd13C Cycles Used'] = d13_used
        pre_rows.at[sample_idx, 'd18O Cycles Used'] = d18_used
        pre_rows.at[sample_idx, 'd13C Cycles Excluded'] = d13_excl
        pre_rows.at[sample_idx, 'd18O Cycles Excluded'] = d18_excl

        # Determine collector status
        pre_d13 = pre_rows.at[sample_idx, 'd 13C/12C  Mean'] if 'd 13C/12C  Mean' in pre_rows.columns else np.nan
        pre_d18 = pre_rows.at[sample_idx, 'd 18O/16O  Mean'] if 'd 18O/16O  Mean' in pre_rows.columns else np.nan
        # Any missing isotope value is treated as failed so it is grouped/marked with failed samples.
        failed = low_signal_failed or (not np.isfinite(pre_d13)) or (not np.isfinite(pre_d18))
        fully_saturated = (
            has_cycle_intensity and
            (d13_has_cycles or d18_has_cycles) and
            d13_used == 0 and d18_used == 0 and
            (d13_excl > 0 or d18_excl > 0)
        )
        if fully_saturated:
            pre_rows.at[sample_idx, 'Collector Status'] = 'Fully Saturated Collectors'
            pre_rows.at[sample_idx, 'd 13C/12C  Mean'] = np.nan
            pre_rows.at[sample_idx, 'd 13C/12C  Std Dev'] = np.nan
            pre_rows.at[sample_idx, 'd 18O/16O  Mean'] = np.nan
            pre_rows.at[sample_idx, 'd 18O/16O  Std Dev'] = np.nan
        elif failed:
            pre_rows.at[sample_idx, 'Collector Status'] = 'Failed Sample'
            if low_signal_failed:
                pre_rows.at[sample_idx, 'd 13C/12C  Mean'] = np.nan
                pre_rows.at[sample_idx, 'd 13C/12C  Std Dev'] = np.nan
                pre_rows.at[sample_idx, 'd 18O/16O  Mean'] = np.nan
                pre_rows.at[sample_idx, 'd 18O/16O  Std Dev'] = np.nan
        elif saturated_any:
            pre_rows.at[sample_idx, 'Collector Status'] = 'Partially Saturated Collectors'

    # Keep non-cycle rows (rows without Cycle Number)
    other_rows = work[work['_cycle_order'].isna()].copy()
    if not other_rows.empty and 'Collector Status' not in other_rows.columns:
        other_rows['Collector Status'] = 'OK'

    result = pd.concat([pre_rows, other_rows], axis=0).sort_index()
    result = result.drop(columns=['_cycle_order', '_cycle_group'], errors='ignore')
    return result

def _get_species_series(df):
    """Prefer Species column; else use Label species or Label identifier when species missing."""
    if df is None:
        return pd.Series(dtype=object)
    if 'Species' in df.columns and not df['Species'].isna().all():
        return df['Species']
    if 'Label' in df.columns and not df['Label'].isna().all():
        label_parts = df['Label'].apply(_split_label_species)
        label_ident = label_parts.map(lambda v: v[0] if v else None)
        label_species = label_parts.map(lambda v: v[1] if v else None)
        # Use species when present; otherwise fall back to identifier (first part of Label)
        return label_species.where(
            label_species.notna() & (label_species.astype(str).str.strip() != ''),
            label_ident
        )
    return pd.Series(index=df.index, dtype=object)

def _normalize_column_key(name):
    """Normalize column labels for robust matching across unicode variants."""
    if not isinstance(name, str):
        return ''
    text = name.strip()
    # Normalize common unicode variants (CO₂, µ/μ)
    text = text.replace('\u2082', '2')
    text = text.replace('\u00b5', 'u').replace('\u03bc', 'u')
    text = re.sub(r'\s+', ' ', text)
    return text.lower()

def _find_column(df, *candidates):
    """Find a column in df by exact or normalized label match."""
    if df is None:
        return None
    norm_map = {_normalize_column_key(col): col for col in df.columns}
    for cand in candidates:
        if cand in df.columns:
            return cand
        key = _normalize_column_key(cand)
        if key in norm_map:
            return norm_map[key]
    return None

def _split_label_species(label):
    """Split Label into identifier and species using 'Label - Species' convention."""
    if not isinstance(label, str):
        return label, None
    parts = label.split('-', 1)
    if len(parts) == 2:
        ident = parts[0].strip() or label
        species = parts[1].strip() or None
        return ident, species
    return label, None

def _canonicalize_header_columns(df):
    """Normalize key column names after multi-row header merge."""
    if df is None:
        return df
    rename = {}
    for col in df.columns:
        if not isinstance(col, str):
            continue
        low = _normalize_column_key(col)
        target = None
        if re.search(r'\bindex\b', low):
            target = 'Index'
        elif 'user name' in low:
            target = 'User name'
        elif 'start time' in low:
            target = 'Start Time'
        elif 'stop time' in low:
            target = 'Stop Time'
        elif re.search(r'\bstatus\b', low):
            target = 'Status'
        elif 'mark to pause' in low:
            target = 'Mark To Pause'
        elif re.search(r'\blabel\b', low):
            target = 'Label'
        elif re.search(r'\bcomment\b', low):
            target = 'Comment'
        elif 'run id' in low:
            target = 'Run ID'
        elif re.search(r'\bline\b', low):
            target = 'Line'
        elif re.search(r'\bvial\b', low):
            target = 'Vial'
        elif 'evaluate' in low:
            target = 'Evaluate'
        elif 'sample type' in low:
            target = 'Sample Type'
        elif 'reference' in low:
            target = 'Reference'
        elif 'cycle number' in low:
            target = 'Cycle Number'
        elif low == 'information' or low.endswith(' information'):
            target = 'Information'
        elif low == 'date':
            target = 'Date'
        if target and target not in df.columns and target not in rename.values():
            rename[col] = target
    if rename:
        df = df.rename(columns=rename)
    return df

def _parse_new_table_layout(raw_df):
    """Parse the 'New Table' layout with multi-row headers."""
    header_idx = None
    for i in range(min(len(raw_df), 20)):
        row_vals = raw_df.iloc[i].astype(str).tolist()
        if 'Index' in row_vals and 'User name' in row_vals:
            header_idx = i
            break
    if header_idx is None:
        return None

    header_row = raw_df.iloc[header_idx].tolist()
    header_start_idx = header_idx
    if header_idx > 0:
        prev_row = raw_df.iloc[header_idx - 1]
        if prev_row.notna().any():
            header_start_idx = header_idx - 1
    index_col_pos = None
    for idx, val in enumerate(header_row):
        if isinstance(val, str) and val.strip().lower() == 'index':
            index_col_pos = idx
            break

    data_start_idx = header_idx + 1
    if index_col_pos is not None:
        for i in range(header_idx + 1, len(raw_df)):
            val = raw_df.iat[i, index_col_pos]
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            text = str(val).strip()
            if text == '' or text.lower() == 'nan':
                continue
            if _parse_numeric_token(val) is not None:
                data_start_idx = i
                break

    unit_tokens = {'\u2030', 'â€°', '%', 'ppm', 'ppb', 'mv', 'v', 'c'}
    cols = []
    for col_idx in range(raw_df.shape[1]):
        parts = []
        for row_idx in range(header_start_idx, data_start_idx):
            val = raw_df.iat[row_idx, col_idx]
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            text = str(val).strip()
            if text == '' or text.lower() == 'nan':
                continue
            if text.lower() in unit_tokens:
                continue
            parts.append(text)
        dedup = []
        for part in parts:
            if not dedup or dedup[-1].lower() != part.lower():
                dedup.append(part)
        col_name = ' '.join(dedup) if dedup else f'Unnamed: {col_idx}'
        cols.append(col_name)

    df = raw_df.iloc[data_start_idx:].copy()
    df.columns = cols
    df = _canonicalize_header_columns(df)

    # Drop a units row if present immediately after headers (fallback)
    if len(df) > 0:
        unit_row = df.iloc[0]
        unit_hits = 0
        non_empty = 0
        for val in unit_row.values.tolist():
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            text = str(val).strip().lower()
            if text == '' or text == 'nan':
                continue
            non_empty += 1
            if text in unit_tokens:
                unit_hits += 1
        if non_empty > 0 and unit_hits / max(non_empty, 1) >= 0.6:
            df = df.iloc[1:].copy()

    if 'Index' in df.columns:
        if 'Cycle Number' in df.columns:
            df = df[df['Index'].notna() | df['Cycle Number'].notna()].copy()
        else:
            df = df[df['Index'].notna()].copy()

    return df
def _standardize_isotope_columns(df):
    """Map isotope columns to canonical names used throughout the app."""
    rename_map = {}
    for col in df.columns:
        if not isinstance(col, str):
            continue
        low = col.strip().lower()
        if 'd13' in low or 'δ13' in low:
            if 'mean' in low:
                rename_map[col] = 'd 13C/12C  Mean'
            elif 'sd' in low or 'std' in low:
                rename_map[col] = 'd 13C/12C  Std Dev'
        if 'd18' in low or 'δ18' in low:
            if 'mean' in low:
                rename_map[col] = 'd 18O/16O  Mean'
            elif 'sd' in low or 'std' in low:
                rename_map[col] = 'd 18O/16O  Std Dev'
    if rename_map:
        df = df.rename(columns=rename_map)
    return df

def extract_info_values(df):
    """Extract values from Information column with the specific format provided."""
    # Initialize new columns
    df['acid_temp'] = np.nan
    df['leak_rate'] = np.nan
    df['p_no_acid'] = np.nan
    df['p_gases'] = np.nan
    df['total_co2'] = np.nan
    df['co2_after_exp'] = np.nan
    df['left_mbar'] = np.nan
    df['right_mbar'] = np.nan
    df['left_pos'] = np.nan
    df['right_pos'] = np.nan
    df['vm1_after_transfer'] = np.nan

    # Regular expressions for extracting values
    patterns = {
        'acid_temp': r'Acid:\s*([\d.]+)',
        'leak_rate': r'LeakRate.*?:\s*([\d.]+)',
        'p_no_acid': r'P\s*no\s*Acid\s*:\s*([\d.]+)',
        'p_gases': r'P\s*gases\s*:\s*([\d.]+)',
        'total_co2': r'Total\s*CO(?:2|\u2082)\s*:\s*([\d.]+)',
        'co2_after_exp': r'CO(?:2|\u2082)\s*after\s*Exp\.:\s*([\d.]+)',
        'left_mbar': r'RefRe skipped: L mBar\s*([\d.]+)',
        'right_mbar': r'RefRe skipped: R mBar\s*([\d.]+)',
        'left_pos': r'L.*?Pos\s*([\d.]+)',
        'right_pos': r'R.*?Pos\s*([\d.]+)',
        'vm1_after_transfer': r'VM1 aftr Trfr\.:\s*([-\d.]+)'
    }

    # Extract values using regex
    for idx, row in df.iterrows():
        info = str(row['Information'])

        for col, pattern in patterns.items():
            match = re.search(pattern, info, flags=re.IGNORECASE)
            if match:
                df.at[idx, col] = float(match.group(1))

    return df

def identify_outliers(data, column, sigma_level):
    """
    Identify outliers in the specified column based on the sigma level.

    Parameters:
    - data: DataFrame containing the data.
    - column: The column name to check for outliers.
    - sigma_level: The number of standard deviations (sigma) for identifying outliers.

    Returns:
    - A boolean Series indicating True for outliers and False otherwise.
    """
    # Coerce to numeric to avoid silent failures on string/object columns
    series = pd.to_numeric(data[column], errors='coerce')
    mean_val, std_val, outliers = _compute_sigma_stats(series, sigma_level)
    if outliers is None:
        return pd.Series(False, index=data.index)
    return outliers.reindex(data.index, fill_value=False)


def _compute_sigma_stats(series, sigma_level):
    """Compute mean/std and outlier mask using a two-pass sigma calculation."""
    valid_mask = series.notna()
    if valid_mask.sum() < 2:
        return (np.nan, np.nan, None)

    vals = series[valid_mask]
    mean1 = vals.mean()
    std1 = vals.std()
    if not np.isfinite(mean1) or not np.isfinite(std1) or std1 == 0:
        return (mean1, std1, None)

    lower1 = mean1 - sigma_level * std1
    upper1 = mean1 + sigma_level * std1
    inliers1 = valid_mask & (series >= lower1) & (series <= upper1)
    base = series[inliers1]
    if base.dropna().shape[0] >= 2:
        mean2 = base.mean()
        std2 = base.std()
    else:
        mean2 = mean1
        std2 = std1

    if not np.isfinite(mean2) or not np.isfinite(std2) or std2 == 0:
        return (mean2, std2, None)

    lower2 = mean2 - sigma_level * std2
    upper2 = mean2 + sigma_level * std2
    outliers = valid_mask & ((series < lower2) | (series > upper2))
    return (mean2, std2, outliers)

def identify_outliers_iqr(data, column, iqr_multiplier=1.5):
    """
    Identify outliers in the specified column using the IQR method with a customizable multiplier.

    Parameters:
    - data: DataFrame containing the data.
    - column: The column name to check for outliers.
    - iqr_multiplier: Multiplier for the IQR to define the bounds for outliers.

    Returns:
    - A boolean Series indicating True for outliers and False otherwise.
    """
    # Coerce to numeric to avoid silent failures on string/object columns
    series = pd.to_numeric(data[column], errors='coerce')
    valid = series.dropna()
    if valid.empty:
        return pd.Series(False, index=data.index)

    # Calculate Q1, Q3, and IQR for the column
    q1 = valid.quantile(0.25)
    q3 = valid.quantile(0.75)
    iqr = q3 - q1
    if not np.isfinite(iqr):
        return pd.Series(False, index=data.index)

    # Define the upper and lower bounds for outliers using the provided multiplier
    upper_bound = q3 + iqr_multiplier * iqr
    lower_bound = q1 - iqr_multiplier * iqr

    # Identify outliers (values outside the upper and lower bounds)
    outliers = (series > upper_bound) | (series < lower_bound)

    return outliers.fillna(False)

# def calibrate_results(df):
#     """Calibrate results based on SHP2L standards."""
#     # Get SHP2L measurements (excluding outliers)
#     shp2l_data = df[df['Identifier 1'] == 'SHP2L'].copy()
#
#     # Calculate correction factors
#     d13c_correction = -0.7 - shp2l_data['d 13C/12C  Mean'].mean()
#     d18o_correction = -5.7 - shp2l_data['d 18O/16O  Mean'].mean()
#
#     # Create calibrated columns
#     df['d13C_calibrated'] = df['d 13C/12C  Mean'] + d13c_correction
#     df['d18O_calibrated'] = df['d 18O/16O  Mean'] + d18o_correction
#
#     return df


# Load standards reference (tolerant to encoding/case differences)
try:
    standards_df = pd.read_csv("standards.csv", encoding="utf-8")
except Exception:
    standards_df = pd.read_csv("Standards.csv", encoding="utf-8")
# Normalize isotopic type labels to match internal constants
try:
    standards_df['Isotopic_Value_Type'] = (
        standards_df['Isotopic_Value_Type']
        .astype(str)
        .str.strip()
        .replace({
            'VPDB(13C)': ISOTYPE_D13C,
            'VSMOW(18O)': ISOTYPE_D18O,
            'dVPDB(13C)': ISOTYPE_D13C,
            'dVSMOW(18O)': ISOTYPE_D18O,
            '?VPDB(13C)': ISOTYPE_D13C,
            '?VSMOW(18O)': ISOTYPE_D18O,
            'δVPDB(13C)': ISOTYPE_D13C,
            'δVSMOW(18O)': ISOTYPE_D18O,
            'Î´VPDB(13C)': ISOTYPE_D13C,
            'Î´VSMOW(18O)': ISOTYPE_D18O,
            '??VPDB(13C)': ISOTYPE_D13C,
            '??VSMOW(18O)': ISOTYPE_D18O,
        })
    )
except Exception:
    pass

def get_true_value(standard_name, isotopic_type):
    """Fetch the true isotopic value for a given standard and isotopic type."""
    match = standards_df[(standards_df['Standard'] == standard_name) &
                         (standards_df['Isotopic_Value_Type'] == isotopic_type)]
    if not match.empty:
        value = match['Value'].values[0]
        print(f"Found true value for {standard_name} ({isotopic_type}): {value}")
        return value
    else:
        raise ValueError(f"True value not found for {standard_name} with type {isotopic_type}")

def single_point_calibration(raw_sample, raw_std, true_std):
    """Apply single-point calibration formula."""
    calibrated_value = ((raw_sample + 1000) * (true_std + 1000)) / (raw_std + 1000) - 1000
    return calibrated_value

def double_point_calibration(raw_sample, raw_rm1, true_rm1, raw_rm2, true_rm2):
    """Apply double-point calibration formula."""
    m = (true_rm2 - true_rm1) / (raw_rm2 - raw_rm1)
    b = true_rm1 - m * raw_rm1
    calibrated_value = m * raw_sample + b
    return calibrated_value

def _filter_standards_remove_outliers(df, standards, method, sigma, iqr_mult):
    '''Return selected standards with outliers removed.'''
    if not standards:
        return pd.DataFrame(columns=df.columns)
    parts = []
    for std in standards:
        std_df = df[df['Identifier 1'] == std].copy()
        if std_df.empty:
            continue
        try:
            if method == 'Z-Score':
                out13 = identify_outliers(std_df, 'd 13C/12C  Mean', sigma)
                out18 = identify_outliers(std_df, 'd 18O/16O  Mean', sigma)
            else:
                out13 = identify_outliers_iqr(std_df, 'd 13C/12C  Mean', iqr_mult)
                out18 = identify_outliers_iqr(std_df, 'd 18O/16O  Mean', iqr_mult)
            keep = ~(out13 | out18)
            parts.append(std_df.loc[keep])
        except Exception:
            parts.append(std_df)
    if not parts:
        return pd.DataFrame(columns=df.columns)
    return pd.concat(parts, axis=0, ignore_index=True)

def _compute_linearity_fit(clean_df, y_col, x_col):
    '''Compute linear regression y = a + b*x. Returns dict with slope, intercept, r2, x_ref, n.'''
    result = {'slope': np.nan, 'intercept': np.nan, 'r2': np.nan, 'x_ref': np.nan, 'n': 0}
    if clean_df is None or clean_df.empty:
        return result
    x = pd.to_numeric(clean_df[x_col], errors='coerce')
    y = pd.to_numeric(clean_df[y_col], errors='coerce')
    m = np.isfinite(x) & np.isfinite(y)
    x = x[m]
    y = y[m]
    if len(x) < 2:
        return result
    lr = linregress(x, y)
    result['slope'] = float(lr.slope)
    result['intercept'] = float(lr.intercept)
    result['r2'] = float(lr.rvalue ** 2)
    result['x_ref'] = float(np.median(x.values))
    result['n'] = int(len(x))
    return result

def _apply_linearity_correction(df, intensity_col, fits):
    '''Apply linearity correction to raw and calibrated isotope columns.'''
    i = pd.to_numeric(df[intensity_col], errors='coerce')
    if 'd 13C/12C  Mean' in df.columns and np.isfinite(fits.get('d13C', {}).get('slope', np.nan)):
        slope = fits['d13C']['slope']; x_ref = fits['d13C']['x_ref']
        y = pd.to_numeric(df['d 13C/12C  Mean'], errors='coerce')
        df['d13C_linearity_corrected'] = (y - slope * (i - x_ref)).where(np.isfinite(y) & np.isfinite(i))
    if 'd 18O/16O  Mean' in df.columns and np.isfinite(fits.get('d18O', {}).get('slope', np.nan)):
        slope = fits['d18O']['slope']; x_ref = fits['d18O']['x_ref']
        y = pd.to_numeric(df['d 18O/16O  Mean'], errors='coerce')
        df['d18O_linearity_corrected'] = (y - slope * (i - x_ref)).where(np.isfinite(y) & np.isfinite(i))
    if 'd13C_calibrated' in df.columns and np.isfinite(fits.get('d13C', {}).get('slope', np.nan)):
        slope = fits['d13C']['slope']; x_ref = fits['d13C']['x_ref']
        y = pd.to_numeric(df['d13C_calibrated'], errors='coerce')
        df['d13C_calibrated_linearity_corrected'] = (y - slope * (i - x_ref)).where(np.isfinite(y) & np.isfinite(i))
    if 'd18O_calibrated' in df.columns and np.isfinite(fits.get('d18O', {}).get('slope', np.nan)):
        slope = fits['d18O']['slope']; x_ref = fits['d18O']['x_ref']
        y = pd.to_numeric(df['d18O_calibrated'], errors='coerce')
        df['d18O_calibrated_linearity_corrected'] = (y - slope * (i - x_ref)).where(np.isfinite(y) & np.isfinite(i))
    return df

def _interpolate_outliers_by_identifier2(df, outlier_mask, cols, id2_col='Identifier 2'):
    """Interpolate specified columns for rows flagged as outliers, using
    the sequence defined by ``Identifier 2`` as the order reference.

    Only values on outlier rows are replaced by the interpolation; non-outlier
    rows retain their original values. Interpolation is linear and uses the
    previous and next measurements in Identifier 2 order.

    Parameters
    ----------
    df : pandas.DataFrame
        Source dataframe.
    outlier_mask : pandas.Series of bool
        Boolean mask (aligned to df.index) indicating outlier rows.
    cols : list of str
        Columns to interpolate.
    id2_col : str
        Column used to define the sequence (default: 'Identifier 2').

    Returns
    -------
    pandas.DataFrame
        A copy of df with interpolated values for outlier rows.
    """
    if df is None or len(df) == 0 or not any(c in df.columns for c in cols):
        return df

    work = df.copy()

    # Build an order column from Identifier 2; prefer numeric, fallback to extracted number, then to original order
    if id2_col in work.columns:
        order = pd.to_numeric(work[id2_col], errors='coerce')
        if order.isna().all():
            # Try extracting numbers from strings
            try:
                order = work[id2_col].apply(lambda v: extract_number(v))
                order = pd.to_numeric(order, errors='coerce')
            except Exception:
                order = pd.Series(np.arange(len(work)), index=work.index)
    else:
        order = pd.Series(np.arange(len(work)), index=work.index)

    work['_order_irms'] = order
    work['_orig_pos_irms'] = np.arange(len(work))

    # Sort by order then by original position to keep stability; NaNs go to the end
    work_sorted = work.sort_values(['_order_irms', '_orig_pos_irms'], na_position='last')
    mask_sorted = outlier_mask.reindex(work_sorted.index).fillna(False)

    for col in cols:
        if col not in work_sorted.columns:
            continue
        s = pd.to_numeric(work_sorted[col], errors='coerce')
        s_masked = s.copy()
        s_masked[mask_sorted] = np.nan
        s_interp = s_masked.interpolate(method='linear', limit_direction='both')
        # Assign back only for the outlier rows
        idx_to_update = mask_sorted[mask_sorted].index
        work_sorted.loc[idx_to_update, col] = s_interp.loc[idx_to_update]

    # Restore original order
    work_sorted = work_sorted.sort_values('_orig_pos_irms')
    work_sorted = work_sorted.drop(columns=['_order_irms', '_orig_pos_irms'])
    return work_sorted

def _sanitize_filename(name: str) -> str:
    try:
        s = str(name)
    except Exception:
        return "output"
    s = re.sub(r'[\\/:*?"<>|]', '_', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s or "output"

def _build_client_filename(client_name: str, client_df: pd.DataFrame) -> str:
    client_part = _sanitize_filename(client_name) if client_name else "Client"
    try:
        raw_ids = [str(x) for x in client_df['Identifier'].dropna().unique().tolist()]
    except Exception:
        raw_ids = []
    # Always include the explicit list of Identifier 1 values (sanitized), no count summary
    ids_sanitized = [_sanitize_filename(x) for x in raw_ids]
    id_part = " ".join(ids_sanitized).strip()

    date_str = pd.Timestamp.today().strftime('%d%m%Y')
    # Use exact label requested, preserving '&'
    title = "Stable C&O isosopes results P2L"
    parts = [p for p in [client_part, id_part, title, date_str] if p]
    return (" ".join(parts) + ".xlsx").strip()

def calibrate_results(standards_df, full_df, selected_standards):
    """
    Calibrate results based on single or double standards for both d13C and d18O.

    Parameters:
    - standards_df: DataFrame containing filtered standards data (without outliers)
    - full_df: DataFrame containing all raw sample data to be calibrated
    - selected_standards: List of selected standards (1 or 2)

    Returns:
    - DataFrame with both d13C_calibrated and d18O_calibrated columns added
    """
    # Create a copy of the full dataframe to avoid modifying the original
    calibrated_df = full_df.copy()

    # Define isotopic types and corresponding column names
    isotopic_types = {
        ISOTYPE_D13C: ('d 13C/12C  Mean', 'd13C_calibrated'),
        ISOTYPE_D18O: ('d 18O/16O  Mean', 'd18O_calibrated')
    }

    for isotopic_type, (raw_column, calibrated_column) in isotopic_types.items():
        if len(selected_standards) == 1:
            # Single Point Calibration
            standard = selected_standards[0]
            # Use the mean value from filtered standards data
            raw_std = standards_df.loc[standards_df['Identifier 1'] == standard, raw_column].mean()
            true_std = get_true_value(standard, isotopic_type)
            calibrated_df[calibrated_column] = calibrated_df[raw_column].apply(
                lambda raw_sample: single_point_calibration(raw_sample, raw_std, true_std)
            )

        elif len(selected_standards) == 2:
            # Double Point Calibration
            standard1, standard2 = selected_standards
            # Use mean values from filtered standards data
            raw_rm1 = standards_df.loc[standards_df['Identifier 1'] == standard1, raw_column].mean()
            true_rm1 = get_true_value(standard1, isotopic_type)
            raw_rm2 = standards_df.loc[standards_df['Identifier 1'] == standard2, raw_column].mean()
            true_rm2 = get_true_value(standard2, isotopic_type)
            calibrated_df[calibrated_column] = calibrated_df[raw_column].apply(
                lambda raw_sample: double_point_calibration(raw_sample, raw_rm1, true_rm1, raw_rm2, true_rm2)
            )

        else:
            raise ValueError("Please select either one or two standards for calibration.")

    # print(calibrated_df.columns.tolist())

    return calibrated_df

def create_calibration_plots(standards_reference_df, measurement_df, selected_standards, color_param):
    """
    Create calibration plots for d13C and d18O using Plotly.

    Parameters:
    standards_reference_df (pd.DataFrame): DataFrame containing the reference standards data.
    measurement_df (pd.DataFrame): DataFrame containing the measured values.
    selected_standards (list): List of selected standard names.
    color_param (str): Column name in measurement_df to use for point coloring.

    Returns:
    dict: Dictionary containing calibration plots for d13C and d18O.
    """
    # Initialize dictionary for storing plots
    figs = {}

    # Define isotope mappings for processing
    isotopes = {
        ISOTYPE_D13C: {
            'y_label': 'd13C',
            'measurement_col': 'd 13C/12C  Mean'
        },
        ISOTYPE_D18O: {
            'y_label': 'd18O',
            'measurement_col': 'd 18O/16O  Mean'
        }
    }

    for isotope_type, isotope_data in isotopes.items():
        fig = go.Figure()
        true_values = []
        measured_values = []
        color_values = []

        # Build a shared coloraxis so all traces use the same colorbar
        coloraxis_cfg = dict(
            colorscale='Viridis',
            colorbar=dict(
                title='Date' if color_param == 'Date_ordinal' else color_param,
                thickness=20,
                len=0.75,
                y=0.5,
                yanchor='middle',
                x=1.15,
                xanchor='right'
            )
        )
        color_values_all, colorbar_category_ticks = _prepare_color_values(
            measurement_df[color_param] if color_param in measurement_df.columns else None
        )
        if color_values_all is not None:
            try:
                cdata = pd.to_numeric(color_values_all, errors='coerce')
                cmin = float(np.nanmin(cdata))
                cmax = float(np.nanmax(cdata))
                if np.isfinite(cmin) and np.isfinite(cmax):
                    coloraxis_cfg.update(cmin=cmin, cmax=cmax)
            except Exception:
                pass
        if color_param == 'Date_ordinal' and color_param in measurement_df.columns:
            tickvals, ticktext = _build_date_colorbar_ticks(measurement_df[color_param])
            if tickvals and ticktext:
                coloraxis_cfg['colorbar'].update(tickmode='array', tickvals=tickvals, ticktext=ticktext)
        elif colorbar_category_ticks is not None:
            tickvals, ticktext = colorbar_category_ticks
            if tickvals and ticktext:
                coloraxis_cfg['colorbar'].update(tickmode='array', tickvals=tickvals, ticktext=ticktext)

        for standard in selected_standards:



            # Get true value for the standard
            try:
                true_value = standards_reference_df[
                    (standards_reference_df['Standard'] == standard) &
                    (standards_reference_df['Isotopic_Value_Type'] == isotope_type)
                ]['Value'].iloc[0]
            except IndexError:
                st.warning(f"No true value found for standard {standard} and isotope {isotope_type}.")
                continue

            # Get measured values and color parameter
            measured_series = pd.to_numeric(
                measurement_df.loc[measurement_df['Identifier 1'] == standard, isotope_data['measurement_col']],
                errors='coerce'
            )
            valid_mask = measured_series.notna() & np.isfinite(measured_series)
            measured_values_for_standard = measured_series.loc[valid_mask].values

            color_values_for_standard = None
            if color_values_all is not None:
                color_values_for_standard = color_values_all.loc[
                    measurement_df['Identifier 1'] == standard
                ]
                color_values_for_standard = color_values_for_standard.loc[valid_mask].values

            print(f"Standard: {standard}")
            print(f"Measured values for {isotope_data['y_label']}: {measured_values_for_standard}")
            print(f"Color values: {color_values_for_standard}")

            # Skip standards with no measurements
            if len(measured_values_for_standard) == 0:
                st.warning(f"No measured values found for standard {standard}. Skipping.")
                continue

            # Append values for calibration processing
            true_val = pd.to_numeric(pd.Series([true_value]), errors='coerce').iloc[0]
            if not np.isfinite(true_val):
                st.warning(f"Invalid true value for standard {standard}. Skipping.")
                continue
            true_values.extend([true_val] * len(measured_values_for_standard))
            measured_values.extend(measured_values_for_standard)
            if color_values_for_standard is not None:
                color_values.extend(color_values_for_standard)

            # Add scatter points for this standard
            marker_kwargs = dict(size=10)
            if color_values_for_standard is not None and pd.notna(color_values_for_standard).any():
                marker_kwargs.update(color=color_values_for_standard, coloraxis='coloraxis')
            else:
                marker_kwargs.update(color='rgba(150,150,150,0.8)')
            fig.add_trace(go.Scatter(
                x=[true_value] * len(measured_values_for_standard),
                y=measured_values_for_standard,
                mode='markers',
                name=f'{standard}',
                marker=marker_kwargs
            ))

        # Determine calibration method (single or double anchor)
        true_arr = np.array(true_values, dtype=float)
        measured_arr = np.array(measured_values, dtype=float)
        valid = np.isfinite(true_arr) & np.isfinite(measured_arr)
        true_arr = true_arr[valid]
        measured_arr = measured_arr[valid]

        if len(selected_standards) == 1:
            # Single anchor calibration
            if len(true_arr) > 0 and len(measured_arr) > 0:
                offset = np.mean(measured_arr - true_arr)
                annotation_text = f"Offset = {offset:.3f}"
                try:
                    x_min, x_max = float(np.min(true_arr)) - 1, float(np.max(true_arr)) + 1
                except ValueError:
                    x_min, x_max = -1, 1
                y_range = [x_min + offset, x_max + offset]

                # Add offset line
                fig.add_trace(go.Scatter(
                    x=[x_min, x_max],
                    y=y_range,
                    mode='lines',
                    name='Offset Line',
                    line=dict(color='orange', dash='dash')
                ))
            else:
                annotation_text = "No valid points for calibration"
        else:
            # Double anchor calibration
            try:
                if len(true_arr) < 2:
                    raise ValueError("Insufficient data for linear regression.")
                slope, intercept, _, _, _ = linregress(true_arr, measured_arr)
                annotation_text = f"y = {slope:.3f}x + {intercept:.3f}"
                x_min, x_max = float(np.min(true_arr)) - 1, float(np.max(true_arr)) + 1
                x_range = [x_min, x_max]
                y_range = [slope * x + intercept for x in x_range]

                # Add calibration line
                fig.add_trace(go.Scatter(
                    x=x_range,
                    y=y_range,
                    mode='lines',
                    name='Calibration Line',
                    line=dict(color='blue')
                ))
            except ValueError:
                st.warning("Insufficient data for linear regression.")

        # Update layout with annotation and axis labels (attach shared coloraxis)
        fig.update_layout(
            title=f"{'Single' if len(selected_standards) == 1 else 'Double'} Anchor Calibration for {isotope_type}",
            xaxis_title=f"True {isotope_data['y_label']} value",
            yaxis_title=f"Raw/Measured {isotope_data['y_label']} value",
            showlegend=True,
            width=900,   # Increased width to accommodate colorbar
            height=600,
            margin=dict(r=150),  # Add right margin for colorbar
            coloraxis=coloraxis_cfg,
            annotations=[
                dict(
                    x=0.05, y=0.85, xref="paper", yref="paper",  # Adjusted y position for annotation
                    text=annotation_text,
                    showarrow=False,
                    font=dict(size=12, color="black"),
                    align="left",
                    bordercolor="black",
                    borderwidth=1,
                    borderpad=4,
                    bgcolor="white"
                )
            ]
        )

        figs[isotope_type] = fig

    return figs

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
        title='Date' if color_param == 'Date_ordinal' else color_param,
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

    # Update layout with right margin for colorbar
    fig.update_layout(
        title_text='Diagnostic Plots',
        height=2600,
        showlegend=False,
        margin=dict(r=150)  # Add right margin for colorbar
    )

    return fig


def download_excel(df, outliers=None, filename="data.xlsx", selected_standards=None,
                   calibration_type=None, sigma_level=None, irq_multiplier=None,
                   client_name=None, comment_map=None):
    """
    Creates a download button for exporting DataFrames as an Excel file with multiple sheets.

    Parameters:
    - df (DataFrame): The main DataFrame to be downloaded.
    - outliers (DataFrame): Optional DataFrame containing outliers data.
    - filename (str): The filename for the download. Default is "data.xlsx".
    - selected_standards (list): List of selected standards for calibration.
    """
    if not any(col in df.columns for col in ['d13C_calibrated', 'd18O_calibrated']):
        if not st.warning("Data has not been calibrated. Do you want to continue downloading without calibration data?") or not st.button("Continue", key=f"continue_btn_{filename}"):
            return
    
    # Convert the DataFrame to Excel format in memory
    towrite = io.BytesIO()
    
    with pd.ExcelWriter(towrite, engine="xlsxwriter") as writer:
        # Split data into standards and non-standards
        standards_mask = df['Identifier 1'].isin(selected_standards) if selected_standards else pd.Series(False, index=df.index)
        main_data = df[~standards_mask].copy()

        # Calculate statistics
        total_samples = len(df)
        outliers_stats = {}
        if outliers is not None and not outliers.empty:
            outliers_by_category = outliers.groupby('Category').size()
            outliers_stats = {
                cat: {'count': count, 'percentage': (count/total_samples)*100}
                for cat, count in outliers_by_category.items()
            }
        
        final_analyses = total_samples
        if outliers is not None:
            final_analyses -= len(outliers)
        if False and selected_standards:  # legacy block disabled; see new block below
            final_analyses -= len(df[standards_mask])

        # Create Statistics sheet
        stats_data = [
            ['Total Samples', total_samples],
            ['Final Analyses', final_analyses],
            ['', ''],
            ['Outliers Statistics:', '']
        ]
        
        if outliers_stats:
            for category, stat in outliers_stats.items():
                stats_data.append([
                    f'{category} Outliers',
                    f'{stat["count"]} ({stat["percentage"]:.1f}%)'
                ])
        
        stats_df = pd.DataFrame(stats_data, columns=['Metric', 'Value'])
        stats_df.to_excel(writer, index=False, sheet_name='Statistics')

        # Write main data to Data sheet
        main_data.to_excel(writer, index=False, sheet_name="Data")

        # Build Client Output sheet with corrected values and information box
        try:
            # Determine linearity fits (reuse from session or recompute on-the-fly from standards)
            fits = st.session_state.get('linearity_fits') if isinstance(st.session_state, dict) else None
            intensity_col = '1  Cycle Int  Samp  44'

            # If no fits in session, compute using currently selected standards (cleaned by chosen outlier method)
            if (not fits) and selected_standards:
                try:
                    _method = calibration_type or st.session_state.get("calibration_type") or "IQR"
                    _sigma = sigma_level if sigma_level is not None else st.session_state.get("sigma_level", 1.0)
                    _iqr = irq_multiplier if irq_multiplier is not None else st.session_state.get("irq_multiplier", 1.5)
                    clean_stds_all = _filter_standards_remove_outliers(df, selected_standards, _method, _sigma, _iqr)
                    fit13 = _compute_linearity_fit(clean_stds_all, 'd 13C/12C  Mean', intensity_col)
                    fit18 = _compute_linearity_fit(clean_stds_all, 'd 18O/16O  Mean', intensity_col)
                    fits = {
                        'd13C': {'slope': fit13.get('slope', np.nan), 'x_ref': fit13.get('x_ref', np.nan)},
                        'd18O': {'slope': fit18.get('slope', np.nan), 'x_ref': fit18.get('x_ref', np.nan)},
                        'raw': {'fit13': fit13, 'fit18': fit18},
                    }
                except Exception:
                    fits = None

            # Build corrected columns with best available data
            def _build_corrected(series_cal, series_raw, isotope_key):
                try:
                    s_cal = pd.to_numeric(series_cal, errors='coerce') if series_cal is not None else None
                except Exception:
                    s_cal = None
                try:
                    s_raw = pd.to_numeric(series_raw, errors='coerce') if series_raw is not None else None
                except Exception:
                    s_raw = None
                if s_cal is not None and f"{isotope_key}_calibrated_linearity_corrected" in df.columns:
                    # Prefer precomputed calibrated+linearity-corrected column if present in export df
                    return pd.to_numeric(df[f"{isotope_key}_calibrated_linearity_corrected"], errors='coerce')
                if s_cal is not None and fits and np.isfinite(fits.get(isotope_key, {}).get('slope', np.nan)) and intensity_col in df.columns:
                    # Apply linearity correction to calibrated values
                    i = pd.to_numeric(df[intensity_col], errors='coerce')
                    slope = fits[isotope_key]['slope']; xr = fits[isotope_key]['x_ref']
                    return (s_cal - slope * (i - xr)).where(np.isfinite(s_cal) & np.isfinite(i))
                # Fallbacks
                if s_cal is not None:
                    return s_cal
                if s_raw is not None and fits and np.isfinite(fits.get(isotope_key, {}).get('slope', np.nan)) and intensity_col in df.columns:
                    i = pd.to_numeric(df[intensity_col], errors='coerce')
                    slope = fits[isotope_key]['slope']; xr = fits[isotope_key]['x_ref']
                    return (s_raw - slope * (i - xr)).where(np.isfinite(s_raw) & np.isfinite(i))
                return s_raw if s_raw is not None else pd.Series(index=df.index, dtype=float)

            corrected_d13 = _build_corrected(
                df.get('d13C_calibrated'),
                df.get('d 13C/12C  Mean'),
                'd13C'
            )
            corrected_d18 = _build_corrected(
                df.get('d18O_calibrated'),
                df.get('d 18O/16O  Mean'),
                'd18O'
            )

            # Prepare client output dataframe (non-standards only)
            species_series = _get_species_series(df)
            client_df = pd.DataFrame({
                'Identifier': df['Identifier 1'],
                'Sample #': df.get('Identifier 2', pd.Series(index=df.index, dtype=object)),
                'Species': species_series,
                'd13C (‰, VPDB)  Mean': pd.to_numeric(df.get('d 13C/12C  Mean'), errors='coerce'),
                'd13C (‰, VPDB)  Std Dev': pd.to_numeric(df.get('d 13C/12C  Std Dev'), errors='coerce'),
                'd18O (‰, VPDB)  Mean': pd.to_numeric(df.get('d 18O/16O  Mean'), errors='coerce'),
                'd18O (‰, VPDB)  Std Dev': pd.to_numeric(df.get('d 18O/16O  Std Dev'), errors='coerce'),
                'Corrected d13C (‰, VPDB)': corrected_d13,
                'Corrected d18O (‰, VPDB)': corrected_d18,
            })

            # Keep only non-standards entries if selected_standards is provided
            if selected_standards:
                client_df = client_df[~df['Identifier 1'].isin(selected_standards)]

            # Round numeric columns to 2 decimals specifically for Client Output
            round_cols = [
                'd13C (‰, VPDB)  Mean', 'd13C (‰, VPDB)  Std Dev',
                'd18O (‰, VPDB)  Mean', 'd18O (‰, VPDB)  Std Dev',
                'Corrected d13C (‰, VPDB)', 'Corrected d18O (‰, VPDB)'
            ]
            for rc in round_cols:
                if rc in client_df.columns:
                    client_df[rc] = pd.to_numeric(client_df[rc], errors='coerce').round(2)

            # Do not write Client Output into dataset workbook; handled as a separate file below
            client_sheet = "Client Output"
            workbook = writer.book
            # Intentionally do not create worksheet here to avoid including it in dataset file

            # Basic formatting: header bold, corrected columns purple
            header_fmt = workbook.add_format({'bold': True})
            corrected_hdr_fmt = workbook.add_format({'bold': True, 'font_color': '#6A1B9A'})
            num_fmt = workbook.add_format({'num_format': '0.00'})
            num_fmt_sd = workbook.add_format({'num_format': '0.00'})

            # Set column widths and header formats
            headers = list(client_df.columns)
            for col_idx, col_name in enumerate(headers):
                fmt = header_fmt
                if 'Corrected' in col_name:
                    fmt = corrected_hdr_fmt
                worksheet.write(0, col_idx, col_name, fmt)
                # Reasonable widths
                width = 15
                if col_name in ('Identifier', 'Species'):
                    width = 18
                elif 'Corrected' in col_name:
                    width = 22
                worksheet.set_column(col_idx, col_idx, width)

            # Apply numeric format to measure columns
            meas_cols = [
                'd13C (‰, VPDB)  Mean', 'd13C (‰, VPDB)  Std Dev',
                'd18O (‰, VPDB)  Mean', 'd18O (‰, VPDB)  Std Dev',
                'Corrected d13C (‰, VPDB)', 'Corrected d18O (‰, VPDB)'
            ]
            for col_name in meas_cols:
                if col_name in headers:
                    col_idx = headers.index(col_name)
                    worksheet.set_column(col_idx, col_idx, None, num_fmt if 'Std Dev' not in col_name else num_fmt_sd)

            # Compute SHP2L precision over period using standards-style logic
            d13c_sd_val = np.nan
            d18o_sd_val = np.nan
            n_used = 0
            try:
                _method = calibration_type or st.session_state.get("calibration_type") or "IQR"
                _sigma = sigma_level if sigma_level is not None else st.session_state.get("sigma_level", 1.0)
                _iqr = irq_multiplier if irq_multiplier is not None else st.session_state.get("irq_multiplier", 1.5)

                shp = df[df['Identifier 1'] == 'SHP2L'].copy() if 'Identifier 1' in df.columns else pd.DataFrame()
                if not shp.empty:
                    # Remove outliers on SHP2L like in standards table
                    if _method == "Z-Score":
                        m13 = identify_outliers(shp, 'd 13C/12C  Mean', _sigma)
                        m18 = identify_outliers(shp, 'd 18O/16O  Mean', _sigma)
                    else:
                        m13 = identify_outliers_iqr(shp, 'd 13C/12C  Mean', _iqr)
                        m18 = identify_outliers_iqr(shp, 'd 18O/16O  Mean', _iqr)
                    clean_shp = shp.loc[~(m13 | m18)].copy()
                    n_used = len(clean_shp)

                    # Ensure we have fits; compute from selected standards or SHP2L itself
                    if not fits:
                        try:
                            if selected_standards:
                                clean_all = _filter_standards_remove_outliers(df, selected_standards, _method, _sigma, _iqr)
                            else:
                                clean_all = clean_shp
                            f13 = _compute_linearity_fit(clean_all, 'd 13C/12C  Mean', intensity_col)
                            f18 = _compute_linearity_fit(clean_all, 'd 18O/16O  Mean', intensity_col)
                            fits = {
                                'd13C': {'slope': f13.get('slope', np.nan), 'x_ref': f13.get('x_ref', np.nan)},
                                'd18O': {'slope': f18.get('slope', np.nan), 'x_ref': f18.get('x_ref', np.nan)},
                            }
                        except Exception:
                            fits = None

                    # Compute linearity-corrected precision
                    y13s = pd.to_numeric(clean_shp.get('d 13C/12C  Mean'), errors='coerce')
                    y18s = pd.to_numeric(clean_shp.get('d 18O/16O  Mean'), errors='coerce')
                    if fits and np.isfinite(fits.get('d13C', {}).get('slope', np.nan)) and intensity_col in clean_shp.columns:
                        i = pd.to_numeric(clean_shp[intensity_col], errors='coerce')
                        y13s = (y13s - fits['d13C']['slope'] * (i - fits['d13C']['x_ref'])).where(np.isfinite(y13s) & np.isfinite(i))
                    if fits and np.isfinite(fits.get('d18O', {}).get('slope', np.nan)) and intensity_col in clean_shp.columns:
                        i = pd.to_numeric(clean_shp[intensity_col], errors='coerce')
                        y18s = (y18s - fits['d18O']['slope'] * (i - fits['d18O']['x_ref'])).where(np.isfinite(y18s) & np.isfinite(i))
                    d13c_sd_val = float(y13s.std()) if y13s is not None else np.nan
                    d18o_sd_val = float(y18s.std()) if y18s is not None else np.nan
            except Exception:
                pass

            # Write Equipment and standard deviation block on the right
            equip_title_fmt = workbook.add_format({'bold': True})
            worksheet.write(1, 10, "Equiment:", equip_title_fmt)
            worksheet.write(1, 11, "ThermoFisher Scientific MAT253 gas isotope ratio mass spectrometer")
            worksheet.write(2, 11, "Kiel IV automated carbonate preparation device")
            worksheet.write(4, 10, "Standard deviation of SHP2L over measurement period:", equip_title_fmt)
            worksheet.write(5, 11, f"{0.00 if np.isnan(d13c_sd_val) else d13c_sd_val:.2f} ‰ for d13C")
            worksheet.write(6, 11, f"{0.00 if np.isnan(d18o_sd_val) else d18o_sd_val:.2f} ‰ for d18O")
            worksheet.write(7, 11, f"{n_used} n")

            # Insert textbox with provided content
            materials_text = (
                "When results produced at P2L are being published, we suggest to use the following text in the “Material and Methods” section of the publication:\n\n"
                "\"Analyses on (your samples) for determination of d13C and d18O were performed at the Paleoceanography and Paleoclimatology Laboratory, School of Arts, Sciences and Humanities of the University of Sāo Paulo, Brazil. The laboratory is equipped with a Thermo Fisher Scientific™ MAT253 isotope ratio mass spectrometer (IRMS) coupled with a Thermo Fisher Scientific™ Kiel IV carbonate preparation device. The details on the laboratory analytical setup and performance are described in Crivellari et al. (2021). The IRMS measures the isotopic composition of the CO2 developed by the reaction between the sample carbonate and orthophosphoric acid at 70°C. Measurements were calibrated against repeated analyses of SHP2L reference material which is used as internal working standard (Crivellari et al., 2021). SHP2L is in turn calibrated against international reference material NBS19 and values are anchored to the Vienna Pee Dee Belemnite (VPDB) scale. Analytical precision was better than (please use the value informed by P2L) ‰ for d13C and (please use the value informed by P2L) ‰ for d18O (±1 s, n = please use the value informed by P2L).\"\n\n"
                "Reference\nCrivellari, S., Viana, P.J., Campos, M.D., Kuhnert, H., Lopes, A.B.M., da Cruz, F.W., Chiessi, C.M., 2021. Development and characterization of a new in-house reference material for stable carbon and oxygen isotopes analyses. Journal of Analytical Atomic Spectrometry 36, 1125-1134. DOI: 10.1039/D1JA00030F."
            )
            # Skip adding textbox since the sheet is not created in this workbook
        except Exception:
            # Silently skip any errors in this bypassed block
            pass
        
        # Write outliers to second sheet only if they exist and we want to exclude them
        if outliers is not None and not outliers.empty and df is not None:
            filtered_outliers = outliers[~outliers['Identifier 1'].isin(selected_standards)] if selected_standards else outliers
            if not filtered_outliers.empty:
                # Add Category column if it doesn't exist
                if 'Category' not in filtered_outliers.columns:
                    filtered_outliers['Category'] = 'Statistical'  # Default category for legacy outliers
                
                # Create category-wise sheets
                for category in filtered_outliers['Category'].unique():
                    category_outliers = filtered_outliers[filtered_outliers['Category'] == category]
                    if not category_outliers.empty:
                        sheet_name = f"Outliers - {category}"
                        if len(sheet_name) > 31:  # Excel sheet name length limit
                            sheet_name = sheet_name[:31]
                        category_outliers.to_excel(writer, index=False, sheet_name=sheet_name)
            
        # Create standards sheet if standards are selected
        if selected_standards:
            standards_data = []
            
            # Create a separate sheet for standards measurements
            standards_measurements = df[standards_mask].copy()
            if not standards_measurements.empty:
                standards_measurements.to_excel(writer, index=False, sheet_name="Standards Measurements")
                
            for standard in selected_standards:
                standard_df = df[df['Identifier 1'] == standard].copy()
                if not standard_df.empty:
                    # Calculate precision and averages
                    d13c_precision = standard_df['d 13C/12C  Mean'].std()
                    d13c_average = standard_df['d 13C/12C  Mean'].mean()
                    d18o_precision = standard_df['d 18O/16O  Mean'].std()
                    d18o_average = standard_df['d 18O/16O  Mean'].mean()
                    
                    standards_data.append({
                        'Standard': standard,
                        'd13C Precision': d13c_precision,
                        'd13C Average': d13c_average,
                        'd18O Precision': d18o_precision,
                        'd18O Average': d18o_average,
                        'Sample Count': len(standard_df),
                        'Calibration Type': 'Single Anchor' if len(selected_standards) == 1 else 'Double Anchor'
                    })
                    
            if standards_data:
                # Create summary DataFrame
                standards_summary = pd.DataFrame(standards_data)
                standards_summary.to_excel(writer, index=False, sheet_name="Standards Results")
                
                # Get workbook and worksheet
                workbook = writer.book
                worksheet = writer.sheets["Standards Results"]
                
                # Add text description of calibration plots
                row_offset = len(standards_data) + 3
                worksheet.write(row_offset, 0, "Calibration plots are available in the Calibration tab of the application.")
                worksheet.write(row_offset + 1, 0, f"Calibration type: {'Single' if len(selected_standards) == 1 else 'Double'} Anchor")
                worksheet.write(row_offset + 2, 0, f"Standards used: {', '.join(selected_standards)}")

        # New standards export block aligned with Calibration outlier filtering
        if selected_standards:
            # Determine outlier method/thresholds from args or session_state
            _method = calibration_type or st.session_state.get("calibration_type") or "IQR"
            _sigma = sigma_level if sigma_level is not None else st.session_state.get("sigma_level", 1.0)
            _iqr = irq_multiplier if irq_multiplier is not None else st.session_state.get("irq_multiplier", 1.5)

            standards_rows = []

            # Write (unfiltered) standards measurements if not already present
            try:
                if "Standards Measurements" not in writer.sheets:
                    std_mask = df['Identifier 1'].isin(selected_standards)
                    df[std_mask].to_excel(writer, index=False, sheet_name="Standards Measurements")
            except Exception:
                pass

            # Build a consolidated clean standards frame for fits/params
            try:
                clean_stds_all = _filter_standards_remove_outliers(
                    df,
                    selected_standards,
                    _method,
                    _sigma,
                    _iqr,
                )
            except Exception:
                clean_stds_all = df[df['Identifier 1'].isin(selected_standards)].copy()

            # Compute linearity fits on cleaned standards (used for precision-after-correction)
            intensity_col = '1  Cycle Int  Samp  44'
            try:
                fit13 = _compute_linearity_fit(clean_stds_all, 'd 13C/12C  Mean', intensity_col)
            except Exception:
                fit13 = {'slope': np.nan, 'intercept': np.nan, 'r2': np.nan, 'x_ref': np.nan, 'n': 0}
            try:
                fit18 = _compute_linearity_fit(clean_stds_all, 'd 18O/16O  Mean', intensity_col)
            except Exception:
                fit18 = {'slope': np.nan, 'intercept': np.nan, 'r2': np.nan, 'x_ref': np.nan, 'n': 0}

            # Build per-standard summary rows, including precision after linearity correction
            for _std in selected_standards:
                _std_df = df[df['Identifier 1'] == _std].copy()
                if _std_df.empty:
                    continue

                _total = len(_std_df)
                try:
                    if _method == "Z-Score":
                        _d13 = identify_outliers(_std_df, 'd 13C/12C  Mean', _sigma)
                        _d18 = identify_outliers(_std_df, 'd 18O/16O  Mean', _sigma)
                    else:
                        _d13 = identify_outliers_iqr(_std_df, 'd 13C/12C  Mean', _iqr)
                        _d18 = identify_outliers_iqr(_std_df, 'd 18O/16O  Mean', _iqr)
                    _clean = _std_df.loc[~(_d13 | _d18)].copy()
                except Exception:
                    _clean = _std_df.copy()

                _included = len(_clean)
                _d13p = _clean['d 13C/12C  Mean'].std()
                _d13m = _clean['d 13C/12C  Mean'].mean()
                _d18p = _clean['d 18O/16O  Mean'].std()
                _d18m = _clean['d 18O/16O  Mean'].mean()

                # Precision after linearity correction (if fits available)
                _d13p_lin = np.nan
                _d18p_lin = np.nan
                try:
                    if np.isfinite(fit13.get('slope', np.nan)) and intensity_col in _clean.columns:
                        i = pd.to_numeric(_clean[intensity_col], errors='coerce')
                        y = pd.to_numeric(_clean['d 13C/12C  Mean'], errors='coerce')
                        corr = (y - fit13['slope'] * (i - fit13['x_ref'])).where(np.isfinite(y) & np.isfinite(i))
                        _d13p_lin = float(corr.std())
                    if np.isfinite(fit18.get('slope', np.nan)) and intensity_col in _clean.columns:
                        i = pd.to_numeric(_clean[intensity_col], errors='coerce')
                        y = pd.to_numeric(_clean['d 18O/16O  Mean'], errors='coerce')
                        corr = (y - fit18['slope'] * (i - fit18['x_ref'])).where(np.isfinite(y) & np.isfinite(i))
                        _d18p_lin = float(corr.std())
                except Exception:
                    pass

                _method_label = f"Z-Score (Ïƒ={_sigma})" if _method == "Z-Score" else f"IQR (Ã—{_iqr})"

                standards_rows.append({
                    'Standard': _std,
                    'd13C Precision': _d13p,
                    'd13C Precision (Lin-Corr)': _d13p_lin,
                    'd13C Average': _d13m,
                    'd18O Precision': _d18p,
                    'd18O Precision (Lin-Corr)': _d18p_lin,
                    'd18O Average': _d18m,
                    'Sample Count': _included,
                    'Total Samples': _total,
                    'Outlier Method': _method_label,
                    'Calibration Type': 'Single Anchor' if len(selected_standards) == 1 else 'Double Anchor',
                })

            if standards_rows:
                # Write the per-standard summary table
                pd.DataFrame(standards_rows).to_excel(writer, index=False, sheet_name="Standards Results")

                # Append calibration and linearity parameters below the table
                try:
                    worksheet = writer.sheets["Standards Results"]
                    start_row = len(standards_rows) + 2

                    # Calibration parameters section
                    worksheet.write(start_row, 0, "Calibration Parameters")
                    start_row += 1
                    cal_type_text = 'Single Anchor' if len(selected_standards) == 1 else 'Double Anchor'
                    worksheet.write(start_row, 0, f"Type: {cal_type_text}")
                    start_row += 1
                    worksheet.write(start_row, 0, f"Standards used: {', '.join(selected_standards)}")
                    start_row += 2

                    # Compute calibration parameters from cleaned standards
                    try:
                        if len(selected_standards) == 1:
                            std = selected_standards[0]
                            # d13C single-point
                            raw13 = pd.to_numeric(clean_stds_all.loc[clean_stds_all['Identifier 1'] == std, 'd 13C/12C  Mean'], errors='coerce').mean()
                            tru13 = get_true_value(std, ISOTYPE_D13C)
                            worksheet.write(start_row, 0, "d13C Single-Point: raw_std(mean)")
                            worksheet.write(start_row, 1, float(raw13) if pd.notna(raw13) else np.nan)
                            worksheet.write(start_row, 2, "true_std")
                            worksheet.write(start_row, 3, float(tru13))
                            start_row += 1
                            # d18O single-point
                            raw18 = pd.to_numeric(clean_stds_all.loc[clean_stds_all['Identifier 1'] == std, 'd 18O/16O  Mean'], errors='coerce').mean()
                            tru18 = get_true_value(std, ISOTYPE_D18O)
                            worksheet.write(start_row, 0, "d18O Single-Point: raw_std(mean)")
                            worksheet.write(start_row, 1, float(raw18) if pd.notna(raw18) else np.nan)
                            worksheet.write(start_row, 2, "true_std")
                            worksheet.write(start_row, 3, float(tru18))
                            start_row += 2
                            worksheet.write(start_row, 0, "Formula")
                            worksheet.write(start_row, 1, "((raw+1000)*(true+1000))/(raw_std+1000)-1000")
                            start_row += 2
                        elif len(selected_standards) == 2:
                            s1, s2 = selected_standards
                            # d13C double-point
                            raw13_1 = pd.to_numeric(clean_stds_all.loc[clean_stds_all['Identifier 1'] == s1, 'd 13C/12C  Mean'], errors='coerce').mean()
                            raw13_2 = pd.to_numeric(clean_stds_all.loc[clean_stds_all['Identifier 1'] == s2, 'd 13C/12C  Mean'], errors='coerce').mean()
                            tru13_1 = get_true_value(s1, ISOTYPE_D13C)
                            tru13_2 = get_true_value(s2, ISOTYPE_D13C)
                            m13 = (tru13_2 - tru13_1) / (raw13_2 - raw13_1) if pd.notna(raw13_1) and pd.notna(raw13_2) else np.nan
                            b13 = tru13_1 - m13 * raw13_1 if pd.notna(m13) and pd.notna(raw13_1) else np.nan
                            worksheet.write(start_row, 0, "d13C Double-Point: slope (m)")
                            worksheet.write(start_row, 1, float(m13) if pd.notna(m13) else np.nan)
                            worksheet.write(start_row, 2, "intercept (b)")
                            worksheet.write(start_row, 3, float(b13) if pd.notna(b13) else np.nan)
                            start_row += 1
                            # d18O double-point
                            raw18_1 = pd.to_numeric(clean_stds_all.loc[clean_stds_all['Identifier 1'] == s1, 'd 18O/16O  Mean'], errors='coerce').mean()
                            raw18_2 = pd.to_numeric(clean_stds_all.loc[clean_stds_all['Identifier 1'] == s2, 'd 18O/16O  Mean'], errors='coerce').mean()
                            tru18_1 = get_true_value(s1, ISOTYPE_D18O)
                            tru18_2 = get_true_value(s2, ISOTYPE_D18O)
                            m18 = (tru18_2 - tru18_1) / (raw18_2 - raw18_1) if pd.notna(raw18_1) and pd.notna(raw18_2) else np.nan
                            b18 = tru18_1 - m18 * raw18_1 if pd.notna(m18) and pd.notna(raw18_1) else np.nan
                            worksheet.write(start_row, 0, "d18O Double-Point: slope (m)")
                            worksheet.write(start_row, 1, float(m18) if pd.notna(m18) else np.nan)
                            worksheet.write(start_row, 2, "intercept (b)")
                            worksheet.write(start_row, 3, float(b18) if pd.notna(b18) else np.nan)
                            start_row += 2
                            worksheet.write(start_row, 0, "Formula")
                            worksheet.write(start_row, 1, "cal = m*raw + b")
                            start_row += 2
                    except Exception:
                        # Ignore if parameter derivation fails
                        pass

                    # Linearity correction parameters section
                    worksheet.write(start_row, 0, "Linearity Correction Parameters")
                    start_row += 1
                    # d13C
                    worksheet.write(start_row, 0, "d13C slope")
                    worksheet.write(start_row, 1, float(fit13.get('slope', np.nan)) if np.isfinite(fit13.get('slope', np.nan)) else np.nan)
                    worksheet.write(start_row, 2, "intercept")
                    worksheet.write(start_row, 3, float(fit13.get('intercept', np.nan)) if np.isfinite(fit13.get('intercept', np.nan)) else np.nan)
                    start_row += 1
                    worksheet.write(start_row, 0, "d13C R^2")
                    worksheet.write(start_row, 1, float(fit13.get('r2', np.nan)) if np.isfinite(fit13.get('r2', np.nan)) else np.nan)
                    worksheet.write(start_row, 2, "x_ref (V)")
                    worksheet.write(start_row, 3, float(fit13.get('x_ref', np.nan)) if np.isfinite(fit13.get('x_ref', np.nan)) else np.nan)
                    start_row += 1
                    worksheet.write(start_row, 0, "d13C points (n)")
                    worksheet.write(start_row, 1, int(fit13.get('n', 0)))
                    start_row += 1
                    # d18O
                    worksheet.write(start_row, 0, "d18O slope")
                    worksheet.write(start_row, 1, float(fit18.get('slope', np.nan)) if np.isfinite(fit18.get('slope', np.nan)) else np.nan)
                    worksheet.write(start_row, 2, "intercept")
                    worksheet.write(start_row, 3, float(fit18.get('intercept', np.nan)) if np.isfinite(fit18.get('intercept', np.nan)) else np.nan)
                    start_row += 1
                    worksheet.write(start_row, 0, "d18O R^2")
                    worksheet.write(start_row, 1, float(fit18.get('r2', np.nan)) if np.isfinite(fit18.get('r2', np.nan)) else np.nan)
                    worksheet.write(start_row, 2, "x_ref (V)")
                    worksheet.write(start_row, 3, float(fit18.get('x_ref', np.nan)) if np.isfinite(fit18.get('x_ref', np.nan)) else np.nan)
                    start_row += 1
                    worksheet.write(start_row, 0, "d18O points (n)")
                    worksheet.write(start_row, 1, int(fit18.get('n', 0)))
                except Exception:
                    # If appending extra info fails, continue without blocking export
                    pass
    
    towrite.seek(0)

    # Create dataset download button
    st.download_button(
        label="Download Dataset Excel",
        data=towrite,
        file_name=filename,
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        key=f"download_dataset_{filename}"
    )

    # Build a separate Client Output file and download button
    try:
        intensity_col = '1  Cycle Int  Samp  44'
        fits = st.session_state.get('linearity_fits') if isinstance(st.session_state, dict) else None
        if (not fits) and selected_standards:
            _method = calibration_type or st.session_state.get("calibration_type") or "IQR"
            _sigma = sigma_level if sigma_level is not None else st.session_state.get("sigma_level", 1.0)
            _iqr = irq_multiplier if irq_multiplier is not None else st.session_state.get("irq_multiplier", 1.5)
            # Use the full session dataset for computing fits, not the filtered export frame
            base_df = st.session_state.df if 'df' in st.session_state else df
            clean_stds_all = _filter_standards_remove_outliers(base_df, selected_standards, _method, _sigma, _iqr)
            f13 = _compute_linearity_fit(clean_stds_all, 'd 13C/12C  Mean', intensity_col)
            f18 = _compute_linearity_fit(clean_stds_all, 'd 18O/16O  Mean', intensity_col)
            fits = {
                'd13C': {'slope': f13.get('slope', np.nan), 'x_ref': f13.get('x_ref', np.nan)},
                'd18O': {'slope': f18.get('slope', np.nan), 'x_ref': f18.get('x_ref', np.nan)},
            }

        def _build_corrected(series_cal, series_raw, isotope_key):
            try:
                s_cal = pd.to_numeric(series_cal, errors='coerce') if series_cal is not None else None
            except Exception:
                s_cal = None
            try:
                s_raw = pd.to_numeric(series_raw, errors='coerce') if series_raw is not None else None
            except Exception:
                s_raw = None
            if s_cal is not None and fits and np.isfinite(fits.get(isotope_key, {}).get('slope', np.nan)) and intensity_col in df.columns:
                i = pd.to_numeric(df[intensity_col], errors='coerce')
                slope = fits[isotope_key]['slope']; xr = fits[isotope_key]['x_ref']
                return (s_cal - slope * (i - xr)).where(np.isfinite(s_cal) & np.isfinite(i))
            if s_cal is not None:
                return s_cal
            if s_raw is not None and fits and np.isfinite(fits.get(isotope_key, {}).get('slope', np.nan)) and intensity_col in df.columns:
                i = pd.to_numeric(df[intensity_col], errors='coerce')
                slope = fits[isotope_key]['slope']; xr = fits[isotope_key]['x_ref']
                return (s_raw - slope * (i - xr)).where(np.isfinite(s_raw) & np.isfinite(i))
            return s_raw if s_raw is not None else pd.Series(index=df.index, dtype=float)

        # Recompute calibrated series from the (possibly interpolated) export dataframe so corrected values reflect interpolation
        s_cal13 = pd.to_numeric(df['d13C_calibrated'], errors='coerce') if 'd13C_calibrated' in df.columns else None
        s_cal18 = pd.to_numeric(df['d18O_calibrated'], errors='coerce') if 'd18O_calibrated' in df.columns else None
        if selected_standards and len(selected_standards) in (1, 2):
            try:
                _method = calibration_type or st.session_state.get("calibration_type") or "IQR"
                _sigma = sigma_level if sigma_level is not None else st.session_state.get("sigma_level", 1.0)
                _iqr = irq_multiplier if irq_multiplier is not None else st.session_state.get("irq_multiplier", 1.5)
                base_df = st.session_state.df if 'df' in st.session_state else df
                clean_stds_cal = _filter_standards_remove_outliers(base_df, selected_standards, _method, _sigma, _iqr)
                cal_tmp = calibrate_results(standards_df=clean_stds_cal if clean_stds_cal is not None else base_df,
                                            full_df=df.copy(),
                                            selected_standards=selected_standards)
                s_cal13 = pd.to_numeric(cal_tmp.get('d13C_calibrated'), errors='coerce')
                s_cal18 = pd.to_numeric(cal_tmp.get('d18O_calibrated'), errors='coerce')
            except Exception:
                pass
        corrected_d13 = _build_corrected(s_cal13 if s_cal13 is not None else df.get('d13C_calibrated'), df.get('d 13C/12C  Mean'), 'd13C')
        corrected_d18 = _build_corrected(s_cal18 if s_cal18 is not None else df.get('d18O_calibrated'), df.get('d 18O/16O  Mean'), 'd18O')

        species_series = _get_species_series(df)
        client_df = pd.DataFrame({
            'Identifier': df['Identifier 1'],
            'Sample #': df.get('Identifier 2', pd.Series(index=df.index, dtype=object)),
            'Species': species_series,
            'd13C (‰, VPDB)  Mean': pd.to_numeric(df.get('d 13C/12C  Mean'), errors='coerce'),
            'd13C (‰, VPDB)  Std Dev': pd.to_numeric(df.get('d 13C/12C  Std Dev'), errors='coerce'),
            'd18O (‰, VPDB)  Mean': pd.to_numeric(df.get('d 18O/16O  Mean'), errors='coerce'),
            'd18O (‰, VPDB)  Std Dev': pd.to_numeric(df.get('d 18O/16O  Std Dev'), errors='coerce'),
            'Corrected d13C (‰, VPDB)': corrected_d13,
            'Corrected d18O (‰, VPDB)': corrected_d18,
        })
        # Apply species replacements if provided
        if comment_map:
            try:
                client_df['Species'] = client_df['Species'].astype(str).map(lambda v: comment_map.get(v, v))
            except Exception:
                pass
        if selected_standards:
            client_df = client_df[~df['Identifier 1'].isin(selected_standards)]

        for rc in ['d13C (‰, VPDB)  Mean','d13C (‰, VPDB)  Std Dev','d18O (‰, VPDB)  Mean','d18O (‰, VPDB)  Std Dev','Corrected d13C (‰, VPDB)','Corrected d18O (‰, VPDB)']:
            if rc in client_df.columns:
                client_df[rc] = pd.to_numeric(client_df[rc], errors='coerce').round(2)

        # SHP2L precision
        d13c_sd_val = np.nan; d18o_sd_val = np.nan; n_used = 0
        try:
            _method = calibration_type or st.session_state.get("calibration_type") or "IQR"
            _sigma = sigma_level if sigma_level is not None else st.session_state.get("sigma_level", 1.0)
            _iqr = irq_multiplier if irq_multiplier is not None else st.session_state.get("irq_multiplier", 1.5)
            base_df = st.session_state.df if 'df' in st.session_state else df
            shp = base_df[base_df['Identifier 1'] == 'SHP2L'].copy() if 'Identifier 1' in base_df.columns else pd.DataFrame()
            if not shp.empty:
                if _method == "Z-Score":
                    m13 = identify_outliers(shp, 'd 13C/12C  Mean', _sigma)
                    m18 = identify_outliers(shp, 'd 18O/16O  Mean', _sigma)
                else:
                    m13 = identify_outliers_iqr(shp, 'd 13C/12C  Mean', _iqr)
                    m18 = identify_outliers_iqr(shp, 'd 18O/16O  Mean', _iqr)
                clean_shp = shp.loc[~(m13 | m18)].copy()
                n_used = len(clean_shp)
                y13s = pd.to_numeric(clean_shp.get('d 13C/12C  Mean'), errors='coerce')
                y18s = pd.to_numeric(clean_shp.get('d 18O/16O  Mean'), errors='coerce')
                if fits and np.isfinite(fits.get('d13C', {}).get('slope', np.nan)) and intensity_col in clean_shp.columns:
                    i = pd.to_numeric(clean_shp[intensity_col], errors='coerce')
                    y13s = (y13s - fits['d13C']['slope'] * (i - fits['d13C']['x_ref'])).where(np.isfinite(y13s) & np.isfinite(i))
                if fits and np.isfinite(fits.get('d18O', {}).get('slope', np.nan)) and intensity_col in clean_shp.columns:
                    i = pd.to_numeric(clean_shp[intensity_col], errors='coerce')
                    y18s = (y18s - fits['d18O']['slope'] * (i - fits['d18O']['x_ref'])).where(np.isfinite(y18s) & np.isfinite(i))
                d13c_sd_val = float(y13s.std()) if y13s is not None else np.nan
                d18o_sd_val = float(y18s.std()) if y18s is not None else np.nan
        except Exception:
            pass

        client_towrite = BytesIO()
        with pd.ExcelWriter(client_towrite, engine='xlsxwriter') as w2:
            client_df.to_excel(w2, index=False, sheet_name='Client Output')
            wb = w2.book; ws = w2.sheets['Client Output']
            header_fmt = wb.add_format({'bold': True}); corrected_hdr_fmt = wb.add_format({'bold': True, 'font_color': '#6A1B9A'})
            num_fmt = wb.add_format({'num_format': '0.00'})
            headers = list(client_df.columns)
            for col_idx, col_name in enumerate(headers):
                ws.write(0, col_idx, col_name, corrected_hdr_fmt if 'Corrected' in col_name else header_fmt)
                width = 18 if col_name in ('Identifier','Species') else (22 if 'Corrected' in col_name else 15)
                ws.set_column(col_idx, col_idx, width, num_fmt if col_name in ['d13C (‰, VPDB)  Mean','d13C (‰, VPDB)  Std Dev','d18O (‰, VPDB)  Mean','d18O (‰, VPDB)  Std Dev','Corrected d13C (‰, VPDB)','Corrected d18O (‰, VPDB)'] else None)
            equip_title_fmt = wb.add_format({'bold': True})
            ws.write(1, 10, 'Equiment:', equip_title_fmt)
            ws.write(1, 11, 'ThermoFisher Scientific MAT253 gas isotope ratio mass spectrometer')
            ws.write(2, 11, 'Kiel IV automated carbonate preparation device')
            ws.write(4, 10, 'Standard deviation of SHP2L over measurement period:', equip_title_fmt)
            ws.write(5, 11, f"{0.00 if np.isnan(d13c_sd_val) else d13c_sd_val:.2f} ‰ for d13C")
            ws.write(6, 11, f"{0.00 if np.isnan(d18o_sd_val) else d18o_sd_val:.2f} ‰ for d18O")
            ws.write(7, 11, f"{n_used} n")
            materials_text = (
                "When results produced at P2L are being published, we suggest to use the following text in the “Material and Methods” section of the publication:\n\n"
                "\"Analyses on (your samples) for determination of d13C and d18O were performed at the Paleoceanography and Paleoclimatology Laboratory, School of Arts, Sciences and Humanities of the University of Sāo Paulo, Brazil. The laboratory is equipped with a Thermo Fisher Scientific™ MAT253 isotope ratio mass spectrometer (IRMS) coupled with a Thermo Fisher Scientific™ Kiel IV carbonate preparation device. The details on the laboratory analytical setup and performance are described in Crivellari et al. (2021). The IRMS measures the isotopic composition of the CO2 developed by the reaction between the sample carbonate and orthophosphoric acid at 70°C. Measurements were calibrated against repeated analyses of SHP2L reference material which is used as internal working standard (Crivellari et al., 2021). SHP2L is in turn calibrated against international reference material NBS19 and values are anchored to the Vienna Pee Dee Belemnite (VPDB) scale. Analytical precision was better than (please use the value informed by P2L) ‰ for d13C and (please use the value informed by P2L) ‰ for d18O (±1 s, n = please use the value informed by P2L).\"\n\n"
                "Reference\nCrivellari, S., Viana, P.J., Campos, M.D., Kuhnert, H., Lopes, A.B.M., da Cruz, F.W., Chiessi, C.M., 2021. Development and characterization of a new in-house reference material for stable carbon and oxygen isotopes analyses. Journal of Analytical Atomic Spectrometry 36, 1125-1134. DOI: 10.1039/D1JA00030F."
            )
            ws.insert_textbox('L10', materials_text, {'width': 820, 'height': 580, 'line': {'color': '#4F81BD'}})

        client_towrite.seek(0)
        client_filename = _build_client_filename(client_name, client_df)
        st.download_button(
            label='Download Client Output',
            data=client_towrite,
            file_name=client_filename,
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            key=f"download_client_{client_filename}"
        )
    except Exception as e:
        st.warning(f"Client Output creation failed: {e}")


if "df" not in st.session_state:
    st.session_state.df = None

def main():
    st.title('Isotope Ratio Mass Spectrometer Data Analyzer')

    # Initialize session state variables if they don't exist
    if 'df' not in st.session_state:
        st.session_state.df = None
    if 'file_processed' not in st.session_state:
        st.session_state.file_processed = False
    if 'confirm_reset' not in st.session_state:
        st.session_state.confirm_reset = False

    tab_import, tab1, tab2, tab3 = st.tabs([
        'Data import',
        'Diagnostics',
        'Calibration',
        'Data Processing'
    ])

    has_data = False

    with tab_import:
        # File uploader
        uploaded_files = st.file_uploader(
            "Choose XLS files",
            type=['xls', 'xlsx'],
            accept_multiple_files=True
        )

        # Reset file processing with confirmation
        if st.button("Load a New File", key="load_new_file_btn"):
            st.session_state.confirm_reset = True  # Trigger confirmation prompt

        # Confirmation prompt
        if st.session_state.confirm_reset:
            st.warning("Are you sure you want to load a new file? This will overwrite the current data.")
            col1, col2 = st.columns(2)
            if col1.button("Yes, load new file", key="confirm_load_btn"):
                # Reset session state to allow a new file upload
                st.session_state.file_processed = False
                st.session_state.df = None
                st.session_state.confirm_reset = False  # Reset confirmation state
            elif col2.button("Cancel", key="cancel_load_btn"):
                st.session_state.confirm_reset = False  # Cancel reset and close prompt

        # Only load files if they haven't been processed yet
        if uploaded_files and not st.session_state.file_processed:
            try:
                dfs = []
                for uploaded_file in uploaded_files:
                    try:
                        # First try with openpyxl engine (check for multi-row headers)
                        raw = pd.read_excel(uploaded_file, header=None, engine='openpyxl')
                        df = _parse_new_table_layout(raw)
                        if df is None:
                            uploaded_file.seek(0)
                            df = pd.read_excel(uploaded_file, engine='openpyxl')
                    except Exception as e:
                        try:
                            # If openpyxl fails, try with xlrd engine
                            uploaded_file.seek(0)
                            raw = pd.read_excel(uploaded_file, header=None, engine='xlrd')
                            df = _parse_new_table_layout(raw)
                            if df is None:
                                uploaded_file.seek(0)
                                df = pd.read_excel(uploaded_file, engine='xlrd')
                        except Exception as e:
                            st.error(f"Failed to read Excel file '{uploaded_file.name}': {str(e)}")
                            continue
                    
                    # Standardize types and create a clean copy
                    df = _coalesce_duplicate_columns(df)
                    df = df.convert_dtypes()
                    df.reset_index(drop=True, inplace=True)
                    df = df.map(lambda x: None if pd.isna(x) else x)
                    df['Excel File'] = uploaded_file.name

                    # Normalize isotope column names
                    df = _standardize_isotope_columns(df)
                    df = _coalesce_duplicate_columns(df)
                    # Ensure isotope mean/std columns exist
                    for col in ['d 13C/12C  Mean', 'd 13C/12C  Std Dev', 'd 18O/16O  Mean', 'd 18O/16O  Std Dev']:
                        if col not in df.columns:
                            df[col] = np.nan

                    # Convert the DataFrame 'Date' column to datetime with explicit format
                    if 'Date' in df.columns:
                        df['Date'] = pd.to_datetime(df['Date'], format='%m/%d/%y', errors='coerce')
                    elif 'Start Time' in df.columns:
                        df['Date'] = pd.to_datetime(df['Start Time'], errors='coerce')

                    if 'Date' in df.columns:
                        df['Date_ordinal'] = pd.to_numeric(
                            df['Date'].map(lambda x: x.toordinal() if pd.notnull(x) else None)
                        )

                    # Save original columns for reference
                    original_columns = df.columns.tolist()

                    # Extract values from Information column when present
                    if 'Information' in df.columns:
                        df = extract_info_values(df)

                    # Map structured columns to analysis fields when present (tolerate unicode variants)
                    leak_col = _find_column(df, 'Kiel IV Leak Rate')
                    if leak_col and 'leak_rate' not in df.columns:
                        df['leak_rate'] = _extract_numeric(df[leak_col])
                    gases_col = _find_column(df, 'Kiel IV Non Condensable Pressure', 'Kiel IV Non-Condensable Pressure')
                    if gases_col and 'p_gases' not in df.columns:
                        df['p_gases'] = _extract_numeric(df[gases_col])
                    residual_col = _find_column(df, 'Kiel IV Residual CO2 Pressure')
                    if residual_col and 'p_no_acid' not in df.columns:
                        df['p_no_acid'] = _extract_numeric(df[residual_col])
                    sample_col = _find_column(df, 'Kiel IV CO2 Sample Pressure')
                    if sample_col and 'total_co2' not in df.columns:
                        df['total_co2'] = _extract_numeric(df[sample_col])

                    if '1  Cycle Int  Samp  44' in df.columns:
                        df['1  Cycle Int  Samp  44'] = _normalize_signal_intensity(df['1  Cycle Int  Samp  44'])
                    else:
                        intensity_candidates = [
                            'Pressure Adjust Result Intensity',
                            'Pressure Adjust Initial Intensity',
                            'Initial Intensity from µ-Volume',
                            'Initial Intensity from μ-Volume',
                            'Initial Intensity from Âµ-Volume'
                        ]
                        for cand in intensity_candidates:
                            col = _find_column(df, cand)
                            if col:
                                df['1  Cycle Int  Samp  44'] = _normalize_signal_intensity(df[col])
                                break

                    if 'Label' in df.columns:
                        label_parts = df['Label'].apply(_split_label_species)
                        if 'Identifier 1' not in df.columns:
                            df['Identifier 1'] = label_parts.map(lambda v: v[0] if v else None)
                        if 'Species' not in df.columns:
                            df['Species'] = label_parts.map(lambda v: v[1] if v else None)
                    elif 'Identifier 1' not in df.columns:
                        if 'Sample' in df.columns:
                            df['Identifier 1'] = df['Sample']
                        else:
                            df['Identifier 1'] = None
                    if 'Identifier 2' not in df.columns:
                        if 'Comment' in df.columns:
                            df['Identifier 2'] = df['Comment']
                        elif 'Run ID' in df.columns:
                            df['Identifier 2'] = df['Run ID']
                        elif 'Index' in df.columns:
                            df['Identifier 2'] = df['Index']
                        else:
                            df['Identifier 2'] = None

                    if 'Comment' not in df.columns and 'Sample Type' in df.columns:
                        df['Comment'] = df['Sample Type']
                    # Leave Species empty unless provided or parsed from Label

                    # Normalize Label to "Identifier 1 - Species" when possible
                    if 'Identifier 1' in df.columns:
                        df['Label'] = _compose_label_series(
                            df['Identifier 1'],
                            df.get('Species', pd.Series(index=df.index, dtype=object))
                        )

                    # Ensure required analysis columns exist
                    for col in ['leak_rate', 'p_no_acid', 'total_co2', 'p_gases', '1  Cycle Int  Samp  44', 'Line']:
                        if col not in df.columns:
                            df[col] = np.nan

                    # Compute per-sample means from cycles when Cycle Number is present
                    df = _apply_cycle_averages(df)

                    # Ensure all original columns are included
                    for col in original_columns:
                        if col not in df.columns:
                            df[col] = None

                    dfs.append(df)

                if not dfs:
                    return

                df = pd.concat(dfs, ignore_index=True, sort=False) if len(dfs) > 1 else dfs[0]

                # Save df to session_state
                st.session_state.df = df
                st.session_state.file_processed = True

            except Exception as e:
                st.error(f"Error loading file: {e}")

        # Display a warning if no file is uploaded
        if st.session_state.df is None:
            st.warning("Please upload a file to begin analysis.")
        else:
            # Display data preview if available
            with st.expander("Data Table", expanded=True):
                # Display the DataFrame using Streamlit's native table component
                st.dataframe(
                    st.session_state.df,
                    height=400,  # Set table height for vertical scroll
                    width='stretch'  # Use full width of the container
                )

            st.subheader('Sample Statistics')
            # Display sample counts as a table with percentage
            # Count samples considering duplicates (Identifier 1 and 2 combinations)
            sample_counts = st.session_state.df.groupby('Identifier 1').agg({
                'Identifier 2': 'nunique',
                'Identifier 1': 'count'
            }).rename(columns={
                'Identifier 2': 'Unique Samples',
                'Identifier 1': 'Total Measurements'
            })
            total_unique = sample_counts['Unique Samples'].sum()
            total_measurements = sample_counts['Total Measurements'].sum()
            
            # Create DataFrame with percentages
            count_df = pd.DataFrame({
                'Identifier': sample_counts.index,
                'Unique Samples': sample_counts['Unique Samples'],
                'Total Measurements': sample_counts['Total Measurements'],
                'Measurements %': (sample_counts['Total Measurements'] / total_measurements * 100).round(1)
            })
            # Format the percentage column
            count_df['Measurements %'] = count_df['Measurements %'].map('{:,.1f}%'.format)
            st.dataframe(count_df, hide_index=True, width='stretch')
            # Display metrics
            metrics_col1, metrics_col2 = st.columns(2)
            metrics_col1.metric("Total Unique Samples", total_unique)
            metrics_col2.metric("Total Measurements", total_measurements)

        has_data = st.session_state.df is not None

    if not has_data:
        return
    # Sidebar for user-selected sigma level
    # with st.sidebar:
    #     sigma_level = st.number_input("Set Sigma Level for Outlier Exclusion",
    #                                   min_value=0.1,
    #                                   max_value=5.0,
    #                                   value=1.0,
    #                                   step=0.1)

    color_options = {
        'Line': 'Line',
        'Signal Intensity': '1  Cycle Int  Samp  44',
        'd18O values': 'd 18O/16O  Mean',
        'd13C values': 'd 13C/12C  Mean',
        'Leak Rate': 'leak_rate',
        'Total CO2': 'total_co2',
        'P gasses': 'p_gases',
        'P no acid': 'p_no_acid',
        'Date': 'Date_ordinal'
    }

    # Get list of friendly names for dropdown
    color_param_names = list(color_options.keys())

    with tab1:
        st.header('Diagnostic Plots')
        
        # Create two columns for controls
        col1, col2 = st.columns(2)

        with col1:
            st.subheader('Parameter Selection')
            # Dropdown for selecting color parameter
            default_color_param = 'd18O values'
            default_index = color_param_names.index(default_color_param) if default_color_param in color_param_names else 0
            selected_color_param = st.selectbox(
                "Choose a parameter to color the dots:",
                color_param_names,
                index=default_index,
                key="diagnostic_param"
            )
            
            # Filter by Identifier 1
            identifier_filter = st.multiselect(
                "Filter by Identifier 1:",
                options=st.session_state.df['Identifier 1'].unique().tolist(),
                default=None
            )
            
        with col2:
            st.subheader('Value Ranges')
            # d13C/12C Mean range selector
            d13c_min = float(st.session_state.df['d 13C/12C  Mean'].min())
            d13c_max = float(st.session_state.df['d 13C/12C  Mean'].max())
            d13c_range = st.slider(
                "Select min and max d13C/12C Mean",
                min_value=d13c_min,
                max_value=d13c_max,
                value=(d13c_min, d13c_max),
                step=0.1
            )
            
            # d18O/16O Mean range selector
            d18o_min = float(st.session_state.df['d 18O/16O  Mean'].min())
            d18o_max = float(st.session_state.df['d 18O/16O  Mean'].max())
            d18o_range = st.slider(
                "Select min and max d18O/16O Mean",
                min_value=d18o_min,
                max_value=d18o_max,
                value=(d18o_min, d18o_max),
                step=0.1
            )

        st.divider()
        # Map the selected friendly name to the actual column name
        color_param = color_options[selected_color_param]

        # Get filter values from the three-column controls
        min_d13C, max_d13C = d13c_range
        min_d18O, max_d18O = d18o_range

        # Ensure that there are no NaN values in the columns before filtering
        filtered_df = st.session_state.df.dropna(subset=['d 13C/12C  Mean', 'd 18O/16O  Mean', 'Identifier 1'])

        # Apply identifier filter if any identifiers are selected
        if identifier_filter:
            filtered_df = filtered_df[filtered_df['Identifier 1'].isin(identifier_filter)]

        # Ensure the columns are of the correct type (float) for comparison
        filtered_df['d 13C/12C  Mean'] = filtered_df['d 13C/12C  Mean'].astype(float)
        filtered_df['d 18O/16O  Mean'] = filtered_df['d 18O/16O  Mean'].astype(float)

        # Filter the DataFrame based on the selected min and max values
        filtered_df = filtered_df[
            (filtered_df['d 13C/12C  Mean'] >= min_d13C) &
            (filtered_df['d 13C/12C  Mean'] <= max_d13C) &
            (filtered_df['d 18O/16O  Mean'] >= min_d18O) &
            (filtered_df['d 18O/16O  Mean'] <= max_d18O)
        ]

        # Generate the figure using the filtered DataFrame and selected color parameter
        fig = create_diagnostic_plots(filtered_df, color_param)

        # Display the plot
        st.plotly_chart(fig, width='stretch')

    with tab2:
            st.header("Calibration")

            # Load standards reference data
            standards_reference = pd.read_csv('standards.csv')
            # Normalize isotopic type labels to avoid encoding mismatches
            try:
                standards_reference['Isotopic_Value_Type'] = (
                    standards_reference['Isotopic_Value_Type']
                    .astype(str)
                    .str.strip()
                    .replace({
                        'VPDB(13C)': ISOTYPE_D13C,
                        'VSMOW(18O)': ISOTYPE_D18O,
                        'dVPDB(13C)': ISOTYPE_D13C,
                        'dVSMOW(18O)': ISOTYPE_D18O,
                        '?VPDB(13C)': ISOTYPE_D13C,
                        '?VSMOW(18O)': ISOTYPE_D18O,
                        'δVPDB(13C)': ISOTYPE_D13C,
                        'δVSMOW(18O)': ISOTYPE_D18O,
                        'Î´VPDB(13C)': ISOTYPE_D13C,
                        'Î´VSMOW(18O)': ISOTYPE_D18O,
                        '??VPDB(13C)': ISOTYPE_D13C,
                        '??VSMOW(18O)': ISOTYPE_D18O,
                    })
                )
            except Exception:
                pass

            # Create a list of unique standards
            standards_list = standards_reference['Standard'].unique().tolist()

            # Create three columns for the controls
            col1, col2, col3 = st.columns(3)

            with col1:
                st.markdown("#### Standard Selection")
                # Dropdown for user to select standards (multiple selection)
                selected_standards = st.multiselect(
                    "Select Standards to Filter Data:",
                    standards_list,
                    help="Select 1 standard for single-point calibration or 2 standards for double-point calibration"
                )
                st.session_state.selected_standards = selected_standards

            with col2:
                st.markdown("#### Outlier Detection")
                sigma_level = st.number_input("Set Sigma Level for standardÂ´s Outlier Exclusion",
                                            min_value=0.1,
                                            max_value=5.0,
                                            value=1.0,
                                            step=0.1)

                irq_multiplier = st.number_input("Set IQR Multiplier for standardÂ´s Outlier Exclusion",
                                                min_value=1.0,
                                                max_value=10.0,
                                                value=1.5,
                                                step=0.1)

                # User selects the calibration method
                calibration_type = st.selectbox("Choose Outlier Detection Method", options=["Z-Score", "IQR"])

                # Persist current calibration/outlier settings for reuse (e.g., Excel export)
                st.session_state.calibration_type = calibration_type
                st.session_state.sigma_level = sigma_level
                st.session_state.irq_multiplier = irq_multiplier

            with col3:
                st.markdown("#### Visualization")
                # Dropdown for selecting color parameter
                # Ensure the default value exists in the list
                default_color_param = 'd18O values'
                default_index = color_param_names.index(
                    default_color_param) if default_color_param in color_param_names else 0

                # Dropdown for selecting color parameter with a default value
                selected_color_param = st.selectbox(
                    "Choose a parameter to color the dots:",
                    color_param_names,
                    index=default_index,
                    key="calibration_param"
                )

                # Map the selected friendly name to the actual column name
                color_param = color_options[selected_color_param]

                # Add some vertical spacing
                st.write("")
                st.write("")

            # Precision date range selection (standards only)
            precision_date_bounds = None
            date_col = _find_column(st.session_state.df, 'Date')
            if date_col:
                date_series_all = pd.to_datetime(st.session_state.df[date_col], errors='coerce')
                valid_dates = date_series_all.dropna()
                if not valid_dates.empty:
                    min_date = valid_dates.min().date()
                    max_date = valid_dates.max().date()

                    def _normalize_date_range(value):
                        if isinstance(value, (list, tuple)) and len(value) == 2:
                            start_val, end_val = value
                        elif value is not None:
                            start_val = value
                            end_val = value
                        else:
                            start_val = min_date
                            end_val = max_date
                        try:
                            start_val = pd.Timestamp(start_val).date()
                            end_val = pd.Timestamp(end_val).date()
                        except Exception:
                            start_val = min_date
                            end_val = max_date
                        if start_val < min_date:
                            start_val = min_date
                        if end_val > max_date:
                            end_val = max_date
                        if start_val > end_val:
                            start_val, end_val = end_val, start_val
                        return start_val, end_val

                    stored_range = st.session_state.get('precision_date_range')
                    default_start, default_end = _normalize_date_range(stored_range)
                    if 'precision_date_range_input' not in st.session_state:
                        st.session_state.precision_date_range_input = (default_start, default_end)

                    st.markdown("#### Precision Date Range")
                    precision_date_range = st.date_input(
                        "Select date range for precision calculations (standards only):",
                        min_value=min_date,
                        max_value=max_date,
                        value=st.session_state.precision_date_range_input,
                        key="precision_date_range_input",
                    )
                    start_date, end_date = _normalize_date_range(precision_date_range)
                    st.session_state.precision_date_range = (start_date, end_date)
                    if start_date is not None and end_date is not None:
                        st.caption(
                            f"Precision calculations use standards dated {start_date:%Y-%m-%d} to {end_date:%Y-%m-%d}."
                        )
                        precision_date_bounds = (
                            pd.Timestamp(start_date),
                            pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1),
                        )
                else:
                    st.info("No valid dates available; precision calculations will use all standards.")
            else:
                st.info("No Date column available; precision calculations will use all standards.")
            
            # Compute a clean standards dataframe (outliers removed) for charts/fits as soon as standards are selected
            clean_stds = None
            if selected_standards:
                clean_stds = _filter_standards_remove_outliers(
                    st.session_state.df,
                    selected_standards,
                    calibration_type,
                    sigma_level,
                    irq_multiplier
                )
            clean_stds_for_charts = clean_stds
            if clean_stds_for_charts is not None and precision_date_bounds and date_col and date_col in clean_stds_for_charts.columns:
                date_series_chart = pd.to_datetime(clean_stds_for_charts[date_col], errors='coerce')
                date_mask_chart = (date_series_chart >= precision_date_bounds[0]) & (date_series_chart <= precision_date_bounds[1])
                clean_stds_for_charts = clean_stds_for_charts.loc[date_mask_chart].copy()

            # Action row: Calibrate results + optional linearity correction toggle
            if selected_standards:
                act_c1, act_c2 = st.columns([2, 1])
                with act_c1:
                    calibrate_clicked = st.button("Calibrate results", width='stretch')
                with act_c2:
                    apply_linearity_toggle = st.checkbox("Apply linearity correction", key="apply_linearity_toggle")

                if calibrate_clicked:
                    if len(selected_standards) not in [1, 2]:
                        st.warning("Please select either 1 or 2 standards for calibration.")
                    else:
                        # Perform calibration (using standards with outliers removed)
                        try:
                            calibrated_df = calibrate_results(
                                standards_df=clean_stds if clean_stds is not None else st.session_state.df,
                                full_df=st.session_state.df,
                                selected_standards=selected_standards
                            )
                            st.session_state.df = calibrated_df
                            st.success("Calibration completed for both isotopic types.")
                        except Exception as e:
                            st.error(f"Calibration failed: {e}")

                        # Optionally compute and apply linearity correction across the whole dataset
                        if apply_linearity_toggle:
                            try:
                                intensity_col = '1  Cycle Int  Samp  44'
                                y13_col = 'd 13C/12C  Mean'
                                y18_col = 'd 18O/16O  Mean'
                                fit13 = _compute_linearity_fit(clean_stds, y13_col, intensity_col) if clean_stds is not None else {'slope': np.nan, 'x_ref': np.nan}
                                fit18 = _compute_linearity_fit(clean_stds, y18_col, intensity_col) if clean_stds is not None else {'slope': np.nan, 'x_ref': np.nan}
                                fits = {
                                    'd13C': {'slope': fit13.get('slope', np.nan), 'x_ref': fit13.get('x_ref', np.nan)},
                                    'd18O': {'slope': fit18.get('slope', np.nan), 'x_ref': fit18.get('x_ref', np.nan)},
                                }
                                st.session_state.df = _apply_linearity_correction(st.session_state.df, intensity_col, fits)
                                # Store fits for downstream display use
                                st.session_state.linearity_fits = fits
                                if np.isfinite(fit13.get('slope', np.nan)) or np.isfinite(fit18.get('slope', np.nan)):
                                    st.success(
                                        f"Applied linearity correction. Slopes: d13C={fit13.get('slope', float('nan')):.6f} per V, "
                                        f"d18O={fit18.get('slope', float('nan')):.6f} per V."
                                    )
                                else:
                                    st.info("Linearity correction requested, but insufficient data to compute fits.")
                            except Exception as e:
                                st.error(f"Linearity correction failed: {e}")

            # Always show calibration charts when standards are selected (using cleaned standards)
            if selected_standards:
                try:
                    chart_src = clean_stds_for_charts if clean_stds_for_charts is not None else clean_stds if clean_stds is not None else st.session_state.df
                    figs = create_calibration_plots(standards_reference, chart_src, selected_standards, color_param)
                    col_cal1, col_cal2 = st.columns(2)
                    with col_cal1:
                        st.plotly_chart(figs[ISOTYPE_D13C], width='stretch')
                    with col_cal2:
                        st.plotly_chart(figs[ISOTYPE_D18O], width='stretch')
                except Exception as e:
                    st.warning(f"Unable to render calibration charts: {e}")

            if False:
                if selected_standards:
                    # Check if the selected standards are 1 or 2
                    if len(selected_standards) in [1, 2]:
                        method_type = "single-point" if len(selected_standards) == 1 else "double-point"
                        st.info(
                            f"Performing {method_type} calibration for {', '.join(selected_standards)} using {calibration_type} method.")

                        # Create a copy of the original dataframe to avoid modifying it directly
                        filtered_df = st.session_state.df.copy()

                        # Filter out outliers for each standard
                        for standard in selected_standards:
                            # Filter data for the current standard
                            mask = filtered_df['Identifier 1'] == standard
                            standard_data = filtered_df[mask]

                            if not standard_data.empty:
                                if calibration_type == "Z-Score":
                                    # Identify outliers for d13C and d18O using Z-Score method
                                    d13c_outliers = identify_outliers(standard_data, 'd 13C/12C  Mean', sigma_level)
                                    d18o_outliers = identify_outliers(standard_data, 'd 18O/16O  Mean', sigma_level)

                                elif calibration_type == "IQR":
                                    # Identify outliers for d13C and d18O using IQR method
                                    d13c_outliers = identify_outliers_iqr(standard_data, 'd 13C/12C  Mean',
                                                                            irq_multiplier)
                                    d18o_outliers = identify_outliers_iqr(standard_data, 'd 18O/16O  Mean',
                                                                            irq_multiplier)

                                # Create combined mask for rows to keep (non-outliers)
                                keep_mask = ~(d13c_outliers | d18o_outliers)

                                # Update the filtered dataframe to exclude outliers for this standard
                                filtered_df.loc[mask] = standard_data[keep_mask]

                        # Create and display the calibration plots using filtered data
                        figs = create_calibration_plots(standards_reference, filtered_df, selected_standards, color_param)

                        # Display plots in columns
                        col1, col2 = st.columns(2)
                        with col1:
                            st.plotly_chart(figs[ISOTYPE_D13C], width='stretch')
                        with col2:
                            st.plotly_chart(figs[ISOTYPE_D18O], width='stretch')

                        # Perform calibration for both isotopic types in a single function call
                        calibrated_df = calibrate_results(
                            standards_df=filtered_df,  # The filtered standards dataframe (without outliers)
                            full_df=st.session_state.df,  # The complete dataframe to be calibrated
                            selected_standards=selected_standards
                        )

                        st.success("Calibration completed for both isotopic types.")
                        st.session_state.df = calibrated_df  # Save the updated filtered df to session_state
                    else:
                        st.warning("Please select either 1 or 2 standards for calibration.")
                else:
                    st.warning("Please select at least one standard to proceed with calibration.")

            # Linearity correction section (charts only; application handled by the toggle above)
            st.subheader("Linearity Correction")
            if not selected_standards:
                st.info("Select one or more standards above to compute linearity.")
            else:
                intensity_col = '1  Cycle Int  Samp  44'
                y13_col = 'd 13C/12C  Mean'
                y18_col = 'd 18O/16O  Mean'

                clean_stds = clean_stds if 'clean_stds' in locals() and clean_stds is not None else _filter_standards_remove_outliers(
                    st.session_state.df,
                    selected_standards,
                    calibration_type,
                    sigma_level,
                    irq_multiplier,
                )
                linearity_src = clean_stds_for_charts if 'clean_stds_for_charts' in locals() and clean_stds_for_charts is not None else clean_stds
                if linearity_src is not None and precision_date_bounds and date_col and date_col in linearity_src.columns:
                    date_series_chart = pd.to_datetime(linearity_src[date_col], errors='coerce')
                    date_mask_chart = (date_series_chart >= precision_date_bounds[0]) & (date_series_chart <= precision_date_bounds[1])
                    linearity_src = linearity_src.loc[date_mask_chart].copy()

                fit13 = _compute_linearity_fit(linearity_src, y13_col, intensity_col)
                fit18 = _compute_linearity_fit(linearity_src, y18_col, intensity_col)

                def _build_linearity_fig(df_src, y_col, fit, title_prefix):
                    fig = go.Figure()
                    color_vals, _ = _prepare_color_values(df_src.get(color_param, None))
                    fig.add_trace(go.Scatter(
                        x=df_src[intensity_col],
                        y=df_src[y_col],
                        mode='markers',
                        marker=dict(color=color_vals, colorscale='Viridis', showscale=False),
                        name='Standards'
                    ))
                    x = pd.to_numeric(df_src[intensity_col], errors='coerce')
                    y = pd.to_numeric(df_src[y_col], errors='coerce')
                    m = np.isfinite(x) & np.isfinite(y)
                    if fit['n'] >= 2 and np.any(m):
                        x_min = float(np.nanmin(x[m]))
                        x_max = float(np.nanmax(x[m]))
                        xs = np.linspace(x_min, x_max, 100)
                        ys = fit['intercept'] + fit['slope'] * xs
                        fig.add_trace(go.Scatter(x=xs, y=ys, mode='lines', name='Fit', line=dict(color='orange')))
                        eq = f"y = {fit['intercept']:.3f} + {fit['slope']:.6f}*I (R^2={fit['r2']:.3f})"
                    else:
                        eq = "Insufficient data for regression"
                    fig.update_layout(
                        title=f"{title_prefix}: {y_col} vs Intensity",
                        xaxis_title='Signal Intensity (V) - 1  Cycle Int  Samp  44',
                        yaxis_title=y_col,
                        annotations=[dict(x=0.02, y=0.98, xref='paper', yref='paper',
                                          text=eq, showarrow=False,
                                          bgcolor='white', bordercolor='black', borderwidth=1, font=dict(size=12))],
                        height=400,
                    )
                    return fig

                def _build_corrected_fig(df_src, y_col, fit, title_prefix):
                    if not fit or not np.isfinite(fit.get('slope', np.nan)):
                        return go.Figure()
                    x = pd.to_numeric(df_src[intensity_col], errors='coerce')
                    y = pd.to_numeric(df_src[y_col], errors='coerce')
                    corr = (y - fit['slope'] * (x - fit['x_ref']))
                    plot_df = pd.DataFrame({
                        'x': x,
                        'y_corr': corr,
                        'color': df_src.get(color_param, None)
                    })
                    color_vals, _ = _prepare_color_values(plot_df['color'])
                    if color_vals is not None:
                        plot_df['color'] = color_vals
                    m = np.isfinite(plot_df['x']) & np.isfinite(plot_df['y_corr'])
                    plot_df = plot_df[m]
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=plot_df['x'],
                        y=plot_df['y_corr'],
                        mode='markers',
                        marker=dict(color=plot_df['color'], colorscale='Viridis', showscale=False),
                        name='Corrected'
                    ))
                    if len(plot_df) >= 2:
                        lr = linregress(plot_df['x'], plot_df['y_corr'])
                        xs = np.linspace(float(plot_df['x'].min()), float(plot_df['x'].max()), 100)
                        ys = lr.intercept + lr.slope * xs
                        fig.add_trace(go.Scatter(x=xs, y=ys, mode='lines', name='Post-correction Fit', line=dict(color='green', dash='dash')))
                        eq = f"y = {lr.intercept:.3f} + {lr.slope:.6f}*I (R^2={lr.rvalue**2:.3f})"
                    else:
                        eq = "Insufficient data for regression"
                    fig.update_layout(
                        title=f"{title_prefix}: {y_col} vs Intensity (Corrected)",
                        xaxis_title='Signal Intensity (V) - 1  Cycle Int  Samp  44',
                        yaxis_title=f"{y_col} (linearity corrected)",
                        annotations=[dict(x=0.02, y=0.98, xref='paper', yref='paper',
                                          text=eq, showarrow=False,
                                          bgcolor='white', bordercolor='black', borderwidth=1, font=dict(size=12))],
                        height=400,
                    )
                    return fig

                c1, c2 = st.columns(2)
                with c1:
                    st.plotly_chart(_build_linearity_fig(linearity_src, y13_col, fit13, 'Linearity (Standards)'), width='stretch')
                with c2:
                    st.plotly_chart(_build_corrected_fig(linearity_src, y13_col, fit13, 'Linearity (Standards)'), width='stretch')

                c3, c4 = st.columns(2)
                with c3:
                    st.plotly_chart(_build_linearity_fig(linearity_src, y18_col, fit18, 'Linearity (Standards)'), width='stretch')
                with c4:
                    st.plotly_chart(_build_corrected_fig(linearity_src, y18_col, fit18, 'Linearity (Standards)'), width='stretch')

                # Persist the latest fit parameters for downstream precision display
                try:
                    st.session_state.linearity_fits = {
                        'd13C': {'slope': fit13.get('slope', np.nan), 'x_ref': fit13.get('x_ref', np.nan)},
                        'd18O': {'slope': fit18.get('slope', np.nan), 'x_ref': fit18.get('x_ref', np.nan)},
                    }
                except Exception:
                    pass

            # print(calibration_type)
            if selected_standards:
                for standard in selected_standards:
                    established_values = standards_reference[standards_reference['Standard'] == standard]

                    if established_values.empty:
                        st.warning(f"No established values found for the standard: {standard}")
                        continue

                    cond13 = established_values['Isotopic_Value_Type'] == ISOTYPE_D13C
                    cond18 = established_values['Isotopic_Value_Type'] == ISOTYPE_D18O
                    vals13 = established_values.loc[cond13, 'Value']
                    vals18 = established_values.loc[cond18, 'Value']
                    if vals13.empty or vals18.empty:
                        st.warning(f"Isotopic values not found for the standard: {standard}. Check standards.csv encoding.")
                        continue
                    d13c_established = vals13.iloc[0]
                    d18o_established = vals18.iloc[0]

                    shp2l_filtered_data = st.session_state.df[
                        st.session_state.df['Identifier 1'] == standard]

                    if precision_date_bounds and date_col:
                        date_series_std = pd.to_datetime(shp2l_filtered_data[date_col], errors='coerce')
                        date_mask = (date_series_std >= precision_date_bounds[0]) & (date_series_std <= precision_date_bounds[1])
                        shp2l_filtered_data = shp2l_filtered_data.loc[date_mask]

                    # print(f"Number of rows: {len(shp2l_filtered_data)}")

                    if shp2l_filtered_data.empty:
                        if precision_date_bounds:
                            start_ts = precision_date_bounds[0].strftime('%Y-%m-%d')
                            end_ts = precision_date_bounds[1].strftime('%Y-%m-%d')
                            st.warning(f"No data available for the standard: {standard} in the selected precision date range ({start_ts} to {end_ts}).")
                        else:
                            st.warning(f"No data available for the standard: {standard}")
                        continue

                    # Initialize outliers variables to ensure they exist
                    d13c_outliers = None
                    d18o_outliers = None

                    if calibration_type == "Z-Score":
                        d13c_outliers = identify_outliers(shp2l_filtered_data, 'd 13C/12C  Mean', sigma_level)
                        d18o_outliers = identify_outliers(shp2l_filtered_data, 'd 18O/16O  Mean', sigma_level)
                    else:  # IQR
                        d13c_outliers = identify_outliers_iqr(shp2l_filtered_data, 'd 13C/12C  Mean', irq_multiplier)
                        d18o_outliers = identify_outliers_iqr(shp2l_filtered_data, 'd 18O/16O  Mean', irq_multiplier)

                    # Display outliers information
                    st.subheader(f"Identified Outliers for {standard}")

                    if d13c_outliers is not None and d18o_outliers is not None and (
                            d13c_outliers.any() or d18o_outliers.any()):
                        col1, col2 = st.columns(2)

                        with col1:
                            st.markdown("### d13C Outliers:")
                            d13c_outliers_data = shp2l_filtered_data.loc[d13c_outliers, ['d 13C/12C  Mean']]
                            if not d13c_outliers_data.empty:
                                st.dataframe(d13c_outliers_data.style.highlight_max(axis=0))
                            else:
                                st.write("No d13C outliers found.")

                        with col2:
                            st.markdown("### d18O Outliers:")
                            d18o_outliers_data = shp2l_filtered_data.loc[d18o_outliers, ['d 18O/16O  Mean']]
                            if not d18o_outliers_data.empty:
                                st.dataframe(d18o_outliers_data.style.highlight_max(axis=0))
                            else:
                                st.write("No d18O outliers found.")
                    else:
                        st.write("No outliers identified at this sigma level.")

                    # Filter out outliers for precision and average calculations
                    shp2l_clean = shp2l_filtered_data.loc[~(d13c_outliers | d18o_outliers)]

                    # Display precision (standard deviation) and averages
                    # Calculate the number of standards and percentage
                    total_standards = len(shp2l_filtered_data)
                    included_standards = len(shp2l_clean)
                    standards_percentage = (included_standards / total_standards) * 100 if total_standards > 0 else 0

                    # Calculate precision values (raw)
                    d13c_precision = shp2l_clean['d 13C/12C  Mean'].std()
                    d18o_precision = shp2l_clean['d 18O/16O  Mean'].std()

                    # Precision per line (1/2), excluding outliers
                    line_precision_markup = ""
                    line_col = _find_column(shp2l_clean, 'Line')
                    if line_col is not None:
                        line_df = shp2l_clean.copy()
                        line_df['_line_val'] = pd.to_numeric(line_df[line_col], errors='coerce')
                        line_df = line_df.dropna(subset=['_line_val'])
                        if not line_df.empty:
                            line_blocks = []
                            for line_value in sorted(line_df['_line_val'].unique()):
                                if not np.isfinite(line_value):
                                    continue
                                if line_value not in (1, 2):
                                    continue
                                line_subset = line_df[line_df['_line_val'] == line_value]
                                d13_line = pd.to_numeric(line_subset['d 13C/12C  Mean'], errors='coerce').std()
                                d18_line = pd.to_numeric(line_subset['d 18O/16O  Mean'], errors='coerce').std()
                                d13_text = "--" if pd.isna(d13_line) else f"{d13_line:.3f}‰"
                                d18_text = "--" if pd.isna(d18_line) else f"{d18_line:.3f}‰"
                                line_blocks.append(
                                    f"<p style='font-size: 16px; margin: 4px 0;'>"
                                    f"<b>Line {int(line_value)} precision:</b> d13C {d13_text} | d18O {d18_text}"
                                    f"</p>"
                                )
                            if line_blocks:
                                line_precision_markup = (
                                    "<div style='margin-top: 8px;'>" + "".join(line_blocks) + "</div>"
                                )

                    # Calculate precision after linearity correction (if available)
                    d13c_lin_prec = None
                    d18o_lin_prec = None
                    try:
                        fits = st.session_state.get('linearity_fits')
                        if fits:
                            i_series = pd.to_numeric(shp2l_clean['1  Cycle Int  Samp  44'], errors='coerce')
                            y13_series = pd.to_numeric(shp2l_clean['d 13C/12C  Mean'], errors='coerce')
                            y18_series = pd.to_numeric(shp2l_clean['d 18O/16O  Mean'], errors='coerce')
                            if np.isfinite(fits.get('d13C', {}).get('slope', np.nan)):
                                s = fits['d13C']['slope']; xr = fits['d13C']['x_ref']
                                d13_corr = (y13_series - s * (i_series - xr)).where(np.isfinite(y13_series) & np.isfinite(i_series))
                                d13c_lin_prec = float(d13_corr.std())
                            if np.isfinite(fits.get('d18O', {}).get('slope', np.nan)):
                                s = fits['d18O']['slope']; xr = fits['d18O']['x_ref']
                                d18_corr = (y18_series - s * (i_series - xr)).where(np.isfinite(y18_series) & np.isfinite(i_series))
                                d18o_lin_prec = float(d18_corr.std())
                    except Exception:
                        pass

                    # Determine colors based on precision values and standards percentage
                    d13c_precision_color = '#ff4444' if d13c_precision > 0.1 else '#2ecc71'
                    d18o_precision_color = '#ff4444' if d18o_precision > 0.1 else '#2ecc71'
                    d13c_lin_color = None if d13c_lin_prec is None else ('#ff4444' if d13c_lin_prec > 0.1 else '#2ecc71')
                    d18o_lin_color = None if d18o_lin_prec is None else ('#ff4444' if d18o_lin_prec > 0.1 else '#2ecc71')
                    standards_percentage_color = '#2ecc71' if standards_percentage >= 75 else '#666666'

                    # Optional markup for linearity-corrected precision display
                    d13c_lin_markup = (f"<p style='font-size: 16px; margin: 2px 0;'><i>d13C Precision (linearity corrected):</i> "
                                       f"<span style='color: {d13c_lin_color}'>{d13c_lin_prec:.3f}‰</span></p>") if d13c_lin_prec is not None else ""
                    d18o_lin_markup = (f"<p style='font-size: 16px; margin: 2px 0;'><i>d18O Precision (linearity corrected):</i> "
                                       f"<span style='color: {d18o_lin_color}'>{d18o_lin_prec:.3f}‰</span></p>") if d18o_lin_prec is not None else ""
                    
                    st.markdown(f"""
                    <div style='background-color: #f0f2f6; padding: 20px; border-radius: 10px; margin: 10px 0;'>
                        <h3 style='color: #1f77b4; margin-bottom: 15px;'>Precision and Averages for {standard} (Excluding Outliers)</h3>
                        <div style='display: flex; justify-content: space-between;'>
                            <div style='flex: 1; margin-right: 20px;'>
                                <p style='font-size: 18px; margin: 5px 0;'><b>d13C Precision:</b> <span style='color: {d13c_precision_color}'>{d13c_precision:.3f}‰</span></p>
                                {d13c_lin_markup}
                                <p style='font-size: 18px; margin: 5px 0;'><b>d13C Average:</b> <span style='color: #000000'>{shp2l_clean['d 13C/12C  Mean'].mean():.3f}‰</span></p>
                            </div>
                            <div style='flex: 1;'>
                                <p style='font-size: 18px; margin: 5px 0;'><b>d18O Precision:</b> <span style='color: {d18o_precision_color}'>{d18o_precision:.3f}‰</span></p>
                                {d18o_lin_markup}
                                <p style='font-size: 18px; margin: 5px 0;'><b>d18O Average:</b> <span style='color: #000000'>{shp2l_clean['d 18O/16O  Mean'].mean():.3f}‰</span></p>
                            </div>
                        </div>
                        {line_precision_markup}
                        <div style='margin-top: 15px; padding-top: 10px; border-top: 1px solid #ddd;'>
                            <p style='font-size: 16px; color: {standards_percentage_color};'>Standards included: {included_standards} out of {total_standards} ({standards_percentage:.1f}%)</p>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    # Calculate statistics for both methods
                    # IMPORTANT: compute stats from the same data shown on the plot.
                    # Use nonâ€‘outlier points when outlier detection is enabled to avoid biased/offset lines.
                    try:
                        stats_source = shp2l_filtered_data.loc[~(d13c_outliers | d18o_outliers)].copy()
                    except Exception:
                        # Fallback to all data if for any reason the masks are unavailable
                        stats_source = shp2l_filtered_data.copy()

                    # Use the same base data for thresholds as the outlier detection
                    threshold_source = shp2l_filtered_data

                    # Ensure numeric dtype and ignore NaNs
                    d13c_series = pd.to_numeric(threshold_source['d 13C/12C  Mean'], errors='coerce')
                    d18o_series = pd.to_numeric(threshold_source['d 18O/16O  Mean'], errors='coerce')

                    # Compute mean/std using the same sigma logic as outlier detection
                    d13c_mean, d13c_std, _ = _compute_sigma_stats(d13c_series, sigma_level)
                    d18o_mean, d18o_std, _ = _compute_sigma_stats(d18o_series, sigma_level)

                    # Sigma level lines (for Z-Score method)
                    sigma_level_d13c_plus = d13c_mean + sigma_level * d13c_std
                    sigma_level_d13c_minus = d13c_mean - sigma_level * d13c_std
                    sigma_level_d18o_plus = d18o_mean + sigma_level * d18o_std
                    sigma_level_d18o_minus = d18o_mean - sigma_level * d18o_std

                    # IQR statistics with the irq_multiplier instead of hardcoded 1.5
                    q1_d13c = d13c_series.quantile(0.25)
                    q3_d13c = d13c_series.quantile(0.75)
                    iqr_d13c = q3_d13c - q1_d13c
                    iqr_level_d13c_plus = q3_d13c + irq_multiplier * iqr_d13c
                    iqr_level_d13c_minus = q1_d13c - irq_multiplier * iqr_d13c

                    q1_d18o = d18o_series.quantile(0.25)
                    q3_d18o = d18o_series.quantile(0.75)
                    iqr_d18o = q3_d18o - q1_d18o
                    iqr_level_d18o_plus = q3_d18o + irq_multiplier * iqr_d18o
                    iqr_level_d18o_minus = q1_d18o - irq_multiplier * iqr_d18o

                    # Define sequence index for plotting (full dataset for alignment)
                    seq_index = pd.Series(range(1, len(shp2l_filtered_data) + 1), index=shp2l_filtered_data.index)
                    outlier_mask = (d13c_outliers | d18o_outliers).reindex(shp2l_filtered_data.index).fillna(False)
                    inlier_mask = ~outlier_mask
                    inlier_df = shp2l_filtered_data.loc[inlier_mask]
                    outlier_df = shp2l_filtered_data.loc[outlier_mask]

                    # Generate plots based on user choice
                    if calibration_type == "Z-Score":
                        # Plot for Î´13C with Z-Score thresholds
                        fig_d13c = px.scatter(
                            x=seq_index.loc[inlier_df.index],
                            y=inlier_df['d 13C/12C  Mean'],
                            color=inlier_df[color_param],  # Add color parameter
            title=f'SHP2L d13C Calibration Values (Z-Score Method)',
            labels={'y': 'd13C (‰)', 'x': 'Sequence', 'color': color_param},
                            color_continuous_scale='Viridis'  # Use the Viridis colorscale
                        )
                        fig_d13c.update_traces(marker=dict(showscale=False))  # Disable color scale legend
                        if not outlier_df.empty:
                            fig_d13c.add_trace(go.Scatter(
                                x=seq_index.loc[outlier_df.index],
                                y=outlier_df['d 13C/12C  Mean'],
                                mode='markers',
                                name='Outliers',
                                marker=dict(color='rgba(220, 50, 50, 0.9)', symbol='x', size=10)
                            ))
                        fig_d13c.add_hline(y=sigma_level_d13c_plus, line_color='green', line_dash='dot',
                                           annotation_text=f'+{sigma_level}Ïƒ')
                        fig_d13c.add_hline(y=sigma_level_d13c_minus, line_color='green', line_dash='dot',
                                           annotation_text=f'-{sigma_level}Ïƒ')
                        fig_d13c.add_hline(y=d13c_mean, line_color='purple', line_dash='solid',
                                           annotation_text='Mean Value')

                        # Plot for Î´18O with Z-Score thresholds
                        fig_d18o = px.scatter(
                            x=seq_index.loc[inlier_df.index],
                            y=inlier_df['d 18O/16O  Mean'],
                            color=inlier_df[color_param],  # Add color parameter
            title=f'SHP2L d18O Calibration Values (Z-Score Method)',
            labels={'y': 'd18O (‰)', 'x': 'Sequence', 'color': color_param},
                            color_continuous_scale='Viridis'  # Use the Viridis colorscale
                        )
                        fig_d18o.update_traces(marker=dict(showscale=False))  # Disable color scale legend
                        if not outlier_df.empty:
                            fig_d18o.add_trace(go.Scatter(
                                x=seq_index.loc[outlier_df.index],
                                y=outlier_df['d 18O/16O  Mean'],
                                mode='markers',
                                name='Outliers',
                                marker=dict(color='rgba(220, 50, 50, 0.9)', symbol='x', size=10)
                            ))
                        fig_d18o.add_hline(y=sigma_level_d18o_plus, line_color='green', line_dash='dot',
                                           annotation_text=f'+{sigma_level}Ïƒ')
                        fig_d18o.add_hline(y=sigma_level_d18o_minus, line_color='green', line_dash='dot',
                                           annotation_text=f'-{sigma_level}Ïƒ')
                        fig_d18o.add_hline(y=d18o_mean, line_color='purple', line_dash='solid',
                                           annotation_text='Mean Value')

                    elif calibration_type == "IQR":
                        # Plot for Î´13C with IQR thresholds
                        fig_d13c = px.scatter(
                            x=seq_index.loc[inlier_df.index],
                            y=inlier_df['d 13C/12C  Mean'],
                            color=inlier_df[color_param],  # Add color parameter
            title=f'SHP2L d13C Calibration Values (IQR Method)',
            labels={'y': 'd13C (‰)', 'x': 'Sequence', 'color': color_param},
                            color_continuous_scale='Viridis'  # Use the Viridis colorscale
                        )
                        fig_d13c.update_traces(marker=dict(showscale=False))  # Disable color scale legend
                        if not outlier_df.empty:
                            fig_d13c.add_trace(go.Scatter(
                                x=seq_index.loc[outlier_df.index],
                                y=outlier_df['d 13C/12C  Mean'],
                                mode='markers',
                                name='Outliers',
                                marker=dict(color='rgba(220, 50, 50, 0.9)', symbol='x', size=10)
                            ))
                        fig_d13c.add_hline(y=iqr_level_d13c_plus, line_color='green', line_dash='dot',
                                           annotation_text=f'+{irq_multiplier:g} IQR')
                        fig_d13c.add_hline(y=iqr_level_d13c_minus, line_color='green', line_dash='dot',
                                           annotation_text=f'-{irq_multiplier:g} IQR')
                        fig_d13c.add_hline(y=q3_d13c, line_color='purple', line_dash='solid',
                                           annotation_text='Q3 (75th Percentile)')
                        fig_d13c.add_hline(y=q1_d13c, line_color='purple', line_dash='solid',
                                           annotation_text='Q1 (25th Percentile)')

                        # Plot for Î´18O with IQR thresholds
                        fig_d18o = px.scatter(
                            x=seq_index.loc[inlier_df.index],
                            y=inlier_df['d 18O/16O  Mean'],
                            color=inlier_df[color_param],  # Add color parameter
            title=f'SHP2L d18O Calibration Values (IQR Method)',
            labels={'y': 'd18O (‰)', 'x': 'Sequence', 'color': color_param},
                            color_continuous_scale='Viridis'  # Use the Viridis colorscale
                        )
                        fig_d18o.update_traces(marker=dict(showscale=False))  # Disable color scale legend
                        if not outlier_df.empty:
                            fig_d18o.add_trace(go.Scatter(
                                x=seq_index.loc[outlier_df.index],
                                y=outlier_df['d 18O/16O  Mean'],
                                mode='markers',
                                name='Outliers',
                                marker=dict(color='rgba(220, 50, 50, 0.9)', symbol='x', size=10)
                            ))
                        fig_d18o.add_hline(y=iqr_level_d18o_plus, line_color='green', line_dash='dot',
                                           annotation_text=f'+{irq_multiplier:g} IQR')
                        fig_d18o.add_hline(y=iqr_level_d18o_minus, line_color='green', line_dash='dot',
                                           annotation_text=f'-{irq_multiplier:g} IQR')
                        fig_d18o.add_hline(y=q3_d18o, line_color='purple', line_dash='solid',
                                           annotation_text='Q3 (75th Percentile)')
                        fig_d18o.add_hline(y=q1_d18o, line_color='purple', line_dash='solid',
                                           annotation_text='Q1 (25th Percentile)')

                    # Display the plots
                    st.plotly_chart(fig_d13c, width='stretch')
                    st.plotly_chart(fig_d18o, width='stretch')


            else:
                st.write("Please select at least one standard.")

    with tab3:
        st.header('Data Processing')

        # Initialize the DataFrame copy at the start
        df_copy = st.session_state.df.copy()

        # Initialize session state for download options if not already set
        if 'include_outliers' not in st.session_state:
            st.session_state.include_outliers = "No"
        if 'selected_ids' not in st.session_state:
            st.session_state.selected_ids = ["All"]

        # Initialize the DataFrame and add Sequence column
        df_copy = st.session_state.df.copy()
        df_copy['Sequence'] = df_copy['Identifier 2'].apply(
            lambda x: int(re.search(r'\d+', str(x)).group()) if pd.notnull(x) and isinstance(x, (
            str, float, int)) and re.search(r'\d+', str(x)) else None
        )

        # Filter ranges for data processing
        st.subheader("Range Filter Outliers Settings")
        col1, col2 = st.columns(2)
        
        with col1:
            # Signal Intensity filter
            signal_min = 0.0
            signal_max = 50.0
            # Store ranges in session state to make them available throughout the app
            st.session_state.signal_range = st.slider(
                'Filter by Signal Intensity',
                min_value=signal_min,
                max_value=signal_max,
                value=(1.0, signal_max)
            )

            # Leak Rate filter
            leak_min = float(df_copy['leak_rate'].min())
            leak_max = float(df_copy['leak_rate'].max())
            st.session_state.leak_range = st.slider(
                'Filter by Leak Rate',
                min_value=leak_min,
                max_value=leak_max,
                value=(leak_min, float(1000))
            )

        with col2:
            # d13C filter
            d13c_min = float(df_copy['d 13C/12C  Mean'].min())
            d13c_max = float(df_copy['d 13C/12C  Mean'].max())
            st.session_state.d13c_range = st.slider(
                'Filter by d13C',
                min_value=d13c_min,
                max_value=d13c_max,
                value=(float(-10), float(10))
            )

            # d18O filter
            d18o_min = float(df_copy['d 18O/16O  Mean'].min())
            d18o_max = float(df_copy['d 18O/16O  Mean'].max())
            st.session_state.d18o_range = st.slider(
                'Filter by d18O',
                min_value=d18o_min,
                max_value=d18o_max,
                value=(float(-10), float(10))
            )

        # Apply identifier filter if any identifiers are selected
        if identifier_filter:
            df_copy = df_copy[df_copy['Identifier 1'].isin(identifier_filter)]
            
        # Calculate total samples before filtering
        total_samples = len(df_copy)
        
        # Create masks for each filter
        signal_mask = (df_copy['1  Cycle Int  Samp  44'] >= st.session_state.signal_range[0]) & (df_copy['1  Cycle Int  Samp  44'] <= st.session_state.signal_range[1])
        leak_mask = (df_copy['leak_rate'] >= st.session_state.leak_range[0]) & (df_copy['leak_rate'] <= st.session_state.leak_range[1])
        d13c_mask = (df_copy['d 13C/12C  Mean'] >= st.session_state.d13c_range[0]) & (df_copy['d 13C/12C  Mean'] <= st.session_state.d13c_range[1])
        d18o_mask = (df_copy['d 18O/16O  Mean'] >= st.session_state.d18o_range[0]) & (df_copy['d 18O/16O  Mean'] <= st.session_state.d18o_range[1])
        
        # Calculate excluded samples for each filter individually
        excluded_by_signal = sum(~signal_mask)
        excluded_by_leak = sum(~leak_mask)
        excluded_by_d13c = sum(~d13c_mask)
        excluded_by_d18o = sum(~d18o_mask)
        
        # Keep an unfiltered copy for outlier detection
        df_unfiltered = df_copy.copy()
        
        # Apply all filters to a filtered copy for plotting
        df_filtered = df_copy.loc[signal_mask & leak_mask & d13c_mask & d18o_mask]

        # Exclude selected standards from plotting data
        standards_to_exclude = st.session_state.get('selected_standards', selected_standards if 'selected_standards' in locals() else [])
        if standards_to_exclude:
            df_filtered = df_filtered[~df_filtered['Identifier 1'].isin(standards_to_exclude)]
            df_unfiltered = df_unfiltered[~df_unfiltered['Identifier 1'].isin(standards_to_exclude)]
        
        # Calculate total excluded after applying all filters
        total_excluded = total_samples - len(df_copy)
        
        # # Display excluded samples information
        # st.markdown("#### Samples Excluded by Filters")
        # col1, col2 = st.columns(2)
        # with col1:
        #     st.write(f"Signal Intensity: {excluded_by_signal:,d} samples")
        #     st.write(f"Leak Rate: {excluded_by_leak:,d} samples")
        # with col2:
        #     st.write(f"Î´13C Range: {excluded_by_d13c:,d} samples")
        #     st.write(f"Î´18O Range: {excluded_by_d18o:,d} samples")
        # st.markdown(f"**Total Samples Excluded: {total_excluded:,d} of {total_samples:,d}**")

        st.subheader("Statistical Outlier Settings")
        sigma_level_data = st.number_input("Set Sigma Level for data Outlier Exclusion",
                                         min_value=0.1,
                                         max_value=6.0,
                                         value=4.0,
                                         step=0.1)

        

        # Create a subheader and expander to show active filters

        # with st.expander("Active Filters"):
        #     st.write("Signal Intensity Range:", f"{signal_range[0]:.2f} to {signal_range[1]:.2f}")
        #     st.write("Leak Rate Range:", f"{leak_range[0]:.2f} to {leak_range[1]:.2f}")
        #     st.write("Î´13C Range:", f"{d13c_range[0]:.2f} to {d13c_range[1]:.2f}")
        #     st.write("Î´18O Range:", f"{d18o_range[0]:.2f} to {d18o_range[1]:.2f}")

        # Prepare main dataset based on user selections
        data_to_process = df_copy.copy()
        
        # Filter by selected Identifier 1 values if not "All"
        if "All" not in st.session_state.selected_ids:
            data_to_process = data_to_process[data_to_process['Identifier 1'].isin(st.session_state.selected_ids)]

        # Initialize mask for statistical outliers
        statistical_mask = pd.Series(False, index=data_to_process.index, dtype=bool)
        group_series = _get_species_series(data_to_process)
        
        # Calculate statistical outliers separately for each identifier and comment group
        for identifier in data_to_process['Identifier 1'].unique():
            for group_val in group_series[data_to_process['Identifier 1'] == identifier].unique():
                group_mask = (data_to_process['Identifier 1'] == identifier) & (group_series == group_val)
                group_data = data_to_process[group_mask]
                
                if len(group_data) > 1:  # Only process groups with more than one sample
                    # Calculate thresholds for this group
                    mean_d13C = group_data['d 13C/12C  Mean'].mean()
                    std_d13C = group_data['d 13C/12C  Mean'].std()
                    mean_d18O = group_data['d 18O/16O  Mean'].mean()
                    std_d18O = group_data['d 18O/16O  Mean'].std()

                    # Identify statistical outliers in this group
                    group_stat_outliers = (
                        (group_data['d 13C/12C  Mean'] < mean_d13C - (sigma_level_data * std_d13C)) |
                        (group_data['d 13C/12C  Mean'] > mean_d13C + (sigma_level_data * std_d13C)) |
                        (group_data['d 18O/16O  Mean'] < mean_d18O - (sigma_level_data * std_d18O)) |
                        (group_data['d 18O/16O  Mean'] > mean_d18O + (sigma_level_data * std_d18O))
                    )
                    statistical_mask.loc[group_mask] = group_stat_outliers.astype(bool).to_numpy()
                    
        # Get standards from calibration table
        try:
            standards_df = pd.read_csv("standards.csv")
            calibration_standards = standards_df['Standard'].unique().tolist()
        except Exception:
            calibration_standards = []
        
        # Add any selected standards from the calibration tab
        all_standards = calibration_standards + (selected_standards if selected_standards else [])
        
        # Now invert the mask to get within_statistical
        within_statistical = ~statistical_mask

        # Create mask for data within all ranges
        status_series_all = data_to_process.get('Collector Status', pd.Series(False, index=data_to_process.index))
        not_saturated_samples = status_series_all != 'Fully Saturated Collectors'
        not_failed_samples = status_series_all != 'Failed Sample'
        within_ranges = (
            (data_to_process['d 13C/12C  Mean'] >= st.session_state.d13c_range[0]) &
            (data_to_process['d 13C/12C  Mean'] <= st.session_state.d13c_range[1]) &
            (data_to_process['d 18O/16O  Mean'] >= st.session_state.d18o_range[0]) &
            (data_to_process['d 18O/16O  Mean'] <= st.session_state.d18o_range[1]) &
            (data_to_process['1  Cycle Int  Samp  44'] >= st.session_state.signal_range[0]) &
            (data_to_process['1  Cycle Int  Samp  44'] <= st.session_state.signal_range[1]) &
            (data_to_process['leak_rate'] >= st.session_state.leak_range[0]) &
            (data_to_process['leak_rate'] <= st.session_state.leak_range[1]) &
            not_saturated_samples &
            not_failed_samples
        )

        # Combine range and statistical masks
        within_all = within_ranges & within_statistical

        # Filter out standards from the data before calculating statistics
        non_standards_mask = ~data_to_process['Identifier 1'].isin(all_standards)
        data_without_standards = data_to_process[non_standards_mask].copy()

        # Calculate total samples (excluding standards)
        # Count unique samples and total measurements
        unique_samples = data_without_standards.groupby(['Identifier 1', 'Identifier 2']).size().reset_index().shape[0]
        total_measurements = len(data_without_standards)

        # Calculate outliers using data_without_standards
        stat_outliers = sum(statistical_mask[non_standards_mask])
        d13c_mask = (data_without_standards['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) | (data_without_standards['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
        d18o_mask = (data_without_standards['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) | (data_without_standards['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
        signal_mask = (data_without_standards['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) | (data_without_standards['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
        leak_mask = (data_without_standards['leak_rate'] < st.session_state.leak_range[0]) | (data_without_standards['leak_rate'] > st.session_state.leak_range[1])
        status_series_no_std = data_without_standards.get('Collector Status', pd.Series(False, index=data_without_standards.index))
        failed_mask = status_series_no_std == 'Failed Sample'
        saturated_mask = status_series_no_std == 'Partially Saturated Collectors'
        saturated_sample_mask = status_series_no_std == 'Fully Saturated Collectors'

        # Count outliers
        d13c_outliers = sum(d13c_mask)
        d18o_outliers = sum(d18o_mask)
        signal_outliers = sum(signal_mask)
        leak_outliers = sum(leak_mask)
        failed_outliers = int(failed_mask.sum())
        saturated_collectors = int(saturated_mask.sum())
        saturated_samples = int(saturated_sample_mask.sum())

        # Calculate final analyses (total samples minus all outliers)
        total_outliers = stat_outliers + d13c_outliers + d18o_outliers + signal_outliers + leak_outliers + failed_outliers + saturated_samples
        final_analyses = total_samples - total_outliers

        # Create a DataFrame for displaying statistics
        stats_data = []

        # Add total samples and final analyses
        stats_data.append({
            'Metric': 'Total Unique Samples',
            'Value': unique_samples,
            'Details': '(excluding standards)'
        })
        stats_data.append({
            'Metric': 'Total Measurements',
            'Value': total_measurements,
            'Details': '(excluding standards)'
        })

        # Add outliers by category
        if stat_outliers > 0:
            stats_data.append({
                'Metric': 'Statistical Outliers',
                'Value': stat_outliers,
                'Details': f'({(stat_outliers/total_measurements)*100:.1f}% of measurements)'
            })
        if d13c_outliers > 0:
            stats_data.append({
                'Metric': 'd13C Range Outliers',
                'Value': d13c_outliers,
                'Details': f'({(d13c_outliers/total_measurements)*100:.1f}% of measurements)'
            })
        if d18o_outliers > 0:
            stats_data.append({
                'Metric': 'd18O Range Outliers',
                'Value': d18o_outliers,
                'Details': f'({(d18o_outliers/total_measurements)*100:.1f}% of measurements)'
            })
        if signal_outliers > 0:
            stats_data.append({
                'Metric': 'Signal Intensity Outliers',
                'Value': signal_outliers,
                'Details': f'({(signal_outliers/total_measurements)*100:.1f}% of measurements)'
            })
        if leak_outliers > 0:
            stats_data.append({
                'Metric': 'Leak Rate Outliers',
                'Value': leak_outliers,
                'Details': f'({(leak_outliers/total_measurements)*100:.1f}% of measurements)'
            })
        stats_data.append({
            'Metric': 'Failed Samples',
            'Value': failed_outliers,
            'Details': f'({(failed_outliers/total_measurements)*100:.1f}% of measurements)'
        })
        stats_data.append({
            'Metric': 'Partially Failed (Recovered Mean)',
            'Value': saturated_collectors,
            'Details': f'({(saturated_collectors/total_measurements)*100:.1f}% of measurements)'
        })
        stats_data.append({
            'Metric': 'Fully Saturated Collectors',
            'Value': saturated_samples,
            'Details': f'({(saturated_samples/total_measurements)*100:.1f}% of measurements)'
        })

        stats_data.append({
            'Metric': 'Final Analyses',
            'Value': final_analyses,
            'Details': f'(Total Measurements - Outliers)'
        })

        # Convert to DataFrame
        stats_df = pd.DataFrame(stats_data)

        # Place the Download Dataset section
        st.subheader("Download Dataset")
        st.write("Configure your dataset download options below:")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            include_outliers = st.radio(
                "Include outliers in dataset?",
                ["Yes", "No"],
                index=0 if st.session_state.include_outliers == "Yes" else 1,  # Match session state
                help="Choose whether to include outliers in the downloaded dataset",
                key="include_outliers_widget"
            )
            # Update session state
            st.session_state.include_outliers = include_outliers

            # When including outliers, allow optional interpolation before export
            if st.session_state.include_outliers == "Yes":
                # Let the widget manage session_state via its key; don't assign to session_state directly
                _interpolate_outliers_export = st.checkbox(
                    "Interpolate outliers before export",
                    value=st.session_state.get("interpolate_outliers_export", False),
                    help="Linearly interpolate values for rows flagged as outliers before downloading.",
                    key="interpolate_outliers_export"
                )
            else:
                st.session_state.interpolate_outliers_export = False

        with col2:
            selected_ids = st.multiselect(
                "Select Identifier 1 values to include:",
                options=["All"] + list(df_copy['Identifier 1'].unique()),
                default=st.session_state.selected_ids,  # Match session state
                help="Choose specific Identifier 1 values to include in the download. Select 'All' to include everything.",
                key="selected_ids_widget"
            )
            # Update session state
            st.session_state.selected_ids = selected_ids

        with col3:
            st.dataframe(
                stats_df,
                hide_index=True,
                column_config={
                    "Metric": st.column_config.TextColumn("Metric", width=200),
                    "Value": st.column_config.NumberColumn("Value", width=100),
                    "Details": st.column_config.TextColumn("Details", width=150)
                }
            )
        with col4:
            st.text_input(
                "Client name",
                value=st.session_state.get('client_name', ''),
                key='client_name'
            )
            # Species replacement mapping for Client Output
            try:
                unique_species_vals = sorted([str(x) for x in _get_species_series(df_copy).dropna().unique().tolist()])
            except Exception:
                unique_species_vals = []
            existing_map = st.session_state.get('comment_replacements', {}) or {}
            with st.expander("Species labels (from Label/Species) — customize for Client Output"):
                new_map = {}
                for i, sval in enumerate(unique_species_vals):
                    key = f"species_map_{i}"
                    default_val = existing_map.get(sval, sval)
                    user_val = st.text_input(f"{sval}", value=str(default_val), key=key)
                    new_map[sval] = user_val
                st.session_state.comment_replacements = new_map
        st.markdown("---")

        # Combine range and statistical masks
        within_all = within_ranges & within_statistical

        # Separate data into main_data and outliers_df
        main_data = data_to_process[within_all].copy() if st.session_state.include_outliers == "No" else data_to_process.copy()
        if st.session_state.include_outliers == "No":
            # Collect outliers with their categories
            outliers_df = pd.DataFrame()
            
            # Failed samples (pre with empty data)
            failed_outliers_df = data_to_process[
                data_to_process.get('Collector Status', pd.Series(False, index=data_to_process.index)) == 'Failed Sample'
            ].copy()
            if not failed_outliers_df.empty:
                failed_outliers_df['Category'] = 'Failed Sample'
                outliers_df = pd.concat([outliers_df, failed_outliers_df])
            
            saturated_samples_df = data_to_process[
                data_to_process.get('Collector Status', pd.Series(False, index=data_to_process.index)) == 'Fully Saturated Collectors'
            ].copy()
            if not saturated_samples_df.empty:
                saturated_samples_df['Category'] = 'Fully Saturated Collectors'
                outliers_df = pd.concat([outliers_df, saturated_samples_df])
            
            # Statistical outliers - making sure to use the correct index
            statistical_mask = pd.Series(False, index=data_to_process.index, dtype=bool)
            # Calculate statistical outliers by group
            for identifier in data_to_process['Identifier 1'].unique():
                for group_val in group_series[data_to_process['Identifier 1'] == identifier].unique():
                    group_mask = (data_to_process['Identifier 1'] == identifier) & (group_series == group_val)
                    group_data = data_to_process[group_mask]
                    
                    if len(group_data) > 1:  # Only process groups with more than one sample
                        # Calculate thresholds for this group
                        mean_d13C = group_data['d 13C/12C  Mean'].mean()
                        std_d13C = group_data['d 13C/12C  Mean'].std()
                        mean_d18O = group_data['d 18O/16O  Mean'].mean()
                        std_d18O = group_data['d 18O/16O  Mean'].std()

                        # Identify statistical outliers in this group
                        group_stat_outliers = (
                            (group_data['d 13C/12C  Mean'] < mean_d13C - (sigma_level_data * std_d13C)) |
                            (group_data['d 13C/12C  Mean'] > mean_d13C + (sigma_level_data * std_d13C)) |
                            (group_data['d 18O/16O  Mean'] < mean_d18O - (sigma_level_data * std_d18O)) |
                            (group_data['d 18O/16O  Mean'] > mean_d18O + (sigma_level_data * std_d18O))
                        )
                        statistical_mask.loc[group_mask] = group_stat_outliers.astype(bool).to_numpy()
            
            statistical_outliers = data_to_process[statistical_mask].copy()
            if not statistical_outliers.empty:
                statistical_outliers['Category'] = 'Statistical'
                outliers_df = pd.concat([outliers_df, statistical_outliers])
            
            # Range outliers by category
            d13c_outliers = data_to_process[
                (data_to_process['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                (data_to_process['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
            ].copy()
            if not d13c_outliers.empty:
                d13c_outliers['Category'] = 'd13C Range'
                outliers_df = pd.concat([outliers_df, d13c_outliers])
            
            d18o_outliers = data_to_process[
                (data_to_process['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                (data_to_process['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
            ].copy()
            if not d18o_outliers.empty:
                d18o_outliers['Category'] = 'd18O Range'
                outliers_df = pd.concat([outliers_df, d18o_outliers])
            
            signal_outliers = data_to_process[
                (data_to_process['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                (data_to_process['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
            ].copy()
            if not signal_outliers.empty:
                signal_outliers['Category'] = 'Signal Intensity'
                outliers_df = pd.concat([outliers_df, signal_outliers])
            
            leak_outliers = data_to_process[
                (data_to_process['leak_rate'] < st.session_state.leak_range[0]) |
                (data_to_process['leak_rate'] > st.session_state.leak_range[1])
            ].copy()
            if not leak_outliers.empty:
                leak_outliers['Category'] = 'Leak Rate'
                outliers_df = pd.concat([outliers_df, leak_outliers])
            
            # Remove duplicates (in case a sample is an outlier in multiple categories)
            if not outliers_df.empty:
                outliers_df = outliers_df.drop_duplicates(subset=['Identifier 1', 'Identifier 2'])
        else:
            outliers_df = pd.DataFrame()
        # Generate descriptive filename
        filename_parts = []
        if "All" not in selected_ids:
            if len(selected_ids) <= 3:
                filename_parts.append(f"ID{'_'.join(selected_ids)}")
            else:
                filename_parts.append(f"ID{len(selected_ids)}selected")
        filename_parts.append(f"{'with' if include_outliers == 'Yes' else 'without'}_outliers")
        filename = f"dataset_{'_'.join(filename_parts)}.xlsx"
        # Clarify in filename if interpolation will be applied
        if st.session_state.include_outliers == "Yes" and st.session_state.get("interpolate_outliers_export"):
            if filename.lower().endswith(".xlsx"):
                filename = filename[:-5] + "_interpolated.xlsx"

        # For "Include outliers = Yes", add an outlier-type column and merge outliers
        if st.session_state.include_outliers == "Yes":
            # Build per-row outlier category labels based on current settings
            try:
                stat_mask_all = statistical_mask.reindex(data_to_process.index, fill_value=False)
            except Exception:
                # Fallback in case statistical_mask is not aligned
                stat_mask_all = pd.Series(False, index=data_to_process.index)

            d13c_out_mask = (
                (data_to_process['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                (data_to_process['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
            )
            d18o_out_mask = (
                (data_to_process['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                (data_to_process['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
            )
            signal_out_mask = (
                (data_to_process['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                (data_to_process['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
            )
            leak_out_mask = (
                (data_to_process['leak_rate'] < st.session_state.leak_range[0]) |
                (data_to_process['leak_rate'] > st.session_state.leak_range[1])
            )
            failed_out_mask = data_to_process.get('Collector Status', pd.Series(False, index=data_to_process.index)) == 'Failed Sample'
            saturated_sample_out_mask = data_to_process.get('Collector Status', pd.Series(False, index=data_to_process.index)) == 'Fully Saturated Collectors'

            cat_bools = pd.DataFrame({
                'Statistical': stat_mask_all,
                'd13C Range': d13c_out_mask,
                'd18O Range': d18o_out_mask,
                'Signal Intensity': signal_out_mask,
                'Leak Rate': leak_out_mask,
                'Failed Sample': failed_out_mask,
                'Fully Saturated Collectors': saturated_sample_out_mask,
            }, index=data_to_process.index)

            # Join multiple categories with '; ' for rows that meet several outlier conditions
            outlier_types = cat_bools.apply(
                lambda row: '; '.join([cat for cat, is_out in row.items() if bool(is_out)]), axis=1
            )

            # Attach the outlier types to the main dataset being exported
            main_data = data_to_process.copy()
            main_data['Outlier Types'] = outlier_types

            # Clear outliers_df so only one consolidated sheet is exported
            outliers_df = pd.DataFrame()
            
            # Optionally interpolate outlier rows before export
            if st.session_state.get("interpolate_outliers_export"):
                try:
                    outlier_mask = main_data['Outlier Types'].astype(str).str.strip().replace({"": np.nan}).notna()
                    cols_to_interp = [
                        "1  Cycle Int  Samp  44",  # signal intensity for linearity correction
                        "d 13C/12C  Mean",
                        "d 13C/12C  Std Dev",
                        "d 18O/16O  Mean",
                        "d 18O/16O  Std Dev",
                        "d13C_calibrated",
                        "d18O_calibrated",
                    ]
                    present_cols = [c for c in cols_to_interp if c in main_data.columns]

                    if present_cols:
                        # Preserve originals in dedicated columns before interpolation
                        original_cols = []
                        for c in present_cols:
                            new_name = f"Original {c}"
                            main_data[new_name] = main_data[c]
                            original_cols.append(new_name)

                        # Interpolate using Identifier 2 ordering
                        main_data = _interpolate_outliers_by_identifier2(main_data, outlier_mask, present_cols, id2_col='Identifier 2')

                        # Reorder columns so that original columns sit next to Outlier Types
                        try:
                            cols = list(main_data.columns)
                            if 'Outlier Types' in cols:
                                pos = cols.index('Outlier Types')
                                # Remove originals from current position
                                for oc in original_cols:
                                    if oc in cols:
                                        cols.remove(oc)
                                # Insert originals after Outlier Types
                                cols = cols[:pos+1] + original_cols + cols[pos+1:]
                                main_data = main_data[cols]
                        except Exception:
                            pass
                except Exception as e:
                    st.warning(f"Interpolation step skipped due to error: {e}")
            
        download_excel(
            main_data,
            outliers=outliers_df,
            filename=filename,
            selected_standards=selected_standards,
            calibration_type=st.session_state.get('calibration_type'),
            sigma_level=st.session_state.get('sigma_level'),
            irq_multiplier=st.session_state.get('irq_multiplier'),
            client_name=st.session_state.get('client_name'),
            comment_map=st.session_state.get('comment_replacements'),
        )

        # Read the standards.csv file
        standards_df = pd.read_csv("standards.csv")
        standard_identifiers = standards_df['Standard'].unique()

        # Get unique identifiers excluding those in the standards file
        unique_identifiers = [
            identifier for identifier in df_copy['Identifier 1'].unique()
            if pd.notna(identifier) and identifier not in standard_identifiers
        ]

        # Remove any standards explicitly selected in the Calibration tab
        if standards_to_exclude:
            unique_identifiers = [identifier for identifier in unique_identifiers if identifier not in standards_to_exclude]

        # Add 'All' option to the unique_identifiers list (this will allow the user to select all identifiers)
        unique_identifiers.insert(0, 'All')

        # Charts Settings section
        st.subheader("Charts Settings")
        
        col1, col2 = st.columns(2)
        with col1:
            selected_identifier = st.selectbox("Select Identifier 1 (from Label):", options=unique_identifiers)
            x_axis_option = st.selectbox(
                "Choose X-Axis Display Option:",
                options=["By Identifier 2", "By Sequence"]
            )
            
        with col2:
            # New dropdown selector in Tab 3 for color parameter
            selected_color_param_tab3 = st.selectbox("Choose a parameter to color the dots in Tab 3:", color_param_names, index='Date' in color_param_names)
            color_param_tab3 = color_options[selected_color_param_tab3]

            show_statistical_outliers = st.checkbox("Show statistical outliers on chart", value=False, key="show_statistical_outliers")
            show_range_outliers = st.checkbox("Show range outliers on chart", value=False, key="show_range_outliers")
            show_saturated_collectors = st.checkbox("Show partially failed (recovered) samples on chart", value=True, key="show_saturated_collectors")
            show_saturated_samples = st.checkbox("Show failed samples (fully saturated) on chart", value=True, key="show_saturated_samples")
            show_failed_samples = st.checkbox("Show failed samples (no values) on chart", value=True, key="show_failed_samples")

        color_param_tab3_value_col = "_tab3_color_value"
        color_source_tab3 = df_filtered[color_param_tab3]
        color_values_tab3 = pd.to_numeric(color_source_tab3, errors='coerce')
        colorbar_category_ticks = None
        if color_values_tab3.isna().all():
            categories = color_source_tab3.astype(str)
            codes, uniques = pd.factorize(categories, sort=True)
            color_values_tab3 = pd.Series(codes, index=df_filtered.index)
            colorbar_category_ticks = (list(range(len(uniques))), [str(u) for u in uniques])
        df_filtered[color_param_tab3_value_col] = color_values_tab3

        # If 'All' is selected, include data for all identifiers
        if selected_identifier == 'All':
            subset_data = df_filtered
            subset_data_unfiltered = df_unfiltered
            
            # Get the actual data range for the selected parameter
            param_min = color_values_tab3.min()
            param_max = color_values_tab3.max()
            
            # Create a shared colorbar figure
            # Build colorbar configuration and use readable dates if needed
            colorbar_cfg = dict(
                title=dict(
                    text=selected_color_param_tab3,
                    side='top'  # Move title above the colorbar
                ),
                len=0.6,  # Make colorbar wider
                thickness=20,  # Make colorbar taller
                x=0.5,  # Center horizontally
                xanchor='center',
                y=0.5,  # Center vertically
                yanchor='middle',
                orientation='h'  # Horizontal orientation
            )
            if color_param_tab3 == 'Date_ordinal' and color_param_tab3 in df_filtered.columns:
                tickvals, ticktext = _build_date_colorbar_ticks(color_values_tab3)
                if tickvals and ticktext:
                    colorbar_cfg.update(tickvals=tickvals, ticktext=ticktext)

            if colorbar_category_ticks is not None:
                tickvals, ticktext = colorbar_category_ticks
                if tickvals and ticktext:
                    colorbar_cfg.update(tickvals=tickvals, ticktext=ticktext)

            colorbar_fig = go.Figure(go.Scatter(
                x=[0],  # Dummy data
                y=[0],
                mode='markers',
                marker=dict(
                    size=1,
                    color=[param_min, param_max],  # Use actual data range
                    cmin=param_min,
                    cmax=param_max,
                    colorscale="Viridis",
                    showscale=True,
                    colorbar=colorbar_cfg
                ),
                showlegend=False
            ))
            colorbar_fig.update_layout(
                margin=dict(t=30, b=0, l=50, r=50),  # Adjust margins for better spacing
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                xaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
                yaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
                height=100,  # Taller height for better visibility
                width=None  # Let width be determined by container
            )
            with col2:
                st.plotly_chart(colorbar_fig, width='stretch')
        else:
            subset_data = df_filtered[df_filtered['Identifier 1'] == selected_identifier]
            subset_data_unfiltered = df_unfiltered[df_unfiltered['Identifier 1'] == selected_identifier]




        # Use "Identifier 1 - Species" when Species exists; otherwise fall back to Identifier 1 only
        plot_label_col = "_plot_label"
        if 'Species' in subset_data.columns and not subset_data['Species'].isna().all():
            subset_data.loc[:, plot_label_col] = _compose_label_series(
                subset_data['Identifier 1'],
                subset_data['Species']
            )
            subset_data_unfiltered.loc[:, plot_label_col] = _compose_label_series(
                subset_data_unfiltered.get('Identifier 1', pd.Series(index=subset_data_unfiltered.index, dtype=object)),
                subset_data_unfiltered.get('Species', pd.Series(index=subset_data_unfiltered.index, dtype=object))
            )
        else:
            subset_data.loc[:, plot_label_col] = subset_data['Identifier 1'].fillna("Unknown").astype(str)
            subset_data_unfiltered.loc[:, plot_label_col] = subset_data_unfiltered.get(
                'Identifier 1', pd.Series(index=subset_data_unfiltered.index, dtype=object)
            ).fillna("Unknown").astype(str)
        species_col = plot_label_col

        # Iterate through unique species (including the placeholder)
        unique_species = subset_data[species_col].unique()

        # Assign a distinct marker symbol to each species for summary charts
        # Avoid symbols used for outliers ('x', 'cross', 'diamond', 'star')
        species_symbol_cycle = [
            'circle', 'square', 'triangle-up', 'triangle-down', 'triangle-left', 'triangle-right'
        ]
        species_symbol_map = {
            sp: species_symbol_cycle[i % len(species_symbol_cycle)]
            for i, sp in enumerate([s for s in unique_species if s != "Unknown"])
        }

        # Create x_axis values
        subset_data['x_axis'] = np.nan
        if x_axis_option == "By Identifier 2":
            subset_data['x_axis'] = subset_data['Identifier 2'].apply(
                lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                    r'\d+\.?\d*', str(x)) else None
            )
        else:
            subset_data['x_axis'] = range(len(subset_data))
        # Also create x_axis for unfiltered subset (used for status overlays)
        subset_data_unfiltered['x_axis'] = np.nan
        if x_axis_option == "By Identifier 2":
            subset_data_unfiltered['x_axis'] = subset_data_unfiltered['Identifier 2'].apply(
                lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                    r'\d+\.?\d*', str(x)) else None
            )
        else:
            subset_data_unfiltered['x_axis'] = range(len(subset_data_unfiltered))

        # Summary Charts
        st.subheader("Summary Charts")
        
        # Create summary chart for d13C
        d13c_summary = go.Figure()
        d13_legend_partial_shown = False
        d13_legend_failed_full_shown = False
        d13_legend_failed_no_values_shown = False
        for species in unique_species:
            if species == "Unknown":
                continue
            
            species_data = subset_data[subset_data[species_col] == species]
            species_data_unfiltered = subset_data_unfiltered[subset_data_unfiltered[species_col] == species]
            if species_data_unfiltered.empty and species_data.empty:
                continue

            status_series = species_data_unfiltered.get('Collector Status', pd.Series(False, index=species_data_unfiltered.index))
            saturated_collectors_mask = status_series == 'Partially Saturated Collectors'
            saturated_samples_mask = status_series == 'Fully Saturated Collectors'
            failed_mask = status_series == 'Failed Sample'
            saturated_samples_idx = species_data_unfiltered[saturated_samples_mask].index
            failed_idx = species_data_unfiltered[failed_mask].index
            
            # Calculate statistical outliers
            mean_d13C = species_data['d 13C/12C  Mean'].mean()
            std_d13C = species_data['d 13C/12C  Mean'].std()
            mean_d18O = species_data['d 18O/16O  Mean'].mean()
            std_d18O = species_data['d 18O/16O  Mean'].std()
            
            outlier_mask = (
                (species_data['d 13C/12C  Mean'] < mean_d13C - (sigma_level_data * std_d13C)) |
                (species_data['d 13C/12C  Mean'] > mean_d13C + (sigma_level_data * std_d13C)) |
                (species_data['d 18O/16O  Mean'] < mean_d18O - (sigma_level_data * std_d18O)) |
                (species_data['d 18O/16O  Mean'] > mean_d18O + (sigma_level_data * std_d18O))
            )
            # Store statistical outliers
            statistical_outliers = species_data[outlier_mask].copy()

            # Calculate range outliers mask (always calculate to filter data)
            range_mask = (
                (species_data['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                (species_data['d 13C/12C  Mean'] > st.session_state.d13c_range[1]) |
                (species_data['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                (species_data['d 18O/16O  Mean'] > st.session_state.d18o_range[1]) |
                (species_data['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                (species_data['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1]) |
                (species_data['leak_rate'] < st.session_state.leak_range[0]) |
                (species_data['leak_rate'] > st.session_state.leak_range[1])
            )

            # Store range outliers if showing them
            if show_range_outliers:
                range_outliers = species_data[range_mask].copy()
                # Add x_axis values to range outliers
                if x_axis_option == "By Identifier 2":
                    range_outliers['x_axis'] = range_outliers['Identifier 2'].apply(
                        lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                            r'\d+\.?\d*', str(x)) else None
                    )
                else:
                    range_outliers['x_axis'] = range(len(range_outliers))
            else:
                range_outliers = pd.DataFrame(columns=species_data.columns)
                
            # Filter data to plot - exclude statistical, range outliers, and saturated samples
            data_to_plot = species_data[
                ~(outlier_mask | range_mask | species_data.index.isin(saturated_samples_idx) | species_data.index.isin(failed_idx))
            ].copy()
            
            # Sort data by x_axis to ensure sequential line connections
            data_to_plot = data_to_plot.sort_values('x_axis')

            # Plot main data
            # Generate unique color based on species/comment
            species_color = f'rgb({hash(species) % 255}, {(hash(species) >> 8) % 255}, {(hash(species) >> 16) % 255})'
            
            d13c_summary.add_trace(go.Scatter(
                x=data_to_plot['x_axis'],
                y=data_to_plot['d 13C/12C  Mean'],
                mode='lines+markers',
                name=species,
                marker=dict(
                    size=8,
                    color=data_to_plot[color_param_tab3_value_col],
                    colorscale="Viridis",
                    showscale=False,
                    symbol=species_symbol_map.get(species, 'circle')
                ),
                line=dict(width=1, color=species_color),
                legendgroup=species
            ))

            # Highlight saturated collectors (compromised but valid)
            if show_saturated_collectors and saturated_collectors_mask.any():
                sat_collectors = species_data_unfiltered[saturated_collectors_mask]
                d13c_summary.add_trace(go.Scatter(
                    x=sat_collectors['x_axis'],
                    y=sat_collectors['d 13C/12C  Mean'],
                    mode='markers',
                    name='Partially Failed (Recovered Mean)',
                    marker=dict(
                        size=12,
                        symbol='diamond-open',
                        color='#ff7f0e',
                        line=dict(width=2, color='#ff7f0e')
                    ),
                    showlegend=not d13_legend_partial_shown,
                    legendgroup='collector_status'
                ))
                d13_legend_partial_shown = True

            # Plot saturated samples as outliers
            if show_saturated_samples and saturated_samples_mask.any():
                sat_samples = species_data_unfiltered[saturated_samples_mask]
                y_vals_sat = pd.to_numeric(sat_samples['d 13C/12C  Mean'], errors='coerce')
                if y_vals_sat.notna().any():
                    y_sat = y_vals_sat.tolist()
                else:
                    y_vals = pd.to_numeric(species_data['d 13C/12C  Mean'], errors='coerce')
                    y_min = y_vals.min()
                    y_max = y_vals.max()
                    if not np.isfinite(y_min):
                        y_min, y_max = -1.0, 1.0
                    y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                    y_sat = [y_min - (0.15 * y_range if y_range > 0 else 0.75)] * len(sat_samples)
                d13c_summary.add_trace(go.Scatter(
                    x=sat_samples['x_axis'],
                    y=y_sat,
                    mode='markers',
                    name='Failed Samples (Fully Saturated)',
                    marker=dict(
                        size=12,
                        symbol='triangle-down',
                        color='#d62728',
                        line=dict(width=2, color='#d62728')
                    ),
                    showlegend=not d13_legend_failed_full_shown,
                    legendgroup='outliers'
                ))
                d13_legend_failed_full_shown = True

            if show_failed_samples and failed_mask.any():
                failed_samples = species_data_unfiltered[failed_mask]
                y_vals = pd.to_numeric(species_data['d 13C/12C  Mean'], errors='coerce')
                y_min = y_vals.min()
                y_max = y_vals.max()
                if not np.isfinite(y_min):
                    y_min, y_max = -1.0, 1.0
                y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                y_failed = y_min - (0.1 * y_range if y_range > 0 else 0.5)
                d13c_summary.add_trace(go.Scatter(
                    x=failed_samples['x_axis'],
                    y=[y_failed] * len(failed_samples),
                    mode='markers',
                    name='Failed Samples (No Values)',
                    marker=dict(
                        size=10,
                        symbol='triangle-down',
                        color='#7f7f7f',
                        line=dict(width=1, color='#7f7f7f')
                    ),
                    showlegend=not d13_legend_failed_no_values_shown,
                    legendgroup='outliers',
                    text=failed_samples['Identifier 2'].astype(str)
                ))
                d13_legend_failed_no_values_shown = True

            # Plot statistical outliers if enabled
            if show_statistical_outliers and not statistical_outliers.empty:
                d13c_summary.add_trace(go.Scatter(
                    x=statistical_outliers['x_axis'],
                    y=statistical_outliers['d 13C/12C  Mean'],
                    mode='markers',
                    name='Statistical Outliers',
                    marker=dict(
                        size=12,
                        symbol='x',
                        color=species_color,
                        line=dict(width=2, color=species_color)
                    ),
                    showlegend=True,
                    legendgroup='outliers'
                ))

            # Plot range outliers by type if enabled
            if show_range_outliers and not range_outliers.empty:
                # Signal intensity outliers
                signal_mask = (range_outliers['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) | (range_outliers['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
                if signal_mask.any():
                    d13c_summary.add_trace(go.Scatter(
                        x=range_outliers[signal_mask]['x_axis'],
                        y=range_outliers[signal_mask]['d 13C/12C  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='diamond',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='Signal Intensity Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))

                # Leak rate outliers
                leak_mask = (range_outliers['leak_rate'] < st.session_state.leak_range[0]) | (range_outliers['leak_rate'] > st.session_state.leak_range[1])
                if leak_mask.any():
                    d13c_summary.add_trace(go.Scatter(
                        x=range_outliers[leak_mask]['x_axis'],
                        y=range_outliers[leak_mask]['d 13C/12C  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='star',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='Leak Rate Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))

                # Î´13C range outliers
                d13c_mask = (range_outliers['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) | (range_outliers['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
                if d13c_mask.any():
                    d13c_summary.add_trace(go.Scatter(
                        x=range_outliers[d13c_mask]['x_axis'],
                        y=range_outliers[d13c_mask]['d 13C/12C  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='cross',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='d13C Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))

                # Î´18O range outliers
                d18o_mask = (range_outliers['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) | (range_outliers['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
                if d18o_mask.any():
                    d13c_summary.add_trace(go.Scatter(
                        x=range_outliers[d18o_mask]['x_axis'],
                        y=range_outliers[d18o_mask]['d 13C/12C  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='x',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='d18O Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))
        d13c_summary.update_layout(
            title="d13C Summary by Species",
            xaxis_title="Sample Number" if x_axis_option == "By Sequence" else "Identifier 2",
            yaxis_title="d13C",
            showlegend=True,
            height=500
        )
        st.plotly_chart(d13c_summary, width='stretch')
        
        # Create summary chart for d18O
        d18o_summary = go.Figure()
        d18_legend_partial_shown = False
        d18_legend_failed_full_shown = False
        d18_legend_failed_no_values_shown = False
        for species in unique_species:
            if species == "Unknown":
                continue
            
            species_data = subset_data[subset_data[species_col] == species]
            species_data_unfiltered = subset_data_unfiltered[subset_data_unfiltered[species_col] == species]
            if species_data_unfiltered.empty and species_data.empty:
                continue

            status_series = species_data_unfiltered.get('Collector Status', pd.Series(False, index=species_data_unfiltered.index))
            saturated_collectors_mask = status_series == 'Partially Saturated Collectors'
            saturated_samples_mask = status_series == 'Fully Saturated Collectors'
            failed_mask = status_series == 'Failed Sample'
            saturated_samples_idx = species_data_unfiltered[saturated_samples_mask].index
            failed_idx = species_data_unfiltered[failed_mask].index
            
            # Calculate statistical outliers
            mean_d13C = species_data['d 13C/12C  Mean'].mean()
            std_d13C = species_data['d 13C/12C  Mean'].std()
            mean_d18O = species_data['d 18O/16O  Mean'].mean()
            std_d18O = species_data['d 18O/16O  Mean'].std()
            
            outlier_mask = (
                (species_data['d 13C/12C  Mean'] < mean_d13C - (sigma_level_data * std_d13C)) |
                (species_data['d 13C/12C  Mean'] > mean_d13C + (sigma_level_data * std_d13C)) |
                (species_data['d 18O/16O  Mean'] < mean_d18O - (sigma_level_data * std_d18O)) |
                (species_data['d 18O/16O  Mean'] > mean_d18O + (sigma_level_data * std_d18O))
            )
            statistical_outliers = species_data[outlier_mask].copy()
            data_to_plot = species_data[
                ~(outlier_mask | species_data.index.isin(saturated_samples_idx) | species_data.index.isin(failed_idx))
            ].copy()
            
            # Sort data by x_axis to ensure sequential line connections
            data_to_plot = data_to_plot.sort_values('x_axis')
            
            # Calculate range outliers
            if show_range_outliers:
                range_mask = (
                    (species_data_unfiltered['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                    (species_data_unfiltered['d 13C/12C  Mean'] > st.session_state.d13c_range[1]) |
                    (species_data_unfiltered['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                    (species_data_unfiltered['d 18O/16O  Mean'] > st.session_state.d18o_range[1]) |
                    (species_data_unfiltered['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                    (species_data_unfiltered['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1]) |
                    (species_data_unfiltered['leak_rate'] < st.session_state.leak_range[0]) |
                    (species_data_unfiltered['leak_rate'] > st.session_state.leak_range[1])
                )
                range_outliers = species_data_unfiltered[range_mask].copy()
                # Add x_axis values to range outliers
                if x_axis_option == "By Identifier 2":
                    range_outliers['x_axis'] = range_outliers['Identifier 2'].apply(
                        lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                            r'\d+\.?\d*', str(x)) else None
                    )
                else:
                    range_outliers['x_axis'] = range(len(range_outliers))
            else:
                range_outliers = pd.DataFrame(columns=species_data.columns)

            # Plot main data
            # Generate unique color for this species
            species_color = f'rgb({hash(species) % 255}, {(hash(species) >> 8) % 255}, {(hash(species) >> 16) % 255})'
            
            # Plot main data with consistent color
            d18o_summary.add_trace(go.Scatter(
                x=data_to_plot['x_axis'],
                y=data_to_plot['d 18O/16O  Mean'],
                mode='lines+markers',
                name=species,
                marker=dict(
                    size=8,
                    color=data_to_plot[color_param_tab3_value_col],
                    colorscale="Viridis",
                    showscale=False,
                    symbol=species_symbol_map.get(species, 'circle')
                ),
                line=dict(width=1, color=species_color),
                legendgroup=species
            ))

            # Highlight saturated collectors (compromised but valid)
            if show_saturated_collectors and saturated_collectors_mask.any():
                sat_collectors = species_data_unfiltered[saturated_collectors_mask]
                d18o_summary.add_trace(go.Scatter(
                    x=sat_collectors['x_axis'],
                    y=sat_collectors['d 18O/16O  Mean'],
                    mode='markers',
                    name='Partially Failed (Recovered Mean)',
                    marker=dict(
                        size=12,
                        symbol='diamond-open',
                        color='#ff7f0e',
                        line=dict(width=2, color='#ff7f0e')
                    ),
                    showlegend=not d18_legend_partial_shown,
                    legendgroup='collector_status'
                ))
                d18_legend_partial_shown = True

            # Plot saturated samples as outliers
            if show_saturated_samples and saturated_samples_mask.any():
                sat_samples = species_data_unfiltered[saturated_samples_mask]
                y_vals_sat = pd.to_numeric(sat_samples['d 18O/16O  Mean'], errors='coerce')
                if y_vals_sat.notna().any():
                    y_sat = y_vals_sat.tolist()
                else:
                    y_vals = pd.to_numeric(species_data['d 18O/16O  Mean'], errors='coerce')
                    y_min = y_vals.min()
                    y_max = y_vals.max()
                    if not np.isfinite(y_min):
                        y_min, y_max = -1.0, 1.0
                    y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                    y_sat = [y_min - (0.15 * y_range if y_range > 0 else 0.75)] * len(sat_samples)
                d18o_summary.add_trace(go.Scatter(
                    x=sat_samples['x_axis'],
                    y=y_sat,
                    mode='markers',
                    name='Failed Samples (Fully Saturated)',
                    marker=dict(
                        size=12,
                        symbol='triangle-down',
                        color='#d62728',
                        line=dict(width=2, color='#d62728')
                    ),
                    showlegend=not d18_legend_failed_full_shown,
                    legendgroup='outliers'
                ))
                d18_legend_failed_full_shown = True

            if show_failed_samples and failed_mask.any():
                failed_samples = species_data_unfiltered[failed_mask]
                y_vals = pd.to_numeric(species_data['d 18O/16O  Mean'], errors='coerce')
                y_min = y_vals.min()
                y_max = y_vals.max()
                if not np.isfinite(y_min):
                    y_min, y_max = -1.0, 1.0
                y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                y_failed = y_min - (0.1 * y_range if y_range > 0 else 0.5)
                d18o_summary.add_trace(go.Scatter(
                    x=failed_samples['x_axis'],
                    y=[y_failed] * len(failed_samples),
                    mode='markers',
                    name='Failed Samples (No Values)',
                    marker=dict(
                        size=10,
                        symbol='triangle-down',
                        color='#7f7f7f',
                        line=dict(width=1, color='#7f7f7f')
                    ),
                    showlegend=not d18_legend_failed_no_values_shown,
                    legendgroup='outliers',
                    text=failed_samples['Identifier 2'].astype(str)
                ))
                d18_legend_failed_no_values_shown = True

            # Plot statistical outliers if enabled
            if show_statistical_outliers and not statistical_outliers.empty:
                d18o_summary.add_trace(go.Scatter(
                    x=statistical_outliers['x_axis'],
                    y=statistical_outliers['d 18O/16O  Mean'],
                    mode='markers',
                    name='Statistical Outliers',
                    marker=dict(
                        size=12,
                        symbol='x',
                        color=species_color,
                        line=dict(width=2, color=species_color)
                    ),
                    showlegend=True,
                    legendgroup='outliers'
                ))

            # Plot range outliers by type if enabled
            if show_range_outliers and not range_outliers.empty:
                # Signal intensity outliers
                signal_mask = (range_outliers['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) | (range_outliers['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
                if signal_mask.any():
                    d18o_summary.add_trace(go.Scatter(
                        x=range_outliers[signal_mask]['x_axis'],
                        y=range_outliers[signal_mask]['d 18O/16O  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='diamond',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='Signal Intensity Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))

                # Leak rate outliers
                leak_mask = (range_outliers['leak_rate'] < st.session_state.leak_range[0]) | (range_outliers['leak_rate'] > st.session_state.leak_range[1])
                if leak_mask.any():
                    d18o_summary.add_trace(go.Scatter(
                        x=range_outliers[leak_mask]['x_axis'],
                        y=range_outliers[leak_mask]['d 18O/16O  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='star',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='Leak Rate Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))

                # Î´13C range outliers
                d13c_mask = (range_outliers['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) | (range_outliers['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
                if d13c_mask.any():
                    d18o_summary.add_trace(go.Scatter(
                        x=range_outliers[d13c_mask]['x_axis'],
                        y=range_outliers[d13c_mask]['d 18O/16O  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='cross',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='d13C Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))

                # Î´18O range outliers
                d18o_mask = (range_outliers['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) | (range_outliers['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
                if d18o_mask.any():
                    d18o_summary.add_trace(go.Scatter(
                        x=range_outliers[d18o_mask]['x_axis'],
                        y=range_outliers[d18o_mask]['d 18O/16O  Mean'],
                        mode='markers',
                        marker=dict(
                            color=species_color,
                            symbol='x',
                            size=12,
                            line=dict(width=2, color=species_color)
                        ),
                        name='d18O Range',
                        showlegend=True,
                        legendgroup='outliers'
                    ))
        d18o_summary.update_layout(
            title="d18O Summary by Species",
            xaxis_title="Sample Number" if x_axis_option == "By Sequence" else "Identifier 2",
            yaxis_title="d18O",
            showlegend=True,
            height=500
        )
        # Invert y-axis so increasing d18O plots downward
        d18o_summary.update_yaxes(autorange='reversed')
        st.plotly_chart(d18o_summary, width='stretch')

        # Create cross-plot: d13C vs d18O grouped by Species
        species_scatter = go.Figure()
        scatter_legend_partial_shown = False
        scatter_legend_failed_full_shown = False
        for species in unique_species:
            if species == "Unknown":
                continue

            species_data = subset_data[subset_data[species_col] == species]
            species_data_unfiltered = subset_data_unfiltered[subset_data_unfiltered[species_col] == species]
            if species_data_unfiltered.empty and species_data.empty:
                continue

            status_series = species_data_unfiltered.get('Collector Status', pd.Series(False, index=species_data_unfiltered.index))
            saturated_collectors_mask = status_series == 'Partially Saturated Collectors'
            saturated_samples_mask = status_series == 'Fully Saturated Collectors'
            saturated_samples_idx = species_data_unfiltered[saturated_samples_mask].index
            failed_mask = status_series == 'Failed Sample'
            failed_idx = species_data_unfiltered[failed_mask].index

            # Compute statistical thresholds per species
            mean_d13C = species_data['d 13C/12C  Mean'].mean()
            std_d13C = species_data['d 13C/12C  Mean'].std()
            mean_d18O = species_data['d 18O/16O  Mean'].mean()
            std_d18O = species_data['d 18O/16O  Mean'].std()

            outlier_mask = (
                (species_data['d 13C/12C  Mean'] < mean_d13C - (sigma_level_data * std_d13C)) |
                (species_data['d 13C/12C  Mean'] > mean_d13C + (sigma_level_data * std_d13C)) |
                (species_data['d 18O/16O  Mean'] < mean_d18O - (sigma_level_data * std_d18O)) |
                (species_data['d 18O/16O  Mean'] > mean_d18O + (sigma_level_data * std_d18O))
            )

            range_mask = (
                (species_data['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                (species_data['d 13C/12C  Mean'] > st.session_state.d13c_range[1]) |
                (species_data['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                (species_data['d 18O/16O  Mean'] > st.session_state.d18o_range[1]) |
                (species_data['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                (species_data['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1]) |
                (species_data['leak_rate'] < st.session_state.leak_range[0]) |
                (species_data['leak_rate'] > st.session_state.leak_range[1])
            )

            # Filter to non-outliers for main scatter
            data_to_plot = species_data[
                ~(outlier_mask | range_mask | species_data.index.isin(saturated_samples_idx) | species_data.index.isin(failed_idx))
            ].copy()
            # Drop rows with missing isotope values
            data_to_plot = data_to_plot[
                data_to_plot['d 13C/12C  Mean'].notna() & data_to_plot['d 18O/16O  Mean'].notna()
            ]
            if data_to_plot.empty:
                continue

            # Color and symbol by species, reusing existing mapping for consistency
            species_color = f'rgb({hash(species) % 255}, {(hash(species) >> 8) % 255}, {(hash(species) >> 16) % 255})'
            species_symbol = species_symbol_map.get(species, 'circle')

            species_scatter.add_trace(go.Scatter(
                x=data_to_plot['d 18O/16O  Mean'],
                y=data_to_plot['d 13C/12C  Mean'],
                mode='markers',
                name=species,
                marker=dict(
                    size=10,
                    color=species_color,
                    symbol=species_symbol,
                    line=dict(width=1, color=species_color)
                ),
                hovertemplate=(
                    f'Species: {species}<br>'
                    'd18O: %{x:.4f}<br>'
                    'd13C: %{y:.4f}<br>'
                    'Identifier 2: %{text}<extra></extra>'
                ),
                text=data_to_plot['Identifier 2'].astype(str)
            ))

            # Overlay saturated collectors (valid means)
            if show_saturated_collectors and saturated_collectors_mask.any():
                sat_collectors = species_data_unfiltered[saturated_collectors_mask]
                sat_collectors = sat_collectors[
                    sat_collectors['d 13C/12C  Mean'].notna() & sat_collectors['d 18O/16O  Mean'].notna()
                ]
                if not sat_collectors.empty:
                    species_scatter.add_trace(go.Scatter(
                        x=sat_collectors['d 18O/16O  Mean'],
                        y=sat_collectors['d 13C/12C  Mean'],
                        mode='markers',
                        name='Partially Failed (Recovered Mean)',
                        marker=dict(
                            size=12,
                            symbol='diamond-open',
                            color='#ff7f0e',
                            line=dict(width=2, color='#ff7f0e')
                        ),
                        showlegend=not scatter_legend_partial_shown,
                        legendgroup='collector_status',
                        text=sat_collectors['Identifier 2'].astype(str)
                    ))
                    scatter_legend_partial_shown = True

            # Overlay saturated samples as outliers
            if show_saturated_samples and saturated_samples_mask.any():
                sat_samples = species_data_unfiltered[saturated_samples_mask]
                sat_samples = sat_samples[
                    sat_samples['d 13C/12C  Mean'].notna() & sat_samples['d 18O/16O  Mean'].notna()
                ]
                if not sat_samples.empty:
                    species_scatter.add_trace(go.Scatter(
                        x=sat_samples['d 18O/16O  Mean'],
                        y=sat_samples['d 13C/12C  Mean'],
                        mode='markers',
                        name='Failed Samples (Fully Saturated)',
                        marker=dict(
                            size=12,
                            symbol='triangle-down',
                            color='#d62728',
                            line=dict(width=2, color='#d62728')
                        ),
                        showlegend=not scatter_legend_failed_full_shown,
                        legendgroup='outliers',
                        text=sat_samples['Identifier 2'].astype(str)
                    ))
                    scatter_legend_failed_full_shown = True

        species_scatter.update_layout(
            title="d13C vs d18O by Species",
            xaxis_title="d18O",
            yaxis_title="d13C",
            showlegend=True,
            height=500
        )
        st.plotly_chart(species_scatter, width='stretch')

        # Process individual species
        for species in unique_species:
            # Filter data for this specific species
            species_data = subset_data[subset_data[species_col] == species]
            species_data_unfiltered = subset_data_unfiltered[subset_data_unfiltered[species_col] == species]
            if species_data_unfiltered.empty and species_data.empty:
                continue

            # Skip if Identifier 2 is empty
            if species_data['Identifier 2'].isna().all() and species_data_unfiltered['Identifier 2'].isna().all():
                continue

            status_series = species_data_unfiltered.get('Collector Status', pd.Series(False, index=species_data_unfiltered.index))
            saturated_collectors_mask = status_series == 'Partially Saturated Collectors'
            saturated_samples_mask = status_series == 'Fully Saturated Collectors'
            failed_mask = status_series == 'Failed Sample'
            saturated_samples_idx = species_data_unfiltered[saturated_samples_mask].index

            col1, col2 = st.columns([3, 1])
            with col1:
                st.subheader(f'Species: {species}')

            # Calculate thresholds for outliers for each comment subset
            mean_d13C = species_data['d 13C/12C  Mean'].mean()
            std_d13C = species_data['d 13C/12C  Mean'].std()
            mean_d18O = species_data['d 18O/16O  Mean'].mean()
            std_d18O = species_data['d 18O/16O  Mean'].std()

            lower_threshold_d13C = mean_d13C - (sigma_level_data * std_d13C)
            upper_threshold_d13C = mean_d13C + (sigma_level_data * std_d13C)
            lower_threshold_d18O = mean_d18O - (sigma_level_data * std_d18O)
            upper_threshold_d18O = mean_d18O + (sigma_level_data * std_d18O)

            # Create x_axis values first for all data
            species_data['x_axis'] = np.nan
            if x_axis_option == "By Identifier 2":
                species_data['x_axis'] = species_data['Identifier 2'].apply(
                    lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                        r'\d+\.?\d*', str(x)) else None
                )
            else:
                species_data['x_axis'] = range(len(species_data))

            # Now identify statistical outliers (after x_axis is created)
            outlier_mask = (
                (species_data['d 13C/12C  Mean'] < lower_threshold_d13C) |
                (species_data['d 13C/12C  Mean'] > upper_threshold_d13C) |
                (species_data['d 18O/16O  Mean'] < lower_threshold_d18O) |
                (species_data['d 18O/16O  Mean'] > upper_threshold_d18O)
            )
            # Apply mask and include necessary columns (including x_axis)
            statistical_outliers = species_data[outlier_mask].copy()

            # Remove statistical outliers and saturated samples from data_to_plot
            data_to_plot = species_data[
                ~(outlier_mask | species_data.index.isin(saturated_samples_idx) | species_data.index.isin(failed_idx))
            ].copy()

            # Identify range bar outliers from unfiltered data
            # Identify and process range outliers if enabled
            if show_range_outliers:
                # Create mask for range outliers
                range_mask = (
                    (species_data_unfiltered['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                    (species_data_unfiltered['d 13C/12C  Mean'] > st.session_state.d13c_range[1]) |
                    (species_data_unfiltered['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                    (species_data_unfiltered['d 18O/16O  Mean'] > st.session_state.d18o_range[1]) |
                    (species_data_unfiltered['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                    (species_data_unfiltered['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1]) |
                    (species_data_unfiltered['leak_rate'] < st.session_state.leak_range[0]) |
                    (species_data_unfiltered['leak_rate'] > st.session_state.leak_range[1])
                )
                # Apply mask and include necessary columns
                range_bar_outliers = species_data_unfiltered[range_mask].copy()

                # Add x_axis values to range outliers if any were found
                if not range_bar_outliers.empty:
                    if x_axis_option == "By Identifier 2":
                        range_bar_outliers['x_axis'] = range_bar_outliers['Identifier 2'].apply(
                            lambda x: float(re.search(r'\d+\.?\d*', str(x)).group()) if pd.notnull(x) and re.search(
                                r'\d+\.?\d*', str(x)) else None
                        )
                    else:
                        range_bar_outliers['x_axis'] = range(len(range_bar_outliers))
            else:
                # Create empty DataFrame with required columns
                range_bar_outliers = pd.DataFrame(columns=['Identifier 1', 'Identifier 2', 'd 13C/12C  Mean', 'd 18O/16O  Mean', species_col, 'x_axis'])

            # Combine both types of outliers
            outliers = pd.concat([statistical_outliers, range_bar_outliers]).drop_duplicates()

            # Handle range outliers
            if not show_range_outliers:
                data_to_plot = data_to_plot[~data_to_plot.index.isin(range_bar_outliers.index)]
            
            # Create a DataFrame for displaying points, always excluding outliers for the main curve
            display_data = data_to_plot.copy()
                
            # Sort the data by x_axis to ensure proper line connections
            display_data = display_data.sort_values(by='x_axis', na_position='last')

            chart_height = 500


            # x_axis values are already created and sorted earlier

            # Loop through all identifiers to plot data for each identifier
            for identifier in unique_identifiers:
                if identifier == 'All':
                    continue  # Skip the 'All' selection here to avoid combined plotting

                # Filter data for the current identifier
                data_for_identifier = data_to_plot[data_to_plot['Identifier 1'] == identifier]

                has_status_markers = False
                if show_saturated_collectors and not species_data_unfiltered[
                    (species_data_unfiltered['Identifier 1'] == identifier) & saturated_collectors_mask
                ].empty:
                    has_status_markers = True
                if show_saturated_samples and not species_data_unfiltered[
                    (species_data_unfiltered['Identifier 1'] == identifier) & saturated_samples_mask
                ].empty:
                    has_status_markers = True
                if show_failed_samples and not species_data_unfiltered[
                    (species_data_unfiltered['Identifier 1'] == identifier) & failed_mask
                ].empty:
                    has_status_markers = True

                if data_for_identifier.empty and not has_status_markers:
                    continue  # Skip if there is no data to plot for this identifier

                # Plot d13C data for this identifier and comment
                # Create figure for d13C
                fig_d13C = go.Figure()

                # Add statistical outliers as markers if enabled
                if show_statistical_outliers and not statistical_outliers.empty:
                    identifier_stat_outliers = statistical_outliers[statistical_outliers['Identifier 1'] == identifier]
                    if not identifier_stat_outliers.empty:
                        fig_d13C.add_trace(go.Scatter(
                            x=identifier_stat_outliers['x_axis'],
                            y=identifier_stat_outliers['d 13C/12C  Mean'],
                            mode='markers',
                            marker=dict(
                                color='red',
                                symbol='x',
                                size=12,
                                line=dict(width=2)
                            ),
                            name='Statistical Outliers'
                        ))
                        # Add them to display_data if checkbox is checked - no need to add here since they're already in display_data

                # Add range outliers if enabled
                if show_range_outliers:
                    identifier_range_outliers = range_bar_outliers[range_bar_outliers['Identifier 1'] == identifier]
                    if not identifier_range_outliers.empty:
                        # Identify outlier types
                        signal_range_mask = (identifier_range_outliers['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) | (identifier_range_outliers['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
                        leak_range_mask = (identifier_range_outliers['leak_rate'] < st.session_state.leak_range[0]) | (identifier_range_outliers['leak_rate'] > st.session_state.leak_range[1])
                        d13c_filter_mask = (identifier_range_outliers['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) | (identifier_range_outliers['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
                        d18o_filter_mask = (identifier_range_outliers['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) | (identifier_range_outliers['d 18O/16O  Mean'] > st.session_state.d18o_range[1])

                        # Plot each type with different symbol but same red color
                        if signal_range_mask.any():
                            fig_d13C.add_trace(go.Scatter(
                                x=identifier_range_outliers[signal_range_mask]['x_axis'],
                                y=identifier_range_outliers[signal_range_mask]['d 13C/12C  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='diamond', size=12, line=dict(width=2)),
                                name='Signal Intensity Range'
                            ))
                        if leak_range_mask.any():
                            fig_d13C.add_trace(go.Scatter(
                                x=identifier_range_outliers[leak_range_mask]['x_axis'],
                                y=identifier_range_outliers[leak_range_mask]['d 13C/12C  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='star', size=12, line=dict(width=2)),
                                name='Leak Rate Range'
                            ))
                        if d13c_filter_mask.any():
                            fig_d13C.add_trace(go.Scatter(
                                x=identifier_range_outliers[d13c_filter_mask]['x_axis'],
                                y=identifier_range_outliers[d13c_filter_mask]['d 13C/12C  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='cross', size=12, line=dict(width=2)),
                                name='d13C Range'
                            ))
                        if d18o_filter_mask.any():
                            fig_d13C.add_trace(go.Scatter(
                                x=identifier_range_outliers[d18o_filter_mask]['x_axis'],
                                y=identifier_range_outliers[d18o_filter_mask]['d 13C/12C  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='x', size=12, line=dict(width=2)),
                                name='d18O Range'
                            ))

                # Highlight saturated collectors (valid means)
                if show_saturated_collectors:
                    identifier_sat_collectors = species_data_unfiltered[
                        (species_data_unfiltered['Identifier 1'] == identifier) & saturated_collectors_mask
                    ]
                    if not identifier_sat_collectors.empty:
                        fig_d13C.add_trace(go.Scatter(
                            x=identifier_sat_collectors['x_axis'],
                            y=identifier_sat_collectors['d 13C/12C  Mean'],
                            mode='markers',
                            marker=dict(color='#ff7f0e', symbol='diamond-open', size=12, line=dict(width=2)),
                            name='Partially Failed (Recovered Mean)'
                        ))

                # Show saturated samples as outliers
                if show_saturated_samples:
                    identifier_sat_samples = species_data_unfiltered[
                        (species_data_unfiltered['Identifier 1'] == identifier) & saturated_samples_mask
                    ]
                    if not identifier_sat_samples.empty:
                        y_vals_sat = pd.to_numeric(identifier_sat_samples['d 13C/12C  Mean'], errors='coerce')
                        if y_vals_sat.notna().any():
                            y_sat = y_vals_sat.tolist()
                        else:
                            y_vals = pd.to_numeric(data_for_identifier['d 13C/12C  Mean'], errors='coerce')
                            y_min = y_vals.min()
                            y_max = y_vals.max()
                            if not np.isfinite(y_min):
                                y_min, y_max = -1.0, 1.0
                            y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                            y_sat = [y_min - (0.15 * y_range if y_range > 0 else 0.75)] * len(identifier_sat_samples)
                        fig_d13C.add_trace(go.Scatter(
                            x=identifier_sat_samples['x_axis'],
                            y=y_sat,
                            mode='markers',
                            marker=dict(color='#d62728', symbol='triangle-down', size=12, line=dict(width=2)),
                            name='Failed Samples (Fully Saturated)'
                        ))

                if show_failed_samples:
                    identifier_failed = species_data_unfiltered[
                        (species_data_unfiltered['Identifier 1'] == identifier) & failed_mask
                    ]
                    if not identifier_failed.empty:
                        y_vals = pd.to_numeric(data_for_identifier['d 13C/12C  Mean'], errors='coerce')
                        y_min = y_vals.min()
                        y_max = y_vals.max()
                        if not np.isfinite(y_min):
                            y_min, y_max = -1.0, 1.0
                        y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                        y_failed = y_min - (0.1 * y_range if y_range > 0 else 0.5)
                        fig_d13C.add_trace(go.Scatter(
                            x=identifier_failed['x_axis'],
                            y=[y_failed] * len(identifier_failed),
                            mode='markers',
                            marker=dict(color='#7f7f7f', symbol='triangle-down', size=10, line=dict(width=1)),
                            name='Failed Samples (No Values)'
                        ))

                fig_d13C.add_trace(go.Scatter(
                    x=display_data[display_data['Identifier 1'] == identifier]['x_axis'],
                    y=display_data[display_data['Identifier 1'] == identifier]['d 13C/12C  Mean'],
                    mode='lines+markers',
                    line=dict(color='blue', dash='dot', width=2),
                    marker=dict(
                        color=display_data[display_data['Identifier 1'] == identifier][color_param_tab3_value_col],
                        colorscale="Viridis",
                        symbol='circle',
                        size=8,
                        showscale=False  # Hide individual colorbar
                    ),
                    name=f'Raw d13C - {identifier}'
                ))

                if 'd13C_calibrated' in data_for_identifier.columns:
                    fig_d13C.add_trace(go.Scatter(
                        x=display_data[display_data['Identifier 1'] == identifier]['x_axis'],
                        y=display_data[display_data['Identifier 1'] == identifier]['d13C_calibrated'],
                        mode='lines+markers',
                        line=dict(color='orange', dash='dot', width=2),
                        marker=dict(
                            color=display_data[display_data['Identifier 1'] == identifier][color_param_tab3_value_col],
                            colorscale="Viridis",
                            symbol='square',
                            size=8,
                            showscale=False  # Hide individual colorbar
                        ),
                        name=f'Calibrated d13C - {identifier}'
                    ))

                fig_d13C.update_layout(
                    title=f'{identifier} - d13C for Species: {species}',
                    xaxis_title='X Axis',
                    yaxis_title='d13C (‰)',
                    legend_title='Data Type',
                    margin=dict(r=100, t=100),  # Reduced right margin
                    xaxis=dict(
                        # Show ~10 ticks across the axis
                        nticks=10,
                        tickmode='auto'
                    ),
                    legend=dict(
                        x=1.05,  # Move legend closer to chart
                        xanchor='left',
                        y=0.8,  # Keep consistent position
                        yanchor='middle'
                    )
                )

                st.plotly_chart(fig_d13C, width='stretch', height=chart_height)

                # Plot Î´18O data for this identifier and comment
                # Create figure for Î´18O
                fig_d18O = go.Figure()

                # Add statistical outliers if enabled
                if show_statistical_outliers:
                    identifier_stat_outliers = statistical_outliers[statistical_outliers['Identifier 1'] == identifier]
                    if not identifier_stat_outliers.empty:
                        fig_d18O.add_trace(go.Scatter(
                            x=identifier_stat_outliers['x_axis'],
                            y=identifier_stat_outliers['d 18O/16O  Mean'],
                            mode='markers',
                            marker=dict(
                                color='red',
                                symbol='x',
                                size=12,
                                line=dict(width=2)
                            ),
                            name='Statistical Outliers'
                        ))

                # Add range outliers if enabled
                # Initialize filter masks with default values
                signal_range_mask = pd.Series(False)
                leak_range_mask = pd.Series(False)
                d13c_filter_mask = pd.Series(False)
                d18o_filter_mask = pd.Series(False)

                if show_range_outliers:
                    identifier_range_outliers = range_bar_outliers[range_bar_outliers['Identifier 1'] == identifier]
                    if not identifier_range_outliers.empty:
                        # Identify outlier types
                        signal_range_mask = (identifier_range_outliers['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) | (identifier_range_outliers['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
                        leak_range_mask = (identifier_range_outliers['leak_rate'] < st.session_state.leak_range[0]) | (identifier_range_outliers['leak_rate'] > st.session_state.leak_range[1])
                        d13c_filter_mask = (identifier_range_outliers['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) | (identifier_range_outliers['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
                        d18o_filter_mask = (identifier_range_outliers['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) | (identifier_range_outliers['d 18O/16O  Mean'] > st.session_state.d18o_range[1])

                        # Plot each type with different symbol but same red color
                        if signal_range_mask.any():
                            fig_d18O.add_trace(go.Scatter(
                                x=identifier_range_outliers[signal_range_mask]['x_axis'],
                                y=identifier_range_outliers[signal_range_mask]['d 18O/16O  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='diamond', size=12, line=dict(width=2)),
                                name='Signal Intensity Range'
                            ))
                        if leak_range_mask.any():
                            fig_d18O.add_trace(go.Scatter(
                                x=identifier_range_outliers[leak_range_mask]['x_axis'],
                                y=identifier_range_outliers[leak_range_mask]['d 18O/16O  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='star', size=12, line=dict(width=2)),
                                name='Leak Rate Range'
                            ))

                    # Add main data trace using display_data
                    fig_d18O.add_trace(go.Scatter(
                        x=display_data[display_data['Identifier 1'] == identifier]['x_axis'],
                        y=display_data[display_data['Identifier 1'] == identifier]['d 18O/16O  Mean'],
                        mode='lines+markers',
                        line=dict(color='blue', dash='dot', width=2),
                        marker=dict(
                            color=display_data[display_data['Identifier 1'] == identifier][color_param_tab3_value_col],
                            colorscale="Viridis",
                            symbol='circle',
                            size=8,
                            showscale=False  # Hide individual colorbar
                        ),
                        name=f'Raw d18O - {identifier}'
                    ))
    
                    if 'd18O_calibrated' in display_data.columns:
                        fig_d18O.add_trace(go.Scatter(
                            x=display_data[display_data['Identifier 1'] == identifier]['x_axis'],
                            y=display_data[display_data['Identifier 1'] == identifier]['d18O_calibrated'],
                            mode='lines+markers',
                            line=dict(color='orange', dash='dot', width=2),
                            marker=dict(
                                color=display_data[display_data['Identifier 1'] == identifier][color_param_tab3_value_col],
                                colorscale="Viridis",
                                symbol='square',
                                size=8
                            ),
                            name=f'Calibrated d18O - {identifier}'
                        ))
                        if d13c_filter_mask.any():
                            fig_d18O.add_trace(go.Scatter(
                                x=identifier_range_outliers[d13c_filter_mask]['x_axis'],
                                y=identifier_range_outliers[d18o_filter_mask]['d 18O/16O  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='cross', size=12, line=dict(width=2)),
                                name='d13C Range'
                            ))
                        if d18o_filter_mask.any():
                            fig_d18O.add_trace(go.Scatter(
                                x=identifier_range_outliers[d18o_filter_mask]['x_axis'],
                                y=identifier_range_outliers[d18o_filter_mask]['d 18O/16O  Mean'],
                                mode='markers',
                                marker=dict(color='red', symbol='x', size=12, line=dict(width=2)),
                                name='d18O Range'
                            ))

                # Plot main data trace with correct sorting
                sorted_data = data_for_identifier.sort_values(by='x_axis')

                # Highlight saturated collectors (valid means)
                if show_saturated_collectors:
                    identifier_sat_collectors = species_data_unfiltered[
                        (species_data_unfiltered['Identifier 1'] == identifier) & saturated_collectors_mask
                    ]
                    if not identifier_sat_collectors.empty:
                        fig_d18O.add_trace(go.Scatter(
                            x=identifier_sat_collectors['x_axis'],
                            y=identifier_sat_collectors['d 18O/16O  Mean'],
                            mode='markers',
                            marker=dict(color='#ff7f0e', symbol='diamond-open', size=12, line=dict(width=2)),
                            name='Partially Failed (Recovered Mean)'
                        ))

                # Show saturated samples as outliers
                if show_saturated_samples:
                    identifier_sat_samples = species_data_unfiltered[
                        (species_data_unfiltered['Identifier 1'] == identifier) & saturated_samples_mask
                    ]
                    if not identifier_sat_samples.empty:
                        y_vals_sat = pd.to_numeric(identifier_sat_samples['d 18O/16O  Mean'], errors='coerce')
                        if y_vals_sat.notna().any():
                            y_sat = y_vals_sat.tolist()
                        else:
                            y_vals = pd.to_numeric(data_for_identifier['d 18O/16O  Mean'], errors='coerce')
                            y_min = y_vals.min()
                            y_max = y_vals.max()
                            if not np.isfinite(y_min):
                                y_min, y_max = -1.0, 1.0
                            y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                            y_sat = [y_min - (0.15 * y_range if y_range > 0 else 0.75)] * len(identifier_sat_samples)
                        fig_d18O.add_trace(go.Scatter(
                            x=identifier_sat_samples['x_axis'],
                            y=y_sat,
                            mode='markers',
                            marker=dict(color='#d62728', symbol='triangle-down', size=12, line=dict(width=2)),
                            name='Failed Samples (Fully Saturated)'
                        ))

                if show_failed_samples:
                    identifier_failed = species_data_unfiltered[
                        (species_data_unfiltered['Identifier 1'] == identifier) & failed_mask
                    ]
                    if not identifier_failed.empty:
                        y_vals = pd.to_numeric(data_for_identifier['d 18O/16O  Mean'], errors='coerce')
                        y_min = y_vals.min()
                        y_max = y_vals.max()
                        if not np.isfinite(y_min):
                            y_min, y_max = -1.0, 1.0
                        y_range = y_max - y_min if np.isfinite(y_max) else 1.0
                        y_failed = y_min - (0.1 * y_range if y_range > 0 else 0.5)
                        fig_d18O.add_trace(go.Scatter(
                            x=identifier_failed['x_axis'],
                            y=[y_failed] * len(identifier_failed),
                            mode='markers',
                            marker=dict(color='#7f7f7f', symbol='triangle-down', size=10, line=dict(width=1)),
                            name='Failed Samples (No Values)'
                        ))

                fig_d18O.add_trace(go.Scatter(
                    x=sorted_data['x_axis'],
                    y=sorted_data['d 18O/16O  Mean'],
                    mode='lines+markers',
                    line=dict(color='blue', dash='dot', width=2),
                    marker=dict(
                        color=sorted_data[color_param_tab3_value_col],
                        colorscale="Viridis",
                        symbol='circle',
                        size=8,
                        showscale=False  # Hide individual colorbar
                    ),
                    name=f'Raw d18O - {identifier}'
                ))

                if 'd18O_calibrated' in data_for_identifier.columns:
                    fig_d18O.add_trace(go.Scatter(
                        x=sorted_data['x_axis'],
                        y=sorted_data['d18O_calibrated'],
                        mode='lines+markers',
                        line=dict(color='orange', dash='dot', width=2),
                        marker=dict(
                            color=sorted_data[color_param_tab3_value_col],
                            colorscale="Viridis",
                            symbol='square',
                            size=8
                        ),
                        name=f'Calibrated d18O - {identifier}'
                    ))

                fig_d18O.update_layout(
                    title=f'{identifier} - d18O for Species: {species}',
                    xaxis_title='X Axis',
                    yaxis_title='d18O (‰)',
                    legend_title='Data Type',
                    margin=dict(r=100, t=100),  # Reduced right margin
                    xaxis=dict(
                        # Show ~10 ticks across the axis
                        nticks=10,
                        tickmode='auto'
                    ),
                    legend=dict(
                        x=1.05,  # Move legend closer to chart
                        xanchor='left',
                        y=0.8,  # Keep consistent position
                        yanchor='middle'
                    )
                )
                # Invert y-axis so increasing d18O plots downward
                fig_d18O.update_yaxes(autorange='reversed')

                st.plotly_chart(fig_d18O, width='stretch', height=chart_height)

            # Display outliers header for each comment if detected
            if not species_data['Identifier 2'].isna().all():
                st.subheader(f'Outliers Detected for Species: {species}')
            
            # Get outliers data
            stat_outliers_only = statistical_outliers[statistical_outliers[species_col] == species]
            
            # Get original data for this species before any filtering
            species_data = subset_data_unfiltered[subset_data_unfiltered[species_col] == species]
            
            # Create masks for each range category
            d13c_outliers = species_data[
                (species_data['d 13C/12C  Mean'] < st.session_state.d13c_range[0]) |
                (species_data['d 13C/12C  Mean'] > st.session_state.d13c_range[1])
            ]
            
            d18o_outliers = species_data[
                (species_data['d 18O/16O  Mean'] < st.session_state.d18o_range[0]) |
                (species_data['d 18O/16O  Mean'] > st.session_state.d18o_range[1])
            ]
            
            signal_outliers = species_data[
                (species_data['1  Cycle Int  Samp  44'] < st.session_state.signal_range[0]) |
                (species_data['1  Cycle Int  Samp  44'] > st.session_state.signal_range[1])
            ]
            
            leak_outliers = species_data[
                (species_data['leak_rate'] < st.session_state.leak_range[0]) |
                (species_data['leak_rate'] > st.session_state.leak_range[1])
            ]
        
            # Create two columns for outlier information
            col1, col2 = st.columns(2)

            # Column 1: Isotope Outliers
            with col1:
                st.markdown("### ?? Isotope Outliers")
                st.markdown("---")
                
                # Statistical Outliers
                with st.expander("Statistical Outliers (Sigma-Based)", expanded=True):
                    if not stat_outliers_only.empty:
                        st.markdown("**Based on statistical deviation from the mean**")
                        styled_stats = stat_outliers_only[['Identifier 2', species_col, 'd 13C/12C  Mean', 'd 18O/16O  Mean']].copy()
                        styled_stats = styled_stats.rename(columns={
                            species_col: 'Species',
                            'd 13C/12C  Mean': 'd13C Value (‰)',
                            'd 18O/16O  Mean': 'd18O Value (‰)'
                        })
                        st.dataframe(styled_stats, width='stretch')
                    else:
                        st.info("No statistical outliers detected")

                # Î´13C Outliers
                with st.expander("d13C Range Outliers", expanded=True):
                    if not d13c_outliers.empty:
                        st.markdown(f"**Acceptable Range:** {st.session_state.d13c_range[0]:.2f} to {st.session_state.d13c_range[1]:.2f} ‰")
                        styled_d13c = d13c_outliers[['Identifier 2', species_col, 'd 13C/12C  Mean']].copy()
                        styled_d13c = styled_d13c.rename(columns={
                            species_col: 'Species','d 13C/12C  Mean': 'd13C Value (‰)'})
                        st.dataframe(styled_d13c, width='stretch')
                    else:
                        st.info("No d13C outliers detected")

                # Î´18O Outliers
                with st.expander("d18O Range Outliers", expanded=True):
                    if not d18o_outliers.empty:
                        st.markdown(f"**Acceptable Range:** {st.session_state.d18o_range[0]:.2f} to {st.session_state.d18o_range[1]:.2f} ‰")
                        styled_d18o = d18o_outliers[['Identifier 2', species_col, 'd 18O/16O  Mean']].copy()
                        styled_d18o = styled_d18o.rename(columns={
                            species_col: 'Species','d 18O/16O  Mean': 'd18O Value (‰)'})
                        st.dataframe(styled_d18o, width='stretch')
                    else:
                        st.info("No d18O outliers detected")

            # Column 2: Technical Outliers
            with col2:
                st.markdown("### ?? Technical Outliers")
                st.markdown("---")
                
                # Signal Intensity Outliers
                with st.expander("Signal Intensity Outliers", expanded=True):
                    if not signal_outliers.empty:
                        st.markdown(f"**Acceptable Range:** {st.session_state.signal_range[0]:.2f} to {st.session_state.signal_range[1]:.2f}")
                        styled_signal = signal_outliers[['Identifier 2', species_col, '1  Cycle Int  Samp  44']].copy()
                        styled_signal = styled_signal.rename(columns={
                            species_col: 'Species','1  Cycle Int  Samp  44': 'Signal Intensity'})
                        st.dataframe(styled_signal, width='stretch')
                    else:
                        st.info("No signal intensity outliers detected")
                
                # Leak Rate Outliers
                with st.expander("Leak Rate Outliers", expanded=True):
                    if not leak_outliers.empty:
                        st.markdown(f"**Acceptable Range:** {st.session_state.leak_range[0]:.2f} to {st.session_state.leak_range[1]:.2f}")
                        styled_leak = leak_outliers[['Identifier 2', species_col, 'leak_rate']].copy()
                        styled_leak = styled_leak.rename(columns={
                            species_col: 'Species','leak_rate': 'Leak Rate'})
                        st.dataframe(styled_leak, width='stretch')
                    else:
                        st.info("No leak rate outliers detected")

                # Collector Status (Partial / Full / Failed)
                with st.expander("Collector Status (Partial / Full / Failed)", expanded=True):
                    src_df = species_data_unfiltered if 'species_data_unfiltered' in locals() else species_data
                    status_series = src_df.get('Collector Status', pd.Series(False, index=src_df.index))
                    failed_samples = src_df[status_series == 'Failed Sample'].copy()
                    saturated_samples = src_df[status_series == 'Partially Saturated Collectors'].copy()
                    saturated_all = src_df[status_series == 'Fully Saturated Collectors'].copy()
                    if failed_samples.empty and saturated_samples.empty and saturated_all.empty:
                        st.info("No saturated collectors or failed samples detected")
                    else:
                        if not saturated_samples.empty:
                            st.markdown("**Partially Failed (Recovered Mean)**")
                            cols = ['Identifier 2', species_col, 'd 13C/12C  Mean', 'd 18O/16O  Mean',
                                    'd13C Cycles Excluded', 'd18O Cycles Excluded']
                            cols = [c for c in cols if c in saturated_samples.columns]
                            styled_sat = saturated_samples[cols].copy()
                            styled_sat = styled_sat.rename(columns={species_col: 'Species'})
                            st.dataframe(styled_sat, width='stretch')
                        if not saturated_all.empty:
                            st.markdown("**Failed Samples (Fully Saturated)**")
                            cols = ['Identifier 2', species_col, 'd 13C/12C  Mean', 'd 18O/16O  Mean',
                                    'Cycles Total', 'd13C Cycles Excluded', 'd18O Cycles Excluded']
                            cols = [c for c in cols if c in saturated_all.columns]
                            styled_sat_all = saturated_all[cols].copy()
                            styled_sat_all = styled_sat_all.rename(columns={species_col: 'Species'})
                            st.dataframe(styled_sat_all, width='stretch')
                        if not failed_samples.empty:
                            st.markdown("**Failed Samples (No Values)**")
                            cols = ['Identifier 2', species_col, 'd 13C/12C  Mean', 'd 18O/16O  Mean']
                            cols = [c for c in cols if c in failed_samples.columns]
                            styled_fail = failed_samples[cols].copy()
                            styled_fail = styled_fail.rename(columns={species_col: 'Species'})
                            st.dataframe(styled_fail, width='stretch')

            # with st.expander("Leak Rate Outliers", expanded=True):
            #     if not leak_outliers.empty:
            #         st.markdown(f"Range: {st.session_state.leak_range[0]:.2f} to {st.session_state.leak_range[1]:.2f}")
            #         st.dataframe(leak_outliers[['Identifier 2', species_col, 'leak_rate']])
            #     else:
            #         st.write("No leak rate outliers detected")

        #     # Check if the required columns are present
        #     calibrated_columns = ['d18O_calibrated', 'd13C_calibrated']
        #     calibration_status = all(col in data_to_plot.columns for col in calibrated_columns)

        #     # Determine the calibration status and set the filename
        #     if calibration_status:
        #         calibration_label = "Calibration performed"
        #         filename_suffix = "calibrated"
        #         label_color = "green"
        #         columns_to_export = [
        #             'Row', 'Method', 'Date', 'Time', 'Identifier 1', 'Identifier 2', 'Comment',
        #             'd 13C/12C  Mean', 'd 13C/12C  Std Dev', 'd 18O/16O  Mean', 'd 18O/16O  Std Dev',
        #             'd13C_calibrated', 'd18O_calibrated'
        #         ]
        #     else:
        #         calibration_label = "Calibration not performed"
        #         filename_suffix = "uncalibrated"
        #         label_color = "red"
        #         columns_to_export = [
        #             'Row', 'Method', 'Date', 'Time', 'Identifier 1', 'Identifier 2', 'Comment',
        #             'd 13C/12C  Mean', 'd 13C/12C  Std Dev', 'd 18O/16O  Mean', 'd 18O/16O  Std Dev'
        #         ]

        #     # Add a colored label next to the button indicating calibration status
        #     st.markdown(f'<span style="color:{label_color}; font-weight:bold;">{calibration_label}</span>',
        #                 unsafe_allow_html=True)


        #     # Function to convert the dataframe to an Excel file
        #     @st.cache_data
        #     def to_excel(df):
        #         output = io.BytesIO()
        #         with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        #             df.to_excel(writer, index=False, sheet_name='Data')
        #         return output.getvalue()

        #     # Filter the dataframe to include only the columns to export
        #     filtered_data = data_to_plot[columns_to_export]  # Assuming 'df' is your dataframe

        #     # Export the filtered data as Excel
        #     excel_data = to_excel(filtered_data)

        #     # Create the download button
        #     st.download_button(
        #         label="Download Data as Excel",
        #         data=excel_data,
        #         file_name=f'{identifier}_{comment}_{filename_suffix}_results.xlsx',
        #         mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        #     )
        # else:
        #     st.write("No chart displayed since 'All' was selected.")





if __name__ == '__main__':
    main()


