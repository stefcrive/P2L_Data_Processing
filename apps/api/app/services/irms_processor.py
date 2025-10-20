from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import numpy as np


pd.options.mode.copy_on_write = True


def _find_repo_file(filename: str) -> Path | None:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / filename
        if candidate.exists():
            return candidate
    return None


def extract_info_values(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in [
        "acid_temp",
        "leak_rate",
        "p_no_acid",
        "p_gases",
        "total_co2",
        "co2_after_exp",
        "left_mbar",
        "right_mbar",
        "left_pos",
        "right_pos",
        "vm1_after_transfer",
    ]:
        if col not in df.columns:
            df[col] = np.nan

    patterns: dict[str, str] = {
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

    # vectorized-safe parse via apply to avoid iterrows cost on large dfs
    info_series = df.get("Information", pd.Series(index=df.index, dtype="object")).astype(str)
    for col, pat in patterns.items():
        df[col] = info_series.str.extract(pat, expand=False).astype(float)
    return df


def identify_outliers_sigma(data: pd.Series, sigma_level: float) -> pd.Series:
    mu = data.mean()
    sd = data.std()
    if pd.isna(mu) or pd.isna(sd) or sd == 0:
        return pd.Series(False, index=data.index)
    upper = mu + sigma_level * sd
    lower = mu - sigma_level * sd
    return (data > upper) | (data < lower)


def identify_outliers_iqr(data: pd.Series, iqr_multiplier: float = 1.5) -> pd.Series:
    q1 = data.quantile(0.25)
    q3 = data.quantile(0.75)
    iqr = q3 - q1
    if pd.isna(iqr) or iqr == 0:
        return pd.Series(False, index=data.index)
    lower = q1 - iqr_multiplier * iqr
    upper = q3 + iqr_multiplier * iqr
    base = pd.Series(False, index=data.index)
    mask = data.notna()
    base.loc[mask] = (data.loc[mask] < lower) | (data.loc[mask] > upper)
    return base


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt"}:
        try:
            return pd.read_csv(path)
        except Exception:
            # fallback with python engine for odd delimiters
            return pd.read_csv(path, engine="python")
    # Excel
    if suffix == ".xls":
        # Explicitly use xlrd for legacy Excel format
        try:
            return pd.read_excel(path, engine="xlrd")
        except Exception:
            return pd.read_excel(path)
    if suffix == ".xlsx":
        try:
            return pd.read_excel(path, engine="openpyxl")
        except Exception:
            return pd.read_excel(path)
    # Fallback: let pandas infer
    return pd.read_excel(path)


def _load_standards() -> pd.DataFrame | None:
    p = _find_repo_file("Standards.csv")
    if not p:
        return None
    try:
        return pd.read_csv(p)
    except Exception:
        return None


def process_file(file_path: str) -> Dict[str, Any]:
    """Process an IRMS data file and return a compact JSON-friendly summary.

    The goal is to mirror the data preparation described in IRMS_results_processing.md
    without any Streamlit UI, keeping payload small for frontend display.
    """
    src = Path(file_path)
    df = _read_table(src)

    # Standardize and enrich
    df = df.convert_dtypes()
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%y", errors="coerce")
        df["Date_ordinal"] = pd.to_numeric(df["Date"].map(lambda x: x.toordinal() if pd.notnull(x) else None))

    df = extract_info_values(df)

    # Columns of interest (handle possible variations/missing)
    c13 = "d 13C/12C  Mean"
    c18 = "d 18O/16O  Mean"
    id1 = "Identifier 1"
    id2 = "Identifier 2"

    # Sample counts per Identifier 1
    sample_counts: List[Dict[str, Any]] = []
    if id1 in df.columns and id2 in df.columns:
        grp = df.groupby(id1).agg({id2: "nunique", id1: "count"}).rename(columns={id2: "unique_samples", id1: "total_measurements"})
        total_measurements = int(grp["total_measurements"].sum()) if len(grp) else 0
        for rec in grp.reset_index().to_dict(orient="records"):
            pct = float(rec["total_measurements"]) / total_measurements * 100 if total_measurements else 0.0
            sample_counts.append(
                {
                    "identifier": rec[id1],
                    "unique_samples": int(rec["unique_samples"]),
                    "total_measurements": int(rec["total_measurements"]),
                    "measurements_pct": round(pct, 2),
                }
            )

    # Isotope ranges
    def _range(col: str) -> dict | None:
        if col not in df.columns:
            return None
        series = pd.to_numeric(df[col], errors="coerce")
        if series.dropna().empty:
            return None
        return {"min": float(series.min()), "max": float(series.max())}

    isotopes = {
        "d13c_range": _range(c13),
        "d18o_range": _range(c18),
    }

    # Standards summary if present
    standards_csv = _load_standards()
    standards_summary: List[Dict[str, Any]] = []
    if standards_csv is not None and id1 in df.columns:
        known = set(standards_csv["Standard"].astype(str).unique())
        present = sorted(set(df[id1].dropna().astype(str).unique()) & known)
        for std in present:
            sub = df[df[id1].astype(str) == std]
            entry: Dict[str, Any] = {
                "standard": std,
                "count": int(len(sub)),
            }
            if c13 in sub.columns:
                s = pd.to_numeric(sub[c13], errors="coerce")
                entry["d13c_mean"] = float(s.mean()) if not s.dropna().empty else None
                entry["d13c_std"] = float(s.std()) if not s.dropna().empty else None
            if c18 in sub.columns:
                s = pd.to_numeric(sub[c18], errors="coerce")
                entry["d18o_mean"] = float(s.mean()) if not s.dropna().empty else None
                entry["d18o_std"] = float(s.std()) if not s.dropna().empty else None
            standards_summary.append(entry)

    # Date range
    date_min = None
    date_max = None
    if "Date" in df.columns:
        non_null = df["Date"].dropna()
        if not non_null.empty:
            date_min = str(pd.to_datetime(non_null.min()).date())
            date_max = str(pd.to_datetime(non_null.max()).date())

    # Build compact result
    result: Dict[str, Any] = {
        "rows": int(len(df)),
        "cols": int(len(df.columns)),
        "columns": list(map(str, df.columns.tolist())),
        "date_range": {"min": date_min, "max": date_max},
        "isotopes": isotopes,
        "sample_counts": sample_counts,
        "standards": standards_summary,
    }

    return result
