from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

try:
    import plotly.graph_objects as go
except ModuleNotFoundError:  # pragma: no cover - optional for logic-only tests
    go = None

from ..constants import CYCLE1_SIGNAL_SAMP44_COL
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


def get_global_signal_intensity_mean(df: pd.DataFrame, default_value: float = 15.0) -> float:
    if df is None or CYCLE1_SIGNAL_SAMP44_COL not in df.columns:
        return float(default_value)
    vals = pd.to_numeric(df[CYCLE1_SIGNAL_SAMP44_COL], errors="coerce")
    vals = vals[np.isfinite(vals)]
    if vals.empty:
        return float(default_value)
    mean_val = float(vals.mean())
    return mean_val if np.isfinite(mean_val) and mean_val > 0 else float(default_value)


def _is_partially_saturated_collector(target: dict[str, Any]) -> bool:
    return str(target.get("collector_status", "")).strip().lower() == "partially saturated collectors"


def compute_cycle_mean_for_target(
    df: pd.DataFrame,
    cycles_df: pd.DataFrame | None,
    target: dict[str, Any],
    correct_linearity: bool = False,
    target_intensity: float = 15.0,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mean": None,
        "valid_mean": None,
        "valid_cycles": 0,
        "method": "valid_cycle_mean",
        "linearity_applied": False,
        "linearity_points": 0,
        "linearity_target_intensity": float(target_intensity),
        "intensity_col": None,
        "fit_x": [],
        "fit_y": [],
        "linearity_prediction": None,
        "selected_cycle": None,
        "selected_value": None,
        "reason": "",
    }
    is_partially_saturated = _is_partially_saturated_collector(target)
    apply_linearity = bool(correct_linearity)
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
    valid_mask = cycle_delta.notna() & ~sat_mask
    valid_delta = cycle_delta[valid_mask]
    if valid_delta.empty:
        result["reason"] = "no_valid_cycles_after_saturation_filter"
        return result

    valid_mean = float(valid_delta.mean())
    result["mean"] = valid_mean
    result["valid_mean"] = valid_mean
    valid_cycles = int(valid_delta.shape[0])
    result["valid_cycles"] = valid_cycles
    first_valid_idx = valid_delta.index[0]
    first_valid_value = float(valid_delta.iloc[0])
    first_valid_cycle = pd.to_numeric(pd.Series([cycles.loc[first_valid_idx, "_cycle_order"]]), errors="coerce").iloc[0]

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

    intensity_col = _pick_cycle_sample_intensity_column(cycles, intensity_cols, [44])
    result["intensity_col"] = intensity_col
    if intensity_col is None:
        if not apply_linearity:
            return result
        result["reason"] = "missing_intensity_column"
        return result

    intensity_vals = _normalize_signal_intensity(cycles[intensity_col])
    x = pd.to_numeric(intensity_vals[valid_mask], errors="coerce")
    y = valid_delta.reindex(x.index)
    xy_mask = x.notna() & y.notna()
    x = x[xy_mask]
    y = y[xy_mask]
    result["fit_x"] = x.astype(float).tolist()
    result["fit_y"] = y.astype(float).tolist()
    result["linearity_points"] = int(x.shape[0])
    if not apply_linearity:
        return result
    if x.shape[0] < 2 or x.nunique(dropna=True) < 2:
        result["reason"] = "insufficient_points_for_linearity_fit"
        return result

    try:
        slope, intercept = np.polyfit(x.to_numpy(dtype=float), y.to_numpy(dtype=float), 1)
        predicted = float(slope * float(target_intensity) + intercept)
        if np.isfinite(predicted):
            result["mean"] = predicted
            result["linearity_applied"] = True
            result["method"] = "linearity_extrapolated_to_target_intensity"
            result["linearity_prediction"] = predicted
            result["linearity_slope"] = float(slope)
            result["linearity_intercept"] = float(intercept)
            return result
    except Exception:
        pass
    result["reason"] = "linearity_fit_failed"
    return result


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
    target_intensity: float | None = None,
    correct_linearity: bool = False,
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
    cycle_table["Excluded d13C"] = sat_d13.to_numpy(dtype=bool)
    cycle_table["Excluded d18O"] = sat_d18.to_numpy(dtype=bool)
    cycle_table["Excluded (Saturation)"] = cycle_table["Excluded d13C"] | cycle_table["Excluded d18O"]

    mean_payload = compute_cycle_mean_for_target(
        df,
        cycles_df,
        target,
        correct_linearity=correct_linearity,
        target_intensity=float(target_intensity) if target_intensity is not None else get_global_signal_intensity_mean(df),
    )
    selected_cycle = pd.to_numeric(pd.Series([mean_payload.get("selected_cycle")]), errors="coerce").iloc[0]
    selected_cycle_mask = pd.Series(False, index=cycle_table.index, dtype=bool)
    if np.isfinite(selected_cycle):
        cycle_numbers = pd.to_numeric(cycle_table["Cycle"], errors="coerce")
        selected_cycle_mask = cycle_numbers.eq(float(selected_cycle))
    cycle_table["Set Value Cycle"] = selected_cycle_mask.to_numpy(dtype=bool)

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
        table=table_frame.to_dict(orient="records"),
        cycle_mean=to_json_compatible(mean_payload),
    )
