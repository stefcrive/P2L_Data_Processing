from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .constants import ISOTYPE_D13C, ISOTYPE_D18O

_TYPE_MAP = {
    "VPDB(13C)": ISOTYPE_D13C,
    "VSMOW(18O)": ISOTYPE_D18O,
    "dVPDB(13C)": ISOTYPE_D13C,
    "dVSMOW(18O)": ISOTYPE_D18O,
    "?VPDB(13C)": ISOTYPE_D13C,
    "?VSMOW(18O)": ISOTYPE_D18O,
    "δVPDB(13C)": ISOTYPE_D13C,
    "δVSMOW(18O)": ISOTYPE_D18O,
    "??VPDB(13C)": ISOTYPE_D13C,
    "??VSMOW(18O)": ISOTYPE_D18O,
}


def default_standards_path() -> Path:
    return Path(__file__).resolve().parents[3] / "standards.csv"


def normalize_standards_frame(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if "Isotopic_Value_Type" in work.columns:
        work["Isotopic_Value_Type"] = (
            work["Isotopic_Value_Type"].astype(str).str.strip().replace(_TYPE_MAP)
        )
    return work


@lru_cache(maxsize=4)
def load_standards(path: str | Path | None = None) -> pd.DataFrame:
    target = Path(path) if path is not None else default_standards_path()
    return normalize_standards_frame(pd.read_csv(target, encoding="utf-8"))


@dataclass(slots=True)
class StandardsRepository:
    frame: pd.DataFrame
    source_path: Path | None = None

    @classmethod
    def default(cls, path: str | Path | None = None) -> "StandardsRepository":
        target = Path(path) if path is not None else default_standards_path()
        return cls(frame=load_standards(target), source_path=target)

    def standards_list(self) -> list[str]:
        if "Standard" not in self.frame.columns:
            return []
        return sorted(self.frame["Standard"].dropna().astype(str).unique().tolist())

    def get_true_value(self, standard_name: str, isotopic_type: str) -> float:
        match = self.frame[
            (self.frame["Standard"] == standard_name)
            & (self.frame["Isotopic_Value_Type"] == isotopic_type)
        ]
        if match.empty:
            raise ValueError(
                f"True value not found for {standard_name!r} with type {isotopic_type!r}"
            )
        return float(match["Value"].iloc[0])
