from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

try:
    import plotly.graph_objects as go
except ModuleNotFoundError:  # pragma: no cover - optional for logic-only tests
    go = None

from ..constants import (
    CYCLE1_SIGNAL_DIFF44_COL,
    CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL,
    CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
    CYCLE1_SIGNAL_RELATIVE_MISMATCH44_COL,
    CYCLE1_SIGNAL_SAMP44_COL,
    CYCLE1_SIGNAL_SYMMETRIC_MISMATCH44_COL,
)
from ..contracts import CycleDiagnosticsPayload
from ..shared.dataframe import (
    _find_cycle_intensity_columns,
    _get_species_series,
    _normalize_column_key,
    _normalize_signal_intensity,
    _parse_numeric_token,
)
from ..shared.json_compat import to_json_compatible
from .outliers import (
    RangeConfig,
    _apply_manual_outlier_overrides,
    _partial_status_outlier_mask,
    _signal_out_of_range_mask,
    compute_statistical_outlier_masks,
    is_row_outlier_effective,
)

SATURATION_CORRECTION_METHODS = {
    "cycle_mean",
    "first_valid_cycle",
    "last_valid_cycle",
    "reference_gas_intensity",
    "first_cycle",
    "cycle_relative_mismatch",
    "cycle_symmetric_mismatch",
    "cycle_mean_intensity",
    "cycle_intensity_weighted_mismatch",
    "cycle_two_term_mean_mismatch",
    "cycle_plateau",
}

CYCLE_LINEARITY_SATURATION_METHODS = {
    "cycle_relative_mismatch",
    "cycle_symmetric_mismatch",
    "cycle_mean_intensity",
    "cycle_intensity_weighted_mismatch",
    "cycle_two_term_mean_mismatch",
    "cycle_plateau",
}

CYCLE_LINEARITY_METHOD_LABELS = {
    "cycle_relative_mismatch": "Relative mismatch",
    "cycle_symmetric_mismatch": "Symmetric mismatch",
    "cycle_mean_intensity": "Mean intensity",
    "cycle_intensity_weighted_mismatch": "Intensity-weighted mismatch",
    "cycle_two_term_mean_mismatch": "Two-term mean + mismatch",
    "cycle_plateau": "Late-cycle plateau",
}


def normalize_saturation_correction_method(value: Any) -> str:
    method = str(value or "reference_gas_intensity").strip()
    return method if method in SATURATION_CORRECTION_METHODS else "reference_gas_intensity"


def saturation_correction_method_for_isotope(config: Any, isotope_key: Any) -> str:
    if isinstance(config, dict):
        legacy_method = config.get("saturation_correction_method")
        d13_method = config.get("saturation_correction_method_d13")
        d18_method = config.get("saturation_correction_method_d18")
    else:
        legacy_method = getattr(config, "saturation_correction_method", None)
        d13_method = getattr(config, "saturation_correction_method_d13", None)
        d18_method = getattr(config, "saturation_correction_method_d18", None)
    key = str(isotope_key or "").strip()
    if key == "d18O":
        return normalize_saturation_correction_method(d18_method or legacy_method)
    return normalize_saturation_correction_method(d13_method or legacy_method)


def resolve_row_label(df: pd.DataFrame, raw_row_label: Any) -> Any | None:
    candidates = [raw_row_label, str(raw_row_label)]
    try:
        as_int = int(float(raw_row_label))
        candidates.extend([as_int, str(as_int)])
    except Exception:
        pass
    for candidate in candidates:
        try:
            if candidate in df.index:
                return candidate
        except Exception:
            continue
    return None


def _get_isotope_target_column(isotope_key: str) -> str | None:
    key = str(isotope_key).strip()
    if key == "d13C":
        return "d 13C/12C  Mean"
    if key == "d18O":
        return "d 18O/16O  Mean"
    return None


def build_target_info(
    df: pd.DataFrame,
    row_label: Any,
    isotope_key: str,
    edit_state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    target_col = _get_isotope_target_column(isotope_key)
    if target_col is None or row_label not in df.index or target_col not in df.columns:
        return None
    status_value = ""
    if "Collector Status" in df.columns:
        raw_status = df.at[row_label, "Collector Status"]
        if pd.notna(raw_status) and str(raw_status).strip() != "":
            status_value = str(raw_status).strip()
    source_excel = "Unknown"
    if "Excel File" in df.columns:
        source_val = df.at[row_label, "Excel File"]
        if pd.notna(source_val) and str(source_val).strip() != "":
            source_excel = str(source_val).strip()
    identifier_1 = df.at[row_label, "Identifier 1"] if "Identifier 1" in df.columns else ""
    identifier_2 = df.at[row_label, "Identifier 2"] if "Identifier 2" in df.columns else ""
    current_value_raw = pd.to_numeric(pd.Series([df.at[row_label, target_col]]), errors="coerce").iloc[0]
    has_value = pd.notna(current_value_raw)
    current_value = float(current_value_raw) if has_value else 0.0
    original_map = (edit_state or {}).get("original_delta_values", {})
    original_missing_tokens = {
        str(token)
        for token in (edit_state or {}).get("original_missing_delta_tokens", [])
        if str(token).strip() != ""
    }
    original_key = f"{str(isotope_key).strip()}|{str(row_label)}"
    if original_key in original_missing_tokens:
        original_value_raw = np.nan
    else:
        original_value_raw = original_map.get(original_key, current_value_raw if has_value else np.nan)
    original_value_num = pd.to_numeric(pd.Series([original_value_raw]), errors="coerce").iloc[0]
    original_value = float(original_value_num) if pd.notna(original_value_num) else None
    return {
        "row_label": row_label,
        "row_key": str(row_label),
        "isotope_key": str(isotope_key).strip(),
        "identifier_1": "" if identifier_1 is None or pd.isna(identifier_1) else str(identifier_1),
        "identifier_2": "" if identifier_2 is None or pd.isna(identifier_2) else str(identifier_2),
        "target_col": target_col,
        "source_excel": source_excel,
        "collector_status": status_value,
        "is_failed_sample": status_value == "Failed Sample",
        "has_value": bool(has_value),
        "current_value": current_value,
        "original_value": original_value,
    }


def _extract_mass_from_intensity_column(col_name: str) -> int | None:
    low = _normalize_column_key(col_name)
    match = re.search(r"(?<!\d)(44|45|46)(?:\.0+)?(?!\d)", low)
    return int(match.group(1)) if match else None


def _pick_cycle_value_column(df: pd.DataFrame, primary_col: str, patterns: list[str]) -> str | None:
    if df is None or df.empty:
        return None
    for col in df.columns:
        if not isinstance(col, str):
            continue
        low = _normalize_column_key(col)
        if "standard" in low:
            continue
        if any(term in low for term in ("std", "sd", "se")):
            continue
        if "mean" in low:
            continue
        if any(re.search(pattern, low) for pattern in patterns):
            vals = pd.to_numeric(df[col], errors="coerce")
            if vals.notna().any():
                return col
    if primary_col in df.columns:
        vals = pd.to_numeric(df[primary_col], errors="coerce")
        if vals.notna().any():
            return primary_col
    return primary_col if primary_col in df.columns else None


def _build_saturation_mask_from_intensity_df(
    intensity_df: pd.DataFrame,
    required_masses: list[int],
    threshold: float = 48.0,
) -> pd.Series:
    if intensity_df is None or intensity_df.empty:
        return pd.Series(False, index=pd.Index([], dtype=int), dtype=bool)
    sat_mask = pd.Series(False, index=intensity_df.index, dtype=bool)
    has_mass_cols = False
    for mass in required_masses:
        mass_cols = [col for col in intensity_df.columns if _extract_mass_from_intensity_column(col) == mass]
        if not mass_cols:
            continue
        has_mass_cols = True
        mass_sat = (intensity_df[mass_cols] > float(threshold)).any(axis=1)
        sat_mask = sat_mask | mass_sat
    if not has_mass_cols:
        return pd.Series(False, index=intensity_df.index, dtype=bool)
    return sat_mask


def _build_sample_gas_escape_mask(
    cycles: pd.DataFrame,
    intensity_cols: list[str],
    required_masses: list[int],
    *,
    collapse_ratio: float = 0.20,
    abrupt_drop_ratio: float = 0.40,
    sample_reference_ratio: float = 0.35,
    minimum_previous_signal: float = 1.0,
) -> pd.Series:
    if cycles is None or cycles.empty or not intensity_cols:
        index = cycles.index if isinstance(cycles, pd.DataFrame) else pd.Index([], dtype=int)
        return pd.Series(False, index=index, dtype=bool)

    ordered = cycles.sort_values("_cycle_order", kind="mergesort") if "_cycle_order" in cycles.columns else cycles
    escape_candidates = pd.Series(0, index=ordered.index, dtype=int)
    mass44_candidates = pd.Series(False, index=ordered.index, dtype=bool)
    masses_with_sample_signal = 0

    for mass in required_masses:
        sample_col, reference_col = _pick_mass_role_columns(ordered, intensity_cols, mass)
        if sample_col is None or sample_col not in ordered.columns:
            continue

        sample = _normalize_signal_intensity(ordered[sample_col])
        if not bool(sample.notna().any()):
            continue

        masses_with_sample_signal += 1
        previous_sample = sample.shift(1)
        previous_peak = sample.where(sample > 0).cummax().shift(1)
        has_signal_history = previous_peak.notna() & (previous_peak >= float(minimum_previous_signal))
        collapsed_from_peak = sample.notna() & (sample <= previous_peak * float(collapse_ratio))
        dropped_abruptly = (
            sample.notna()
            & previous_sample.notna()
            & (previous_sample > 0)
            & (sample <= previous_sample * float(abrupt_drop_ratio))
        )
        candidate = has_signal_history & collapsed_from_peak & dropped_abruptly

        if reference_col is not None and reference_col in ordered.columns:
            reference = _normalize_signal_intensity(ordered[reference_col])
            reference_present = reference.notna() & (reference > 0)
            sample_far_below_reference = sample.notna() & (sample <= reference * float(sample_reference_ratio))
            candidate = candidate & (~reference_present | sample_far_below_reference)

        candidate = candidate.fillna(False).astype(bool)
        escape_candidates = escape_candidates + candidate.astype(int)
        if mass == 44:
            mass44_candidates = candidate

    if masses_with_sample_signal == 0:
        return pd.Series(False, index=cycles.index, dtype=bool)

    if masses_with_sample_signal == 1:
        combined_candidates = escape_candidates >= 1
    else:
        combined_candidates = (escape_candidates >= 2) | mass44_candidates

    escape_mask = pd.Series(False, index=ordered.index, dtype=bool)
    if bool(combined_candidates.any()):
        first_escape_idx = combined_candidates[combined_candidates].index[0]
        first_pos = ordered.index.get_loc(first_escape_idx)
        if isinstance(first_pos, slice):
            first_pos = int(first_pos.start or 0)
        elif isinstance(first_pos, np.ndarray):
            first_pos = int(np.flatnonzero(first_pos)[0])
        escape_mask.iloc[int(first_pos):] = True

    return escape_mask.reindex(cycles.index, fill_value=False).astype(bool)


def _extract_cycle_order(value: Any) -> float:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    text = str(value).strip().lower()
    if text == "" or text == "nan":
        return np.nan
    if text == "pre":
        return 0
    match = re.search(r"(\d+)", text)
    if match:
        return float(match.group(1))
    return np.nan


_PREPARED_CYCLES_ATTR = "_irms_prepared_cycles_frame"
_RUN_LEVEL_BASIS_SUMMARY_ATTR = "_irms_run_level_linearity_basis_summary"

LINEARITY_CYCLE_INTENSITY_AGGREGATIONS = {
    "run_median",
    "first_valid_cycle",
    "last_valid_cycle",
}


def normalize_linearity_cycle_intensity_aggregation(value: Any) -> str:
    mode = str(value or "run_median").strip()
    return mode if mode in LINEARITY_CYCLE_INTENSITY_AGGREGATIONS else "run_median"


def _prepare_cycles_lookup_frame(cycles_df: pd.DataFrame) -> pd.DataFrame:
    cached = cycles_df.attrs.get(_PREPARED_CYCLES_ATTR)
    if isinstance(cached, pd.DataFrame):
        return cached

    work = cycles_df.copy()
    cycle_order = work["Cycle Number"].apply(_extract_cycle_order)
    is_pre = work["Cycle Number"].astype(str).str.strip().str.lower().eq("pre")
    cycle_order = cycle_order.where(~is_pre, 0)
    work["_cycle_order"] = cycle_order
    group_id = is_pre.cumsum()
    group_id = group_id.where(is_pre | cycle_order.notna(), np.nan)
    work["_cycle_group"] = group_id

    id_cols = [
        "Identifier 1",
        "Identifier 2",
        "Label",
        "Species",
        "Comment",
        "Run ID",
        "Line",
        "Date",
        "Date_ordinal",
        "Sample Type",
        "Reference",
        "Excel File",
    ]
    for col in id_cols:
        if col in work.columns:
            work[col] = work.groupby("_cycle_group")[col].ffill()

    cycles_df.attrs[_PREPARED_CYCLES_ATTR] = work
    return work


def get_cycles_for_selected_point(
    df: pd.DataFrame,
    cycles_df: pd.DataFrame | None,
    row_label: Any,
    target_col: str,
) -> tuple[pd.DataFrame | None, pd.Series | None]:
    if cycles_df is None or cycles_df.empty or "Cycle Number" not in cycles_df.columns:
        return None, None
    if df is None or row_label not in df.index:
        return None, None

    processed_row = df.loc[row_label]
    if isinstance(processed_row, pd.DataFrame):
        processed_row = processed_row.iloc[0]

    work = _prepare_cycles_lookup_frame(cycles_df)
    cycle_order = work["_cycle_order"]
    is_pre = work["Cycle Number"].astype(str).str.strip().str.lower().eq("pre")

    pre_rows = work[is_pre].copy()
    if pre_rows.empty:
        return None, None

    def _value_present(value: Any) -> bool:
        if value is None:
            return False
        try:
            if pd.isna(value):
                return False
        except Exception:
            pass
        return str(value).strip() != ""

    candidates = pre_rows.copy()
    for col in ["Excel File", "Identifier 1", "Identifier 2"]:
        if col not in candidates.columns:
            continue
        value = processed_row[col] if col in processed_row.index else None
        if not _value_present(value):
            continue
        mask = candidates[col].astype(str).str.strip().eq(str(value).strip())
        if mask.any():
            candidates = candidates.loc[mask]
    if candidates.empty:
        return None, None

    for col in ["Run ID", "Line", "Date"]:
        if col not in candidates.columns:
            continue
        value = processed_row[col] if col in processed_row.index else None
        if not _value_present(value):
            continue
        if col == "Date":
            p_date = pd.to_datetime(value, errors="coerce")
            if pd.notna(p_date):
                c_dates = pd.to_datetime(candidates[col], errors="coerce")
                mask = c_dates.eq(p_date)
                if mask.any():
                    candidates = candidates.loc[mask]
        else:
            mask = candidates[col].astype(str).str.strip().eq(str(value).strip())
            if mask.any():
                candidates = candidates.loc[mask]
    if candidates.empty:
        return None, None

    if len(candidates) == 1:
        selected_pre = candidates.iloc[0]
    else:
        cand_vals = pd.to_numeric(candidates.get(target_col), errors="coerce")
        proc_val = pd.to_numeric(pd.Series([processed_row.get(target_col)]), errors="coerce").iloc[0]
        if pd.notna(proc_val) and cand_vals.notna().any():
            selected_pre = candidates.loc[(cand_vals - float(proc_val)).abs().idxmin()]
        else:
            selected_pre = candidates.iloc[0]

    group = selected_pre.get("_cycle_group")
    if pd.isna(group):
        return None, None
    cycles = work[(work["_cycle_group"] == group) & (work["_cycle_order"] > 0)].copy()
    if cycles.empty:
        return None, None
    cycles = cycles.sort_values("_cycle_order")
    return cycles, selected_pre


def _pick_cycle_sample_intensity_column(
    cycles: pd.DataFrame,
    intensity_cols: list[str],
    preferred_masses: list[int],
) -> str | None:
    if cycles is None or cycles.empty:
        return None
    for mass in preferred_masses:
        mass_cols = [col for col in intensity_cols if _extract_mass_from_intensity_column(col) == mass]
        if not mass_cols:
            continue
        sample_labeled = []
        for col in mass_cols:
            low = _normalize_column_key(col)
            if "standard" in low or re.search(r"\bstd\b", low) or re.search(r"\bref\b", low):
                continue
            if "sample" in low or "samp" in low or re.search(r"\bsmp\b", low):
                sample_labeled.append(col)
        candidates = sample_labeled if sample_labeled else mass_cols
        ranked = []
        for col in candidates:
            vals = _normalize_signal_intensity(cycles[col])
            median_val = float(vals.median(skipna=True)) if vals.notna().any() else -np.inf
            ranked.append((col, median_val))
        ranked.sort(key=lambda item: item[1], reverse=True)
        if ranked:
            return ranked[0][0]
    return None


def _pick_mass_role_columns(
    cycles: pd.DataFrame,
    intensity_cols: list[str],
    mass: int,
) -> tuple[str | None, str | None]:
    mass_cols = [col for col in intensity_cols if _extract_mass_from_intensity_column(col) == mass]
    if not mass_cols:
        return None, None

    sample_candidates: list[str] = []
    ref_candidates: list[str] = []
    unknown_candidates: list[str] = []
    for col in mass_cols:
        low = _normalize_column_key(col)
        is_ref = "standard" in low or "reference" in low or bool(re.search(r"\bref\b|\bstd\b", low))
        is_sample = "sample" in low or "samp" in low or bool(re.search(r"\bsmp\b", low))
        if is_sample and not is_ref:
            sample_candidates.append(col)
        elif is_ref and not is_sample:
            ref_candidates.append(col)
        else:
            unknown_candidates.append(col)

    col_order = {col: idx for idx, col in enumerate(mass_cols)}

    def _ordered(cols: list[str]) -> list[str]:
        return sorted(cols, key=lambda col: col_order.get(col, len(col_order)))

    def _is_duplicate_col(col: str) -> bool:
        return bool(re.search(r"__dup\d+$", _normalize_column_key(col)))

    if not sample_candidates and not ref_candidates and unknown_candidates:
        # Flattened dual-header cycle exports often produce unlabeled duplicate pairs
        # (e.g. 45.00 m/z and 45.00 m/z__dup2) where the first column is sample and
        # the duplicate is reference gas.
        ordered_unknown = _ordered(unknown_candidates)
        non_duplicate = [col for col in ordered_unknown if not _is_duplicate_col(col)]
        duplicate = [col for col in ordered_unknown if _is_duplicate_col(col)]
        if non_duplicate and duplicate:
            return non_duplicate[0], duplicate[0]
        if len(ordered_unknown) >= 2:
            return ordered_unknown[0], ordered_unknown[1]
        return ordered_unknown[0], None

    def _rank(cols: list[str]) -> list[tuple[str, float]]:
        ranked: list[tuple[str, float]] = []
        for col in cols:
            vals = _normalize_signal_intensity(cycles[col])
            median_val = float(vals.median(skipna=True)) if vals.notna().any() else -np.inf
            ranked.append((col, median_val))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked

    ranked_all = _rank(mass_cols)
    ranked_samples = _rank(sample_candidates)
    ranked_refs = _rank(ref_candidates)

    smp_col = ranked_samples[0][0] if ranked_samples else (ranked_all[0][0] if ranked_all else None)

    std_col: str | None = None
    if ranked_refs:
        ref_pool = [item for item in ranked_refs if item[0] != smp_col]
        if ref_pool:
            std_col = sorted(ref_pool, key=lambda item: item[1])[0][0]
    if std_col is None:
        remaining = [item for item in ranked_all if item[0] != smp_col]
        if remaining:
            std_col = sorted(remaining, key=lambda item: item[1])[0][0]

    if smp_col == std_col:
        std_col = None
    return smp_col, std_col


def _is_partially_saturated_collector(target: dict[str, Any]) -> bool:
    return str(target.get("collector_status", "")).strip().lower() == "partially saturated collectors"


def compute_cycle_mean_for_target(
    df: pd.DataFrame,
    cycles_df: pd.DataFrame | None,
    target: dict[str, Any],
    cycles: pd.DataFrame | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mean": None,
        "valid_mean": None,
        "valid_std_dev": None,
        "valid_cycles": 0,
        "method": "valid_cycle_mean",
        "first_valid_cycle": None,
        "last_valid_cycle": None,
        "selected_cycle": None,
        "selected_value": None,
        "reason": "",
    }
    is_partially_saturated = _is_partially_saturated_collector(target)
    if cycles is None:
        cycles, _ = get_cycles_for_selected_point(df, cycles_df, target.get("row_label"), target.get("target_col"))
    if cycles is None or cycles.empty:
        result["reason"] = "no_cycle_data"
        return result

    isotope_key = str(target.get("isotope_key", "")).strip()
    if isotope_key == "d13C":
        value_col = _pick_cycle_value_column(cycles, "d 13C/12C  Mean", [r"d13", r"d ?13c", r"d45co2", r"\bd45\b"])
        required_masses = [44, 45]
    elif isotope_key == "d18O":
        value_col = _pick_cycle_value_column(cycles, "d 18O/16O  Mean", [r"d18", r"d ?18o", r"d46co2", r"\bd46\b"])
        required_masses = [44, 45, 46]
    else:
        result["reason"] = "unsupported_isotope"
        return result

    if value_col is None or value_col not in cycles.columns:
        result["reason"] = "missing_cycle_delta_column"
        return result
    cycle_delta = pd.to_numeric(cycles[value_col], errors="coerce")
    if cycle_delta.notna().sum() == 0:
        result["reason"] = "no_cycle_delta_values"
        return result

    intensity_cols = [col for col in _find_cycle_intensity_columns(cycles) if col in cycles.columns]
    intensity_for_mask = pd.DataFrame(index=cycles.index)
    for col in intensity_cols:
        intensity_for_mask[col] = _normalize_signal_intensity(cycles[col])
    sat_mask = _build_saturation_mask_from_intensity_df(intensity_for_mask, required_masses).reindex(
        cycles.index,
        fill_value=False,
    )
    escape_mask = _build_sample_gas_escape_mask(cycles, intensity_cols, required_masses).reindex(
        cycles.index,
        fill_value=False,
    )
    valid_mask = cycle_delta.notna() & ~sat_mask & ~escape_mask
    valid_delta = cycle_delta[valid_mask]
    if valid_delta.empty:
        result["reason"] = "no_valid_cycles_after_saturation_filter"
        return result

    valid_mean = float(valid_delta.mean())
    result["mean"] = valid_mean
    result["valid_mean"] = valid_mean
    valid_cycles = int(valid_delta.shape[0])
    result["valid_cycles"] = valid_cycles
    if valid_cycles > 1:
        valid_std_dev = float(valid_delta.std())
        if np.isfinite(valid_std_dev):
            result["valid_std_dev"] = valid_std_dev
    first_valid_idx = valid_delta.index[0]
    last_valid_idx = valid_delta.index[-1]
    first_valid_value = float(valid_delta.iloc[0])
    first_valid_cycle = pd.to_numeric(pd.Series([cycles.loc[first_valid_idx, "_cycle_order"]]), errors="coerce").iloc[0]
    last_valid_cycle = pd.to_numeric(pd.Series([cycles.loc[last_valid_idx, "_cycle_order"]]), errors="coerce").iloc[0]
    if np.isfinite(first_valid_cycle):
        cycle_value = float(first_valid_cycle)
        result["first_valid_cycle"] = int(cycle_value) if cycle_value.is_integer() else cycle_value
    if np.isfinite(last_valid_cycle):
        cycle_value = float(last_valid_cycle)
        result["last_valid_cycle"] = int(cycle_value) if cycle_value.is_integer() else cycle_value

    if is_partially_saturated:
        if np.isfinite(first_valid_cycle):
            cycle_value = float(first_valid_cycle)
            if cycle_value.is_integer():
                result["selected_cycle"] = int(cycle_value)
            else:
                result["selected_cycle"] = cycle_value
        result["selected_value"] = first_valid_value
        result["mean"] = first_valid_value
        result["method"] = "first_valid_cycle"
        result["reason"] = "partially_saturated_use_first_valid_cycle"
        return result

    return result


def _cycle_delta_context(
    cycles: pd.DataFrame,
    isotope_key: str,
) -> tuple[str | None, list[int], int | None]:
    if isotope_key == "d13C":
        return (
            _pick_cycle_value_column(cycles, "d 13C/12C  Mean", [r"d13", r"d ?13c", r"d45co2", r"\bd45\b"]),
            [44, 45],
            45,
        )
    if isotope_key == "d18O":
        return (
            _pick_cycle_value_column(cycles, "d 18O/16O  Mean", [r"d18", r"d ?18o", r"d46co2", r"\bd46\b"]),
            [44, 45, 46],
            46,
        )
    return None, [], None


def _linear_prediction(
    x_values: pd.Series,
    y_values: pd.Series,
    target_x: float,
) -> tuple[float | None, float | None, float | None]:
    x = pd.to_numeric(x_values, errors="coerce")
    y = pd.to_numeric(y_values, errors="coerce")
    mask = x.notna() & y.notna()
    x_fit = x.loc[mask].astype(float)
    y_fit = y.loc[mask].astype(float)
    if len(x_fit) == 0 or not np.isfinite(target_x):
        return None, None, None
    if len(x_fit) == 1 or float(x_fit.max() - x_fit.min()) == 0.0:
        return float(y_fit.iloc[0]), 0.0, float(y_fit.iloc[0])
    slope, intercept = np.polyfit(x_fit.to_numpy(dtype=float), y_fit.to_numpy(dtype=float), 1)
    predicted = (float(slope) * float(target_x)) + float(intercept)
    return float(predicted), float(slope), float(intercept)


def _polynomial_prediction(
    x_values: pd.Series,
    y_values: pd.Series,
    target_x: float,
    max_degree: int = 2,
) -> tuple[float | None, list[float] | None, int | None]:
    x = pd.to_numeric(x_values, errors="coerce")
    y = pd.to_numeric(y_values, errors="coerce")
    mask = np.isfinite(x) & np.isfinite(y)
    x_fit = x.loc[mask].astype(float)
    y_fit = y.loc[mask].astype(float)
    if len(x_fit) == 0 or not np.isfinite(target_x):
        return None, None, None
    if len(x_fit) == 1 or float(x_fit.max() - x_fit.min()) == 0.0:
        return float(y_fit.iloc[0]), [float(y_fit.iloc[0])], 0
    unique_x_count = int(pd.Series(x_fit).nunique(dropna=True))
    degree = int(min(max(1, max_degree), max(1, unique_x_count - 1)))
    try:
        coeffs = np.polyfit(x_fit.to_numpy(dtype=float), y_fit.to_numpy(dtype=float), degree)
    except Exception:
        return None, None, None
    predicted = float(np.polyval(coeffs, float(target_x)))
    if not np.isfinite(predicted):
        return None, None, None
    coeff_list = [float(value) for value in coeffs]
    if not all(np.isfinite(value) for value in coeff_list):
        return None, None, None
    return predicted, coeff_list, degree


def _quadratic_horizontal_target(
    coefficients: list[float] | tuple[float, ...] | None,
    fallback_target: float,
) -> tuple[float, str]:
    if coefficients is None:
        return float(fallback_target), "fallback"
    try:
        coeffs = [float(value) for value in coefficients]
    except Exception:
        return float(fallback_target), "fallback"
    if len(coeffs) >= 3 and all(np.isfinite(value) for value in coeffs[:2]):
        a = float(coeffs[0])
        b = float(coeffs[1])
        if abs(a) > 1e-12:
            target = -b / (2.0 * a)
            if np.isfinite(target):
                return float(target), "quadratic_horizontal"
    return float(fallback_target), "fallback"


def _fit_line_points(
    x_values: pd.Series,
    slope: float | None,
    intercept: float | None,
    target_x: float | None,
) -> tuple[list[float], list[float]]:
    if slope is None or intercept is None:
        return [], []
    x = pd.to_numeric(x_values, errors="coerce").dropna().astype(float)
    if x.empty and target_x is None:
        return [], []
    candidates = x.tolist()
    if target_x is not None and np.isfinite(float(target_x)):
        candidates.append(float(target_x))
    if not candidates:
        return [], []
    x_min = min(candidates)
    x_max = max(candidates)
    if x_min == x_max:
        return [x_min], [(float(slope) * x_min) + float(intercept)]
    xs = [x_min, x_max]
    ys = [(float(slope) * value) + float(intercept) for value in xs]
    return xs, ys


def _fit_curve_points(
    x_values: pd.Series,
    coefficients: list[float] | tuple[float, ...] | None,
    target_x: float | None,
) -> tuple[list[float], list[float]]:
    if coefficients is None:
        return [], []
    try:
        coeffs = [float(value) for value in coefficients]
    except Exception:
        return [], []
    if not coeffs or not all(np.isfinite(value) for value in coeffs):
        return [], []
    x = pd.to_numeric(x_values, errors="coerce").dropna().astype(float)
    if x.empty and target_x is None:
        return [], []
    candidates = x.tolist()
    if target_x is not None and np.isfinite(float(target_x)):
        candidates.append(float(target_x))
    if not candidates:
        return [], []
    x_min = min(candidates)
    x_max = max(candidates)
    if x_min == x_max:
        xs = [x_min]
    else:
        xs = np.linspace(x_min, x_max, 80).tolist()
    ys = [float(np.polyval(coeffs, value)) for value in xs]
    return xs, ys


def _cycle_44_basis_frame(cycles: pd.DataFrame, intensity_cols: list[str]) -> pd.DataFrame:
    smp_col, ref_col = _pick_mass_role_columns(cycles, intensity_cols, 44)
    basis = pd.DataFrame(index=cycles.index)
    samp = _normalize_signal_intensity(cycles[smp_col]) if smp_col is not None else pd.Series(np.nan, index=cycles.index)
    ref = _normalize_signal_intensity(cycles[ref_col]) if ref_col is not None else pd.Series(np.nan, index=cycles.index)
    diff = samp - ref
    mean_intensity = (samp + ref) / 2.0
    with np.errstate(divide="ignore", invalid="ignore"):
        relative = diff / ref
        symmetric = diff / mean_intensity
    finite_samp = samp[np.isfinite(samp)]
    samp_ref = float(finite_samp.median()) if not finite_samp.empty else np.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        weighted = 10.0 * relative * (samp / samp_ref)
    basis["sample"] = samp
    basis["reference"] = ref
    basis["relative_mismatch"] = relative.where(np.isfinite(relative))
    basis["symmetric_mismatch"] = symmetric.where(np.isfinite(symmetric))
    basis["mean_intensity"] = mean_intensity.where(np.isfinite(mean_intensity))
    basis["intensity_weighted_mismatch"] = weighted.where(np.isfinite(weighted))
    return basis


def apply_run_level_linearity_basis_from_cycles(
    df: pd.DataFrame,
    cycles_df: pd.DataFrame | None,
    row_labels: set[Any] | None = None,
    cycle_intensity_aggregation: str = "run_median",
) -> pd.DataFrame:
    if df is None or df.empty or cycles_df is None or cycles_df.empty or "Cycle Number" not in cycles_df.columns:
        return df
    work = df.copy()
    target_col = "d 13C/12C  Mean" if "d 13C/12C  Mean" in work.columns else "d 18O/16O  Mean"
    aggregation_mode = normalize_linearity_cycle_intensity_aggregation(cycle_intensity_aggregation)

    def _median_value(values: pd.Series) -> float | None:
        numeric = pd.to_numeric(values, errors="coerce")
        numeric = numeric[np.isfinite(numeric)]
        if numeric.empty:
            return None
        return float(numeric.median())

    def _endpoint_value(values: pd.Series, basis: pd.DataFrame, from_end: bool) -> float | None:
        numeric = pd.to_numeric(values, errors="coerce")
        valid_mask = np.isfinite(numeric)
        if not bool(valid_mask.any()):
            return None
        sat_mask = pd.Series(False, index=basis.index, dtype=bool)
        escape_mask = pd.Series(False, index=basis.index, dtype=bool)
        if {"sample", "reference"}.issubset(set(basis.columns)):
            intensity_for_mask = pd.DataFrame(
                {
                    "Cycle Intensity Samp 44": pd.to_numeric(basis["sample"], errors="coerce"),
                    "Cycle Intensity Ref 44": pd.to_numeric(basis["reference"], errors="coerce"),
                },
                index=basis.index,
            )
            sat_mask = _build_saturation_mask_from_intensity_df(intensity_for_mask, [44]).reindex(basis.index, fill_value=False)
            basis_cycles = intensity_for_mask.copy()
            basis_cycles["_cycle_order"] = np.arange(1, len(basis_cycles) + 1, dtype=float)
            escape_mask = _build_sample_gas_escape_mask(
                basis_cycles,
                ["Cycle Intensity Samp 44", "Cycle Intensity Ref 44"],
                [44],
            ).reindex(basis.index, fill_value=False)
        valid_unsaturated = (
            valid_mask
            & ~sat_mask.reindex(numeric.index, fill_value=False).astype(bool)
            & ~escape_mask.reindex(numeric.index, fill_value=False).astype(bool)
        )
        selected = numeric.loc[valid_unsaturated] if bool(valid_unsaturated.any()) else numeric.loc[valid_mask]
        return float(selected.iloc[-1] if from_end else selected.iloc[0])

    def _basis_value(values: pd.Series, basis: pd.DataFrame) -> float | None:
        if aggregation_mode == "first_valid_cycle":
            return _endpoint_value(values, basis, from_end=False)
        if aggregation_mode == "last_valid_cycle":
            return _endpoint_value(values, basis, from_end=True)
        return _median_value(values)

    def _basis_column_values(basis: pd.DataFrame) -> dict[str, float | None]:
        return {
            CYCLE1_SIGNAL_SAMP44_COL: _basis_value(basis["sample"], basis),
            CYCLE1_SIGNAL_DIFF44_COL: _basis_value(basis["sample"] - basis["reference"], basis),
            CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL: _basis_value(basis["mean_intensity"], basis),
            CYCLE1_SIGNAL_RELATIVE_MISMATCH44_COL: _basis_value(basis["relative_mismatch"], basis),
            CYCLE1_SIGNAL_SYMMETRIC_MISMATCH44_COL: _basis_value(basis["symmetric_mismatch"], basis),
            CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL: _basis_value(basis["intensity_weighted_mismatch"], basis),
        }

    updates: dict[str, dict[Any, float]] = {
        CYCLE1_SIGNAL_SAMP44_COL: {},
        CYCLE1_SIGNAL_DIFF44_COL: {},
        CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL: {},
        CYCLE1_SIGNAL_RELATIVE_MISMATCH44_COL: {},
        CYCLE1_SIGNAL_SYMMETRIC_MISMATCH44_COL: {},
        CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL: {},
    }
    if row_labels is not None:
        row_tokens = {str(label) for label in row_labels}
        scoped_index = work.index[work.index.to_series().astype(str).isin(row_tokens)]
        for row_label in scoped_index:
            cycles, _ = get_cycles_for_selected_point(work, cycles_df, row_label, target_col)
            if cycles is None or cycles.empty:
                continue
            intensity_cols = [col for col in _find_cycle_intensity_columns(cycles) if col in cycles.columns]
            if not intensity_cols:
                continue
            if "_cycle_order" in cycles.columns:
                cycles = cycles.sort_values("_cycle_order")
            basis = _cycle_44_basis_frame(cycles, intensity_cols)
            column_values = _basis_column_values(basis)
            for col, value in column_values.items():
                if value is not None and np.isfinite(value):
                    updates[col][row_label] = float(value)
        for col, values in updates.items():
            if not values:
                continue
            if col not in work.columns:
                work[col] = np.nan
            series = pd.Series(values, dtype=float)
            work.loc[series.index, col] = series
        return work

    prepared = _prepare_cycles_lookup_frame(cycles_df)
    is_pre = prepared["Cycle Number"].astype(str).str.strip().str.lower().eq("pre")
    pre_rows = prepared.loc[is_pre].copy()
    if pre_rows.empty:
        return work

    summary_cache = cycles_df.attrs.get(_RUN_LEVEL_BASIS_SUMMARY_ATTR)
    if not isinstance(summary_cache, dict) or any(key not in LINEARITY_CYCLE_INTENSITY_AGGREGATIONS for key in summary_cache.keys()):
        summary_cache = {}
    summary_by_group = summary_cache.get(aggregation_mode)
    if not isinstance(summary_by_group, dict):
        summary_by_group = {}
        intensity_cols = [col for col in _find_cycle_intensity_columns(prepared) if col in prepared.columns]
        if intensity_cols:
            cycle_rows = prepared.loc[prepared["_cycle_order"] > 0].copy()
            if aggregation_mode == "run_median":
                # Column roles are stable within an imported workbook. Resolve
                # them once per source file and aggregate all of its runs in one
                # vectorized pass instead of constructing a DataFrame per run.
                partition_col = "Excel File" if "Excel File" in cycle_rows.columns else None
                partitions = (
                    cycle_rows.groupby(partition_col, sort=False, dropna=False)
                    if partition_col
                    else [(None, cycle_rows)]
                )
                for _, partition in partitions:
                    smp_col, ref_col = _pick_mass_role_columns(partition, intensity_cols, 44)
                    if smp_col is None and ref_col is None:
                        continue
                    samp = (
                        _normalize_signal_intensity(partition[smp_col])
                        if smp_col is not None
                        else pd.Series(np.nan, index=partition.index, dtype=float)
                    ).replace([np.inf, -np.inf], np.nan)
                    ref = (
                        _normalize_signal_intensity(partition[ref_col])
                        if ref_col is not None
                        else pd.Series(np.nan, index=partition.index, dtype=float)
                    ).replace([np.inf, -np.inf], np.nan)
                    groups = partition["_cycle_group"]
                    diff = samp - ref
                    mean_intensity = (samp + ref) / 2.0
                    with np.errstate(divide="ignore", invalid="ignore"):
                        relative = diff / ref
                        symmetric = diff / mean_intensity
                        sample_reference = samp.groupby(groups).transform("median")
                        weighted = 10.0 * relative * (samp / sample_reference)
                    basis = pd.DataFrame(
                        {
                            CYCLE1_SIGNAL_SAMP44_COL: samp,
                            CYCLE1_SIGNAL_DIFF44_COL: diff,
                            CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL: mean_intensity,
                            CYCLE1_SIGNAL_RELATIVE_MISMATCH44_COL: relative,
                            CYCLE1_SIGNAL_SYMMETRIC_MISMATCH44_COL: symmetric,
                            CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL: weighted,
                            "_cycle_group": groups,
                        },
                        index=partition.index,
                    ).replace([np.inf, -np.inf], np.nan)
                    medians = basis.groupby("_cycle_group", sort=False).median(numeric_only=True)
                    for group, values in medians.iterrows():
                        summary_by_group[group] = {
                            col: (float(values[col]) if pd.notna(values[col]) else None)
                            for col in updates
                        }
            else:
                for group, group_cycles in cycle_rows.groupby("_cycle_group", sort=False):
                    if pd.isna(group) or group_cycles.empty:
                        continue
                    group_cycles = group_cycles.sort_values("_cycle_order")
                    basis = _cycle_44_basis_frame(group_cycles, intensity_cols)
                    summary_by_group[group] = _basis_column_values(basis)
        summary_cache[aggregation_mode] = summary_by_group
        cycles_df.attrs[_RUN_LEVEL_BASIS_SUMMARY_ATTR] = summary_cache
    if not summary_by_group:
        return work

    def _value_present(value: Any) -> bool:
        if value is None:
            return False
        try:
            if pd.isna(value):
                return False
        except Exception:
            pass
        return str(value).strip() != ""

    # Convert the small run-level lookup to Python records once. Repeated pandas
    # boolean indexing here used to allocate several temporary frames per result
    # row, which becomes very expensive for large imported workbooks.
    pre_records = pre_rows.to_dict(orient="records")

    def _text_matches(left: Any, right: Any) -> bool:
        return str(left).strip() == str(right).strip()

    def _date_matches(left: Any, right: Any) -> bool:
        left_date = pd.to_datetime(left, errors="coerce")
        right_date = pd.to_datetime(right, errors="coerce")
        return bool(pd.notna(left_date) and pd.notna(right_date) and left_date == right_date)

    for row_label, processed_row in work.iterrows():
        candidates = pre_records
        for col in ["Excel File", "Identifier 1", "Identifier 2"]:
            if col not in pre_rows.columns:
                continue
            value = processed_row.get(col)
            if not _value_present(value):
                continue
            matches = [candidate for candidate in candidates if _text_matches(candidate.get(col), value)]
            if matches:
                candidates = matches
        for col in ["Run ID", "Line", "Date"]:
            if col not in pre_rows.columns:
                continue
            value = processed_row.get(col)
            if not _value_present(value):
                continue
            if col == "Date":
                matches = [candidate for candidate in candidates if _date_matches(candidate.get(col), value)]
            else:
                matches = [candidate for candidate in candidates if _text_matches(candidate.get(col), value)]
            if matches:
                candidates = matches
        if not candidates:
            continue
        if len(candidates) == 1:
            selected_pre = candidates[0]
        else:
            proc_val = pd.to_numeric(processed_row.get(target_col), errors="coerce")
            numeric_candidates = [
                (candidate, pd.to_numeric(candidate.get(target_col), errors="coerce"))
                for candidate in candidates
            ]
            finite_candidates = [
                (candidate, float(value))
                for candidate, value in numeric_candidates
                if pd.notna(value) and np.isfinite(float(value))
            ]
            selected_pre = (
                min(finite_candidates, key=lambda item: abs(item[1] - float(proc_val)))[0]
                if pd.notna(proc_val) and np.isfinite(float(proc_val)) and finite_candidates
                else candidates[0]
            )
        group = selected_pre.get("_cycle_group")
        column_values = summary_by_group.get(group)
        if not isinstance(column_values, dict):
            continue
        for col, value in column_values.items():
            if value is not None and np.isfinite(value):
                updates[col][row_label] = float(value)
    for col, values in updates.items():
        if not values:
            continue
        if col not in work.columns:
            work[col] = np.nan
        series = pd.Series(values, dtype=float)
        work.loc[series.index, col] = series
    return work


def _curve_prediction_for_cycle_basis(
    valid_basis: pd.Series,
    valid_delta: pd.Series,
    fallback_target_basis: float,
) -> tuple[float | None, list[float] | None, int | None, float | None, str | None]:
    basis = pd.to_numeric(valid_basis, errors="coerce")
    delta = pd.to_numeric(valid_delta, errors="coerce")
    mask = np.isfinite(basis) & np.isfinite(delta)
    if int(mask.sum()) < 2 or not np.isfinite(fallback_target_basis):
        return None, None, None, None, None
    _, coefficients, fit_degree = _polynomial_prediction(
        basis.loc[mask],
        delta.loc[mask],
        float(fallback_target_basis),
        max_degree=2,
    )
    if coefficients is None or fit_degree is None:
        return None, None, None, None, None
    target_basis, target_reason = _quadratic_horizontal_target(coefficients, float(fallback_target_basis))
    predicted = float(np.polyval(coefficients, float(target_basis)))
    if not np.isfinite(predicted):
        return None, None, None, None, None
    return predicted, coefficients, fit_degree, float(target_basis), target_reason


def _two_term_prediction_for_cycle_basis(
    valid_mean: pd.Series,
    valid_mismatch: pd.Series,
    valid_delta: pd.Series,
    target_mean: float,
    target_mismatch: float,
) -> tuple[float | None, float | None, float | None, float | None]:
    x1 = pd.to_numeric(valid_mean, errors="coerce")
    x2 = pd.to_numeric(valid_mismatch, errors="coerce")
    y = pd.to_numeric(valid_delta, errors="coerce")
    mask = np.isfinite(x1) & np.isfinite(x2) & np.isfinite(y)
    if int(mask.sum()) < 3 or not (np.isfinite(target_mean) and np.isfinite(target_mismatch)):
        return None, None, None, None
    design = np.column_stack(
        [x1.loc[mask].to_numpy(dtype=float), x2.loc[mask].to_numpy(dtype=float), np.ones(int(mask.sum()))]
    )
    try:
        coeffs, *_ = np.linalg.lstsq(design, y.loc[mask].to_numpy(dtype=float), rcond=None)
    except Exception:
        return None, None, None, None
    b1, b2, intercept = (float(coeffs[0]), float(coeffs[1]), float(coeffs[2]))
    value = intercept + b1 * float(target_mean) + b2 * float(target_mismatch)
    return float(value), b1, b2, intercept


def _linear_slope_or_none(x_values: pd.Series, y_values: pd.Series) -> float | None:
    x = pd.to_numeric(x_values, errors="coerce")
    y = pd.to_numeric(y_values, errors="coerce")
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 2:
        return None
    x_fit = x.loc[mask].astype(float)
    y_fit = y.loc[mask].astype(float)
    if float(x_fit.max() - x_fit.min()) == 0.0:
        return None
    try:
        slope, _ = np.polyfit(x_fit.to_numpy(dtype=float), y_fit.to_numpy(dtype=float), 1)
    except Exception:
        return None
    return float(slope) if np.isfinite(slope) else None


def _format_cycle_number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else float(value)


def _cycle_delta_rate_frame(
    valid_cycles: pd.Series,
    valid_mean_intensity: pd.Series,
    valid_delta: pd.Series,
) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "cycle": pd.to_numeric(valid_cycles, errors="coerce"),
            "mean_intensity": pd.to_numeric(valid_mean_intensity, errors="coerce"),
            "delta": pd.to_numeric(valid_delta, errors="coerce"),
        }
    )
    finite = (
        np.isfinite(frame["cycle"])
        & np.isfinite(frame["mean_intensity"])
        & np.isfinite(frame["delta"])
    )
    frame = frame.loc[finite].sort_values("cycle")
    if frame.empty:
        return frame
    frame["cycle_step"] = frame["cycle"].diff()
    frame["delta_change"] = frame["delta"].diff()
    with np.errstate(divide="ignore", invalid="ignore"):
        frame["delta_rate"] = frame["delta_change"] / frame["cycle_step"]
    rate_finite = (
        np.isfinite(frame["cycle_step"])
        & (np.abs(frame["cycle_step"]) > 1e-12)
        & np.isfinite(frame["delta_change"])
        & np.isfinite(frame["delta_rate"])
    )
    return frame.loc[rate_finite].copy()


def _late_cycle_plateau_estimate(
    valid_cycles: pd.Series,
    valid_mean_intensity: pd.Series,
    valid_delta: pd.Series,
    min_points: int = 3,
    max_points: int = 6,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "value": None,
        "std_dev": None,
        "selected_cycles": [],
        "selected_count": 0,
        "target_mean_intensity": None,
        "target_cycle": None,
        "target_delta_rate": 0.0,
        "delta_span": None,
        "delta_rate_span": None,
        "delta_rate_coefficient": None,
        "fit_std_dev": None,
        "tail_slope": None,
        "full_slope": None,
        "basis": "delta_rate_asymptote",
        "reason": "",
    }
    rate_frame = _cycle_delta_rate_frame(valid_cycles, valid_mean_intensity, valid_delta)
    if len(rate_frame) < int(min_points):
        payload["reason"] = "insufficient_valid_cycles"
        return payload

    full_delta_span = float(rate_frame["delta"].max() - rate_frame["delta"].min())
    if not np.isfinite(full_delta_span):
        payload["reason"] = "invalid_delta_range"
        return payload
    payload["full_slope"] = _linear_slope_or_none(rate_frame["mean_intensity"], rate_frame["delta"])

    selected: pd.DataFrame | None = None
    selected_fit: tuple[float, float, float] | None = None
    max_window = min(int(max_points), len(rate_frame))
    for window_size in range(max_window, int(min_points) - 1, -1):
        window = rate_frame.tail(window_size)
        x = pd.to_numeric(window["delta_rate"], errors="coerce")
        y = pd.to_numeric(window["delta"], errors="coerce")
        mask = np.isfinite(x) & np.isfinite(y)
        if int(mask.sum()) < int(min_points):
            continue
        x_fit = x.loc[mask].astype(float)
        y_fit = y.loc[mask].astype(float)
        x_span = float(x_fit.max() - x_fit.min())
        if abs(x_span) <= 1e-12:
            if float(x_fit.abs().max()) > max(0.02, full_delta_span * 0.25):
                continue
            slope = 0.0
            intercept = float(y_fit.mean())
            residual_std = float(y_fit.std(ddof=1)) if len(y_fit) > 1 else 0.0
        else:
            try:
                slope, intercept = np.polyfit(x_fit.to_numpy(dtype=float), y_fit.to_numpy(dtype=float), 1)
            except Exception:
                continue
            fitted = (float(slope) * x_fit) + float(intercept)
            residual = y_fit - fitted
            residual_std = float(residual.std(ddof=1)) if len(residual) > 1 else 0.0
        if not (np.isfinite(slope) and np.isfinite(intercept) and np.isfinite(residual_std)):
            continue
        last_delta = float(y_fit.iloc[-1])
        tail_span = float(y_fit.max() - y_fit.min())
        max_distance = max(0.05, full_delta_span * 1.25, tail_span * 4.0)
        if abs(float(intercept) - last_delta) > max_distance:
            continue
        selected = window.loc[mask]
        selected_fit = (float(slope), float(intercept), float(residual_std))
        break

    if selected is None or selected_fit is None:
        payload["reason"] = "no_stable_delta_rate_fit"
        return payload

    slope, value, fit_std_dev = selected_fit
    std_dev = float(selected["delta"].std(ddof=1)) if len(selected) > 1 else 0.0
    target_mean = float(selected["mean_intensity"].median())
    target_cycle = float(selected["cycle"].median())
    delta_span = float(selected["delta"].max() - selected["delta"].min())
    delta_rate_span = float(selected["delta_rate"].max() - selected["delta_rate"].min())
    payload.update(
        {
            "value": value,
            "std_dev": std_dev if np.isfinite(std_dev) and std_dev >= 0 else None,
            "fit_std_dev": fit_std_dev if np.isfinite(fit_std_dev) and fit_std_dev >= 0 else None,
            "selected_cycles": [_format_cycle_number(float(value)) for value in selected["cycle"].tolist()],
            "selected_count": int(len(selected)),
            "target_mean_intensity": target_mean if np.isfinite(target_mean) else None,
            "target_cycle": _format_cycle_number(target_cycle) if np.isfinite(target_cycle) else None,
            "delta_span": delta_span if np.isfinite(delta_span) else None,
            "delta_rate_span": delta_rate_span if np.isfinite(delta_rate_span) else None,
            "delta_rate_coefficient": slope,
            "tail_slope": _linear_slope_or_none(selected["mean_intensity"], selected["delta"]),
            "reason": "delta_rate_asymptote",
        }
    )
    return payload


def _inside_prediction_annotation(
    x_values: pd.Series,
    y_values: pd.Series,
    target_x: float,
    target_y: float,
    label: str,
) -> dict[str, Any]:
    xs = pd.to_numeric(
        pd.concat([pd.Series(x_values), pd.Series([target_x])]),
        errors="coerce",
    ).dropna()
    ys = pd.to_numeric(
        pd.concat([pd.Series(y_values), pd.Series([target_y])]),
        errors="coerce",
    ).dropna()
    x_mid = float((xs.min() + xs.max()) / 2.0) if not xs.empty else float(target_x)
    y_mid = float((ys.min() + ys.max()) / 2.0) if not ys.empty else float(target_y)
    anchor_right = float(target_x) >= x_mid
    anchor_top = float(target_y) >= y_mid
    return {
        "x": float(target_x),
        "y": float(target_y),
        "xref": "x",
        "yref": "y",
        "text": label,
        "showarrow": False,
        "xanchor": "right" if anchor_right else "left",
        "yanchor": "top" if anchor_top else "bottom",
        "xshift": -8 if anchor_right else 8,
        "yshift": -8 if anchor_top else 8,
        "font": {"color": "#243b63", "size": 12},
        "align": "left",
    }


def _finite_color_values(values: pd.Series, fallback: pd.Series) -> pd.Series:
    color = pd.to_numeric(values, errors="coerce")
    if color.notna().any():
        return color
    return pd.to_numeric(fallback, errors="coerce")


def _plot_values(values: Any) -> list[float | None]:
    numeric = pd.to_numeric(pd.Series(values), errors="coerce")
    result: list[float | None] = []
    for value in numeric:
        result.append(float(value) if pd.notna(value) and np.isfinite(value) else None)
    return result


def _marker_with_colorbar(color_values: pd.Series, title: str, size: int = 8) -> dict[str, Any]:
    return {
        "color": _plot_values(color_values),
        "colorscale": "Viridis",
        "size": size,
        "showscale": True,
        "colorbar": {"title": title},
    }


def _cycle_customdata(
    cycles: pd.Series,
    sample: pd.Series,
    reference: pd.Series,
    mean_intensity: pd.Series,
    mismatch: pd.Series,
    d13c: pd.Series | None = None,
    d18o: pd.Series | None = None,
) -> list[list[float | None]]:
    columns = [
        _plot_values(cycles),
        _plot_values(sample),
        _plot_values(reference),
        _plot_values(mean_intensity),
        _plot_values(mismatch),
        _plot_values(d13c if d13c is not None else pd.Series(np.nan, index=cycles.index)),
        _plot_values(d18o if d18o is not None else pd.Series(np.nan, index=cycles.index)),
    ]
    if not columns or not columns[0]:
        return []
    return [[column[index] for column in columns] for index in range(len(columns[0]))]


def _cycle_hovertemplate(x_label: str, y_label: str, color_label: str) -> str:
    return (
        f"{x_label}: %{{x:.4g}}<br>"
        f"{y_label}: %{{y:.4g}}<br>"
        "Cycle: %{customdata[0]:.0f}<br>"
        "Samp44: %{customdata[1]:.4g} V<br>"
        "Ref44: %{customdata[2]:.4g} V<br>"
        "Mean44: %{customdata[3]:.4g} V<br>"
        "Mismatch: %{customdata[4]:.4g}<br>"
        f"Color: {color_label}<extra></extra>"
    )


def compute_saturation_correction_for_target(
    df: pd.DataFrame,
    cycles_df: pd.DataFrame | None,
    target: dict[str, Any],
    cycles: pd.DataFrame | None = None,
    include_figures: bool = True,
    require_partially_saturated: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "available": False,
        "reason": "",
        "last_valid_cycle": None,
        "last_valid_value": None,
        "reference_gas_intensity": {"value": None, "target_intensity": None, "slope": None, "intercept": None},
        "first_cycle": {
            "value": None,
            "target_cycle": None,
            "slope": None,
            "intercept": None,
            "coefficients": None,
            "fit_degree": None,
        },
        "cycle_relative_mismatch": {
            "value": None,
            "target_basis": None,
            "slope": None,
            "intercept": None,
            "coefficients": None,
            "fit_degree": None,
        },
        "cycle_symmetric_mismatch": {
            "value": None,
            "target_basis": None,
            "slope": None,
            "intercept": None,
            "coefficients": None,
            "fit_degree": None,
        },
        "cycle_mean_intensity": {
            "value": None,
            "target_basis": None,
            "slope": None,
            "intercept": None,
            "coefficients": None,
            "fit_degree": None,
        },
        "cycle_intensity_weighted_mismatch": {
            "value": None,
            "target_basis": None,
            "slope": None,
            "intercept": None,
            "coefficients": None,
            "fit_degree": None,
        },
        "cycle_two_term_mean_mismatch": {
            "value": None,
            "target_mean_intensity": None,
            "target_mismatch": None,
            "mean_intensity_coefficient": None,
            "mismatch_coefficient": None,
            "intercept": None,
        },
        "cycle_plateau": {
            "value": None,
            "std_dev": None,
            "selected_cycles": [],
            "selected_count": 0,
            "target_mean_intensity": None,
            "target_cycle": None,
            "target_delta_rate": 0.0,
            "delta_span": None,
            "delta_rate_span": None,
            "delta_rate_coefficient": None,
            "fit_std_dev": None,
            "tail_slope": None,
            "full_slope": None,
            "basis": "delta_rate_asymptote",
        },
        "figures": {},
    }
    if require_partially_saturated and not _is_partially_saturated_collector(target):
        payload["reason"] = "not_partially_saturated"
        return payload
    if cycles is None:
        cycles, _ = get_cycles_for_selected_point(df, cycles_df, target.get("row_label"), target.get("target_col"))
    if cycles is None or cycles.empty:
        payload["reason"] = "no_cycle_data"
        return payload

    isotope_key = str(target.get("isotope_key", "")).strip()
    value_col, required_masses, reference_mass = _cycle_delta_context(cycles, isotope_key)
    if value_col is None or value_col not in cycles.columns:
        payload["reason"] = "missing_cycle_delta_column"
        return payload

    cycle_delta = pd.to_numeric(cycles[value_col], errors="coerce")
    cycle_numbers = pd.to_numeric(cycles["_cycle_order"], errors="coerce")
    intensity_cols = [col for col in _find_cycle_intensity_columns(cycles) if col in cycles.columns]
    intensity_for_mask = pd.DataFrame(index=cycles.index)
    for col in intensity_cols:
        intensity_for_mask[col] = _normalize_signal_intensity(cycles[col])
    sat_mask = _build_saturation_mask_from_intensity_df(intensity_for_mask, required_masses).reindex(
        cycles.index,
        fill_value=False,
    )
    escape_mask = _build_sample_gas_escape_mask(cycles, intensity_cols, required_masses).reindex(
        cycles.index,
        fill_value=False,
    )
    valid_mask = cycle_delta.notna() & cycle_numbers.notna() & ~sat_mask & ~escape_mask
    if not bool(valid_mask.any()):
        payload["reason"] = "no_valid_cycles_after_saturation_filter"
        return payload

    valid_delta = cycle_delta.loc[valid_mask]
    valid_cycles = cycle_numbers.loc[valid_mask]
    last_valid_idx = valid_cycles.index[-1]
    last_valid_cycle = float(cycle_numbers.loc[last_valid_idx])
    last_valid_value = float(cycle_delta.loc[last_valid_idx])
    payload["last_valid_cycle"] = int(last_valid_cycle) if last_valid_cycle.is_integer() else last_valid_cycle
    payload["last_valid_value"] = last_valid_value

    basis_frame = _cycle_44_basis_frame(cycles, intensity_cols)
    saturated_target_cycle_raw = cycle_numbers.dropna().min()
    saturated_target_cycle = float(saturated_target_cycle_raw) if pd.notna(saturated_target_cycle_raw) else 1.0
    fallback_stabilized_cycle = float(valid_cycles.max()) if not valid_cycles.empty else saturated_target_cycle
    _, first_coefficients, first_fit_degree = _polynomial_prediction(
        valid_cycles,
        valid_delta,
        fallback_stabilized_cycle,
        max_degree=2,
    )
    target_cycle, first_target_reason = _quadratic_horizontal_target(
        first_coefficients,
        fallback_stabilized_cycle,
    )
    first_value = (
        float(np.polyval(first_coefficients, target_cycle))
        if first_coefficients is not None and np.isfinite(target_cycle)
        else None
    )
    if first_value is not None and not np.isfinite(first_value):
        first_value = None
    first_slope = None
    first_intercept = None
    if first_coefficients is not None:
        if len(first_coefficients) >= 2:
            first_slope = float(first_coefficients[-2])
        if len(first_coefficients) >= 1:
            first_intercept = float(first_coefficients[-1])
    payload["first_cycle"] = {
        "value": first_value,
        "target_cycle": int(target_cycle) if float(target_cycle).is_integer() else target_cycle,
        "slope": first_slope,
        "intercept": first_intercept,
        "coefficients": first_coefficients,
        "fit_degree": first_fit_degree,
        "basis": "cycle_quadratic_horizontal",
        "target_reason": first_target_reason,
    }

    ref_col = None
    if reference_mass is not None:
        _, ref_col = _pick_mass_role_columns(cycles, intensity_cols, reference_mass)
    ref_intensity = (
        _normalize_signal_intensity(cycles[ref_col])
        if ref_col is not None and ref_col in cycles.columns
        else pd.Series(np.nan, index=cycles.index, dtype=float)
    )
    target_ref_raw = ref_intensity.loc[cycle_numbers.eq(saturated_target_cycle) & ref_intensity.notna()]
    if target_ref_raw.empty:
        target_ref_raw = ref_intensity.loc[sat_mask & ref_intensity.notna()]
    if target_ref_raw.empty:
        target_ref_raw = ref_intensity.dropna().head(1)
    target_ref = float(target_ref_raw.iloc[0]) if not target_ref_raw.empty else np.nan
    ref_value, ref_slope, ref_intercept = _linear_prediction(ref_intensity.loc[valid_mask], valid_delta, target_ref)
    payload["reference_gas_intensity"] = {
        "value": ref_value,
        "target_intensity": target_ref if np.isfinite(target_ref) else None,
        "slope": ref_slope,
        "intercept": ref_intercept,
        "reference_mass": reference_mass,
        "reference_column": ref_col,
    }

    target_basis_row = basis_frame.loc[cycle_numbers.eq(fallback_stabilized_cycle)] if np.isfinite(fallback_stabilized_cycle) else pd.DataFrame()
    if target_basis_row.empty:
        target_basis_row = basis_frame.loc[valid_mask].tail(1)
    if target_basis_row.empty:
        target_basis_row = basis_frame.tail(1)
    target_basis = target_basis_row.iloc[0] if not target_basis_row.empty else pd.Series(dtype=float)
    linear_basis_specs = {
        "cycle_relative_mismatch": ("relative_mismatch", "Relative mismatch"),
        "cycle_symmetric_mismatch": ("symmetric_mismatch", "Symmetric mismatch"),
        "cycle_mean_intensity": ("mean_intensity", "Mean intensity"),
        "cycle_intensity_weighted_mismatch": ("intensity_weighted_mismatch", "Intensity-weighted mismatch"),
    }
    for method_key, (basis_key, _label) in linear_basis_specs.items():
        basis_series = pd.to_numeric(basis_frame.get(basis_key, pd.Series(np.nan, index=cycles.index)), errors="coerce")
        fallback_basis_value = pd.to_numeric(pd.Series([target_basis.get(basis_key, np.nan)]), errors="coerce").iloc[0]
        basis_value, basis_coefficients, basis_fit_degree, target_basis_value, target_basis_reason = _curve_prediction_for_cycle_basis(
            basis_series.loc[valid_mask],
            valid_delta,
            float(fallback_basis_value) if np.isfinite(fallback_basis_value) else np.nan,
        )
        basis_slope = None
        basis_intercept = None
        if basis_coefficients is not None:
            if len(basis_coefficients) >= 2:
                basis_slope = float(basis_coefficients[-2])
            if len(basis_coefficients) >= 1:
                basis_intercept = float(basis_coefficients[-1])
        payload[method_key] = {
            "value": basis_value,
            "target_basis": float(target_basis_value) if target_basis_value is not None and np.isfinite(target_basis_value) else None,
            "slope": basis_slope,
            "intercept": basis_intercept,
            "coefficients": basis_coefficients,
            "fit_degree": basis_fit_degree,
            "basis": basis_key,
            "target_cycle": int(target_cycle) if float(target_cycle).is_integer() else target_cycle,
            "target_reason": target_basis_reason,
            "fallback_target_basis": float(fallback_basis_value) if np.isfinite(fallback_basis_value) else None,
        }

    target_mean = pd.to_numeric(pd.Series([target_basis.get("mean_intensity", np.nan)]), errors="coerce").iloc[0]
    target_mismatch = pd.to_numeric(pd.Series([target_basis.get("symmetric_mismatch", np.nan)]), errors="coerce").iloc[0]
    two_value, two_b1, two_b2, two_intercept = _two_term_prediction_for_cycle_basis(
        basis_frame["mean_intensity"].loc[valid_mask],
        basis_frame["symmetric_mismatch"].loc[valid_mask],
        valid_delta,
        float(target_mean) if np.isfinite(target_mean) else np.nan,
        float(target_mismatch) if np.isfinite(target_mismatch) else np.nan,
    )
    payload["cycle_two_term_mean_mismatch"] = {
        "value": two_value,
        "target_mean_intensity": float(target_mean) if np.isfinite(target_mean) else None,
        "target_mismatch": float(target_mismatch) if np.isfinite(target_mismatch) else None,
        "mean_intensity_coefficient": two_b1,
        "mismatch_coefficient": two_b2,
        "intercept": two_intercept,
        "basis": "mean_intensity+symmetric_mismatch",
    }
    payload["cycle_plateau"] = _late_cycle_plateau_estimate(
        valid_cycles,
        basis_frame["mean_intensity"].loc[valid_mask],
        valid_delta,
    )

    payload["available"] = first_value is not None or ref_value is not None or any(
        isinstance(payload.get(method), dict) and payload[method].get("value") is not None
        for method in CYCLE_LINEARITY_SATURATION_METHODS
    )
    if not payload["available"]:
        payload["reason"] = "insufficient_fit_points"

    if bool(include_figures) and go is not None:
        figures: dict[str, Any] = {}
        valid_label = "Valid cycles"
        target_label = "Reference-gas prediction"
        valid_cycle_numbers = cycle_numbers.loc[valid_mask]
        valid_sample44 = basis_frame["sample"].loc[valid_mask]
        valid_reference44 = basis_frame["reference"].loc[valid_mask]
        valid_mean44 = basis_frame["mean_intensity"].loc[valid_mask]
        valid_mismatch44 = basis_frame["symmetric_mismatch"].loc[valid_mask]
        d13_col, _, _ = _cycle_delta_context(cycles, "d13C")
        d18_col, _, _ = _cycle_delta_context(cycles, "d18O")
        valid_d13c = (
            pd.to_numeric(cycles[d13_col], errors="coerce").loc[valid_mask]
            if d13_col is not None and d13_col in cycles.columns
            else pd.Series(np.nan, index=valid_cycle_numbers.index)
        )
        valid_d18o = (
            pd.to_numeric(cycles[d18_col], errors="coerce").loc[valid_mask]
            if d18_col is not None and d18_col in cycles.columns
            else pd.Series(np.nan, index=valid_cycle_numbers.index)
        )
        valid_customdata = _cycle_customdata(
            valid_cycle_numbers,
            valid_sample44,
            valid_reference44,
            valid_mean44,
            valid_mismatch44,
            valid_d13c,
            valid_d18o,
        )
        if ref_value is not None and ref_col is not None:
            fig = go.Figure()
            valid_ref = ref_intensity.loc[valid_mask]
            fig.add_trace(
                go.Scatter(
                    x=_plot_values(valid_ref),
                    y=_plot_values(valid_delta),
                    mode="markers",
                    name=valid_label,
                    marker=_marker_with_colorbar(valid_cycle_numbers, "Cycle"),
                    customdata=valid_customdata,
                    hovertemplate=_cycle_hovertemplate("Reference intensity", isotope_key, "cycle number"),
                )
            )
            line_x, line_y = _fit_line_points(valid_ref, ref_slope, ref_intercept, target_ref)
            if line_x:
                fig.add_trace(
                    go.Scatter(
                    x=line_x,
                    y=line_y,
                    mode="lines",
                    name="Linear fit",
                    line=dict(color="#0f766e", width=3),
                    hovertemplate=(
                        "Linear fit using valid cycles<br>"
                        "x = reference-gas intensity<br>"
                        "y = isotope value<extra></extra>"
                    ),
                )
                )
            fig.add_trace(
                go.Scatter(
                    x=[target_ref],
                    y=[ref_value],
                    mode="markers",
                    name=target_label,
                    marker=dict(color="#dc2626", symbol="diamond", size=12),
                    hovertemplate=(
                        "Predicted saturated-cycle value<br>"
                        "Uses the fitted relation between valid-cycle isotope value "
                        "and reference-gas intensity.<br>"
                        "Reference intensity: %{x:.4g}<br>"
                        f"{isotope_key}: %{{y:.4g}}<extra></extra>"
                    ),
                )
            )
            fig.update_layout(
                xaxis_title=(
                    f"REF Int m/z {reference_mass} (V)"
                    if reference_mass is not None
                    else "Reference intensity (V)"
                ),
                yaxis_title=isotope_key,
                height=320,
                margin=dict(l=40, r=20, t=15, b=40),
                legend=dict(orientation="h", yanchor="top", y=-0.25, x=0.0),
                annotations=[
                    _inside_prediction_annotation(
                        valid_ref,
                        valid_delta,
                        target_ref,
                        ref_value,
                        f"({target_ref:.3f}, {ref_value:.3f}) Reference-gas prediction",
                    )
                ],
            )
            figures["reference_gas_intensity"] = to_json_compatible(fig.to_plotly_json())
        if first_value is not None:
            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=_plot_values(valid_cycles),
                    y=_plot_values(valid_delta),
                    mode="markers",
                    name=valid_label,
                    marker=_marker_with_colorbar(valid_mean44, "Mean44"),
                    customdata=valid_customdata,
                    hovertemplate=_cycle_hovertemplate("Cycle", isotope_key, "mean Samp44/Ref44 intensity"),
                )
            )
            line_x, line_y = _fit_curve_points(
                valid_cycles,
                (payload.get("first_cycle", {}) or {}).get("coefficients"),
                target_cycle,
            )
            if line_x:
                fig.add_trace(
                    go.Scatter(
                        x=line_x,
                        y=line_y,
                        mode="lines",
                        name="Curve fit",
                        line=dict(color="#0f766e", width=3),
                        hovertemplate=(
                            "Quadratic curve through valid cycles<br>"
                            "The red diamond is the fitted value where the curve becomes horizontal.<extra></extra>"
                        ),
                    )
                )
            fig.add_trace(
                go.Scatter(
                    x=[target_cycle],
                    y=[first_value],
                    mode="markers",
                    name="Stabilized cycle prediction",
                    marker=dict(color="#dc2626", symbol="diamond", size=12),
                    hovertemplate=(
                        "Predicted stabilized-cycle isotope value<br>"
                        "Cycle: %{x:.0f}<br>"
                        f"{isotope_key}: %{{y:.4g}}<extra></extra>"
                    ),
                )
            )
            fig.update_layout(
                xaxis_title="Cycle",
                yaxis_title=isotope_key,
                height=320,
                margin=dict(l=40, r=20, t=15, b=40),
                legend=dict(orientation="h", yanchor="top", y=-0.25, x=0.0),
                annotations=[
                    _inside_prediction_annotation(
                        valid_cycles,
                        valid_delta,
                        target_cycle,
                        first_value,
                        f"({target_cycle:.0f}, {first_value:.3f}) Stabilized cycle prediction",
                    )
                ],
            )
            figures["first_cycle"] = to_json_compatible(fig.to_plotly_json())
        for method_key, (basis_key, label) in linear_basis_specs.items():
            method_payload = payload.get(method_key, {})
            if not isinstance(method_payload, dict) or method_payload.get("value") is None:
                continue
            target_basis_value = method_payload.get("target_basis")
            predicted_value = method_payload.get("value")
            if target_basis_value is None or predicted_value is None:
                continue
            basis_series = pd.to_numeric(basis_frame.get(basis_key, pd.Series(np.nan, index=cycles.index)), errors="coerce")
            valid_basis = basis_series.loc[valid_mask]
            if basis_key in {"relative_mismatch", "symmetric_mismatch", "intensity_weighted_mismatch"}:
                color_values = _finite_color_values(valid_mean44, valid_cycle_numbers)
                color_title = "Mean44"
                color_label = "mean Samp44/Ref44 intensity"
            elif basis_key == "mean_intensity":
                color_values = _finite_color_values(valid_mismatch44, valid_cycle_numbers)
                color_title = "Mismatch"
                color_label = "symmetric Samp-Ref mismatch"
            else:
                color_values = valid_cycle_numbers
                color_title = "Cycle"
                color_label = "cycle number"
            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=_plot_values(valid_basis),
                    y=_plot_values(valid_delta),
                    mode="markers",
                    name=valid_label,
                    marker=_marker_with_colorbar(color_values, color_title),
                    customdata=valid_customdata,
                    hovertemplate=_cycle_hovertemplate(label, isotope_key, color_label),
                )
            )
            line_x, line_y = _fit_curve_points(
                valid_basis,
                method_payload.get("coefficients"),
                float(target_basis_value),
            )
            if line_x:
                fig.add_trace(
                    go.Scatter(
                        x=line_x,
                        y=line_y,
                        mode="lines",
                        name="Curve fit",
                        line=dict(color="#0f766e", width=3),
                        hovertemplate=(
                            f"One-variable quadratic curve fit<br>x = {label}<br>"
                            "y = isotope value from valid cycles<extra></extra>"
                        ),
                    )
                )
            fig.add_trace(
                go.Scatter(
                    x=[target_basis_value],
                    y=[predicted_value],
                    mode="markers",
                    name=f"{label} prediction",
                    marker=dict(color="#dc2626", symbol="diamond", size=12),
                    hovertemplate=(
                        f"Predicted horizontal-fit value from {label}<br>"
                        f"{label}: %{{x:.4g}}<br>"
                        f"{isotope_key}: %{{y:.4g}}<extra></extra>"
                    ),
                )
            )
            fig.update_layout(
                xaxis_title=label,
                yaxis_title=isotope_key,
                height=320,
                margin=dict(l=40, r=20, t=15, b=40),
                legend=dict(orientation="h", yanchor="top", y=-0.25, x=0.0),
                annotations=[
                    _inside_prediction_annotation(
                        valid_basis,
                        valid_delta,
                        float(target_basis_value),
                        float(predicted_value),
                        f"({float(target_basis_value):.3f}, {float(predicted_value):.3f}) {label} prediction",
                    )
                ],
            )
            figures[method_key] = to_json_compatible(fig.to_plotly_json())
        two_payload = payload.get("cycle_two_term_mean_mismatch", {})
        if isinstance(two_payload, dict) and two_payload.get("value") is not None:
            target_mean_value = two_payload.get("target_mean_intensity")
            target_mismatch_value = two_payload.get("target_mismatch")
            predicted_value = two_payload.get("value")
            b1 = two_payload.get("mean_intensity_coefficient")
            b2 = two_payload.get("mismatch_coefficient")
            intercept = two_payload.get("intercept")
            if (
                target_mean_value is not None
                and target_mismatch_value is not None
                and predicted_value is not None
                and b1 is not None
                and b2 is not None
                and intercept is not None
            ):
                valid_mean = pd.to_numeric(basis_frame["mean_intensity"].loc[valid_mask], errors="coerce")
                valid_mismatch = pd.to_numeric(basis_frame["symmetric_mismatch"].loc[valid_mask], errors="coerce")
                fig = go.Figure()
                fig.add_trace(
                    go.Scatter(
                        x=_plot_values(valid_mean),
                        y=_plot_values(valid_delta),
                        mode="markers",
                        name=valid_label,
                        marker=dict(
                            color=_plot_values(valid_mismatch),
                            colorscale="Viridis",
                            size=8,
                            showscale=True,
                            colorbar=dict(title="Mismatch"),
                        ),
                        customdata=valid_customdata,
                        hovertemplate=_cycle_hovertemplate(
                            "Mean intensity",
                            isotope_key,
                            "symmetric Samp-Ref mismatch",
                        ),
                    )
                )
                line_x = pd.to_numeric(
                    valid_mean,
                    errors="coerce",
                )
                fitted_y = float(intercept) + float(b1) * valid_mean + float(b2) * valid_mismatch
                fit_mask = np.isfinite(line_x) & np.isfinite(fitted_y)
                if bool(fit_mask.any()):
                    fit_frame = pd.DataFrame({"x": line_x.loc[fit_mask], "y": fitted_y.loc[fit_mask]}).sort_values("x")
                    fig.add_trace(
                        go.Scatter(
                            x=_plot_values(fit_frame["x"]),
                            y=_plot_values(fit_frame["y"]),
                            mode="lines",
                            name="Two-term fitted values",
                            line=dict(color="#0f766e", width=3),
                            hovertemplate=(
                                "Two-term fitted values<br>"
                                "The model is y = a + b1*Mean44 + b2*Mismatch.<br>"
                                "Each point on this line uses that cycle's own mean intensity and mismatch; "
                                "the line simply connects those fitted values in mean-intensity order.<extra></extra>"
                            ),
                        )
                    )
                fig.add_trace(
                    go.Scatter(
                        x=[target_mean_value],
                        y=[predicted_value],
                        mode="markers",
                        name="Two-term prediction",
                        marker=dict(color="#dc2626", symbol="diamond", size=12),
                        hovertemplate=(
                            "Two-term predicted saturated-cycle value<br>"
                            "Uses target mean intensity and target mismatch.<br>"
                            "Mean intensity: %{x:.4g}<br>"
                            f"{isotope_key}: %{{y:.4g}}<extra></extra>"
                        ),
                    )
                )
                fig.update_layout(
                    xaxis_title="Mean intensity",
                    yaxis_title=isotope_key,
                    height=320,
                    margin=dict(l=40, r=20, t=15, b=40),
                    legend=dict(orientation="h", yanchor="top", y=-0.25, x=0.0),
                    annotations=[
                        _inside_prediction_annotation(
                            valid_mean,
                            valid_delta,
                            float(target_mean_value),
                            float(predicted_value),
                            f"({float(target_mean_value):.3f}, {float(predicted_value):.3f}) Two-term prediction",
                        )
                    ],
                )
                figures["cycle_two_term_mean_mismatch"] = to_json_compatible(fig.to_plotly_json())
        plateau_payload = payload.get("cycle_plateau", {})
        if isinstance(plateau_payload, dict) and plateau_payload.get("value") is not None:
            predicted_value = plateau_payload.get("value")
            delta_rate_coefficient = plateau_payload.get("delta_rate_coefficient")
            selected_cycles = pd.to_numeric(
                pd.Series(plateau_payload.get("selected_cycles", [])),
                errors="coerce",
            ).dropna()
            if (
                predicted_value is not None
                and delta_rate_coefficient is not None
                and not selected_cycles.empty
            ):
                selected_cycle_values = selected_cycles.astype(float).tolist()
                rate_frame = _cycle_delta_rate_frame(valid_cycle_numbers, valid_mean44, valid_delta)
                plateau_mask = pd.to_numeric(rate_frame["cycle"], errors="coerce").isin(selected_cycle_values)
                rate_customdata = _cycle_customdata(
                    rate_frame["cycle"],
                    valid_sample44.reindex(rate_frame.index),
                    valid_reference44.reindex(rate_frame.index),
                    rate_frame["mean_intensity"],
                    valid_mismatch44.reindex(rate_frame.index),
                    valid_d13c.reindex(rate_frame.index),
                    valid_d18o.reindex(rate_frame.index),
                )
                fig = go.Figure()
                fig.add_trace(
                    go.Scatter(
                        x=_plot_values(rate_frame["delta_rate"]),
                        y=_plot_values(rate_frame["delta"]),
                        mode="markers",
                        name=valid_label,
                        marker=_marker_with_colorbar(rate_frame["cycle"], "Cycle"),
                        customdata=rate_customdata,
                        hovertemplate=_cycle_hovertemplate(
                            "Delta change/cycle",
                            isotope_key,
                            "cycle number",
                        ),
                    )
                )
                if bool(plateau_mask.any()):
                    selected_customdata = [
                        row
                        for row, keep in zip(
                            rate_customdata,
                            plateau_mask.to_numpy(dtype=bool),
                            strict=False,
                        )
                        if bool(keep)
                    ]
                    fig.add_trace(
                        go.Scatter(
                            x=_plot_values(rate_frame.loc[plateau_mask, "delta_rate"]),
                            y=_plot_values(rate_frame.loc[plateau_mask, "delta"]),
                            mode="markers",
                            name="Plateau cycles",
                            marker=dict(
                                color="rgba(14, 116, 144, 0.95)",
                                symbol="circle-open",
                                size=12,
                                line=dict(color="#0e7490", width=2),
                            ),
                            customdata=selected_customdata,
                            hovertemplate=_cycle_hovertemplate(
                                "Delta change/cycle",
                                isotope_key,
                                "selected plateau cycle",
                            ),
                        )
                    )
                line_candidates = pd.to_numeric(rate_frame["delta_rate"], errors="coerce").dropna()
                if not line_candidates.empty:
                    line_candidates = pd.concat([line_candidates, pd.Series([0.0])])
                    line_x = [float(line_candidates.min()), float(line_candidates.max())]
                    line_y = [
                        float(predicted_value) + float(delta_rate_coefficient) * value
                        for value in line_x
                    ]
                    fig.add_trace(
                        go.Scatter(
                            x=line_x,
                            y=line_y,
                            mode="lines",
                            name="Asymptote fit",
                            line=dict(color="#0f766e", width=3),
                            hovertemplate=(
                                "Delta-rate asymptote fit<br>"
                                "The target is where cycle-to-cycle delta change reaches zero.<extra></extra>"
                            ),
                        )
                    )
                fig.add_trace(
                    go.Scatter(
                        x=[0.0],
                        y=[predicted_value],
                        mode="markers",
                        name="Asymptote prediction",
                        marker=dict(color="#dc2626", symbol="diamond", size=12),
                        hovertemplate=(
                            "Delta-rate asymptote prediction<br>"
                            "Delta change/cycle: %{x:.4g}<br>"
                            f"{isotope_key}: %{{y:.4g}}<extra></extra>"
                        ),
                    )
                )
                fig.update_layout(
                    xaxis_title="Delta change per cycle",
                    yaxis_title=isotope_key,
                    height=320,
                    margin=dict(l=40, r=20, t=15, b=40),
                    legend=dict(orientation="h", yanchor="top", y=-0.25, x=0.0),
                    annotations=[
                        _inside_prediction_annotation(
                            rate_frame["delta_rate"],
                            rate_frame["delta"],
                            0.0,
                            float(predicted_value),
                            f"(0, {float(predicted_value):.3f}) Asymptote prediction",
                        )
                    ],
                )
                figures["cycle_plateau"] = to_json_compatible(fig.to_plotly_json())
        payload["figures"] = figures

    return to_json_compatible(payload)


def resolve_saturation_correction_value_for_target(
    df: pd.DataFrame,
    cycles_df: pd.DataFrame | None,
    target: dict[str, Any],
    method: Any,
) -> tuple[float | None, str, float | None]:
    selected_method = normalize_saturation_correction_method(method)
    value_raw: Any = None
    std_raw: Any = None
    if selected_method in {"cycle_mean", "first_valid_cycle"}:
        mean_payload = compute_cycle_mean_for_target(df, cycles_df, target)
        value_raw = (
            mean_payload.get("valid_mean")
            if selected_method == "cycle_mean"
            else mean_payload.get("selected_value")
        )
        std_raw = mean_payload.get("valid_std_dev")
    elif selected_method == "last_valid_cycle":
        correction = compute_saturation_correction_for_target(df, cycles_df, target, include_figures=False)
        value_raw = correction.get("last_valid_value")
    else:
        correction = compute_saturation_correction_for_target(df, cycles_df, target, include_figures=False)
        method_payload = correction.get(selected_method, {}) if isinstance(correction, dict) else {}
        if isinstance(method_payload, dict):
            value_raw = method_payload.get("value")
            std_raw = method_payload.get("std_dev")
    value = pd.to_numeric(pd.Series([value_raw]), errors="coerce").iloc[0]
    std_dev = pd.to_numeric(pd.Series([std_raw]), errors="coerce").iloc[0]
    resolved_std_dev = float(std_dev) if pd.notna(std_dev) and np.isfinite(std_dev) and float(std_dev) >= 0.0 else None
    if pd.notna(value) and np.isfinite(value):
        return float(value), selected_method, resolved_std_dev
    return None, selected_method, None


def build_selected_point_diagnostics_inline(
    df: pd.DataFrame,
    target: dict[str, Any],
    pre_row: pd.Series | None = None,
) -> str:
    if df is None or target["row_label"] not in df.index:
        return ""
    processed_row = df.loc[target["row_label"]]
    if isinstance(processed_row, pd.DataFrame):
        processed_row = processed_row.iloc[0]
    row_sources = [processed_row]
    if isinstance(pre_row, pd.Series):
        row_sources.append(pre_row)
    field_map = [
        ("Line", ["Line"]),
        ("Signal Intensity", [CYCLE1_SIGNAL_SAMP44_COL]),
        ("d18O values", ["d 18O/16O  Mean"]),
        ("d13C values", ["d 13C/12C  Mean"]),
        ("Leak Rate", ["leak_rate"]),
        ("Total CO2", ["total_co2"]),
        ("P gasses", ["p_gases"]),
        ("P no acid", ["p_no_acid"]),
        ("Date", ["Date", "Date_ordinal"]),
    ]

    def _has_value(value: Any) -> bool:
        if value is None:
            return False
        try:
            if pd.isna(value):
                return False
        except Exception:
            pass
        return str(value).strip() != ""

    def _get_value_from_series(src: pd.Series, candidates: list[str]) -> tuple[Any, str | None]:
        src_norm_map = {_normalize_column_key(col): col for col in src.index}
        for candidate in candidates:
            if candidate in src.index and _has_value(src[candidate]):
                return src[candidate], candidate
        for candidate in candidates:
            col = src_norm_map.get(_normalize_column_key(candidate))
            if col is not None and _has_value(src[col]):
                return src[col], col
        return None, None

    def _format_value(label: str, value: Any, source_col: str | None) -> str:
        if not _has_value(value):
            return "N/A"
        if label == "Date":
            parsed = pd.to_datetime(value, errors="coerce")
            if pd.notna(parsed):
                return parsed.strftime("%Y-%m-%d")
            if source_col and _normalize_column_key(source_col) == "date_ordinal":
                try:
                    parsed_ord = pd.Timestamp.fromordinal(int(float(value)))
                    return parsed_ord.strftime("%Y-%m-%d")
                except Exception:
                    pass
        if isinstance(value, (float, np.floating)) and np.isfinite(value):
            if label == "Line" and float(value).is_integer():
                return str(int(value))
            if label in {"Leak Rate", "Total CO2", "P gasses", "P no acid"}:
                return f"{float(value):.0f}"
            precision = 3 if label in {"d18O values", "d13C values"} else 4
            return f"{float(value):.{precision}f}"
        if isinstance(value, (int, np.integer)):
            return str(int(value))
        return str(value)

    parts: list[str] = []
    for label, candidates in field_map:
        selected_value = None
        selected_col = None
        for src in row_sources:
            selected_value, selected_col = _get_value_from_series(src, candidates)
            if _has_value(selected_value):
                break
        parts.append(f"**{label}:** `{_format_value(label, selected_value, selected_col)}`")
    return " | ".join(parts)


def find_interpolation_neighbors(
    df: pd.DataFrame,
    target: dict[str, Any],
    config: RangeConfig,
    edit_state: dict[str, Any] | None = None,
    sigma_level: float = 4.0,
    statistical_outlier_method: str = "Z-Score",
    iqr_multiplier: float = 1.5,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    target_col = str(target.get("target_col", ""))
    id1 = str(target.get("identifier_1", "")).strip()
    base = df.copy()
    if "Identifier 1" in base.columns and id1 != "":
        id_mask = base["Identifier 1"].astype(str).str.strip().eq(id1)
        if id_mask.any():
            base = base.loc[id_mask].copy()
    if base.empty or target["row_label"] not in base.index:
        return None, None

    sort_rows: list[tuple[tuple[Any, ...], Any]] = []
    for idx, row in base.iterrows():
        id2_val = row.get("Identifier 2", None)
        id2_num = _parse_numeric_token(id2_val)
        sort_key = (
            id2_num is None,
            float(id2_num) if id2_num is not None else float("inf"),
            "" if id2_val is None or pd.isna(id2_val) else str(id2_val),
            int(idx) if isinstance(idx, (int, np.integer)) else str(idx),
        )
        sort_rows.append((sort_key, idx))
    sort_rows.sort(key=lambda item: item[0])
    ordered_rows = [idx for _, idx in sort_rows]
    try:
        anchor_idx = ordered_rows.index(target["row_label"])
    except ValueError:
        return None, None

    status_series = base.get("Collector Status", pd.Series("", index=base.index)).astype(str).str.strip()
    target_col_norm = _normalize_column_key(target_col)
    if "13c" in target_col_norm:
        partial_iso_key = "d13C"
    elif "18o" in target_col_norm:
        partial_iso_key = "d18O"
    else:
        partial_iso_key = "any"
    partial_status_excluded = _partial_status_outlier_mask(
        base,
        config=config,
        edit_state=edit_state,
        isotope_key=partial_iso_key,
    )
    status_excluded = status_series.isin({"Failed Sample", "Fully Saturated Collectors"}) | partial_status_excluded
    range_excluded = _signal_out_of_range_mask(base.get(CYCLE1_SIGNAL_SAMP44_COL), config.signal_range)
    range_excluded = range_excluded | pd.to_numeric(base.get("d 13C/12C  Mean"), errors="coerce").lt(
        float(config.d13c_range[0])
    )
    range_excluded = range_excluded | pd.to_numeric(base.get("d 13C/12C  Mean"), errors="coerce").gt(
        float(config.d13c_range[1])
    )
    range_excluded = range_excluded | pd.to_numeric(base.get("d 18O/16O  Mean"), errors="coerce").lt(
        float(config.d18o_range[0])
    )
    range_excluded = range_excluded | pd.to_numeric(base.get("d 18O/16O  Mean"), errors="coerce").gt(
        float(config.d18o_range[1])
    )
    range_excluded = range_excluded | pd.to_numeric(base.get("leak_rate"), errors="coerce").lt(float(config.leak_range[0]))
    range_excluded = range_excluded | pd.to_numeric(base.get("leak_rate"), errors="coerce").gt(float(config.leak_range[1]))

    groups = _get_species_series(base)
    _, _, sigma_excluded = compute_statistical_outlier_masks(
        base,
        sigma_level=sigma_level,
        edit_state=edit_state,
        species_series=groups,
        method=statistical_outlier_method,
        iqr_multiplier=iqr_multiplier,
    )

    excluded_mask = status_excluded | range_excluded | sigma_excluded.reindex(base.index, fill_value=False).astype(bool)
    excluded_mask = _apply_manual_outlier_overrides(excluded_mask, edit_state, row_index=base.index)
    candidate_mask = ~excluded_mask

    prev_neighbor = None
    for i in range(anchor_idx - 1, -1, -1):
        idx = ordered_rows[i]
        if idx not in candidate_mask.index or not bool(candidate_mask.loc[idx]):
            continue
        value = pd.to_numeric(pd.Series([base.at[idx, target_col]]), errors="coerce").iloc[0]
        if pd.notna(value):
            prev_neighbor = {
                "row_label": str(idx),
                "identifier_2": "" if pd.isna(base.at[idx, "Identifier 2"]) else str(base.at[idx, "Identifier 2"]),
                "value": float(value),
            }
            break

    next_neighbor = None
    for i in range(anchor_idx + 1, len(ordered_rows)):
        idx = ordered_rows[i]
        if idx not in candidate_mask.index or not bool(candidate_mask.loc[idx]):
            continue
        value = pd.to_numeric(pd.Series([base.at[idx, target_col]]), errors="coerce").iloc[0]
        if pd.notna(value):
            next_neighbor = {
                "row_label": str(idx),
                "identifier_2": "" if pd.isna(base.at[idx, "Identifier 2"]) else str(base.at[idx, "Identifier 2"]),
                "value": float(value),
            }
            break

    return prev_neighbor, next_neighbor


def build_cycle_diagnostics_payload(
    session_id: str,
    df: pd.DataFrame,
    cycles_df: pd.DataFrame | None,
    target: dict[str, Any],
    config: RangeConfig,
    edit_state: dict[str, Any] | None = None,
    sigma_level: float = 4.0,
    statistical_outlier_method: str = "Z-Score",
    iqr_multiplier: float = 1.5,
) -> CycleDiagnosticsPayload:
    cycles, pre_row = get_cycles_for_selected_point(df, cycles_df, target["row_label"], target["target_col"])
    inline_summary = build_selected_point_diagnostics_inline(df, target, pre_row=pre_row)
    if cycles is None or cycles.empty:
        return CycleDiagnosticsPayload(
            session_id=session_id,
            target=target,
            inline_summary=inline_summary,
            cycle_mean={"reason": "no_cycle_data"},
        )

    intensity_cols = [col for col in _find_cycle_intensity_columns(cycles) if col in cycles.columns]
    mass_roles = {44: {"SMP": None, "STD": None}, 45: {"SMP": None, "STD": None}, 46: {"SMP": None, "STD": None}}
    for mass in [44, 45, 46]:
        smp_col, std_col = _pick_mass_role_columns(cycles, intensity_cols, mass)
        mass_roles[mass]["SMP"] = smp_col
        mass_roles[mass]["STD"] = std_col

    figure_json: dict[str, Any] = {}
    if go is not None:
        fig = go.Figure()
        x_cycles = pd.to_numeric(cycles["_cycle_order"], errors="coerce")
        mass_colors = {44: "#E67E22", 45: "#1E7D2B", 46: "#D4A017"}
        for mass in [44, 45, 46]:
            color = mass_colors[mass]
            smp_col = mass_roles[mass]["SMP"]
            std_col = mass_roles[mass]["STD"]
            if smp_col is not None:
                fig.add_trace(
                    go.Scatter(
                        x=x_cycles,
                        y=_normalize_signal_intensity(cycles[smp_col]),
                        mode="lines+markers",
                        name=f"{mass:.2f} m/z SMP",
                        line=dict(color=color, width=2, dash="solid"),
                        marker=dict(size=6),
                    )
                )
            if std_col is not None:
                fig.add_trace(
                    go.Scatter(
                        x=x_cycles,
                        y=_normalize_signal_intensity(cycles[std_col]),
                        mode="lines+markers",
                        name=f"{mass:.2f} m/z REF",
                        line=dict(color=color, width=2, dash="dash"),
                        marker=dict(size=6),
                    )
                )
        fig.update_layout(
            title="Cycle Intensities (Sample vs Reference Gas)",
            xaxis_title="Cycles",
            yaxis_title="Intensity (V)",
            height=460,
            margin=dict(l=20, r=20, t=40, b=20),
            legend=dict(orientation="h", yanchor="top", y=-0.25, x=0.0),
        )
        figure_json = to_json_compatible(fig.to_plotly_json())

    d13_col = _pick_cycle_value_column(cycles, "d 13C/12C  Mean", [r"d13", r"d ?13c", r"d45co2", r"\bd45\b"])
    d18_col = _pick_cycle_value_column(cycles, "d 18O/16O  Mean", [r"d18", r"d ?18o", r"d46co2", r"\bd46\b"])
    cycle_table = pd.DataFrame({"Cycle": pd.to_numeric(cycles["_cycle_order"], errors="coerce").astype("Int64")}, index=cycles.index)
    for mass in [44, 45, 46]:
        smp_col = mass_roles[mass]["SMP"]
        std_col = mass_roles[mass]["STD"]
        table_col = f"SMP Int m/z {mass} (V)"
        table_ref_col = f"REF Int m/z {mass} (V)"
        cycle_table[table_col] = _normalize_signal_intensity(cycles[smp_col]) if smp_col is not None else np.nan
        cycle_table[table_ref_col] = _normalize_signal_intensity(cycles[std_col]) if std_col is not None else np.nan
    if d13_col and d13_col in cycles.columns:
        cycle_table["d13C"] = pd.to_numeric(cycles[d13_col], errors="coerce")
    if d18_col and d18_col in cycles.columns:
        cycle_table["d18O"] = pd.to_numeric(cycles[d18_col], errors="coerce")
    intensity_for_mask = pd.DataFrame(index=cycles.index)
    for col in intensity_cols:
        intensity_for_mask[col] = _normalize_signal_intensity(cycles[col])
    sat_d13 = _build_saturation_mask_from_intensity_df(intensity_for_mask, [44, 45]).reindex(cycles.index, fill_value=False)
    sat_d18 = _build_saturation_mask_from_intensity_df(intensity_for_mask, [44, 45, 46]).reindex(cycles.index, fill_value=False)
    escape_d13 = _build_sample_gas_escape_mask(cycles, intensity_cols, [44, 45]).reindex(cycles.index, fill_value=False)
    escape_d18 = _build_sample_gas_escape_mask(cycles, intensity_cols, [44, 45, 46]).reindex(cycles.index, fill_value=False)
    cycle_table["Excluded d13C"] = (sat_d13 | escape_d13).to_numpy(dtype=bool)
    cycle_table["Excluded d18O"] = (sat_d18 | escape_d18).to_numpy(dtype=bool)
    cycle_table["Excluded (Saturation)"] = (sat_d13 | sat_d18).to_numpy(dtype=bool)
    cycle_table["Excluded (Sample Gas Escape)"] = (escape_d13 | escape_d18).to_numpy(dtype=bool)

    mean_payload = compute_cycle_mean_for_target(
        df,
        cycles_df,
        target,
        cycles=cycles,
    )
    saturation_correction = compute_saturation_correction_for_target(
        df,
        cycles_df,
        target,
        cycles=cycles,
        require_partially_saturated=False,
    )
    mean_payload["last_valid_cycle"] = saturation_correction.get("last_valid_cycle") or mean_payload.get("last_valid_cycle")
    mean_payload["last_valid_value"] = saturation_correction.get("last_valid_value")
    mean_payload["saturation_reference_gas_value"] = (
        saturation_correction.get("reference_gas_intensity", {}) or {}
    ).get("value")
    mean_payload["saturation_first_cycle_value"] = (
        saturation_correction.get("first_cycle", {}) or {}
    ).get("value")
    cycle_numbers = pd.to_numeric(cycle_table["Cycle"], errors="coerce")
    first_valid_cycle = pd.to_numeric(pd.Series([mean_payload.get("first_valid_cycle")]), errors="coerce").iloc[0]
    last_valid_cycle = pd.to_numeric(pd.Series([mean_payload.get("last_valid_cycle")]), errors="coerce").iloc[0]
    first_valid_cycle_mask = pd.Series(False, index=cycle_table.index, dtype=bool)
    last_valid_cycle_mask = pd.Series(False, index=cycle_table.index, dtype=bool)
    if np.isfinite(first_valid_cycle):
        first_valid_cycle_mask = cycle_numbers.eq(float(first_valid_cycle))
    if np.isfinite(last_valid_cycle):
        last_valid_cycle_mask = cycle_numbers.eq(float(last_valid_cycle))
    cycle_table["First Valid Cycle"] = first_valid_cycle_mask.to_numpy(dtype=bool)
    cycle_table["Last Valid Cycle"] = last_valid_cycle_mask.to_numpy(dtype=bool)

    prev_neighbor, next_neighbor = find_interpolation_neighbors(
        df,
        target,
        config=config,
        edit_state=edit_state,
        sigma_level=sigma_level,
        statistical_outlier_method=statistical_outlier_method,
        iqr_multiplier=iqr_multiplier,
    )
    mean_payload["prev_neighbor"] = prev_neighbor
    mean_payload["next_neighbor"] = next_neighbor
    target_payload = to_json_compatible(dict(target))
    target_payload["effective_outlier"] = is_row_outlier_effective(
        df,
        target["row_label"],
        config,
        edit_state=edit_state,
        sigma_level=sigma_level,
        statistical_outlier_method=statistical_outlier_method,
        iqr_multiplier=iqr_multiplier,
    )
    table_frame = cycle_table.reset_index(drop=True).replace({pd.NA: None}).where(pd.notnull(cycle_table.reset_index(drop=True)), None)
    return CycleDiagnosticsPayload(
        session_id=session_id,
        target=target_payload,
        inline_summary=inline_summary,
        figure=figure_json,
        saturation_correction=saturation_correction,
        table=table_frame.to_dict(orient="records"),
        cycle_mean=to_json_compatible(mean_payload),
    )
