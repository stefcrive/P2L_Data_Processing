import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from services.irms_api.metrology.models import AssignedValue, MethodConfig, PeriodCommand, RunCommand, ResultsSessionCommand
from services.irms_api.metrology.pipeline import identify
from services.irms_api.metrology.reference_catalog import catalog
from services.irms_api.metrology.repository import Repository
from services.irms_api.metrology.science import control_summary
from services.irms_api.tests import test_metrology as fixtures
from services.irms_api.tests import test_metrology_sessions as session_fixtures


class ReferenceLibraryTests(unittest.TestCase):
    def test_catalog_documents_idempotence_and_uncertainty_scope(self):
        with tempfile.TemporaryDirectory() as root:
            repo = Repository(root)
            with repo.connect() as db:
                before = repo.list(db, "materials")
                materials = {m["catalog_code"]: m for m in before if m.get("catalog_code")}
                self.assertEqual(len(materials), 11)
                for material in materials.values():
                    self.assertFalse(material["verified"])
                    for doc in material["documents"]:
                        data = repo.read_blob(doc["sha256"])
                        self.assertTrue(data.startswith(b"%PDF"))
                        self.assertEqual(hashlib.sha256(data).hexdigest(), doc["sha256"])
            again = Repository(root)
            with again.connect() as db:
                self.assertEqual(before, again.list(db, "materials"))
            self.assertEqual(materials["IAEA-603"]["issue_date"], "2026-06-30")
            self.assertEqual(AssignedValue.model_validate(materials["IAEA-610"]["assigned"]["d13c"]).standard_uncertainty(), .03)
            with self.assertRaises(ValueError):
                AssignedValue.model_validate(materials["IAEA-610"]["assigned"]["d18o"]).standard_uncertainty()
            with self.assertRaises(ValueError):
                AssignedValue.model_validate(materials["USGS44"]["assigned"]["d13c"]).standard_uncertainty()

    def test_identifier_matches_library_without_changing_method_anchor_role(self):
        record = next(m for m in catalog() if m.name == "IAEA-603").model_dump()
        materials = {"reference": record}
        row = {"source_role": "unknown", "label": "Vial 17", "identifier1": "IAEA603"}
        self.assertEqual(identify(row, MethodConfig(), materials), ("reference_standard", "reference"))
        self.assertEqual(identify(row, MethodConfig(anchor_ids=["reference"]), materials), ("anchor", "reference"))
        self.assertEqual(identify({**row, "identifier1": "", "reference": "IAEA603"}, MethodConfig(), materials), ("unknown", None))

    def test_control_limit_violation_is_not_an_extra_session_outlier(self):
        points = [{"id": "retained", "value": 9.}, {"id": "excluded", "value": 1., "session_outlier": True}]
        result = control_summary(points, 0., .1, exclude_outliers=True, session_outliers_only=True)
        self.assertEqual(result["outlier_ids"], ["excluded"])
        self.assertEqual(result["mean"], 9.)
        self.assertEqual(result["status"], "out_of_control")


class CorrectedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = session_fixtures.ResultsSessionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service = self.fixture.service
        self.repo = self.fixture.repo

    def test_history_values_equal_final_session_results_and_pdf_endpoint(self):
        session = self.fixture.create()
        rows = []
        for i in range(12):
            row = fixtures.row(i+1, "SHP2L", .02*i + (.001 if i%2 else -.001), sample_type="QC Standard")
            row.update(i44_v=5.+i*.05, reference_i44_v=6.)
            rows.append(row)
        run = self.service.import_run("residual-qc.xlsx", fixtures.workbook(rows), RunCommand(**fixtures.DECISION, results_session_id=session["id"]))
        detail = self.service.results_session_detail(session["id"])
        final = {r["id"]: r for r in detail["runs"][0]["evaluation"]["results"]}
        with self.repo.connect() as db:
            history = self.service.qc_history(db)
            material = next(m for m in self.repo.list(db, "materials") if m.get("catalog_code") == "IAEA-603")
        for iso in ("d13c", "d18o"):
            values = {o["id"]: o["measurement_id"] for h in history for o in h["observations"]}
            points = [p for h in history for p in h["isotopes"][iso]["points"] if p["run_id"] == run["id"]]
            self.assertEqual(len(points), 12)
            self.assertTrue(any(r["isotopes"][iso].get("residual_correction") for r in final.values()))
            for point in points:
                self.assertAlmostEqual(point["value"], final[values[point["id"]]]["isotopes"][iso]["value"])
                self.assertEqual(point["session_id"], session["id"])
        doc = material["documents"][0]
        response = self.fixture.client.get(f"/metrology/materials/{material['id']}/documents/{doc['sha256']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), doc["sha256"])

    def test_period_uses_complete_sessions_and_freezes_corrected_observations(self):
        with self.repo.connect(write=True) as db:
            original = self.fixture.method
            second = self.repo.insert(db, "methods", {k: v for k, v in original.items() if k not in ("id", "created_at", "status")} | {"version": 2}, status="active")
        sessions = [self.fixture.create("A"), self.service.save_results_session(ResultsSessionCommand(
            **fixtures.DECISION, name="Second version", client="B", method_id=second["id"],
            processing_evidence="Uses the compatible frozen calibration and acquisition configuration"))]
        for i, session in enumerate(sessions):
            rows = [fixtures.row(j+1, "SHP2L", v, sample_type="QC Standard", date=datetime.now(timezone.utc)+timedelta(days=i+1)) for j, v in enumerate([-.01, .01, 0.])]
            self.service.import_run(f"day-{i}.xlsx", fixtures.workbook(rows), RunCommand(**fixtures.DECISION, results_session_id=session["id"]))
        result = self.service.create_period(PeriodCommand(**fixtures.DECISION, name="Session period", method_ids=[self.fixture.method["id"], second["id"]], session_ids=[s["id"] for s in sessions]))
        self.assertEqual(len(result["evaluation_ids"]), 2)
        self.assertEqual(result["statistics"]["d13c"]["n"], 6)
        self.assertEqual(len(result["corrected_qc_snapshot"]["d13c"]["points"]), 6)
        self.assertEqual(set(result["method_ids"]), {original["id"], second["id"]})
        with self.repo.connect() as db:
            frozen = self.service.qc_history(db, period_id=result["id"])
        self.assertEqual(frozen[0]["isotopes"]["d13c"]["sd"], result["statistics"]["d13c"]["sd"])
