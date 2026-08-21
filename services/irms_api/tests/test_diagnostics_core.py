from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from services.irms_api.domain.diagnostics.core import (
    _fit_saturating_co2_curve,
    create_diagnostic_plots,
    split_diagnostic_plot_grid,
)


def diagnostic_frame() -> pd.DataFrame:
    row_count = 12
    sequence = np.arange(1, row_count + 1, dtype=float)
    return pd.DataFrame(
        {
            "Identifier 1": ["Sample"] * row_count,
            "Identifier 2": [str(index) for index in range(row_count)],
            "Species": ["Test"] * row_count,
            "Date": pd.date_range("2026-01-01", periods=row_count).astype(str),
            "leak_rate": 0.4 * sequence + np.sin(sequence),
            "d 13C/12C  Mean": -2.0 + 0.12 * sequence,
            "p_no_acid": 100.0 + 1.8 * sequence + np.cos(sequence),
            "p_gases": 200.0 + 0.9 * sequence,
            "total_co2": 3.5 * np.square(sequence) + 8.0 * sequence,
            "d 18O/16O  Mean": 1.5 - 0.08 * sequence + 0.03 * np.sin(sequence),
            "Line": np.tile([1.0, 2.0, 3.0], row_count // 3),
            "1  Cycle Int  Samp  44": sequence,
        }
    )


class DiagnosticPlotTests(unittest.TestCase):
    def test_total_co2_fit_is_nonnegative_monotonic_and_saturating(self) -> None:
        intensity = pd.Series([-3.0, 0.0, 1.0, 2.0, 4.0, 8.0, 16.0, 24.0, 30.0])
        total_co2 = pd.Series([900.0, -25.0, 180.0, 320.0, 520.0, 690.0, 770.0, 795.0, -500.0])
        curve = _fit_saturating_co2_curve(intensity, total_co2)

        self.assertIsNotNone(curve)
        x_values, y_values = curve
        self.assertAlmostEqual(float(x_values[0]), 0.0, places=12)
        self.assertAlmostEqual(float(x_values[-1]), 30.0, places=12)
        self.assertAlmostEqual(float(y_values[0]), 0.0, places=12)
        self.assertTrue(np.all(y_values >= 0.0))
        self.assertTrue(np.all(np.diff(y_values) >= -1e-12))
        self.assertLess(float(y_values[-1] - y_values[-2]), float(y_values[1] - y_values[0]))

    def test_total_co2_chart_uses_asymptotic_fit(self) -> None:
        figure = create_diagnostic_plots(
            diagnostic_frame(),
            "Date",
            selected_standards=(),
        )
        grid = {title: chart for _, title, chart in split_diagnostic_plot_grid(figure)}
        chart = grid["Total CO2 vs Initial Sample Intensity"]
        fit_trace = next(
            trace
            for trace in chart.data
            if getattr(trace, "name", None) == "Asymptotic Fit"
        )

        x_values = np.asarray(fit_trace.x, dtype=float)
        y_values = np.asarray(fit_trace.y, dtype=float)

        self.assertAlmostEqual(float(x_values[0]), 0.0, places=12)
        self.assertAlmostEqual(float(y_values[0]), 0.0, places=12)
        self.assertTrue(np.all(y_values >= 0.0))
        self.assertTrue(np.all(np.diff(y_values) >= -1e-12))
        self.assertGreater(len(x_values), 100)

    def test_multivariate_plot_ranks_parameter_contributions(self) -> None:
        figure = create_diagnostic_plots(
            diagnostic_frame(),
            "Date",
            selected_standards=(),
        )
        grid = {title: chart for _, title, chart in split_diagnostic_plot_grid(figure)}
        chart = grid["Parameter Contributions to Variability"]

        self.assertEqual(len(chart.data), 1)
        contribution_trace = chart.data[0]
        self.assertEqual(contribution_trace.type, "bar")
        self.assertEqual(contribution_trace.orientation, "h")

        contributions = np.asarray(contribution_trace.x, dtype=float)
        self.assertAlmostEqual(float(contributions.sum()), 100.0, places=8)
        self.assertTrue(np.all(np.diff(contributions) >= -1e-12))
        self.assertEqual(
            set(contribution_trace.y),
            {
                "Leak rate",
                "d13C/12C mean",
                "P no acid",
                "P gasses",
                "Total CO2",
                "d18O/16O mean",
                "Line",
                "Initial sample intensity",
            },
        )
        self.assertEqual(
            chart.layout.xaxis.title.text,
            "Contribution to Leading Variability (%)",
        )

    def test_multivariate_overview_includes_scree_and_correlation_plots(self) -> None:
        figure = create_diagnostic_plots(
            diagnostic_frame(),
            "Date",
            selected_standards=(),
        )
        grid_items = split_diagnostic_plot_grid(figure)
        self.assertEqual(
            [(group, title) for group, title, _ in grid_items[:3]],
            [
                ("Multivariate Overview", "Parameter Contributions to Variability"),
                ("Multivariate Overview", "Explained Variance by Component"),
                ("Multivariate Overview", "Spearman Correlation Matrix"),
            ],
        )
        grid = {title: chart for _, title, chart in grid_items}

        scree = grid["Explained Variance by Component"]
        self.assertEqual(len(scree.data), 1)
        self.assertEqual(scree.data[0].type, "bar")
        explained = np.asarray(scree.data[0].y, dtype=float)
        self.assertAlmostEqual(float(explained.sum()), 100.0, places=8)
        self.assertTrue(np.all(np.diff(explained) <= 1e-12))
        self.assertEqual(scree.layout.yaxis.title.text, "Explained Variance (%)")
        self.assertIn("Standardized parameters", scree.layout.title.text)

        correlation = grid["Spearman Correlation Matrix"]
        self.assertEqual(len(correlation.data), 1)
        self.assertEqual(correlation.data[0].type, "heatmap")
        matrix = np.asarray(correlation.data[0].z, dtype=float)
        self.assertEqual(matrix.shape, (8, 8))
        np.testing.assert_allclose(matrix, matrix.T, atol=1e-12)
        np.testing.assert_allclose(np.diag(matrix), np.ones(8), atol=1e-12)
        pair_counts = np.asarray(correlation.data[0].customdata, dtype=int)
        self.assertTrue(np.all(pair_counts == len(diagnostic_frame().index)))
        preview = correlation.data[0].meta["correlationScatterPreview"]
        self.assertEqual(preview["kind"], "spearman-scatter-preview")
        self.assertEqual(preview["featureLabels"], list(correlation.data[0].x))
        self.assertEqual(len(preview["values"]), len(diagnostic_frame().index))
        self.assertEqual(len(preview["values"][0]), 8)
        self.assertEqual(preview["rowLabels"], [str(index) for index in diagnostic_frame().index])
        self.assertEqual(preview["colorLabel"], "Date")
        self.assertIn("Pairwise-complete measurements", correlation.layout.title.text)
        self.assertIn("hover a cell", correlation.layout.title.text)


if __name__ == "__main__":
    unittest.main()
