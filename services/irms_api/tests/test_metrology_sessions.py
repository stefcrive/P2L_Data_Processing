from __future__ import annotations

import csv
import io
import json
import sqlite3
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from services.irms_api.tests import test_metrology as fixtures
from services.irms_api.metrology.models import ResultsSessionCommand, RunCommand, SessionGroupsCommand, SessionExportCommand, ReleaseCommand, AnnotationCommand, RowReviewCommand, EvaluateCommand
from services.irms_api.metrology.repository import Repository
from services.irms_api.metrology.service import Service, Conflict

D = fixtures.DECISION


class ResultsSessionTests(unittest.TestCase):
    def test_residual_overrides_are_audited_previews_and_can_be_restored(self):
        session = self.create()
        run, _ = self.import_batch(session)
        detail = self.service.results_session_detail(session["id"])
        original = detail["runs"][0]["evaluation"]
        material = self.method["config"]["qc_id"]
        endpoint = f"/metrology/results-sessions/{session['id']}/residual-overrides"
        command = {**D, "material_id": material, "effect": "drift", "isotope": "d13c",
                   "settings": {"enabled": True, "slope": .002, "center": 2}}
        response = self.client.put(endpoint, json=command)
        self.assertEqual(response.status_code, 200, response.text)
        analysis = self.client.get(f"/metrology/results-sessions/{session['id']}/analysis").json()
        key = f"{material}:drift:d13c"
        self.assertIn(key, analysis["residual_previews"])
        persisted = Service(Repository(self.repo.root)).results_session_detail(session["id"])
        self.assertEqual(persisted["residual_overrides"][key]["slope"], .002)
        self.assertEqual(persisted["runs"][0]["evaluation"], original)
        self.assertEqual(self.client.put(endpoint, json={**command,"material_id":"foreign"}).status_code, 422)
        self.assertEqual(self.client.put(endpoint, json={**command,"settings":None}).status_code, 200)
        self.assertNotIn(key, self.service.results_session_detail(session["id"])["residual_overrides"])

    def test_saved_qc_outliers_persist_and_history_is_isotope_specific(self):
        session = self.create()
        rows = [fixtures.row(i+1, "SHP2L", v, sample_type="QC Standard")
                for i, v in enumerate([-.002, -.001, 0, .001, .002, .003, .004, .07])]
        for row in rows:
            row["d18O Mean"] = 0
        run = self.service.import_run("outliers.xlsx", fixtures.workbook(rows), RunCommand(**D, results_session_id=session["id"]))
        original = self.service.run_detail(run["id"])["evaluation"]
        endpoint = f"/metrology/results-sessions/{session['id']}"
        # A session IQR outlier can be inside the frozen +/-3 SD limits.
        response = self.client.put(endpoint+"/outlier-screening", json={**D, "method":"iqr", "threshold":1.5})
        self.assertEqual(response.status_code, 200, response.text)
        screening = response.json()
        self.assertEqual(len(screening["flags"]), 1)
        flag = screening["flags"][0]
        self.assertEqual(flag["isotope"], "d13c")
        self.assertEqual(flag["evaluation_id"], original["id"])
        self.assertLess(flag["value"], 3*self.method["config"]["precision"]["d13c"])
        restarted = Service(Repository(self.repo.root))
        analysis = restarted.results_session_analysis(session["id"])
        self.assertEqual(analysis["outliers"]["id"], screening["id"])
        self.assertEqual(analysis["correction_review"]["d13c"]["paired_n"], 7)
        self.assertEqual(analysis["correction_review"]["d18o"]["paired_n"], 8)
        self.assertEqual(restarted.results_session_analysis(session["id"])["outliers"]["id"], screening["id"])
        detail = restarted.results_session_detail(session["id"])
        carbon, oxygen = (detail["history"][0]["isotopes"][iso] for iso in ("d13c", "d18o"))
        self.assertEqual((carbon["total_n"], carbon["n"], oxygen["n"]), (8, 7, 8))
        self.assertEqual(len(carbon["points"]), 8)
        self.assertIn("session_qc_outlier", [f["rule"] for f in carbon["flags"]])
        self.assertAlmostEqual(carbon["sd"], .002160246899469287)
        self.assertEqual(detail["runs"][0]["evaluation"], original)
        with self.repo.connect(write=True) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "Append-only"):
                db.execute("DELETE FROM qc_screenings WHERE id=?", (screening["id"],))
        # New settings replace the active detection without destroying its provenance.
        sigma = self.client.put(endpoint+"/outlier-screening", json={**D, "method":"sigma", "threshold":3}).json()
        self.assertNotEqual(sigma["id"], screening["id"])
        self.assertEqual(sigma["flags"], [])
        self.assertEqual(self.service.results_session_detail(session["id"])["history"][0]["isotopes"]["d13c"]["n"], 8)
        # A new immutable evaluation gets a different cache population.
        self.service.evaluate_run(run["id"], EvaluateCommand(**D))
        next_analysis = self.service.results_session_analysis(session["id"])
        self.assertNotEqual(next_analysis["outliers"]["id"], sigma["id"])
        for invalid in [{"method":"bad", "threshold":3}, {"method":"sigma", "threshold":0}]:
            self.assertEqual(self.client.put(endpoint+"/outlier-screening", json={**D, **invalid}).status_code, 422)

    def setUp(self):
        self.fixture = fixtures.WorkflowTests("test_seed_has_no_provisional_anchor_certificates")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service = self.fixture.service
        self.repo = self.fixture.repo
        self.client = self.fixture.client
        self.method = self.fixture.activate()

    def create(self, client="Client A"):
        return self.service.save_results_session(ResultsSessionCommand(**D, name="Carbonate batch", client=client,
            project="Reservoir", method_id=self.method["id"], processing_evidence="Qtegra uses this exact validated dual-anchor model; no residual correction configured"))

    def import_batch(self, session):
        rows = []
        for i, offset in enumerate([-.01, 0, .01]):
            rows.append(fixtures.row(len(rows)+1,"SHP2L",offset,sample_type="QC Standard"))
            rows.append(fixtures.row(len(rows)+1,"+sample-formula" if i==0 else f"sample-{i}",1+i))
        content=fixtures.workbook(rows)
        run=self.service.import_run("routine.xlsx",content,RunCommand(**D, results_session_id=session["id"], sample_group="Core A"))
        return run,content

    def test_persisted_session_pins_provenance_without_double_normalization(self):
        session=self.create()
        run,content=self.import_batch(session)
        self.assertEqual(run["input_basis"],"already_vpdb")
        self.assertEqual(run["external_method_id"],self.method["id"])
        detail=Service(Repository(self.repo.root)).results_session_detail(session["id"])
        self.assertEqual(detail["client"],"Client A")
        self.assertEqual(detail["qualification_id"],self.method["approval"]["qualification_id"])
        self.assertIsNotNone(detail["qualification_run_id"])
        self.assertEqual(detail["run_ids"],[run["id"]])
        self.assertEqual(detail["history"][0]["isotopes"]["d13c"]["n"],3)
        for row in detail["runs"][0]["evaluation"]["results"]:
            value=row["isotopes"]["d13c"]
            self.assertFalse(value["processing"]["normalization_applied"])
            self.assertAlmostEqual(value["value"],row["d13c"])
            self.assertGreater(value["u_norm"],0)
        again=self.service.import_run("renamed.xlsx",content,RunCommand(**D,results_session_id=session["id"]))
        self.assertTrue(again["duplicate"])
        self.assertEqual(self.service.results_session_detail(session["id"])["run_ids"],[run["id"]])
        with self.assertRaisesRegex(ValueError,"fixed"):
            self.service.save_results_session(ResultsSessionCommand(**D,name="Changed",client="Other client",method_id=self.method["id"],processing_evidence="Another model"),session["id"])
        with self.assertRaisesRegex(Conflict,"pinned"):
            self.service.annotate(run["id"],AnnotationCommand(**D,input_basis="instrument_delta"))

    def test_aragonite_import_records_basis_and_scales_full_budget(self):
        session=self.create()
        content=fixtures.workbook([fixtures.row(1,"SHP2L",0,sample_type="QC Standard"),fixtures.row(2,"Aragonite sample",1)])
        run=self.service.import_run("aragonite.xlsx",content,RunCommand(**D,results_session_id=session["id"],carbonate_material="aragonite"))
        detail=self.service.run_detail(run["id"])
        self.assertEqual(detail["carbonate_material"],"aragonite")
        qc,sample=detail["evaluation"]["results"]
        factor=1.0087/1.0091
        result=sample["isotopes"]["d18o"]
        self.assertAlmostEqual(result["value"],(sample["d18o"]+1000)*factor-1000)
        self.assertEqual(qc["isotopes"]["d18o"]["value"],qc["d18o"])
        self.assertAlmostEqual(result["u_prec"],self.method["config"]["precision"]["d18o"]*factor)
        self.assertTrue(result["processing"]["carbonate"]["applied"])
        self.assertGreater(result["budget"]["expanded_uncertainty"],0)
        with self.assertRaisesRegex(ValueError,"different carbonate basis"):
            self.service.import_run("again.xlsx",content,RunCommand(**D,results_session_id=session["id"]))
        response=self.client.get(f"/metrology/runs/{run['id']}/measurements/{sample['id']}/evidence")
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["raw_rows"][0]["values"]["Label"],"Aragonite sample")

    def test_client_isolation_and_foreign_groups(self):
        a=self.create(); b=self.create("Client B")
        run,content=self.import_batch(a)
        with self.assertRaisesRegex(ValueError,"Client A"):
            self.service.import_run("another-name.xlsx",content,RunCommand(**D,results_session_id=b["id"]))
        self.assertEqual(self.service.results_session_detail(b["id"])["run_ids"],[])
        with self.assertRaisesRegex(ValueError,"belonging"):
            self.service.save_session_groups(b["id"],SessionGroupsCommand(**D,groups={self.service.run_detail(run["id"])["measurements"][0]["id"]:"Stolen group"}))

    def test_group_export_has_uncertainties_and_immutable_lineage(self):
        session=self.create(); run,_=self.import_batch(session)
        detail=self.service.run_detail(run["id"])
        unknowns=[r for r in detail["evaluation"]["results"] if r["role"]=="unknown"]
        self.service.save_session_groups(session["id"],SessionGroupsCommand(**D,groups={unknowns[0]["id"]:"Client subset"}))
        record=self.service.export_results_session(session["id"],SessionExportCommand(**D,group="Client subset"))
        with zipfile.ZipFile(io.BytesIO(self.repo.read_blob(record["sha256"]))) as archive:
            rows=list(csv.DictReader(io.StringIO(archive.read("results.csv").decode("utf-8-sig"))))
            dossier=json.loads(archive.read("calculation-dossier.json"))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["sample"],"'+sample-formula")
        self.assertEqual(rows[0]["raw_sha256"],detail["source"]["sha256"])
        self.assertEqual(rows[0]["qualification_id"],session["qualification_id"])
        self.assertGreater(float(rows[0]["d13c_u_norm"]),0)
        self.assertAlmostEqual(float(rows[0]["d13c_U"]),2*float(rows[0]["d13c_u_combined"]))
        self.assertEqual(sum(len(c["results"]) for c in dossier["calculations"]),1)
        with self.repo.connect(write=True) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE session_exports SET data='{}' WHERE id=?",(record["id"],))
        response=self.client.get(f"/metrology/session-exports/{record['id']}")
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.content,self.repo.read_blob(record["sha256"]))

    def test_release_freezes_client_and_sample_groups(self):
        session=self.create();run,_=self.import_batch(session)
        release=self.service.release(run["id"],ReleaseCommand(**D,evaluation_id=run["latest_evaluation_id"]))
        self.assertEqual(release["results_session_snapshot"]["client"],"Client A")
        sample=release["results"][0]
        with self.assertRaisesRegex(ValueError,"frozen"):
            self.service.save_session_groups(session["id"],SessionGroupsCommand(**D,groups={sample["id"]:"Other group"}))

    def test_existing_plotting_tools_receive_the_correct_workbook(self):
        from services.irms_api.api import main as legacy
        from services.irms_api.session_store import FileSessionStore
        session=self.create();run,_=self.import_batch(session)
        with patch.object(legacy,"store",FileSessionStore(Path(self.fixture.temp.name)/"plot-tools")):
            url=f"/metrology/results-sessions/{session['id']}/tools/{run['id']}"
            response=self.client.post(url,json=D)
            self.assertEqual(response.status_code,200,response.text)
            bridge=response.json()["session_id"]
            metadata=legacy.store.load_metadata(bridge)
            self.assertEqual(metadata["metrology_link"]["run_id"],run["id"])
            self.assertEqual(self.client.post(url,json=D).json()["session_id"],bridge)
            qualification_run=self.service.results_session_detail(session["id"])["qualification_run_id"]
            calibration=self.client.post(f"/metrology/results-sessions/{session['id']}/tools/{qualification_run}",json=D)
            self.assertEqual(calibration.status_code,200,calibration.text)
            calibration_id=calibration.json()["session_id"]
            calibration_meta=legacy.store.load_metadata(calibration_id)
            self.assertEqual(calibration_meta["calibration"]["selected_standards"],["NBS18","NBS19"])
            from services.irms_api.domain.calibration.workspace import build_calibration_workspace
            workspace=build_calibration_workspace(calibration_id,legacy.store.load_frame(calibration_id),calibration_meta,include_figures=False)
            self.assertEqual(next(v.value for v in workspace.selected_standard_official_values if v.standard=="NBS18"),-10)
            from fastapi.testclient import TestClient
            with TestClient(legacy.app) as app:
                guarded=app.post(f"/sessions/{bridge}/calibration/run",json={})
                self.assertEqual(guarded.status_code,409,guarded.text)

    def test_full_session_bridge_maps_all_workbooks_and_invalidates_after_revision(self):
        from services.irms_api.api import main as legacy
        from services.irms_api.session_store import FileSessionStore
        from services.irms_api.metrology.plot_bridge import open_plot_bridge
        from services.irms_api.metrology.models import Decision
        session=self.create(); run,_=self.import_batch(session)
        second=self.service.import_run("routine.xlsx",fixtures.workbook([fixtures.row(1,"SHP2L",.002,sample_type="QC Standard"),fixtures.row(2,"another sample",2)]),RunCommand(**D,results_session_id=session["id"]))
        with patch.object(legacy,"store",FileSessionStore(Path(self.fixture.temp.name)/"session-tools")):
            result=open_plot_bridge(self.service,session["id"],"all",Decision(**D))
            self.assertEqual(len(result["row_mapping"]),8)
            self.assertEqual(len(set(result["row_mapping"].values())),8)
            frame=legacy.store.load_frame(result["session_id"])
            for source in self.service.results_session_detail(session["id"])["runs"]:
                for row in source["measurements"]:
                    self.assertAlmostEqual(frame.loc[int(result["row_mapping"][row["id"]]),"d 13C/12C  Mean"],row["d13c"])
            self.assertEqual(open_plot_bridge(self.service,session["id"],"all",Decision(**D))["session_id"],result["session_id"])
            # A format upgrade reuses already parsed workbooks and saved plot controls.
            import hashlib
            from services.irms_api.metrology.repository import encode
            meta=legacy.store.load_metadata(result["session_id"])
            runs=self.service.results_session_detail(session["id"])["runs"]
            meta["metrology_link"].update(bridge_version=7,fingerprint=hashlib.sha256(encode([(r["id"],r["revision"],r.get("latest_evaluation_id")) for r in runs]).encode()).hexdigest())
            meta["calibration"]["config"]["color_param"]="Date"
            legacy.store.write_metadata(result["session_id"],meta)
            with patch.object(legacy,"_import_session_from_bytes",side_effect=AssertionError("Unchanged workbooks must not be reparsed")):
                upgraded=open_plot_bridge(self.service,session["id"],"all",Decision(**D))
            self.assertEqual(upgraded["session_id"],result["session_id"])
            self.assertEqual(legacy.store.load_metadata(result["session_id"])["calibration"]["config"]["color_param"],"Date")
            self.service.annotate(second["id"],AnnotationCommand(**D,acquisition_complete=True))
            changed=open_plot_bridge(self.service,session["id"],"all",Decision(**D))
            self.assertNotEqual(changed["session_id"],result["session_id"])

    def test_session_analysis_reuses_qc_outlier_algorithm_and_pressure_fit(self):
        from services.irms_api.metrology.session_analysis import session_analysis
        from services.irms_api.domain.calibration.core import identify_outliers_iqr
        import pandas as pd
        session=self.create(); self.import_batch(session)
        self.service.import_run("additional.xlsx",fixtures.workbook([fixtures.row(i+1,"SHP2L",v,sample_type="QC Standard") for i,v in enumerate([-.01,0,.01,.5])]),RunCommand(**D,results_session_id=session["id"]))
        detail=self.service.results_session_detail(session["id"])
        data=session_analysis(detail,outlier_method="iqr",threshold=1.5)
        self.assertEqual(data["workbooks"],2); self.assertEqual(len(data["rows"]),10)
        qc=[r for r in data["rows"] if r["role"]=="qc"]
        expected=identify_outliers_iqr(pd.DataFrame({"value":[r["isotopes"]["d13c"]["value"] for r in qc]}),"value",1.5)
        self.assertEqual({r["measurement_id"] for r in data["outliers"]["flags"] if r["isotope"]=="d13c"},{r["id"] for r,flag in zip(qc,expected) if flag})
        self.assertEqual(data["correction_review"]["d13c"]["paired_n"],6)
        self.assertEqual(data["correction_review"]["d13c"]["excluded_outlier_n"],1)
        self.assertEqual(data["correction_review"]["d18o"]["paired_n"],6)
        self.assertEqual(data["qc_statistics"]["d13c"]["final"]["n"],6)
        self.assertEqual(data["qc_statistics"]["d18o"]["final"]["n"],6)
        self.assertIn("pressure_dependence",data["diagnostics_before"]["materials"][0]["isotopes"]["d13c"])
        self.assertFalse(any(r["excluded"] for r in data["rows"]))

    def test_sample_override_preserves_original_and_does_not_clear_other_gates(self):
        session=self.create()
        rows=[fixtures.row(i+1,"SHP2L",v,sample_type="QC Standard") for i,v in enumerate([-.01,0,.01])]
        rows.append(fixtures.row(4,"Outside anchors",20))
        run=self.service.import_run("exceptions.xlsx",fixtures.workbook(rows),RunCommand(**D,results_session_id=session["id"]))
        original=self.service.run_detail(run["id"])["evaluation"]
        row=next(r for r in original["results"] if r["role"]=="unknown")
        issues=[i for i in row["issues"] if "extrapolates" in i or "outside validated isotope" in i]
        self.assertTrue(issues)
        reviewed=self.service.review_row(run["id"],RowReviewCommand(**D,evaluation_id=original["id"],measurement_id=row["id"],issues=issues))
        self.assertEqual(reviewed["results"][-1]["accepted_issues"],issues)
        self.assertEqual(reviewed["results"][-1]["isotopes"],row["isotopes"])
        self.assertEqual(self.service.results_session_detail(session["id"])["history"][0]["isotopes"]["d13c"]["n"],3)
        with self.repo.connect() as db:
            self.assertEqual(self.repo.get(db,"evaluations",original["id"])["results"][-1]["issues"],row["issues"])
        with self.assertRaisesRegex(Conflict,"latest"):
            self.service.review_row(run["id"],RowReviewCommand(**D,evaluation_id=original["id"],measurement_id=row["id"],issues=issues))
        reevaluated=self.service.evaluate_run(run["id"],EvaluateCommand(**D))
        self.assertEqual(reevaluated["results"][-1]["issues"],row["issues"])
        self.assertNotIn("accepted_issues",reevaluated["results"][-1])

    def test_override_does_not_accept_qc_or_missing_uncertainty(self):
        session=self.create();run,_=self.import_batch(session)
        evaluation=self.service.run_detail(run["id"])["evaluation"]
        qc=next(r for r in evaluation["results"] if r["role"]=="qc")
        with self.assertRaisesRegex(ValueError,"unknown samples"):
            self.service.review_row(run["id"],RowReviewCommand(**D,evaluation_id=evaluation["id"],measurement_id=qc["id"],issues=["force keep"]))
        rows=[fixtures.row(i+1,"SHP2L",v,sample_type="QC Standard") for i,v in enumerate([-.01,0,.01])]
        rows.append(fixtures.row(4,"Missing isotope",1));rows[-1]["d18O Mean"]=None
        broken=self.service.import_run("missing.xlsx",fixtures.workbook(rows),RunCommand(**D,results_session_id=session["id"]))
        evaluation=self.service.run_detail(broken["id"])["evaluation"]
        unknown=next(r for r in evaluation["results"] if r["role"]=="unknown")
        with self.assertRaisesRegex(ValueError,"complete calculated"):
            self.service.review_row(broken["id"],RowReviewCommand(**D,evaluation_id=evaluation["id"],measurement_id=unknown["id"],issues=unknown["issues"]))

    def test_empty_session_analysis_and_legacy_mapping_preserve_exported_means(self):
        from services.irms_api.metrology.session_analysis import session_analysis
        session=self.create()
        empty=session_analysis(self.service.results_session_detail(session["id"]))
        self.assertEqual(empty["rows"],[])
        self.assertEqual(empty["diagnostics_before"]["materials"],[])
        self.assertEqual(empty["qc_statistics"]["d13c"]["final"]["n"],0)

    def test_pdf_dossier_and_legacy_filenames_keep_review_status_and_group_scope(self):
        import pymupdf
        from openpyxl import load_workbook
        session=self.create();run,_=self.import_batch(session)
        record=self.service.export_results_session(session["id"],SessionExportCommand(**D,client_name="Client / A",series_name="17 # 2"))
        self.assertTrue(record["filename"].startswith("Results for 17 series - stable C & O isotopes - P2L - Client _ A"))
        with zipfile.ZipFile(io.BytesIO(self.repo.read_blob(record["sha256"]))) as archive:
            document=pymupdf.open(stream=archive.read("calculation-certificate.pdf"),filetype="pdf")
            content="\n".join(p.get_text() for p in document)
            self.assertIn("Result status: review",content)
            self.assertIn("u_norm",content)
            self.assertIn(self.service.run_detail(run["id"])["source"]["sha256"],content.replace("\n",""))
            xlsx=load_workbook(io.BytesIO(archive.read(next(n for n in archive.namelist() if n.endswith(".xlsx")))))
            self.assertEqual(xlsx.active.max_row,4)
            self.assertEqual(xlsx.active.freeze_panes,"A2")


if __name__=="__main__":
    unittest.main()
