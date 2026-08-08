from __future__ import annotations

import unittest

import pandas as pd

from services.irms_api.domain.constants import (
    CYCLE1_SIGNAL_DIFF44_COL,
    CYCLE1_SIGNAL_DIFF45_COL,
    CYCLE1_SIGNAL_DIFF46_COL,
    CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
    CYCLE1_SIGNAL_REF44_COL,
    CYCLE1_SIGNAL_REF45_COL,
    CYCLE1_SIGNAL_REF46_COL,
    CYCLE1_SIGNAL_SAMP44_COL,
    CYCLE1_SIGNAL_SAMP45_COL,
    CYCLE1_SIGNAL_SAMP46_COL,
    VALID_CYCLES_COL,
)
from services.irms_api.domain.shared.dataframe import (
    _apply_cycle_averages,
    _ensure_cycle1_signal_difference_columns,
    _normalize_signal_intensity,
    _parse_numeric_token,
    _split_label_species,
)


class DataframeUtilsTests(unittest.TestCase):
    def test_parse_numeric_token_handles_decimal_comma(self) -> None:
        self.assertEqual(_parse_numeric_token("34,26-34,28"), 34.26)

    def test_parse_numeric_token_preserves_numeric_three_decimal_value(self) -> None:
        self.assertEqual(_parse_numeric_token(19.987), 19.987)

    def test_normalize_signal_intensity_handles_mixed_volts_and_millivolts(self) -> None:
        values = pd.Series([14574.861, 19.987, 17.700, None])

        normalized = _normalize_signal_intensity(values)
        normalized_again = _normalize_signal_intensity(normalized)

        self.assertAlmostEqual(float(normalized.iloc[0]), 14.574861, places=6)
        self.assertAlmostEqual(float(normalized.iloc[1]), 19.987, places=6)
        self.assertAlmostEqual(float(normalized.iloc[2]), 17.700, places=6)
        pd.testing.assert_series_equal(normalized_again, normalized)

    def test_parse_numeric_token_handles_unicode_minus(self) -> None:
        self.assertEqual(_parse_numeric_token("−12.5"), -12.5)

    def test_split_label_species(self) -> None:
        identifier, species = _split_label_species("Coral- Porites")
        self.assertEqual(identifier, "Coral")
        self.assertEqual(species, "Porites")
        identifier, species = _split_label_species("MD23-3678")
        self.assertEqual(identifier, "MD23-3678")
        self.assertIsNone(species)
        identifier, species = _split_label_species("MD23-3678 - G. ruber")
        self.assertEqual(identifier, "MD23-3678")
        self.assertEqual(species, "G. ruber")

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

    def test_apply_cycle_averages_uses_first_valid_cycle_for_samp_ref_difference(self) -> None:
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
        # from the first available valid cycle (cycle 2 here).
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_SAMP44_COL]), 30.0, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_REF44_COL]), 23.0, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_DIFF44_COL]), 7.0, places=6)

    def test_ensure_cycle1_signal_difference_columns_adds_valid_cycles(self) -> None:
        df = pd.DataFrame(
            {
                "d13C Cycles Used": [8, 7, None],
                "d18O Cycles Used": [8, 6, 5],
            }
        )

        result = _ensure_cycle1_signal_difference_columns(df)

        self.assertIn(VALID_CYCLES_COL, result.columns)
        self.assertEqual(pd.to_numeric(result[VALID_CYCLES_COL], errors="coerce").tolist(), [8.0, 6.0, 5.0])

    def test_apply_cycle_averages_marks_fully_saturated_when_valid_cycles_below_three(self) -> None:
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

        self.assertEqual(str(result.loc[0, "Collector Status"]), "Fully Saturated Collectors")
        self.assertEqual(int(result.loc[0, "d13C Cycles Used"]), 2)
        self.assertEqual(int(result.loc[0, "d18O Cycles Used"]), 2)
        self.assertTrue(pd.isna(result.loc[0, "d 13C/12C  Mean"]))
        self.assertTrue(pd.isna(result.loc[0, "d 18O/16O  Mean"]))

    def test_apply_cycle_averages_keeps_partially_saturated_when_valid_cycles_at_least_three(self) -> None:
        df = pd.DataFrame(
            {
                "Cycle Number": ["Pre", "1", "2", "3", "4"],
                "Identifier 1": ["SampleA", None, None, None, None],
                "Identifier 2": ["1", None, None, None, None],
                "Label": ["SampleA - Coral", None, None, None, None],
                "Species": ["Coral", None, None, None, None],
                "d 13C/12C  Mean": [None, 1.00, 1.10, 1.20, 1.30],
                "d 18O/16O  Mean": [None, 2.00, 2.10, 2.20, 2.30],
                "Cycle Intensity Samp 44": [None, 60.0, 30.0, 25.0, 24.0],
                "Cycle Intensity Ref 44": [None, 50.0, 23.0, 21.0, 20.0],
            }
        )

        result = _apply_cycle_averages(df)

        self.assertEqual(str(result.loc[0, "Collector Status"]), "Partially Saturated Collectors")
        self.assertEqual(int(result.loc[0, "d13C Cycles Used"]), 3)
        self.assertEqual(int(result.loc[0, "d18O Cycles Used"]), 3)
        self.assertAlmostEqual(float(result.loc[0, "d 13C/12C  Mean"]), 1.1, places=6)
        self.assertAlmostEqual(float(result.loc[0, "d 18O/16O  Mean"]), 2.1, places=6)

    def test_ensure_cycle1_signal_difference_columns_keeps_negative_sign_for_ambiguous_channels(self) -> None:
        df = pd.DataFrame(
            {
                "1  Cycle Int  44 A": [8.0, 7.0],
                "1  Cycle Int  44 B": [10.0, 9.0],
                CYCLE1_SIGNAL_DIFF44_COL: [-2.0, -2.0],
            }
        )

        result = _ensure_cycle1_signal_difference_columns(df)

        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_SAMP44_COL]), 8.0, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_REF44_COL]), 10.0, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_DIFF44_COL]), -2.0, places=6)
        self.assertAlmostEqual(float(result.loc[1, CYCLE1_SIGNAL_DIFF44_COL]), -2.0, places=6)

    def test_ensure_cycle1_signal_difference_columns_orients_from_raw_diff_column(self) -> None:
        df = pd.DataFrame(
            {
                "1  Cycle Int  44 A": [8.0, 7.0],
                "1  Cycle Int  44 B": [10.0, 9.0],
                "1  Cycle Int  Diff Samp-Ref  44 Raw": [-2.0, -2.0],
            }
        )

        result = _ensure_cycle1_signal_difference_columns(df)

        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_SAMP44_COL]), 8.0, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_REF44_COL]), 10.0, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_DIFF44_COL]), -2.0, places=6)
        self.assertAlmostEqual(float(result.loc[1, CYCLE1_SIGNAL_DIFF44_COL]), -2.0, places=6)

    def test_apply_cycle_averages_keeps_negative_samp_ref_sign_for_ambiguous_channels(self) -> None:
        df = pd.DataFrame(
            {
                "Cycle Number": ["Pre", "1", "2", "3"],
                "Identifier 1": ["SampleA", None, None, None],
                "Identifier 2": ["1", None, None, None],
                "Label": ["SampleA - Coral", None, None, None],
                "Species": ["Coral", None, None, None],
                "d 13C/12C  Mean": [None, 1.00, 1.10, 1.20],
                "d 18O/16O  Mean": [None, 2.00, 2.10, 2.20],
                "Cycle Intensity 44 A": [None, 8.0, 8.5, 9.0],
                "Cycle Intensity 44 B": [None, 10.0, 10.5, 11.0],
                CYCLE1_SIGNAL_DIFF44_COL: [-2.0, None, None, None],
            }
        )

        result = _apply_cycle_averages(df)

        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_SAMP44_COL]), 8.0, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_REF44_COL]), 10.0, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_DIFF44_COL]), -2.0, places=6)

    def test_apply_cycle_averages_uses_cycle_level_diff_sign_when_pre_diff_missing(self) -> None:
        df = pd.DataFrame(
            {
                "Cycle Number": ["Pre", "1", "2", "3"],
                "Identifier 1": ["SampleA", None, None, None],
                "Identifier 2": ["1", None, None, None],
                "Label": ["SampleA - Coral", None, None, None],
                "Species": ["Coral", None, None, None],
                "d 13C/12C  Mean": [None, 1.00, 1.10, 1.20],
                "d 18O/16O  Mean": [None, 2.00, 2.10, 2.20],
                "Cycle Intensity 44 A": [None, 8.0, 8.5, 9.0],
                "Cycle Intensity 44 B": [None, 10.0, 10.5, 11.0],
                "1  Cycle Int  Diff Samp-Ref  44 Raw": [None, -2.0, -2.0, -2.0],
            }
        )

        result = _apply_cycle_averages(df)

        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_SAMP44_COL]), 8.0, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_REF44_COL]), 10.0, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_DIFF44_COL]), -2.0, places=6)

    def test_apply_cycle_averages_populates_mass45_mass46_diffs_from_first_valid_cycle(self) -> None:
        df = pd.DataFrame(
            {
                "Cycle Number": ["Pre", "1", "2", "3"],
                "Identifier 1": ["SampleA", None, None, None],
                "Identifier 2": ["1", None, None, None],
                "Label": ["SampleA - Coral", None, None, None],
                "Species": ["Coral", None, None, None],
                "d 13C/12C  Mean": [None, 1.00, 1.10, 1.20],
                "d 18O/16O  Mean": [None, 2.00, 2.10, 2.20],
                "Cycle Intensity Samp 45": [None, 1.60, 1.50, 1.40],
                "Cycle Intensity Ref 45": [None, 3.20, 3.00, 2.80],
                "Cycle Intensity Samp 46": [None, 2.10, 2.00, 1.90],
                "Cycle Intensity Ref 46": [None, 4.40, 4.10, 3.80],
            }
        )

        result = _apply_cycle_averages(df)

        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_SAMP45_COL]), 1.60, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_REF45_COL]), 3.20, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_DIFF45_COL]), -1.60, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_SAMP46_COL]), 2.10, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_REF46_COL]), 4.40, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_DIFF46_COL]), -2.30, places=6)

    def test_ensure_cycle1_signal_difference_columns_computes_diff45_diff46_from_canonical_columns(self) -> None:
        df = pd.DataFrame(
            {
                CYCLE1_SIGNAL_SAMP45_COL: [1.6],
                CYCLE1_SIGNAL_REF45_COL: [3.2],
                CYCLE1_SIGNAL_SAMP46_COL: [2.1],
                CYCLE1_SIGNAL_REF46_COL: [4.4],
            }
        )

        result = _ensure_cycle1_signal_difference_columns(df)

        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_DIFF45_COL]), -1.6, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_DIFF46_COL]), -2.3, places=6)

    def test_ensure_cycle1_signal_difference_columns_derives_diff45_diff46_from_unlabeled_mass_columns(self) -> None:
        df = pd.DataFrame(
            {
                CYCLE1_SIGNAL_SAMP44_COL: [1.52],
                CYCLE1_SIGNAL_REF44_COL: [3.10],
                "45.00 m/z": [1.64],
                "45.00 m/z__dup2": [3.31],
                "46.00 m/z": [2.15],
                "46.00 m/z__dup2": [4.42],
            }
        )

        result = _ensure_cycle1_signal_difference_columns(df)

        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_SAMP45_COL]), 1.64, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_REF45_COL]), 3.31, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_DIFF45_COL]), -1.67, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_SAMP46_COL]), 2.15, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_REF46_COL]), 4.42, places=6)
        self.assertAlmostEqual(float(result.loc[0, CYCLE1_SIGNAL_DIFF46_COL]), -2.27, places=6)


if __name__ == "__main__":
    unittest.main()
