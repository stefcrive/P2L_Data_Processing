"""Reproducible numerical fixtures; XLSX authoring is done by the companion builder."""
from __future__ import annotations

import argparse
import json
import zipfile
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import numpy as np

AS_OF = "2026-10-05"
ASSIGNED = {"NBS18": [-5.014, -23.010], "NBS19": [1.950, -2.200], "SHP2L": [-.750, -5.720]}
SLOPES = [.009, .015]
OFFSET = [.12, -.15]
HEADERS = ["Index", "User name", "Start Time", "Stop Time", "Status", "Label", "Comment", "Evaluate", "Sample Type", "Reference", "Cycle Number", "d13C Mean", "d18O Mean", "d13C SD", "d18O SD", "Sample Intensity", "Standard Intensity", "mass_ug", "Pressure Adjust Target Intensity", "Pressure Adjust Result Intensity", "Pressure Adjust failed with Target Intensity", "Kiel IV CO2 Sample Pressure", "Linearity Correction Result", "d13C", "d18O"]


def measurement(index, label, mass, true_values, start, rng, *, noise=(0., 0.), externally_referenced=False, bad_internal=False):
    intensity = .06 * mass + rng.normal(0, .012)
    values = [v + noise[i] + (0 if externally_referenced else OFFSET[i] + SLOPES[i] * (intensity - 6)) for i, v in enumerate(true_values)]
    sd = [.012, .024 if not bad_internal else .085]
    cycle_values = []
    for iso in range(2):
        draws = rng.normal(size=8)
        draws = (draws - draws.mean()) / draws.std(ddof=1) * sd[iso] + values[iso]
        cycle_values.append(draws.tolist())
    cycle_signals = rng.normal(intensity, .006, 8)
    cycle_signals += intensity - cycle_signals.mean()
    head = [index, "DEMO scientist", start.isoformat(), (start + timedelta(minutes=11)).isoformat(), "Completed", label,
            f"MASS={mass:.1f} ug; REP={index}; MOCK_METROLOGY", True, "QC Standard" if label == "SHP2L" else "Unknown", "SHP2L" if label == "SHP2L" else "NBS19", "Pre",
            *values, *sd, float(cycle_signals[0]), float(cycle_signals[0] * 1.003), mass, intensity,
            intensity + rng.normal(0, .002), False, float(mass * .036), "Qtegra instrumental linearity enabled; residual application correction is separate", cycle_values[0][0], cycle_values[1][0]]
    rows = [head]
    for cycle in range(1, 9):
        r = [None] * len(HEADERS)
        r[10] = f"Cycle {cycle}"
        if cycle < 8:
            r[15], r[16] = float(cycle_signals[cycle]), float(cycle_signals[cycle] * 1.003)
            r[23], r[24] = cycle_values[0][cycle], cycle_values[1][cycle]
        else:
            r[16] = intensity * 1.004
        rows.append(r)
    return rows


def qtegra_grid(rows):
    subhead = [None] * len(HEADERS)
    subhead[15:17] = ["44.00 m/z"] * 2
    units = [None] * len(HEADERS)
    units[11:15] = ["‰"] * 4
    for i in [15, 16, 18, 19]:
        units[i] = "V"
    units[17] = "µg"
    return [[None] * len(HEADERS), HEADERS, subhead, units, *rows]


def create_plan():
    rng = np.random.default_rng(2532026)
    # Keep aliquot errors independent of cycle/instrument draws and sequence position.
    qc_rng = np.random.default_rng(2532026)
    books = []
    periods = []
    dates = ["2026-04-05", "2026-06-04", "2026-08-03", "2026-10-02"]
    for period, date in enumerate(dates):
        start = datetime.fromisoformat(date + "T06:00:00")
        rows = []
        index = 0
        # Interleaved materials and balanced masses prevent a mass/time ordering artifact.
        for replicate in range(5):
            for mass in [60., 100., 140.]:
                for name in (["NBS19", "SHP2L", "NBS18"] if replicate % 2 == 0 else ["NBS18", "SHP2L", "NBS19"]):
                    index += 1
                    jitter = [-.010, .006, .011, -.006, -.001][replicate]
                    rows.extend(measurement(index, name, mass, ASSIGNED[name], start + timedelta(minutes=12 * (index - 1)), rng, noise=(jitter, 1.45 * jitter)))
        qualification = f"qualification-{date}.xlsx"
        books.append({"filename": qualification, "grid": qtegra_grid(rows), "kind": "qualification", "date": date, "period": period})
        period_item = {"date": date, "qualification": qualification, "routines": []}
        if period < 3:
            routine_dates = [start + timedelta(days=3 + 4*i, hours=2) for i in range(12)]
        else:
            routine_dates = [datetime(2026,10,3,8), datetime(2026,10,4,18), datetime(2026,10,5,5)]
        for run_index, acquired in enumerate(routine_dates):
            failed_qc = period == 2 and run_index == 11
            out_of_range = period == 3 and run_index == 2
            rows, index = [], 0
            for qc_index in range(6):
                index += 1
                noise = tuple(qc_rng.normal(0., [.028, .043]))
                if failed_qc:
                    noise = (noise[0] + .19, noise[1] + .28)
                rows.extend(measurement(index, "SHP2L", 100., ASSIGNED["SHP2L"], acquired + timedelta(minutes=(index-1)*12), rng, noise=noise))
                if qc_index == 5:
                    continue
                for sample in range(4):
                    index += 1
                    mass = 90. + 4 * ((sample + qc_index) % 6)
                    if out_of_range and index == 3:
                        mass = 180.
                    true = [-3.5 + .72 * ((index + run_index) % 7), -18.5 + 2.0 * ((index + run_index) % 7)]
                    rows.extend(measurement(index, f"DEMO-{period+1:02}-{run_index+1:02}-{index:02}", mass, true, acquired + timedelta(minutes=(index-1)*12), rng, noise=(rng.normal(0,.010),rng.normal(0,.015))))
            filename = f"routine-{acquired:%Y-%m-%d}.xlsx"
            scenario = "historical_qc_failure" if failed_qc else ("range_block" if out_of_range else ("ready_for_review" if period == 3 and run_index == 1 else "released"))
            books.append({"filename": filename, "grid": qtegra_grid(rows), "kind": "routine", "date": acquired.date().isoformat(), "period": period, "scenario": scenario})
            period_item["routines"].append({"filename": filename, "date": acquired.date().isoformat(), "scenario": scenario})
        periods.append(period_item)
    # A second independent recent carousel remains fully populated and open for user review.
    recent = next(b for b in books if b["filename"] == "qualification-2026-10-02.xlsx")
    import copy
    draft = copy.deepcopy(recent)
    draft.update(filename="qualification-2026-10-04-review.xlsx", date="2026-10-04", kind="qualification_review")
    for row in draft["grid"][4:]:
        for col in (2,3):
            if row[col]:
                row[col] = (datetime.fromisoformat(row[col]) + timedelta(days=2)).isoformat()
    books.append(draft)
    for study, name, seed in [("training","NBS19",431),("validation","NBS18",927)]:
        rs = np.random.default_rng(seed)
        grid = [["Study", "Material", "I44 / V", "d13C / per mille", "d18O / per mille"]]
        for signal in np.tile(np.linspace(3.6,8.4,6),5):
            grid.append([study,name,float(signal), ASSIGNED[name][0]+OFFSET[0]+SLOPES[0]*(signal-6)+rs.normal(0,.002), ASSIGNED[name][1]+OFFSET[1]+SLOPES[1]*(signal-6)+rs.normal(0,.003)])
        books.append({"filename":f"correction-{study}.xlsx","grid":grid,"kind":"study"})
    return {"as_of":AS_OF,"seed":2532026,"assigned_examples":ASSIGNED,"slope_examples":SLOPES,"periods":periods,"review_file":draft["filename"],"books":books,
            "note":"Synthetic demonstration only. Assigned anchor values and uncertainty metadata are examples, not verified certificates. Noise and effects are explicitly modeled, not observed laboratory performance."}


def package_plan(plan, assets):
    """Refresh numerical fixtures, retaining the packaged workbook layouts."""
    from openpyxl import load_workbook

    assets = Path(assets)
    archive_path = assets / "workbooks.zip"
    result = BytesIO()
    with zipfile.ZipFile(archive_path) as templates, zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as output:
        for book in plan["books"]:
            workbook = load_workbook(BytesIO(templates.read(book["filename"])))
            sheet = workbook.worksheets[0]
            for row_index, row in enumerate(book["grid"], 1):
                for column, value in enumerate(row, 1):
                    sheet.cell(row_index, column).value = value
            content = BytesIO()
            workbook.save(content)
            output.writestr(book["filename"], content.getvalue())
    archive_path.write_bytes(result.getvalue())
    manifest = {**plan, "books": [{k: v for k, v in b.items() if k != "grid"} for b in plan["books"]]}
    (assets / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",default="tmp/metrology-demo-work/workbooks.json")
    parser.add_argument("--package-assets", help="Refresh an existing demo_assets directory using its XLSX layouts")
    args=parser.parse_args()
    target=Path(args.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    plan=create_plan()
    target.write_text(json.dumps(plan,ensure_ascii=False,allow_nan=False),encoding="utf-8")
    if args.package_assets:
        package_plan(plan, args.package_assets)
    print(f"Prepared {len(plan['books'])} reproducible workbook matrices")
