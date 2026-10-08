import unittest

from services.irms_api.metrology.importer import number


class ImportNumericTests(unittest.TestCase):
    def test_decimal_comma_preserves_precision_and_units(self):
        for token, expected in [("4,802", 4.802), ("4,479 ?", 4.479),
                                ("-4,802", -4.802), ("4.802", 4.802),
                                ("4,802e-2", 0.04802)]:
            with self.subTest(token=token):
                self.assertEqual(number(token), expected)
        self.assertAlmostEqual(number("4,802 mV", dimension="voltage"), 0.004802)
