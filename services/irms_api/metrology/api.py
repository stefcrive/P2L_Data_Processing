from __future__ import annotations

import json
import os
from pathlib import Path
from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError

from .models import (
    AnnotationCommand, ApproveCommand, Decision, EffectCommand, EvaluateCommand, ExclusionCommand,
    InterventionCommand, MaterialCommand, MethodCommand, PeriodCommand, QualificationCommand,
    ReleaseCommand, RunCommand, TestCommand,
    OutlierScreeningCommand, ResultsSessionCommand, SessionGroupsCommand, SessionExportCommand, RowReviewCommand, ResidualOverrideCommand, SessionChartSettingsCommand,
)
from .repository import Repository, encode
from .reports import build_report
from .pipeline import process_value
from .service import Conflict, Service

router = APIRouter(prefix="/metrology", tags=["metrology"])


@lru_cache(maxsize=4)
def _service(root, demo=False):
    return Service(Repository(root, demo=demo))


def get_service():
    return _service(os.getenv("IRMS_METROLOGY_DATA_DIR", ".data/metrology"), os.getenv("IRMS_METROLOGY_DEMO") == "1")


def invoke(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, str(exc)) from exc


def download(content, filename, media_type="application/octet-stream"):
    return Response(content, media_type=media_type, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}", "Cache-Control": "no-store"})


@router.get("/state")
def state(s: Service = Depends(get_service)):
    return s.state()


@router.get("/demo")
def demo_info(s: Service = Depends(get_service)):
    path = s.repo.root / "demo-manifest.json"
    if not s.repo.demo or not path.exists():
        raise HTTPException(404, "No populated demo workspace")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/demo/files/{filename}")
def demo_file(filename: str, s: Service = Depends(get_service)):
    manifest = demo_info(s)
    if filename not in {f["filename"] for f in manifest["files"]} or Path(filename).name != filename:
        raise HTTPException(404, "Demo workbook not found")
    return download((s.repo.root / "examples" / filename).read_bytes(), filename,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.post("/materials")
def material(command: MaterialCommand, s: Service = Depends(get_service)):
    return invoke(s.save_material, command)


@router.post("/results-sessions")
def create_results_session(command: ResultsSessionCommand, s: Service = Depends(get_service)):
    return invoke(s.save_results_session,command)


@router.put("/results-sessions/{id_}")
def update_results_session(id_: str, command: ResultsSessionCommand, s: Service = Depends(get_service)):
    return invoke(s.save_results_session,command,id_)


@router.get("/results-sessions/{id_}")
def results_session(id_: str, s: Service = Depends(get_service)):
    return invoke(s.results_session_detail,id_)


@router.post("/results-sessions/{id_}/groups")
def results_groups(id_: str, command: SessionGroupsCommand, s: Service = Depends(get_service)):
    return invoke(s.save_session_groups,id_,command)


@router.put("/results-sessions/{id_}/chart-settings")
def session_chart_settings(id_: str, command: SessionChartSettingsCommand, s: Service = Depends(get_service)):
    return invoke(s.save_chart_settings, id_, command)


@router.put("/results-sessions/{id_}/residual-overrides")
def residual_override(id_: str, command: ResidualOverrideCommand, s: Service = Depends(get_service)):
    return invoke(s.save_residual_override, id_, command)


@router.post("/results-sessions/{id_}/exports")
def results_export(id_: str, command: SessionExportCommand, s: Service = Depends(get_service)):
    return invoke(s.export_results_session,id_,command)


@router.get("/session-exports/{id_}")
def exported_results(id_: str, s: Service = Depends(get_service)):
    with s.repo.connect() as db:
        record=invoke(s.repo.get,db,"session_exports",id_)
        return download(invoke(s.repo.read_blob,record["sha256"]),record["filename"],
                        {"csv":"text/csv; charset=utf-8","json":"application/json","zip":"application/zip", "pdf":"application/pdf", "xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[record["format"]])


@router.get("/results-sessions/{id_}/analysis")
def session_analysis_view(id_: str, outlier_method: str | None = None, threshold: float | None = None, s: Service = Depends(get_service)):
    return invoke(s.results_session_analysis, id_, outlier_method, threshold)


@router.put("/results-sessions/{id_}/outlier-screening")
def session_outlier_screening(id_: str, command: OutlierScreeningCommand, s: Service = Depends(get_service)):
    return invoke(s.save_outlier_screening, id_, command)


@router.post("/runs/{id_}/row-review")
def row_review(id_: str, command: RowReviewCommand, s: Service = Depends(get_service)):
    return invoke(s.review_row, id_, command)


@router.post("/results-sessions/{id_}/tools/{run_id}")
def results_tools(id_: str, run_id: str, command: Decision, s: Service = Depends(get_service)):
    from .plot_bridge import open_plot_bridge
    return invoke(open_plot_bridge, s, id_, run_id, command)


@router.get("/session-sources/{id_}")
def session_source(id_: str, s: Service = Depends(get_service)):
    with s.repo.connect() as db:
        record=invoke(s.repo.get,db,"session_sources",id_)
        return download(invoke(s.repo.read_blob,record["sha256"]),record["filename"])


@router.post("/methods")
def create_method(command: MethodCommand, s: Service = Depends(get_service)):
    return invoke(s.save_method, command)


@router.put("/methods/{id_}")
def update_method(id_: str, command: MethodCommand, s: Service = Depends(get_service)):
    return invoke(s.save_method, command, id_)


@router.post("/methods/{id_}/approve")
def approve(id_: str, command: ApproveCommand, s: Service = Depends(get_service)):
    return invoke(s.approve, id_, command)


@router.post("/methods/{id_}/retire")
def retire(id_: str, command: Decision, s: Service = Depends(get_service)):
    return invoke(s.retire_method, id_, command)


@router.post("/methods/{id_}/activate")
def activate(id_: str, command: Decision, s: Service = Depends(get_service)):
    return invoke(s.activate_method, id_, command)


@router.post("/qualifications")
def qualification(command: QualificationCommand, s: Service = Depends(get_service)):
    return invoke(s.create_qualification, command)


@router.post("/qualifications/{id_}/tests")
def qualification_test(id_: str, command: TestCommand, s: Service = Depends(get_service)):
    return invoke(s.save_test, id_, command)


@router.post("/qualifications/{id_}/effects")
def effect(id_: str, command: EffectCommand, s: Service = Depends(get_service)):
    return invoke(s.save_effect, id_, command)


@router.post("/qualifications/{id_}/assets")
def asset(id_: str, file: UploadFile = File(...), actor: str = Form(...), reason: str = Form(...),
          interpretation: str = Form(...), s: Service = Depends(get_service)):
    decision = invoke(Decision, actor=actor, reason=reason)
    return invoke(s.attach, id_, file.filename or "evidence", file.file.read(25 * 1024 * 1024 + 1), decision, interpretation)


@router.get("/assets/{id_}")
def get_asset(id_: str, s: Service = Depends(get_service)):
    with s.repo.connect() as db:
        asset = invoke(s.repo.get, db, "assets", id_)
        return download(invoke(s.repo.read_blob, asset["sha256"]), asset["filename"])


@router.post("/runs/import")
def import_run(file: UploadFile = File(...), metadata: str = Form(...), s: Service = Depends(get_service)):
    command = invoke(RunCommand.model_validate_json, metadata)
    content = file.file.read(75 * 1024 * 1024 + 1)
    if len(content) > 75 * 1024 * 1024:
        raise HTTPException(413, "Workbook exceeds the 75 MB import limit")
    return invoke(s.import_run, file.filename or "workbook.xlsx", content, command)


@router.get("/runs/{id_}")
def run(id_: str, s: Service = Depends(get_service)):
    return invoke(s.run_detail, id_)


@router.get("/sources/{id_}")
def source(id_: str, s: Service = Depends(get_service)):
    with s.repo.connect() as db:
        source = invoke(s.repo.get, db, "raw_imports", id_)
        return download(invoke(s.repo.read_blob, source["sha256"]), source["filename"])


@router.get("/measurements/{id_}")
def raw_measurement(id_: str, s: Service = Depends(get_service)):
    with s.repo.connect() as db:
        return invoke(s.repo.get, db, "measurements", id_)


@router.post("/runs/{id_}/annotations")
def annotate(id_: str, command: AnnotationCommand, s: Service = Depends(get_service)):
    return invoke(s.annotate, id_, command)


@router.post("/runs/{id_}/exclusions")
def exclude(id_: str, command: ExclusionCommand, s: Service = Depends(get_service)):
    return invoke(s.exclude, id_, command)


@router.post("/runs/{id_}/evaluate")
def evaluation(id_: str, command: EvaluateCommand, s: Service = Depends(get_service)):
    return invoke(s.evaluate_run, id_, command)


@router.post("/runs/{id_}/release")
def release(id_: str, command: ReleaseCommand, s: Service = Depends(get_service)):
    return invoke(s.release, id_, command)


@router.post("/interventions")
def intervention(command: InterventionCommand, s: Service = Depends(get_service)):
    return invoke(s.intervention, command)


@router.post("/periods")
def period(command: PeriodCommand, s: Service = Depends(get_service)):
    return invoke(s.create_period, command)


@router.get("/history")
def history(method_id: str | None = None, period_id: str | None = None, s: Service = Depends(get_service)):
    with s.repo.connect() as db:
        return invoke(s.qc_history, db, method_id, period_id)


@router.get("/evaluations/{id_}/monte-carlo")
def monte_carlo(id_: str, measurement_id: str, isotope: Literal["d13c", "d18o"], s: Service = Depends(get_service)):
    with s.repo.connect() as db:
        evaluation = invoke(s.repo.get, db, "evaluations", id_)
        row = next((r for r in evaluation["results"] if r["id"] == measurement_id), None)
        if row is None or isotope not in evaluation["normalization"] or row[isotope] is None:
            raise HTTPException(422, "Choose a measured isotope with an available normalization")
        model = evaluation["normalization"][isotope]
        correction = model.get("correction")
        run = s.repo.get(db, "runs", evaluation["run_id"])
        return invoke(process_value, row, isotope, model, run, monte_carlo=True)


class ReportCommand(Decision):
    kind: Literal["client", "qualification", "history"]
    target_id: str | None = None


@router.post("/reports")
def report(command: ReportCommand, s: Service = Depends(get_service)):
    # Persist the exact source snapshot and rendered bytes, so later downloads never regenerate history.
    with s.repo.connect(write=True) as db:
        snapshot = {"generated_at": s.repo.timestamp(), "kind": command.kind, "simulation": s.repo.demo}
        if command.kind == "client":
            snapshot["release"] = invoke(s.repo.get, db, "releases", command.target_id)
        elif command.kind == "qualification":
            q = invoke(s.qualification_detail, db, command.target_id)
            method = s.repo.get(db, "methods", q["method_id"])
            runs = s.repo.list(db, "runs", qualification_id=q["id"])
            snapshot.update(qualification=q, method=method, materials=s.material_map(db, method),
                            evaluations=[s.repo.get(db, "evaluations", r["latest_evaluation_id"]) for r in runs if r["latest_evaluation_id"]],
                            audit=[e for e in s.repo.audit_log(db, limit=None) if e["entity_id"] in {q["id"], method["id"], *[r["id"] for r in runs]}])
        else:
            methods = s.repo.list(db, "methods")
            current = next((m for m in reversed(methods) if m["status"] == "active"), methods[-1] if methods else None)
            snapshot.update(history=s.qc_history(db), periods=s.repo.list(db, "periods"), interventions=s.repo.list(db, "interventions"),
                            qualifications=s.repo.list(db, "qualifications"),
                            laboratory=current["config"].get("laboratory", "") if current else "")
        content = build_report(command.kind, snapshot, s.repo)
        digest = s.repo.blob(content)
        snapshot_digest = s.repo.blob(encode(snapshot).encode("utf-8"))
        record = s.repo.insert(db, "reports", {"kind": command.kind, "target_id": command.target_id, "sha256": digest,
                                             "snapshot_sha256": snapshot_digest, "actor": command.actor, "reason": command.reason})
        s.repo.audit(db, "report_generated", record["id"], command.actor, command.reason, after=record)
        return record


@router.post("/reports/{id_}/reissue")
def reissue_report(id_: str, command: Decision, s: Service = Depends(get_service)):
    """Render a new edition from a saved snapshot while preserving the original PDF."""
    with s.repo.connect(write=True) as db:
        original = invoke(s.repo.get, db, "reports", id_)
        snapshot = json.loads(invoke(s.repo.read_blob, original["snapshot_sha256"]))
        content = build_report(original["kind"], snapshot, s.repo)
        record = s.repo.insert(db, "reports", {
            "kind": original["kind"], "target_id": original["target_id"],
            "sha256": s.repo.blob(content), "snapshot_sha256": original["snapshot_sha256"],
            "reissued_from": id_, "actor": command.actor, "reason": command.reason,
        })
        s.repo.audit(db, "report_reissued", record["id"], command.actor, command.reason,
                     after={"report_id": record["id"], "reissued_from": id_})
        return record


@router.get("/reports/{id_}/{format_}")
def get_report(id_: str, format_: Literal["pdf", "json"], s: Service = Depends(get_service)):
    with s.repo.connect() as db:
        report = invoke(s.repo.get, db, "reports", id_)
        digest = report["sha256"] if format_ == "pdf" else report["snapshot_sha256"]
        return download(invoke(s.repo.read_blob, digest), f"irms-{report['kind']}-{id_[:8]}.{format_}", "application/pdf" if format_ == "pdf" else "application/json")


@router.get("/runs/{id_}/measurements/{measurement_id}/evidence")
def measurement_evidence(id_: str, measurement_id: str, s: Service = Depends(get_service)):
    from .analysis_evidence import analysis_evidence
    with s.repo.connect() as db:
        run = invoke(s.repo.get, db, "runs", id_)
        row = invoke(s.repo.get, db, "measurements", measurement_id)
        if row["run_id"] != id_:
            raise HTTPException(404, "Analysis does not belong to this workbook")
        source = invoke(s.repo.get, db, "raw_imports", run["raw_import_id"])
        return analysis_evidence(row, source)
