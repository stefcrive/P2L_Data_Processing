from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .constants import DEFAULT_STANDARDS_DB_PATH, ISOTYPE_D13C, ISOTYPE_D18O

_TYPE_MAP = {
    "VPDB(13C)": ISOTYPE_D13C,
    "VSMOW(18O)": ISOTYPE_D18O,
    "dVPDB(13C)": ISOTYPE_D13C,
    "dVSMOW(18O)": ISOTYPE_D18O,
    "?VPDB(13C)": ISOTYPE_D13C,
    "?VSMOW(18O)": ISOTYPE_D18O,
    "\u03b4VPDB(13C)": ISOTYPE_D13C,
    "\u03b4VSMOW(18O)": ISOTYPE_D18O,
    "\u00ce\u00b4VPDB(13C)": ISOTYPE_D13C,
    "\u00ce\u00b4VSMOW(18O)": ISOTYPE_D18O,
    "??VPDB(13C)": ISOTYPE_D13C,
    "??VSMOW(18O)": ISOTYPE_D18O,
}

_STANDARDS_TABLE = "standards_official_values"
_DELETED_STANDARDS_TABLE = "standards_deleted_official_values"


def _normalize_standard_name(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_isotopic_type(value: Any) -> str:
    token = str(value or "").strip()
    return str(_TYPE_MAP.get(token, token)).strip()


def default_standards_path() -> Path:
    override = os.getenv("IRMS_STANDARDS_CSV_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "standards.csv"


def default_standards_db_path() -> Path:
    override = os.getenv("IRMS_STANDARDS_DB_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return (Path(__file__).resolve().parents[3] / DEFAULT_STANDARDS_DB_PATH).resolve()


def normalize_standards_frame(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if "Standard" in work.columns:
        work["Standard"] = work["Standard"].apply(_normalize_standard_name)
    if "Isotopic_Value_Type" in work.columns:
        work["Isotopic_Value_Type"] = work["Isotopic_Value_Type"].apply(_normalize_isotopic_type)
    if "Value" in work.columns:
        work["Value"] = pd.to_numeric(work["Value"], errors="coerce")
    if "Source" in work.columns:
        work["Source"] = work["Source"].astype(str).str.strip()
    return work


def _connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_STANDARDS_TABLE} (
            standard TEXT NOT NULL,
            isotopic_value_type TEXT NOT NULL,
            value REAL NOT NULL,
            source TEXT NOT NULL DEFAULT 'database',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (standard, isotopic_value_type)
        )
        """
    )
    try:
        conn.commit()
    finally:
        cursor.close()
    cursor = conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_DELETED_STANDARDS_TABLE} (
            standard TEXT NOT NULL,
            isotopic_value_type TEXT NOT NULL,
            deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (standard, isotopic_value_type)
        )
        """
    )
    try:
        conn.commit()
    finally:
        cursor.close()


def _table_row_count(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(f"SELECT COUNT(*) FROM {_STANDARDS_TABLE}")
    try:
        row = cursor.fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        cursor.close()


def _coerce_float(value: Any) -> float | None:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(parsed):
        return None
    numeric = float(parsed)
    return numeric if np.isfinite(numeric) else None


def _rows_from_csv(path: Path) -> list[tuple[str, str, float, str]]:
    frame = normalize_standards_frame(pd.read_csv(path, encoding="utf-8"))
    required_columns = {"Standard", "Isotopic_Value_Type", "Value"}
    if not required_columns.issubset(frame.columns):
        return []
    rows: list[tuple[str, str, float, str]] = []
    for record in frame.to_dict(orient="records"):
        standard = _normalize_standard_name(record.get("Standard"))
        isotopic_type = _normalize_isotopic_type(record.get("Isotopic_Value_Type"))
        value = _coerce_float(record.get("Value"))
        if not standard or not isotopic_type or value is None:
            continue
        rows.append((standard, isotopic_type, value, "standards.csv"))
    return rows


def _seed_database_from_csv(conn: sqlite3.Connection, csv_path: Path) -> None:
    rows = _rows_from_csv(csv_path)
    if not rows:
        return
    cursor = conn.executemany(
        f"""
        INSERT INTO {_STANDARDS_TABLE} (
            standard,
            isotopic_value_type,
            value,
            source,
            updated_at
        )
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(standard, isotopic_value_type)
        DO UPDATE SET
            value = excluded.value,
            source = excluded.source,
            updated_at = CURRENT_TIMESTAMP
        """,
        rows,
    )
    try:
        conn.commit()
    finally:
        cursor.close()


def _insert_missing_rows_from_csv(conn: sqlite3.Connection, csv_path: Path) -> None:
    rows = _rows_from_csv(csv_path)
    if not rows:
        return
    deleted_cursor = conn.execute(
        f"""
        SELECT standard, isotopic_value_type
        FROM {_DELETED_STANDARDS_TABLE}
        """
    )
    try:
        deleted_keys = {
            (_normalize_standard_name(row[0]), _normalize_isotopic_type(row[1]))
            for row in deleted_cursor.fetchall()
        }
    finally:
        deleted_cursor.close()
    rows = [
        row
        for row in rows
        if (_normalize_standard_name(row[0]), _normalize_isotopic_type(row[1])) not in deleted_keys
    ]
    if not rows:
        return
    cursor = conn.executemany(
        f"""
        INSERT OR IGNORE INTO {_STANDARDS_TABLE} (
            standard,
            isotopic_value_type,
            value,
            source,
            updated_at
        )
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        rows,
    )
    try:
        conn.commit()
    finally:
        cursor.close()


def ensure_standards_database(
    db_path: str | Path | None = None,
    standards_csv_path: str | Path | None = None,
) -> Path:
    target_db = Path(db_path) if db_path is not None else default_standards_db_path()
    target_csv = Path(standards_csv_path) if standards_csv_path is not None else default_standards_path()
    target_db = target_db.resolve()
    target_csv = target_csv.resolve()
    conn = _connect_db(target_db)
    try:
        _ensure_schema(conn)
        if target_csv.exists():
            _insert_missing_rows_from_csv(conn, target_csv)
    finally:
        conn.close()
    return target_db


def load_standards(
    path: str | Path | None = None,
    db_path: str | Path | None = None,
    ensure_missing_csv_rows: bool = True,
) -> pd.DataFrame:
    standards_csv_path = Path(path) if path is not None else default_standards_path()
    if ensure_missing_csv_rows:
        standards_db_path = ensure_standards_database(
            db_path=db_path,
            standards_csv_path=standards_csv_path,
        )
    else:
        standards_db_path = Path(db_path) if db_path is not None else default_standards_db_path()
        standards_db_path = standards_db_path.resolve()
        conn = _connect_db(standards_db_path)
        try:
            _ensure_schema(conn)
        finally:
            conn.close()
    conn = _connect_db(standards_db_path)
    try:
        cursor = conn.execute(
            f"""
            SELECT
                standard AS Standard,
                isotopic_value_type AS Isotopic_Value_Type,
                value AS Value,
                source AS Source
            FROM {_STANDARDS_TABLE}
            ORDER BY standard, isotopic_value_type
            """,
        )
        try:
            rows = cursor.fetchall()
        finally:
            cursor.close()
        frame = pd.DataFrame(
            rows,
            columns=["Standard", "Isotopic_Value_Type", "Value", "Source"],
        )
    finally:
        conn.close()
    return normalize_standards_frame(frame)


@dataclass(slots=True)
class StandardsRepository:
    frame: pd.DataFrame
    source_path: Path | None = None
    database_path: Path | None = None

    @classmethod
    def default(
        cls,
        path: str | Path | None = None,
        db_path: str | Path | None = None,
    ) -> "StandardsRepository":
        target_csv = Path(path) if path is not None else default_standards_path()
        target_db = ensure_standards_database(db_path=db_path, standards_csv_path=target_csv)
        return cls(
            frame=load_standards(path=target_csv, db_path=target_db),
            source_path=target_csv,
            database_path=target_db,
        )

    def standards_list(self) -> list[str]:
        if "Standard" not in self.frame.columns:
            return []
        return sorted(
            self.frame["Standard"]
            .dropna()
            .astype(str)
            .str.strip()
            .replace("", np.nan)
            .dropna()
            .unique()
            .tolist()
        )

    def get_true_value(self, standard_name: str, isotopic_type: str) -> float:
        normalized_standard = _normalize_standard_name(standard_name)
        normalized_isotopic_type = _normalize_isotopic_type(isotopic_type)
        match = self.frame[
            (self.frame["Standard"] == normalized_standard)
            & (self.frame["Isotopic_Value_Type"] == normalized_isotopic_type)
        ]
        if match.empty:
            raise ValueError(
                f"True value not found for {normalized_standard!r} with type {normalized_isotopic_type!r}"
            )
        value = _coerce_float(match["Value"].iloc[0])
        if value is None:
            raise ValueError(
                f"True value for {normalized_standard!r} with type {normalized_isotopic_type!r} is invalid"
            )
        return value

    def all_official_values(self) -> list[dict[str, Any]]:
        if not {"Standard", "Isotopic_Value_Type", "Value"}.issubset(self.frame.columns):
            return []
        frame = normalize_standards_frame(self.frame)
        frame = frame.sort_values(["Standard", "Isotopic_Value_Type"], na_position="last")
        records: list[dict[str, Any]] = []
        for record in frame.to_dict(orient="records"):
            standard = _normalize_standard_name(record.get("Standard"))
            isotopic_type = _normalize_isotopic_type(record.get("Isotopic_Value_Type"))
            if not standard or not isotopic_type:
                continue
            source_raw = str(record.get("Source", "")).strip()
            records.append(
                {
                    "standard": standard,
                    "isotopic_value_type": isotopic_type,
                    "value": _coerce_float(record.get("Value")),
                    "source": source_raw if source_raw else "database",
                }
            )
        return records

    def upsert_official_value(
        self,
        standard: str,
        isotopic_value_type: str,
        value: float,
        source: str | None = None,
    ) -> dict[str, Any]:
        if self.database_path is None:
            raise ValueError("Standards repository has no database path.")
        normalized_standard = _normalize_standard_name(standard)
        normalized_isotopic_type = _normalize_isotopic_type(isotopic_value_type)
        numeric_value = _coerce_float(value)
        if not normalized_standard:
            raise ValueError("Standard name is required.")
        if not normalized_isotopic_type:
            raise ValueError("Isotopic value type is required.")
        if numeric_value is None:
            raise ValueError("Value must be numeric.")
        source_value = str(source or "manual").strip() or "manual"
        conn = _connect_db(self.database_path)
        try:
            _ensure_schema(conn)
            delete_cursor = conn.execute(
                f"""
                DELETE FROM {_DELETED_STANDARDS_TABLE}
                WHERE standard = ? AND isotopic_value_type = ?
                """,
                (normalized_standard, normalized_isotopic_type),
            )
            delete_cursor.close()
            cursor = conn.execute(
                f"""
                INSERT INTO {_STANDARDS_TABLE} (
                    standard,
                    isotopic_value_type,
                    value,
                    source,
                    updated_at
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(standard, isotopic_value_type)
                DO UPDATE SET
                    value = excluded.value,
                    source = excluded.source,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (normalized_standard, normalized_isotopic_type, numeric_value, source_value),
            )
            try:
                conn.commit()
            finally:
                cursor.close()
        finally:
            conn.close()
        self.frame = load_standards(path=self.source_path, db_path=self.database_path)
        return {
            "standard": normalized_standard,
            "isotopic_value_type": normalized_isotopic_type,
            "value": numeric_value,
            "source": source_value,
        }

    def delete_standard(self, standard: str) -> int:
        if self.database_path is None:
            raise ValueError("Standards repository has no database path.")
        normalized_standard = _normalize_standard_name(standard)
        if not normalized_standard:
            return 0
        conn = _connect_db(self.database_path)
        try:
            _ensure_schema(conn)
            cursor = conn.execute(
                f"DELETE FROM {_STANDARDS_TABLE} WHERE standard = ?",
                (normalized_standard,),
            )
            try:
                conn.commit()
                deleted_rows = int(cursor.rowcount or 0)
            finally:
                cursor.close()
            tombstones = [
                (normalized_standard, isotopic_type)
                for isotopic_type in (ISOTYPE_D13C, ISOTYPE_D18O)
            ]
            tombstone_cursor = conn.executemany(
                f"""
                INSERT INTO {_DELETED_STANDARDS_TABLE} (
                    standard,
                    isotopic_value_type,
                    deleted_at
                )
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(standard, isotopic_value_type)
                DO UPDATE SET deleted_at = CURRENT_TIMESTAMP
                """,
                tombstones,
            )
            try:
                conn.commit()
            finally:
                tombstone_cursor.close()
        finally:
            conn.close()
        self.frame = load_standards(
            path=self.source_path,
            db_path=self.database_path,
            ensure_missing_csv_rows=False,
        )
        return deleted_rows

    def delete_official_value(self, standard: str, isotopic_value_type: str) -> int:
        if self.database_path is None:
            raise ValueError("Standards repository has no database path.")
        normalized_standard = _normalize_standard_name(standard)
        normalized_isotopic_type = _normalize_isotopic_type(isotopic_value_type)
        if not normalized_standard or not normalized_isotopic_type:
            return 0
        conn = _connect_db(self.database_path)
        try:
            _ensure_schema(conn)
            cursor = conn.execute(
                f"""
                DELETE FROM {_STANDARDS_TABLE}
                WHERE standard = ? AND isotopic_value_type = ?
                """,
                (normalized_standard, normalized_isotopic_type),
            )
            try:
                conn.commit()
                deleted_rows = int(cursor.rowcount or 0)
            finally:
                cursor.close()
            tombstone_cursor = conn.execute(
                f"""
                INSERT INTO {_DELETED_STANDARDS_TABLE} (
                    standard,
                    isotopic_value_type,
                    deleted_at
                )
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(standard, isotopic_value_type)
                DO UPDATE SET deleted_at = CURRENT_TIMESTAMP
                """,
                (normalized_standard, normalized_isotopic_type),
            )
            try:
                conn.commit()
            finally:
                tombstone_cursor.close()
        finally:
            conn.close()
        self.frame = load_standards(path=self.source_path, db_path=self.database_path)
        return deleted_rows

    def official_values_for_standards(self, standards: list[str]) -> list[dict[str, Any]]:
        normalized_standards: list[str] = []
        seen: set[str] = set()
        for raw_standard in standards:
            standard = _normalize_standard_name(raw_standard)
            if not standard or standard in seen:
                continue
            seen.add(standard)
            normalized_standards.append(standard)
        if not normalized_standards:
            return []

        isotopic_types = [ISOTYPE_D13C, ISOTYPE_D18O]
        if not {"Standard", "Isotopic_Value_Type", "Value"}.issubset(self.frame.columns):
            return [
                {
                    "standard": standard,
                    "isotopic_value_type": isotopic_type,
                    "value": None,
                    "source": None,
                }
                for standard in normalized_standards
                for isotopic_type in isotopic_types
            ]

        frame = normalize_standards_frame(self.frame)
        frame = frame.loc[frame["Standard"].isin(normalized_standards)].copy()
        lookup: dict[tuple[str, str], tuple[float | None, str | None]] = {}
        for record in frame.to_dict(orient="records"):
            standard = _normalize_standard_name(record.get("Standard"))
            isotopic_type = _normalize_isotopic_type(record.get("Isotopic_Value_Type"))
            value = _coerce_float(record.get("Value"))
            source_raw = str(record.get("Source", "")).strip()
            source = source_raw if source_raw else "database"
            if standard and isotopic_type:
                lookup[(standard, isotopic_type)] = (value, source)

        records: list[dict[str, Any]] = []
        for standard in normalized_standards:
            for isotopic_type in isotopic_types:
                value, source = lookup.get((standard, isotopic_type), (None, None))
                records.append(
                    {
                        "standard": standard,
                        "isotopic_value_type": isotopic_type,
                        "value": value,
                        "source": source,
                    }
                )
        return records
