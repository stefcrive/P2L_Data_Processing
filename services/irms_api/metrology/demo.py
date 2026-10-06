"""Isolated synthetic workspace, populated through the ordinary scientific workflow."""
from __future__ import annotations

import argparse
import copy
import json
import math
import tempfile
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pandas as pd

from .models import (ApproveCommand, Decision, EffectCommand, EvaluateCommand, LinearCorrection, Material,
                     MaterialCommand, MethodCommand, MethodConfig, PeriodCommand,
                     QualificationCommand, ReleaseCommand, RunCommand, TestCommand)
from .repository import Repository
from .science import regression
from .service import Service

ASSETS = Path(__file__).with_name("demo_assets")
REVIEW = dict(actor="Demo scientist", reason="Synthetic workflow demonstration; example evidence reviewed, not a laboratory authorization.")


def populate(root: Path):
    plan = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
    instant = [datetime(2026, 4, 5, 1, tzinfo=timezone.utc)]
    service = Service(Repository(root, demo=True, clock=lambda: instant[0]))
    examples = root / "examples"
    examples.mkdir()
    with zipfile.ZipFile(ASSETS / "workbooks.zip") as archive:
        for book in plan["books"]:
            name = book["filename"]
            if Path(name).name != name or not name.endswith(".xlsx"):
                raise ValueError("Invalid packaged demo asset")
            (examples / name).write_bytes(archive.read(name))
    initial = service.state()
    materials = []
    uncertainties = {"NBS18": [.035, .070], "NBS19": [.020, .040], "SHP2L": [.047, .089]}
    for previous in initial["materials"]:
        name = previous["name"]
        material = Material(name=name, lot="DEMO-2026-01", aliases=previous["aliases"],
            supplier="Synthetic reference-material library", certificate=f"DEMO-{name}-001: illustrative values only",
            issue_date="2026-01-01", valid_until="2027-12-31", verified=True, revision_of=previous["id"],
            traceability="Synthetic VPDB example. Anchor assignments are not verified certificate values. SHP2L uncertainties are treated as standard uncertainties ONLY for this demonstration.",
            assigned={iso: dict(value=plan["assigned_examples"][name][i], uncertainty=uncertainties[name][i],
                      uncertainty_type="standard", k=1, scale="VPDB", coverage_probability=.6827)
                      for i, iso in enumerate(("d13c", "d18o"))})
        materials.append(service.save_material(MaterialCommand(**REVIEW, material=material)))
    ids = {m["name"]: m["id"] for m in materials}
    config = MethodConfig(name="DEMO carbonate method", intended_use="Synthetic carbonate workflow evaluation, 60–140 µg; not for laboratory reporting",
        laboratory="P2L demonstration laboratory", configuration="DEMO Kiel IV / MAT253 Plus / Dual Inlet, configuration A",
        tunebook="DEMO carbonates 2026-A", reaction_temperature_c=70,
        preparation="Synthetic aliquots, phosphoric acid digestion at 70 °C; example preparation protocol DEMO-P01",
        acquisition="Eight sample observations per analysis; mean I44 from Pre + cycles 1–7; cycle 8 reference only",
        anchor_ids=[ids["NBS18"], ids["NBS19"]], qc_id=ids["SHP2L"],
        ranges={"mass_ug":dict(low=60,high=140),"i44_v":dict(low=3.55,high=8.45),
                "pressure_mismatch_v":dict(low=-.02,high=.02),"d13c":dict(low=-5.1,high=2.05),"d18o":dict(low=-23.1,high=-2.1)},
        qc=dict(bias={"d13c":.07,"d18o":.10}), precision={"d13c":.035,"d18o":.055},
        precision_evidence="Initial simulated intermediate precision baseline; later versions use reviewed individual QC observations across days.",
        independence_rationale="Routine QC dispersion, separate carousel anchor means/certificates, and independently trained correction coefficients represent separate inputs. No extra repeatability term is added to historical precision.",
        coverage_rationale="Demonstration k=2, approximate 95% coverage under a normal model; no claim of exact coverage or certified performance.",
        qualification_interval_days=60, qtegra_linearity="enabled")
    fits = {}
    for study in ("training", "validation"):
        frame = pd.read_excel(BytesIO((examples / f"correction-{study}.xlsx").read_bytes()))
        fits[study] = {iso: regression(frame.iloc[:,2].tolist(),frame.iloc[:,i+3].tolist()) for i,iso in enumerate(("d13c","d18o"))}
    evidence_summary = json.dumps({"simulation": True, "fits": fits,
        "practical_effect_threshold_per_mille": {"d13c":.01,"d18o":.02}}, indent=2).encode()
    highlights, files = {}, []
    last_period = None
    def at(date, hour=23):
        instant[0] = datetime.fromisoformat(f"{date}T{hour:02}:00:00+00:00")

    def qualify(filename, date, method_id=None, approve=True):
        nonlocal config
        at(date, 1)
        local = copy.deepcopy(config)
        local.corrections = {"d13c":None,"d18o":None}
        if last_period:
            local.precision_source = "historical_qc"
            local.precision_period = last_period["id"]
            local.precision = {iso:last_period["statistics"][iso]["sd"] for iso in ("d13c","d18o")}
            local.precision_evidence = f"Reviewed period {last_period['name']}; SD of individual results, not standard error. Failed runs excluded with their evidence retained."
        method = service.save_method(MethodCommand(**REVIEW, config=local), method_id)
        q = service.create_qualification(QualificationCommand(**REVIEW, method_id=method["id"],
            carousel=[dict(material_id=m["id"],mass_ug=mass,replicates=5) for m in materials for mass in (60,100,140)]))
        assets = [service.attach(q["id"], f"correction-{s}.xlsx", (examples/f"correction-{s}.xlsx").read_bytes(), Decision(**REVIEW),
                    f"Synthetic independent {s} intensity experiment, 30 observations; not carousel data.") for s in ("training","validation")]
        service.attach(q["id"], "effect-evidence.json", evidence_summary, Decision(**REVIEW), "Numerical regressions, covariance and independent replication of the synthetic residual intensity effect.")
        for iso in ("d13c","d18o"):
            train, check = fits["training"][iso], fits["validation"][iso]
            if train["slope_ci95"][0] <= 0 or abs(train["slope"]-check["slope"]) > 3*math.hypot(train["slope_se"],check["slope_se"]):
                raise ValueError("Synthetic correction does not reproduce in the independent experiment")
            local.corrections[iso] = LinearCorrection(name="Residual intensity effect", predictor="i44_v", slope=train["slope"],u_slope=train["slope_se"],center=6,
                domain=dict(low=3.55,high=8.45),training_evidence=f"DEMO training study, n=30, slope={train['slope']:.7f}, SE={train['slope_se']:.7f} per mille/V",
                validation_evidence=f"Separate DEMO validation study, n=30, slope={check['slope']:.7f}, SE={check['slope_se']:.7f}; compatible within 3 combined SE",
                independence_rationale="Separate synthetic training and validation experiments, different materials and independent random draws; neither shares carousel anchor or routine QC observations.",
                predictor_uncertainty_rationale="Example I44 resolution 0.001 V gives at most 0.00002 per mille, negligible against the 0.01 per mille practical threshold.",evidence_asset_ids=[a["id"] for a in assets])
        local = MethodConfig.model_validate(local.model_dump())
        method = service.save_method(MethodCommand(**REVIEW, config=local), method["id"])
        tests = [("Vacuum",1.2e-8,"mbar","< 2e-8 mbar"),("Magnet scan / background",.003,"V","Background < 0.010 V"),
            ("Peak center",.02,"V","Absolute offset < 0.05 V"),("Peak shape",.995,"ratio","Flat-top ratio > 0.990"),
            ("Sensitivity",6,"V / 100 µg","5.5–6.5 V / 100 µg"),("Signal stability",.012,"%","Relative SD < 0.05%"),
            ("Zero enrichment",.008,"‰","Absolute offset < 0.02‰"),("Dry CO2 linearity",.004,"‰/V","Residual slope < 0.01‰/V"),
            ("Sample/reference mismatch",.006,"V","Absolute mismatch < 0.02 V"),("Dual Inlet",.018,"‰","Repeated switch SD < 0.03‰"),
            ("Independent QC",.015,"‰","Corrected/normalized SD C < 0.07, O < 0.10‰; |bias| C ≤ 0.07, O ≤ 0.10‰")]
        for name,value,unit,criterion in tests:
            service.save_test(q["id"],TestCommand(**REVIEW,name=name,value=value,unit=unit,criterion="DEMO criterion: "+criterion,result="pass"))
        for effect,decision,evidence in [
            ("mass_intensity","approve_correction","Synthetic intensity effects exceed practical thresholds C 0.01‰ / O 0.02‰ over 4.8 V; independent studies reproduce slopes. Fit one intensity term; mass is collinear and gets no second correction."),
            ("pressure_adjustment","negligible","Simulated pressure jitter is independent of isotope noise; mismatch stays within ±0.02 V. Inspect residual plots; no pressure correction introduced."),
            ("drift","monitor","Synthetic generator contains no temporal drift. QC sequence regressions are screening evidence only; any accidental slope is not independently reproduced. Qtegra drift correction disabled."),
            ("memory","monitor","No carryover term in the synthetic generator. Contrasting predecessor plots are reviewed; no independent memory demonstration, therefore no correction."),
            ("bias","negligible","Independent SHP2L checked after correction and dual-point normalization. Mean bias must be within C 0.07‰ / O 0.10‰; no QC-derived bias correction.")]:
            service.save_effect(q["id"],EffectCommand(**REVIEW,effect=effect,decision=decision,evidence=evidence))
        run = service.import_run(filename,(examples/filename).read_bytes(),RunCommand(**REVIEW,context="qualification",method_id=method["id"],qualification_id=q["id"],label=f"DEMO qualification · {date}"))
        at(date)
        ev = service.evaluate_run(run["id"],EvaluateCommand(**REVIEW))
        if not ev["ready"]:
            raise ValueError(f"Demo qualification {date}: {ev['blockers']}")
        if approve:
            method = service.approve(method["id"],ApproveCommand(**REVIEW,qualification_id=q["id"],evaluation_id=ev["id"]))
        files.append(dict(filename=filename,context="qualification",method_id=method["id"],qualification_id=q["id"],run_id=run["id"]))
        return method,q,run

    for index, period in enumerate(plan["periods"]):
        method,q,run = qualify(period["qualification"],period["date"], initial["methods"][0]["id"] if index==0 else None)
        highlights.update(active_method=method["id"],qualification_run=run["id"])
        eligible = []
        for book in period["routines"]:
            at(book["date"])
            run = service.import_run(book["filename"],(examples/book["filename"]).read_bytes(),RunCommand(**REVIEW,
                method_id=method["id"],label=f"DEMO {book['scenario'].replace('_',' ')} · {book['date']}"))
            ev = service.evaluate_run(run["id"],EvaluateCommand(**REVIEW))
            if book["scenario"] in ("released","ready_for_review"):
                if not ev["ready"]:
                    raise ValueError(f"Demo routine {book['date']}: {ev['blockers']}")
                eligible.append(ev["id"])
            if book["scenario"] == "released":
                service.release(run["id"],ReleaseCommand(**REVIEW,evaluation_id=ev["id"]))
                highlights["released_run"] = run["id"]
            else:
                highlights[{"ready_for_review":"ready_run","range_block":"blocked_run","historical_qc_failure":"historical_failure_run"}[book["scenario"]]] = run["id"]
            files.append(dict(filename=book["filename"],context="routine",method_id=method["id"],run_id=run["id"],scenario=book["scenario"]))
        last_period = service.create_period(PeriodCommand(**REVIEW,method_id=method["id"],name=f"DEMO controlled period {index+1} ({period['date']})",evaluation_ids=eligible))
        print(f"Populated method v{method['version']}: {len(period['routines'])} routine runs",flush=True)
    _,q,run = qualify(plan["review_file"],"2026-10-04",approve=False)
    highlights.update(qualification_review=q["id"],review_run=run["id"])
    at(plan["as_of"],12)
    from .api import ReportCommand, report
    with service.repo.connect() as db:
        release = service.repo.list(db,"releases",run_id=highlights["released_run"])[0]
    for kind, target in [("history",None),("qualification",q["id"]),("client",release["id"])]:
        report(ReportCommand(**REVIEW,kind=kind,target_id=target),service)
    manifest = dict(as_of=plan["as_of"],simulation=True,highlights=highlights,files=files,note=plan["note"])
    (root/"demo-manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return manifest


def seed_demo(root: str | Path = ".data/metrology-demo"):
    root = Path(root).resolve()
    if root == Path(".data/metrology").resolve():
        raise ValueError("Demo data requires a separate workspace")
    marker = root/"demo-manifest.json"
    if marker.exists():
        Repository(root,demo=True)
        return json.loads(marker.read_text(encoding="utf-8"))
    if root.exists():
        raise ValueError("Choose a new empty demo path; an existing workspace will not be overwritten")
    root.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".metrology-demo-build-",dir=root.parent) as temporary:
        staged = Path(temporary)/"workspace"
        manifest = populate(staged)
        staged.rename(root)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",default=".data/metrology-demo")
    args = parser.parse_args()
    result = seed_demo(args.data_dir)
    print(f"Demo ready: {len(result['files'])} imported workbooks; {Path(args.data_dir).resolve()}")
