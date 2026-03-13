from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..constants import CYCLE1_SIGNAL_SAMP44_COL, DEFAULT_SIGNAL_RANGE
from ..contracts import OutlierTable, ProcessingSummary, ProcessingSummaryMetric
from ..shared.dataframe import _get_species_series


@dataclass(slots=True)
class RangeConfig:
    signal_range: tuple[float, float] = DEFAULT_SIGNAL_RANGE
    leak_range: tuple[float, float] = (0.0, 1000.0)
    d13c_range: tuple[float, float] = (-10.0, 10.0)
    d18o_range: tuple[float, float] = (-10.0, 10.0)
    partial_saturated_outliers: bool = False


def _partial_saturation_isotope_masks(df: pd.DataFrame | None) -> dict[str, pd.Series]:
    if df is None:
        empty = pd.Series(dtype=bool)
        return {"d13C": empty, "d18O": empty, "any": empty}
    idx = df.index
    status_series = df.get("Collector Status", pd.Series("", index=idx)).astype(str).str.strip()
    partial_any = status_series == "Partially Saturated Collectors"
    d13_excl = pd.to_numeric(df.get("d13C Cycles Excluded", pd.Series(0, index=idx)), errors="coerce").fillna(0) > 0
    d18_excl = pd.to_numeric(df.get("d18O Cycles Excluded", pd.Series(0, index=idx)), errors="coerce").fillna(0) > 0
    d13_has_value = pd.to_numeric(
        df.get("d 13C/12C  Mean", pd.Series(np.nan, index=idx)),
        errors="coerce",
    ).notna()
    d18_has_value = pd.to_numeric(
        df.get("d 18O/16O  Mean", pd.Series(np.nan, index=idx)),
        errors="coerce",
    ).notna()
    d13_mask = partial_any & d13_has_value
    d18_mask = partial_any & d18_has_value
    unresolved = partial_any & ~(d13_mask | d18_mask)
    if unresolved.any():
        d13_mask = d13_mask | (unresolved & ~d13_excl)
        d18_mask = d18_mask | (unresolved & ~d18_excl)
        unresolved = partial_any & ~(d13_mask | d18_mask)
        d13_mask = d13_mask | unresolved
        d18_mask = d18_mask | unresolved
    return {"d13C": d13_mask.astype(bool), "d18O": d18_mask.astype(bool), "any": partial_any.astype(bool)}


def _signal_in_range_mask(
    signal_series: pd.Series,
    signal_range: tuple[float, float] = DEFAULT_SIGNAL_RANGE,
) -> pd.Series:
    values = pd.to_numeric(signal_series, errors="coerce")
    signal_low = float(signal_range[0])
    signal_high = float(signal_range[1])
    return values.ge(signal_low) & values.le(signal_high)


def _signal_out_of_range_mask(
    signal_series: pd.Series,
    signal_range: tuple[float, float] = DEFAULT_SIGNAL_RANGE,
) -> pd.Series:
    values = pd.to_numeric(signal_series, errors="coerce")
    signal_low = float(signal_range[0])
    signal_high = float(signal_range[1])
    return values.notna() & (values.lt(signal_low) | values.gt(signal_high))


def _range_outlier_mask(df: pd.DataFrame | None, config: RangeConfig | None = None) -> pd.Series:
    if df is None:
        return pd.Series(dtype=bool)
    cfg = config or RangeConfig()
    idx = df.index
    mask = pd.Series(False, index=idx, dtype=bool)
    d13_vals = pd.to_numeric(df.get("d 13C/12C  Mean", pd.Series(np.nan, index=idx)), errors="coerce")
    d18_vals = pd.to_numeric(df.get("d 18O/16O  Mean", pd.Series(np.nan, index=idx)), errors="coerce")
    leak_vals = pd.to_numeric(df.get("leak_rate", pd.Series(np.nan, index=idx)), errors="coerce")
    signal_vals = df.get(CYCLE1_SIGNAL_SAMP44_COL, pd.Series(np.nan, index=idx))
    mask = mask | (
        d13_vals.notna()
        & ((d13_vals < float(cfg.d13c_range[0])) | (d13_vals > float(cfg.d13c_range[1])))
    )
    mask = mask | (
        d18_vals.notna()
        & ((d18_vals < float(cfg.d18o_range[0])) | (d18_vals > float(cfg.d18o_range[1])))
    )
    mask = mask | _signal_out_of_range_mask(signal_vals, cfg.signal_range)
    mask = mask | (
        leak_vals.notna()
        & ((leak_vals < float(cfg.leak_range[0])) | (leak_vals > float(cfg.leak_range[1])))
    )
    return mask.astype(bool)


def _get_edited_row_tokens(edit_state: dict[str, Any] | None) -> set[str]:
    raw = (edit_state or {}).get("edited_rows", [])
    if isinstance(raw, set):
        return {str(x) for x in raw}
    if isinstance(raw, (list, tuple, pd.Index, np.ndarray)):
        return {str(x) for x in raw}
    if raw is None:
        return set()
    return {str(raw)}


def _is_row_edited(row_label: Any, edit_state: dict[str, Any] | None) -> bool:
    return str(row_label) in _get_edited_row_tokens(edit_state)


def _get_manual_outlier_override_map(edit_state: dict[str, Any] | None) -> dict[str, bool]:
    raw = (edit_state or {}).get("manual_outlier_overrides", {})
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, bool] = {}
    for key, value in raw.items():
        row_key = str(key).strip()
        if row_key == "":
            continue
        cleaned[row_key] = bool(value)
    return cleaned


def _apply_manual_outlier_overrides(
    mask: pd.Series | list[bool] | np.ndarray,
    edit_state: dict[str, Any] | None,
    row_index: pd.Index | None = None,
    apply_true: bool = True,
    apply_false: bool = True,
) -> pd.Series:
    if row_index is None:
        if isinstance(mask, pd.Series):
            mask_series = mask.copy()
        else:
            mask_series = pd.Series(mask)
    else:
        mask_series = pd.Series(mask, index=row_index)
    mask_series = mask_series.fillna(False).astype(bool)

    overrides = _get_manual_outlier_override_map(edit_state)
    if not overrides:
        return mask_series

    override_for_rows = pd.Series(
        [overrides.get(str(idx), None) for idx in mask_series.index],
        index=mask_series.index,
        dtype=object,
    )
    if apply_false:
        mask_series = mask_series.mask(override_for_rows.eq(False), False)
    if apply_true:
        mask_series = mask_series.mask(override_for_rows.eq(True), True)
    return mask_series


def _z_score_outlier_mask(values: pd.Series, sigma_level: float) -> pd.Series:
    mean_val = values.mean(skipna=True)
    std_val = values.std(skipna=True)
    if not np.isfinite(mean_val) or not np.isfinite(std_val) or std_val <= 0:
        return pd.Series(False, index=values.index, dtype=bool)
    return values.notna() & (
        (values < mean_val - (float(sigma_level) * std_val))
        | (values > mean_val + (float(sigma_level) * std_val))
    )


def _iqr_outlier_mask(values: pd.Series, iqr_multiplier: float) -> pd.Series:
    if values.notna().sum() <= 1:
        return pd.Series(False, index=values.index, dtype=bool)
    q1 = values.quantile(0.25)
    q3 = values.quantile(0.75)
    if not np.isfinite(q1) or not np.isfinite(q3):
        return pd.Series(False, index=values.index, dtype=bool)
    iqr = float(q3 - q1)
    lower = float(q1) - (float(iqr_multiplier) * iqr)
    upper = float(q3) + (float(iqr_multiplier) * iqr)
    return values.notna() & ((values < lower) | (values > upper))


def _partial_status_outlier_mask(
    df: pd.DataFrame | None,
    config: RangeConfig | None = None,
    edit_state: dict[str, Any] | None = None,
    isotope_key: str = "any",
) -> pd.Series:
    if df is None:
        return pd.Series(dtype=bool)
    cfg = config or RangeConfig()
    idx = df.index
    sat_masks = _partial_saturation_isotope_masks(df)
    partial_mask = sat_masks.get(isotope_key, sat_masks.get("any", pd.Series(False, index=idx, dtype=bool)))
    partial_mask = partial_mask.reindex(idx, fill_value=False).astype(bool)
    if bool(cfg.partial_saturated_outliers):
        base_mask = partial_mask.copy()
    else:
        base_mask = pd.Series(False, index=idx, dtype=bool)
    effective_mask = _apply_manual_outlier_overrides(
        base_mask,
        edit_state,
        row_index=idx,
        apply_true=True,
        apply_false=True,
    )
    return (effective_mask & partial_mask).astype(bool)


def compute_statistical_outlier_masks(
    df: pd.DataFrame,
    sigma_level: float,
    edit_state: dict[str, Any] | None = None,
    species_series: pd.Series | None = None,
    method: str = "Z-Score",
    iqr_multiplier: float = 1.5,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    if df is None or df.empty:
        empty = pd.Series(dtype=bool)
        return empty, empty, empty
    statistical_mask_d13 = pd.Series(False, index=df.index, dtype=bool)
    statistical_mask_d18 = pd.Series(False, index=df.index, dtype=bool)
    edited_mask = pd.Series(df.index.map(lambda idx: _is_row_edited(idx, edit_state)), index=df.index, dtype=bool)
    groups = species_series if species_series is not None else _get_species_series(df)
    groups_str = groups.astype(str)
    method_name = str(method or "Z-Score").strip().upper()
    if method_name == "IRQ":
        method_name = "IQR"
    if "Identifier 1" not in df.columns:
        empty = pd.Series(False, index=df.index, dtype=bool)
        return empty, empty, empty

    for identifier in df["Identifier 1"].dropna().astype(str).unique():
        id_mask = df["Identifier 1"].astype(str) == identifier
        for group_val in groups[id_mask].dropna().astype(str).unique():
            group_mask = id_mask & groups_str.eq(group_val)
            group_data = df.loc[group_mask]
            if len(group_data) <= 1:
                continue
            d13_vals = pd.to_numeric(group_data.get("d 13C/12C  Mean"), errors="coerce")
            d18_vals = pd.to_numeric(group_data.get("d 18O/16O  Mean"), errors="coerce")
            if method_name == "IQR":
                group_stat_outliers_d13 = _iqr_outlier_mask(d13_vals, iqr_multiplier)
                group_stat_outliers_d18 = _iqr_outlier_mask(d18_vals, iqr_multiplier)
            else:
                group_stat_outliers_d13 = _z_score_outlier_mask(d13_vals, sigma_level)
                group_stat_outliers_d18 = _z_score_outlier_mask(d18_vals, sigma_level)
            statistical_mask_d13.loc[group_data.index] = group_stat_outliers_d13.astype(bool).to_numpy()
            statistical_mask_d18.loc[group_data.index] = group_stat_outliers_d18.astype(bool).to_numpy()
    statistical_mask_d13 = statistical_mask_d13 & ~edited_mask
    statistical_mask_d18 = statistical_mask_d18 & ~edited_mask
    statistical_mask_d13 = _apply_manual_outlier_overrides(statistical_mask_d13, edit_state, row_index=df.index)
    statistical_mask_d18 = _apply_manual_outlier_overrides(statistical_mask_d18, edit_state, row_index=df.index)
    statistical_mask_combined = (statistical_mask_d13 | statistical_mask_d18).fillna(False).astype(bool)
    return (
        statistical_mask_d13.fillna(False).astype(bool),
        statistical_mask_d18.fillna(False).astype(bool),
        statistical_mask_combined,
    )


def _compute_statistical_outlier_mask(
    df: pd.DataFrame,
    sigma_level: float,
    edit_state: dict[str, Any] | None = None,
    species_series: pd.Series | None = None,
    method: str = "Z-Score",
    iqr_multiplier: float = 1.5,
) -> pd.Series:
    _, _, combined = compute_statistical_outlier_masks(
        df,
        sigma_level,
        edit_state=edit_state,
        species_series=species_series,
        method=method,
        iqr_multiplier=iqr_multiplier,
    )
    return combined


def _compute_within_ranges_mask(
    df: pd.DataFrame,
    config: RangeConfig,
    edit_state: dict[str, Any] | None = None,
) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=bool)
    edited_mask = pd.Series(df.index.map(lambda idx: _is_row_edited(idx, edit_state)), index=df.index, dtype=bool)
    status_series = df.get("Collector Status", pd.Series("", index=df.index)).astype(str).str.strip()
    not_fully_saturated = status_series != "Fully Saturated Collectors"
    not_partially_saturated = (
        status_series != "Partially Saturated Collectors"
        if config.partial_saturated_outliers
        else pd.Series(True, index=df.index, dtype=bool)
    )
    not_failed = status_series != "Failed Sample"
    within_ranges = (
        pd.to_numeric(df.get("d 13C/12C  Mean"), errors="coerce").between(*config.d13c_range, inclusive="both")
        & pd.to_numeric(df.get("d 18O/16O  Mean"), errors="coerce").between(*config.d18o_range, inclusive="both")
        & _signal_in_range_mask(df.get(CYCLE1_SIGNAL_SAMP44_COL), config.signal_range)
        & pd.to_numeric(df.get("leak_rate"), errors="coerce").between(*config.leak_range, inclusive="both")
        & not_fully_saturated
        & not_partially_saturated
        & not_failed
        & ~edited_mask
    )
    return ~_apply_manual_outlier_overrides(~within_ranges, edit_state, row_index=df.index)


def build_category_masks(
    df: pd.DataFrame,
    config: RangeConfig,
    edit_state: dict[str, Any] | None = None,
    sigma_level: float = 4.0,
    species_series: pd.Series | None = None,
    statistical_outlier_method: str = "Z-Score",
    iqr_multiplier: float = 1.5,
) -> dict[str, pd.Series]:
    idx = df.index
    edited_mask = pd.Series(idx.map(lambda row: _is_row_edited(row, edit_state)), index=idx, dtype=bool)
    status_series = df.get("Collector Status", pd.Series("", index=idx)).astype(str).str.strip()
    species_groups = species_series if species_series is not None else _get_species_series(df)
    manual_map = _get_manual_outlier_override_map(edit_state)

    masks = {
        "Statistical": _compute_statistical_outlier_mask(
            df,
            sigma_level,
            edit_state,
            species_groups,
            method=statistical_outlier_method,
            iqr_multiplier=iqr_multiplier,
        ),
        "d13C Range": _apply_manual_outlier_overrides(
            pd.to_numeric(df.get("d 13C/12C  Mean"), errors="coerce").lt(float(config.d13c_range[0]))
            | pd.to_numeric(df.get("d 13C/12C  Mean"), errors="coerce").gt(float(config.d13c_range[1])),
            edit_state,
            row_index=idx,
            apply_true=False,
            apply_false=True,
        )
        & ~edited_mask,
        "d18O Range": _apply_manual_outlier_overrides(
            pd.to_numeric(df.get("d 18O/16O  Mean"), errors="coerce").lt(float(config.d18o_range[0]))
            | pd.to_numeric(df.get("d 18O/16O  Mean"), errors="coerce").gt(float(config.d18o_range[1])),
            edit_state,
            row_index=idx,
            apply_true=False,
            apply_false=True,
        )
        & ~edited_mask,
        "Signal Intensity": _apply_manual_outlier_overrides(
            _signal_out_of_range_mask(df.get(CYCLE1_SIGNAL_SAMP44_COL), config.signal_range),
            edit_state,
            row_index=idx,
            apply_true=False,
            apply_false=True,
        )
        & ~edited_mask,
        "Leak Rate": _apply_manual_outlier_overrides(
            pd.to_numeric(df.get("leak_rate"), errors="coerce").lt(float(config.leak_range[0]))
            | pd.to_numeric(df.get("leak_rate"), errors="coerce").gt(float(config.leak_range[1])),
            edit_state,
            row_index=idx,
            apply_true=False,
            apply_false=True,
        )
        & ~edited_mask,
        "Failed Sample": _apply_manual_outlier_overrides(
            status_series == "Failed Sample",
            edit_state,
            row_index=idx,
            apply_true=False,
            apply_false=True,
        )
        & ~edited_mask,
        "Partially Saturated Collectors": _partial_status_outlier_mask(
            df,
            config,
            edit_state=edit_state,
            isotope_key="any",
        )
        & ~edited_mask,
        "Fully Saturated Collectors": _apply_manual_outlier_overrides(
            status_series == "Fully Saturated Collectors",
            edit_state,
            row_index=idx,
            apply_true=False,
            apply_false=True,
        )
        & ~edited_mask,
        "Manual Override": pd.Series(
            [manual_map.get(str(row), False) for row in idx],
            index=idx,
            dtype=bool,
        ),
    }
    return {key: value.fillna(False).astype(bool) for key, value in masks.items()}


def build_outlier_type_labels(
    df: pd.DataFrame,
    category_masks: dict[str, pd.Series],
) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=object)
    rows: list[str] = []
    for row_label in df.index:
        active_categories = [name for name, mask in category_masks.items() if bool(mask.get(row_label, False))]
        rows.append("; ".join(active_categories))
    return pd.Series(rows, index=df.index, dtype=object)


def build_processing_summary(
    data_to_process: pd.DataFrame,
    config: RangeConfig,
    edit_state: dict[str, Any] | None = None,
    standards_to_exclude: list[str] | None = None,
    sigma_level: float = 4.0,
    statistical_outlier_method: str = "Z-Score",
    iqr_multiplier: float = 1.5,
) -> ProcessingSummary:
    if data_to_process is None or data_to_process.empty:
        return ProcessingSummary()

    standards = {str(value) for value in standards_to_exclude or []}
    non_standards_mask = (
        ~data_to_process["Identifier 1"].astype(str).isin(standards)
        if "Identifier 1" in data_to_process.columns
        else pd.Series(True, index=data_to_process.index, dtype=bool)
    )
    data_without_standards = data_to_process.loc[non_standards_mask].copy()
    category_masks = build_category_masks(
        data_without_standards,
        config,
        edit_state=edit_state,
        sigma_level=sigma_level,
        statistical_outlier_method=statistical_outlier_method,
        iqr_multiplier=iqr_multiplier,
    )
    total_measurements = int(len(data_without_standards))
    if {"Identifier 1", "Identifier 2"}.issubset(data_without_standards.columns):
        total_unique_samples = int(
            data_without_standards.groupby(["Identifier 1", "Identifier 2"]).size().reset_index().shape[0]
        )
    else:
        total_unique_samples = total_measurements

    statistical_outliers = int(category_masks["Statistical"].sum())
    d13c_outliers = int(category_masks["d13C Range"].sum())
    d18o_outliers = int(category_masks["d18O Range"].sum())
    signal_outliers = int(category_masks["Signal Intensity"].sum())
    leak_outliers = int(category_masks["Leak Rate"].sum())
    failed_samples = int(category_masks["Failed Sample"].sum())
    partially_failed = int(category_masks["Partially Saturated Collectors"].sum())
    fully_saturated = int(category_masks["Fully Saturated Collectors"].sum())
    total_outliers = (
        statistical_outliers
        + d13c_outliers
        + d18o_outliers
        + signal_outliers
        + leak_outliers
        + failed_samples
        + fully_saturated
    )
    if config.partial_saturated_outliers:
        total_outliers += partially_failed
    final_analyses = max(total_measurements - total_outliers, 0)

    metrics = [
        ProcessingSummaryMetric(
            metric="Total Unique Samples",
            value=total_unique_samples,
            details="(excluding standards)",
        ),
        ProcessingSummaryMetric(
            metric="Total Measurements",
            value=total_measurements,
            details="(excluding standards)",
        ),
    ]
    optional_counts = [
        ("Statistical Outliers", statistical_outliers),
        ("d13C Range Outliers", d13c_outliers),
        ("d18O Range Outliers", d18o_outliers),
        ("Signal Intensity Outliers", signal_outliers),
        ("Leak Rate Outliers", leak_outliers),
    ]
    for label, count in optional_counts:
        if count > 0 and total_measurements > 0:
            metrics.append(
                ProcessingSummaryMetric(
                    metric=label,
                    value=count,
                    details=f"({(count / total_measurements) * 100:.1f}% of measurements)",
                )
            )
    for label, count in [
        ("Failed Samples", failed_samples),
        ("Partially Failed (Recovered Mean)", partially_failed),
        ("Fully Saturated Collectors", fully_saturated),
    ]:
        details = (
            f"({(count / total_measurements) * 100:.1f}% of measurements)"
            if total_measurements > 0
            else ""
        )
        metrics.append(ProcessingSummaryMetric(metric=label, value=count, details=details))
    metrics.append(
        ProcessingSummaryMetric(
            metric="Final Analyses",
            value=final_analyses,
            details="(Total measurements - outliers)",
        )
    )
    return ProcessingSummary(
        total_unique_samples=total_unique_samples,
        total_measurements=total_measurements,
        statistical_outliers=statistical_outliers,
        d13c_range_outliers=d13c_outliers,
        d18o_range_outliers=d18o_outliers,
        signal_intensity_outliers=signal_outliers,
        leak_rate_outliers=leak_outliers,
        failed_samples=failed_samples,
        partially_failed_recovered_mean=partially_failed,
        fully_saturated_collectors=fully_saturated,
        final_analyses=final_analyses,
        metrics=metrics,
    )


def _table_rows_from_mask(
    df: pd.DataFrame,
    mask: pd.Series,
    species_col: str,
) -> list[dict[str, Any]]:
    rows_df = df.loc[mask.fillna(False).astype(bool)].copy()
    if rows_df.empty:
        return []
    preferred = [
        "Identifier 1",
        "Identifier 2",
        species_col,
        "d 13C/12C  Mean",
        "d 18O/16O  Mean",
        CYCLE1_SIGNAL_SAMP44_COL,
        "leak_rate",
        "Collector Status",
        "d13C Cycles Excluded",
        "d18O Cycles Excluded",
    ]
    present = [col for col in preferred if col in rows_df.columns]
    table = rows_df[present].replace({pd.NA: None}).where(pd.notnull(rows_df[present]), None)
    if species_col in table.columns and species_col != "Species":
        table = table.rename(columns={species_col: "Species"})
    return table.to_dict(orient="records")


def build_outlier_tables(
    df: pd.DataFrame,
    category_masks: dict[str, pd.Series],
    species_col: str,
    scope_title: str | None = None,
) -> list[OutlierTable]:
    scope = scope_title or "All Data"
    tables: list[OutlierTable] = []
    ordered_keys = [
        "Statistical",
        "d13C Range",
        "d18O Range",
        "Signal Intensity",
        "Leak Rate",
        "Partially Saturated Collectors",
        "Fully Saturated Collectors",
        "Failed Sample",
        "Manual Override",
    ]
    for name in ordered_keys:
        mask = category_masks.get(name)
        if mask is None:
            continue
        tables.append(
            OutlierTable(
                name=name,
                title=f"{scope} - {name}",
                rows=_table_rows_from_mask(df, mask, species_col),
            )
        )
    return tables


def is_row_outlier_effective(
    df: pd.DataFrame,
    row_label: Any,
    config: RangeConfig,
    edit_state: dict[str, Any] | None = None,
    sigma_level: float = 4.0,
    statistical_outlier_method: str = "Z-Score",
    iqr_multiplier: float = 1.5,
) -> bool:
    if df is None or row_label not in df.index:
        override = _get_manual_outlier_override_map(edit_state).get(str(row_label))
        return bool(override) if override is not None else False
    masks = build_category_masks(
        df,
        config,
        edit_state=edit_state,
        sigma_level=sigma_level,
        statistical_outlier_method=statistical_outlier_method,
        iqr_multiplier=iqr_multiplier,
    )
    computed = any(bool(mask.get(row_label, False)) for mask in masks.values())
    override = _get_manual_outlier_override_map(edit_state).get(str(row_label))
    return bool(override) if override is not None else computed
