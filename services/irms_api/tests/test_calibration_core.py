from __future__ import annotations

import unittest

import pandas as pd

from services.irms_api.domain.calibration.core import (
    _compute_linearity_fit,
    identify_outliers_iqr,
    single_point_calibration,
)


class CalibrationCoreTests(unittest.TestCase):
    def test_single_point_calibration_changes_value(self) -> None:
        calibrated = single_point_calibration(-1.0, -2.0, -0.5)
        self.assertIsInstance(calibrated, float)
        self.assertNotEqual(calibrated, -1.0)

    def test_compute_linearity_fit_returns_expected_keys(self) -> None:
        df = pd.DataFrame({"intensity": [10.0, 15.0, 20.0], "value": [1.0, 2.0, 3.0]})
        result = _compute_linearity_fit(df, "value", "intensity")
        self.assertEqual(set(result.keys()), {"slope", "intercept", "r2", "x_ref", "n"})
        self.assertEqual(result["n"], 3)

    def test_identify_outliers_iqr(self) -> None:
        df = pd.DataFrame({"value": [1, 1, 1, 1, 25]})
        mask = identify_outliers_iqr(df, "value", 1.5)
        self.assertTrue(bool(mask.iloc[-1]))


if __name__ == "__main__":
    unittest.main()
