from __future__ import annotations

from typing import Any

import pandas as pd


def normalize_identifier1_name_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, str] = {}
    for source, target in raw.items():
        source_label = str(source).strip()
        target_label = str(target).strip()
        if not source_label or not target_label or source_label == target_label:
            continue
        normalized[source_label] = target_label
    return normalized


def apply_identifier1_name_map(
    df: pd.DataFrame,
    identifier1_name_map: dict[str, str] | None,
) -> pd.DataFrame:
    """Apply the Import-page Identifier 1 aliases to a working dataframe."""
    if df is None or df.empty or "Identifier 1" not in df.columns:
        return df
    normalized_map = normalize_identifier1_name_map(identifier1_name_map)
    if not normalized_map:
        return df
    work = df.copy()
    identifiers = work["Identifier 1"].fillna("").astype(str).map(str.strip)
    work["Identifier 1"] = identifiers.map(lambda value: normalized_map.get(value, value))
    return work


def mapped_identifier1(value: Any, identifier1_name_map: dict[str, str] | None) -> str:
    label = str(value).strip()
    return normalize_identifier1_name_map(identifier1_name_map).get(label, label)
