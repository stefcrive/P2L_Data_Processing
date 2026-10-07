from __future__ import annotations

import hashlib
import io
import json
import math
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.irms_api.metrology.api import get_service, router
from services.irms_api.metrology.importer import parse_workbook
from services.irms_api.metrology.models import (
    AnnotationCommand, ApproveCommand, AssignedValue, Decision, EffectCommand, EvaluateCommand,
    ExclusionCommand, InterventionCommand, Material, MaterialCommand, MethodCommand, MethodConfig,
    PeriodCommand, QualificationCommand, ReleaseCommand, RunCommand, TestCommand, Range, LinearCorrection,
)
from services.irms_api.metrology.pipeline import identify
from services.irms_api.metrology.repository import Repository
from services.irms_api.metrology.science import anchor_model, budget, control_summary, corrected_normalize, normalize, regression, summary
from services.irms_api.metrology.service import Conflict, Service


def workbook(rows):
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, engine="openpyxl", index=False)
    return buf.getvalue()


def row(index, label, value, mass=100., *, sample_type="Unknown", date=None):
    timestamp = date or datetime.now(timezone.utc).replace(tzinfo=None)
    return {"Index": index, "Label": label, "Sample Type": sample_type, "Comment": str(index),
            "d13C Mean": value, "d18O Mean": value, "d13C SD": .01, "d18O SD": .02,
            "i44_v": mass * .06, "reference_i44_v": mass * .061, "mass_ug": mass,
            "Start Time": timestamp.isoformat() if isinstance(timestamp, datetime) else timestamp, "Status": "Completed", "Evaluate": True}


DECISION = {"actor": "Test scientist", "reason": "Synthetic fixture scientific review"}


class ScientificTests(unittest.TestCase):
    def test_shared_correction_uncertainty_and_monte_carlo(self):
        model = anchor_model([-10., 10.], [-10., 10.], [.02, .02, .01, .01])
        model["correction"] = {"name": "Independent mass model", "slope": .01, "u_slope": .001,
                               "center": 100., "domain": {"low": 50., "high": 150.},
                               "anchor_predictor_means": [100., 100.]}
        result = corrected_normalize(1.2, 120., model, monte_carlo=True, draws=100000)
        self.assertAlmostEqual(result["value"], 1.)
        self.assertAlmostEqual(result["correction"]["coefficient_sensitivity"], -20.)
        self.assertAlmostEqual(result["correction"]["u"], .02)
        self.assertAlmostEqual(result["monte_carlo"]["u"], result["u_normalization_and_correction"], delta=.0004)
        # A common correction at the same predictor cancels in the anchoring.
        model["correction"]["anchor_predictor_means"] = [120., 120.]
        self.assertAlmostEqual(corrected_normalize(1.2, 120., model)["correction"]["u"], 0.)
        with self.assertRaisesRegex(ValueError, "validated domain"):
            corrected_normalize(1.2, 160., model)

    def test_known_ols_and_covariance(self):
        fit = regression([0, 1, 2, 3, 4], [1, 3, 5, 7, 9])
        self.assertAlmostEqual(fit["slope"], 2)
        self.assertAlmostEqual(fit["intercept"], 1)
        self.assertAlmostEqual(fit["residual_standard_error"], 0)
        fit = regression([0, 1, 2], [0, 2, 1])
        self.assertAlmostEqual(fit["slope"], .5)
        self.assertAlmostEqual(fit["residual_standard_error"], math.sqrt(1.5))
        self.assertAlmostEqual(fit["slope_se"], math.sqrt(.75))
        self.assertAlmostEqual(fit["covariance"][0][1], -.75)

    def test_constant_response_and_insufficient_design(self):
        fit = regression([0, 1, 2, 3], [4, 4, 4, 4])
        self.assertAlmostEqual(fit["slope"], 0)
        self.assertIsNone(fit["r_squared"])
        self.assertEqual(regression([1, 1, 1], [2, 3, 4])["status"], "insufficient_evidence")
        self.assertEqual(regression([1, 2], [2, 4])["status"], "insufficient_evidence")

    def test_heteroscedasticity_covariance_is_available(self):
        x = np.arange(20, dtype=float)
        y = 2 + .3 * x + np.random.default_rng(12).normal(0, 1, 20) * (x + 1)
        fit = regression(x.tolist(), y.tolist())
        self.assertFalse(np.allclose(fit["covariance"], fit["hc3_covariance"]))
        self.assertEqual(len(fit["residuals"]), 20)

    def test_two_anchor_hand_calculation_and_monte_carlo(self):
        model = anchor_model([0., 10.], [1., 6.], [.1, .2, .01, .02])
        result = normalize(3.5, model, monte_carlo=True, draws=100000)
        expected = math.sqrt(.5**2 * .1**2 + .5**2 * .2**2 + 1**2 * .01**2 + 1**2 * .02**2)
        self.assertAlmostEqual(result["value"], 5.)
        self.assertAlmostEqual(result["u_norm"], expected)
        self.assertAlmostEqual(result["monte_carlo"]["u"], expected, delta=expected * .012)
        self.assertIsNone(model["residual_uncertainty"])
        self.assertTrue(normalize(7., model)["extrapolated"])
        self.assertEqual(normalize(3.5, model, monte_carlo=True), normalize(3.5, model, monte_carlo=True))

    def test_covariance_in_normalization_and_singular_anchors(self):
        covariance = np.diag([.01, .04, .0001, .0004])
        covariance[0, 1] = covariance[1, 0] = .01
        model = anchor_model([0., 10.], [1., 6.], [.1, .2, .01, .02], covariance.tolist())
        self.assertAlmostEqual(normalize(3.5, model)["u_norm"]**2, .018)
        with self.assertRaises(ValueError):
            anchor_model([0., 10.], [1., 1.01], [.1, .2, .01, .02])
        with self.assertRaises(ValueError):
            anchor_model([0., 10.], [1., 6.], [.1, .2, .01, .02], np.eye(4).tolist())

    def test_assigned_uncertainty_interpretation(self):
        self.assertEqual(AssignedValue(value=1, uncertainty=.2, uncertainty_type="expanded", k=2).standard_uncertainty(), .1)
        with self.assertRaises(ValueError):
            AssignedValue(value=1, uncertainty=.2).standard_uncertainty()
        with self.assertRaises(ValueError):
            AssignedValue(value=1, uncertainty=.2, uncertainty_type="expanded").standard_uncertainty()

    def test_budget_double_counting_and_correlations(self):
        components = [{"name": "norm", "u": .3, "covers": ["assigned_values"]}, {"name": "precision", "u": .4, "covers": ["precision"]}]
        self.assertAlmostEqual(budget(components, 2)["expanded_uncertainty"], 1)
        self.assertAlmostEqual(budget(components, 2, [[.09, .03], [.03, .16]])["u_combined"], math.sqrt(.31))
        with self.assertRaisesRegex(ValueError, "double counting"):
            budget(components + [{"name": "rm", "u": .1, "covers": ["assigned_values"]}], 2)
        with self.assertRaises(ValueError):
            budget(components, 2, [[.09, .2], [.2, .16]])

    def test_qc_sd_not_sem_and_shift_rules(self):
        stats = summary([1., 2., 3.])
        self.assertEqual(stats["sd"], 1)
        self.assertAlmostEqual(stats["se_mean"], 1 / math.sqrt(3))
        points = [{"id": str(i), "value": .2 + i * .01} for i in range(10)]
        control = control_summary(points, 0, .1)
        rules = {f["rule"] for f in control["flags"]}
        self.assertTrue({"eight_same_side", "six_point_trend"}.issubset(rules))
        self.assertEqual(control["status"], "out_of_control")


class ImportTests(unittest.TestCase):
    def test_multilevel_headers_units_and_reference_only_cycle(self):
        grid = [
            [None] * 12,
            ["Index", "User name", "Label", "Sample Type", "Cycle Number", "d13C Mean", "d18O Mean", "d13C SD", "d18O SD", "Sample Intensity", "Standard Intensity", "Mass"],
            [None] * 9 + ["44.00 m/z", "44.00 m/z", None],
            [None] * 5 + ["‰", "‰", "‰", "‰", "mV", "mV", "mg"],
            [1, "operator", "SHP2L", "QC Standard", "Pre", -.75, -5.72, .01, .02, 4000., 4100., .1],
            [None] * 4 + ["Cycle 1"] + [None] * 4 + [6000., 6200., None],
            [None] * 4 + ["Cycle 2"] + [None] * 4 + [None, 12000., None],
        ]
        buf = io.BytesIO()
        pd.DataFrame(grid).to_excel(buf, engine="openpyxl", index=False, header=False, sheet_name="New Table")
        result = parse_workbook(buf.getvalue(), "multilevel.xlsx")
        point = result["measurements"][0]
        self.assertEqual(point["i44_v"], 5.)
        self.assertAlmostEqual(point["reference_i44_v"], 5.15)
        self.assertEqual(point["mass_ug"], 100.)
        self.assertEqual(point["cycle_count"], 2)
        self.assertEqual(point["sample_observation_count"], 2)
        self.assertEqual(len(point["raw_rows"]), 3)
        self.assertEqual(point["sheet_row"], 5)
        self.assertIn("mv", result["source_units"].values())

    def test_all_types_and_explicit_mass_metadata(self):
        types = ["Conditioning", "Delta Standard (DualInlet)", "Drift Correction", "QC Standard", "Ref Gas Calibration", "Unknown", "Standard", "mystery"]
        rows = [row(i + 1, "NBS18", -1, sample_type=t) for i, t in enumerate(types)]
        for r in rows:
            del r["mass_ug"]
        rows[0]["Comment"] = "MASS=0.06 mg; REP=2; explicit metadata"
        rows[1]["Comment"] = "60.0"
        result = parse_workbook(workbook(rows), "types.xlsx")
        roles = [r["source_role"] for r in result["measurements"]]
        self.assertEqual(roles, ["conditioning", "dual_inlet_standard", "unsupported_drift", "qc", "gas_reference_calibration", "unknown", "carbonate_standard", "unrecognized"])
        self.assertEqual(result["measurements"][0]["mass_ug"], 60.)
        self.assertEqual(result["measurements"][0]["replicate"], 2)
        self.assertIsNone(result["measurements"][1]["mass_ug"])

    def test_qc_and_reference_gas_never_anchor(self):
        config = MethodConfig(anchor_ids=["a", "b"], qc_id="q")
        mats = {"a": {"name": "NBS18", "aliases": []}, "b": {"name": "NBS19", "aliases": []}, "q": {"name": "SHP2L", "aliases": []}}
        self.assertEqual(identify({"source_role": "qc", "label": "NBS18"}, config, mats)[0], "unassigned_qc")
        self.assertEqual(identify({"source_role": "gas_reference_calibration", "label": "NBS18"}, config, mats)[0], "gas_reference_calibration")
        self.assertEqual(identify({"source_role": "unknown", "label": "sample", "reference": "NBS18"}, config, mats)[0], "unknown")

    def test_malformed_and_duplicate_indices(self):
        with self.assertRaises(ValueError):
            parse_workbook(b"", "empty.xlsx")
        with self.assertRaises(ValueError):
            parse_workbook(b"broken", "broken.xlsx")
        with self.assertRaisesRegex(ValueError, "Duplicate analysis"):
            parse_workbook(workbook([row(1, "A", 1), row(1, "B", 2)]), "duplicate.xlsx")
        with self.assertRaisesRegex(ValueError, "Missing required"):
            parse_workbook(workbook([{"Label": "A"}]), "incomplete.xlsx")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Repository(self.temp.name)
        self.service = Service(self.repo)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_service] = lambda: self.service
        self.client = TestClient(app)

    def prepare(self, mass_effect=0.):
        state = self.service.state()
        for original in state["materials"]:
            assigned = {iso: AssignedValue(value={"NBS18": -10., "NBS19": 10., "SHP2L": 0.}[original["name"]], uncertainty=.02, uncertainty_type="standard") for iso in ("d13c", "d18o")}
            self.service.save_material(MaterialCommand(**DECISION, material=Material(name=original["name"], aliases=[original["name"]], lot="TEST-ONLY", certificate="Synthetic test certificate", assigned=assigned, verified=True, revision_of=original["id"])))
        method = self.service.state()["methods"][0]
        config = MethodConfig.model_validate(method["config"])
        config.laboratory = "Synthetic test laboratory"
        config.configuration = "fixture-configuration"
        config.reaction_temperature_c = 72
        config.preparation = "Test acid preparation"
        config.acquisition = "Test acquisition conditions"
        config.acquisition_timezone = "UTC"
        config.ranges = {"mass_ug": Range(low=50, high=150), "i44_v": Range(low=2, high=12), "d13c": Range(low=-11, high=11), "d18o": Range(low=-11, high=11)}
        config.qc.bias = {"d13c": .1, "d18o": .1}
        config.precision = {"d13c": .03, "d18o": .04}
        config.precision_evidence = "Independent validation precision fixture"
        config.independence_rationale = "Fixed anchor transformation; no repeated recalibration in precision study"
        config.coverage_rationale = "k=2 for the test model; laboratory coverage requires evaluation"
        config.qualification_interval_days = 90
        config.required_tests = ["Signal stability", "Independent QC"]
        # Revalidate after attribute assignment, as UI commands do.
        method = self.service.save_method(MethodCommand(**DECISION, config=config.model_dump()), method["id"])
        ids = [*method["config"]["anchor_ids"], method["config"]["qc_id"]]
        q = self.service.create_qualification(QualificationCommand(**DECISION, method_id=method["id"], carousel=[{"material_id": id_, "mass_ug": mass, "replicates": 2} for id_ in ids for mass in [60, 100, 140]]))
        rows = []
        for mass in [60, 100, 140]:
            for replicate in range(2):
                for name, base in [("NBS18", -10.), ("NBS19", 10.), ("SHP2L", 0.)]:
                    rows.append(row(len(rows) + 1, name, base + [-.005, .005][replicate] + mass_effect*(mass-100), mass, sample_type="QC Standard" if name == "SHP2L" else "Unknown"))
        run = self.service.import_run("fixture-qualification.xlsx", workbook(rows), RunCommand(**DECISION, context="qualification", method_id=method["id"], qualification_id=q["id"]))
        ev = self.service.run_detail(run["id"])["evaluation"]
        self.assertTrue(ev["ready"], ev["blockers"])
        for test in config.required_tests:
            self.service.save_test(q["id"], TestCommand(**DECISION, name=test, result="pass", criterion="Test fixture acceptance criterion"))
        for effect in ["mass_intensity", "pressure_adjustment", "drift", "memory", "bias"]:
            self.service.save_effect(q["id"], EffectCommand(**DECISION, effect=effect, decision="negligible", evidence="Known synthetic fixture has negligible effect"))
        return method, q, run, ev

    def activate(self):
        method, q, run, ev = self.prepare()
        self.service.approve(method["id"], ApproveCommand(**DECISION, qualification_id=q["id"], evaluation_id=ev["id"]))
        return self.service.state()["active_method"]

    def routine(self, *, shift=0., mass=100., date=None, label="Routine fixture"):
        rows = []
        for i in range(3):
            rows.append(row(len(rows) + 1, "SHP2L", [-.01, 0., .01][i] + shift, sample_type="QC Standard", date=date))
            rows.append(row(len(rows) + 1, f"sample-{i + 1}", 1 + i, mass, date=date))
        return self.service.import_run(label + ".xlsx", workbook(rows), RunCommand(**DECISION))

    def test_seed_has_no_provisional_anchor_certificates(self):
        state = self.service.state()
        self.assertIsNone(state["active_method"])
        anchors = [m for m in state["materials"] if m["name"].startswith("NBS")]
        self.assertTrue(all(m["assigned"]["d13c"]["value"] is None for m in anchors))
        qc = next(m for m in state["materials"] if m["name"] == "SHP2L")
        self.assertEqual(qc["assigned"]["d13c"]["value"], -.75)
        self.assertEqual(qc["assigned"]["d13c"]["uncertainty_type"], "unset")

    def test_validated_activation_and_retirement(self):
        method, q, run, ev = self.prepare()
        self.assertEqual(q["initial_method_snapshot"]["config"], method["config"])
        validated = self.service.approve(method["id"], ApproveCommand(**DECISION, qualification_id=q["id"], evaluation_id=ev["id"], activate=False))
        self.assertEqual(validated["status"], "validated")
        self.assertIsNone(self.service.state()["active_method"])
        activated = self.client.post(f"/metrology/methods/{method['id']}/activate", json=DECISION)
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(activated.json()["status"], "active")
        routine = self.routine()
        retired = self.client.post(f"/metrology/methods/{method['id']}/retire", json=DECISION)
        self.assertEqual(retired.status_code, 200)
        with self.repo.connect() as db:
            frozen = self.service.qualification_detail(db, q["id"])["method_snapshot"]
        self.assertEqual(frozen["status"], "validated")
        self.assertEqual(frozen["config"], method["config"])
        self.assertEqual(frozen["normalization"], ev["normalization"])
        with self.assertRaisesRegex(Conflict, "no longer active"):
            self.service.release(routine["id"], ReleaseCommand(**DECISION, evaluation_id=routine["latest_evaluation_id"]))
        self.assertEqual(self.client.post(f"/metrology/methods/{method['id']}/activate", json=DECISION).status_code, 409)

    def test_same_day_acquisition_before_approval_cannot_release(self):
        method = self.activate()
        before = datetime.fromisoformat(method["approval"]["at"]).replace(tzinfo=None) - timedelta(minutes=1)
        run = self.routine(date=before)
        with self.assertRaisesRegex(Conflict, "predates method approval"):
            self.service.release(run["id"], ReleaseCommand(**DECISION, evaluation_id=run["latest_evaluation_id"]))

    def test_old_acquisition_does_not_join_new_intervention_period(self):
        method = self.activate()
        acquired = datetime.fromisoformat(method["approval"]["at"]).replace(tzinfo=None)
        event = self.service.intervention(InterventionCommand(**DECISION, kind="qc_shift"))
        run = self.routine(date=acquired)
        self.assertEqual(run["period_key"], "initial")
        self.assertNotEqual(run["period_key"], event["id"])

    def test_later_verification_does_not_approve_earlier_routine_acquisition(self):
        method = self.activate()
        event = self.service.intervention(InterventionCommand(**DECISION, kind="qc_shift"))
        earlier = self.routine(label="Acquired while unverified")
        q = self.service.create_qualification(QualificationCommand(**DECISION, method_id=method["id"], trigger="event", intervention_id=event["id"]))
        rows = [row(i+1, "SHP2L", v, sample_type="QC Standard") for i,v in enumerate([-.01,0.,.01])]
        run = self.service.import_run("event-verification.xlsx", workbook(rows), RunCommand(**DECISION, context="qualification", method_id=method["id"], qualification_id=q["id"]))
        for name in q["required_tests"]:
            self.service.save_test(q["id"], TestCommand(**DECISION, name=name, result="pass", criterion="Independent targeted fixture check"))
        self.service.save_effect(q["id"], EffectCommand(**DECISION, effect="bias", decision="negligible", evidence="Independent QC passed"))
        self.service.approve(method["id"], ApproveCommand(**DECISION, qualification_id=q["id"], evaluation_id=run["latest_evaluation_id"]))
        with self.assertRaisesRegex(Conflict, "before the required intervention verification"):
            self.service.release(earlier["id"], ReleaseCommand(**DECISION, evaluation_id=earlier["latest_evaluation_id"]))
        later = self.routine(label="Acquired after verification")
        self.assertFalse(self.service.run_detail(later["id"])["release_blockers"])

    def test_historical_shift_blocks_an_individually_passing_run(self):
        self.activate()
        for i in range(3):
            run = self.routine(shift=.03, label=f"Shift run {i}")
        detail = self.service.run_detail(run["id"])
        self.assertTrue(detail["evaluation"]["qc"]["passed"])
        self.assertTrue(any("Historical QC" in e for e in detail["release_blockers"]))

    def test_correction_requires_evidence_and_approval_then_freezes(self):
        method, q, run, ev = self.prepare(mass_effect=.001)
        config = MethodConfig.model_validate(method["config"])
        asset = self.service.attach(q["id"], "correction-evidence.txt", b"Synthetic training and independent validation reports", Decision(**DECISION), "Test-only correction evidence")
        config.corrections["d13c"] = LinearCorrection(name="Independent mass correction", predictor="mass_ug", slope=.001, u_slope=.00005, center=100., domain=Range(low=50, high=150), training_evidence="Independent coefficient training study A", validation_evidence="Independent holdout study B confirms effect", independence_rationale="Separate coefficient data; fixed coefficient precision study", predictor_uncertainty_rationale="Calibrated balance uncertainty negligible at this slope", evidence_asset_ids=[asset["id"]])
        self.service.save_method(MethodCommand(**DECISION, config=config), method["id"])
        ev = self.service.evaluate_run(run["id"], EvaluateCommand(**DECISION))
        with self.assertRaisesRegex(Conflict, "explicitly approve"):
            self.service.approve(method["id"], ApproveCommand(**DECISION, qualification_id=q["id"], evaluation_id=ev["id"]))
        self.service.save_effect(q["id"], EffectCommand(**DECISION, effect="mass_intensity", decision="approve_correction", evidence="Independent training and validation files reviewed"))
        approved = self.service.approve(method["id"], ApproveCommand(**DECISION, qualification_id=q["id"], evaluation_id=ev["id"]))
        self.assertEqual(len(approved["corrections"]), 1)
        routine = self.routine(mass=120.)
        detail = self.service.run_detail(routine["id"])
        sample = next(r for r in detail["evaluation"]["results"] if r["role"] == "unknown")
        self.assertAlmostEqual(sample["isotopes"]["d13c"]["value"], .98)
        self.assertIn("secondary_correction", [c["name"] for c in sample["isotopes"]["d13c"]["budget"]["components"]])
        mc = self.client.get(f"/metrology/evaluations/{detail['evaluation']['id']}/monte-carlo", params={"measurement_id": sample["id"], "isotope": "d13c"})
        self.assertEqual(mc.status_code, 200, mc.text)
        self.assertIn("correction", mc.json()["monte_carlo"]["distribution"])
        release = self.service.release(routine["id"], ReleaseCommand(**DECISION, evaluation_id=detail["evaluation"]["id"]))
        self.assertEqual(release["evaluation"]["corrections"][0]["predictor"], "mass_ug")

    def test_complete_lifecycle_and_immutable_release(self):
        method = self.activate()
        run = self.routine()
        detail = self.service.run_detail(run["id"])
        self.assertFalse(detail["release_blockers"], detail["release_blockers"])
        release = self.service.release(run["id"], ReleaseCommand(**DECISION, evaluation_id=detail["evaluation"]["id"]))
        self.assertEqual(len(release["results"]), 3)
        result = release["results"][0]["isotopes"]["d13c"]
        self.assertAlmostEqual(result["value"], 1)
        self.assertGreater(result["budget"]["expanded_uncertainty"], .06)
        with self.assertRaises(Conflict):
            self.service.annotate(run["id"], AnnotationCommand(**DECISION, acquisition_complete=True))
        with self.assertRaises(Conflict):
            self.service.save_method(MethodCommand(**DECISION, config=method["config"]), method["id"])
        reopened = Service(Repository(self.temp.name)).run_detail(run["id"])
        self.assertEqual(reopened["releases"][0]["evaluation"], release["evaluation"])

    def test_qc_bias_failure_blocks_api_release(self):
        self.activate()
        run = self.routine(shift=.2)
        ev = self.service.run_detail(run["id"])["evaluation"]
        self.assertFalse(ev["qc"]["passed"])
        response = self.client.post(f"/metrology/runs/{run['id']}/release", json={**DECISION, "evaluation_id": ev["id"]})
        self.assertEqual(response.status_code, 409)
        self.assertIn("QC", response.json()["detail"])

    def test_out_of_range_and_stale_evaluation_block_release(self):
        self.activate()
        run = self.routine(mass=250)
        ev = self.service.run_detail(run["id"])["evaluation"]
        self.assertFalse(ev["ready"])
        self.assertTrue(any("i44_v" in e for e in ev["blockers"]))
        self.assertFalse(any("mass_ug" in e for e in ev["blockers"]))
        self.service.annotate(run["id"], AnnotationCommand(**DECISION, acquisition_complete=True))
        with self.assertRaisesRegex(Conflict, "stale"):
            self.service.release(run["id"], ReleaseCommand(**DECISION, evaluation_id=ev["id"]))

    def test_intervention_requires_targeted_verification(self):
        method = self.activate()
        run = self.routine()
        event = self.service.intervention(InterventionCommand(**DECISION, kind="filament_replacement"))
        detail = self.service.run_detail(run["id"])
        self.assertTrue(any("intervention" in s for s in detail["release_blockers"]))
        q = self.service.create_qualification(QualificationCommand(**DECISION, method_id=method["id"], trigger="event", intervention_id=event["id"]))
        self.assertIn("Sensitivity", q["required_tests"])
        self.assertNotIn("Dual Inlet", q["required_tests"])

    def test_duplicate_import_and_original_bytes(self):
        content = workbook([row(1, "SHP2L", -.75, sample_type="QC Standard")])
        a = self.service.import_run("original.xlsx", content, RunCommand(**DECISION))
        b = self.service.import_run("renamed.xlsx", content, RunCommand(**DECISION))
        self.assertEqual(a["id"], b["id"])
        source = self.service.run_detail(a["id"])["source"]
        self.assertEqual(source["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(self.client.get(f"/metrology/sources/{source['id']}").content, content)

    def test_exclusion_retains_data_and_qc_history_deduplicates_evaluations(self):
        self.activate()
        run = self.routine()
        detail = self.service.run_detail(run["id"])
        unknown = next(r for r in detail["evaluation"]["results"] if r["role"] == "unknown")
        self.service.exclude(run["id"], ExclusionCommand(**DECISION, measurement_id=unknown["id"], evidence="Synthetic documented transfer failure"))
        self.service.evaluate_run(run["id"], EvaluateCommand(**DECISION))
        revised = self.service.run_detail(run["id"])
        self.assertEqual(len(revised["measurements"]), 6)
        self.assertEqual(len(revised["exclusions"]), 1)
        with self.repo.connect() as db:
            obs = self.repo.list(db, "qc_observations", run_id=run["id"])
            self.assertEqual(len(obs), 6)
            history = self.service.qc_history(db)
            # Qualification QC is also visible, but the routine run occurs only once in the chart.
            points = [p for g in history for p in g["isotopes"]["d13c"]["points"] if p["run_id"] == run["id"]]
            self.assertEqual(len(points), 3)

    def test_missing_instrument_check_blocks_approval(self):
        method, q, run, ev = self.prepare()
        self.service.save_test(q["id"], TestCommand(**DECISION, name="Signal stability", result="fail", criterion="Fixture failure"))
        with self.assertRaisesRegex(Conflict, "Signal stability"):
            self.service.approve(method["id"], ApproveCommand(**DECISION, qualification_id=q["id"], evaluation_id=ev["id"]))

    def test_stale_method_revision_blocks_approval(self):
        method, q, run, ev = self.prepare()
        self.service.save_method(MethodCommand(**DECISION, config=method["config"]), method["id"])
        with self.assertRaisesRegex(Conflict, "stale"):
            self.service.approve(method["id"], ApproveCommand(**DECISION, qualification_id=q["id"], evaluation_id=ev["id"]))

    def test_append_only_database_and_audit_hash_chain(self):
        content = workbook([row(1, "SHP2L", 0, sample_type="QC Standard")])
        self.service.import_run("audit.xlsx", content, RunCommand(**DECISION))
        with self.repo.connect() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE measurements SET data='{}'")
            previous = "0" * 64
            for row_ in db.execute("SELECT * FROM audit ORDER BY id"):
                self.assertEqual(row_["previous_hash"], previous)
                self.assertEqual(row_["hash"], hashlib.sha256((previous + row_["data"]).encode()).hexdigest())
                previous = row_["hash"]

    def test_synthetic_workbook_cannot_qualify_or_release(self):
        self.activate()
        rows = [row(i + 1, "SHP2L", .001 * i, sample_type="QC Standard") for i in range(3)]
        rows[0]["Comment"] = "MOCK_METROLOGY"
        run = self.service.import_run("synthetic.xlsx", workbook(rows), RunCommand(**DECISION))
        ev = self.service.run_detail(run["id"])["evaluation"]
        self.assertTrue(any("Synthetic" in b for b in ev["blockers"]))

    def test_homogeneous_period_and_intervention_separation(self):
        method = self.activate()
        first = self.routine(label="day one")
        later = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
        second = self.routine(date=later, label="day two")
        period = self.service.create_period(PeriodCommand(**DECISION, name="Two-day fixture", method_id=method["id"], evaluation_ids=[first["latest_evaluation_id"], second["latest_evaluation_id"]]))
        self.assertEqual(period["statistics"]["d13c"]["n"], 6)
        self.assertGreater(period["statistics"]["d13c"]["sd"], period["statistics"]["d13c"]["se_mean"])
        self.service.intervention(InterventionCommand(**DECISION, kind="source_opening"))
        third = self.routine(date=later + timedelta(days=1), label="new state")
        with self.assertRaisesRegex(ValueError, "intervention period"):
            self.service.create_period(PeriodCommand(**DECISION, name="Invalid mixed period", method_id=method["id"], evaluation_ids=[first["latest_evaluation_id"], third["latest_evaluation_id"]]))

    def test_reports_are_stored_reproducibly_and_client_needs_release(self):
        self.activate()
        run = self.routine()
        release = self.service.release(run["id"], ReleaseCommand(**DECISION, evaluation_id=run["latest_evaluation_id"]))
        response = self.client.post("/metrology/reports", json={**DECISION, "kind": "client", "target_id": release["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        pdf = self.client.get(f"/metrology/reports/{report['id']}/pdf").content
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(hashlib.sha256(pdf).hexdigest(), report["sha256"])
        self.assertEqual(pdf, self.client.get(f"/metrology/reports/{report['id']}/pdf").content)
        invalid = self.client.post("/metrology/reports", json={**DECISION, "kind": "client", "target_id": run["id"]})
        self.assertEqual(invalid.status_code, 404)
        history = self.client.post("/metrology/reports", json={**DECISION, "kind": "history"})
        self.assertEqual(history.status_code, 200, history.text)
        q = self.service.state()["qualifications"][0]
        dossier = self.client.post("/metrology/reports", json={**DECISION, "kind": "qualification", "target_id": q["id"]})
        self.assertEqual(dossier.status_code, 200, dossier.text)

    def test_invalid_requests_are_actionable(self):
        response = self.client.post("/metrology/interventions", json={"actor": "", "reason": "x", "kind": "source_opening"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/metrology/runs/not-found").status_code, 404)

    def test_report_reissue_preserves_saved_data_and_original_pdf(self):
        legacy_pdf = b"%PDF-1.4\n% legacy report rendering fixture\n"
        with patch("services.irms_api.metrology.api.build_report", return_value=legacy_pdf):
            response = self.client.post("/metrology/reports", json={**DECISION, "kind": "history"})
        self.assertEqual(response.status_code, 200, response.text)
        original = response.json()
        saved_snapshot = self.client.get(f"/metrology/reports/{original['id']}/json").content
        response = self.client.post(f"/metrology/reports/{original['id']}/reissue", json=DECISION)
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertNotEqual(updated["id"], original["id"])
        self.assertEqual(updated["reissued_from"], original["id"])
        self.assertEqual(updated["snapshot_sha256"], original["snapshot_sha256"])
        self.assertEqual(self.client.get(f"/metrology/reports/{updated['id']}/json").content, saved_snapshot)
        self.assertEqual(self.client.get(f"/metrology/reports/{original['id']}/pdf").content, legacy_pdf)
        new_pdf = self.client.get(f"/metrology/reports/{updated['id']}/pdf").content
        self.assertTrue(new_pdf.startswith(b"%PDF"))
        self.assertNotEqual(new_pdf, legacy_pdf)
        self.assertEqual(hashlib.sha256(new_pdf).hexdigest(), updated["sha256"])
        self.assertEqual(self.client.post("/metrology/reports/missing/reissue", json=DECISION).status_code, 404)


if __name__ == "__main__":
    unittest.main()
