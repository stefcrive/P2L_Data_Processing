
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .shared.dataframe import (
    _apply_cycle_averages,
    _coalesce_duplicate_columns,
    _ensure_cycle1_signal_difference_columns,
    _extract_numeric,
    _find_column,
    _normalize_signal_intensity,
    _parse_new_table_layout,
    _split_label_species,
    _standardize_isotope_columns,
    extract_info_values,
)
from .constants import CYCLE1_SIGNAL_SAMP44_COL

from .shared.plotting import _compose_label_series

def _safe_filename_fragment(value):
    """Return a filesystem-safe filename fragment."""
    text = str(value).strip()
    if text == "":
        return "session"
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = text.strip("._")
    return text if text else "session"


def _file_stem(value):
    """Return filename stem from any slash style path."""
    text = str(value or "").replace("\\", "/").strip()
    if text == "":
        return ""
    basename = text.split("/")[-1]
    dot = basename.rfind(".")
    if dot > 0:
        return basename[:dot]
    return basename


def _build_session_name_from_source_files(source_files):
    """Build a stable human-readable session name from uploaded workbook names."""
    names = []
    for item in source_files or []:
        spec = _normalize_upload_spec(item)
        if spec is None:
            continue
        name = str(spec.get("name", "")).strip()
        if name:
            names.append(name)

    if len(names) == 0:
        return "irms_data"
    if len(names) == 1:
        return _safe_filename_fragment(_file_stem(names[0]))

    stems = [_safe_filename_fragment(_file_stem(name)) for name in names]
    ordered = sorted(stems, key=lambda value: value.lower())
    candidate = f"{ordered[0]}_plus_{len(stems) - 1}"
    return candidate[:96].strip("._-") or "irms_data"

def _numeric_or_none(value):
    """Convert numeric-like values to float; return None for NaN/invalid."""
    num = pd.to_numeric(pd.Series([value]), errors='coerce').iloc[0]
    return float(num) if pd.notna(num) else None

def _normalize_upload_spec(upload_item):
    """Normalize uploaded file metadata to {'name','size','md5','raw_name'}."""
    raw_name = upload_item
    raw_size = None
    raw_md5 = None
    if isinstance(upload_item, dict):
        raw_name = upload_item.get("name")
        raw_size = upload_item.get("size")
        raw_md5 = upload_item.get("md5")

    raw_name_text = "" if raw_name is None else str(raw_name).strip()
    if raw_name_text == "":
        return None

    size_val = None
    try:
        if raw_size is not None and not pd.isna(raw_size):
            size_val = int(raw_size)
            if size_val < 0:
                size_val = None
    except Exception:
        size_val = None

    md5_val = None
    if raw_md5 is not None:
        text = str(raw_md5).strip().lower()
        if re.fullmatch(r"[0-9a-f]{32}", text):
            md5_val = text

    return {
        "raw_name": raw_name_text,
        "name": Path(raw_name_text).name,
        "size": size_val,
        "md5": md5_val,
    }

def _build_uploaded_file_spec(uploaded_file):
    """Build a normalized file metadata dict for an uploaded workbook."""
    raw_name = str(getattr(uploaded_file, "name", "")).strip()
    if raw_name == "":
        raw_name = "uploaded_file"

    file_size = getattr(uploaded_file, "size", None)
    try:
        file_size = int(file_size) if file_size is not None else None
    except Exception:
        file_size = None

    file_md5 = None
    try:
        if hasattr(uploaded_file, "getvalue"):
            file_md5 = hashlib.md5(uploaded_file.getvalue()).hexdigest().lower()
    except Exception:
        file_md5 = None

    return {
        "name": raw_name,
        "size": file_size,
        "md5": file_md5,
    }

def _upload_spec_signature(upload_item):
    """Return a stable signature tuple for upload de-duplication."""
    spec = _normalize_upload_spec(upload_item)
    if spec is None:
        return None
    return (
        str(spec.get("name", "")).strip().lower(),
        spec.get("size"),
        str(spec.get("md5") or "").strip().lower(),
    )

def _next_append_index(existing_index, count):
    """Build non-overlapping index labels for appended rows."""
    if int(count) <= 0:
        return pd.Index([])

    try:
        numeric = pd.to_numeric(pd.Series(existing_index), errors="coerce")
        if len(existing_index) == 0:
            return pd.Index(range(int(count)))
        if bool(numeric.notna().all()):
            start = int(np.nanmax(numeric.to_numpy(dtype=float))) + 1
            return pd.Index(range(start, start + int(count)))
    except Exception:
        pass

    used = {str(v) for v in existing_index}
    labels = []
    cursor = 1
    while len(labels) < int(count):
        token = f"append_{cursor}"
        if token not in used:
            labels.append(token)
            used.add(token)
        cursor += 1
    return pd.Index(labels, dtype=object)

def _append_rows_preserve_existing_index(existing_df, append_df):
    """Append rows while keeping existing index labels unchanged."""
    if append_df is None:
        return existing_df.copy() if isinstance(existing_df, pd.DataFrame) else None
    if existing_df is None:
        return append_df.copy()
    if append_df.empty:
        return existing_df.copy()
    if existing_df.empty:
        return append_df.copy()

    combined = existing_df.copy()
    append_block = append_df.copy()
    append_block.index = _next_append_index(combined.index, len(append_block))
    return pd.concat([combined, append_block], axis=0, sort=False)

def _append_cycles_source(existing_cycles_df, append_cycles_df):
    """Append cycle-source rows for diagnostics."""
    if append_cycles_df is None:
        return existing_cycles_df
    if existing_cycles_df is None:
        return append_cycles_df.copy()
    if append_cycles_df.empty:
        return existing_cycles_df
    if existing_cycles_df.empty:
        return append_cycles_df.copy()
    return pd.concat([existing_cycles_df, append_cycles_df], ignore_index=True, sort=False)

def _load_uploaded_workbooks(uploaded_files):
    """Read and normalize uploaded Excel workbooks into analysis-ready dataframes."""
    dfs = []
    dfs_cycles_source = []
    loaded_file_specs = []
    load_errors = []

    for uploaded_file in (uploaded_files or []):
        try:
            uploaded_file.seek(0)
        except Exception:
            pass

        try:
            raw = pd.read_excel(uploaded_file, header=None, engine='openpyxl')
            df = _parse_new_table_layout(raw)
            if df is None:
                uploaded_file.seek(0)
                df = pd.read_excel(uploaded_file, engine='openpyxl')
        except Exception:
            try:
                uploaded_file.seek(0)
                raw = pd.read_excel(uploaded_file, header=None, engine='xlrd')
                df = _parse_new_table_layout(raw)
                if df is None:
                    uploaded_file.seek(0)
                    df = pd.read_excel(uploaded_file, engine='xlrd')
            except Exception as exc:
                load_errors.append(f"Failed to read Excel file '{uploaded_file.name}': {exc}")
                continue

        df = _coalesce_duplicate_columns(df)
        df = df.convert_dtypes()
        df.reset_index(drop=True, inplace=True)
        df = df.map(lambda x: None if pd.isna(x) else x)
        df['Excel File'] = uploaded_file.name

        df = _standardize_isotope_columns(df)
        df = _coalesce_duplicate_columns(df)
        for col in ['d 13C/12C  Mean', 'd 13C/12C  Std Dev', 'd 18O/16O  Mean', 'd 18O/16O  Std Dev']:
            if col not in df.columns:
                df[col] = np.nan

        if 'Date' in df.columns:
            df['Date'] = pd.to_datetime(df['Date'], format='%m/%d/%y', errors='coerce')
        elif 'Start Time' in df.columns:
            df['Date'] = pd.to_datetime(df['Start Time'], errors='coerce')

        if 'Date' in df.columns:
            df['Date_ordinal'] = pd.to_numeric(
                df['Date'].map(lambda x: x.toordinal() if pd.notnull(x) else None)
            )

        original_columns = df.columns.tolist()

        if 'Information' in df.columns:
            df = extract_info_values(df)

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

        if CYCLE1_SIGNAL_SAMP44_COL in df.columns:
            df[CYCLE1_SIGNAL_SAMP44_COL] = _normalize_signal_intensity(df[CYCLE1_SIGNAL_SAMP44_COL])
        else:
            intensity_candidates = [
                'Pressure Adjust Result Intensity',
                'Pressure Adjust Initial Intensity',
                'Initial Intensity from \u00b5-Volume',
                'Initial Intensity from \u03bc-Volume',
            ]
            for cand in intensity_candidates:
                col = _find_column(df, cand)
                if col:
                    df[CYCLE1_SIGNAL_SAMP44_COL] = _normalize_signal_intensity(df[col])
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

        if 'Identifier 1' in df.columns:
            df['Label'] = _compose_label_series(
                df['Identifier 1'],
                df.get('Species', pd.Series(index=df.index, dtype=object))
            )

        for col in ['leak_rate', 'p_no_acid', 'total_co2', 'p_gases', CYCLE1_SIGNAL_SAMP44_COL, 'Line']:
            if col not in df.columns:
                df[col] = np.nan

        dfs_cycles_source.append(df.copy())
        df = _apply_cycle_averages(df)
        df = _ensure_cycle1_signal_difference_columns(df)

        for col in original_columns:
            if col not in df.columns:
                df[col] = None

        dfs.append(df)
        loaded_file_specs.append(_build_uploaded_file_spec(uploaded_file))

    if not dfs:
        return None, None, loaded_file_specs, load_errors

    combined_df = pd.concat(dfs, ignore_index=True, sort=False) if len(dfs) > 1 else dfs[0]
    combined_cycles_source = (
        pd.concat(dfs_cycles_source, ignore_index=True, sort=False)
        if len(dfs_cycles_source) > 1 else
        (dfs_cycles_source[0] if dfs_cycles_source else None)
    )
    return combined_df, combined_cycles_source, loaded_file_specs, load_errors
