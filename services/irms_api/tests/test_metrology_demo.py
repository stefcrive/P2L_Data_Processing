from __future__ import annotations

import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.irms_api.metrology.api import get_service, router
from services.irms_api.metrology.demo import REVIEW, seed_demo
from services.irms_api.metrology.models import ApproveCommand, ReleaseCommand
from services.irms_api.metrology.pipeline import evaluate, process_value
from services.irms_api.metrology.repository import Repository
from services.irms_api.metrology.science import anchor_model
from services.irms_api.metrology.service import Conflict, Service


class ProcessingProvenanceTests(unittest.TestCase):
    def test_external_and_preapplied_stages_are_not_repeated(self):
        model = anchor_model([-10.,10.],[-4.,6.],[.02,.03,.01,.02])
        model["correction"] = dict(name="Intensity",predictor="i44_v",slope=.02,u_slope=.001,center=6.,
            domain=dict(low=3.,high=9.),anchor_predictor_means=[5.,7.])
        row = dict(d13c=2.3,i44_v=8.)
        baseline = process_value(row,"d13c",model,{})
        external = process_value({**row,"d13c":baseline["value"]},"d13c",model,
            dict(input_basis="already_vpdb",preapplied_corrections=["d13c"],processing_evidence="Matching frozen model"),monte_carlo=True)
        preapplied = process_value({**row,"d13c":2.3-.02*2},"d13c",model,dict(preapplied_corrections=["d13c"]))
        for result in (external,preapplied):
            self.assertAlmostEqual(result["value"],baseline["value"],places=12)
            self.assertAlmostEqual(result["u_norm"],baseline["u_norm"],places=12)
            self.assertAlmostEqual(result["correction"]["u"],baseline["correction"]["u"],places=12)
        self.assertFalse(external["processing"]["normalization_applied"])
        self.assertFalse(external["processing"]["correction_applied"])
        self.assertAlmostEqual(external["monte_carlo"]["u"],external["u_normalization_and_correction"],delta=.001)

    def test_external_normalization_with_remaining_correction(self):
        model = anchor_model([-10.,10.],[-4.,6.],[.02,.03,.01,.02])
        model["correction"] = dict(name="Intensity",predictor="i44_v",slope=.02,u_slope=.001,center=6.,
            domain=dict(low=3.,high=9.),anchor_predictor_means=[5.,7.])
        expected = process_value(dict(d13c=2.3,i44_v=8.),"d13c",model,{})
        external_value = model["intercept"] + model["slope"]*2.3
        actual = process_value(dict(d13c=external_value,i44_v=8.),"d13c",model,dict(input_basis="already_vpdb"))
        self.assertAlmostEqual(actual["value"],expected["value"])
        self.assertTrue(actual["processing"]["correction_applied"])
        self.assertFalse(actual["processing"]["normalization_applied"])

    def test_demo_modes_and_clocks_are_isolated(self):
        with self.assertRaisesRegex(ValueError,"separate workspace"):
            Repository(".data/metrology",demo=True)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"lab"
            lab=Repository(root)
            with self.assertRaisesRegex(ValueError,"does not match"):
                Repository(root,demo=True)
            with self.assertRaisesRegex(ValueError,"only in a demo"):
                Repository(Path(tmp)/"clock",clock=lambda:datetime.now(timezone.utc))
            self.assertFalse(Service(lab).state()["demo"])


class DemoWorkflowTests(unittest.TestCase):
    def test_populated_demo_and_review_actions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/"demo"
            manifest=seed_demo(root)
            service=Service(Repository(root,demo=True,clock=lambda:datetime(2026,10,5,20,tzinfo=timezone.utc)))
            state=service.state()
            self.assertEqual(state["status"],"valid")
            self.assertEqual(len(state["runs"]),44)
            self.assertEqual(len(state["reports"]),3)
            routine_history = [h for h in state["history"] if h["context"] == "routine"]
            self.assertTrue(any(h["context"] == "qualification" for h in state["history"]))
            self.assertEqual(sum(h["isotopes"]["d13c"]["total_n"] for h in routine_history),234)
            self.assertEqual(sum(h["isotopes"]["d13c"]["n"] + len(h["isotopes"]["d13c"]["outlier_ids"]) for h in routine_history),234)
            self.assertEqual(state["active_method"]["version"],4)
            self.assertEqual(len([q for q in state["qualifications"] if q["status"]=="approved"]),4)
            before_audit=state["audit"]
            self.assertEqual(seed_demo(root),manifest)
            self.assertEqual(service.state()["audit"],before_audit)
            for h in routine_history:
                self.assertTrue(all(next(r for r in state["runs"] if r["id"]==p["run_id"])["context"]=="routine" for p in h["isotopes"]["d13c"]["points"]))
            # Routine aliquots should vary independently rather than alternate
            # signs or reuse the same signed error for both isotopes.
            first = next(h for h in state["history"] if h["method_version"] == 1)
            deviations = {
                iso: [p["value"] - first["isotopes"][iso]["target"]
                      for p in first["isotopes"][iso]["points"]]
                for iso in ("d13c", "d18o")
            }
            for values in deviations.values():
                self.assertTrue(any(a * b > 0 for a, b in zip(values, values[1:])))
            self.assertGreater(sum(c * o < 0 for c, o in zip(deviations["d13c"], deviations["d18o"])), 10)
            failure = service.run_detail(manifest["highlights"]["historical_failure_run"])
            self.assertFalse(failure["evaluation"]["qc"]["passed"])
            current=state["active_method"]
            period=next(p for p in state["periods"] if p["id"]==current["config"]["precision_period"])
            self.assertEqual(current["config"]["precision"]["d13c"],period["statistics"]["d13c"]["sd"])
            hi=manifest["highlights"]
            blocked=service.run_detail(hi["blocked_run"])
            self.assertTrue(blocked["evaluation"]["qc"]["passed"])
            self.assertFalse(blocked["evaluation"]["ready"])
            with self.assertRaises(Conflict):
                service.release(blocked["id"],ReleaseCommand(**REVIEW,evaluation_id=blocked["latest_evaluation_id"]))
            ready=service.run_detail(hi["ready_run"])
            self.assertEqual(ready["release_blockers"],[])
            row=next(r for r in ready["evaluation"]["results"] if r["role"]=="unknown")
            for iso in ("d13c","d18o"):
                result=row["isotopes"][iso]
                self.assertAlmostEqual(result["budget"]["u_combined"],math.sqrt(sum(c["u"]**2 for c in result["budget"]["components"])))
                self.assertAlmostEqual(result["budget"]["expanded_uncertainty"],2*result["budget"]["u_combined"])
            # Invalid or absent Qtegra provenance must block an otherwise valid run.
            with service.repo.connect() as db:
                material_map=service.material_map(db,current)
                measurements=service.repo.list(db,"measurements",run_id=ready["id"])
            bad=evaluate({**ready,"input_basis":"already_vpdb","external_method_id":"wrong","processing_evidence":""},current,material_map,measurements,[],demo=True)
            self.assertTrue(any("frozen method" in b for b in bad["blockers"]))
            self.assertTrue(any("provenance" in b for b in bad["blockers"]))
            lab_evaluation=evaluate(ready,current,material_map,measurements,[],demo=False)
            self.assertTrue(any("Synthetic" in b for b in lab_evaluation["blockers"]))
            app=FastAPI(); app.include_router(router); app.dependency_overrides[get_service]=lambda:service
            with TestClient(app) as client:
                self.assertEqual(client.get("/metrology/demo").status_code,200)
                name=manifest["files"][0]["filename"]
                self.assertEqual(client.get(f"/metrology/demo/files/{name}").content,(root/"examples"/name).read_bytes())
                self.assertEqual(client.get("/metrology/demo/files/nope.xlsx").status_code,404)
                response=client.post(f"/metrology/runs/{ready['id']}/release",json={**REVIEW,"evaluation_id":ready["latest_evaluation_id"]})
                self.assertEqual(response.status_code,200,response.text)
                self.assertTrue(response.json()["simulation"])
            qrun=service.run_detail(hi["review_run"])
            approved=service.approve(qrun["method_id"],ApproveCommand(**REVIEW,qualification_id=qrun["qualification_id"],evaluation_id=qrun["latest_evaluation_id"]))
            self.assertEqual(approved["status"],"active")
            self.assertEqual(approved["version"],5)


if __name__ == "__main__":
    unittest.main()
