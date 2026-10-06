from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import SOFTWARE_VERSION
from .importer import parse_workbook
from .models import (
    EVENT_TESTS, ISOTOPES, SAMPLE_TYPES, TESTS, ApproveCommand, Decision,
    Material, MethodConfig,
)
from .pipeline import diagnostics, evaluate, identify
from .repository import Repository
from .science import control_summary, summary
from .sessions import ResultsSessions


class Conflict(ValueError):
    pass


def acquisition_time(value, zone):
    if not value:
        raise ValueError("Missing acquisition time")
    timestamp = datetime.fromisoformat(str(value))
    if timestamp.tzinfo is None:
        tz = ZoneInfo(zone)
        if timestamp.replace(tzinfo=tz, fold=0).utcoffset() != timestamp.replace(tzinfo=tz, fold=1).utcoffset():
            raise ValueError("Ambiguous acquisition time at a timezone transition")
        timestamp = timestamp.replace(tzinfo=tz)
    return timestamp.astimezone(timezone.utc)


class Service(ResultsSessions):
    def __init__(self, repository: Repository):
        self.repo = repository

    def material_map(self, db, method):
        ids = [*method["config"]["anchor_ids"], method["config"]["qc_id"]]
        return {id_: self.repo.get(db, "materials", id_) for id_ in ids if id_}

    def check_aliases(self, db, config):
        seen = {}
        for id_ in [*config.anchor_ids, config.qc_id]:
            if not id_:
                continue
            material = self.repo.get(db, "materials", id_)
            for alias in {material["name"], *material["aliases"]}:
                key = alias.strip().casefold()
                if key in seen and seen[key] != id_:
                    raise ValueError(f"Material alias '{alias}' is ambiguous")
                seen[key] = id_

    def save_material(self, command):
        with self.repo.connect(write=True) as db:
            previous = self.repo.get(db, "materials", command.material.revision_of) if command.material.revision_of else None
            record = self.repo.insert(db, "materials", command.material.model_dump())
            self.repo.audit(db, "material_revision_created", record["id"], command.actor, command.reason, previous, record)
            # Draft methods follow an explicitly edited material. Approved snapshots never change.
            if previous:
                for method in self.repo.list(db, "methods", status="draft"):
                    config = method["config"]
                    if previous["id"] in config["anchor_ids"] or previous["id"] == config["qc_id"]:
                        config["anchor_ids"] = [record["id"] if id_ == previous["id"] else id_ for id_ in config["anchor_ids"]]
                        if config["qc_id"] == previous["id"]:
                            config["qc_id"] = record["id"]
                        self.check_aliases(db, MethodConfig.model_validate(config))
                        data = {"config": config, "version": method["version"], "revision": method["revision"] + 1}
                        self.repo.update(db, "methods", method["id"], data)
                        self.repo.audit(db, "draft_material_updated", method["id"], command.actor, command.reason, after={"material_id": record["id"]})
            return record

    def save_method(self, command, method_id=None):
        with self.repo.connect(write=True) as db:
            self.check_aliases(db, command.config)
            before = self.repo.get(db, "methods", method_id) if method_id else None
            if before and before["status"] != "draft":
                raise Conflict("Approved method is frozen. Create a new draft version.")
            version = before["version"] if before else max([m["version"] for m in self.repo.list(db, "methods")] or [0]) + 1
            data = {"config": command.config.model_dump(), "version": version, "revision": before["revision"] + 1 if before else 1}
            if before:
                self.repo.update(db, "methods", method_id, data)
                result = self.repo.get(db, "methods", method_id)
            else:
                result = self.repo.insert(db, "methods", data, status="draft")
            self.repo.audit(db, "method_draft_saved", result["id"], command.actor, command.reason, before, result)
            return result

    def create_qualification(self, command):
        with self.repo.connect(write=True) as db:
            method = self.repo.get(db, "methods", command.method_id)
            if method["status"] not in ("draft", "active"):
                raise Conflict("Choose a draft or active method")
            tests = command.tests or method["config"]["required_tests"]
            event = None
            if command.trigger == "event":
                if not command.intervention_id:
                    raise ValueError("An event verification must reference an intervention")
                event = self.repo.get(db, "interventions", command.intervention_id)
                if event["status"] != "open" or event["instrument"] != method["config"]["instrument"]:
                    raise ValueError("Choose an open intervention for this instrument")
                if method["status"] == "draft":
                    raise ValueError("First qualification must be periodic; targeted verification uses an active method")
                tests = sorted(set(tests if command.tests else []) | set(event["required_tests"]))
            else:
                tests = sorted(set(tests) | set(method["config"]["required_tests"]))
            for slot in command.carousel:
                if slot.material_id not in [*method["config"]["anchor_ids"], method["config"]["qc_id"]]:
                    raise ValueError("Carousel materials must belong to the selected method")
            record = self.repo.insert(db, "qualifications", {**command.model_dump(exclude={"method_id"}),
                                      "required_tests": tests, "protocol_revision": method["revision"],
                                      "initial_method_snapshot": method, "effects": {}},
                                      method_id=method["id"], status="open")
            self.repo.audit(db, "qualification_created", record["id"], command.actor, command.reason, after=record)
            return record

    def qualification_detail(self, db, id_):
        record = self.repo.get(db, "qualifications", id_)
        record["tests"] = self.repo.list(db, "tests", qualification_id=id_)
        record["assets"] = self.repo.list(db, "assets", qualification_id=id_)
        # Older approved records already have an immutable evaluation snapshot.
        # Expose it without rewriting their audit history.
        if record.get("approval") and not record.get("method_snapshot"):
            evaluation = self.repo.get(db, "evaluations", record["approval"]["evaluation_id"])
            record["method_snapshot"] = evaluation["method_snapshot"]
        return record

    def check_open_qualification(self, db, id_):
        record = self.repo.get(db, "qualifications", id_)
        if record["status"] != "open":
            raise Conflict("Approved qualification evidence is frozen")
        return record

    def save_test(self, id_, command):
        with self.repo.connect(write=True) as db:
            self.check_open_qualification(db, id_)
            result = self.repo.insert(db, "tests", command.model_dump(), qualification_id=id_)
            self.repo.audit(db, "qualification_test_recorded", id_, command.actor, command.reason, after=result)
            return result

    def save_effect(self, id_, command):
        with self.repo.connect(write=True) as db:
            record = self.check_open_qualification(db, id_)
            before = record.get("effects", {}).get(command.effect)
            record.setdefault("effects", {})[command.effect] = {**command.model_dump(), "at": self.repo.timestamp()}
            self.repo.update(db, "qualifications", id_, record)
            self.repo.audit(db, "effect_reviewed", id_, command.actor, command.reason, before, command.model_dump())
            return record

    def attach(self, id_, filename, content, decision, interpretation):
        if not content or len(content) > 25 * 1024 * 1024:
            raise ValueError("Evidence must be between 1 byte and 25 MB")
        digest = self.repo.blob(content)
        with self.repo.connect(write=True) as db:
            self.check_open_qualification(db, id_)
            record = self.repo.insert(db, "assets", {"filename": Path(filename.replace("\\", "/")).name,
                                      "sha256": digest, "size": len(content), "interpretation": interpretation,
                                      **decision.model_dump()}, qualification_id=id_)
            self.repo.audit(db, "evidence_attached", id_, decision.actor, decision.reason, after=record)
            return record

    def import_run(self, filename, content, command):
        parsed = parse_workbook(content, filename)
        digest = self.repo.blob(content)
        with self.repo.connect(write=True) as db:
            session = self.prepare_session_import(db, command)
            duplicates = self.repo.list(db, "raw_imports", sha256=digest)
            if duplicates:
                existing = [r for r in self.repo.list(db, "runs") if r["raw_import_id"] == duplicates[0]["id"]]
                if existing:
                    if session:
                        self.attach_session_run(db, session, existing[0], command)
                    return {**existing[0], "duplicate": True}
            method = self.repo.get(db, "methods", command.method_id) if command.method_id else None
            if method is None and command.context == "routine":
                active = self.repo.list(db, "methods", status="active")
                method = active[-1] if active else None
            if command.context == "qualification":
                if not command.qualification_id:
                    raise ValueError("Choose a qualification session")
                qualification = self.check_open_qualification(db, command.qualification_id)
                method = method or self.repo.get(db, "methods", qualification["method_id"])
                if qualification["method_id"] != method["id"]:
                    raise ValueError("Qualification and run must use the same method")
            elif command.qualification_id:
                raise ValueError("Routine runs cannot be attached as qualification data")
            if method and command.context == "routine" and method["status"] != "active":
                raise ValueError("Routine runs use the active method; import unassigned until a method is approved")
            source = self.repo.insert(db, "raw_imports", {k: v for k, v in parsed.items() if k != "measurements"} |
                                     {"filename": Path(filename.replace("\\", "/")).name, "size": len(content), "operator": command.actor}, sha256=digest)
            acquisition = [str(r["acquired_at"])[:10] for r in parsed["measurements"] if r["acquired_at"]]
            events = self.repo.list(db, "interventions")
            instrument = method["config"]["instrument"] if method else "Kiel IV + MAT253 Plus + Dual Inlet"
            zone = method["config"].get("acquisition_timezone", "America/Sao_Paulo") if method else "America/Sao_Paulo"
            try:
                started = min(acquisition_time(r["acquired_at"], zone) for r in parsed["measurements"])
            except ValueError:
                started = None
            matching_events = [e for e in events if e["instrument"] == instrument and started and datetime.fromisoformat(e["created_at"]) <= started]
            period_key = matching_events[-1]["id"] if matching_events else "initial"
            run = self.repo.insert(db, "runs", {"label": command.label or source["filename"], "context": command.context,
                                   "operator": command.actor, "revision": 1, "masses_ug": {}, "acquisition_complete": False,
                                   "synthetic": parsed["synthetic"], "simulation": self.repo.demo,"source_kind":parsed.get("source_kind","qtegra_raw"),
                                   "calibration_verification":session.get("calibration_verification","documented") if session else "documented",
                                   "input_basis": command.input_basis, "external_method_id": command.external_method_id,
                                   "processing_evidence": command.processing_evidence, "preapplied_corrections": command.preapplied_corrections,
                                   "latest_evaluation_id": None, "period_key": period_key,
                                   "acquired_date": min(acquisition) if acquisition else None,
                                   "analysis_count": parsed["analysis_count"], "type_counts": parsed["type_counts"],
                                   "warnings": parsed["warnings"]}, raw_import_id=source["id"], method_id=method["id"] if method else None,
                                   qualification_id=command.qualification_id, status="imported")
            for row in parsed["measurements"]:
                self.repo.insert(db, "measurements", row, run_id=run["id"])
            if session:
                self.attach_session_run(db, session, run, command)
            self.repo.audit(db, "raw_run_imported", run["id"], command.actor, command.reason, after={"sha256": digest, "raw_import_id": source["id"], "analyses": parsed["analysis_count"]})
        # Record QC automatically whenever a method is assigned, including failed evaluations.
        if method:
            from .models import EvaluateCommand
            self.evaluate_run(run["id"], EvaluateCommand(actor=command.actor, reason="Initial processing of imported run", method_id=method["id"]))
        if session:
            self.archive_session_source(session["id"],source["filename"],content,command,relative_path=source["filename"],
                                        disposition="Imported raw acquisition",run_id=run["id"])
        if not session:
            self.repo.migrate_results_sessions()
        with self.repo.connect() as db:
            return self.repo.get(db, "runs", run["id"])

    def mutable_run(self, db, id_):
        run = self.repo.get(db, "runs", id_)
        if run["status"] == "released":
            raise Conflict("Released results and run inputs are frozen")
        if run["qualification_id"]:
            self.check_open_qualification(db, run["qualification_id"])
        return run

    def annotate(self, id_, command):
        with self.repo.connect(write=True) as db:
            run = self.mutable_run(db, id_)
            ids = {r["id"] for r in self.repo.list(db, "measurements", run_id=id_)}
            if not set(command.masses_ug).issubset(ids):
                raise ValueError("Mass annotations reference a different run")
            before = dict(run)
            session = next((s for s in self.repo.list(db,"results_sessions") if id_ in s["run_ids"]),None)
            if session and any(key in command.model_fields_set and getattr(command,key)!=run.get(key)
                               for key in ("input_basis","external_method_id","processing_evidence","preapplied_corrections")):
                raise Conflict("Processing provenance is pinned to this results session. Create a separate session for different input assumptions.")
            run["masses_ug"] = {**run.get("masses_ug", {}), **command.masses_ug}
            if command.acquisition_complete is not None:
                run["acquisition_complete"] = command.acquisition_complete
            for key in ("input_basis", "external_method_id", "processing_evidence", "preapplied_corrections"):
                if key in command.model_fields_set:
                    run[key] = getattr(command, key)
            run["revision"] += 1
            self.repo.update(db, "runs", id_, run, "review_required")
            self.repo.audit(db, "run_annotated", id_, command.actor, command.reason, before, run)
            return run

    def exclude(self, id_, command):
        with self.repo.connect(write=True) as db:
            run = self.mutable_run(db, id_)
            measurement = self.repo.get(db, "measurements", command.measurement_id)
            if measurement["run_id"] != id_:
                raise ValueError("Measurement belongs to another run")
            if self.repo.list(db, "exclusions", measurement_id=command.measurement_id):
                raise Conflict("Measurement already has an exclusion record")
            record = self.repo.insert(db, "exclusions", command.model_dump(exclude={"measurement_id"}), run_id=id_, measurement_id=command.measurement_id)
            run["revision"] += 1
            self.repo.update(db, "runs", id_, run, "review_required")
            self.repo.audit(db, "measurement_excluded", id_, command.actor, command.reason, measurement, record)
            return record

    def evaluate_run(self, id_, command):
        with self.repo.connect(write=True) as db:
            run = self.mutable_run(db, id_)
            method_id = command.method_id or run["method_id"]
            if not method_id:
                raise ValueError("Select a method version before evaluation")
            if run["method_id"] and run["method_id"] != method_id:
                raise Conflict("Run is already associated with another method version")
            method = self.repo.get(db, "methods", method_id)
            if run["context"] == "routine" and method["status"] != "active":
                raise Conflict("Routine evaluation requires an active method")
            if run["qualification_id"] and self.repo.get(db, "qualifications", run["qualification_id"])["method_id"] != method_id:
                raise ValueError("Method does not match qualification")
            if not run["method_id"]:
                db.execute("UPDATE runs SET method_id=? WHERE id=?", (method_id, id_))
                run["method_id"] = method_id
            result = evaluate(run, method, self.material_map(db, method), self.repo.list(db, "measurements", run_id=id_), self.repo.list(db, "exclusions", run_id=id_), demo=self.repo.demo)
            evaluation = self.repo.insert(db, "evaluations", {**result, "software_version": SOFTWARE_VERSION, "operator": command.actor}, run_id=id_, method_id=method_id)
            for row in result["results"]:
                if row["role"] == "qc":
                    self.repo.insert(db, "qc_observations", {"isotopes": row["isotopes"], "raw": {i: row[i] for i in ISOTOPES},
                                     "excluded": row["excluded"], "issues": row["issues"], "material_id": row["material_id"],
                                     "acquired_at": row["acquired_at"], "sequence": row["sequence"], "period_key": run["period_key"],
                                     "configuration": method["config"]["configuration"], "operator": run["operator"], "qc_passed": result["qc"]["passed"]},
                                     evaluation_id=evaluation["id"], run_id=id_, method_id=method_id, measurement_id=row["id"])
            run["latest_evaluation_id"] = evaluation["id"]
            run["qc_summary"] = result["qc"]
            self.repo.update(db, "runs", id_, run, "awaiting_review" if result["ready"] else "blocked")
            self.repo.audit(db, "run_evaluated", id_, command.actor, command.reason, after={"evaluation_id": evaluation["id"], "blockers": result["blockers"]})
            return evaluation

    def review_row(self, id_, command):
        """Accept a documented sample exception without inventing a result or clearing QC gates."""
        with self.repo.connect(write=True) as db:
            run = self.mutable_run(db, id_)
            evaluation = self.repo.get(db, "evaluations", command.evaluation_id)
            if (run["latest_evaluation_id"] != evaluation["id"] or evaluation["run_id"] != id_
                    or run["revision"] != evaluation["run_revision"]):
                raise Conflict("Review the latest evaluation of this run")
            row = next((r for r in evaluation["results"] if r["id"] == command.measurement_id), None)
            if not row or row["role"] != "unknown" or row["excluded"]:
                raise ValueError("Acceptance overrides apply to unknown samples; QC requires evidence and an explicit exclusion or a new qualification")
            if not all(row.get("isotopes", {}).get(iso, {}).get("budget") for iso in ISOTOPES):
                raise ValueError("A complete calculated value and uncertainty for both isotopes is required; missing inputs cannot be overridden")
            allowed = {issue for issue in row["issues"] if (
                "internal SD" in issue or "extrapolates beyond" in issue or "outside validated isotope range" in issue
                or issue.endswith("missing or outside validated range"))}
            if not set(command.issues).issubset(allowed):
                raise ValueError("Resolve acquisition, identity and calculation failures in their source fields before review")
            review = {**command.model_dump(), "at": self.repo.timestamp(), "run_revision": run["revision"]}
            row.setdefault("accepted_issues", []).extend(issue for issue in command.issues if issue not in row.get("accepted_issues", []))
            row.setdefault("reviews", []).append(review)
            row["issues"] = [issue for issue in row["issues"] if issue not in command.issues]
            removed = {f"Analysis {row['source_index']}: {issue}" for issue in command.issues}
            evaluation["blockers"] = [issue for issue in evaluation["blockers"] if issue not in removed]
            evaluation["ready"] = not evaluation["blockers"]
            data = {key: value for key, value in evaluation.items() if key not in ("id", "created_at", "run_id", "method_id")}
            data["supersedes_evaluation_id"] = evaluation["id"]
            updated = self.repo.insert(db, "evaluations", data, run_id=id_, method_id=run["method_id"])
            for observation in self.repo.list(db, "qc_observations", evaluation_id=evaluation["id"]):
                values = {k: v for k, v in observation.items() if k not in ("id", "created_at", "evaluation_id", "run_id", "method_id", "measurement_id")}
                self.repo.insert(db, "qc_observations", values, evaluation_id=updated["id"], run_id=id_, method_id=run["method_id"], measurement_id=observation["measurement_id"])
            run["latest_evaluation_id"] = updated["id"]
            self.repo.update(db, "runs", id_, run, "awaiting_review" if updated["ready"] else "blocked")
            self.repo.audit(db, "sample_acceptance_exception", id_, command.actor, command.reason, after=review)
            return updated

    def qualification_gates(self, db, qualification, method, evaluation):
        errors = list(evaluation["blockers"])
        run = self.repo.get(db, "runs", evaluation["run_id"])
        if run["qualification_id"] != qualification["id"] or evaluation["method_id"] != method["id"]:
            errors.append("Evaluation must belong to this qualification and method")
        if run["latest_evaluation_id"] != evaluation["id"] or run["revision"] != evaluation["run_revision"] or method["revision"] != evaluation["method_revision"]:
            errors.append("Evaluation is stale; evaluate the latest inputs")
        tests = {t["name"]: t for t in self.repo.list(db, "tests", qualification_id=qualification["id"])}
        required = set(qualification["required_tests"])
        if qualification["trigger"] == "periodic":
            required.update(method["config"]["required_tests"])
        for name in sorted(required):
            if name not in tests or tests[name]["result"] != "pass":
                errors.append(f"Qualification test requires a passing review: {name}")
        effects = qualification.get("effects", {})
        required_effects = ("mass_intensity", "pressure_adjustment", "drift", "memory", "bias") if qualification["trigger"] == "periodic" else ("bias",)
        for effect in required_effects:
            if effect not in effects or effects[effect]["decision"] == "investigate":
                errors.append(f"Complete the {effect.replace('_', ' ')} decision with supporting evidence")
        config = MethodConfig.model_validate(method["config"])
        assets = {a["id"] for a in self.repo.list(db, "assets", qualification_id=qualification["id"])}
        for iso, correction in (config.corrections.items() if qualification["trigger"] == "periodic" else []):
            if correction is None:
                continue
            if effects.get(correction.effect, {}).get("decision") != "approve_correction":
                errors.append(f"{iso}: explicitly approve the {correction.effect} correction")
            if not set(correction.evidence_asset_ids).issubset(assets):
                errors.append(f"{iso}: attach correction training and validation evidence to this qualification")
            check=evaluation.get("correction_review",{}).get(iso)
            if not check or check["status"]!="eligible_for_review":
                errors.append(f"{iso}: paired QC correction verification is not eligible for approval; " + "; ".join(check["reasons"] if check else ["Re-evaluate with current verification criteria"]))
        for effect, review in effects.items():
            if review["decision"] == "approve_correction" and not any(c and c.effect == effect for c in config.corrections.values()):
                errors.append(f"{effect}: no supported correction equation is configured")
        if not config.laboratory or not config.configuration or config.reaction_temperature_c is None or not config.preparation or not config.acquisition:
            errors.append("Record laboratory, configuration, reaction temperature, preparation and acquisition conditions")
        if config.qualification_interval_days is None:
            errors.append("Define the qualification review interval")
        if qualification["trigger"] == "periodic":
            carousel = qualification["carousel"]
            if not carousel:
                errors.append("A versioned carousel plan is required")
            material_ids = {s["material_id"] for s in carousel}
            if not set([*config.anchor_ids, config.qc_id]).issubset(material_ids):
                errors.append("Carousel must contain both anchors and independent QC")
            rows = [r for r in evaluation["results"] if not r["excluded"] and not r["issues"]]
            for slot in carousel:
                # Explicit matching tolerance, exposed in the UI and report.
                found = [r for r in rows if r["material_id"] == slot["material_id"] and r["mass_ug"] is not None and abs(r["mass_ug"] - slot["mass_ug"]) <= .5]
                if len(found) < slot["replicates"]:
                    errors.append(f"Carousel lacks {slot['replicates']} accepted aliquots at {slot['mass_ug']} µg ±0.5 µg")
            mass_levels = {}
            for slot in carousel:
                if slot["replicates"] >= 2:
                    mass_levels.setdefault(slot["material_id"], set()).add(slot["mass_ug"])
            if not any(len(levels) >= 3 for levels in mass_levels.values()):
                errors.append("Design at least three mass levels with duplicate aliquots of one homogeneous material")
        if config.precision_source == "historical_qc":
            if not config.precision_period:
                errors.append("Select a reviewed homogeneous QC period for historical precision")
            else:
                period = self.repo.get(db, "periods", config.precision_period)
                for iso in ISOTOPES:
                    sd = period["statistics"][iso]["sd"]
                    if sd is None or config.precision[iso] is None or abs(config.precision[iso] - sd) > 1e-9:
                        errors.append(f"{iso} precision must equal the selected period's individual-observation SD")
                if period["configuration"] != config.configuration:
                    errors.append("Historical precision period has a different instrument configuration")
        return list(dict.fromkeys(errors))

    def approve(self, method_id: str, command: ApproveCommand):
        with self.repo.connect(write=True) as db:
            method = self.repo.get(db, "methods", method_id)
            qualification = self.check_open_qualification(db, command.qualification_id)
            evaluation = self.repo.get(db, "evaluations", command.evaluation_id)
            if qualification["method_id"] != method_id or method["status"] not in ("draft", "active"):
                raise Conflict("Qualification and eligible method must match")
            errors = self.qualification_gates(db, qualification, method, evaluation)
            if errors:
                raise Conflict("Approval blocked: " + "; ".join(errors))
            approval = {**command.model_dump(), "at": self.repo.timestamp()}
            if method["status"] == "draft":
                frozen = {**method, "normalization": evaluation["normalization"], "material_snapshots": evaluation["material_snapshots"],
                          "approval": approval, "corrections": evaluation["corrections"]}
                self.repo.update(db, "methods", method_id, frozen)
                if command.activate:
                    for old in self.repo.list(db, "methods", status="active"):
                        self.repo.update(db, "methods", old["id"], status="superseded")
                        self.repo.audit(db, "method_superseded", old["id"], command.actor, command.reason, after={"replacement": method_id})
                self.repo.update(db, "methods", method_id, status="active" if command.activate else "validated")
            qualification["approval"] = approval
            qualification["method_snapshot"] = self.repo.get(db, "methods", method_id)
            self.repo.update(db, "qualifications", qualification["id"], qualification, "approved")
            if qualification.get("intervention_id"):
                event = self.repo.get(db, "interventions", qualification["intervention_id"])
                event["resolved_by"] = qualification["id"]
                self.repo.update(db, "interventions", event["id"], event, "resolved")
                self.repo.audit(db, "intervention_verified", event["id"], command.actor, command.reason, after=approval)
            self.repo.audit(db, "qualification_approved", qualification["id"], command.actor, command.reason, after=approval)
            return self.repo.get(db, "methods", method_id)

    def retire_method(self, method_id, command):
        with self.repo.connect(write=True) as db:
            method = self.repo.get(db, "methods", method_id)
            if method["status"] not in ("active", "superseded", "validated"):
                raise Conflict("Only an approved method can be retired")
            self.repo.update(db, "methods", method_id, status="retired")
            self.repo.audit(db, "method_retired", method_id, command.actor, command.reason, before={"status": method["status"]}, after={"status": "retired"})
            return self.repo.get(db, "methods", method_id)

    def activate_method(self, method_id, command):
        with self.repo.connect(write=True) as db:
            method = self.repo.get(db, "methods", method_id)
            if method["status"] != "validated":
                raise Conflict("Only a validated method awaiting activation can be activated")
            periodic = [q for q in self.repo.list(db, "qualifications", method_id=method_id, status="approved") if q["trigger"] == "periodic"]
            interval = method["config"]["qualification_interval_days"]
            if not periodic or not interval or datetime.fromisoformat(periodic[-1]["approval"]["at"]) + timedelta(days=interval) <= self.repo.instant():
                raise Conflict("A current periodic qualification is required")
            if any(e["instrument"] == method["config"]["instrument"] for e in self.repo.list(db, "interventions", status="open")):
                raise Conflict("Resolve outstanding instrument interventions before activation")
            for old in self.repo.list(db, "methods", status="active"):
                self.repo.update(db, "methods", old["id"], status="superseded")
                self.repo.audit(db, "method_superseded", old["id"], command.actor, command.reason, after={"replacement": method_id})
            self.repo.update(db, "methods", method_id, status="active")
            self.repo.audit(db, "method_activated", method_id, command.actor, command.reason, before={"status": "validated"}, after={"status": "active"})
            return self.repo.get(db, "methods", method_id)

    def release_gates(self, db, run, evaluation):
        errors = list(evaluation["blockers"])
        method = self.repo.get(db, "methods", evaluation["method_id"])
        if run["context"] != "routine":
            errors.append("Only routine unknown results can be released")
        if evaluation["run_id"] != run["id"] or evaluation["method_id"] != run["method_id"]:
            errors.append("Evaluation must belong to this run and method")
        if run["latest_evaluation_id"] != evaluation["id"] or run["revision"] != evaluation["run_revision"]:
            errors.append("Evaluation is stale; re-evaluate the latest inputs")
        if method["status"] != "active":
            errors.append("The associated method is no longer active; review the method state")
        periodic = [q for q in self.repo.list(db, "qualifications", method_id=method["id"], status="approved") if q["trigger"] == "periodic"]
        interval = method["config"]["qualification_interval_days"]
        if not periodic or not interval:
            errors.append("No current periodic qualification")
        else:
            approved_at = max(q["approval"]["at"] for q in periodic)
            due = datetime.fromisoformat(approved_at) + timedelta(days=interval)
            if self.repo.instant() >= due:
                errors.append("Periodic qualification is due")
        events = [e for e in self.repo.list(db, "interventions", status="open") if e["instrument"] == method["config"]["instrument"]]
        if events:
            errors.append("Targeted verification is required after an intervention")
        for group in self.qc_history(db, method["id"]):
            if group.get("data_origin")!=("synthetic" if run.get("synthetic") else "observed"):
                continue
            if group["period_key"] == run["period_key"] and any(s["status"] == "out_of_control" for s in group["isotopes"].values()):
                errors.append("Historical QC control signals require investigation and a reviewed instrument state")
        if not any(r["role"] == "unknown" and not r["excluded"] for r in evaluation["results"]):
            errors.append("There are no unknown sample results to release")
        # Compare complete instants, including the instrument's export timezone.
        try:
            zone = method["config"].get("acquisition_timezone", "America/Sao_Paulo")
            started = min(acquisition_time(r["acquired_at"], zone) for r in evaluation["results"] if not r["excluded"] and r["role"] in ("anchor", "qc", "unknown"))
            activated = next((e["at"] for e in self.repo.audit_log(db, limit=None) if e["entity_id"] == method["id"] and e["action"] == "method_activated"), method.get("approval", {}).get("at"))
            if activated and started < datetime.fromisoformat(activated):
                errors.append("Acquisition predates method approval/activation; retrospective data remain review-only")
            qualified_at_acquisition = any(datetime.fromisoformat(q["approval"]["at"]) <= started < datetime.fromisoformat(q["approval"]["at"]) + timedelta(days=interval or 0) for q in periodic)
            if not qualified_at_acquisition:
                errors.append("No valid periodic qualification covered the acquisition time")
            for event in self.repo.list(db, "interventions"):
                if event["instrument"] != method["config"]["instrument"] or datetime.fromisoformat(event["created_at"]) > started:
                    continue
                verification = self.repo.get(db, "qualifications", event["resolved_by"]) if event.get("resolved_by") else None
                if not verification or datetime.fromisoformat(verification["approval"]["at"]) > started:
                    errors.append("Acquisition occurred before the required intervention verification was approved")
        except (ValueError, KeyError):
            errors.append("Valid acquisition timestamps and their timezone are required for release")
        return list(dict.fromkeys(errors))

    def release(self, id_, command):
        with self.repo.connect(write=True) as db:
            run = self.mutable_run(db, id_)
            evaluation = self.repo.get(db, "evaluations", command.evaluation_id)
            errors = self.release_gates(db, run, evaluation)
            if errors:
                raise Conflict("Release blocked: " + "; ".join(errors))
            source = self.repo.get(db, "raw_imports", run["raw_import_id"])
            self.repo.read_blob(source["sha256"])
            session = next((s for s in self.repo.list(db,"results_sessions") if id_ in s["run_ids"]),None)
            record = self.repo.insert(db, "releases", {"review": command.model_dump(), "at": self.repo.timestamp(), "evaluation": evaluation,
                                      "results_session_snapshot": session,
                                      "simulation": self.repo.demo, "raw_sha256": source["sha256"], "run_snapshot": run, "software_version": SOFTWARE_VERSION,
                                      "results": [r for r in evaluation["results"] if r["role"] == "unknown" and not r["excluded"]]},
                                      run_id=id_, evaluation_id=evaluation["id"], method_id=evaluation["method_id"])
            self.repo.update(db, "runs", id_, status="released")
            self.repo.audit(db, "results_released", id_, command.actor, command.reason, after={"release_id": record["id"], "evaluation_id": evaluation["id"]})
            return record

    def intervention(self, command):
        with self.repo.connect(write=True) as db:
            record = self.repo.insert(db, "interventions", {**command.model_dump(), "required_tests": EVENT_TESTS[command.kind]}, status="open")
            self.repo.audit(db, "intervention_recorded", record["id"], command.actor, command.reason, after=record)
            return record

    def qc_history(self, db, method_id=None, period_id=None):
        filters = {"method_id": method_id} if method_id else {}
        observations = self.repo.list(db, "qc_observations", **filters)
        runs = {r["id"]: r for r in self.repo.list(db, "runs")}
        period = self.repo.get(db, "periods", period_id) if period_id else None
        owners={run_id:session for session in self.repo.list(db,"results_sessions") for run_id in session["run_ids"]}
        groups = {}
        for row in observations:
            run = runs[row["run_id"]]
            # Deliberate qualification mass levels are not a routine precision population.
            if run["context"] != "routine":
                continue
            if period:
                if row["evaluation_id"] not in period["evaluation_ids"]:
                    continue
            elif run["latest_evaluation_id"] != row["evaluation_id"]:
                continue
            origin="synthetic" if run.get("synthetic") else "observed"
            owner=owners.get(run["id"])
            unverified=run.get("calibration_verification")=="simulation_assumption"
            # Retrospective series do not demonstrate one homogeneous calibration
            # period. Keep their observed populations separate from each other too.
            population=owner["id"] if unverified and owner else "qualified"
            key = f"{row['method_id']}:{row['material_id']}:{row['period_key']}:{origin}:{population}"
            if key not in groups:
                method = self.repo.get(db, "methods", row["method_id"])
                material = self.repo.get(db, "materials", row["material_id"])
                groups[key] = {"key": key, "method_id": row["method_id"], "method_version": method["version"],
                               "material": material, "configuration": row["configuration"], "period_key": row["period_key"],
                               "rows": [], "config": method["config"],"data_origin":origin,
                               "results_session_id":owner["id"] if unverified and owner else None,
                               "population_label":owner["project"] if unverified and owner else f"v{method['version']}",
                               "value_basis":"Original externally referenced exports; historical calibration unverified" if run.get("calibration_verification")=="simulation_assumption" else "Corrected and normalized results",
                               "use_original":run.get("calibration_verification")=="simulation_assumption"}
            groups[key]["rows"].append(row)
        output = []
        for group in groups.values():
            rows = sorted(group.pop("rows"), key=lambda r: (r["acquired_at"] or r["created_at"], r["sequence"]))
            config = group.pop("config")
            use_original=group.pop("use_original")
            group["isotopes"] = {}
            for iso in ISOTOPES:
                points = [{"id": r["id"], "value": r["raw"][iso] if use_original else r["isotopes"][iso]["value"], "run_id": r["run_id"],
                           "at": r["acquired_at"] or r["created_at"], "qc_passed": r["qc_passed"], "issues": r["issues"]}
                          for r in rows if not r["excluded"] and (r["raw"].get(iso) is not None if use_original else iso in r["isotopes"])]
                group["isotopes"][iso] = control_summary(points, group["material"]["assigned"][iso]["value"], config["precision"][iso])
            group["observations"] = rows
            group["run_count"] = len({r["run_id"] for r in rows})
            group["precision_eligible"] = period is not None
            output.append(group)
        return output

    def create_period(self, command):
        with self.repo.connect(write=True) as db:
            evaluations = [self.repo.get(db, "evaluations", id_) for id_ in dict.fromkeys(command.evaluation_ids)]
            if len({e["run_id"] for e in evaluations}) != len(evaluations) or len(evaluations) < 2:
                raise ValueError("Choose evaluations from at least two distinct runs, once per run")
            runs = [self.repo.get(db, "runs", e["run_id"]) for e in evaluations]
            if len({bool(r.get("synthetic")) for r in runs})>1:
                raise ValueError("Observed and synthetic QC cannot share a precision period")
            if any(e["method_id"] != command.method_id or not e["ready"] or not e["qc"]["passed"] for e in evaluations):
                raise ValueError("A precision period requires passing evaluations under one method")
            if any(r["context"] != "routine" or r["latest_evaluation_id"] != e["id"] or r["revision"] != e["run_revision"] for r, e in zip(runs, evaluations)):
                raise ValueError("Use current routine evaluations with unchanged inputs")
            if len({r["period_key"] for r in runs}) != 1 or len({r.get("acquired_date") for r in runs}) < 2 or any(not r.get("acquired_date") for r in runs):
                raise ValueError("Use multiple acquisition dates within one intervention period")
            method = self.repo.get(db, "methods", command.method_id)
            material = self.repo.get(db, "materials", method["config"]["qc_id"])
            stats = {}
            for iso in ISOTOPES:
                observations = sorted([r for e in evaluations for r in e["results"] if r["role"] == "qc" and not r["excluded"]], key=lambda r: (r["acquired_at"] or "", r["sequence"]))
                points = [{"id": r["id"], "value": r["isotopes"][iso]["value"]} for r in observations]
                control = control_summary(points, material["assigned"][iso]["value"], method["config"]["precision"][iso])
                if control["flags"]:
                    raise ValueError("QC control signals must be investigated before estimating a homogeneous precision period")
                stats[iso] = {k: control[k] for k in ("n", "mean", "sd", "se_mean")}
            record = self.repo.insert(db, "periods", {**command.model_dump(exclude={"method_id"}), "statistics": stats,
                                      "period_key": runs[0]["period_key"], "configuration": method["config"]["configuration"],
                                      "material_id": material["id"], "estimator": "SD of individual observations, not SE of mean"}, method_id=command.method_id)
            self.repo.audit(db, "qc_period_reviewed", record["id"], command.actor, command.reason, after=record)
            return record

    def run_detail(self, id_):
        with self.repo.connect() as db:
            run = self.repo.get(db, "runs", id_)
            run["source"] = self.repo.get(db, "raw_imports", run["raw_import_id"])
            rows = self.repo.list(db, "measurements", run_id=id_)
            run["measurements"] = [{k: v for k, v in r.items() if k != "raw_rows"} for r in sorted(rows, key=lambda r: r["sequence"])]
            run["exclusions"] = self.repo.list(db, "exclusions", run_id=id_)
            run["evaluation"] = self.repo.get(db, "evaluations", run["latest_evaluation_id"]) if run["latest_evaluation_id"] else None
            run["releases"] = self.repo.list(db, "releases", run_id=id_)
            run["release_blockers"] = self.release_gates(db, run, run["evaluation"]) if run["evaluation"] and run["context"] == "routine" else []
            if not run["evaluation"]:
                # Descriptive QC diagnostics remain available before certificates are supplied.
                draft = self.repo.list(db, "methods", status="draft")
                if draft:
                    config = MethodConfig.model_validate(draft[-1]["config"])
                    material_map = self.material_map(db, draft[-1])
                    projected = [{**r, "role": identify(r, config, material_map)[0], "material_id": identify(r, config, material_map)[1]} for r in rows]
                    run["diagnostics"] = diagnostics(projected)
            return run

    def state(self):
        with self.repo.connect() as db:
            methods = self.repo.list(db, "methods")
            active = next((m for m in reversed(methods) if m["status"] == "active"), None)
            quals = [self.qualification_detail(db, q["id"]) for q in self.repo.list(db, "qualifications")]
            interventions = self.repo.list(db, "interventions")
            runs = self.repo.list(db, "runs")
            history = self.qc_history(db)
            last = next((q for q in reversed(quals) if q["status"] == "approved" and q["trigger"] == "periodic" and active and q["method_id"] == active["id"]), None)
            due = (datetime.fromisoformat(last["approval"]["at"]) + timedelta(days=active["config"]["qualification_interval_days"])).isoformat() if last and active else None
            state = "review_required" if not active else "valid"
            if due and datetime.fromisoformat(due) <= self.repo.instant():
                state = "qualification_due"
            if active and any(e["status"] == "open" and e["instrument"] == active["config"]["instrument"] for e in interventions):
                state = "requalification_triggered"
            active_events = [e for e in interventions if active and e["instrument"] == active["config"]["instrument"]]
            current_period = active_events[-1]["id"] if active_events else "initial"
            active_history = [h for h in history if active and h["method_id"] == active["id"] and h["period_key"] == current_period and
                              (not self.repo.demo or h.get("data_origin")=="synthetic")]
            if any(s["status"] == "out_of_control" for h in active_history for s in h["isotopes"].values()):
                state = "out_of_control"
            operational_path=self.repo.root/"operational-manifest.json"
            import json
            operational=json.loads(operational_path.read_text(encoding="utf-8")) if operational_path.exists() else None
            return {"software_version": SOFTWARE_VERSION, "demo": self.repo.demo,"operational":operational, "status": state, "active_method": active, "next_review": due,
                    "last_qualification": last, "methods": methods, "materials": self.repo.list(db, "materials"),
                    "qualifications": quals, "runs": runs, "results_sessions": self.session_catalog(db), "interventions": interventions,
                    "periods": self.repo.list(db, "periods"), "history": history,
                    "audit": self.repo.audit_log(db), "sample_types": SAMPLE_TYPES, "test_catalog": TESTS,
                    "event_tests": EVENT_TESTS, "reports": self.repo.list(db, "reports")}
