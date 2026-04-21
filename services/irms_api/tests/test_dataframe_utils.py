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
    _apply_cycle_averages,
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

    def test_apply_cycle_averages_uses_last_valid_cycle_for_samp_ref_difference(self) -> None:
        df = pd.DataFrame(
            {
                "Cycle Number": ["Pre", "1", "2", "3"],
                "Identifier 1": ["SampleA", None, None, None],
                "Identifier 2": ["1", None, None, None],
                "Label": ["SampleA - Coral", None, None, None],
                "Species": ["Coral", None, None, None],
                "d 13C/12C  Mean": [None, 1.00, 1.10, 1.20],
                "d 18O/16O  Mean": [None, 2.00, 2.10, 2.20],
                "Cycle Intensity Samp 44": [None, 60.0, 30.0, 25.0],
                "Cycle Intensity Ref 44": [None, 50.0, 23.0, 21.0],
            }
        )

        result = _apply_cycle_averages(df)

        # Cycle 1 is saturated (>48 V), so the m/z44 signal pair should be taken
        # from the last available valid cycle (cycle 3 here).
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_SAMP44_COL]), 25.0, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_REF44_COL]), 21.0, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_DIFF44_COL]), 4.0, places=6)


if __name__ == "__main__":
    unittest.main()
