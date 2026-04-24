
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ..constants import (
    CYCLE1_SIGNAL_DIFF44_COL,
    CYCLE1_SIGNAL_DIFF45_COL,
    CYCLE1_SIGNAL_DIFF46_COL,
    CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
    CYCLE1_SIGNAL_REF44_COL,
    CYCLE1_SIGNAL_REF45_COL,
    CYCLE1_SIGNAL_REF46_COL,
    CYCLE1_SIGNAL_SAMP44_COL,
    CYCLE1_SIGNAL_SAMP45_COL,
    CYCLE1_SIGNAL_SAMP46_COL,
)

def extract_number(text):
    """Extract the first number from a string."""
    return _parse_numeric_token(text)

def _parse_numeric_token(token):
    """Parse a numeric token with optional thousands/decimal separators."""
    if token is None or pd.isna(token):
        return None
    text = str(token).strip()
    # Normalize common unicode sign characters to preserve numeric polarity.
    text = (
        text.replace('\u2212', '-')
        .replace('\u2010', '-')
        .replace('\u2011', '-')
        .replace('\u2012', '-')
        .replace('\u2013', '-')
        .replace('\u2014', '-')
    )
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
        looks_derived_mismatch = bool('diff' in low or 'mismatch' in low or 'samp-ref' in low or 'ref-samp' in low)
        if looks_delta or looks_derived_mismatch:
            continue
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
    min_valid_cycles_for_saturation_recovery = 3
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
            sat_mask_44 = _build_saturation_mask(intensity_df, [44])

            # Derive cycle intensity metrics from the first available valid cycle pair.
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

            ordered_cycles = sample_cycles.sort_values('_cycle_order')
            sat_mask_45 = _build_saturation_mask(intensity_df, [45])
            sat_mask_46 = _build_saturation_mask(intensity_df, [46])

            def _resolve_mass_pair(mass_value, sat_mask_mass):
                selected_idx = None
                sample_val = np.nan
                ref_val = np.nan
                samp_col = _pick_mass_sample_column(sample_cycles, mass_value)
                ref_col = _pick_mass_reference_column(sample_cycles, mass_value)
                if samp_col is not None and ref_col is not None and not ordered_cycles.empty:
                    samp_vals = _normalize_signal_intensity(ordered_cycles[samp_col])
                    ref_vals = _normalize_signal_intensity(ordered_cycles[ref_col])
                    valid_pair_mask = samp_vals.notna() & ref_vals.notna()
                    if sat_mask_mass is not None:
                        sat_mass = sat_mask_mass.reindex(ordered_cycles.index).fillna(False)
                        valid_pair_mask = valid_pair_mask & ~sat_mass
                    if valid_pair_mask.any():
                        # Use the first valid cycle pair by request.
                        selected_idx = ordered_cycles.index[valid_pair_mask][0]
                    else:
                        finite_pair_mask = samp_vals.notna() & ref_vals.notna()
                        if finite_pair_mask.any():
                            selected_idx = ordered_cycles.index[finite_pair_mask][0]
                    if selected_idx is not None:
                        sample_val = float(samp_vals.loc[selected_idx])
                        ref_val = float(ref_vals.loc[selected_idx])
                    else:
                        finite_samp = samp_vals.dropna()
                        finite_ref = ref_vals.dropna()
                        if not finite_samp.empty:
                            sample_val = float(finite_samp.iloc[0])
                        if not finite_ref.empty:
                            ref_val = float(finite_ref.iloc[0])
                return sample_val, ref_val, selected_idx

            sample_signal, ref_signal, selected_pair_idx = _resolve_mass_pair(44, sat_mask_44)
            if np.isfinite(sample_signal) and np.isfinite(ref_signal):
                existing_diff = (
                    pd.to_numeric(
                        pd.Series(
                            [
                                pre_rows.at[sample_idx, CYCLE1_SIGNAL_DIFF44_COL]
                                if CYCLE1_SIGNAL_DIFF44_COL in pre_rows.columns
                                else np.nan
                            ]
                        ),
                        errors='coerce',
                    ).iloc[0]
                )
                if not np.isfinite(existing_diff):
                    diff_col = _pick_cycle1_diff44_column(ordered_cycles)
                    if diff_col:
                        diff_series = pd.to_numeric(_cycle1_diff44_as_samp_minus_ref(ordered_cycles, diff_col), errors='coerce')
                        if sat_mask_44 is not None:
                            sat44 = sat_mask_44.reindex(ordered_cycles.index).fillna(False)
                            diff_series = diff_series.where(~sat44)
                        if selected_pair_idx is not None:
                            selected_diff = pd.to_numeric(pd.Series([diff_series.get(selected_pair_idx)]), errors='coerce').iloc[0]
                            if np.isfinite(selected_diff):
                                existing_diff = float(selected_diff)
                        if not np.isfinite(existing_diff):
                            finite_diff = diff_series[np.isfinite(diff_series) & (np.abs(diff_series) > 1e-12)]
                            if not finite_diff.empty:
                                existing_diff = float(finite_diff.iloc[-1])
                computed_diff = float(sample_signal - ref_signal)
                if (
                    np.isfinite(existing_diff)
                    and abs(float(existing_diff)) > 1e-12
                    and abs(computed_diff) > 1e-12
                    and np.sign(computed_diff) != np.sign(float(existing_diff))
                ):
                    # Preserve signed Samp-Ref direction when cycle channels are unlabeled/ambiguous.
                    sample_signal, ref_signal = ref_signal, sample_signal
                    pre_rows.at[sample_idx, CYCLE1_SIGNAL_SAMP44_COL] = float(sample_signal)
                    pre_rows.at[sample_idx, CYCLE1_SIGNAL_REF44_COL] = float(ref_signal)
                    computed_diff = float(sample_signal - ref_signal)
                pre_rows.at[sample_idx, CYCLE1_SIGNAL_SAMP44_COL] = float(sample_signal)
                pre_rows.at[sample_idx, CYCLE1_SIGNAL_REF44_COL] = float(ref_signal)
                pre_rows.at[sample_idx, CYCLE1_SIGNAL_DIFF44_COL] = computed_diff

            samp45, ref45, _ = _resolve_mass_pair(45, sat_mask_45)
            if np.isfinite(samp45) and np.isfinite(ref45):
                pre_rows.at[sample_idx, CYCLE1_SIGNAL_SAMP45_COL] = float(samp45)
                pre_rows.at[sample_idx, CYCLE1_SIGNAL_REF45_COL] = float(ref45)
                pre_rows.at[sample_idx, CYCLE1_SIGNAL_DIFF45_COL] = float(samp45 - ref45)

            samp46, ref46, _ = _resolve_mass_pair(46, sat_mask_46)
            if np.isfinite(samp46) and np.isfinite(ref46):
                pre_rows.at[sample_idx, CYCLE1_SIGNAL_SAMP46_COL] = float(samp46)
                pre_rows.at[sample_idx, CYCLE1_SIGNAL_REF46_COL] = float(ref46)
                pre_rows.at[sample_idx, CYCLE1_SIGNAL_DIFF46_COL] = float(samp46 - ref46)
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
        d13_first_valid = np.nan
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
                    d13_filtered_cycles = d13_cycles.loc[d13_filtered.index].sort_values('_cycle_order')
                    if not d13_filtered_cycles.empty:
                        d13_first_valid = float(d13_filtered_cycles['_d13'].iloc[0])

        # d18O
        d18_mean = np.nan
        d18_first_valid = np.nan
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
                    d18_filtered_cycles = d18_cycles.loc[d18_filtered.index].sort_values('_cycle_order')
                    if not d18_filtered_cycles.empty:
                        d18_first_valid = float(d18_filtered_cycles['_d18'].iloc[0])

        # For partially saturated samples, persist first valid cycle values
        # to align stored deltas with cycle diagnostics/set-value defaults.
        d13_value_to_apply = d13_mean
        d18_value_to_apply = d18_mean
        if saturated_any:
            if np.isfinite(d13_first_valid):
                d13_value_to_apply = d13_first_valid
            if np.isfinite(d18_first_valid):
                d18_value_to_apply = d18_first_valid

        # Apply cycle-derived values when available; otherwise keep existing pre values.
        if np.isfinite(d13_value_to_apply):
            pre_rows.at[sample_idx, 'd 13C/12C  Mean'] = float(d13_value_to_apply)
        if np.isfinite(d13_std):
            pre_rows.at[sample_idx, 'd 13C/12C  Std Dev'] = d13_std
        if np.isfinite(d18_value_to_apply):
            pre_rows.at[sample_idx, 'd 18O/16O  Mean'] = float(d18_value_to_apply)
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
        insufficient_valid_cycles = (
            (d13_has_cycles and d13_used < min_valid_cycles_for_saturation_recovery) or
            (d18_has_cycles and d18_used < min_valid_cycles_for_saturation_recovery)
        )
        fully_saturated = (
            has_cycle_intensity and
            saturated_any and
            (d13_has_cycles or d18_has_cycles) and
            (
                (d13_used == 0 and d18_used == 0 and (d13_excl > 0 or d18_excl > 0))
                or insufficient_valid_cycles
            )
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

def _is_cycle1_diff44_like_column(col_name):
    if not isinstance(col_name, str):
        return False
    low = _normalize_column_key(col_name)
    if low == "":
        return False
    has_mass44 = re.search(r'(?<!\d)(44|44\.0+)(?!\d)', low) is not None
    has_cycle1 = bool(re.search(r'(?:^|\s)1(?:\s|$)', low))
    if "cycle" in low:
        has_cycle1 = has_cycle1 or bool(re.search(r'cycle\s*0*1(?:\D|$)', low))
    if not has_mass44 or not has_cycle1:
        return False
    return bool("diff" in low or "mismatch" in low or "samp-ref" in low or "ref-samp" in low)


def _is_ref_minus_sample_label(col_name):
    if not isinstance(col_name, str):
        return False
    low = _normalize_column_key(col_name)
    if "samp-ref" in low or "sample-reference" in low:
        return False
    if "ref-samp" in low or "reference-sample" in low:
        return True

    samp_pos_candidates = [pos for pos in (low.find("samp"), low.find("sample")) if pos >= 0]
    ref_pos_candidates = [pos for pos in (low.find("ref"), low.find("reference")) if pos >= 0]
    if not samp_pos_candidates or not ref_pos_candidates:
        return False
    return min(ref_pos_candidates) < min(samp_pos_candidates)


def _pick_cycle1_diff44_column(df):
    if df is None:
        return None
    if CYCLE1_SIGNAL_DIFF44_COL in df.columns:
        return CYCLE1_SIGNAL_DIFF44_COL
    candidates = [col for col in df.columns if _is_cycle1_diff44_like_column(col)]
    if not candidates:
        return None
    scored: list[tuple[str, int]] = []
    for col in candidates:
        vals = pd.to_numeric(df[col], errors='coerce')
        nonzero = vals[np.isfinite(vals) & (np.abs(vals) > 1e-12)]
        scored.append((col, int(nonzero.shape[0])))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[0][0] if scored else None


def _cycle1_diff44_as_samp_minus_ref(df, col_name):
    if df is None or not col_name or col_name not in df.columns:
        return pd.Series(np.nan, index=(df.index if isinstance(df, pd.DataFrame) else pd.Index([])), dtype=float)
    values = pd.to_numeric(df[col_name], errors='coerce')
    if _is_ref_minus_sample_label(col_name):
        return -values
    return values


def _orient_cycle1_sample_ref_by_existing_diff(
    df,
    sample_col,
    ref_col,
    diff_col=None,
):
    """Orient ambiguous sample/ref picks using existing signed Samp-Ref values."""
    if df is None:
        return sample_col, ref_col
    if not sample_col or not ref_col or sample_col == ref_col:
        return sample_col, ref_col
    if sample_col not in df.columns or ref_col not in df.columns:
        return sample_col, ref_col
    resolved_diff_col = str(diff_col).strip() if isinstance(diff_col, str) and str(diff_col).strip() != "" else _pick_cycle1_diff44_column(df)
    if not resolved_diff_col or resolved_diff_col not in df.columns:
        return sample_col, ref_col

    sample_vals = pd.to_numeric(_normalize_signal_intensity(df[sample_col]), errors='coerce')
    ref_vals = pd.to_numeric(_normalize_signal_intensity(df[ref_col]), errors='coerce')
    existing_diff = pd.to_numeric(_cycle1_diff44_as_samp_minus_ref(df, resolved_diff_col), errors='coerce')
    computed_diff = sample_vals - ref_vals
    valid = (
        np.isfinite(sample_vals)
        & np.isfinite(ref_vals)
        & np.isfinite(existing_diff)
        & (np.abs(computed_diff) > 1e-12)
        & (np.abs(existing_diff) > 1e-12)
    )
    if not bool(valid.any()):
        return sample_col, ref_col

    existing_sign = np.sign(existing_diff[valid])
    direct_match_count = int((np.sign(computed_diff[valid]) == existing_sign).sum())
    swapped_match_count = int((np.sign(-computed_diff[valid]) == existing_sign).sum())
    if swapped_match_count > direct_match_count:
        return ref_col, sample_col
    return sample_col, ref_col

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
        if 'diff' in low or 'mismatch' in low or 'samp-ref' in low or 'ref-samp' in low:
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
    if explicit_sample is None and explicit_ref is None:
        sample_col, ref_col = _orient_cycle1_sample_ref_by_existing_diff(df, sample_col, ref_col)

    return sample_col, ref_col

def _ensure_cycle1_signal_difference_columns(df):
    """Ensure canonical cycle-1 sample/ref intensity columns and their difference exist."""
    if df is None:
        return df

    sample_col, ref_col = _pick_cycle1_signal_columns(df)

    def _ensure_signal_column(target_col, fallback_col=None):
        if target_col in df.columns:
            df[target_col] = _normalize_signal_intensity(df[target_col])
            return
        if fallback_col is not None and fallback_col in df.columns:
            df[target_col] = _normalize_signal_intensity(df[fallback_col])
            return
        df[target_col] = np.nan

    _ensure_signal_column(CYCLE1_SIGNAL_SAMP44_COL, sample_col)
    _ensure_signal_column(CYCLE1_SIGNAL_REF44_COL, ref_col)

    def _collect_mass_signal_candidates(mass_value):
        candidates = []
        for col in df.columns:
            if not isinstance(col, str):
                continue
            low = _normalize_column_key(col)
            if low == "":
                continue
            if re.search(rf'(?<!\d){int(mass_value)}(?:\.0+)?(?!\d)', low) is None:
                continue
            looks_delta = bool(
                'delta' in low
                or re.search(r'\bd4[5-6]co2\b', low)
                or low.startswith('d45')
                or low.startswith('d46')
            )
            looks_derived = bool('diff' in low or 'mismatch' in low or 'samp-ref' in low or 'ref-samp' in low)
            if looks_delta or looks_derived:
                continue
            candidates.append(col)
        return candidates

    def _resolve_mass_pair_columns(mass_value):
        sample_target = CYCLE1_SIGNAL_SAMP45_COL if int(mass_value) == 45 else CYCLE1_SIGNAL_SAMP46_COL
        ref_target = CYCLE1_SIGNAL_REF45_COL if int(mass_value) == 45 else CYCLE1_SIGNAL_REF46_COL
        if sample_target in df.columns and ref_target in df.columns:
            return sample_target, ref_target
        candidates = [c for c in _collect_mass_signal_candidates(mass_value) if c not in (sample_target, ref_target)]
        if not candidates:
            return None, None
        labeled_sample = []
        labeled_ref = []
        unlabeled = []
        for col in candidates:
            low = _normalize_column_key(col)
            is_ref = ('reference' in low) or ('standard' in low) or bool(re.search(r'\bref\b|\bstd\b', low))
            is_sample = ('sample' in low) or ('samp' in low) or bool(re.search(r'\bsmp\b', low))
            if is_sample and not is_ref:
                labeled_sample.append(col)
            elif is_ref and not is_sample:
                labeled_ref.append(col)
            else:
                unlabeled.append(col)
        sample_source = labeled_sample[0] if labeled_sample else None
        ref_source = labeled_ref[0] if labeled_ref else None
        remaining = [c for c in candidates if c not in {sample_source, ref_source}]
        if sample_source is None and remaining:
            sample_source = remaining[0]
            remaining = [c for c in remaining if c != sample_source]
        if ref_source is None and remaining:
            ref_source = remaining[0]
        if sample_source is None or ref_source is None:
            return sample_source, ref_source

        # If roles are unlabeled/ambiguous, align mass-45/46 role ordering to mass-44 ordering.
        ambig = (
            sample_source in unlabeled
            and ref_source in unlabeled
            and CYCLE1_SIGNAL_SAMP44_COL in df.columns
            and CYCLE1_SIGNAL_REF44_COL in df.columns
        )
        if ambig:
            s44 = pd.to_numeric(df[CYCLE1_SIGNAL_SAMP44_COL], errors='coerce')
            r44 = pd.to_numeric(df[CYCLE1_SIGNAL_REF44_COL], errors='coerce')
            anchor_order = s44 - r44
            a = pd.to_numeric(_normalize_signal_intensity(df[sample_source]), errors='coerce')
            b = pd.to_numeric(_normalize_signal_intensity(df[ref_source]), errors='coerce')
            valid = (
                np.isfinite(anchor_order)
                & np.isfinite(a)
                & np.isfinite(b)
                & (np.abs(anchor_order) > 1e-12)
                & (np.abs(a - b) > 1e-12)
            )
            if bool(valid.any()):
                score_ab = int((np.sign(a[valid] - b[valid]) == np.sign(anchor_order[valid])).sum())
                score_ba = int((np.sign(b[valid] - a[valid]) == np.sign(anchor_order[valid])).sum())
                if score_ba > score_ab:
                    sample_source, ref_source = ref_source, sample_source
            else:
                # Median fallback: follow the dominant mass-44 sample/ref ordering.
                anchor_med = float(np.nanmedian(anchor_order)) if anchor_order.notna().any() else np.nan
                a_med = float(np.nanmedian(a)) if a.notna().any() else np.nan
                b_med = float(np.nanmedian(b)) if b.notna().any() else np.nan
                if np.isfinite(anchor_med) and np.isfinite(a_med) and np.isfinite(b_med):
                    if anchor_med < 0 and a_med > b_med:
                        sample_source, ref_source = ref_source, sample_source
                    if anchor_med > 0 and a_med < b_med:
                        sample_source, ref_source = ref_source, sample_source
        return sample_source, ref_source

    def _ensure_mass_pair_columns(mass_value):
        sample_target = CYCLE1_SIGNAL_SAMP45_COL if int(mass_value) == 45 else CYCLE1_SIGNAL_SAMP46_COL
        ref_target = CYCLE1_SIGNAL_REF45_COL if int(mass_value) == 45 else CYCLE1_SIGNAL_REF46_COL
        sample_source, ref_source = _resolve_mass_pair_columns(mass_value)
        _ensure_signal_column(sample_target, sample_source)
        _ensure_signal_column(ref_target, ref_source)

    _ensure_mass_pair_columns(45)
    _ensure_mass_pair_columns(46)

    def _ensure_diff_column(sample_signal_col, ref_signal_col, diff_col):
        if sample_signal_col not in df.columns or ref_signal_col not in df.columns:
            return
        samp_vals = pd.to_numeric(df[sample_signal_col], errors='coerce')
        ref_vals = pd.to_numeric(df[ref_signal_col], errors='coerce')
        valid = np.isfinite(samp_vals) & np.isfinite(ref_vals)
        diff_vals = (samp_vals - ref_vals).where(valid)
        if diff_col in df.columns:
            existing_diff = pd.to_numeric(df[diff_col], errors='coerce')
            df[diff_col] = diff_vals.where(valid, existing_diff)
        else:
            df[diff_col] = diff_vals

    _ensure_diff_column(CYCLE1_SIGNAL_SAMP44_COL, CYCLE1_SIGNAL_REF44_COL, CYCLE1_SIGNAL_DIFF44_COL)
    _ensure_diff_column(CYCLE1_SIGNAL_SAMP45_COL, CYCLE1_SIGNAL_REF45_COL, CYCLE1_SIGNAL_DIFF45_COL)
    _ensure_diff_column(CYCLE1_SIGNAL_SAMP46_COL, CYCLE1_SIGNAL_REF46_COL, CYCLE1_SIGNAL_DIFF46_COL)

    _ensure_cycle1_pressure_weighted_mismatch_column(df)
    return df


def _ensure_cycle1_pressure_weighted_mismatch_column(df):
    """Ensure pressure-weighted mismatch column exists for cycle-1 m/z44.

    Formula:
    10 * (Samp-Ref) / Ref * (Samp / median(Samp))
    Fallback (when sample median is unavailable): 10 * (Samp-Ref) / Ref
    """
    if df is None:
        return df

    samp_vals = pd.to_numeric(df.get(CYCLE1_SIGNAL_SAMP44_COL), errors='coerce')
    ref_vals = pd.to_numeric(df.get(CYCLE1_SIGNAL_REF44_COL), errors='coerce')
    diff_vals = pd.to_numeric(df.get(CYCLE1_SIGNAL_DIFF44_COL), errors='coerce')
    if diff_vals.isna().all():
        valid_samp_ref = np.isfinite(samp_vals) & np.isfinite(ref_vals)
        diff_vals = (samp_vals - ref_vals).where(valid_samp_ref)

    valid_ref = np.isfinite(ref_vals) & (np.abs(ref_vals) > 1e-12)
    valid_diff = np.isfinite(diff_vals)
    with np.errstate(divide='ignore', invalid='ignore'):
        mismatch_10v = (diff_vals / ref_vals) * 10.0
    mismatch_10v = mismatch_10v.where(valid_ref & valid_diff)

    weighted = mismatch_10v
    finite_sample = samp_vals[np.isfinite(samp_vals)]
    if not finite_sample.empty:
        sample_ref = float(finite_sample.median())
        if np.isfinite(sample_ref) and abs(sample_ref) > 1e-12:
            with np.errstate(divide='ignore', invalid='ignore'):
                sample_scale = samp_vals / sample_ref
            weighted = (mismatch_10v * sample_scale).where(np.isfinite(mismatch_10v) & np.isfinite(sample_scale))

    if CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL in df.columns:
        existing = pd.to_numeric(df[CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL], errors='coerce')
        df[CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL] = weighted.where(np.isfinite(weighted), existing)
    else:
        df[CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL] = weighted
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
