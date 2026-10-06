from __future__ import annotations

import io
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..domain.shared.dataframe import _parse_new_table_layout
from . import PARSER_VERSION
from .models import SAMPLE_TYPES
from .science import summary


def measurement_identity(record: dict, source_kind: str = "qtegra_raw") -> dict:
    """Use the original importer rules, including for older stored acquisitions."""
    from ..domain.import_session import _suggest_import_parsing_config, _apply_import_parsing_config
    keys = ("identifier1", "identifier2", "species")
    if all(key in record for key in keys):
        return {key: record[key] for key in keys}
    raw = record.get("raw_rows") or []
    values = raw[0]["values"] if raw else ({"Identifier 1": record.get("label"), "Identifier 2": record.get("comment")}
        if source_kind == "isodat_raw" else {"Label": record.get("label"), "Comment": record.get("comment")})
    frame = pd.DataFrame([values])
    software = "isodat" if source_kind == "isodat_raw" else "qtegra" if "Label" in frame else "generic"
    config = _suggest_import_parsing_config(frame, file_index=0, file_name="acquisition", software=software)
    parsed = _apply_import_parsing_config(frame, config).iloc[0]
    return {key: scalar(parsed[column]) or "" for key, column in zip(keys, ("Identifier 1", "Identifier 2", "Species"))}


def scalar(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    return value if isinstance(value, (str, int, float, bool)) else str(value)


def number(value, *, dimension: str | None = None, column: str = "") -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace("−", "-").replace("μ", "µ")
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)\s*(.*)", text)
    if not match:
        return None
    val = float(match.group(1))
    if not math.isfinite(val):
        return None
    unit = (match.group(2) or column).lower().replace("μ", "µ")
    if dimension == "voltage":
        if "mv" in unit:
            val /= 1000
        elif "µv" in unit or "uv" in unit:
            val /= 1_000_000
    elif dimension == "pressure":
        if "mbar" in unit:
            val *= 1000
        elif unit == "bar":
            val *= 1_000_000
    elif dimension == "mass":
        if "mg" in unit:
            val *= 1000
        elif re.search(r"\bg\b", unit) and not re.search(r"[µu]g", unit):
            val *= 1_000_000
        elif not re.search(r"[µu]g", unit):
            return None  # An unlabelled mass is not assumed to be micrograms.
    return val


def parse_workbook(content: bytes, filename: str) -> dict:
    if not content:
        raise ValueError("The workbook is empty")
    extension = Path(filename).suffix.lower()
    if extension not in {".xls", ".xlsx"}:
        raise ValueError("Choose a Qtegra .xls or .xlsx export")
    try:
        book = pd.ExcelFile(io.BytesIO(content), engine="xlrd" if extension == ".xls" else "openpyxl")
        sheet = "New Table" if "New Table" in book.sheet_names else book.sheet_names[0]
        raw = pd.read_excel(book, sheet_name=sheet, header=None)
        frame = _parse_new_table_layout(raw)
        layout = "qtegra_new_table"
        if frame is None:
            frame = raw.iloc[1:].copy()
            frame.columns = [str(v).strip() for v in raw.iloc[0]]
            layout = "tabular"
        counts: Counter = Counter()
        cols = []
        for col in frame.columns:
            counts[str(col)] += 1
            cols.append(str(col) if counts[str(col)] == 1 else f"{col} [{counts[str(col)]}]")
        frame.columns = cols
        if {"Row", "Method", "Date", "Time", "Identifier 1"}.issubset(cols) and "Sample Type" not in cols:
            return parse_legacy_export(frame, sheet, book.sheet_names)
        source_units = {}
        if layout == "qtegra_new_table" and len(frame):
            # The shared parser removes unit tokens from names. Recover them from
            # the original header rows, retaining their original column positions.
            known_units = {"v", "mv", "µv", "uv", "ug", "µg", "mg", "g", "ubar", "µbar", "mbar", "bar", "‰", "%"}
            for j, col in enumerate(cols):
                for value in raw.iloc[:int(frame.index[0]), j]:
                    unit = str(value).strip().lower().replace("μ", "µ")
                    if unit in known_units:
                        source_units[col] = unit
    except Exception as exc:
        raise ValueError(f"Could not parse workbook: {exc}") from exc

    def find(*patterns):
        for pattern in patterns:
            found = [c for c in cols if re.search(pattern, c, flags=re.I)]
            if len(found) > 1:
                raise ValueError(f"Ambiguous column for {pattern}: {found}")
            if found:
                return found[0]
        return None

    mapping = {
        "index": find(r"^Index$", r"^Run ID$"), "label": find(r"^Label$", r"^Sample$", r"^Identifier 1$"),
        "comment": find(r"^Comment$", r"^Identifier 2$"), "sample_type": find(r"^Sample Type$"),
        "reference": find(r"^Reference$"), "cycle": find(r"^Cycle Number$"),
        "d13c": find(r"\bd13C Mean$", r"^d13c$", r"^d 13C/12C\s+Mean$"),
        "d18o": find(r"\bd18O Mean$", r"^d18o$", r"^d 18O/16O\s+Mean$"),
        "d13c_sd": find(r"\bd13C SD$", r"^d13c_sd$", r"^d 13C/12C\s+Std Dev$"),
        "d18o_sd": find(r"\bd18O SD$", r"^d18o_sd$", r"^d 18O/16O\s+Std Dev$"),
        "cycle_d13c": find(r"\bd13C$"), "cycle_d18o": find(r"\bd18O$"),
        "i44_v": find(r"Sample Intensit.*44\.00 m/z$", r"^i44_v$", r"^I44\s*\(?V\)?$", r"^1\s+Cycle Int\s+Samp\s+44$"),
        "reference_i44_v": find(r"Standard Intensit.*44\.00 m/z$", r"^reference_i44_v$"),
        "mass_ug": find(r"^mass[_\s(].*", r"^Weight.*"),
        "time": find(r"^Start Time$", r"^Date$"), "stop_time": find(r"^Stop Time$"),
        "status": find(r"^Status$"), "evaluate": find(r"^Evaluate$"),
        "pressure_target_v": find(r"^Pressure Adjust Target Intensity$"),
        "pressure_result_v": find(r"^Pressure Adjust Result Intensity$"),
        "pressure_failed": find(r"^Pressure Adjust failed with Target Intensity$"),
        "co2_pressure_ubar": find(r"^Kiel IV CO[₂2] Sample Pressure$"),
        "qtegra_linearity": find(r"^Linearity Correction Result$"),
        "background": find(r"^Acquisition information Background Result$"),
    }
    missing = [key for key in ("label", "sample_type", "d13c", "d18o") if mapping[key] is None]
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))
    records = []
    blocks = []
    current = []
    for idx, row in frame.iterrows():
        data = {c: scalar(v) for c, v in row.items()}
        if not any(v is not None for v in data.values()):
            continue
        is_head = data.get(mapping["index"]) is not None if mapping["index"] else data.get(mapping["label"]) is not None
        if is_head and current:
            blocks.append(current)
            current = []
        if is_head or current:
            current.append({"sheet_row": int(idx) + 1, "values": data})
        else:
            raise ValueError(f"Orphan cycle at worksheet row {int(idx) + 1}")
    if current:
        blocks.append(current)
    seen = set()
    for position, block in enumerate(blocks, 1):
        head = block[0]["values"]
        get = lambda key: head.get(mapping.get(key))
        index = str(get("index") if get("index") is not None else position)
        if index in seen:
            raise ValueError(f"Duplicate analysis index {index}; split the run exports before import")
        seen.add(index)
        cycles = [r for r in block if re.fullmatch(r"(?:Cycle\s*)?\d+", str(r["values"].get(mapping["cycle"])), flags=re.I)] if mapping["cycle"] else []
        type_text = str(get("sample_type") or "").strip()
        canonical = next((name for name in SAMPLE_TYPES if name.casefold() == type_text.casefold()), None)
        source_role = "carbonate_standard" if type_text.casefold() == "standard" else SAMPLE_TYPES.get(canonical, "unrecognized")
        record = {"source_index": index, "sequence": position, "label": str(get("label") or ""),
                  "comment": str(get("comment") or ""), "reference": str(get("reference") or ""),
                  "sample_type": type_text, "source_role": source_role,
                  "acquired_at": get("time"), "stopped_at": get("stop_time"), "status": get("status"),
                  "evaluate": get("evaluate"), "sheet_row": block[0]["sheet_row"], "cycle_count": len(cycles),
                  "raw_rows": block, "qtegra_linearity": get("qtegra_linearity")}
        record["background"] = get("background")
        for isotope in ("d13c", "d18o"):
            record[isotope] = number(get(isotope))
            cycle_values = [number(c["values"].get(mapping["cycle_" + isotope])) for c in cycles]
            cycle_values = [v for v in cycle_values if v is not None]
            record[isotope + "_sd"] = number(get(isotope + "_sd"))
            if record[isotope + "_sd"] is None:
                record[isotope + "_sd"] = summary(cycle_values)["sd"]
        sample_rows = [c for c in block if number(c["values"].get(mapping["i44_v"])) is not None]
        record["sample_observation_count"] = len(sample_rows)
        for key in ("i44_v", "reference_i44_v"):
            values = [number(c["values"].get(mapping[key]), dimension="voltage", column=source_units.get(mapping[key], "")) for c in sample_rows]
            values = [v for v in values if v is not None]
            record[key] = summary(values)["mean"] if values else number(get(key), dimension="voltage", column=source_units.get(mapping[key], ""))
        record["sample_reference_difference_v"] = record["i44_v"] - record["reference_i44_v"] if record["i44_v"] is not None and record["reference_i44_v"] is not None else None
        for key in ("pressure_target_v", "pressure_result_v"):
            record[key] = number(get(key), dimension="voltage", column=source_units.get(mapping[key], ""))
        record["co2_pressure_ubar"] = number(get("co2_pressure_ubar"), dimension="pressure", column=source_units.get(mapping["co2_pressure_ubar"], ""))
        target, actual = record["pressure_target_v"], record["pressure_result_v"]
        record["pressure_mismatch_v"] = actual - target if actual is not None and target is not None else None
        failed = get("pressure_failed")
        record["pressure_failed"] = failed not in (None, "", False, "False", "false", 0, "0")
        record["mass_ug"] = number(get("mass_ug"), dimension="mass", column=source_units.get(mapping["mass_ug"], mapping["mass_ug"] or ""))
        record["mass_source"] = mapping["mass_ug"] if record["mass_ug"] is not None else None
        # Parse only an explicit key and unit, never a bare numeric sample comment.
        mass_match = re.search(r"(?:^|;)\s*MASS\s*=\s*([\d.]+)\s*(ug|µg|μg|mg)\s*(?:;|$)", record["comment"], re.I)
        if record["mass_ug"] is None and mass_match:
            record["mass_ug"] = number(" ".join(mass_match.groups()), dimension="mass")
            record["mass_source"] = "Comment: explicit MASS=<value> <unit>"
        replicate = re.search(r"(?:^|;)\s*REP\s*=\s*(\d+)\s*(?:;|$)", record["comment"], re.I)
        record["replicate"] = int(replicate.group(1)) if replicate else None
        record.update(measurement_identity(record))
        records.append(record)
    if not records:
        raise ValueError("No analyses found")
    warnings = []
    if any(r["mass_ug"] is None for r in records):
        warnings.append("Mass is absent or has no explicit unit. Enter weighed masses in µg before range validation.")
    if any(r["status"] and str(r["status"]).casefold() == "running" for r in records):
        warnings.append("The export contains Running statuses. Confirm completed acquisition before approval or release.")
    if any(r["source_role"] == "unsupported_drift" for r in records):
        warnings.append("Drift Correction rows conflict with the laboratory policy and block processing approval.")
    if any(r["source_role"] == "carbonate_standard" for r in records):
        warnings.append("Standard is a legacy/mock carbonate-material type, outside the six Qtegra choices. Anchoring still requires an exact configured material identity.")
    synthetic = "__MOCK_INFO__" in book.sheet_names or any("MOCK_METROLOGY" in r["comment"] for r in records)
    if synthetic:
        warnings.append("Synthetic training data. Diagnostics are available; this dataset cannot approve a laboratory method or release client results.")
    return {"parser_version": PARSER_VERSION, "source_kind": "qtegra_raw", "layout": layout, "sheet": sheet, "sheets": book.sheet_names,
            "columns": cols, "source_units": source_units, "mapping": mapping, "measurements": records, "warnings": warnings,
            "type_counts": dict(Counter(r["sample_type"] for r in records)),
            "analysis_count": len(records), "cycle_count": sum(r["cycle_count"] for r in records), "synthetic": synthetic,
            "units": {"d13c": "per mille", "d18o": "per mille", "i44_v": "V", "mass_ug": "µg", "co2_pressure_ubar": "µbar"},
            "isotope_basis": "Qtegra exported elemental delta means; no additional Qtegra correction",
            "intensity_basis": "Mean of valid sample observations including Pre; reference-only rows excluded; reference I44 paired to sample rows"}


def parse_legacy_export(frame, sheet, sheets):
    """Read the original ISODAT row export, without importing processor outputs.

    This export uses first-cycle mV rather than Qtegra mean-cycle V. Preserve this
    distinction and do not substitute sample/reference imbalance for PressAdj.
    """
    cols = list(frame.columns)
    if any(re.search(r"corrected|interpolat|linearity correction", c, re.I) for c in cols):
        raise ValueError("Processed workbook: import the original acquisition export instead")
    def col(pattern):
        matches = [c for c in cols if re.fullmatch(pattern, c, re.I)]
        if len(matches) != 1:
            raise ValueError(f"Legacy acquisition column missing or ambiguous: {pattern}")
        return matches[0]
    mapping = {"d13c": col(r"d\s+13C/12C\s+Mean"), "d18o": col(r"d\s+18O/16O\s+Mean"),
               "d13c_sd": col(r"d\s+13C/12C\s+Std Dev"), "d18o_sd": col(r"d\s+18O/16O\s+Std Dev"),
               "i44_v": col(r"1\s+Cycle Int\s+Samp\s+44"), "reference_i44_v": col(r"1\s+Cycle Int\s+Ref\s+44")}
    records=[]
    for idx, row in frame.iterrows():
        data={c:scalar(v) for c,v in row.items()}
        if number(data.get("Row")) is None or not data.get("Identifier 1"):
            continue
        date, time = str(data.get("Date") or ""), str(data.get("Time") or "")
        try:
            stamp = datetime.strptime(f"{date} {time}", "%m/%d/%y %H:%M:%S").isoformat()
        except ValueError:
            raise ValueError(f"Unrecognized legacy acquisition date/time at worksheet row {idx+1}")
        rec={"source_index":f"{data['Row']}@{stamp}","instrument_row":data["Row"],"sequence":0,
             "label":str(data["Identifier 1"]),"comment":str(data.get("Identifier 2") or ""),
             "reference":"","sample_type":"Unknown","source_role":"unknown",
             "sample_type_basis":"No sample-type field in ISODAT; exact configured material labels identify QC/anchors",
             "acquired_at":stamp,"stopped_at":None,"status":None,"evaluate":None,"sheet_row":int(idx)+1,
             "cycle_count":0,"sample_observation_count":1,"qtegra_linearity":None,"background":None,
             "raw_rows":[{"sheet_row":int(idx)+1,"values":data}],"pressure_target_v":None,"pressure_result_v":None,
             "pressure_mismatch_v":None,"pressure_failed":False,"mass_ug":None,"mass_source":None,"replicate":None}
        for key, name in mapping.items():
            rec[key]=number(data[name],dimension="voltage",column="mV") if key.endswith("_v") else number(data[name])
        rec["sample_reference_difference_v"]=rec["i44_v"]-rec["reference_i44_v"] if rec["i44_v"] is not None and rec["reference_i44_v"] is not None else None
        pressure=re.search(r"Total CO2\s*:\s*([+-]?[\d.]+)",str(data.get("Information") or ""))
        rec["co2_pressure_ubar"]=float(pressure.group(1)) if pressure else None
        rec.update(measurement_identity(rec, "isodat_raw"))
        records.append(rec)
    if not records:
        raise ValueError("No original acquisition rows found")
    records.sort(key=lambda r:(r["acquired_at"],r["sheet_row"]))
    for i,rec in enumerate(records,1): rec["sequence"]=i
    return {"parser_version":PARSER_VERSION,"source_kind":"isodat_raw","layout":"isodat_row_export","sheet":sheet,"sheets":sheets,
            "columns":cols,"source_units":{mapping[k]:"mV" for k in ("i44_v","reference_i44_v")},"mapping":mapping,
            "measurements":records,"warnings":["Legacy ISODAT acquisition export. First-cycle intensities converted from mV to V; not Qtegra cycle means.",
                "Mass and pressure-adjustment target/result are absent. Sample/reference signal difference is kept separately.",
                "Sample Type is absent. Material identity comes from exact configured reference-material labels."],
            "type_counts":{"Unknown":len(records)},"analysis_count":len(records),"cycle_count":0,"synthetic":False,
            "units":{"d13c":"per mille","d18o":"per mille","i44_v":"V","mass_ug":"µg","co2_pressure_ubar":"µbar"},
            "isotope_basis":"Original ISODAT exported elemental means; historical calibration provenance requires review",
            "intensity_basis":"First-cycle sample and reference I44, mV converted to V; not a mean over cycles"}
