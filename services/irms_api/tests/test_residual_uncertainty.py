import copy
import unittest

import numpy as np

from services.irms_api.metrology.models import ResidualOverride
from services.irms_api.metrology.residual_preview import _fit_preview, residual_previews, linearity_preview_views
from services.irms_api.metrology.science import anchor_model, corrected_normalize, partial_regression
from services.irms_api.metrology.pipeline import partial_pair_fit


class ResidualUncertaintyTests(unittest.TestCase):
    def test_one_intensity_correction_is_shared_across_signal_plots(self):
        x = np.arange(1., 10.)
        points = self.points(x, -.7-.03*x+np.array([.01, -.01, 0]*3))
        points[-1].update(y=20., excluded_from_fit=True)
        other = [{**p, "x": float((i % 3)-1)} for i, p in enumerate(reversed(points))]
        effects = {"intensity_dependence": {"points": points},
                   "sample_reference_dependence": {"points": other},
                   "pressure_adjusted_dependence": {"points": other[:-1]}}
        diagnostics = {"materials": [{"material_id": "qc", "isotopes": {"d13c": effects}}]}
        original = copy.deepcopy(diagnostics)
        overrides = {"qc:intensity_dependence:d13c": {"enabled": True}}
        previews = residual_previews(diagnostics, overrides)
        views = linearity_preview_views(diagnostics, previews)
        corrected = {p["id"]: p for p in previews["qc:intensity_dependence:d13c"]["points"]}
        self.assertAlmostEqual(views["qc:intensity_dependence:d13c"]["slope"], 0, places=12)
        for effect in ("sample_reference_dependence", "pressure_adjusted_dependence"):
            view = views[f"qc:{effect}:d13c"]
            self.assertEqual(len(view["points"]), len(effects[effect]["points"]))
            self.assertIsNone(view["slope_ci95"])
            self.assertFalse(view["independent_validation"])
            for point, source in zip(view["points"], effects[effect]["points"]):
                self.assertEqual(point["x"], source["x"])
                self.assertEqual(point["y"], corrected[point["id"]]["y"])
                self.assertEqual(point["u_correction"], corrected[point["id"]]["u_correction"])
            self.assertEqual(view["n"], len(view["points"])-1)
        self.assertEqual(diagnostics, original)
        self.assertEqual(linearity_preview_views(diagnostics, {}), {})

    def points(self, x, y):
        return [{"id": str(i), "x": float(xx), "y": float(yy)} for i, (xx, yy) in enumerate(zip(x, y))]

    def test_linear_sign_units_and_reference(self):
        x = np.arange(4., 12.)
        y = -.7 - .025 * (x - 7.)
        points = self.points(x, y)
        original = copy.deepcopy(points)
        result = _fit_preview(points, ResidualOverride(enabled=True, center=7))
        self.assertAlmostEqual(result["model"]["slope"], -.025)
        np.testing.assert_allclose([p["y"] for p in result["points"]], -.7, atol=1e-12)
        self.assertAlmostEqual(result["points"][-1]["adjustment"], .1)
        self.assertEqual(result["model"]["residual_degrees_of_freedom"], 6)
        self.assertIsNone(result["slope_ci95"])
        self.assertFalse(result["independent_validation"])
        self.assertEqual(points, original)

    def test_linear_coefficient_uncertainty_matches_hand_calculation(self):
        x = np.arange(1., 7.)
        y = np.array([1., 2.1, 2.9, 4.2, 4.8, 6.1])
        result = _fit_preview(self.points(x, y), ResidualOverride(enabled=True, center=3))
        slope, intercept = np.polyfit(x, y, 1)
        variance = np.sum((y - intercept - slope*x)**2) / (len(x)-2)
        u_slope = np.sqrt(variance / np.sum((x-x.mean())**2))
        self.assertAlmostEqual(result["model"]["u_slope"], u_slope)
        for point in result["points"]:
            self.assertAlmostEqual(point["u_correction"], abs(point["x"]-3)*u_slope)

    def test_quadratic_covariance_and_degrees_of_freedom(self):
        x = np.arange(3., 15.)
        y = -.6 - .04*x + .003*x*x + np.random.default_rng(4).normal(0, .01, len(x))
        result = _fit_preview(self.points(x, y), ResidualOverride(enabled=True, algorithm="quadratic", center=7))
        design = np.column_stack([np.ones(len(x)), x, x*x])
        beta = np.linalg.lstsq(design, y, rcond=None)[0]
        variance = np.sum((y-design@beta)**2)/(len(x)-3)
        cov = np.linalg.inv(design.T@design)*variance
        np.testing.assert_allclose(result["model"]["coefficient_covariance"], cov[1:, 1:], rtol=1e-9)
        self.assertEqual(result["residual_degrees_of_freedom"], len(x)-3)
        self.assertAlmostEqual(result["residual_standard_error"], np.sqrt(variance))
        g = np.array([x[-1]-7, x[-1]**2-49])
        expected = np.sqrt(g@cov[1:, 1:]@g)
        self.assertAlmostEqual(result["points"][-1]["u_correction"], expected)
        samples = np.random.default_rng(51).multivariate_normal(beta[1:], cov[1:, 1:], 100000)
        self.assertAlmostEqual(np.std(samples@g, ddof=1), expected, delta=expected*.015)
        self.assertAlmostEqual(result["slope"], 0, places=12)

    def test_excluded_points_remain_displayable_but_do_not_fit(self):
        points = self.points(np.arange(1., 9.), np.arange(1., 9.)*.2)
        points[-1].update(y=1000., excluded_from_fit=True)
        result = _fit_preview(points, ResidualOverride(enabled=True))
        self.assertEqual(result["n"], 7)
        self.assertEqual(len(result["points"]), 8)
        self.assertTrue(result["points"][-1]["excluded_from_fit"])
        self.assertTrue(result["points"][-1]["extrapolated"])
        self.assertAlmostEqual(result["model"]["slope"], .2)

    def test_manual_coefficient_does_not_inherit_fitted_uncertainty(self):
        points = self.points(np.arange(1., 8.), np.arange(1., 8.)*.2)
        result = _fit_preview(points, ResidualOverride(enabled=True, slope=.1, center=4, offset=.3))
        self.assertAlmostEqual(result["points"][0]["adjustment"], .6)
        self.assertAlmostEqual(result["slope"], .1)
        self.assertIsNone(result["model"]["coefficient_covariance"])
        self.assertTrue(all(p["u_correction"] is None for p in result["points"]))
        self.assertEqual(result["uncertainty"]["status"], "unavailable_manual_coefficients")

    def test_large_predictor_and_invalid_designs(self):
        x = 1.e9 + np.arange(12.)
        z = x-1.e9
        y = 2 + .03*z + .002*z*z
        result = _fit_preview(self.points(x, y), ResidualOverride(enabled=True, algorithm="quadratic"))
        self.assertLess(np.ptp([p["y"] for p in result["points"]]), 1e-10)
        for points in [[], self.points([1, 1, 1, 1], [1, 2, 3, 4]), self.points([1, 2, 1, 2], [2, 3, 4, 5])]:
            self.assertEqual(_fit_preview(points, ResidualOverride(enabled=True, algorithm="quadratic"))["status"], "insufficient_evidence")

    def test_saved_shared_coefficient_derivative_uses_both_anchors(self):
        model = anchor_model([-12., 3.], [-10., 2.], [.02, .02, .01, .01])
        model["correction"] = {"name": "test", "slope": .03, "u_slope": .001, "center": 6.,
                               "domain": {"low": 2., "high": 12.}, "anchor_predictor_means": [4., 9.]}
        x, predictor = -3., 7.5
        result = corrected_normalize(x, predictor, model, monte_carlo=True, draws=100000)
        def evaluate_delta(dc):
            m1, m2 = [v-dc*(p-6.) for v, p in zip(model["measured"], [4., 9.])]
            corrected_x = x-(.03+dc)*(predictor-6.)
            return -12.+(corrected_x-m1)*15./(m2-m1)
        derivative = (evaluate_delta(1e-6)-evaluate_delta(-1e-6))/2e-6
        self.assertAlmostEqual(result["correction"]["coefficient_sensitivity"], derivative, places=8)
        self.assertAlmostEqual(result["monte_carlo"]["u"], result["u_normalization_and_correction"], delta=.0004)

    def test_pressure_partial_slope_and_uncertainty_match_joint_regression(self):
        rng = np.random.default_rng(14)
        intensity = np.linspace(3, 12, 40)
        pressure = .8*intensity+rng.normal(0, .3, len(intensity))
        y = -.1*intensity+.7*pressure+rng.normal(0, .02, len(intensity))
        fit = partial_regression(pressure.tolist(), y.tolist(), intensity.tolist())
        design = np.column_stack((np.ones(len(y)), intensity, pressure))
        beta = np.linalg.lstsq(design, y, rcond=None)[0]
        variance = np.sum((y-design@beta)**2)/(len(y)-3)
        covariance = np.linalg.inv(design.T@design)*variance
        self.assertAlmostEqual(fit["slope"], beta[2])
        self.assertAlmostEqual(fit["slope_se"], np.sqrt(covariance[2, 2]))
        self.assertEqual(fit["residual_degrees_of_freedom"], 37)
        # Residualizing only y, as before, severely attenuated this effect.
        trend = np.polyval(np.polyfit(intensity, y, 1), intensity)
        old_slope = np.polyfit(pressure, y-trend, 1)[0]
        self.assertLess(abs(old_slope), abs(fit["slope"])*.1)
        self.assertEqual(partial_regression(intensity.tolist(), y.tolist(), intensity.tolist())["status"], "insufficient_evidence")
        rows = [{"id": str(i), "pressure": p, "intensity": x, "y": yy} for i, (p, x, yy) in enumerate(zip(pressure, intensity, y))]
        result = partial_pair_fit(rows, "pressure", "y", "intensity", {"39"})
        self.assertEqual(result["n"], 39)
        self.assertEqual(len(result["points"]), 40)
        self.assertTrue(result["points"][-1]["excluded_from_fit"])
        preview = _fit_preview(result["points"], ResidualOverride(enabled=True))
        self.assertEqual(preview["residual_degrees_of_freedom"], 36)
        self.assertAlmostEqual(preview["model"]["slope"], result["slope"])


if __name__ == "__main__":
    unittest.main()
