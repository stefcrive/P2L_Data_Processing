from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from services.irms_api.domain.processing.cycles import summarize_cycle_signal_intensities


class CycleSignalIntensityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frame = pd.DataFrame({
            "Identifier 1": ["Sample"], "Identifier 2": ["1"],
            "Excel File": ["run.xlsx"], "Run ID": ["run"],
            "d 13C/12C  Mean": [-1.0],
        }, index=["analysis"])
        self.cycles = pd.DataFrame({
            "Cycle Number": ["pre", "1", "2", "3", "4", "5"],
            "Identifier 1": ["Sample"] * 6, "Identifier 2": ["1"] * 6,
            "Excel File": ["run.xlsx"] * 6, "Run ID": ["run"] * 6,
            "Cycle Intensity Samp 44": [30.0, 50.0, 14.0, 12.0, 8.0, 1.0],
            "Cycle Intensity Ref 44": [30.0, 12.0, 12.0, 12.0, 12.0, 12.0],
        })

    def test_endpoints_and_arithmetic_mean_exclude_pre_saturation_and_gas_escape(self) -> None:
        summary = summarize_cycle_signal_intensities(self.frame, self.cycles)["analysis"]
        self.assertEqual(summary["first_valid"], 14.0)
        self.assertEqual(summary["last_valid"], 8.0)
        self.assertAlmostEqual(summary["average"], 34.0 / 3.0)
        self.assertNotEqual(summary["average"], 12.0)  # The median is not the average.

    def test_signals_are_normalized_to_volts_and_sorted_by_cycle_number(self) -> None:
        cycles = self.cycles.iloc[[0, 4, 3, 2, 1, 5]].copy()
        cycles["Cycle Intensity Samp 44"] *= 1000
        summary = summarize_cycle_signal_intensities(self.frame, cycles)["analysis"]
        self.assertEqual(summary["first_valid"], 14.0)
        self.assertEqual(summary["last_valid"], 8.0)
        self.assertAlmostEqual(summary["average"], 34.0 / 3.0)

    def test_saturation_on_other_collectors_also_excludes_cycle(self) -> None:
        self.cycles["Cycle Intensity Samp 46"] = [1, 1, 49, 1, 1, 1]
        summary = summarize_cycle_signal_intensities(self.frame, self.cycles)["analysis"]
        self.assertEqual(summary, {"first_valid": 12.0, "last_valid": 8.0, "average": 10.0})

    def test_missing_or_all_invalid_cycles_do_not_fall_back_to_imported_intensity(self) -> None:
        self.assertEqual(summarize_cycle_signal_intensities(self.frame, None), {})
        self.cycles["Cycle Intensity Samp 44"] = [30.0, np.nan, np.inf, 50.0, 51.0, 52.0]
        self.assertEqual(summarize_cycle_signal_intensities(self.frame, self.cycles)["analysis"], {
            "first_valid": None, "last_valid": None, "average": None,
        })

    def test_cycles_are_not_borrowed_from_another_acquisition(self) -> None:
        for column, value in (("Excel File", "other.xlsx"), ("Identifier 2", "2"), ("Run ID", "other-run")):
            with self.subTest(column=column):
                frame = self.frame.copy()
                frame[column] = value
                self.assertEqual(summarize_cycle_signal_intensities(frame, self.cycles), {})

    def test_batched_gas_escape_history_and_summaries_stay_with_each_acquisition(self) -> None:
        frame = pd.concat([self.frame, self.frame.rename(index={"analysis": "second"}).assign(**{"Identifier 2": "2"})])
        second = self.cycles.copy()
        second["Identifier 2"] = "2"
        second["Cycle Intensity Samp 44"] = [30, 16, 15, 14, 13, 12]
        cycles = pd.concat([self.cycles, second], ignore_index=True)
        summaries = summarize_cycle_signal_intensities(frame, cycles)
        self.assertEqual(summaries["second"], {"first_valid": 16.0, "last_valid": 12.0, "average": 14.0})
        self.assertEqual(summaries["analysis"]["last_valid"], 8.0)


if __name__ == "__main__":
    unittest.main()
