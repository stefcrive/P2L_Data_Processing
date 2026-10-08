import unittest

import numpy as np

from services.irms_api.metrology.pressure_correction import fit_pressure_failed_qc


class PressureCorrectionTests(unittest.TestCase):
    def points(self):
        return [{"id": f"{i}-{j}", "x": float(p), "intensity": float(signal),
                 "y": -1.+.2*p-.04*signal+.002*(-1)**(i+j)}
                for i, p in enumerate([-5, -3, -1, 1, 3])
                for j, signal in enumerate([4, 8, 12, 16])]

    def test_joint_model_recovers_both_effects_and_predicts_unseen_qc(self):
        points = self.points()
        fit = fit_pressure_failed_qc(points, 3, 6.)
        model = fit["model"]
        self.assertEqual(fit["n"], 20)
        self.assertAlmostEqual(model["slope"], .2, places=3)
        self.assertAlmostEqual(model["intensity_slope"], -.04, places=3)
        self.assertAlmostEqual(fit["intensity_after"]["slope"], 0., places=12)
        self.assertLess(fit["after"]["sd"], .003)
        # This point was not in the coefficient-estimation population.
        pressure, intensity = -2., 10.
        observed = -1.+.2*pressure-.04*intensity
        corrected = observed-model["slope"]*pressure-model["intensity_slope"]*(intensity-6.)
        self.assertAlmostEqual(corrected, -1.-.04*6., delta=.001)
        covariance = np.array(model["coefficient_covariance"])
        design = np.array([[1., p["x"], p["intensity"]] for p in points])
        y = np.array([p["y"] for p in points])
        beta = np.linalg.lstsq(design, y, rcond=None)[0]
        residual = y-design@beta
        expected = np.linalg.inv(design.T@design)[1:, 1:]*(residual@residual)/(len(points)-3)
        np.testing.assert_allclose(covariance, expected, rtol=1e-9, atol=1e-15)

    def test_extreme_qc_is_recorded_and_does_not_define_the_trend(self):
        points = self.points()+[{"id": "bad", "x": -2., "intensity": 10., "y": 30.}]
        fit = fit_pressure_failed_qc(points, 3, 6.)
        self.assertEqual(fit["fit_excluded_ids"], ["bad"])
        self.assertEqual(len(fit["points"]), 21)
        self.assertEqual(fit["n"], 20)
        self.assertAlmostEqual(fit["model"]["slope"], .2, places=3)
        self.assertAlmostEqual(fit["model"]["intensity_slope"], -.04, places=3)

    def test_insufficient_or_collinear_predictors_cannot_define_joint_correction(self):
        self.assertIsNone(fit_pressure_failed_qc(self.points()[:3], 3, 6.))
        self.assertIsNone(fit_pressure_failed_qc([{**p, "intensity": 2*p["x"]+20} for p in self.points()], 3, 6.))
        self.assertIsNone(fit_pressure_failed_qc([{**p, "x": 0.} for p in self.points()], 3, 6.))


if __name__ == "__main__":
    unittest.main()
