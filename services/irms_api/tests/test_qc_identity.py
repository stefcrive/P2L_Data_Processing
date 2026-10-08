import unittest

from services.irms_api.metrology.models import MethodConfig
from services.irms_api.metrology.pipeline import identify


class QcIdentityTests(unittest.TestCase):
    def setUp(self):
        self.config = MethodConfig(anchor_ids=["a", "b"], qc_id="q")
        self.materials = {"q": {"name": "SHP2L", "aliases": ["SHP-2L"]}}

    def test_unknown_qc_matches_identifier_despite_species_in_label(self):
        for name in ["SHP2L", " shp2l ", "SHP-2L"]:
            with self.subTest(name=name):
                row = {"source_role": "unknown", "label": "SHP2L - U. peregrina",
                       "identifier1": name, "species": "U. peregrina"}
                self.assertEqual(identify(row, self.config, self.materials), ("qc", "q"))

    def test_older_import_parses_identifier_from_raw_label(self):
        self.assertEqual(identify({"source_role": "unknown",
            "label": "SHP2L - U. peregrina"}, self.config, self.materials), ("qc", "q"))

    def test_qc_reference_or_species_does_not_classify_unknown_sample(self):
        row = {"source_role": "unknown", "label": "Sample - SHP2L",
               "identifier1": "Sample", "species": "SHP2L", "reference": "SHP2L"}
        self.assertEqual(identify(row, self.config, self.materials), ("unknown", None))
