import copy
import unittest

from services.irms_api.metrology.importer import measurement_identity, parse_workbook
from services.irms_api.metrology.residual_preview import residual_previews
from services.irms_api.metrology.models import ResidualOverride
from services.irms_api.tests.test_metrology import workbook, row


class IdentityTests(unittest.TestCase):
    def test_qtegra_import_and_existing_source_use_original_identity_rules(self):
        source = row(1, "BTS - Porites", 1)
        source["Comment"] = "007"
        imported = parse_workbook(workbook([source]), "qtegra.xlsx")["measurements"][0]
        expected = {"identifier1": "BTS", "identifier2": "007", "species": "Porites"}
        self.assertEqual(measurement_identity(imported), expected)
        historical = {k: v for k, v in imported.items() if k not in expected}
        self.assertEqual(measurement_identity(historical), expected)
        self.assertEqual(imported["label"], "BTS - Porites")
        self.assertEqual(imported["raw_rows"][0]["values"]["Comment"], "007")

    def test_isodat_preserves_explicit_species_and_identifiers(self):
        record = {"raw_rows": [{"values": {"Identifier 1": "Core A", "Identifier 2": "003", "Species": "Cibicidoides", "Comment": "note"}}]}
        self.assertEqual(measurement_identity(record, "isodat_raw"), {"identifier1": "Core A", "identifier2": "003", "species": "Cibicidoides"})


class ResidualPreviewTests(unittest.TestCase):
    def test_original_quadratic_model_removes_known_effect_without_mutating_results(self):
        points = [{"id": str(x), "x": x, "y": 4 + 2*x + .5*x*x} for x in range(1, 9)]
        diagnostics = {"materials": [{"material_id": "qc", "isotopes": {"d13c": {"intensity_dependence": {"points": points}}}}]}
        snapshot = copy.deepcopy(diagnostics)
        key = "qc:intensity_dependence:d13c"
        preview = residual_previews(diagnostics, {key: ResidualOverride(enabled=True, algorithm="quadratic").model_dump()})[key]
        self.assertAlmostEqual(preview["model"]["slope"], 2)
        self.assertAlmostEqual(preview["model"]["quad"], .5)
        self.assertLess(preview["after"]["sd"], 1e-12)
        self.assertEqual(preview["points"][0]["id"], "1")
        self.assertEqual(diagnostics, snapshot)

    def test_manual_coefficients_center_and_offset_are_applied(self):
        points = [{"id": str(x), "x": x, "y": 10+3*x} for x in range(5)]
        diagnostics = {"materials": [{"material_id": "qc", "isotopes": {"d13c": {"drift": {"points": points}}}}]}
        key = "qc:drift:d13c"
        config = ResidualOverride(enabled=True,slope=3,center=2,offset=1).model_dump()
        preview = residual_previews(diagnostics, {key: config})[key]
        self.assertEqual([p["y"] for p in preview["points"]], [17]*5)
        self.assertEqual(residual_previews(diagnostics, {key: {**config, "enabled": False}}), {})

    def test_insufficient_predictor_range_is_not_a_zero_effect(self):
        diagnostics = {"materials": [{"material_id": "qc", "isotopes": {"d13c": {"memory": {"points": [{"x": 1, "y": 2}]*5}}}}]}
        key = "qc:memory:d13c"
        result = residual_previews(diagnostics, {key: ResidualOverride(enabled=True).model_dump()})[key]
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertNotIn("effect_span", result)


if __name__ == "__main__":
    unittest.main()
