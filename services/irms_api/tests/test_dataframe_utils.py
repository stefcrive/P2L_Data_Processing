from __future__ import annotations

import unittest

import pandas as pd

from services.irms_api.domain.shared.dataframe import _parse_numeric_token, _split_label_species


class DataframeUtilsTests(unittest.TestCase):
    def test_parse_numeric_token_handles_decimal_comma(self) -> None:
        self.assertEqual(_parse_numeric_token("34,26-34,28"), 34.26)

    def test_split_label_species(self) -> None:
        identifier, species = _split_label_species("Coral- Porites")
        self.assertEqual(identifier, "Coral")
        self.assertEqual(species, "Porites")


if __name__ == "__main__":
    unittest.main()
