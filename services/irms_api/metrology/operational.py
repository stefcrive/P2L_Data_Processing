"""An isolated laboratory simulation with synthetic qualification and real raw exports.

Sources are read only. Reports and saved processing snapshots are never imported.
The original bytes, including alternative raw exports, remain downloadable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .demo import seed_demo
from .importer import parse_workbook
from .models import Decision, EvaluateCommand, ExclusionCommand, ResultsSessionCommand, RunCommand, SessionExportCommand
from .repository import Repository
from .service import Service

SERIES = [
    ("Venancio 383 U1539 benthic series", "Igor Venancio", "383 U1539"),
    ("Igor Venanacio MD28-3678 series", "Igor Venancio", "MD28-3678"),
    ("Natan Pereira BTS coral series", "Natan Pereira", "BTS coral"),
]
REVIEW = dict(actor="Operational simulation importer", reason="User-requested simulation from original raw acquisition exports; historical calibration transfer remains unverified.")


def acquisition_key(row):
    return (row["acquired_at"],row["label"],row["comment"])


def populate_operational(root, source_root):
    root,source_root=Path(root).resolve(),Path(source_root).resolve()
    marker=root/"operational-manifest.json"
    if marker.exists():
        return json.loads(marker.read_text(encoding="utf-8"))
    seed_demo(root)
    service=Service(Repository(root,demo=True))
    state=service.state()
    method=state["active_method"]
    manifest={"simulation":True,"raw_exports_only":True,"source_root":str(source_root),"method_id":method["id"],
              "note":"Real raw observations processed under a simulated current qualification. Dates and missing metadata retained. No historical calibration transfer verified. No client release.",
              "sessions":[],"excluded_files":[],"counts":{}}
    for folder,client,project in SERIES:
        directory=source_root/folder
        if not directory.is_dir():
            raise ValueError(f"Source folder missing: {directory}")
        candidates=[]
        for path in sorted(directory.rglob("*")):
            if not path.is_file(): continue
            relative=path.relative_to(directory).as_posix()
            if path.suffix.lower() not in (".xlsx",".xls") or path.name.startswith("~$"):
                manifest["excluded_files"].append({"series":folder,"path":relative,"reason":"Not an original Excel acquisition export"})
                continue
            try:
                parsed=parse_workbook(path.read_bytes(),path.name)
            except ValueError as exc:
                manifest["excluded_files"].append({"series":folder,"path":relative,"reason":str(exc)})
                continue
            candidates.append((path,relative,parsed))
        # Prefer main-folder completed exports over backups and temporary copies.
        # This choice is documented; alternative bytes are archived, never discarded.
        candidates.sort(key=lambda c:("temp" in c[0].stem.lower(),len(Path(c[1]).parts),-c[2]["analysis_count"],c[1]))
        with service.repo.connect() as db:
            existing=next((s for s in service.repo.list(db,"results_sessions") if s.get("project")==project and s.get("calibration_verification")=="simulation_assumption"),None)
        session=existing or service.save_results_session(ResultsSessionCommand(**REVIEW,name=folder,client=client,project=project,
            method_id=method["id"],input_basis="already_vpdb",calibration_verification="simulation_assumption",
            notes="Original raw acquisitions only. Acquisition dates, isotope values and identifiers retained. Mock qualification and uncertainty are illustrative; missing masses remain unset.",
            processing_evidence="Simulation assumption: externally referenced isotope means are retained, with no second normalization. The displayed current mock qualification is a comparison model, not evidence of the original historical Qtegra/ISODAT calibration. Qtegra drift correction is disabled."))
        seen={}; files=[]; imported=[]; unique=0; archived_only=0
        for path,relative,parsed in candidates:
            content=path.read_bytes(); keys=[acquisition_key(r) for r in parsed["measurements"]]
            if all(key in seen for key in keys):
                disposition="Alternative / duplicate raw export; acquisition rows already represented by preferred main-folder exports"
                run_id=seen[keys[0]][0]; archived_only+=1
            else:
                run=service.import_run(path.name,content,RunCommand(**REVIEW,results_session_id=session["id"],label=path.stem,sample_group=path.stem))
                run_id=run["id"]; imported.append(run_id)
                with service.repo.connect() as db:
                    measurements=service.repo.list(db,"measurements",run_id=run_id)
                    excluded={e["measurement_id"] for e in service.repo.list(db,"exclusions",run_id=run_id)}
                overlap=0
                for row in measurements:
                    key=acquisition_key(row)
                    if key in seen:
                        overlap+=1
                        if row["id"] not in excluded:
                            service.exclude(run_id,ExclusionCommand(actor=REVIEW["actor"],reason="Repeated acquisition in overlapping raw exports; retain one observation per acquisition.",
                                measurement_id=row["id"],evidence=f"Preferred raw export {seen[key][1]}, run {seen[key][0]}; matching acquisition time, label and sample identifier. Alternative original bytes retained."))
                    else:
                        seen[key]=(run_id,relative); unique+=1
                if overlap: service.evaluate_run(run_id,EvaluateCommand(**REVIEW))
                disposition=f"Imported raw acquisition; {overlap} overlapping observations retained but excluded from statistics"
                print(f"Imported {project}: {path.name} ({len(measurements)} analyses, {overlap} overlaps)",flush=True)
            asset=service.archive_session_source(session["id"],path.name,content,Decision(**REVIEW),relative_path=relative,disposition=disposition,run_id=run_id)
            files.append({"source_id":asset["id"],"run_id":run_id,"path":relative,"sha256":hashlib.sha256(content).hexdigest(),"disposition":disposition})
        export=service.export_results_session(session["id"],SessionExportCommand(**REVIEW)) if imported else None
        manifest["sessions"].append({"id":session["id"],"name":folder,"client":client,"project":project,"run_count":len(imported),
            "unique_analyses":unique,"archived_alternatives":archived_only,"raw_files":len(files),"files":files,"export_id":export["id"] if export else None})
    manifest["counts"]={"sessions":len(manifest["sessions"]),"raw_workbooks":sum(s["run_count"] for s in manifest["sessions"]),
        "unique_analyses":sum(s["unique_analyses"] for s in manifest["sessions"]),"archived_alternatives":sum(s["archived_alternatives"] for s in manifest["sessions"])}
    with service.repo.connect() as db:
        real_run_ids={r for s in manifest["sessions"] for r in service.repo.get(db,"results_sessions",s["id"])["run_ids"]}
        evaluations=[service.repo.get(db,"evaluations",r["latest_evaluation_id"]) for r in service.repo.list(db,"runs") if r["id"] in real_run_ids]
        manifest["counts"]["observed_qc"]=sum(r["role"]=="qc" and not r["excluded"] for e in evaluations for r in e["results"])
        manifest["counts"]["unknowns"]=sum(r["role"]=="unknown" and not r["excluded"] for e in evaluations for r in e["results"])
    marker.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",default=".data/metrology-operational")
    parser.add_argument("--source-root",default=str(Path.home()/"Documents/P2L/MAT253 + Kiel IV/MAIN RESULTS FOLDER"))
    args=parser.parse_args()
    result=populate_operational(args.data_dir,args.source_root)
    print(json.dumps(result["counts"],indent=2))
