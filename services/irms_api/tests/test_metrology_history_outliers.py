import unittest
from services.irms_api.metrology.science import control_summary


class HistoryOutlierTests(unittest.TestCase):
    def test_review_categories_use_recorded_issues_and_keep_isotopes_independent(self):
        from services.irms_api.metrology.qc_screening import qc_review_flags
        rows = [{"id":"q", "run_id":"run", "role":"qc", "issues":["d13c internal SD missing or at/above limit", "Acquisition completion needs confirmation"],
                 "isotopes":{"d13c":{"value":1}, "d18o":{"value":2}}},
                {"id":"excluded", "run_id":"run", "role":"qc", "excluded":True},
                {"id":"failed", "run_id":"run", "role":"qc", "issues":["Qtegra reports an acquisition failure"]},
                {"id":"sample", "run_id":"run", "role":"unknown", "excluded":True}]
        flags = qc_review_flags(rows)
        self.assertEqual([(f["category"], f["isotope"]) for f in flags if f["measurement_id"]=="q"], [("range","d13c")])
        self.assertEqual(len([f for f in flags if f["category"]=="manual"]),2)
        self.assertEqual(len([f for f in flags if f["category"]=="failed"]),2)
        self.assertFalse(any(f["measurement_id"]=="sample" for f in flags))

    def test_individual_outlier_removed_from_statistics_but_not_points_or_flags(self):
        points = [{"id": str(i), "value": v} for i, v in enumerate([-.1, 0, .1, 9])]
        actual = control_summary(points, 0, .1, exclude_outliers=True)
        self.assertEqual(actual["points"], points)
        self.assertEqual(actual["n"], 3)
        self.assertEqual(actual["total_n"], 4)
        self.assertEqual(actual["outlier_ids"], ["3"])
        self.assertAlmostEqual(actual["sd"], .1)
        self.assertEqual(actual["status"], "out_of_control")
        self.assertAlmostEqual(actual["limits"][1], .3)
        self.assertEqual(control_summary(points, 0, .1)["n"], 4)

    def test_run_and_trend_signals_are_retained(self):
        points = [{"id": str(i), "value": .01*i+.01} for i in range(8)]
        actual = control_summary(points, 0, 1, exclude_outliers=True)
        self.assertEqual(actual["n"], 8)
        self.assertTrue(actual["flags"])
        self.assertEqual(actual["outlier_ids"], [])

    def test_missing_limits_and_insufficient_retained_data(self):
        points = [{"id": "a", "value": 7}, {"id": "b", "value": 8}]
        self.assertEqual(control_summary(points, None, None, exclude_outliers=True)["n"], 2)
        actual = control_summary(points, 0, .1, exclude_outliers=True)
        self.assertEqual(actual["n"], 0)
        self.assertIsNone(actual["sd"])
        self.assertEqual(len(actual["points"]), 2)
