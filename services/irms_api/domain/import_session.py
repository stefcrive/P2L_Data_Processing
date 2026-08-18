
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .contracts import (
    ImportFieldParsingRule,
    ImportFilePreview,
    ImportParsingConfig,
    ImportPreviewResponse,
    ImportWorkbookParsingConfig,
)
from .shared.dataframe import (
    _apply_cycle_averages,
    _coalesce_duplicate_columns,
    _ensure_cycle1_signal_difference_columns,
    _extract_numeric,
    _find_column,
    _normalize_signal_intensity,
    _parse_new_table_layout,
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


def _read_uploaded_workbook_frame(uploaded_file) -> tuple[pd.DataFrame, str]:
    """Read one workbook and identify the stable software layout."""
    filename = str(getattr(uploaded_file, "name", "uploaded workbook"))
    extension = Path(filename).suffix.lower()
    engine = "xlrd" if extension == ".xls" else "openpyxl"
    try:
        uploaded_file.seek(0)
    except Exception:
        pass
    raw = pd.read_excel(uploaded_file, header=None, engine=engine)
    parsed = _parse_new_table_layout(raw)
    software = "qtegra" if parsed is not None else "generic"
    if parsed is None:
        uploaded_file.seek(0)
        parsed = pd.read_excel(uploaded_file, engine=engine)
    parsed = _coalesce_duplicate_columns(parsed)
    parsed = parsed.convert_dtypes()
    parsed.reset_index(drop=True, inplace=True)
    parsed = parsed.map(lambda value: None if pd.isna(value) else value)
    normalized_columns = {str(column).strip().lower() for column in parsed.columns}
    has_isodat_identifiers = {"identifier 1", "identifier 2"}.issubset(normalized_columns)
    has_isodat_cycles = any(str(column).strip().lower().startswith("rintensity 44") for column in parsed.columns)
    if software == "generic" and has_isodat_identifiers and has_isodat_cycles:
        software = "isodat"
    return parsed, software


def _direct_rule(source_column: str | None) -> ImportFieldParsingRule:
    return ImportFieldParsingRule(source_column=source_column, mode="direct")


def _split_rule(source_column: str, part_index: int) -> ImportFieldParsingRule:
    return ImportFieldParsingRule(
        source_column=source_column,
        mode="split",
        delimiter=" - ",
        part_index=part_index,
    )


def _first_available_column(df: pd.DataFrame, *candidates: str) -> str | None:
    return _find_column(df, *candidates)


def _suggest_import_parsing_config(
    df: pd.DataFrame,
    *,
    file_index: int,
    file_name: str,
    software: str,
) -> ImportWorkbookParsingConfig:
    """Return an editable identity-field mapping for a detected workbook layout."""
    label_col = _first_available_column(df, "Label")
    identifier1_col = _first_available_column(df, "Identifier 1")
    identifier2_col = _first_available_column(df, "Identifier 2")
    species_col = _first_available_column(df, "Species")
    comment_col = _first_available_column(df, "Comment")
    sample_col = _first_available_column(df, "Sample")
    run_id_col = _first_available_column(df, "Run ID")
    index_col = _first_available_column(df, "Index", "Row")

    if software == "qtegra" and label_col:
        identifier1 = _split_rule(label_col, 0)
        species = _split_rule(label_col, 1)
        identifier2 = _direct_rule(comment_col or identifier2_col or run_id_col or index_col)
    elif software == "isodat":
        identifier1 = _direct_rule(identifier1_col or label_col or sample_col)
        identifier2 = _direct_rule(identifier2_col or comment_col or run_id_col or index_col)
        species = _direct_rule(species_col or comment_col or identifier1_col)
    else:
        identifier1 = (
            _direct_rule(identifier1_col)
            if identifier1_col
            else (_split_rule(label_col, 0) if label_col else _direct_rule(sample_col))
        )
        identifier2 = _direct_rule(identifier2_col or comment_col or run_id_col or index_col)
        species = (
            _direct_rule(species_col)
            if species_col
            else (_split_rule(label_col, 1) if label_col else _direct_rule(identifier1_col or sample_col))
        )

    return ImportWorkbookParsingConfig(
        file_index=file_index,
        file_name=file_name,
        software=software,
        identifier1=identifier1,
        identifier2=identifier2,
        species=species,
    )


def _clean_import_field_value(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip()
    return None if text == "" or text.lower() == "nan" else text


def _extract_import_field(
    df: pd.DataFrame,
    rule: ImportFieldParsingRule,
    *,
    target_name: str,
) -> pd.Series:
    source_name = str(rule.source_column or "").strip()
    if not source_name:
        return pd.Series(None, index=df.index, dtype=object)
    source_col = _find_column(df, source_name)
    if source_col is None:
        raise ValueError(f"{target_name}: source column '{source_name}' was not found")
    source = df[source_col]
    if rule.mode == "direct":
        return source.map(_clean_import_field_value)
    if rule.mode == "split":
        delimiter = str(rule.delimiter)
        if delimiter == "":
            raise ValueError(f"{target_name}: split delimiter cannot be empty")

        def _split_value(value: Any) -> str | None:
            text = _clean_import_field_value(value)
            if text is None:
                return None
            parts = text.split(delimiter)
            index = int(rule.part_index)
            if index < 0:
                index += len(parts)
            if index < 0 or index >= len(parts):
                return None
            return _clean_import_field_value(parts[index])

        return source.map(_split_value)

    pattern_text = str(rule.regex_pattern or "")
    if pattern_text == "":
        raise ValueError(f"{target_name}: regular expression cannot be empty")
    try:
        pattern = re.compile(pattern_text)
    except re.error as exc:
        raise ValueError(f"{target_name}: invalid regular expression ({exc})") from exc

    def _regex_value(value: Any) -> str | None:
        text = _clean_import_field_value(value)
        if text is None:
            return None
        match = pattern.search(text)
        if match is None:
            return None
        try:
            return _clean_import_field_value(match.group(rule.regex_group))
        except (IndexError, KeyError) as exc:
            raise ValueError(
                f"{target_name}: regex group '{rule.regex_group}' was not found"
            ) from exc

    return source.map(_regex_value)


def _apply_import_parsing_config(
    df: pd.DataFrame,
    config: ImportWorkbookParsingConfig,
) -> pd.DataFrame:
    """Populate canonical identity columns without mutating source columns."""
    work = df.copy()
    for raw_column, source_column in (
        ("Raw Identifier 1", "Identifier 1"),
        ("Raw Label", "Label"),
        ("Raw Comment", "Comment"),
    ):
        source = _find_column(work, source_column)
        work[raw_column] = work[source].copy() if source is not None else None
    parsed = {
        "Identifier 1": _extract_import_field(work, config.identifier1, target_name="Identifier 1"),
        "Identifier 2": _extract_import_field(work, config.identifier2, target_name="Identifier 2"),
        "Species": _extract_import_field(work, config.species, target_name="Species"),
    }
    for column, values in parsed.items():
        work[column] = values
    return work


def _resolve_workbook_parsing_config(
    parsing_config: ImportParsingConfig | None,
    suggested_config: ImportWorkbookParsingConfig,
) -> ImportWorkbookParsingConfig:
    if parsing_config is None:
        return suggested_config
    for config in parsing_config.files:
        if config.file_index == suggested_config.file_index:
            return config
    for config in parsing_config.files:
        if str(config.file_name).strip().lower() == str(suggested_config.file_name).strip().lower():
            return config
    return suggested_config


def _json_preview_value(value: Any) -> Any:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _preview_sample_rows(
    df: pd.DataFrame,
    config: ImportWorkbookParsingConfig,
    limit: int = 8,
) -> list[dict[str, Any]]:
    source_columns = [
        str(rule.source_column or "").strip()
        for rule in (config.identifier1, config.identifier2, config.species)
        if str(rule.source_column or "").strip()
    ]
    mask = pd.Series(False, index=df.index, dtype=bool)
    for source_name in source_columns:
        source_col = _find_column(df, source_name)
        if source_col is None:
            continue
        values = df[source_col].map(_clean_import_field_value)
        mask = mask | values.notna()
    sample = df.loc[mask].head(limit) if bool(mask.any()) else df.head(limit)
    return [
        {str(column): _json_preview_value(value) for column, value in row.items()}
        for _, row in sample.iterrows()
    ]


def preview_uploaded_workbooks(uploaded_files) -> ImportPreviewResponse:
    previews: list[ImportFilePreview] = []
    errors: list[str] = []
    for file_index, uploaded_file in enumerate(uploaded_files or []):
        filename = str(getattr(uploaded_file, "name", "uploaded workbook"))
        try:
            df, software = _read_uploaded_workbook_frame(uploaded_file)
            suggested = _suggest_import_parsing_config(
                df,
                file_index=file_index,
                file_name=filename,
                software=software,
            )
            previews.append(
                ImportFilePreview(
                    file_index=file_index,
                    file_name=filename,
                    software=software,
                    columns=[str(column) for column in df.columns],
                    row_count=int(len(df)),
                    sample_rows=_preview_sample_rows(df, suggested),
                    suggested_config=suggested,
                )
            )
        except Exception as exc:
            errors.append(f"Failed to inspect Excel file '{filename}': {exc}")
    return ImportPreviewResponse(files=previews, errors=errors)


def _standardize_isodat_alternating_cycles(df: pd.DataFrame) -> pd.DataFrame:
    """Convert alternating reference/sample ISODAT rows into paired cycle columns."""
    if df is None or df.empty or "Cycle Number" in df.columns:
        return df
    mass_columns = {
        mass: _find_column(df, f"rIntensity {mass}")
        for mass in (44, 45, 46)
    }
    if mass_columns[44] is None:
        return df
    signature_columns = [
        column
        for column in ("Row", "Identifier 1", "Identifier 2", "Date", "Time", "Line")
        if column in df.columns
    ]
    if signature_columns:
        signature = df[signature_columns].fillna("").astype(str)
        boundaries = signature.ne(signature.shift()).any(axis=1)
        group_ids = boundaries.cumsum()
    else:
        group_ids = pd.Series(1, index=df.index)

    standardized_rows: list[pd.Series] = []
    for _, group in df.groupby(group_ids, sort=False):
        group = group.copy()
        pair_count = len(group) // 2
        if pair_count < 1:
            row = group.iloc[0].copy()
            row["Cycle Number"] = "Pre"
            standardized_rows.append(row)
            continue

        even_is_reference = True
        expected_sample_col = _find_column(group, "1  Cycle Int  Samp  44")
        expected_reference_col = _find_column(group, "1  Cycle Int  Ref  44")
        if expected_sample_col and expected_reference_col:
            r44_col = mass_columns[44]
            row0 = pd.to_numeric(pd.Series([group.iloc[0].get(r44_col)]), errors="coerce").iloc[0]
            row1 = pd.to_numeric(pd.Series([group.iloc[1].get(r44_col)]), errors="coerce").iloc[0]
            expected_sample = pd.to_numeric(
                pd.Series([group.iloc[0].get(expected_sample_col)]),
                errors="coerce",
            ).iloc[0]
            expected_reference = pd.to_numeric(
                pd.Series([group.iloc[0].get(expected_reference_col)]),
                errors="coerce",
            ).iloc[0]
            if all(np.isfinite(value) for value in (row0, row1, expected_sample, expected_reference)):
                normal_distance = abs(row0 - expected_reference) + abs(row1 - expected_sample)
                reversed_distance = abs(row0 - expected_sample) + abs(row1 - expected_reference)
                even_is_reference = normal_distance <= reversed_distance

        pre_row = group.iloc[0].copy()
        pre_row["Cycle Number"] = "Pre"
        standardized_rows.append(pre_row)
        for pair_index in range(pair_count):
            even_row = group.iloc[pair_index * 2]
            odd_row = group.iloc[pair_index * 2 + 1]
            reference_row = even_row if even_is_reference else odd_row
            sample_row = odd_row if even_is_reference else even_row
            cycle_row = sample_row.copy()
            cycle_row["Cycle Number"] = f"Cycle {pair_index + 1}"
            for mass, source_col in mass_columns.items():
                if source_col is None:
                    continue
                cycle_row[f"1  Cycle Int  Samp  {mass}"] = sample_row.get(source_col)
                cycle_row[f"1  Cycle Int  Ref  {mass}"] = reference_row.get(source_col)
            standardized_rows.append(cycle_row)

    if not standardized_rows:
        return df
    return pd.DataFrame(standardized_rows).reset_index(drop=True)


def _load_uploaded_workbooks(
    uploaded_files,
    parsing_config: ImportParsingConfig | None = None,
):
    """Read and normalize uploaded Excel workbooks into analysis-ready dataframes."""
    dfs = []
    dfs_cycles_source = []
    loaded_file_specs = []
    load_errors = []

    for file_index, uploaded_file in enumerate(uploaded_files or []):
        filename = str(getattr(uploaded_file, 'name', 'uploaded workbook'))
        size = getattr(uploaded_file, 'size', None)
        if size == 0:
            load_errors.append(
                f"Failed to read Excel file '{filename}': the file is empty (0 bytes). "
                "Download or save the workbook locally, then select it again."
            )
            continue

        try:
            uploaded_file.seek(0)
        except Exception:
            pass

        try:
            df, software = _read_uploaded_workbook_frame(uploaded_file)
        except Exception as exc:
            load_errors.append(f"Failed to read Excel file '{filename}': {exc}")
            continue

        df['Excel File'] = uploaded_file.name
        suggested_config = _suggest_import_parsing_config(
            df,
            file_index=file_index,
            file_name=filename,
            software=software,
        )
        selected_parsing_config = _resolve_workbook_parsing_config(
            parsing_config,
            suggested_config,
        )

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

        if software == "isodat":
            df = _standardize_isodat_alternating_cycles(df)

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
        for signal_col in (
            "1  Cycle Int  Ref  44",
            "1  Cycle Int  Samp  45",
            "1  Cycle Int  Ref  45",
            "1  Cycle Int  Samp  46",
            "1  Cycle Int  Ref  46",
        ):
            if signal_col in df.columns:
                df[signal_col] = _normalize_signal_intensity(df[signal_col])

        try:
            df = _apply_import_parsing_config(df, selected_parsing_config)
        except ValueError as exc:
            load_errors.append(f"Failed to parse identity fields in '{filename}': {exc}")
            continue

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
        file_spec = _build_uploaded_file_spec(uploaded_file)
        file_spec["software"] = software
        file_spec["identity_parsing"] = selected_parsing_config.model_dump(mode="json")
        loaded_file_specs.append(file_spec)

    if not dfs:
        return None, None, loaded_file_specs, load_errors

    combined_df = pd.concat(dfs, ignore_index=True, sort=False) if len(dfs) > 1 else dfs[0]
    combined_cycles_source = (
        pd.concat(dfs_cycles_source, ignore_index=True, sort=False)
        if len(dfs_cycles_source) > 1 else
        (dfs_cycles_source[0] if dfs_cycles_source else None)
    )
    return combined_df, combined_cycles_source, loaded_file_specs, load_errors
