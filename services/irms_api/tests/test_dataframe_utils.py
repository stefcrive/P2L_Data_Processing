from __future__ import annotations

import unittest

import pandas as pd

from services.irms_api.domain.constants import (
    CYCLE1_SIGNAL_DIFF44_COL,
    CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
    CYCLE1_SIGNAL_REF44_COL,
    CYCLE1_SIGNAL_SAMP44_COL,
)
from services.irms_api.domain.shared.dataframe import (
    _ensure_cycle1_signal_difference_columns,
    _parse_numeric_token,
    _split_label_species,
)


class DataframeUtilsTests(unittest.TestCase):
    def test_parse_numeric_token_handles_decimal_comma(self) -> None:
        self.assertEqual(_parse_numeric_token("34,26-34,28"), 34.26)

    def test_split_label_species(self) -> None:
        identifier, species = _split_label_species("Coral- Porites")
        self.assertEqual(identifier, "Coral")
        self.assertEqual(species, "Porites")

    def test_ensure_cycle1_signal_difference_columns_populates_pressure_weighted_mismatch(self) -> None:
        df = pd.DataFrame(
            {
                CYCLE1_SIGNAL_SAMP44_COL: [20.0, 10.0],
                CYCLE1_SIGNAL_REF44_COL: [10.0, 10.0],
                CYCLE1_SIGNAL_DIFF44_COL: [10.0, 0.0],
            }
        )
        result = _ensure_cycle1_signal_difference_columns(df)
        self.assertIn(CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL, result.columns)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL]), 13.3333333333, places=6)
        self.assertAlmostEqual(float(result.loc[1, CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL]), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
