"""Read-only cycle evidence from archived measurement cells and their import mapping."""
import re
from .importer import number


def analysis_evidence(record, source):
    mapping, units = source.get("mapping", {}), source.get("source_units", {})
    cycles = []
    for raw in record.get("raw_rows", []):
        cells = raw["values"]
        label = str(cells.get(mapping.get("cycle"), ""))
        match = re.fullmatch(r"(?:Cycle\s*)?(\d+)", label, flags=re.I)
        if match:
            values = {"cycle": int(match[1]), "sheet_row": raw["sheet_row"]}
            for key in ("cycle_d13c", "cycle_d18o", "i44_v", "reference_i44_v"):
                column = mapping.get(key)
                values[key] = number(cells.get(column), dimension="voltage" if key.endswith("_v") else None, column=units.get(column, ""))
            cycles.append(values)
        elif source.get("source_kind") == "isodat_raw":
            # Wide ISODAT rows contain cycle-specific intensities, not elemental means.
            wide = {}
            for column, value in cells.items():
                m = re.fullmatch(r"(\d+)\s+Cycle Int\s+(Samp|Ref)\s+44", column, flags=re.I)
                if m:
                    cycle = int(m[1])
                    wide.setdefault(cycle, {"cycle": cycle, "sheet_row": raw["sheet_row"]})["i44_v" if m[2].lower() == "samp" else "reference_i44_v"] = number(value, dimension="voltage", column="mV")
            cycles.extend(wide.values())
    return {"cycles": sorted(cycles, key=lambda r: r["cycle"]), "raw_rows": record.get("raw_rows", []),
            "source_units": units, "intensity_basis": source.get("intensity_basis", ""), "source_sha256": source.get("sha256")}
