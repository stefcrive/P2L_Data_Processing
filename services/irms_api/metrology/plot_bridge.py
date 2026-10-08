"""Read-only projections through the original IRMS workbook importer."""
from __future__ import annotations

import hashlib
import math

import pandas as pd

from .repository import encode
from .importer import measurement_identity


def open_plot_bridge(service, session_id, scope, command):
    from ..api import main as legacy
    from ..domain.constants import ISOTYPE_D13C, ISOTYPE_D18O
    detail = service.results_session_detail(session_id)
    effective = {r["id"]: r for run in detail["runs"] for r in (run.get("evaluation") or {}).get("results", [])}
    with service.repo.connect(write=True) as db:
        session = service.repo.get(db, "results_sessions", session_id)
        q = service.repo.get(db, "qualifications", session["qualification_id"]) if session["qualification_id"] else None
        qualification_run = service.repo.get(db, "evaluations", q["approval"]["evaluation_id"])["run_id"] if q and q.get("approval") else None
        run_ids = session["run_ids"] if scope == "all" else [scope]
        if not run_ids or any(r not in session["run_ids"] and r != qualification_run for r in run_ids):
            raise ValueError("Choose an imported session or its applied qualification")
        runs = [service.repo.get(db, "runs", rid) for rid in run_ids]
        method = service.repo.get(db, "methods", session["method_id"])
        fingerprint = hashlib.sha256(encode({"runs": [(r["id"], r["revision"], r.get("latest_evaluation_id")) for r in runs], "method": method,
                                            "session_population": [(r["id"], r["revision"], r.get("latest_evaluation_id")) for r in detail["runs"]],
                                            "residual_overrides": session.get("residual_overrides", {}), "outlier_screening": session.get("outlier_screening"),
                                            "correct_failed_analyses": bool(session.get("correct_failed_analyses", False)),
                                            "identity_revision": session.get("identity_revision", 0), "processing_version": detail.get("residual_processing_version")}).encode()).hexdigest()
        bridge = session.get("bridges", {}).get(scope)
        reuse_parsed = False
        if bridge and legacy.store.session_exists(bridge):
            link = legacy.store.load_metadata(bridge).get("metrology_link", {})
            if link.get("bridge_version") == 10 and link.get("fingerprint") == fingerprint:
                return {"session_id": bridge, "run_id": scope, "row_mapping": link.get("row_mapping", {})}
            # Upgrade an unchanged, frozen v7 consultation from its parsed cache.
            # Scientific revisions still create a fresh bridge below.
            old_fingerprint = hashlib.sha256(encode([(r["id"], r["revision"], r.get("latest_evaluation_id")) for r in runs]).encode()).hexdigest()
            reuse_parsed = (link.get("bridge_version") == 7 and link.get("fingerprint") == old_fingerprint
                            and link.get("method_id") == method["id"] and method["status"] != "draft")
        uploads, sources = [], {}
        for run in runs:
            raw = service.repo.get(db, "raw_imports", run["raw_import_id"])
            filename = f"{run['id'][:12]}__{raw['filename']}"
            if not reuse_parsed:
                uploads.append((filename, service.repo.read_blob(raw["sha256"])))
            sources[filename] = service.repo.list(db, "measurements", run_id=run["id"])
        if not reuse_parsed:
            result = legacy._import_session_from_bytes(uploads)
            bridge = result.session.session_id
        frame = legacy.store.load_frame(bridge)
        for column in ("Identifier 1", "Identifier 2", "Species"):
            frame[column] = frame[column].astype(object) if column in frame else ""
        frame["Metrology consultation"] = True
        mapping = {}
        def finite(value):
            try:
                value = float(value)
                return value if math.isfinite(value) else None
            except (ValueError, TypeError):
                return None
        for index, row in frame.iterrows():
            candidates = sources.get(str(row.get("Excel File", "")).strip(), [])
            row_number = finite(row.get("Row", row.get("Index")))
            matches = [r for r in candidates if finite(r.get("instrument_row", r["source_index"])) == row_number]
            # The legacy importer computes cycle averages. Identity must instead use
            # acquisition keys, so a recomputed mean never determines row identity.
            if len(matches) > 1:
                stamp = row.get("Start Time") or f"{row.get('Date', '')} {row.get('Time', '')}"
                parsed = pd.to_datetime(stamp, errors="coerce")
                matches = [r for r in matches if parsed == pd.to_datetime(r.get("acquired_at"), errors="coerce")]
            if len(matches) == 1 and matches[0]["id"] not in mapping:
                original = matches[0]
                mapping[original["id"]] = str(index)
                source_run = next(r for r in runs if r["id"] == original["run_id"])
                current = effective.get(original["id"], original)
                identity = measurement_identity(current, source_run.get("source_kind", "qtegra_raw"))
                for field, column in (("identifier1", "Identifier 1"), ("identifier2", "Identifier 2"), ("species", "Species")):
                    frame.loc[index, column] = identity[field]
                # Keep original cycle-derived summaries available for consultation,
                # and use the authoritative exported analysis values in result plots.
                for field, column in (("d13c", "d 13C/12C  Mean"), ("d18o", "d 18O/16O  Mean"),
                                      ("d13c_sd", "d 13C/12C  Std Dev"), ("d18o_sd", "d 18O/16O  Std Dev"),
                                      ):
                    frame.loc[index, "IRMS cycle summary: " + column] = row.get(column)
                    frame.loc[index, column] = current.get("isotopes", {}).get(field, {}).get("value") if field in ("d13c", "d18o") and current.get("isotopes") is not None else original.get(field)
        metadata = legacy.store.load_metadata(bridge)
        metadata["session_name"] = f"{session['client']} / {session['name']}"
        metadata["metrology_link"] = {"results_session_id": session_id, "run_id": scope, "run_ids": run_ids,
            "method_id": session["method_id"], "qualification_id": session["qualification_id"], "bridge_version": 10,
            "repository_root": str(service.repo.root), "demo": service.repo.demo,
            "fingerprint": fingerprint, "row_mapping": mapping}
        method = service.repo.get(db, "methods", session["method_id"])
        materials = service.material_map(db, method)
        identifiers = set(frame["Identifier 1"].dropna().astype(str)) if "Identifier 1" in frame else set()
        anchors = [materials[mid]["name"] for mid in method["config"]["anchor_ids"]]
        selected = [name for name in anchors if name in identifiers] if scope != "all" else []
        if not selected and method["config"]["qc_id"]:
            material = materials[method["config"]["qc_id"]]
            selected = list(dict.fromkeys(name for name in [material["name"], *material["aliases"]] if name in identifiers))
        if not reuse_parsed:
            metadata["calibration"] = {"config": {"selected_standards": selected, "linearity": {"apply": False}}, "selected_standards": selected}
        metadata["metrology_reference_values"] = [{"Standard": m["name"], "Isotopic_Value_Type": token,
            "Value": m["assigned"][iso]["value"], "Source": f"Session method v{method['version']}; {m['assigned'][iso]['scale']}; {m['lot']}"}
            for m in materials.values() for iso, token in (("d13c", ISOTYPE_D13C), ("d18o", ISOTYPE_D18O)) if m["assigned"][iso]["value"] is not None]
        if not reuse_parsed:
            legacy._set_processing_apply_calibration(metadata, False)
        metadata.setdefault("processing", {}).setdefault("config", {})["pressure_adjustment_as_outlier"] = bool(
            session.get("outlier_screening", {}).get("pressure_adjustment_as_outlier", False))
        legacy._persist_session_update(bridge, action="metrology_consultation_linked", metadata=metadata, df=frame)
        session.setdefault("bridges", {})[scope] = bridge
        service.repo.update(db, "results_sessions", session_id, session)
        service.repo.audit(db, "results_tools_opened", session_id, command.actor, command.reason,
                           after={"run_id": scope, "tools_session_id": bridge, "mapped_analyses": len(mapping)})
        return {"session_id": bridge, "run_id": scope, "row_mapping": mapping}
