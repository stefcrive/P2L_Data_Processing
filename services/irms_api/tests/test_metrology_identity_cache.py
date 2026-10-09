"""Request-local identity reuse must preserve the legacy importer rules."""
import unittest
from unittest.mock import patch

from services.irms_api.domain import import_session
from services.irms_api.metrology.importer import measurement_identity, reuse_measurement_identities


class MeasurementIdentityCacheTests(unittest.TestCase):
    def test_cached_identities_match_uncached_importer_for_old_layouts(self):
        cases = [
            ({"label": "Core 1 - calcite - extra", "comment": " aliquot 2 "}, "qtegra_raw"),
            ({"label": "Core 1", "comment": None}, "qtegra_raw"),
            ({"label": None, "comment": float("nan")}, "qtegra_raw"),
            ({"label": 0.0, "comment": True}, "qtegra_raw"),
            ({"label": -0.0, "comment": True}, "qtegra_raw"),
            ({"label": "Core 1 - calcite", "comment": "aliquot 2"}, "isodat_raw"),
            ({"raw_rows": [{"values": {"Identifier 1": " A ", "Identifier 2": 12, "Species": "calcite"}}]}, "isodat_raw"),
            ({"raw_rows": [{"values": {"Sample": "sample", "Row": 3}}]}, "generic"),
            ({"raw_rows": [{"values": {"Label": "raw - species", "Comment": "raw comment"}}],
              "label": "different label", "comment": "different comment"}, "qtegra_raw"),
            ({"identifier1": "edited", "identifier2": "", "species": "", "label": "raw"}, "qtegra_raw"),
        ]
        expected = [measurement_identity(record, kind) for record, kind in cases]
        with reuse_measurement_identities():
            for (record, kind), identity in zip(cases, expected):
                with self.subTest(record=record, kind=kind):
                    self.assertEqual(measurement_identity(record, kind), identity)
                    returned = measurement_identity(record, kind)
                    returned["identifier1"] = "mutated by caller"
                    self.assertEqual(measurement_identity(record, kind), identity)

    def test_reuse_is_scoped_by_inputs_and_cleared_after_exception(self):
        record = {"label": "A - calcite", "comment": "1"}
        with patch.object(import_session, "_suggest_import_parsing_config", wraps=import_session._suggest_import_parsing_config) as parse:
            with self.assertRaisesRegex(RuntimeError, "failed"):
                with reuse_measurement_identities():
                    first = measurement_identity(record)
                    with reuse_measurement_identities():
                        self.assertEqual(measurement_identity(dict(record)), first)
                    self.assertEqual(parse.call_count, 1)
                    self.assertNotEqual(measurement_identity(record, "isodat_raw"), first)
                    self.assertNotEqual(measurement_identity({**record, "comment": "2"}), first)
                    self.assertEqual(parse.call_count, 3)
                    raise RuntimeError("failed")
            with reuse_measurement_identities():
                self.assertEqual(measurement_identity(record), first)
                self.assertEqual(parse.call_count, 4)


if __name__ == "__main__":
    unittest.main()
