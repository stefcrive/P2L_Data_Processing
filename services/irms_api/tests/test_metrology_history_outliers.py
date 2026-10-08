import unittest
from services.irms_api.metrology.science import control_summary


class HistoryOutlierTests(unittest.TestCase):
    def test_pressure_range_warning_is_not_an_acquisition_failure(self):
        from services.irms_api.metrology.qc_screening import failure_category, qc_review_flags
        issue = "pressure_mismatch_v missing or outside validated range"
        row = {"id": "q", "run_id": "r", "role": "qc", "issues": [issue], "pressure_mismatch_v": .2}
        self.assertIsNone(failure_category(row, "d13c"))
        flags = qc_review_flags([row])
        self.assertTrue(all(f["reasons"] == [issue] for f in flags))
        self.assertTrue(all(f["category"] == "range" for f in flags))
        self.assertEqual(failure_category({**row, "pressure_failed": True}, "d13c"), "pressure_adjustment")
        for pressure in (None, float("nan"), float("inf")):
            self.assertIsNone(failure_category({**row, "pressure_mismatch_v": pressure}, "d13c"))
        self.assertIsNone(failure_category({**row, "issues": []}, "d13c"))
        self.assertEqual(failure_category({**row, "issues": [issue, "Qtegra reports an acquisition failure"]}, "d13c"), "no_signal")

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
        self.assertEqual(len([f for f in flags if f["category"]=="no_signal"]),2)
        self.assertFalse(any(f["measurement_id"]=="sample" for f in flags))

    def test_missing_metadata_does_not_hide_available_qc_measurements(self):
        from services.irms_api.metrology.qc_screening import qc_review_flags
        base = {"id": "qc", "run_id": "run", "role": "qc", "mass_ug": None, "pressure_mismatch_v": None,
                "isotopes": {"d13c": {"value": -.7}, "d18o": {"value": -6}},
                "issues": ["mass_ug missing or outside validated range", "pressure_mismatch_v missing or outside validated range"]}
        flags = qc_review_flags([base])
        self.assertEqual(len(flags), 2)
        self.assertTrue(all(flag["metadata_only"] for flag in flags))
        self.assertTrue(all(flag["reasons"] == base["issues"] for flag in flags))
        actual_range = {**base, "i44_v": 90, "issues": [*base["issues"], "i44_v missing or outside validated range"]}
        self.assertTrue(all(not flag["metadata_only"] for flag in qc_review_flags([actual_range])))
        failed = {**base, "issues": [*base["issues"], "Qtegra reports an acquisition failure"]}
        self.assertTrue(all(not flag["metadata_only"] for flag in qc_review_flags([failed]) if flag["category"] == "no_signal"))

    def test_pressure_toggle_is_separate_from_no_signal_and_statistical_screening(self):
        from services.irms_api.metrology.qc_screening import detect_qc_outliers, failure_category
        rows = [{"id": str(i), "run_id": "run", "role": "qc", "material_id": "qc",
                 "d13c": value, "d18o": value} for i, value in enumerate([0., .1, -.1, 0., 0., 2.])]
        rows += [{**rows[0], "id": "pressure", "d13c": 1000., "d18o": 1000., "pressure_failed": True},
                 {**rows[0], "id": "missing", "d13c": None, "d18o": None, "pressure_failed": True},
                 {**rows[0], "id": "aborted", "issues": ["Qtegra reports an acquisition failure"], "pressure_failed": True},
                 {**rows[0], "id": "unknown-pressure", "role": "unknown", "pressure_failed": True}]
        self.assertEqual(failure_category(rows[-3], "d13c"), "no_signal")
        for method, threshold in (("sigma", 1.5), ("iqr", 1.5)):
            with self.subTest(method=method):
                default = detect_qc_outliers(rows, method, threshold)
                enabled = detect_qc_outliers(rows, method, threshold, True)
                stats = lambda result: [f for f in result["flags"] if f["category"] == "statistical"]
                self.assertEqual(stats(default), stats(enabled))
                self.assertEqual({f["measurement_id"] for f in stats(default)}, {"5"})
                self.assertTrue(all(f["population_n"] == 6 for f in stats(default)))
                self.assertEqual({f["measurement_id"] for f in default["flags"] if f["category"] == "no_signal"}, {"missing", "aborted"})
                self.assertFalse(any(f["category"] == "pressure_adjustment" for f in default["flags"]))
                self.assertEqual({f["measurement_id"] for f in enabled["flags"] if f["category"] == "pressure_adjustment"}, {"pressure", "unknown-pressure"})

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
