
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ..constants import (
    CYCLE1_SIGNAL_DIFF44_COL,
    CYCLE1_SIGNAL_REF44_COL,
    CYCLE1_SIGNAL_SAMP44_COL,
)

def extract_number(text):
    """Extract the first number from a string."""
    return _parse_numeric_token(text)

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
            if right.isdigit():
                # Treat comma as decimal when precision is >= 1 digit and not a clear thousands group.
                if len(right) in (1, 2):
                    num = left + "." + right
                elif len(right) == 3 and left.isdigit() and left not in ("0", "+0", "-0"):
                    num = left + right
                else:
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

    def _pick_mass_sample_column(cycles_df, mass_value):
        cols = [c for c in intensity_cols if c in cycles_df.columns and _extract_col_mass(c) == mass_value]
        if not cols:
            return None
        labeled_sample = []
        for c in cols:
            low = _normalize_column_key(c)
            if 'standard' in low:
                continue
            if 'sample' in low or 'samp' in low:
                labeled_sample.append(c)
        if labeled_sample:
            return labeled_sample[0]
        # Fallback: choose the column with the highest median intensity
        medians = []
        for c in cols:
            vals = _normalize_signal_intensity(cycles_df[c])
            medians.append((c, float(vals.median(skipna=True)) if vals.notna().any() else -np.inf))
        medians.sort(key=lambda t: t[1], reverse=True)
        return medians[0][0] if medians else None

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

            # Use Cycle 1 m/z44 sample/reference collectors to derive signal metrics.
            samp44_col = _pick_mass_sample_column(sample_cycles, 44)
            ref44_col = None
            def _pick_mass_reference_column(cycles_df, mass_value):
                cols = [c for c in intensity_cols if c in cycles_df.columns and _extract_col_mass(c) == mass_value]
                if not cols:
                    return None
                labeled_ref = []
                for c in cols:
                    low = _normalize_column_key(c)
                    if 'standard' in low or 'reference' in low or re.search(r'\bref\b|\bstd\b', low):
                        labeled_ref.append(c)
                if labeled_ref:
                    medians = []
                    for c in labeled_ref:
                        vals = _normalize_signal_intensity(cycles_df[c])
                        medians.append((c, float(vals.median(skipna=True)) if vals.notna().any() else np.inf))
                    medians.sort(key=lambda t: t[1])
                    return medians[0][0] if medians else None
                # Fallback: choose the column with the lowest median intensity.
                medians = []
                for c in cols:
                    vals = _normalize_signal_intensity(cycles_df[c])
                    medians.append((c, float(vals.median(skipna=True)) if vals.notna().any() else np.inf))
                medians.sort(key=lambda t: t[1])
                return medians[0][0] if medians else None

            ref44_col = _pick_mass_reference_column(sample_cycles, 44)
            if samp44_col is not None:
                cycle1 = sample_cycles[sample_cycles['_cycle_order'] == 1]
                if not cycle1.empty:
                    cycle1_val = _normalize_signal_intensity(cycle1[samp44_col]).iloc[0]
                else:
                    cycle1_val = _normalize_signal_intensity(sample_cycles[samp44_col]).dropna().iloc[0] if _normalize_signal_intensity(sample_cycles[samp44_col]).notna().any() else np.nan
                if pd.notna(cycle1_val):
                    pre_rows.at[sample_idx, CYCLE1_SIGNAL_SAMP44_COL] = float(cycle1_val)
            if ref44_col is not None:
                cycle1 = sample_cycles[sample_cycles['_cycle_order'] == 1]
                if not cycle1.empty:
                    cycle1_ref = _normalize_signal_intensity(cycle1[ref44_col]).iloc[0]
                else:
                    cycle1_ref = _normalize_signal_intensity(sample_cycles[ref44_col]).dropna().iloc[0] if _normalize_signal_intensity(sample_cycles[ref44_col]).notna().any() else np.nan
                if pd.notna(cycle1_ref):
                    pre_rows.at[sample_idx, CYCLE1_SIGNAL_REF44_COL] = float(cycle1_ref)
            sample_cycle1 = pd.to_numeric(pd.Series([pre_rows.at[sample_idx, CYCLE1_SIGNAL_SAMP44_COL] if CYCLE1_SIGNAL_SAMP44_COL in pre_rows.columns else np.nan]), errors='coerce').iloc[0]
            ref_cycle1 = pd.to_numeric(pd.Series([pre_rows.at[sample_idx, CYCLE1_SIGNAL_REF44_COL] if CYCLE1_SIGNAL_REF44_COL in pre_rows.columns else np.nan]), errors='coerce').iloc[0]
            if np.isfinite(sample_cycle1) and np.isfinite(ref_cycle1):
                pre_rows.at[sample_idx, CYCLE1_SIGNAL_DIFF44_COL] = float(sample_cycle1 - ref_cycle1)
        low_signal_failed = False
        pre_intensity_cols = [c for c in sample_intensity_cols if c in pre_rows.columns]
        if pre_intensity_cols:
            pre_vals = _normalize_signal_intensity(pre_rows.loc[sample_idx, pre_intensity_cols])
            pre_max = pre_vals.max(skipna=True)
            if pd.notna(pre_max) and pre_max < low_signal_threshold:
                low_signal_failed = True
        elif CYCLE1_SIGNAL_SAMP44_COL in pre_rows.columns:
            pre_val = _parse_numeric_token(pre_rows.at[sample_idx, CYCLE1_SIGNAL_SAMP44_COL])
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
        has_pre_d13 = bool(np.isfinite(pre_d13))
        has_pre_d18 = bool(np.isfinite(pre_d18))
        both_missing = (not has_pre_d13) and (not has_pre_d18)
        one_missing = has_pre_d13 ^ has_pre_d18
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
        elif low_signal_failed:
            pre_rows.at[sample_idx, 'Collector Status'] = 'Failed Sample'
            pre_rows.at[sample_idx, 'd 13C/12C  Mean'] = np.nan
            pre_rows.at[sample_idx, 'd 13C/12C  Std Dev'] = np.nan
            pre_rows.at[sample_idx, 'd 18O/16O  Mean'] = np.nan
            pre_rows.at[sample_idx, 'd 18O/16O  Std Dev'] = np.nan
        elif saturated_any and (has_pre_d13 or has_pre_d18):
            pre_rows.at[sample_idx, 'Collector Status'] = 'Partially Saturated Collectors'
        elif both_missing or one_missing:
            pre_rows.at[sample_idx, 'Collector Status'] = 'Failed Sample'
            pre_rows.at[sample_idx, 'd 13C/12C  Mean'] = np.nan
            pre_rows.at[sample_idx, 'd 13C/12C  Std Dev'] = np.nan
            pre_rows.at[sample_idx, 'd 18O/16O  Mean'] = np.nan
            pre_rows.at[sample_idx, 'd 18O/16O  Std Dev'] = np.nan

    # Keep non-cycle rows (rows without Cycle Number)
    other_rows = work[work['_cycle_order'].isna()].copy()
    if not other_rows.empty and 'Collector Status' not in other_rows.columns:
        other_rows['Collector Status'] = 'OK'

    result = pd.concat([pre_rows, other_rows], axis=0).sort_index()
    result = result.drop(columns=['_cycle_order', '_cycle_group'], errors='ignore')
    result = _ensure_cycle1_signal_difference_columns(result)
    return result

def _get_species_series(df):
    """Resolve per-row species labels with fallback for missing values.

    Order of preference per row:
    1) explicit ``Species`` value when non-empty
    2) parsed species from ``Label``
    3) parsed identifier from ``Label``
    4) ``Identifier 1``
    """
    if df is None:
        return pd.Series(dtype=object)

    fallback = pd.Series(index=df.index, dtype=object)
    if 'Label' in df.columns and not df['Label'].isna().all():
        label_parts = df['Label'].apply(_split_label_species)
        label_ident = label_parts.map(lambda v: v[0] if v else None)
        label_species = label_parts.map(lambda v: v[1] if v else None)
        fallback = label_species.where(
            label_species.notna() & (label_species.astype(str).str.strip() != ''),
            label_ident
        )

    if 'Identifier 1' in df.columns:
        id_fallback = df['Identifier 1']
        fallback = fallback.where(
            fallback.notna() & (fallback.astype(str).str.strip() != ''),
            id_fallback,
        )

    if 'Species' in df.columns:
        species = df['Species'].copy()
        return species.where(
            species.notna()
            & (species.astype(str).str.strip() != '')
            & (~species.astype(str).str.strip().str.lower().eq('nan')),
            fallback,
        )

    return fallback

def _normalize_column_key(name):
    """Normalize column labels for robust matching across unicode variants."""
    if not isinstance(name, str):
        return ''
    text = name.strip()
    # Normalize common unicode variants (CO2, \u00b5/\u03bc)
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

def _pick_cycle1_signal_columns(df):
    """Pick cycle-1 m/z44 sample/reference intensity columns from a dataframe."""
    if df is None:
        return None, None

    explicit_sample = CYCLE1_SIGNAL_SAMP44_COL if CYCLE1_SIGNAL_SAMP44_COL in df.columns else None
    explicit_ref = CYCLE1_SIGNAL_REF44_COL if CYCLE1_SIGNAL_REF44_COL in df.columns else None

    def _is_cycle1_mz44_col(col_name):
        low = _normalize_column_key(col_name)
        if low == '':
            return False
        has_mass44 = re.search(r'(?<!\d)(44|44\.0+)(?!\d)', low) is not None
        has_intensity = ('intensit' in low) or ('signal' in low) or bool(re.search(r'\bint\b', low))
        has_cycle1 = bool(re.search(r'(?:^|\s)1(?:\s|$)', low))
        if 'cycle' in low:
            has_cycle1 = has_cycle1 or bool(re.search(r'cycle\s*0*1(?:\D|$)', low))
        return has_mass44 and has_intensity and has_cycle1

    candidates = [c for c in df.columns if isinstance(c, str) and _is_cycle1_mz44_col(c)]
    if not candidates:
        return explicit_sample, explicit_ref

    sample_candidates = []
    ref_candidates = []
    unlabeled_candidates = []
    for col in candidates:
        low = _normalize_column_key(col)
        is_ref = ('reference' in low) or ('standard' in low) or bool(re.search(r'\bref\b|\bstd\b', low))
        is_sample = ('sample' in low) or ('samp' in low) or bool(re.search(r'\bsmp\b', low))
        if is_sample and not is_ref:
            sample_candidates.append(col)
        elif is_ref and not is_sample:
            ref_candidates.append(col)
        else:
            unlabeled_candidates.append(col)

    def _rank_by_median(cols):
        ranked = []
        for col in cols:
            vals = _normalize_signal_intensity(df[col]) if col in df.columns else pd.Series(dtype='float64')
            median_val = float(vals.median(skipna=True)) if vals.notna().any() else -np.inf
            ranked.append((col, median_val))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked

    ranked_samples = _rank_by_median(sample_candidates)
    ranked_refs = _rank_by_median(ref_candidates)
    ranked_unlabeled = _rank_by_median(unlabeled_candidates)
    ranked_all = _rank_by_median(candidates)

    sample_col = ranked_samples[0][0] if ranked_samples else explicit_sample
    ref_col = ranked_refs[-1][0] if ranked_refs else explicit_ref

    if sample_col is None:
        if ranked_unlabeled:
            sample_col = ranked_unlabeled[0][0]
        elif ranked_all:
            sample_col = ranked_all[0][0]

    if ref_col is None:
        remaining = [item for item in ranked_all if item[0] != sample_col]
        if ranked_refs:
            pick = [item for item in ranked_refs if item[0] != sample_col]
            if pick:
                ref_col = pick[-1][0]
        if ref_col is None and remaining:
            ref_col = remaining[-1][0]

    if sample_col == ref_col:
        ref_col = None

    if explicit_sample is not None:
        sample_col = explicit_sample
    if explicit_ref is not None:
        ref_col = explicit_ref

    return sample_col, ref_col

def _ensure_cycle1_signal_difference_columns(df):
    """Ensure canonical cycle-1 sample/ref intensity columns and their difference exist."""
    if df is None:
        return df

    sample_col, ref_col = _pick_cycle1_signal_columns(df)

    if CYCLE1_SIGNAL_SAMP44_COL in df.columns:
        df[CYCLE1_SIGNAL_SAMP44_COL] = _normalize_signal_intensity(df[CYCLE1_SIGNAL_SAMP44_COL])
    elif sample_col is not None and sample_col in df.columns:
        df[CYCLE1_SIGNAL_SAMP44_COL] = _normalize_signal_intensity(df[sample_col])
    else:
        df[CYCLE1_SIGNAL_SAMP44_COL] = np.nan

    if CYCLE1_SIGNAL_REF44_COL in df.columns:
        df[CYCLE1_SIGNAL_REF44_COL] = _normalize_signal_intensity(df[CYCLE1_SIGNAL_REF44_COL])
    elif ref_col is not None and ref_col in df.columns:
        df[CYCLE1_SIGNAL_REF44_COL] = _normalize_signal_intensity(df[ref_col])
    else:
        df[CYCLE1_SIGNAL_REF44_COL] = np.nan

    samp_vals = pd.to_numeric(df[CYCLE1_SIGNAL_SAMP44_COL], errors='coerce')
    ref_vals = pd.to_numeric(df[CYCLE1_SIGNAL_REF44_COL], errors='coerce')
    valid = np.isfinite(samp_vals) & np.isfinite(ref_vals)
    diff_vals = (samp_vals - ref_vals).where(valid)
    if CYCLE1_SIGNAL_DIFF44_COL in df.columns:
        existing_diff = pd.to_numeric(df[CYCLE1_SIGNAL_DIFF44_COL], errors='coerce')
        df[CYCLE1_SIGNAL_DIFF44_COL] = diff_vals.where(valid, existing_diff)
    else:
        df[CYCLE1_SIGNAL_DIFF44_COL] = diff_vals

    return df

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

    unit_tokens = {'\u2030', '%', 'ppm', 'ppb', 'mv', 'v', 'c'}
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
        if 'd13' in low or '\u03b413' in low:
            if 'mean' in low:
                rename_map[col] = 'd 13C/12C  Mean'
            elif 'sd' in low or 'std' in low:
                rename_map[col] = 'd 13C/12C  Std Dev'
        if 'd18' in low or '\u03b418' in low:
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
