from __future__ import annotations

import unittest

import pandas as pd

from services.irms_api.domain.calibration.core import (
    _apply_linearity_line_offsets,
    _apply_linearity_correction,
    _apply_manual_linearity_override_to_standards,
    _compute_linearity_fit,
    create_calibration_plots,
    identify_outliers_iqr,
    single_point_calibration,
)
from services.irms_api.domain.constants import ISOTYPE_D13C, ISOTYPE_D18O


class CalibrationCoreTests(unittest.TestCase):
    def test_single_point_calibration_changes_value(self) -> None:
        calibrated = single_point_calibration(-1.0, -2.0, -0.5)
        self.assertIsInstance(calibrated, float)
        self.assertNotEqual(calibrated, -1.0)

    def test_compute_linearity_fit_returns_expected_keys(self) -> None:
        df = pd.DataFrame({"intensity": [10.0, 15.0, 20.0], "value": [1.0, 2.0, 3.0]})
        result = _compute_linearity_fit(df, "value", "intensity")
        self.assertEqual(set(result.keys()), {"slope", "intercept", "quad", "degree", "r2", "x_ref", "n"})
        self.assertEqual(result["n"], 3)
        self.assertEqual(result["degree"], 1)

    def test_compute_linearity_fit_quadratic(self) -> None:
        df = pd.DataFrame(
            {
                "intensity": [1.0, 2.0, 3.0, 4.0, 5.0],
                "value": [15.0, 28.0, 47.0, 72.0, 103.0],  # y = 10 + 2x + 3x^2
            }
        )
        result = _compute_linearity_fit(df, "value", "intensity", quadratic=True)
        self.assertEqual(result["degree"], 2)
        self.assertAlmostEqual(float(result["quad"]), 3.0, places=6)
        self.assertAlmostEqual(float(result["slope"]), 4.0, places=6)
        self.assertAlmostEqual(float(result["intercept"]), 8.0, places=6)

    def test_compute_linearity_fit_identical_x_returns_without_regression_error(self) -> None:
        df = pd.DataFrame({"intensity": [10.0, 10.0, 10.0], "value": [1.0, 2.0, 3.0]})
        result = _compute_linearity_fit(df, "value", "intensity")
        self.assertEqual(int(result["n"]), 3)
        self.assertTrue(pd.isna(pd.to_numeric(pd.Series([result["slope"]]), errors="coerce").iloc[0]))
        self.assertAlmostEqual(float(result["intercept"]), 2.0, places=6)

    def test_apply_linearity_correction_quadratic(self) -> None:
        df = pd.DataFrame(
            {
                "1  Cycle Int  Samp  44": [1.0, 2.0, 3.0, 4.0, 5.0],
                "d 13C/12C  Mean": [15.0, 28.0, 47.0, 72.0, 103.0],  # y = 10 + 2x + 3x^2
            }
        )
        fit = _compute_linearity_fit(df, "d 13C/12C  Mean", "1  Cycle Int  Samp  44", quadratic=True)
        corrected = _apply_linearity_correction(
            df,
            "1  Cycle Int  Samp  44",
            {"d13C": fit, "d18O": {}, "intensity_col": "1  Cycle Int  Samp  44"},
        )
        values = pd.to_numeric(corrected["d13C_linearity_corrected"], errors="coerce")
        self.assertTrue(values.notna().all())
        self.assertAlmostEqual(float(values.min()), 47.0, places=6)
        self.assertAlmostEqual(float(values.max()), 47.0, places=6)

    def test_apply_linearity_line_offsets_adjusts_selected_axis_by_line(self) -> None:
        df = pd.DataFrame(
            {
                "Line": [1, 2, 1],
                "1  Cycle Int  Samp  44": [10.0, 10.0, 12.5],
            }
        )

        adjusted = _apply_linearity_line_offsets(
            df,
            "1  Cycle Int  Samp  44",
            line_1_offset=1.5,
            line_2_offset=-0.5,
        )

        values = pd.to_numeric(adjusted["1  Cycle Int  Samp  44"], errors="coerce")
        self.assertAlmostEqual(float(values.iloc[0]), 11.5, places=6)
        self.assertAlmostEqual(float(values.iloc[1]), 9.5, places=6)
        self.assertAlmostEqual(float(values.iloc[2]), 14.0, places=6)

    def test_identify_outliers_iqr(self) -> None:
        df = pd.DataFrame({"value": [1, 1, 1, 1, 25]})
        mask = identify_outliers_iqr(df, "value", 1.5)
        self.assertTrue(bool(mask.iloc[-1]))

    def test_manual_linearity_override_diff_mode_combines_mismatch_and_initial_intensity(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["STD", "STD", "STD", "SAMPLE"],
                "d 13C/12C  Mean": [1.0, 1.0, 1.0, 1.0],
                "1  Cycle Int  Samp  44": [15.0, 30.0, 40.0, 25.0],
                "1  Cycle Int  Ref  44": [10.0, 20.0, 20.0, 10.0],
                "1  Cycle Int  Diff Samp-Ref  44": [5.0, 10.0, 20.0, 15.0],
            }
        )

        corrected = _apply_manual_linearity_override_to_standards(
            df,
            selected_standards=["STD"],
            enabled=True,
            d13_per_10v=1.0,
            d18_per_10v=0.0,
            use_diff_intensity=True,
            fits=None,
        )

        # Row 0 and 1 have equal relative mismatch, but different initial sample intensity;
        # pressure-weighted mode should produce different corrections.
        self.assertGreater(float(corrected.loc[0, "d 13C/12C  Mean"]), float(corrected.loc[1, "d 13C/12C  Mean"]))
        self.assertAlmostEqual(float(corrected.loc[2, "d 13C/12C  Mean"]), 0.0909090909, places=6)
        self.assertAlmostEqual(float(corrected.loc[3, "d 13C/12C  Mean"]), 1.0, places=6)

    def test_manual_linearity_override_quadratic_uses_quadratic_parameter(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["STD", "STD", "SAMPLE"],
                "d 13C/12C  Mean": [1.0, 1.0, 1.0],
                "1  Cycle Int  Samp  44": [10.0, 20.0, 15.0],
            }
        )

        corrected = _apply_manual_linearity_override_to_standards(
            df,
            selected_standards=["STD"],
            enabled=True,
            d13_per_10v=0.0,
            d18_per_10v=0.0,
            d13_per_10v2=1.0,
            d18_per_10v2=0.0,
            quadratic=True,
            use_diff_intensity=False,
            fits=None,
        )

        # x_ref over standards is 15; delta at x=10 is -1.25 and at x=20 is +1.75 for c=1 per (10V)^2.
        self.assertAlmostEqual(float(corrected.loc[0, "d 13C/12C  Mean"]), 2.25, places=6)
        self.assertAlmostEqual(float(corrected.loc[1, "d 13C/12C  Mean"]), -0.75, places=6)
        self.assertAlmostEqual(float(corrected.loc[2, "d 13C/12C  Mean"]), 1.0, places=6)

    def test_create_calibration_plots_handles_identical_true_values(self) -> None:
        standards_reference_df = pd.DataFrame(
            {
                "Standard": ["STD_A", "STD_A", "STD_B", "STD_B"],
                "Isotopic_Value_Type": [ISOTYPE_D13C, ISOTYPE_D18O, ISOTYPE_D13C, ISOTYPE_D18O],
                "Value": [-1.0, -5.0, -1.0, -5.0],
            }
        )
        measurement_df = pd.DataFrame(
            {
                "Identifier 1": ["STD_A", "STD_A", "STD_B", "STD_B"],
                "Identifier 2": ["1", "2", "1", "2"],
                "d 13C/12C  Mean": [-0.9, -1.1, -0.8, -1.2],
                "d 18O/16O  Mean": [-5.1, -4.9, -5.2, -4.8],
                "Date_ordinal": [739252, 739253, 739252, 739253],
            }
        )

        figs = create_calibration_plots(
            standards_reference_df,
            measurement_df,
            selected_standards=["STD_A", "STD_B"],
            color_param="Date_ordinal",
        )
        self.assertIn(ISOTYPE_D13C, figs)
        annotation_texts = [str(getattr(item, "text", "")) for item in (figs[ISOTYPE_D13C].layout.annotations or [])]
        self.assertTrue(any("Regression undefined" in text for text in annotation_texts))


if __name__ == "__main__":
    unittest.main()
