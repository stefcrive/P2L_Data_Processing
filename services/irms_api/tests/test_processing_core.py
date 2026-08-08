from __future__ import annotations

import base64
import unittest

import numpy as np
import pandas as pd

from services.irms_api.domain.constants import (
    CYCLE1_SIGNAL_DIFF44_COL,
    CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL,
    CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL,
    CYCLE1_SIGNAL_REF44_COL,
    CYCLE1_SIGNAL_SAMP44_COL,
    VALID_CYCLES_COL,
)
from services.irms_api.domain.contracts import EditAction
from services.irms_api.domain.processing.core import (
    RangeConfig,
    _interpolate_outliers_by_identifier2,
    _range_outlier_mask,
    build_category_masks,
)
from services.irms_api.domain.processing.cycles import (
    apply_run_level_linearity_basis_from_cycles,
    build_cycle_diagnostics_payload,
    build_target_info,
    get_cycles_for_selected_point,
)
from services.irms_api.domain.processing.edits import (
    _interpolate_single_target_within_identifier_group,
    apply_edit_action,
)
from services.irms_api.domain.processing.workspace import (
    _derive_working_frame,
    build_processing_workspace,
    normalize_processing_config,
)


def sample_processing_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Identifier 1": ["SampleA", "SampleA", "SampleA", "SampleB"],
            "Identifier 2": ["1", "2", "3", "1"],
            "Species": ["Coral", "Coral", "Coral", "Shell"],
            "Label": ["SampleA - Coral", "SampleA - Coral", "SampleA - Coral", "SampleB - Shell"],
            "Comment": ["ok", "range", "partial", "failed"],
            "d 13C/12C  Mean": [1.0, 50.0, 2.0, 3.0],
            "d 18O/16O  Mean": [2.0, 2.2, 2.4, np.nan],
            "d 13C/12C  Std Dev": [0.10, 0.10, 0.15, 0.20],
            "d 18O/16O  Std Dev": [0.20, 0.20, 0.25, np.nan],
            "d13C_calibrated": [1.5, 50.5, 2.5, 3.5],
            "d18O_calibrated": [2.5, 2.7, 2.9, np.nan],
            "d13C_calibrated_linearity_corrected": [1.4, 50.4, 2.4, 3.4],
            "d18O_calibrated_linearity_corrected": [2.4, 2.6, 2.8, np.nan],
            "1  Cycle Int  Samp  44": [15.0, 15.5, 16.0, 12.0],
            "1  Cycle Int  Ref  44": [10.0, 10.0, 10.0, 10.0],
            "1  Cycle Int  Diff Samp-Ref  44": [5.0, 5.5, 6.0, 2.0],
            "leak_rate": [5.0, 5.0, 5.0, 5.0],
            "Collector Status": ["", "", "Partially Saturated Collectors", "Failed Sample"],
            "d13C Cycles Excluded": [0, 0, 1, 0],
            "d18O Cycles Excluded": [0, 0, 0, 0],
            "d13C Cycles Used": [8, 8, 7, 0],
            "d18O Cycles Used": [8, 8, 6, 0],
            "Line": [1, 1, 1, 2],
            "Run ID": ["run-1", "run-1", "run-1", "run-2"],
            "Excel File": ["run1.xlsx", "run1.xlsx", "run1.xlsx", "run2.xlsx"],
            "Date": ["2025-01-01", "2025-01-01", "2025-01-01", "2025-01-02"],
            "Date_ordinal": [739252, 739252, 739252, 739253],
        }
    )


def sample_cycles_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Cycle Number": ["pre", "1", "2"],
            "Identifier 1": ["SampleA", "SampleA", "SampleA"],
            "Identifier 2": ["1", "1", "1"],
            "Species": ["Coral", "Coral", "Coral"],
            "Excel File": ["run1.xlsx", "run1.xlsx", "run1.xlsx"],
            "Run ID": ["run-1", "run-1", "run-1"],
            "Date": ["2025-01-01", "2025-01-01", "2025-01-01"],
            "d 13C/12C  Mean": [1.0, 0.9, 1.1],
            "d 18O/16O  Mean": [2.0, 2.1, 2.2],
            "Cycle Intensity Samp 44": [15.0, 15.1, 14.9],
            "Cycle Intensity Ref 44": [10.0, 10.1, 10.0],
            "Cycle Intensity Samp 45": [0.5, 0.52, 0.48],
            "Cycle Intensity Ref 45": [0.4, 0.41, 0.39],
            "Cycle Intensity Samp 46": [0.3, 0.31, 0.29],
            "Cycle Intensity Ref 46": [0.2, 0.21, 0.19],
        }
    )


class ProcessingCoreTests(unittest.TestCase):
    def test_cycle_lookup_does_not_borrow_cycles_from_another_workbook(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["ResultOnly"],
                "Identifier 2": ["41"],
                "Excel File": ["result-only.xls"],
                "d 13C/12C  Mean": [-34.291],
                "d 18O/16O  Mean": [-14.274],
            }
        )
        cycles = pd.DataFrame(
            {
                "Cycle Number": ["Pre", "Cycle 1", "Cycle 2"],
                "Identifier 1": ["OtherSample"] * 3,
                "Identifier 2": ["1"] * 3,
                "Excel File": ["cycles.xls"] * 3,
                "d 13C/12C  Mean": [-13.7] * 3,
                "d 18O/16O  Mean": [-9.3] * 3,
            }
        )

        d13_cycles, d13_pre = get_cycles_for_selected_point(df, cycles, 0, "d 13C/12C  Mean")
        d18_cycles, d18_pre = get_cycles_for_selected_point(df, cycles, 0, "d 18O/16O  Mean")

        self.assertIsNone(d13_cycles)
        self.assertIsNone(d13_pre)
        self.assertIsNone(d18_cycles)
        self.assertIsNone(d18_pre)

    def test_range_outlier_mask(self) -> None:
        df = pd.DataFrame(
            {
                "d 13C/12C  Mean": [0.0, 20.0],
                "d 18O/16O  Mean": [0.0, 0.0],
                "1  Cycle Int  Samp  44": [10.0, 10.0],
                "leak_rate": [5.0, 5.0],
            }
        )
        mask = _range_outlier_mask(df, RangeConfig(d13c_range=(-10.0, 10.0)))
        self.assertFalse(bool(mask.iloc[0]))
        self.assertTrue(bool(mask.iloc[1]))

    def test_manual_override_and_edited_rows_affect_masks(self) -> None:
        df = sample_processing_df()
        edit_state = {
            "edited_rows": ["0"],
            "original_delta_values": {"d13C|0": 1.0},
            "manual_outlier_overrides": {"1": False, "3": True},
        }
        masks = build_category_masks(df, RangeConfig(), edit_state=edit_state, sigma_level=1.0)

        self.assertFalse(bool(masks["d13C Range"].loc[1]))
        self.assertFalse(bool(masks["Statistical"].loc[0]))
        self.assertTrue(bool(masks["Manual Override"].loc[3]))

    def test_interpolate_outliers_by_identifier2(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 2": [1, 2, 3],
                "d 13C/12C  Mean": [1.0, 10.0, 3.0],
            }
        )
        mask = pd.Series([False, True, False])
        result = _interpolate_outliers_by_identifier2(df, mask, ["d 13C/12C  Mean"])
        self.assertAlmostEqual(float(result.iloc[1]["d 13C/12C  Mean"]), 2.0)

    def test_interpolate_outliers_by_identifier2_uses_identifier_spacing(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 2": [10, 20, 40],
                "d 13C/12C  Mean": [1.0, 99.0, 5.0],
            }
        )
        mask = pd.Series([False, True, False])
        result = _interpolate_outliers_by_identifier2(df, mask, ["d 13C/12C  Mean"])
        # True linear interpolation at x=20 between x=10 (1.0) and x=40 (5.0): 1 + (10/30)*4.
        self.assertAlmostEqual(float(result.iloc[1]["d 13C/12C  Mean"]), 2.3333333333, places=6)

    def test_interpolate_single_target_uses_identifier_spacing(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A"],
                "Identifier 2": [10, 20, 40],
                "d 13C/12C  Mean": [1.0, 99.0, 5.0],
            }
        )
        interpolated = _interpolate_single_target_within_identifier_group(df, row_label=1, col="d 13C/12C  Mean")
        self.assertIsNotNone(interpolated)
        # True linear interpolation at x=20 between x=10 (1.0) and x=40 (5.0): 1 + (10/30)*4.
        self.assertAlmostEqual(float(interpolated), 2.3333333333, places=6)

    def test_interpolate_single_target_prefers_distinct_identifier2_neighbors(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A", "A", "A"],
                "Identifier 2": [20, 21, 22, 22, 23],
                "d 13C/12C  Mean": [0.5, 2.3, 2.2, np.nan, -0.2],
            }
        )
        interpolated = _interpolate_single_target_within_identifier_group(df, row_label=3, col="d 13C/12C  Mean")
        self.assertIsNotNone(interpolated)
        # Use x=21 and x=23 as neighbors (not the duplicate x=22 value), so midpoint is (2.3 + -0.2)/2 = 1.05.
        self.assertAlmostEqual(float(interpolated), 1.05, places=6)

    def test_interpolate_single_target_does_not_fallback_to_row_position_when_identifier2_missing(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A"],
                "Identifier 2": [10, "bad-token", 40],
                "d 13C/12C  Mean": [1.0, np.nan, 5.0],
            }
        )
        interpolated = _interpolate_single_target_within_identifier_group(df, row_label=1, col="d 13C/12C  Mean")
        # Identifier 2 is not numeric for the target row; do not use row-index spacing fallback.
        self.assertIsNone(interpolated)

    def test_apply_edit_action_interpolate_uses_identifier_spacing(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A"],
                "Identifier 2": [10, 20, 40],
                "d 13C/12C  Mean": [1.0, 99.0, 5.0],
            }
        )
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}

        updated_df, updated_state = apply_edit_action(
            df,
            edit_state,
            EditAction(action="interpolate", targets=[{"row_label": "1", "isotope_key": "d13C"}]),
        )

        self.assertAlmostEqual(float(updated_df.loc[1, "d 13C/12C  Mean"]), 2.3333333333, places=6)
        self.assertIn("1", updated_state["edited_rows"])
        self.assertEqual(updated_state["original_delta_values"]["d13C|1"], 99.0)

    def test_apply_edit_action_interpolate_applies_offset_after_linear_value(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A"],
                "Identifier 2": [10, 20, 40],
                "d 13C/12C  Mean": [1.0, 99.0, 5.0],
            }
        )
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}

        updated_df, updated_state = apply_edit_action(
            df,
            edit_state,
            EditAction(action="interpolate", targets=[{"row_label": "1", "isotope_key": "d13C"}], offset=0.25),
        )

        # linear(1.0, 5.0 @ x=10,40 to x=20) + 0.25
        self.assertAlmostEqual(float(updated_df.loc[1, "d 13C/12C  Mean"]), 2.5833333333, places=6)
        self.assertIn("1", updated_state["edited_rows"])
        self.assertEqual(updated_state["original_delta_values"]["d13C|1"], 99.0)

    def test_apply_edit_action_interpolate_marks_restored_for_both_isotopes(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A"],
                "Identifier 2": [1, 2, 3],
                "Collector Status": ["", "Failed Sample", ""],
                "d 13C/12C  Mean": [1.0, np.nan, 5.0],
                "d 18O/16O  Mean": [2.0, np.nan, 6.0],
                "d13C_calibrated": [1.0, np.nan, 5.0],
                "d18O_calibrated": [2.0, np.nan, 6.0],
                "d 13C/12C  Std Dev": [0.10, np.nan, 0.20],
                "d 18O/16O  Std Dev": [0.15, np.nan, 0.25],
            }
        )
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}

        _, updated_state = apply_edit_action(
            df,
            edit_state,
            EditAction(
                action="interpolate",
                targets=[
                    {"row_label": "1", "isotope_key": "d13C"},
                    {"row_label": "1", "isotope_key": "d18O"},
                ],
            ),
        )

        restored_tokens = set(updated_state.get("restored_delta_tokens", []))
        self.assertIn("d13C|1", restored_tokens)
        self.assertIn("d18O|1", restored_tokens)

    def test_apply_edit_action_interpolate_assigns_stdev_and_reset_all_reverts(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A"],
                "Identifier 2": [1, 2, 3],
                "Collector Status": ["", "Failed Sample", ""],
                "d 13C/12C  Mean": [1.0, np.nan, 5.0],
                "d 18O/16O  Mean": [2.0, np.nan, 6.0],
                "d13C_calibrated": [1.0, np.nan, 5.0],
                "d18O_calibrated": [2.0, np.nan, 6.0],
                "d 13C/12C  Std Dev": [0.10, np.nan, 0.20],
                "d 18O/16O  Std Dev": [0.15, np.nan, 0.25],
            }
        )
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}

        updated_df, updated_state = apply_edit_action(
            df,
            edit_state,
            EditAction(
                action="interpolate",
                targets=[
                    {"row_label": "1", "isotope_key": "d13C"},
                    {"row_label": "1", "isotope_key": "d18O"},
                ],
                stdev=0.123,
            ),
        )
        self.assertAlmostEqual(float(updated_df.loc[1, "d 13C/12C  Std Dev"]), 0.123, places=6)
        self.assertAlmostEqual(float(updated_df.loc[1, "d 18O/16O  Std Dev"]), 0.123, places=6)

        reset_df, _ = apply_edit_action(updated_df, updated_state, EditAction(action="reset_all", targets=[]))
        self.assertTrue(pd.isna(reset_df.loc[1, "d 13C/12C  Std Dev"]))
        self.assertTrue(pd.isna(reset_df.loc[1, "d 18O/16O  Std Dev"]))

    def test_apply_edit_action_interpolate_uses_interpolation_source_domain(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A"],
                "Identifier 2": [1, 2, 3],
                "d 13C/12C  Mean": [1.0, 99.0, 5.0],
            }
        )
        display_source = df.copy()
        # Simulate a non-uniform display transform (for example, linearly corrected/overridden chart domain).
        display_source["d 13C/12C  Mean"] = [1.0, 98.0, 1.0]
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}

        updated_df, _ = apply_edit_action(
            df,
            edit_state,
            EditAction(action="interpolate", targets=[{"row_label": "1", "isotope_key": "d13C"}]),
            interpolation_source_df=display_source,
        )

        # Interpolation is computed from the source-domain values and mapped back to persisted raw.
        self.assertAlmostEqual(float(updated_df.loc[1, "d 13C/12C  Mean"]), 2.0, places=6)

    def test_apply_edit_action_interpolate_missing_target_interpolates_source_to_raw_offset(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A"],
                "Identifier 2": [1, 2, 3],
                "Collector Status": ["", "Failed Sample", ""],
                "d 13C/12C  Mean": [10.0, np.nan, 30.0],
                "d 18O/16O  Mean": [2.0, np.nan, 6.0],
            }
        )
        display_source = df.copy()
        # Simulate processing/display-domain values where source->raw offsets differ by row.
        display_source["d 13C/12C  Mean"] = [12.0, np.nan, 38.0]
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}

        updated_df, _ = apply_edit_action(
            df,
            edit_state,
            EditAction(action="interpolate", targets=[{"row_label": "1", "isotope_key": "d13C"}]),
            interpolation_source_df=display_source,
        )

        # Source-domain interpolation at Identifier2=2 is 25.0.
        # Interpolated source->raw offset at the target is 5.0, so persisted raw should be 20.0.
        self.assertAlmostEqual(float(updated_df.loc[1, "d 13C/12C  Mean"]), 20.0, places=6)

    def test_apply_edit_action_interpolate_does_not_change_other_isotope(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A"],
                "Identifier 2": [1, 2, 3],
                "d 13C/12C  Mean": [1.0, 99.0, 5.0],
                "d 18O/16O  Mean": [2.0, 50.0, 6.0],
                "d18O_calibrated": [2.1, 50.1, 6.1],
                "d 18O/16O  Std Dev": [0.10, 0.20, 0.30],
            }
        )
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}

        updated_df, _ = apply_edit_action(
            df,
            edit_state,
            EditAction(action="interpolate", targets=[{"row_label": "1", "isotope_key": "d13C"}]),
        )

        self.assertAlmostEqual(float(updated_df.loc[1, "d 13C/12C  Mean"]), 3.0, places=6)
        self.assertAlmostEqual(float(updated_df.loc[1, "d 18O/16O  Mean"]), 50.0, places=6)
        self.assertAlmostEqual(float(updated_df.loc[1, "d18O_calibrated"]), 50.1, places=6)
        self.assertAlmostEqual(float(updated_df.loc[1, "d 18O/16O  Std Dev"]), 0.20, places=6)

    def test_reset_all_restores_failed_sample_nan_values_after_interpolation(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A"],
                "Identifier 2": [1, 2, 3],
                "Collector Status": ["", "Failed Sample", ""],
                "d 13C/12C  Mean": [1.0, np.nan, 5.0],
                "d 18O/16O  Mean": [2.0, np.nan, 6.0],
                "d13C_calibrated": [1.0, np.nan, 5.0],
                "d18O_calibrated": [2.0, np.nan, 6.0],
            }
        )
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}

        interpolated_df, interpolated_state = apply_edit_action(
            df,
            edit_state,
            EditAction(
                action="interpolate",
                targets=[
                    {"row_label": "1", "isotope_key": "d13C"},
                    {"row_label": "1", "isotope_key": "d18O"},
                ],
            ),
        )
        self.assertTrue(np.isfinite(float(interpolated_df.loc[1, "d 13C/12C  Mean"])))
        self.assertTrue(np.isfinite(float(interpolated_df.loc[1, "d 18O/16O  Mean"])))
        self.assertIn("d13C|1", set(interpolated_state.get("original_missing_delta_tokens", [])))
        self.assertIn("d18O|1", set(interpolated_state.get("original_missing_delta_tokens", [])))

        reset_df, reset_state = apply_edit_action(
            interpolated_df,
            interpolated_state,
            EditAction(action="reset_all", targets=[]),
        )
        self.assertTrue(pd.isna(reset_df.loc[1, "d 13C/12C  Mean"]))
        self.assertTrue(pd.isna(reset_df.loc[1, "d 18O/16O  Mean"]))
        self.assertEqual(set(reset_state.get("original_missing_delta_tokens", [])), set())

    def test_interpolated_failed_samples_are_marked_as_restored_in_charts(self) -> None:
        df = sample_processing_df().copy()
        df.loc[3, "d 13C/12C  Mean"] = np.nan
        df.loc[3, "d 18O/16O  Mean"] = np.nan
        df.loc[3, "Collector Status"] = "Failed Sample"

        metadata = {
            "processing": {"config": normalize_processing_config({}).model_dump()},
            "edit_state": {
                "edited_rows": ["3"],
                "original_delta_values": {},
                "original_missing_delta_tokens": ["d13C|3", "d18O|3"],
                "manual_outlier_overrides": {},
                "restored_delta_tokens": ["d13C|3", "d18O|3"],
            },
            "calibration": {"selected_standards": []},
        }

        restored_df = df.copy()
        restored_df.loc[3, "d 13C/12C  Mean"] = 2.2
        restored_df.loc[3, "d 18O/16O  Mean"] = 1.7

        workspace = build_processing_workspace("session-1", restored_df, sample_cycles_df(), metadata)
        overview_trace_names = [str(trace.get("name", "")) for trace in (workspace.overview_figures.get("crossplot", {}).get("data", []) or [])]
        self.assertIn("Restored Samples", overview_trace_names)
        processing_3d_data = workspace.overview_figures.get("processing_3d", {}).get("data", []) or []
        processing_3d_restored = next((trace for trace in processing_3d_data if str(trace.get("name", "")) == "Restored Samples"), None)
        self.assertIsNotNone(processing_3d_restored)
        self.assertEqual(str((processing_3d_restored or {}).get("mode", "")), "text")
        self.assertEqual(str(((processing_3d_restored or {}).get("text") or [""])[0]), "*")

        shell_section = next((section for section in workspace.species_sections if section.species == "Shell"), None)
        self.assertIsNotNone(shell_section)
        figure_set = next((item for item in (shell_section.identifier_figures if shell_section else []) if item.identifier == "SampleB"), None)
        self.assertIsNotNone(figure_set)
        identifier_trace_names = [str(trace.get("name", "")) for trace in (figure_set.d13c.get("data", []) if figure_set else [])]
        self.assertIn("Restored Samples", identifier_trace_names)
        self.assertEqual(identifier_trace_names.count("Restored Samples"), 1)
        self.assertNotIn("Edited Samples", identifier_trace_names)
        self.assertNotIn("Partially Failed (Recovered Mean)", identifier_trace_names)
        self.assertNotIn("Failed Samples (Interpolated)", identifier_trace_names)

    def test_stale_restored_tokens_without_original_missing_do_not_render_restored_markers(self) -> None:
        metadata = {
            "processing": {"config": normalize_processing_config({}).model_dump()},
            "edit_state": {
                "edited_rows": [],
                "original_delta_values": {},
                "original_missing_delta_tokens": [],
                "manual_outlier_overrides": {},
                "restored_delta_tokens": ["d13C|3", "d18O|3"],
            },
            "calibration": {"selected_standards": []},
        }
        workspace = build_processing_workspace("session-1", sample_processing_df(), sample_cycles_df(), metadata)

        overview_trace_names = [str(trace.get("name", "")) for trace in (workspace.overview_figures.get("crossplot", {}).get("data", []) or [])]
        self.assertNotIn("Restored Samples", overview_trace_names)

        shell_section = next((section for section in workspace.species_sections if section.species == "Shell"), None)
        self.assertIsNotNone(shell_section)
        figure_set = next((item for item in (shell_section.identifier_figures if shell_section else []) if item.identifier == "SampleB"), None)
        self.assertIsNotNone(figure_set)
        d13_trace_names = [str(trace.get("name", "")) for trace in (figure_set.d13c.get("data", []) if figure_set else [])]
        d18_trace_names = [str(trace.get("name", "")) for trace in (figure_set.d18o.get("data", []) if figure_set else [])]
        self.assertNotIn("Restored Samples", d13_trace_names)
        self.assertNotIn("Restored Samples", d18_trace_names)

    def test_restored_rows_remain_visible_when_statistical_outliers_are_not_active_for_them(self) -> None:
        df = sample_processing_df().copy()
        df["Identifier 1"] = "SampleA"
        df["Species"] = "Coral"
        df["Identifier 2"] = ["1", "2", "3", "4"]
        df["Collector Status"] = ""
        df["d 13C/12C  Mean"] = [1.0, 1.0, 1.0, 100.0]
        df["d 18O/16O  Mean"] = [2.0, 2.0, 2.0, 2.0]
        df["d13C_calibrated"] = [1.5, 1.5, 1.5, 100.5]
        df["d18O_calibrated"] = [2.5, 2.5, 2.5, 2.5]
        df["d13C_calibrated_linearity_corrected"] = [1.4, 1.4, 1.4, 100.4]
        df["d18O_calibrated_linearity_corrected"] = [2.4, 2.4, 2.4, 2.4]

        metadata = {
            "processing": {
                "config": {
                    "selected_identifier": "SampleA",
                    "x_axis_option": "By Identifier 2",
                    "color_param": "Date_ordinal",
                    "z_axis": "1  Cycle Int  Samp  44",
                    "signal_range": [0.0, 50.0],
                    "leak_range": [0.0, 1000.0],
                    "d13c_range": [-200.0, 200.0],
                    "d18o_range": [-200.0, 200.0],
                    "sigma_level_data": 1.0,
                    "overlays": {
                        "show_statistical_outliers": True,
                        "show_range_outliers": False,
                        "show_manual_outliers": False,
                        "show_saturated_collectors": False,
                        "show_saturated_samples": False,
                        "show_failed_samples": False,
                    },
                    "manual_linearity_override": {
                        "enabled": False,
                        "d13_per_10v": 0.0,
                        "d18_per_10v": 0.0,
                    },
                    "export": {
                        "include_outliers": False,
                        "selected_ids": ["All"],
                        "interpolate_outliers": False,
                        "client_name": None,
                        "comment_map": {},
                    },
                }
            },
            "edit_state": {
                "edited_rows": ["3"],
                "original_delta_values": {},
                "original_missing_delta_tokens": ["d13C|3", "d18O|3"],
                "manual_outlier_overrides": {},
                "restored_delta_tokens": ["d13C|3", "d18O|3"],
            },
            "calibration": {"selected_standards": []},
        }

        workspace = build_processing_workspace("session-1", df, sample_cycles_df(), metadata)

        overview_raw = next(
            (
                trace
                for trace in (workspace.overview_figures.get("d13_summary", {}).get("data", []) or [])
                if str(trace.get("name", "")).startswith("Raw d13C")
            ),
            None,
        )
        self.assertIsNotNone(overview_raw)
        overview_raw_ids = [
            str(item[3])
            for item in ((overview_raw or {}).get("customdata") or [])
            if isinstance(item, (list, tuple)) and len(item) > 3
        ]
        self.assertIn("4", overview_raw_ids)

        shell_section = next((section for section in workspace.species_sections if section.species == "Coral"), None)
        self.assertIsNotNone(shell_section)
        figure_set = next((item for item in (shell_section.identifier_figures if shell_section else []) if item.identifier == "SampleA"), None)
        self.assertIsNotNone(figure_set)
        identifier_raw = next(
            (
                trace
                for trace in (figure_set.d13c.get("data", []) if figure_set else [])
                if str(trace.get("name", "")).startswith("Raw d13C")
            ),
            None,
        )
        self.assertIsNotNone(identifier_raw)
        identifier_raw_ids = [
            str(item[3])
            for item in ((identifier_raw or {}).get("customdata") or [])
            if isinstance(item, (list, tuple)) and len(item) > 3
        ]
        self.assertIn("4", identifier_raw_ids)

    def test_restored_rows_do_not_get_reintroduced_when_range_outliers(self) -> None:
        df = sample_processing_df().copy()
        df["Identifier 1"] = "SampleA"
        df["Species"] = "Coral"
        df["Identifier 2"] = ["1", "2", "3", "4"]
        df["Collector Status"] = ""
        df["d 13C/12C  Mean"] = [1.0, 1.1, 1.2, 2.8]
        df["d 18O/16O  Mean"] = [2.0, 2.1, 2.2, 3.5]
        # Force row "4" outside leak range, but keep it marked as restored.
        df["leak_rate"] = [5.0, 5.0, 5.0, 999.0]

        metadata = {
            "processing": {
                "config": {
                    "selected_identifier": "SampleA",
                    "x_axis_option": "By Identifier 2",
                    "color_param": "Date_ordinal",
                    "z_axis": "1  Cycle Int  Samp  44",
                    "signal_range": [0.0, 50.0],
                    "leak_range": [0.0, 100.0],
                    "d13c_range": [-200.0, 200.0],
                    "d18o_range": [-200.0, 200.0],
                    "sigma_level_data": 4.0,
                    "overlays": {
                        "show_statistical_outliers": True,
                        "show_range_outliers": True,
                        "show_manual_outliers": False,
                        "show_saturated_collectors": False,
                        "show_saturated_samples": False,
                        "show_failed_samples": False,
                    },
                    "manual_linearity_override": {
                        "enabled": False,
                        "d13_per_10v": 0.0,
                        "d18_per_10v": 0.0,
                    },
                    "export": {
                        "include_outliers": False,
                        "selected_ids": ["All"],
                        "interpolate_outliers": False,
                        "client_name": None,
                        "comment_map": {},
                    },
                }
            },
            "edit_state": {
                "edited_rows": ["3"],
                "original_delta_values": {},
                "original_missing_delta_tokens": ["d13C|3", "d18O|3"],
                "manual_outlier_overrides": {},
                "restored_delta_tokens": ["d13C|3", "d18O|3"],
            },
            "calibration": {"selected_standards": []},
        }

        workspace = build_processing_workspace("session-1", df, sample_cycles_df(), metadata)

        shell_section = next((section for section in workspace.species_sections if section.species == "Coral"), None)
        self.assertIsNotNone(shell_section)
        figure_set = next((item for item in (shell_section.identifier_figures if shell_section else []) if item.identifier == "SampleA"), None)
        self.assertIsNotNone(figure_set)
        identifier_raw = next(
            (
                trace
                for trace in (figure_set.d13c.get("data", []) if figure_set else [])
                if str(trace.get("name", "")).startswith("Raw d13C")
            ),
            None,
        )
        self.assertIsNotNone(identifier_raw)
        identifier_raw_ids = [
            str(item[3])
            for item in ((identifier_raw or {}).get("customdata") or [])
            if isinstance(item, (list, tuple)) and len(item) > 3
        ]
        self.assertNotIn("4", identifier_raw_ids)

    def test_apply_edit_action_set_value_and_reset(self) -> None:
        df = sample_processing_df()
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}
        coefficients = {"d13C": {"slope": 1.0, "intercept": 1.0}}

        updated_df, updated_state = apply_edit_action(
            df,
            edit_state,
            EditAction(action="set_value", targets=[{"row_label": "0", "isotope_key": "d13C"}], value=7.5),
            calibration_coefficients=coefficients,
        )
        self.assertEqual(float(updated_df.loc[0, "d 13C/12C  Mean"]), 7.5)
        self.assertEqual(float(updated_df.loc[0, "d13C_calibrated"]), 8.5)
        self.assertIn("0", updated_state["edited_rows"])
        self.assertEqual(updated_state["original_delta_values"]["d13C|0"], 1.0)

        reset_df, reset_state = apply_edit_action(
            updated_df,
            updated_state,
            EditAction(action="reset_to_original", targets=[{"row_label": "0", "isotope_key": "d13C"}]),
            calibration_coefficients=coefficients,
        )
        self.assertEqual(float(reset_df.loc[0, "d 13C/12C  Mean"]), 1.0)
        self.assertNotIn("0", reset_state["edited_rows"])
        self.assertNotIn("d13C|0", reset_state["original_delta_values"])

    def test_apply_edit_action_set_value_keeps_calibrated_synced_without_coefficients(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A"],
                "Identifier 2": [1, 2, 3],
                "d 13C/12C  Mean": [1.0, 2.0, 3.0],
                "d13C_calibrated": [np.nan, np.nan, np.nan],
                "d13C_calibrated_linearity_corrected": [np.nan, np.nan, np.nan],
            }
        )
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}

        updated_df, _ = apply_edit_action(
            df,
            edit_state,
            EditAction(action="set_value", targets=[{"row_label": "1", "isotope_key": "d13C"}], value=7.5),
        )

        self.assertAlmostEqual(float(updated_df.loc[1, "d 13C/12C  Mean"]), 7.5, places=6)
        self.assertAlmostEqual(float(updated_df.loc[1, "d13C_calibrated"]), 7.5, places=6)
        self.assertAlmostEqual(float(updated_df.loc[1, "d13C_calibrated_linearity_corrected"]), 7.5, places=6)

    def test_apply_edit_action_interpolate_scopes_to_identifier_group(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A", "B", "B"],
                "Identifier 2": [1, 2, 3, 2, 3],
                "d 13C/12C  Mean": [1.0, 99.0, 3.0, 50.0, 60.0],
            }
        )
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}

        updated_df, updated_state = apply_edit_action(
            df,
            edit_state,
            EditAction(action="interpolate", targets=[{"row_label": "1", "isotope_key": "d13C"}]),
        )

        self.assertAlmostEqual(float(updated_df.loc[1, "d 13C/12C  Mean"]), 2.0)
        self.assertAlmostEqual(float(updated_df.loc[3, "d 13C/12C  Mean"]), 50.0)
        self.assertIn("1", updated_state["edited_rows"])
        self.assertEqual(updated_state["original_delta_values"]["d13C|1"], 99.0)

    def test_apply_edit_action_interpolate_scopes_to_species_within_identifier(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A", "A", "A", "A"],
                "Species": ["Uvigerina", "Uvigerina", "Uvigerina", "Other", "Other", "Other"],
                "Identifier 2": [1, 2, 3, 1, 2, 3],
                "d 13C/12C  Mean": [1.0, np.nan, 3.0, -10.0, 999.0, -20.0],
                "d 18O/16O  Mean": [2.0, np.nan, 4.0, 5.0, 6.0, 7.0],
            }
        )
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}

        updated_df, _ = apply_edit_action(
            df,
            edit_state,
            EditAction(action="interpolate", targets=[{"row_label": "1", "isotope_key": "d13C"}]),
        )

        # Should interpolate from Uvigerina neighbors (1.0 -> 3.0), not from "Other" species values.
        self.assertAlmostEqual(float(updated_df.loc[1, "d 13C/12C  Mean"]), 2.0, places=6)

    def test_apply_edit_action_interpolate_prefers_distinct_identifier2_neighbors(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A", "A", "A"],
                "Identifier 2": [20, 21, 22, 22, 23],
                "d 13C/12C  Mean": [0.5, 2.3, 2.2, np.nan, -0.2],
                "d 18O/16O  Mean": [2.0, 2.1, 2.2, np.nan, 2.4],
            }
        )
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}

        updated_df, _ = apply_edit_action(
            df,
            edit_state,
            EditAction(action="interpolate", targets=[{"row_label": "3", "isotope_key": "d13C"}]),
        )

        self.assertAlmostEqual(float(updated_df.loc[3, "d 13C/12C  Mean"]), 1.05, places=6)

    def test_apply_edit_action_interpolate_no_update_when_target_identifier2_not_numeric(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A"],
                "Species": ["Uvigerina", "Uvigerina", "Uvigerina"],
                "Identifier 2": [10, "bad-token", 40],
                "d 13C/12C  Mean": [1.0, np.nan, 5.0],
                "d 18O/16O  Mean": [2.0, np.nan, 6.0],
            }
        )
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}

        updated_df, _ = apply_edit_action(
            df,
            edit_state,
            EditAction(action="interpolate", targets=[{"row_label": "1", "isotope_key": "d13C"}]),
        )

        self.assertTrue(pd.isna(updated_df.loc[1, "d 13C/12C  Mean"]))

    def test_apply_edit_action_interpolate_multi_target_excludes_selected_neighbors(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A", "A"],
                "Identifier 2": [1, 2, 3, 4],
                "d 13C/12C  Mean": [1.0, 100.0, 200.0, 4.0],
            }
        )
        edit_state = {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}

        updated_df, updated_state = apply_edit_action(
            df,
            edit_state,
            EditAction(
                action="interpolate",
                targets=[
                    {"row_label": "1", "isotope_key": "d13C"},
                    {"row_label": "2", "isotope_key": "d13C"},
                ],
            ),
        )

        # Both selected rows should be interpolated from non-selected neighbors
        # (Identifier 2 == 1 and 4), not from each other's original outlier values.
        self.assertAlmostEqual(float(updated_df.loc[1, "d 13C/12C  Mean"]), 2.0, places=6)
        self.assertAlmostEqual(float(updated_df.loc[2, "d 13C/12C  Mean"]), 3.0, places=6)
        self.assertIn("1", updated_state["edited_rows"])
        self.assertIn("2", updated_state["edited_rows"])

    def test_build_processing_workspace_returns_expected_sections(self) -> None:
        df = sample_processing_df()
        metadata = {
            "processing": {
                "config": {
                    "selected_identifier": "All",
                    "x_axis_option": "By Identifier 2",
                    "color_param": "Date_ordinal",
                    "z_axis": "1  Cycle Int  Samp  44",
                    "signal_range": [0.0, 50.0],
                    "leak_range": [0.0, 1000.0],
                    "d13c_range": [-10.0, 10.0],
                    "d18o_range": [-10.0, 10.0],
                    "sigma_level_data": 4.0,
                    "overlays": {
                        "show_statistical_outliers": True,
                        "show_range_outliers": True,
                        "show_saturated_collectors": True,
                        "show_saturated_samples": True,
                        "show_failed_samples": True,
                    },
                    "manual_linearity_override": {
                        "enabled": False,
                        "d13_per_10v": 0.0,
                        "d18_per_10v": 0.0,
                    },
                    "export": {
                        "include_outliers": False,
                        "selected_ids": ["All"],
                        "interpolate_outliers": False,
                        "client_name": None,
                        "comment_map": {},
                    },
                }
            },
            "edit_state": {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
            "calibration": {"selected_standards": []},
        }

        workspace = build_processing_workspace("session-1", df, sample_cycles_df(), metadata)

        self.assertEqual(workspace.session_id, "session-1")
        self.assertIn("processing_3d", workspace.overview_figures)
        self.assertIn("crossplot", workspace.overview_figures)
        self.assertGreaterEqual(len(workspace.species_sections), 2)
        self.assertIn("All", workspace.available_values.identifiers)
        self.assertGreaterEqual(workspace.summary.total_measurements, 1)
        self.assertIn(VALID_CYCLES_COL, workspace.available_values.color_params)
        self.assertIn(CYCLE1_SIGNAL_SAMP44_COL, workspace.available_values.color_params)
        self.assertIn(CYCLE1_SIGNAL_REF44_COL, workspace.available_values.color_params)
        self.assertIn(CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL, workspace.available_values.color_params)
        self.assertIn(CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL, workspace.available_values.z_axis_options)

    def test_species_name_map_merges_processing_sections(self) -> None:
        df = sample_processing_df()
        df.loc[2, "Species"] = "Corl"
        df.loc[2, "Label"] = "SampleA - Corl"
        metadata = {
            "processing": {
                "config": {
                    "selected_identifier": "All",
                    "x_axis_option": "By Identifier 2",
                    "color_param": "Date_ordinal",
                    "z_axis": "1  Cycle Int  Samp  44",
                    "species_name_map": {"Corl": "Coral"},
                    "signal_range": [0.0, 50.0],
                    "leak_range": [0.0, 1000.0],
                    "d13c_range": [-10.0, 10.0],
                    "d18o_range": [-10.0, 10.0],
                    "sigma_level_data": 4.0,
                    "overlays": {
                        "show_statistical_outliers": True,
                        "show_range_outliers": True,
                        "show_saturated_collectors": True,
                        "show_saturated_samples": True,
                        "show_failed_samples": True,
                    },
                    "manual_linearity_override": {
                        "enabled": False,
                        "d13_per_10v": 0.0,
                        "d18_per_10v": 0.0,
                    },
                    "export": {
                        "include_outliers": False,
                        "selected_ids": ["All"],
                        "interpolate_outliers": False,
                        "client_name": None,
                        "comment_map": {},
                    },
                }
            },
            "edit_state": {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
            "calibration": {"selected_standards": []},
        }

        workspace = build_processing_workspace("session-1", df, sample_cycles_df(), metadata)

        species_sections = {section.species for section in workspace.species_sections}
        self.assertIn("Coral", species_sections)
        self.assertNotIn("Corl", species_sections)
        self.assertIn("Corl", workspace.available_values.species)

    def test_identifier1_name_map_updates_processing_outputs_and_preserves_sources(self) -> None:
        df = sample_processing_df()
        metadata = {
            "processing": {
                "config": {
                    "identifier1_name_map": {"SampleA": "Sample Alpha"},
                }
            },
            "edit_state": {
                "edited_rows": [],
                "original_delta_values": {},
                "manual_outlier_overrides": {},
            },
            "calibration": {"selected_standards": []},
        }

        workspace = build_processing_workspace("session-1", df, sample_cycles_df(), metadata)

        self.assertIn("Sample Alpha", workspace.available_values.identifiers)
        self.assertIn("Sample Alpha", workspace.available_values.export_identifiers)
        self.assertNotIn("SampleA", workspace.available_values.export_identifiers)
        self.assertIn("SampleA", workspace.available_values.identifier1_sources)
        section_identifiers = {
            figure.identifier
            for section in workspace.species_sections
            for figure in section.identifier_figures
        }
        self.assertIn("Sample Alpha", section_identifiers)
        self.assertNotIn("SampleA", section_identifiers)

    def test_all_range_outlier_species_remains_visible_after_identifier_rename(self) -> None:
        df = sample_processing_df().iloc[:3].copy()
        df["d 13C/12C  Mean"] = [-13.7, -13.6, -13.5]
        df["d 18O/16O  Mean"] = [-27.9, -27.8, -27.7]
        metadata = {
            "processing": {
                "config": {
                    "identifier1_name_map": {"SampleA": "Renamed sample"},
                    "species_name_map": {"Coral": "Species 1"},
                    "d13c_range": [-10.0, 10.0],
                    "d18o_range": [-10.0, 10.0],
                    "overlays": {"show_range_outliers": False},
                }
            },
            "edit_state": {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
            "calibration": {"selected_standards": []},
        }

        workspace = build_processing_workspace("all-range-outliers", df, sample_cycles_df(), metadata)

        section = next(item for item in workspace.species_sections if item.species == "Species 1")
        figure_set = next(item for item in section.identifier_figures if item.identifier == "Renamed sample")
        d13_trace_names = {str(trace.get("name", "")) for trace in figure_set.d13c.get("data", [])}
        self.assertIn("d13C Range", d13_trace_names)
        self.assertTrue(figure_set.d13c.get("layout", {}).get("annotations"))

    def test_mapped_standard_only_session_still_builds_processing_summary_traces(self) -> None:
        df = sample_processing_df().iloc[:3].copy()
        df["Identifier 1"] = "Reference batch"
        df["Identifier 2"] = ["1", "2", "3"]
        df["Species"] = "Reference"
        df["Collector Status"] = ""
        df["d 13C/12C  Mean"] = [-0.80, -0.78, -0.74]
        df["d 18O/16O  Mean"] = [-5.90, -5.70, -5.65]
        metadata = {
            "processing": {
                "config": {
                    "identifier1_name_map": {"Reference batch": "SHP2L"},
                    "signal_range": [0.0, 100.0],
                    "leak_range": [0.0, 1000.0],
                    "d13c_range": [-100.0, 100.0],
                    "d18o_range": [-100.0, 100.0],
                    "sigma_level_data": 99.0,
                }
            },
            "edit_state": {
                "edited_rows": [],
                "original_delta_values": {},
                "manual_outlier_overrides": {},
            },
            "calibration": {"selected_standards": ["SHP2L"]},
        }

        workspace = build_processing_workspace("standard-only", df, pd.DataFrame(), metadata)

        d13_trace_names = {
            str(trace.get("name", ""))
            for trace in workspace.overview_figures.get("d13_summary", {}).get("data", [])
        }
        d18_trace_names = {
            str(trace.get("name", ""))
            for trace in workspace.overview_figures.get("d18_summary", {}).get("data", [])
        }
        self.assertIn("Standard measured d13C - SHP2L", d13_trace_names)
        self.assertIn("Standard measured d18O - SHP2L", d18_trace_names)

    def test_run_level_linearity_basis_can_use_cycle_endpoint_intensities(self) -> None:
        df = sample_processing_df().iloc[[0]].copy()
        cycles = pd.DataFrame(
            {
                "Cycle Number": ["pre", "1", "2", "3"],
                "Identifier 1": ["SampleA", "SampleA", "SampleA", "SampleA"],
                "Identifier 2": ["1", "1", "1", "1"],
                "Excel File": ["run1.xlsx", "run1.xlsx", "run1.xlsx", "run1.xlsx"],
                "Run ID": ["run-1", "run-1", "run-1", "run-1"],
                "Date": ["2025-01-01", "2025-01-01", "2025-01-01", "2025-01-01"],
                "d 13C/12C  Mean": [1.0, 0.9, 1.0, 1.1],
                "Cycle Intensity Samp 44": [np.nan, 49.0, 20.0, 30.0],
                "Cycle Intensity Ref 44": [np.nan, 10.0, 18.0, 28.0],
            }
        )

        first = apply_run_level_linearity_basis_from_cycles(
            df,
            cycles,
            cycle_intensity_aggregation="first_valid_cycle",
        )
        last = apply_run_level_linearity_basis_from_cycles(
            df,
            cycles,
            cycle_intensity_aggregation="last_valid_cycle",
        )
        median = apply_run_level_linearity_basis_from_cycles(df, cycles)

        self.assertAlmostEqual(float(first.iloc[0][CYCLE1_SIGNAL_SAMP44_COL]), 20.0)
        self.assertAlmostEqual(float(first.iloc[0][CYCLE1_SIGNAL_DIFF44_COL]), 2.0)
        self.assertAlmostEqual(float(last.iloc[0][CYCLE1_SIGNAL_MEAN_SAMP_REF44_COL]), 29.0)
        self.assertAlmostEqual(float(median.iloc[0][CYCLE1_SIGNAL_SAMP44_COL]), 30.0)

    def test_manual_linearity_override_uses_processing_diff_intensity_toggle(self) -> None:
        df = sample_processing_df().copy()
        df["1  Cycle Int  Diff Samp-Ref  44"] = [4.0, 6.0, 9.0, 2.0]

        sample_intensity_config = normalize_processing_config(
            {
                "manual_linearity_override": {
                    "enabled": True,
                    "use_diff_intensity": False,
                    "d13_per_10v": 1.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        diff_intensity_config = normalize_processing_config(
            {
                "manual_linearity_override": {
                    "enabled": True,
                    "use_diff_intensity": True,
                    "d13_per_10v": 1.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        calibration_meta = {"config": {"linearity": {"use_diff_intensity": False}}, "linearity_fits": {}}

        sample_intensity_work = _derive_working_frame(df, sample_intensity_config, calibration_meta=calibration_meta)
        diff_intensity_work = _derive_working_frame(df, diff_intensity_config, calibration_meta=calibration_meta)

        self.assertAlmostEqual(float(sample_intensity_work.loc[0, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(diff_intensity_work.loc[0, "d 13C/12C  Mean"]), 1.0, places=6)

    def test_manual_linearity_override_diff_mode_combines_mismatch_and_initial_intensity(self) -> None:
        df = sample_processing_df().copy()
        df["d 13C/12C  Mean"] = [1.0, 1.0, 2.0, 3.0]
        df["1  Cycle Int  Ref  44"] = [10.0, 20.0, 20.0, 10.0]
        df["1  Cycle Int  Diff Samp-Ref  44"] = [5.0, 10.0, 20.0, 2.0]

        config = normalize_processing_config(
            {
                "manual_linearity_override": {
                    "enabled": True,
                    "use_diff_intensity": True,
                    "d13_per_10v": 1.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        calibration_meta = {"config": {"linearity": {"use_diff_intensity": False}}, "linearity_fits": {}}

        work = _derive_working_frame(df, config, calibration_meta=calibration_meta)

        self.assertAlmostEqual(float(work.loc[0, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(work.loc[1, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(work.loc[2, "d 13C/12C  Mean"]), 2.0, places=6)

    def test_manual_linearity_override_quadratic_changes_processing_transform(self) -> None:
        df = sample_processing_df().copy()

        linear_config = normalize_processing_config(
            {
                "manual_linearity_override": {
                    "enabled": True,
                    "use_diff_intensity": False,
                    "quadratic": False,
                    "d13_per_10v": 1.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        quadratic_config = normalize_processing_config(
            {
                "manual_linearity_override": {
                    "enabled": True,
                    "use_diff_intensity": False,
                    "quadratic": True,
                    "d13_per_10v": 0.0,
                    "d13_per_10v2": 1.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        calibration_meta = {"config": {"linearity": {"use_diff_intensity": False}}, "linearity_fits": {}}

        linear_work = _derive_working_frame(df, linear_config, calibration_meta=calibration_meta)
        quadratic_work = _derive_working_frame(df, quadratic_config, calibration_meta=calibration_meta)

        self.assertAlmostEqual(float(linear_work.loc[0, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(quadratic_work.loc[0, "d 13C/12C  Mean"]), 1.0, places=6)

    def test_manual_linearity_override_max_sample_signal_excludes_high_signal_samples(self) -> None:
        df = sample_processing_df().copy()

        config = normalize_processing_config(
            {
                "manual_linearity_override": {
                    "enabled": True,
                    "use_diff_intensity": False,
                    "max_sample_signal": 15.0,
                    "d13_per_10v": 1.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        calibration_meta = {"config": {"linearity": {"use_diff_intensity": False}}, "linearity_fits": {}}

        work = _derive_working_frame(df, config, calibration_meta=calibration_meta)

        self.assertAlmostEqual(float(work.loc[0, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(work.loc[1, "d 13C/12C  Mean"]), 50.0, places=6)
        self.assertAlmostEqual(float(work.loc[2, "d 13C/12C  Mean"]), 2.0, places=6)

    def test_calibration_manual_linearity_override_does_not_apply_in_processing_working_frame_when_disabled(self) -> None:
        df = sample_processing_df().copy()
        df["d 13C/12C  Mean"] = [1.0, 1.0, 1.0, 1.0]
        df["1  Cycle Int  Samp  44"] = [10.0, 20.0, 30.0, 20.0]
        config = normalize_processing_config(
            {
                "manual_linearity_override": {
                    "enabled": False,
                    "use_diff_intensity": False,
                    "d13_per_10v": 0.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        calibration_meta = {
            "config": {
                "linearity": {
                    "apply": False,
                    "use_diff_intensity": False,
                    "manual_override_enabled": True,
                    "manual_d13_per_10v": 1.0,
                    "manual_d18_per_10v": 0.0,
                }
            },
            "linearity_fits": {},
        }

        work = _derive_working_frame(df, config, calibration_meta=calibration_meta)

        self.assertAlmostEqual(float(work.loc[0, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(work.loc[1, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(work.loc[2, "d 13C/12C  Mean"]), 1.0, places=6)

    def test_processing_manual_linearity_override_is_ignored_when_calibration_linearity_is_disabled(self) -> None:
        df = sample_processing_df().copy()
        df["d 13C/12C  Mean"] = [1.0, 1.0, 1.0, 1.0]
        df["1  Cycle Int  Samp  44"] = [10.0, 20.0, 30.0, 20.0]
        config = normalize_processing_config(
            {
                "manual_linearity_override": {
                    "enabled": True,
                    "use_diff_intensity": False,
                    "d13_per_10v": 2.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        calibration_meta = {
            "config": {
                "linearity": {
                    "apply": False,
                    "use_diff_intensity": False,
                    "manual_override_enabled": True,
                    "manual_d13_per_10v": 1.0,
                    "manual_d18_per_10v": 0.0,
                }
            },
            "linearity_fits": {},
        }

        work = _derive_working_frame(df, config, calibration_meta=calibration_meta)

        self.assertAlmostEqual(float(work.loc[0, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(work.loc[1, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(work.loc[2, "d 13C/12C  Mean"]), 1.0, places=6)

    def test_saved_linearity_fits_apply_calibration_manual_coefficient_offsets_when_linearity_enabled(self) -> None:
        df = sample_processing_df().copy()
        config = normalize_processing_config(
            {
                "manual_linearity_override": {
                    "enabled": False,
                    "use_diff_intensity": False,
                    "d13_per_10v": 0.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        baseline_meta = {
            "config": {
                "linearity": {
                    "apply": True,
                    "use_diff_intensity": False,
                    "manual_override_enabled": False,
                    "manual_d13_per_10v": 0.0,
                    "manual_d18_per_10v": 0.0,
                }
            },
            "linearity_fits": {
                "d13C": {"slope": 1.0, "intercept": 0.0, "x_ref": 15.0, "n": 4},
                "d18O": {"slope": 0.0, "intercept": 0.0, "x_ref": 15.0, "n": 4},
                "intensity_col": "1  Cycle Int  Samp  44",
            },
            "coefficients": {},
        }
        override_meta = {
            "config": {
                "linearity": {
                    "apply": True,
                    "use_diff_intensity": False,
                    "manual_override_enabled": True,
                    "manual_d13_per_10v": 1.0,
                    "manual_d18_per_10v": 0.0,
                }
            },
            "linearity_fits": {
                "d13C": {"slope": 1.0, "intercept": 0.0, "x_ref": 15.0, "n": 4},
                "d18O": {"slope": 0.0, "intercept": 0.0, "x_ref": 15.0, "n": 4},
                "intensity_col": "1  Cycle Int  Samp  44",
            },
            "coefficients": {},
        }

        baseline_work = _derive_working_frame(df, config, calibration_meta=baseline_meta)
        override_work = _derive_working_frame(df, config, calibration_meta=override_meta)

        self.assertNotAlmostEqual(
            float(baseline_work.loc[1, "d 13C/12C  Mean"]),
            float(override_work.loc[1, "d 13C/12C  Mean"]),
            places=9,
        )
        self.assertAlmostEqual(
            float(baseline_work.loc[1, "d 18O/16O  Mean"]),
            float(override_work.loc[1, "d 18O/16O  Mean"]),
            places=9,
        )

    def test_saved_linearity_fits_are_applied_before_processing_outlier_filtering(self) -> None:
        df = sample_processing_df().copy()
        config = normalize_processing_config(
            {
                "manual_linearity_override": {
                    "enabled": False,
                    "use_diff_intensity": False,
                    "d13_per_10v": 0.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        calibration_meta = {
            "config": {"linearity": {"apply": True, "use_diff_intensity": False}},
            "linearity_fits": {
                "d13C": {"slope": 96.0, "intercept": 0.0, "r2": 1.0, "x_ref": 15.0, "n": 3},
                "d18O": {"slope": 0.0, "intercept": 0.0, "r2": 1.0, "x_ref": 15.0, "n": 3},
                "intensity_col": "1  Cycle Int  Samp  44",
            },
        }

        work = _derive_working_frame(df, config, calibration_meta=calibration_meta)

        self.assertAlmostEqual(float(work.loc[1, "d 13C/12C  Mean"]), 2.0, places=6)

    def test_saved_linearity_fits_honor_isotope_specific_line_offsets(self) -> None:
        df = sample_processing_df().copy()
        config = normalize_processing_config(
            {
                "manual_linearity_override": {
                    "enabled": False,
                    "use_diff_intensity": False,
                    "d13_per_10v": 0.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        base_fits = {
            "d13C": {"slope": 1.0, "intercept": 0.0, "r2": 1.0, "x_ref": 15.0, "n": 3},
            "d18O": {"slope": 0.0, "intercept": 0.0, "r2": 1.0, "x_ref": 15.0, "n": 3},
            "intensity_col": "1  Cycle Int  Samp  44",
        }
        baseline_meta = {
            "config": {"linearity": {"apply": True, "use_diff_intensity": False, "line_1_offset": 0.0, "line_2_offset": 0.0}},
            "linearity_fits": base_fits,
        }
        offset_meta = {
            "config": {
                "linearity": {
                    "apply": True,
                    "use_diff_intensity": False,
                    "line_1_offset": 0.0,
                    "line_2_offset": 0.0,
                    "line_1_offset_d13": 2.0,
                }
            },
            "linearity_fits": base_fits,
        }

        baseline_work = _derive_working_frame(df, config, calibration_meta=baseline_meta)
        offset_work = _derive_working_frame(df, config, calibration_meta=offset_meta)

        self.assertNotAlmostEqual(
            float(baseline_work.loc[0, "d 13C/12C  Mean"]),
            float(offset_work.loc[0, "d 13C/12C  Mean"]),
            places=6,
        )
        self.assertAlmostEqual(
            float(baseline_work.loc[0, "d 18O/16O  Mean"]),
            float(offset_work.loc[0, "d 18O/16O  Mean"]),
            places=6,
        )

    def test_derive_working_frame_applies_isotope_line_offsets_without_linearity_fit(self) -> None:
        df = sample_processing_df().copy()
        config = normalize_processing_config(
            {
                "manual_linearity_override": {
                    "enabled": False,
                    "use_diff_intensity": False,
                    "d13_per_10v": 0.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        calibration_meta = {
            "config": {"linearity": {"apply": False, "line_1_offset_d13": 1.0, "line_1_offset_d18": -2.0}},
            "linearity_fits": {},
            "selected_standards": [],
            "coefficients": {},
        }

        work = _derive_working_frame(df, config, calibration_meta=calibration_meta)

        self.assertAlmostEqual(float(work.loc[0, "d 13C/12C  Mean"]), 2.0, places=6)
        self.assertAlmostEqual(float(work.loc[0, "d 18O/16O  Mean"]), 0.0, places=6)

    def test_derive_working_frame_recalibrates_after_modifications_before_outlier_masking(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["SampleA", "SampleB", "SHP2L"],
                "Identifier 2": ["1", "1", "1"],
                "d 13C/12C  Mean": [1.0, 2.0, -0.8],
                "d 18O/16O  Mean": [2.0, 2.1, -5.7],
                "1  Cycle Int  Samp  44": [15.0, 16.0, 15.0],
                "1  Cycle Int  Ref  44": [10.0, 10.0, 10.0],
                "1  Cycle Int  Diff Samp-Ref  44": [5.0, 6.0, 5.0],
                "leak_rate": [5.0, 1500.0, 5.0],
                "d13C_calibrated": [9.9, 9.9, 9.9],
                "d13C_calibrated_linearity_corrected": [9.8, 9.8, 9.8],
                "Collector Status": ["", "", ""],
            }
        )
        config = normalize_processing_config(
            {
                "manual_linearity_override": {
                    "enabled": False,
                    "use_diff_intensity": False,
                    "d13_per_10v": 0.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        calibration_meta = {
            "config": {"linearity": {"apply": True, "use_diff_intensity": False}},
            "linearity_fits": {
                "d13C": {"slope": 1.0, "intercept": 0.0, "x_ref": 15.0, "n": 3},
                "d18O": {"slope": 0.0, "intercept": 0.0, "x_ref": 15.0, "n": 3},
                "intensity_col": "1  Cycle Int  Samp  44",
            },
            "coefficients": {
                "d13C": {"slope": 2.0, "intercept": 0.0},
            },
            "selected_standards": ["SHP2L"],
        }

        work = _derive_working_frame(
            df,
            config,
            calibration_meta=calibration_meta,
            edit_state={"edited_rows": [], "manual_outlier_overrides": {}},
        )

        self.assertAlmostEqual(float(work.loc[0, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(work.loc[1, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(work.loc[0, "d13C_calibrated"]), 2.0, places=6)
        self.assertAlmostEqual(float(work.loc[0, "d13C_calibrated_linearity_corrected"]), 2.0, places=6)
        self.assertAlmostEqual(float(work.loc[1, "d13C_calibrated"]), 2.0, places=6)
        self.assertAlmostEqual(float(work.loc[1, "d13C_calibrated_linearity_corrected"]), 2.0, places=6)
        self.assertTrue(pd.isna(work.loc[2, "d13C_calibrated"]))
        self.assertTrue(pd.isna(work.loc[2, "d13C_calibrated_linearity_corrected"]))

    def test_derive_working_frame_can_skip_shared_linearity_for_partially_saturated_samples(self) -> None:
        df = sample_processing_df().copy()
        config = normalize_processing_config(
            {
                "apply_shared_linearity_to_partially_saturated": False,
                "manual_linearity_override": {
                    "enabled": False,
                    "use_diff_intensity": False,
                    "d13_per_10v": 0.0,
                    "d18_per_10v": 0.0,
                },
            }
        )
        calibration_meta = {
            "config": {"linearity": {"apply": True, "use_diff_intensity": False}},
            "linearity_fits": {
                "d13C": {"slope": 1.0, "intercept": 0.0, "x_ref": 15.0, "n": 3},
                "d18O": {"slope": 1.0, "intercept": 0.0, "x_ref": 15.0, "n": 3},
                "intensity_col": "1  Cycle Int  Samp  44",
            },
            "coefficients": {
                "d13C": {"slope": 2.0, "intercept": 0.0},
                "d18O": {"slope": 2.0, "intercept": 0.0},
            },
            "selected_standards": [],
        }

        work = _derive_working_frame(df, config, calibration_meta=calibration_meta)

        self.assertAlmostEqual(float(work.loc[0, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(work.loc[2, "d 13C/12C  Mean"]), 2.0, places=6)
        self.assertAlmostEqual(float(work.loc[2, "d 18O/16O  Mean"]), 2.4, places=6)
        self.assertAlmostEqual(float(work.loc[2, "d13C_calibrated"]), 4.0, places=6)
        self.assertAlmostEqual(float(work.loc[2, "d18O_calibrated"]), 4.8, places=6)

    def test_derive_working_frame_applies_shared_linearity_to_partially_saturated_samples_by_default(self) -> None:
        df = sample_processing_df().copy()
        config = normalize_processing_config({})
        calibration_meta = {
            "config": {"linearity": {"apply": True, "use_diff_intensity": False}},
            "linearity_fits": {
                "d13C": {"slope": 1.0, "intercept": 0.0, "x_ref": 15.0, "n": 3},
                "d18O": {"slope": 1.0, "intercept": 0.0, "x_ref": 15.0, "n": 3},
                "intensity_col": "1  Cycle Int  Samp  44",
            },
            "coefficients": {
                "d13C": {"slope": 2.0, "intercept": 0.0},
                "d18O": {"slope": 2.0, "intercept": 0.0},
            },
            "selected_standards": [],
        }

        work = _derive_working_frame(df, config, calibration_meta=calibration_meta)

        self.assertAlmostEqual(float(work.loc[2, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(work.loc[2, "d 18O/16O  Mean"]), 1.4, places=6)
        self.assertAlmostEqual(float(work.loc[2, "d13C_calibrated"]), 2.0, places=6)
        self.assertAlmostEqual(float(work.loc[2, "d18O_calibrated"]), 2.8, places=6)

    def test_derive_working_frame_preserves_edited_raw_values_in_working_view(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["SampleA", "SampleB"],
                "Identifier 2": ["1", "1"],
                "d 13C/12C  Mean": [1.0, -0.844],
                "d 18O/16O  Mean": [2.0, 2.1],
                "1  Cycle Int  Samp  44": [15.0, 16.0],
                "1  Cycle Int  Ref  44": [10.0, 10.0],
                "1  Cycle Int  Diff Samp-Ref  44": [5.0, 6.0],
                "leak_rate": [5.0, 5.0],
                "Collector Status": ["", "Partially Saturated Collectors"],
            }
        )
        config = normalize_processing_config(
            {
                "apply_shared_linearity_to_partially_saturated": False,
                "manual_linearity_override": {
                    "enabled": False,
                    "use_diff_intensity": False,
                    "d13_per_10v": 0.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        calibration_meta = {
            "config": {"linearity": {"apply": True, "use_diff_intensity": False}},
            "linearity_fits": {
                "d13C": {"slope": 1.0, "intercept": 0.0, "x_ref": 15.0, "n": 2},
                "d18O": {"slope": 0.0, "intercept": 0.0, "x_ref": 15.0, "n": 2},
                "intensity_col": "1  Cycle Int  Samp  44",
            },
            "coefficients": {"d13C": {"slope": 2.0, "intercept": 0.0}},
            "selected_standards": [],
        }
        edit_state = {"edited_rows": ["1"], "original_delta_values": {}, "manual_outlier_overrides": {}}

        work = _derive_working_frame(df, config, calibration_meta=calibration_meta, edit_state=edit_state)

        # Unedited rows can still flow through the working-frame transforms.
        self.assertAlmostEqual(float(work.loc[0, "d 13C/12C  Mean"]), 1.0, places=6)
        # Edited rows must preserve the explicitly set raw value in charts/tables.
        self.assertAlmostEqual(float(work.loc[1, "d 13C/12C  Mean"]), -0.844, places=6)

    def test_derive_working_frame_applies_shared_linearity_to_edited_partials_when_enabled(self) -> None:
        df = sample_processing_df().copy()
        config = normalize_processing_config({})
        calibration_meta = {
            "config": {"linearity": {"apply": True, "use_diff_intensity": False}},
            "linearity_fits": {
                "d13C": {"slope": 1.0, "intercept": 0.0, "x_ref": 15.0, "n": 3},
                "d18O": {"slope": 1.0, "intercept": 0.0, "x_ref": 15.0, "n": 3},
                "intensity_col": "1  Cycle Int  Samp  44",
            },
            "coefficients": {
                "d13C": {"slope": 2.0, "intercept": 0.0},
                "d18O": {"slope": 2.0, "intercept": 0.0},
            },
            "selected_standards": [],
        }
        edit_state = {"edited_rows": ["2"], "original_delta_values": {}, "manual_outlier_overrides": {}}

        work = _derive_working_frame(df, config, calibration_meta=calibration_meta, edit_state=edit_state)

        self.assertAlmostEqual(float(work.loc[2, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(work.loc[2, "d 18O/16O  Mean"]), 1.4, places=6)

    def test_derive_working_frame_applies_manual_override_to_edited_rows_when_enabled(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["SampleA", "SampleB"],
                "Identifier 2": ["1", "1"],
                "d 13C/12C  Mean": [1.0, -0.844],
                "d 18O/16O  Mean": [2.0, 2.1],
                "1  Cycle Int  Samp  44": [10.0, 30.0],
                "1  Cycle Int  Ref  44": [10.0, 10.0],
                "1  Cycle Int  Diff Samp-Ref  44": [0.0, 20.0],
                "leak_rate": [5.0, 5.0],
                "Collector Status": ["", "Partially Saturated Collectors"],
            }
        )
        config = normalize_processing_config(
            {
                "manual_linearity_override": {
                    "enabled": True,
                    "use_diff_intensity": False,
                    "d13_per_10v": 1.0,
                    "d18_per_10v": 0.0,
                }
            }
        )
        calibration_meta = {
            "config": {"linearity": {"apply": False, "use_diff_intensity": False}},
            "linearity_fits": {"d13C": {"x_ref": 15.0, "n": 2}, "intensity_col": "1  Cycle Int  Samp  44"},
            "coefficients": {},
            "selected_standards": [],
        }
        edit_state = {"edited_rows": ["1"], "original_delta_values": {}, "manual_outlier_overrides": {}}

        work = _derive_working_frame(df, config, calibration_meta=calibration_meta, edit_state=edit_state)

        # Processing-page manual override is no longer applied in the working frame.
        self.assertAlmostEqual(float(work.loc[1, "d 13C/12C  Mean"]), -0.844, places=6)

    def test_overview_and_sequence_charts_handle_missing_species_and_color_parameter(self) -> None:
        df = sample_processing_df().copy()
        df["Species"] = np.nan
        metadata = {
            "processing": {
                "config": {
                    "selected_identifier": "SampleA",
                    "x_axis_option": "By Identifier 2",
                    "color_param": "Line",
                    "z_axis": "1  Cycle Int  Samp  44",
                    "signal_range": [0.0, 50.0],
                    "leak_range": [0.0, 1000.0],
                    "d13c_range": [-10.0, 10.0],
                    "d18o_range": [-10.0, 10.0],
                    "sigma_level_data": 4.0,
                    "overlays": {
                        "show_statistical_outliers": False,
                        "show_range_outliers": False,
                        "show_saturated_collectors": True,
                        "show_saturated_samples": True,
                        "show_failed_samples": True,
                    },
                    "manual_linearity_override": {
                        "enabled": False,
                        "d13_per_10v": 0.0,
                        "d18_per_10v": 0.0,
                    },
                    "export": {
                        "include_outliers": False,
                        "selected_ids": ["All"],
                        "interpolate_outliers": False,
                        "client_name": None,
                        "comment_map": {},
                    },
                }
            },
            "edit_state": {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
            "calibration": {"selected_standards": []},
        }

        workspace = build_processing_workspace("session-1", df, sample_cycles_df(), metadata)

        d13_summary_data = workspace.overview_figures.get("d13_summary", {}).get("data", [])
        crossplot_data = workspace.overview_figures.get("crossplot", {}).get("data", [])
        self.assertGreaterEqual(len(d13_summary_data), 1)
        self.assertGreaterEqual(len(crossplot_data), 1)

        cross_marker = (crossplot_data[0] or {}).get("marker", {})
        self.assertIn("color", cross_marker)
        self.assertIn("cmin", cross_marker)
        self.assertIn("cmax", cross_marker)

        self.assertGreaterEqual(len(workspace.species_sections), 1)
        self.assertGreaterEqual(len(workspace.species_sections[0].identifier_figures), 1)
        figure_set = workspace.species_sections[0].identifier_figures[0]
        d13_raw_trace = next(
            (
                trace
                for trace in figure_set.d13c.get("data", [])
                if str(trace.get("name", "")).startswith("Raw d13C")
            ),
            None,
        )
        self.assertIsNotNone(d13_raw_trace)
        d13_marker = (d13_raw_trace or {}).get("marker", {})
        self.assertIn("color", d13_marker)
        self.assertIn("cmin", d13_marker)
        self.assertIn("cmax", d13_marker)
        d13_cal_trace = next(
            (
                trace
                for trace in figure_set.d13c.get("data", [])
                if str(trace.get("name", "")).startswith("Calibrated d13C")
            ),
            None,
        )
        self.assertIsNotNone(d13_cal_trace)
        y_payload = (d13_cal_trace or {}).get("y")
        if isinstance(y_payload, dict) and "bdata" in y_payload:
            dtype_name = str(y_payload.get("dtype", "f8"))
            decoded = base64.b64decode(str(y_payload.get("bdata", "")))
            d13_cal_values = np.frombuffer(decoded, dtype=np.dtype(dtype_name)).astype(float).tolist()
        elif isinstance(y_payload, list):
            d13_cal_values = [float(value) for value in y_payload if value is not None]
        else:
            d13_cal_values = []
        self.assertIn(1.4, d13_cal_values)
        self.assertIn(2.4, d13_cal_values)

    def test_overview_summary_traces_split_by_species_and_identifier1(self) -> None:
        df = sample_processing_df().copy()
        # Force multiple Identifier 1 values under a single species so summary traces must split by both keys.
        sample_b_mask = df["Identifier 1"].astype(str) == "SampleB"
        df.loc[sample_b_mask, "Species"] = "Coral"
        df.loc[sample_b_mask, "Label"] = "SampleB - Coral"
        df.loc[sample_b_mask, "Collector Status"] = ""
        df.loc[sample_b_mask, "d 18O/16O  Mean"] = 2.6

        config = normalize_processing_config({}).model_dump()
        config["selected_identifier"] = "All"
        config["sigma_level_data"] = 99.0
        metadata = {
            "processing": {"config": config},
            "edit_state": {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
            "calibration": {"selected_standards": []},
        }

        workspace = build_processing_workspace("session-1", df, sample_cycles_df(), metadata)
        d13_summary_data = workspace.overview_figures.get("d13_summary", {}).get("data", [])
        raw_trace_names = {
            str(trace.get("name", ""))
            for trace in d13_summary_data
            if str(trace.get("name", "")).startswith("Raw d13C -")
        }
        d18_summary_data = workspace.overview_figures.get("d18_summary", {}).get("data", [])
        raw_d18_trace_names = {
            str(trace.get("name", ""))
            for trace in d18_summary_data
            if str(trace.get("name", "")).startswith("Raw d18O -")
        }
        calibrated_d13_traces = [
            trace
            for trace in d13_summary_data
            if str(trace.get("name", "")).startswith("Calibrated")
        ]
        calibrated_d18_traces = [
            trace
            for trace in d18_summary_data
            if str(trace.get("name", "")).startswith("Calibrated")
        ]
        d13_calibrated_legend_entries = [trace for trace in calibrated_d13_traces if trace.get("showlegend", True)]
        d18_calibrated_legend_entries = [trace for trace in calibrated_d18_traces if trace.get("showlegend", True)]

        self.assertIn("Raw d13C - Coral | SampleA", raw_trace_names)
        self.assertIn("Raw d13C - Coral | SampleB", raw_trace_names)
        self.assertNotIn("Raw d13C - Coral", raw_trace_names)
        self.assertIn("Raw d18O - Coral | SampleA", raw_d18_trace_names)
        self.assertIn("Raw d18O - Coral | SampleB", raw_d18_trace_names)
        self.assertNotIn("Raw d18O - Coral", raw_d18_trace_names)
        self.assertEqual(len(d13_calibrated_legend_entries), 1)
        self.assertEqual(len(d18_calibrated_legend_entries), 1)
        self.assertEqual(str(d13_calibrated_legend_entries[0].get("name", "")), "Calibrated")
        self.assertEqual(str(d18_calibrated_legend_entries[0].get("name", "")), "Calibrated")

    def test_standard_measurements_are_date_aligned_in_summary_and_species_charts(self) -> None:
        df = pd.concat([sample_processing_df().iloc[[0]]] * 5, ignore_index=True)
        df["Identifier 1"] = ["SampleA", "SampleA", "SampleA", "SHP2L", "SHP2L"]
        # The sample Identifier-2 values are intentionally non-monotonic by
        # date. Standard lines must still connect in the normal chart's
        # left-to-right x sequence.
        df["Identifier 2"] = ["10", "40", "5", "501", "502"]
        df["Species"] = ["Coral", "Coral", "Coral", "Reference", "Reference"]
        df["Label"] = [
            "SampleA - Coral",
            "SampleA - Coral",
            "SampleA - Coral",
            "SHP2L - Reference",
            "SHP2L - Reference",
        ]
        df["Date"] = ["2025-01-01", "2025-01-03", "2025-01-05", "2025-01-02", "2025-01-04"]
        df["Date_ordinal"] = [739252, 739254, 739256, 739253, 739255]
        df["d 13C/12C  Mean"] = [1.0, 1.1, 1.2, -0.8, -0.7]
        df["d 18O/16O  Mean"] = [2.0, 2.1, 2.2, -5.8, -5.7]
        df["Collector Status"] = ""

        def _numeric_payload(payload: object) -> list[float]:
            if isinstance(payload, dict) and "bdata" in payload:
                dtype_name = str(payload.get("dtype", "f8"))
                decoded = base64.b64decode(str(payload.get("bdata", "")))
                return np.frombuffer(decoded, dtype=np.dtype(dtype_name)).astype(float).tolist()
            if isinstance(payload, list):
                return [float(value) for value in payload if value is not None]
            return []

        expected_by_axis = {
            "By Identifier 2": [22.5, 25.0],
            "By Sequence": [0.5, 1.5],
        }
        for x_axis_option, expected_x in expected_by_axis.items():
            with self.subTest(x_axis_option=x_axis_option):
                config = normalize_processing_config({}).model_dump()
                config["selected_identifier"] = "SampleA"
                config["x_axis_option"] = x_axis_option
                config["sigma_level_data"] = 99.0
                config["signal_range"] = [0.0, 100.0]
                config["leak_range"] = [0.0, 1000.0]
                config["d13c_range"] = [-100.0, 100.0]
                config["d18o_range"] = [-100.0, 100.0]
                metadata = {
                    "processing": {"config": config},
                    "edit_state": {
                        "edited_rows": [],
                        "original_delta_values": {},
                        "manual_outlier_overrides": {},
                    },
                    "calibration": {"selected_standards": ["SHP2L"]},
                }

                workspace = build_processing_workspace("session-standards", df, sample_cycles_df(), metadata)
                summary_trace = next(
                    (
                        trace
                        for trace in workspace.overview_figures.get("d13_summary", {}).get("data", [])
                        if str(trace.get("name", "")) == "Standard measured d13C - SHP2L"
                    ),
                    None,
                )
                self.assertIsNotNone(summary_trace)
                self.assertEqual(str((summary_trace or {}).get("yaxis", "")), "y2")
                self.assertTrue(
                    np.allclose(_numeric_payload((summary_trace or {}).get("x")), expected_x)
                )
                self.assertEqual(
                    workspace.overview_figures.get("d13_summary", {})
                    .get("layout", {})
                    .get("yaxis2", {})
                    .get("overlaying"),
                    "y",
                )
                self.assertEqual(
                    workspace.overview_figures.get("d13_summary", {})
                    .get("layout", {})
                    .get("yaxis2", {})
                    .get("tickmode"),
                    "auto",
                )
                self.assertEqual(
                    [str(item[0]) for item in ((summary_trace or {}).get("customdata") or [])],
                    ["", ""],
                )

                coral_section = next(
                    (section for section in workspace.species_sections if section.species == "Coral"),
                    None,
                )
                figure_set = next(
                    (
                        item
                        for item in (coral_section.identifier_figures if coral_section else [])
                        if item.identifier == "SampleA"
                    ),
                    None,
                )
                self.assertIsNotNone(figure_set)
                species_trace = next(
                    (
                        trace
                        for trace in (figure_set.d13c.get("data", []) if figure_set else [])
                        if str(trace.get("name", "")) == "Standard measured d13C - SHP2L"
                    ),
                    None,
                )
                self.assertIsNotNone(species_trace)
                self.assertEqual(str((species_trace or {}).get("yaxis", "")), "y2")
                self.assertTrue(
                    np.allclose(_numeric_payload((species_trace or {}).get("x")), expected_x)
                )
                self.assertEqual(
                    (figure_set.d13c.get("layout", {}).get("yaxis2", {}) if figure_set else {}).get("tickmode"),
                    "auto",
                )

    def test_overview_crossplot_and_3d_use_species_specific_marker_symbols(self) -> None:
        df = sample_processing_df().copy()
        sample_b_mask = df["Identifier 1"].astype(str) == "SampleB"
        # Keep SampleB rows within plotting domain so both species appear in overview charts.
        df.loc[sample_b_mask, "Collector Status"] = ""
        df.loc[sample_b_mask, "d 18O/16O  Mean"] = 2.8
        df.loc[sample_b_mask, "d 13C/12C  Mean"] = 2.9
        df.loc[sample_b_mask, "leak_rate"] = 5.0

        config = normalize_processing_config({}).model_dump()
        config["selected_identifier"] = "All"
        config["sigma_level_data"] = 99.0
        config["signal_range"] = [0.0, 100.0]
        config["leak_range"] = [0.0, 1000.0]
        config["d13c_range"] = [-100.0, 100.0]
        config["d18o_range"] = [-100.0, 100.0]
        metadata = {
            "processing": {"config": config},
            "edit_state": {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
            "calibration": {"selected_standards": []},
        }

        workspace = build_processing_workspace("session-1", df, sample_cycles_df(), metadata)

        crossplot_data = workspace.overview_figures.get("crossplot", {}).get("data", [])
        species_symbols: dict[str, str] = {}
        for trace in crossplot_data:
            if not isinstance(trace, dict):
                continue
            name = str(trace.get("name", ""))
            marker = trace.get("marker", {})
            symbol = marker.get("symbol") if isinstance(marker, dict) else None
            if name in {"Coral", "Shell"} and isinstance(symbol, str):
                species_symbols[name] = symbol
        self.assertIn("Coral", species_symbols)
        self.assertIn("Shell", species_symbols)
        self.assertNotEqual(species_symbols["Coral"], species_symbols["Shell"])

        processing_3d_data = workspace.overview_figures.get("processing_3d", {}).get("data", [])
        species_3d_symbols: dict[str, str] = {}
        for trace in processing_3d_data:
            if not isinstance(trace, dict):
                continue
            name = str(trace.get("name", ""))
            marker = trace.get("marker", {})
            symbol = marker.get("symbol") if isinstance(marker, dict) else None
            if name in {"Coral", "Shell"} and isinstance(symbol, str):
                species_3d_symbols[name] = symbol
        self.assertIn("Coral", species_3d_symbols)
        self.assertIn("Shell", species_3d_symbols)
        self.assertNotEqual(species_3d_symbols["Coral"], species_3d_symbols["Shell"])

    def test_overview_charts_highlight_edited_rows(self) -> None:
        df = sample_processing_df().copy()
        config = normalize_processing_config({}).model_dump()
        config["selected_identifier"] = "All"
        config["sigma_level_data"] = 99.0
        config["signal_range"] = [0.0, 100.0]
        config["leak_range"] = [0.0, 1000.0]
        config["d13c_range"] = [-100.0, 100.0]
        config["d18o_range"] = [-100.0, 100.0]
        metadata = {
            "processing": {"config": config},
            "edit_state": {"edited_rows": ["0"], "original_delta_values": {}, "manual_outlier_overrides": {}},
            "calibration": {"selected_standards": []},
        }

        workspace = build_processing_workspace("session-1", df, sample_cycles_df(), metadata)

        for figure_key in ["d13_summary", "d18_summary", "crossplot", "processing_3d"]:
            trace_names = [
                str(trace.get("name", ""))
                for trace in workspace.overview_figures.get(figure_key, {}).get("data", [])
                if isinstance(trace, dict)
            ]
            self.assertIn("Edited Samples", trace_names, figure_key)

    def test_unselected_outlier_overlays_are_hidden_from_base_charts(self) -> None:
        df = sample_processing_df()
        metadata = {
            "processing": {
                "config": {
                    "selected_identifier": "SampleA",
                    "x_axis_option": "By Identifier 2",
                    "color_param": "Date_ordinal",
                    "z_axis": "1  Cycle Int  Samp  44",
                    "signal_range": [0.0, 50.0],
                    "leak_range": [0.0, 1000.0],
                    "d13c_range": [-100.0, 100.0],
                    "d18o_range": [-100.0, 100.0],
                    "sigma_level_data": 1.0,
                    "overlays": {
                        "show_statistical_outliers": False,
                        "show_range_outliers": False,
                        "show_saturated_collectors": False,
                        "show_saturated_samples": False,
                        "show_failed_samples": False,
                    },
                    "manual_linearity_override": {
                        "enabled": False,
                        "d13_per_10v": 0.0,
                        "d18_per_10v": 0.0,
                    },
                    "export": {
                        "include_outliers": False,
                        "selected_ids": ["All"],
                        "interpolate_outliers": False,
                        "client_name": None,
                        "comment_map": {},
                    },
                }
            },
            "edit_state": {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
            "calibration": {"selected_standards": []},
        }

        workspace = build_processing_workspace("session-1", df, sample_cycles_df(), metadata)

        # Identifier 2 == "2" is a sigma outlier for SampleA; it should be absent from base traces.
        crossplot_data = workspace.overview_figures.get("crossplot", {}).get("data", [])
        crossplot_identifier2 = [
            str(item[3])
            for trace in crossplot_data
            for item in ((trace or {}).get("customdata") or [])
            if isinstance(item, (list, tuple)) and len(item) > 3
        ]
        self.assertNotIn("2", crossplot_identifier2)

        sample_a_section = next((section for section in workspace.species_sections if section.species == "Coral"), None)
        self.assertIsNotNone(sample_a_section)
        sample_a_fig = next(
            (item for item in (sample_a_section.identifier_figures if sample_a_section else []) if item.identifier == "SampleA"),
            None,
        )
        self.assertIsNotNone(sample_a_fig)
        d13_raw_trace = next(
            (
                trace
                for trace in (sample_a_fig.d13c.get("data", []) if sample_a_fig else [])
                if str(trace.get("name", "")).startswith("Raw d13C")
            ),
            None,
        )
        self.assertIsNotNone(d13_raw_trace)
        d13_identifier2 = [
            str(item[3])
            for item in ((d13_raw_trace or {}).get("customdata") or [])
            if isinstance(item, (list, tuple)) and len(item) > 3
        ]
        self.assertNotIn("2", d13_identifier2)

    def test_crossplot_axis_range_tracks_outlier_overlay_visibility(self) -> None:
        df = sample_processing_df()

        def _metadata(show_range_outliers: bool) -> dict[str, object]:
            return {
                "processing": {
                    "config": {
                        "selected_identifier": "SampleA",
                        "x_axis_option": "By Identifier 2",
                        "color_param": "Date_ordinal",
                        "z_axis": "1  Cycle Int  Samp  44",
                        "signal_range": [0.0, 50.0],
                        "leak_range": [0.0, 1000.0],
                        "d13c_range": [-10.0, 10.0],
                        "d18o_range": [-100.0, 100.0],
                        "sigma_level_data": 10.0,
                        "overlays": {
                            "show_statistical_outliers": False,
                            "show_range_outliers": show_range_outliers,
                            "show_saturated_collectors": False,
                            "show_saturated_samples": False,
                            "show_failed_samples": False,
                        },
                        "manual_linearity_override": {
                            "enabled": False,
                            "d13_per_10v": 0.0,
                            "d18_per_10v": 0.0,
                        },
                        "export": {
                            "include_outliers": False,
                            "selected_ids": ["All"],
                            "interpolate_outliers": False,
                            "client_name": None,
                            "comment_map": {},
                        },
                    }
                },
                "edit_state": {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
                "calibration": {"selected_standards": []},
            }

        workspace_hide = build_processing_workspace("session-hide", df, sample_cycles_df(), _metadata(False))
        workspace_show = build_processing_workspace("session-show", df, sample_cycles_df(), _metadata(True))

        y_range_hide = (
            workspace_hide.overview_figures.get("crossplot", {})
            .get("layout", {})
            .get("yaxis", {})
            .get("range")
        )
        y_range_show = (
            workspace_show.overview_figures.get("crossplot", {})
            .get("layout", {})
            .get("yaxis", {})
            .get("range")
        )
        self.assertIsInstance(y_range_hide, list)
        self.assertIsInstance(y_range_show, list)
        self.assertEqual(len(y_range_hide or []), 2)
        self.assertEqual(len(y_range_show or []), 2)
        self.assertLess(float((y_range_hide or [0, 0])[1]), 10.0)
        self.assertGreater(float((y_range_show or [0, 0])[1]), 40.0)

    def test_manual_outlier_overlay_follows_processing_control_toggle(self) -> None:
        df = sample_processing_df()

        def _metadata(show_manual_outliers: bool) -> dict[str, object]:
            return {
                "processing": {
                    "config": {
                        "selected_identifier": "SampleA",
                        "x_axis_option": "By Identifier 2",
                        "color_param": "Date_ordinal",
                        "z_axis": "1  Cycle Int  Samp  44",
                        "signal_range": [0.0, 50.0],
                        "leak_range": [0.0, 1000.0],
                        "d13c_range": [-100.0, 100.0],
                        "d18o_range": [-100.0, 100.0],
                        "sigma_level_data": 10.0,
                        "overlays": {
                            "show_statistical_outliers": False,
                            "show_range_outliers": False,
                            "show_manual_outliers": show_manual_outliers,
                            "show_saturated_collectors": False,
                            "show_saturated_samples": False,
                            "show_failed_samples": False,
                        },
                        "manual_linearity_override": {
                            "enabled": False,
                            "d13_per_10v": 0.0,
                            "d18_per_10v": 0.0,
                        },
                        "export": {
                            "include_outliers": False,
                            "selected_ids": ["All"],
                            "interpolate_outliers": False,
                            "client_name": None,
                            "comment_map": {},
                        },
                    }
                },
                "edit_state": {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {"1": True}},
                "calibration": {"selected_standards": []},
            }

        workspace_hide = build_processing_workspace("session-hide", df, sample_cycles_df(), _metadata(False))
        workspace_show = build_processing_workspace("session-show", df, sample_cycles_df(), _metadata(True))

        cross_names_hide = [str(trace.get("name", "")) for trace in workspace_hide.overview_figures.get("crossplot", {}).get("data", [])]
        cross_names_show = [str(trace.get("name", "")) for trace in workspace_show.overview_figures.get("crossplot", {}).get("data", [])]
        self.assertNotIn("Manual Outliers", cross_names_hide)
        self.assertIn("Manual Outliers", cross_names_show)

        coral_section = next((section for section in workspace_show.species_sections if section.species == "Coral"), None)
        self.assertIsNotNone(coral_section)
        figure_set = next(
            (item for item in (coral_section.identifier_figures if coral_section else []) if item.identifier == "SampleA"),
            None,
        )
        self.assertIsNotNone(figure_set)
        d13_names = [str(trace.get("name", "")) for trace in (figure_set.d13c.get("data", []) if figure_set else [])]
        self.assertIn("Manual Outliers", d13_names)

    def test_partial_saturated_rows_follow_processing_controls_outlier_toggle(self) -> None:
        df = sample_processing_df()

        def _build_metadata(show_saturated_collectors: bool) -> dict[str, object]:
            return {
                "processing": {
                    "config": {
                        "selected_identifier": "SampleA",
                        "x_axis_option": "By Identifier 2",
                        "color_param": "Date_ordinal",
                        "z_axis": "1  Cycle Int  Samp  44",
                        "signal_range": [0.0, 50.0],
                        "leak_range": [0.0, 1000.0],
                        "d13c_range": [-100.0, 100.0],
                        "d18o_range": [-100.0, 100.0],
                        "sigma_level_data": 6.0,
                        "overlays": {
                            "show_statistical_outliers": False,
                            "show_range_outliers": False,
                            "show_saturated_collectors": show_saturated_collectors,
                            "show_saturated_samples": False,
                            "show_failed_samples": False,
                        },
                        "manual_linearity_override": {
                            "enabled": False,
                            "d13_per_10v": 0.0,
                            "d18_per_10v": 0.0,
                        },
                        "export": {
                            "include_outliers": False,
                            "selected_ids": ["All"],
                            "interpolate_outliers": False,
                            "client_name": None,
                            "comment_map": {},
                        },
                    }
                },
                "edit_state": {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
                "calibration": {"selected_standards": []},
            }

        workspace_show = build_processing_workspace("session-show", df, sample_cycles_df(), _build_metadata(True))
        workspace_hide = build_processing_workspace("session-hide", df, sample_cycles_df(), _build_metadata(False))

        section_show = next((section for section in workspace_show.species_sections if section.species == "Coral"), None)
        section_hide = next((section for section in workspace_hide.species_sections if section.species == "Coral"), None)
        self.assertIsNotNone(section_show)
        self.assertIsNotNone(section_hide)

        figure_show = next((item for item in (section_show.identifier_figures if section_show else []) if item.identifier == "SampleA"), None)
        figure_hide = next((item for item in (section_hide.identifier_figures if section_hide else []) if item.identifier == "SampleA"), None)
        self.assertIsNotNone(figure_show)
        self.assertIsNotNone(figure_hide)

        d13_raw_show = next(
            (
                trace
                for trace in (figure_show.d13c.get("data", []) if figure_show else [])
                if str(trace.get("name", "")).startswith("Raw d13C")
            ),
            None,
        )
        d13_raw_hide = next(
            (
                trace
                for trace in (figure_hide.d13c.get("data", []) if figure_hide else [])
                if str(trace.get("name", "")).startswith("Raw d13C")
            ),
            None,
        )
        self.assertIsNotNone(d13_raw_show)
        self.assertIsNotNone(d13_raw_hide)

        ids_show = [
            str(item[3])
            for item in ((d13_raw_show or {}).get("customdata") or [])
            if isinstance(item, (list, tuple)) and len(item) > 3
        ]
        ids_hide = [
            str(item[3])
            for item in ((d13_raw_hide or {}).get("customdata") or [])
            if isinstance(item, (list, tuple)) and len(item) > 3
        ]
        self.assertIn("3", ids_show)
        self.assertNotIn("3", ids_hide)

    def test_statistical_outliers_are_isotope_specific_in_identifier_charts(self) -> None:
        df = sample_processing_df().copy()
        df["Identifier 1"] = "SampleA"
        df["Species"] = "Coral"
        df["Identifier 2"] = ["1", "2", "3", "4"]
        df["Collector Status"] = ""
        df["d13C Cycles Excluded"] = 0
        df["d18O Cycles Excluded"] = 0
        df["d13C Cycles Used"] = 8
        df["d18O Cycles Used"] = 8
        df["d 13C/12C  Mean"] = [1.0, 1.0, 1.0, 1.0]
        df["d 18O/16O  Mean"] = [-3.9, -4.0, -3.8, -1.0]
        df["d13C_calibrated"] = [1.5, 1.6, 1.4, 1.5]
        df["d18O_calibrated"] = [-3.4, -3.5, -3.3, -0.5]
        df["d13C_calibrated_linearity_corrected"] = [1.4, 1.5, 1.3, 1.4]
        df["d18O_calibrated_linearity_corrected"] = [-3.5, -3.6, -3.4, -0.6]

        metadata = {
            "processing": {
                "config": {
                    "selected_identifier": "SampleA",
                    "x_axis_option": "By Identifier 2",
                    "color_param": "Date_ordinal",
                    "z_axis": "1  Cycle Int  Samp  44",
                        "signal_range": [0.0, 50.0],
                        "leak_range": [0.0, 1000.0],
                        "d13c_range": [-100.0, 100.0],
                        "d18o_range": [-100.0, 100.0],
                        "sigma_level_data": 1.0,
                    "overlays": {
                        "show_statistical_outliers": True,
                        "show_range_outliers": False,
                        "show_saturated_collectors": False,
                        "show_saturated_samples": False,
                        "show_failed_samples": False,
                    },
                    "manual_linearity_override": {
                        "enabled": False,
                        "d13_per_10v": 0.0,
                        "d18_per_10v": 0.0,
                    },
                    "export": {
                        "include_outliers": False,
                        "selected_ids": ["All"],
                        "interpolate_outliers": False,
                        "client_name": None,
                        "comment_map": {},
                    },
                }
            },
            "edit_state": {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
            "calibration": {"selected_standards": []},
        }

        workspace = build_processing_workspace("session-1", df, sample_cycles_df(), metadata)
        coral_section = next((section for section in workspace.species_sections if section.species == "Coral"), None)
        self.assertIsNotNone(coral_section)
        figure_set = next((item for item in (coral_section.identifier_figures if coral_section else []) if item.identifier == "SampleA"), None)
        self.assertIsNotNone(figure_set)

        d13_raw = next((trace for trace in (figure_set.d13c.get("data", []) if figure_set else []) if str(trace.get("name", "")).startswith("Raw d13C")), None)
        d18_raw = next((trace for trace in (figure_set.d18o.get("data", []) if figure_set else []) if str(trace.get("name", "")).startswith("Raw d18O")), None)
        self.assertIsNotNone(d13_raw)
        self.assertIsNotNone(d18_raw)

        d13_ids = [str(item[3]) for item in ((d13_raw or {}).get("customdata") or []) if isinstance(item, (list, tuple)) and len(item) > 3]
        d18_ids = [str(item[3]) for item in ((d18_raw or {}).get("customdata") or []) if isinstance(item, (list, tuple)) and len(item) > 3]
        self.assertIn("4", d13_ids)
        self.assertNotIn("4", d18_ids)

        d13_stat = next((trace for trace in (figure_set.d13c.get("data", []) if figure_set else []) if str(trace.get("name", "")) == "Statistical Outliers"), None)
        d18_stat = next((trace for trace in (figure_set.d18o.get("data", []) if figure_set else []) if str(trace.get("name", "")) == "Statistical Outliers"), None)
        self.assertIsNone(d13_stat)
        self.assertIsNotNone(d18_stat)
        d18_stat_ids = [str(item[3]) for item in ((d18_stat or {}).get("customdata") or []) if isinstance(item, (list, tuple)) and len(item) > 3]
        self.assertIn("4", d18_stat_ids)

    def test_identifier_statistical_mask_uses_filtered_population(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["SampleA", "SampleA", "SampleA", "SampleA", "SampleA"],
                "Identifier 2": ["1", "2", "3", "4", "5"],
                "Species": ["Coral", "Coral", "Coral", "Coral", "Coral"],
                "Label": ["SampleA - Coral"] * 5,
                "d 13C/12C  Mean": [1.0, 1.0, 1.0, 1.0, 1.0],
                "d 18O/16O  Mean": [-3.5, -3.55, -3.6, -2.4, -20.0],
                "d 13C/12C  Std Dev": [0.1, 0.1, 0.1, 0.1, 0.1],
                "d 18O/16O  Std Dev": [0.2, 0.2, 0.2, 0.2, 0.2],
                "d13C_calibrated": [1.5, 1.5, 1.5, 1.5, 1.5],
                "d18O_calibrated": [-3.0, -3.05, -3.1, -2.0, -19.5],
                "d13C_calibrated_linearity_corrected": [1.4, 1.4, 1.4, 1.4, 1.4],
                "d18O_calibrated_linearity_corrected": [-3.1, -3.15, -3.2, -2.1, -19.6],
                "1  Cycle Int  Samp  44": [15.0, 15.0, 15.0, 15.0, 15.0],
                "1  Cycle Int  Ref  44": [10.0, 10.0, 10.0, 10.0, 10.0],
                "1  Cycle Int  Diff Samp-Ref  44": [5.0, 5.0, 5.0, 5.0, 5.0],
                "leak_rate": [5.0, 5.0, 5.0, 5.0, 5.0],
                "Collector Status": ["", "", "", "", ""],
                "d13C Cycles Excluded": [0, 0, 0, 0, 0],
                "d18O Cycles Excluded": [0, 0, 0, 0, 0],
                "d13C Cycles Used": [8, 8, 8, 8, 8],
                "d18O Cycles Used": [8, 8, 8, 8, 8],
                "Line": [1, 1, 1, 1, 1],
                "Run ID": ["run-1", "run-1", "run-1", "run-1", "run-1"],
                "Excel File": ["run1.xlsx", "run1.xlsx", "run1.xlsx", "run1.xlsx", "run1.xlsx"],
                "Date": ["2025-01-01"] * 5,
                "Date_ordinal": [739252, 739252, 739252, 739252, 739252],
            }
        )

        metadata = {
            "processing": {
                "config": {
                    "selected_identifier": "SampleA",
                    "x_axis_option": "By Identifier 2",
                    "color_param": "Date_ordinal",
                    "z_axis": "1  Cycle Int  Samp  44",
                    "signal_range": [0.0, 50.0],
                    "leak_range": [0.0, 1000.0],
                    "d13c_range": [-100.0, 100.0],
                    "d18o_range": [-10.0, 10.0],
                    "sigma_level_data": 1.0,
                    "overlays": {
                        "show_statistical_outliers": True,
                        "show_range_outliers": False,
                        "show_saturated_collectors": False,
                        "show_saturated_samples": False,
                        "show_failed_samples": False,
                    },
                    "manual_linearity_override": {
                        "enabled": False,
                        "d13_per_10v": 0.0,
                        "d18_per_10v": 0.0,
                    },
                    "export": {
                        "include_outliers": False,
                        "selected_ids": ["All"],
                        "interpolate_outliers": False,
                        "client_name": None,
                        "comment_map": {},
                    },
                }
            },
            "edit_state": {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
            "calibration": {"selected_standards": []},
        }

        workspace = build_processing_workspace("session-1", df, sample_cycles_df(), metadata)
        coral_section = next((section for section in workspace.species_sections if section.species == "Coral"), None)
        self.assertIsNotNone(coral_section)
        figure_set = next((item for item in (coral_section.identifier_figures if coral_section else []) if item.identifier == "SampleA"), None)
        self.assertIsNotNone(figure_set)

        d18_stat = next((trace for trace in (figure_set.d18o.get("data", []) if figure_set else []) if str(trace.get("name", "")) == "Statistical Outliers"), None)
        self.assertIsNotNone(d18_stat)
        d18_stat_ids = [str(item[3]) for item in ((d18_stat or {}).get("customdata") or []) if isinstance(item, (list, tuple)) and len(item) > 3]
        self.assertIn("4", d18_stat_ids)
        self.assertNotIn("5", d18_stat_ids)

        d18_raw = next((trace for trace in (figure_set.d18o.get("data", []) if figure_set else []) if str(trace.get("name", "")).startswith("Raw d18O")), None)
        self.assertIsNotNone(d18_raw)
        d18_raw_ids = [str(item[3]) for item in ((d18_raw or {}).get("customdata") or []) if isinstance(item, (list, tuple)) and len(item) > 3]
        self.assertNotIn("4", d18_raw_ids)

    def test_build_cycle_diagnostics_payload(self) -> None:
        df = sample_processing_df()
        cycles_df = sample_cycles_df()
        target = build_target_info(df, 0, "d13C", {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}})
        self.assertIsNotNone(target)

        payload = build_cycle_diagnostics_payload(
            session_id="session-1",
            df=df,
            cycles_df=cycles_df,
            target=target,
            config=RangeConfig(),
            edit_state={"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
        )

        self.assertEqual(payload.session_id, "session-1")
        self.assertEqual(payload.target["row_label"], 0)
        self.assertEqual(len(payload.table), 2)
        self.assertEqual(payload.cycle_mean["valid_cycles"], 2)
        self.assertAlmostEqual(float(payload.cycle_mean["valid_std_dev"]), 0.14142135623730953, places=6)
        self.assertIn("reference_gas_intensity", payload.saturation_correction.get("figures", {}))
        self.assertIn("first_cycle", payload.saturation_correction.get("figures", {}))
        self.assertIn("cycle_relative_mismatch", payload.saturation_correction.get("figures", {}))

    def test_build_cycle_diagnostics_payload_uses_first_valid_cycle_for_partially_saturated_target(self) -> None:
        df = sample_processing_df().copy()
        df.loc[0, "Collector Status"] = "Partially Saturated Collectors"
        cycles_df = sample_cycles_df()
        target = build_target_info(df, 0, "d13C", {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}})
        self.assertIsNotNone(target)

        payload = build_cycle_diagnostics_payload(
            session_id="session-1",
            df=df,
            cycles_df=cycles_df,
            target=target,
            config=RangeConfig(),
            edit_state={"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
        )

        self.assertEqual(payload.cycle_mean["method"], "first_valid_cycle")
        self.assertEqual(payload.cycle_mean["reason"], "partially_saturated_use_first_valid_cycle")
        self.assertEqual(int(payload.cycle_mean["selected_cycle"]), 1)
        self.assertAlmostEqual(float(payload.cycle_mean["selected_value"]), 0.9, places=6)
        self.assertAlmostEqual(float(payload.cycle_mean["valid_mean"]), 1.0, places=6)
        self.assertAlmostEqual(float(payload.cycle_mean["valid_std_dev"]), 0.14142135623730953, places=6)
        self.assertAlmostEqual(float(payload.cycle_mean["mean"]), 0.9, places=6)
        first_valid_rows = [row for row in payload.table if bool(row.get("First Valid Cycle"))]
        last_valid_rows = [row for row in payload.table if bool(row.get("Last Valid Cycle"))]
        self.assertEqual(len(first_valid_rows), 1)
        self.assertEqual(len(last_valid_rows), 1)
        self.assertEqual(int(first_valid_rows[0]["Cycle"]), 1)
        self.assertEqual(int(last_valid_rows[0]["Cycle"]), 2)

    def test_build_cycle_diagnostics_payload_adds_saturation_correction_candidates(self) -> None:
        df = sample_processing_df().copy()
        df.loc[0, "Collector Status"] = "Partially Saturated Collectors"
        cycles_df = pd.DataFrame(
            {
                "Cycle Number": ["pre", "1", "2", "3", "4", "5"],
                "Identifier 1": ["SampleA"] * 6,
                "Identifier 2": ["1"] * 6,
                "Species": ["Coral"] * 6,
                "Excel File": ["run1.xlsx"] * 6,
                "Run ID": ["run-1"] * 6,
                "Date": ["2025-01-01"] * 6,
                "d 13C/12C  Mean": [1.0, 30.0, 20.0, 3.0, 2.0, 1.0],
                "d 18O/16O  Mean": [2.0, 2.0, 2.0, 2.0, 2.0, 2.0],
                "Cycle Intensity Samp 44": [15.0, 50.0, 49.0, 30.0, 20.0, 10.0],
                "Cycle Intensity Ref 44": [10.0, 50.0, 49.0, 30.0, 20.0, 10.0],
                "Cycle Intensity Samp 45": [0.5, 50.0, 49.0, 30.0, 20.0, 10.0],
                "Cycle Intensity Ref 45": [0.4, 50.0, 49.0, 30.0, 20.0, 10.0],
            }
        )
        target = build_target_info(df, 0, "d13C", {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}})
        self.assertIsNotNone(target)

        payload = build_cycle_diagnostics_payload(
            session_id="session-1",
            df=df,
            cycles_df=cycles_df,
            target=target,
            config=RangeConfig(),
            edit_state={"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
        )

        self.assertAlmostEqual(float(payload.cycle_mean["last_valid_value"]), 1.0, places=6)
        self.assertAlmostEqual(float(payload.cycle_mean["saturation_reference_gas_value"]), 5.0, places=6)
        self.assertAlmostEqual(float(payload.cycle_mean["saturation_first_cycle_value"]), 1.0, places=6)
        self.assertIn("reference_gas_intensity", payload.saturation_correction.get("figures", {}))
        self.assertIn("first_cycle", payload.saturation_correction.get("figures", {}))
        ref_fig = payload.saturation_correction["figures"]["reference_gas_intensity"]
        first_fig = payload.saturation_correction["figures"]["first_cycle"]
        self.assertNotIn("title", ref_fig.get("layout", {}))
        self.assertNotIn("title", first_fig.get("layout", {}))
        ref_target_trace = next(
            trace
            for trace in ref_fig.get("data", [])
            if trace.get("name") == "Reference-gas prediction"
        )
        first_target_trace = next(
            trace
            for trace in first_fig.get("data", [])
            if trace.get("name") == "Stabilized cycle prediction"
        )
        self.assertEqual(ref_target_trace.get("mode"), "markers")
        self.assertEqual(first_target_trace.get("mode"), "markers")
        self.assertIn("annotations", ref_fig.get("layout", {}))
        self.assertIn("annotations", first_fig.get("layout", {}))

    def test_cycle_diagnostics_uses_signal_ratio_proxy_when_export_repeats_run_mean(self) -> None:
        df = sample_processing_df().copy()
        cycles_df = pd.DataFrame(
            {
                "Cycle Number": ["pre", "1", "2", "3", "4", "5"],
                "Identifier 1": ["SampleA"] * 6,
                "Identifier 2": ["1"] * 6,
                "Species": ["Coral"] * 6,
                "Excel File": ["run1.xlsx"] * 6,
                "Run ID": ["run-1"] * 6,
                "Date": ["2025-01-01"] * 6,
                "d 13C/12C  Mean": [1.0] * 6,
                "d 13C/12C  Std Dev": [0.1] * 6,
                "d 18O/16O  Mean": [2.0] * 6,
                "d 18O/16O  Std Dev": [0.2] * 6,
                "Cycle Intensity Samp 44": [15.0, 15.0, 14.0, 13.0, 12.0, 11.0],
                "Cycle Intensity Ref 44": [10.0] * 6,
                "Cycle Intensity Samp 45": [0.50, 0.50, 0.47, 0.43, 0.39, 0.35],
                "Cycle Intensity Ref 45": [0.40] * 6,
                "Cycle Intensity Samp 46": [0.30, 0.30, 0.28, 0.26, 0.24, 0.22],
                "Cycle Intensity Ref 46": [0.20] * 6,
            }
        )
        target = build_target_info(
            df,
            0,
            "d13C",
            {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
        )
        self.assertIsNotNone(target)

        payload = build_cycle_diagnostics_payload(
            session_id="session-1",
            df=df,
            cycles_df=cycles_df,
            target=target,
            config=RangeConfig(),
            edit_state={"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
        )

        self.assertTrue(bool(payload.cycle_mean["value_source"]["is_proxy"]))
        self.assertGreater(len({round(float(row["d13C"]), 6) for row in payload.table}), 1)
        self.assertTrue(bool(payload.intensity_linearity["available"]))
        self.assertGreater(float(payload.intensity_linearity["issue_index"]), 0.0)
        mean_intensity_figure = payload.saturation_correction["figures"]["cycle_mean_intensity"]
        self.assertIn("internal signal proxy", mean_intensity_figure["layout"]["yaxis"]["title"]["text"])

    def test_cycle_intensity_weighted_mismatch_uses_quadratic_horizontal_target(self) -> None:
        df = sample_processing_df().copy()
        df.loc[2, "Collector Status"] = "Partially Saturated Collectors"
        samples44 = [10.0, 50.0, 49.0, 10.0, 11.0, 12.0, 13.0]
        refs44 = [10.0] * len(samples44)
        samp_median = float(np.median(samples44))
        weighted = [
            10.0 * ((sample - ref) / ref) * (sample / samp_median)
            for sample, ref in zip(samples44[-4:], refs44[-4:])
        ]
        valid_d13 = [5.0 - 0.8 * value + 0.1 * value * value for value in weighted]
        cycles_df = pd.DataFrame(
            {
                "Cycle Number": ["pre", "1", "2", "3", "4", "5", "6"],
                "Identifier 1": ["SampleA"] * 7,
                "Identifier 2": ["3"] * 7,
                "Species": ["Coral"] * 7,
                "Excel File": ["run1.xlsx"] * 7,
                "Run ID": ["run-1"] * 7,
                "Date": ["2025-01-01"] * 7,
                "d 13C/12C  Mean": [2.0, 30.0, 20.0, *valid_d13],
                "d 18O/16O  Mean": [2.0] * 7,
                "Cycle Intensity Samp 44": samples44,
                "Cycle Intensity Ref 44": refs44,
                "Cycle Intensity Samp 45": [0.5, 50.0, 49.0, 0.5, 0.5, 0.5, 0.5],
                "Cycle Intensity Ref 45": [0.4, 50.0, 49.0, 0.4, 0.4, 0.4, 0.4],
            }
        )
        target = build_target_info(df, 2, "d13C", {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}})
        self.assertIsNotNone(target)

        payload = build_cycle_diagnostics_payload(
            session_id="session-1",
            df=df,
            cycles_df=cycles_df,
            target=target,
            config=RangeConfig(),
            edit_state={"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
        )

        method_payload = payload.saturation_correction["cycle_intensity_weighted_mismatch"]
        coeffs = [float(value) for value in method_payload["coefficients"]]
        expected_target = -coeffs[1] / (2.0 * coeffs[0])
        expected_value = float(np.polyval(coeffs, expected_target))
        self.assertAlmostEqual(float(method_payload["target_basis"]), expected_target, places=6)
        self.assertEqual(method_payload["fit_degree"], 2)
        self.assertEqual(method_payload["target_reason"], "quadratic_horizontal")
        self.assertAlmostEqual(float(method_payload["value"]), expected_value, places=6)
        figure = payload.saturation_correction["figures"]["cycle_intensity_weighted_mismatch"]
        curve_trace = next((trace for trace in figure.get("data", []) if trace.get("name") == "Curve fit"), None)
        self.assertIsNotNone(curve_trace)

    def test_cycle_plateau_uses_clustered_latest_valid_cycles(self) -> None:
        df = sample_processing_df().copy()
        df.loc[2, "Collector Status"] = "Partially Saturated Collectors"
        d18_values = [3.60, 3.72, 3.78, 3.83, 3.86, 3.89, 3.900, 3.905, 3.907, 3.908]
        mean44_values = [45.0, 40.0, 32.0, 25.0, 20.0, 17.0, 15.0, 14.0, 13.5, 13.0]
        cycles_df = pd.DataFrame(
            {
                "Cycle Number": ["pre", *[str(value) for value in range(1, 11)]],
                "Identifier 1": ["SampleA"] * 11,
                "Identifier 2": ["3"] * 11,
                "Species": ["Coral"] * 11,
                "Excel File": ["run1.xlsx"] * 11,
                "Run ID": ["run-1"] * 11,
                "Date": ["2025-01-01"] * 11,
                "d 13C/12C  Mean": [2.0, *([1.0] * 10)],
                "d 18O/16O  Mean": [2.4, *d18_values],
                "Cycle Intensity Samp 44": [16.0, *mean44_values],
                "Cycle Intensity Ref 44": [10.0, *mean44_values],
                "Cycle Intensity Samp 45": [0.5, *([0.5] * 10)],
                "Cycle Intensity Ref 45": [0.4, *([0.4] * 10)],
                "Cycle Intensity Samp 46": [0.3, *([0.3] * 10)],
                "Cycle Intensity Ref 46": [0.2, *([0.2] * 10)],
            }
        )
        target = build_target_info(df, 2, "d18O", {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}})
        self.assertIsNotNone(target)

        payload = build_cycle_diagnostics_payload(
            session_id="session-1",
            df=df,
            cycles_df=cycles_df,
            target=target,
            config=RangeConfig(),
            edit_state={"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
        )

        plateau = payload.saturation_correction["cycle_plateau"]
        expected_values = d18_values[-6:]
        expected_delta_changes = np.diff(d18_values)[-6:]
        _, expected_asymptote = np.polyfit(expected_delta_changes, expected_values, 1)
        self.assertEqual(plateau["selected_cycles"], [5, 6, 7, 8, 9, 10])
        self.assertAlmostEqual(float(plateau["value"]), float(expected_asymptote), places=6)
        self.assertAlmostEqual(float(plateau["std_dev"]), float(np.std(expected_values, ddof=1)), places=6)
        self.assertEqual(plateau["reason"], "delta_rate_asymptote")
        self.assertIn("cycle_plateau", payload.saturation_correction.get("figures", {}))

        config = normalize_processing_config(
            {
                "enable_saturation_correction": True,
                "saturation_correction_method": "cycle_plateau",
            }
        )
        work = _derive_working_frame(df, config, cycles_df=cycles_df)
        self.assertAlmostEqual(float(work.loc[2, "d 18O/16O  Mean"]), float(expected_asymptote), places=6)
        self.assertAlmostEqual(float(work.loc[2, "d 18O/16O  Std Dev"]), float(np.std(expected_values, ddof=1)), places=6)
        self.assertEqual(str(work.loc[2, "__d18O_current_method"]), "cycle_plateau")

    def test_derive_working_frame_applies_enabled_saturation_correction_to_unedited_partials(self) -> None:
        df = sample_processing_df().copy()
        cycles_df = pd.DataFrame(
            {
                "Cycle Number": ["pre", "1", "2", "3", "4", "5"],
                "Identifier 1": ["SampleA"] * 6,
                "Identifier 2": ["3"] * 6,
                "Species": ["Coral"] * 6,
                "Excel File": ["run1.xlsx"] * 6,
                "Run ID": ["run-1"] * 6,
                "Date": ["2025-01-01"] * 6,
                "d 13C/12C  Mean": [1.0, 30.0, 20.0, 3.0, 2.0, 1.0],
                "d 18O/16O  Mean": [2.0, 2.0, 2.0, 2.0, 2.0, 2.0],
                "Cycle Intensity Samp 44": [15.0, 50.0, 49.0, 30.0, 20.0, 10.0],
                "Cycle Intensity Ref 44": [10.0, 50.0, 49.0, 30.0, 20.0, 10.0],
                "Cycle Intensity Samp 45": [0.5, 50.0, 49.0, 30.0, 20.0, 10.0],
                "Cycle Intensity Ref 45": [0.4, 50.0, 49.0, 30.0, 20.0, 10.0],
            }
        )
        expected_values = {
            "cycle_mean": 2.0,
            "first_valid_cycle": 3.0,
            "last_valid_cycle": 1.0,
            "reference_gas_intensity": 5.0,
            "first_cycle": 1.0,
        }
        for method, expected_value in expected_values.items():
            with self.subTest(method=method):
                config = normalize_processing_config(
                    {
                        "enable_saturation_correction": True,
                        "saturation_correction_method": method,
                    }
                )

                work = _derive_working_frame(df, config, cycles_df=cycles_df)
                edited_work = _derive_working_frame(
                    df,
                    config,
                    cycles_df=cycles_df,
                    edit_state={"edited_rows": ["2"], "original_delta_values": {}, "manual_outlier_overrides": {}},
                )

                self.assertAlmostEqual(float(work.loc[2, "d 13C/12C  Mean"]), expected_value, places=6)
                self.assertEqual(str(work.loc[2, "__d13C_current_method"]), method)
                if method in {"cycle_mean", "first_valid_cycle"}:
                    self.assertAlmostEqual(float(work.loc[2, "d 13C/12C  Std Dev"]), 1.0, places=6)
                self.assertAlmostEqual(float(edited_work.loc[2, "d 13C/12C  Mean"]), 2.0, places=6)
                self.assertEqual(str(edited_work.loc[2, "__d13C_current_method"]), "edited")

    def test_derive_working_frame_uses_independent_saturation_methods_by_isotope(self) -> None:
        df = sample_processing_df().copy()
        cycles_df = pd.DataFrame(
            {
                "Cycle Number": ["pre", "1", "2", "3", "4", "5"],
                "Identifier 1": ["SampleA"] * 6,
                "Identifier 2": ["3"] * 6,
                "Species": ["Coral"] * 6,
                "Excel File": ["run1.xlsx"] * 6,
                "Run ID": ["run-1"] * 6,
                "Date": ["2025-01-01"] * 6,
                "d 13C/12C  Mean": [1.0, 30.0, 20.0, 3.0, 2.0, 1.0],
                "d 18O/16O  Mean": [2.0, 30.0, 20.0, 6.0, 8.0, 10.0],
                "Cycle Intensity Samp 44": [15.0, 50.0, 49.0, 30.0, 20.0, 10.0],
                "Cycle Intensity Ref 44": [10.0, 50.0, 49.0, 30.0, 20.0, 10.0],
                "Cycle Intensity Samp 45": [0.5, 50.0, 49.0, 30.0, 20.0, 10.0],
                "Cycle Intensity Ref 45": [0.4, 50.0, 49.0, 30.0, 20.0, 10.0],
                "Cycle Intensity Samp 46": [0.3, 50.0, 49.0, 30.0, 20.0, 10.0],
                "Cycle Intensity Ref 46": [0.2, 50.0, 49.0, 30.0, 20.0, 10.0],
            }
        )
        config = normalize_processing_config(
            {
                "enable_saturation_correction": True,
                "saturation_correction_method_d13": "last_valid_cycle",
                "saturation_correction_method_d18": "first_valid_cycle",
            }
        )

        work = _derive_working_frame(df, config, cycles_df=cycles_df)

        self.assertAlmostEqual(float(work.loc[2, "d 13C/12C  Mean"]), 1.0, places=6)
        self.assertEqual(str(work.loc[2, "__d13C_current_method"]), "last_valid_cycle")
        self.assertAlmostEqual(float(work.loc[2, "d 18O/16O  Mean"]), 6.0, places=6)
        self.assertEqual(str(work.loc[2, "__d18O_current_method"]), "first_valid_cycle")

    def test_build_cycle_diagnostics_payload_uses_first_valid_cycle_for_partially_saturated_target_even_with_many_valid_cycles(self) -> None:
        df = sample_processing_df().copy()
        df.loc[0, "Collector Status"] = "Partially Saturated Collectors"
        cycles_df = pd.DataFrame(
            {
                "Cycle Number": ["pre", "1", "2", "3", "4", "5", "6"],
                "Identifier 1": ["SampleA"] * 7,
                "Identifier 2": ["1"] * 7,
                "Species": ["Coral"] * 7,
                "Excel File": ["run1.xlsx"] * 7,
                "Run ID": ["run-1"] * 7,
                "Date": ["2025-01-01"] * 7,
                "d 13C/12C  Mean": [1.0, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6],
                "d 18O/16O  Mean": [2.0, 2.0, 2.05, 2.1, 2.15, 2.2, 2.25],
                "Cycle Intensity Samp 44": [15.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
                "Cycle Intensity Ref 44": [10.0, 9.8, 9.8, 9.8, 9.8, 9.8, 9.8],
                "Cycle Intensity Samp 45": [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
                "Cycle Intensity Ref 45": [0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],
                "Cycle Intensity Samp 46": [0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],
                "Cycle Intensity Ref 46": [0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],
            }
        )
        target = build_target_info(df, 0, "d13C", {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}})
        self.assertIsNotNone(target)

        payload = build_cycle_diagnostics_payload(
            session_id="session-1",
            df=df,
            cycles_df=cycles_df,
            target=target,
            config=RangeConfig(),
            edit_state={"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
        )

        self.assertEqual(int(payload.cycle_mean["valid_cycles"]), 6)
        self.assertEqual(payload.cycle_mean["method"], "first_valid_cycle")
        self.assertEqual(payload.cycle_mean["reason"], "partially_saturated_use_first_valid_cycle")
        self.assertEqual(int(payload.cycle_mean["selected_cycle"]), 1)
        self.assertAlmostEqual(float(payload.cycle_mean["selected_value"]), 0.6, places=6)
        self.assertAlmostEqual(float(payload.cycle_mean["valid_mean"]), 1.1, places=6)
        self.assertAlmostEqual(float(payload.cycle_mean["valid_std_dev"]), 0.37416573867739417, places=6)
        self.assertAlmostEqual(float(payload.cycle_mean["mean"]), 0.6, places=6)
        first_valid_rows = [row for row in payload.table if bool(row.get("First Valid Cycle"))]
        last_valid_rows = [row for row in payload.table if bool(row.get("Last Valid Cycle"))]
        self.assertEqual(len(first_valid_rows), 1)
        self.assertEqual(len(last_valid_rows), 1)
        self.assertEqual(int(first_valid_rows[0]["Cycle"]), 1)
        self.assertEqual(int(last_valid_rows[0]["Cycle"]), 6)

    def test_build_cycle_diagnostics_payload_stops_before_sample_gas_escape(self) -> None:
        df = sample_processing_df().copy()
        df.loc[0, "Collector Status"] = "Partially Saturated Collectors"
        cycles_df = pd.DataFrame(
            {
                "Cycle Number": ["pre", "1", "2", "3", "4", "5", "6"],
                "Identifier 1": ["SampleA"] * 7,
                "Identifier 2": ["1"] * 7,
                "Species": ["Coral"] * 7,
                "Excel File": ["run1.xlsx"] * 7,
                "Run ID": ["run-1"] * 7,
                "Date": ["2025-01-01"] * 7,
                "d 13C/12C  Mean": [1.0, 1.0, 2.0, 3.0, 4.0, 99.0, 88.0],
                "d 18O/16O  Mean": [2.0, 2.0, 2.05, 2.1, 2.15, 9.0, 8.0],
                "Cycle Intensity Samp 44": [15.0, 20.0, 19.0, 18.0, 17.0, 0.0, 12.0],
                "Cycle Intensity Ref 44": [10.0, 10.1, 10.0, 10.0, 9.9, 10.0, 9.8],
                "Cycle Intensity Samp 45": [0.5, 2.0, 2.1, 2.0, 1.8, 0.0, 1.0],
                "Cycle Intensity Ref 45": [0.4, 1.8, 1.8, 1.8, 1.8, 1.8, 1.8],
                "Cycle Intensity Samp 46": [0.3, 0.8, 0.8, 0.8, 0.8, 0.0, 0.5],
                "Cycle Intensity Ref 46": [0.2, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6],
            }
        )
        target = build_target_info(df, 0, "d13C", {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}})
        self.assertIsNotNone(target)

        payload = build_cycle_diagnostics_payload(
            session_id="session-1",
            df=df,
            cycles_df=cycles_df,
            target=target,
            config=RangeConfig(),
            edit_state={"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
        )

        self.assertEqual(int(payload.cycle_mean["valid_cycles"]), 4)
        self.assertEqual(int(payload.cycle_mean["last_valid_cycle"]), 4)
        self.assertAlmostEqual(float(payload.cycle_mean["last_valid_value"]), 4.0, places=6)
        self.assertAlmostEqual(float(payload.saturation_correction["last_valid_value"]), 4.0, places=6)
        first_valid_rows = [row for row in payload.table if bool(row.get("First Valid Cycle"))]
        last_valid_rows = [row for row in payload.table if bool(row.get("Last Valid Cycle"))]
        escaped_rows = [row for row in payload.table if bool(row.get("Excluded (Sample Gas Escape)"))]
        self.assertEqual(len(first_valid_rows), 1)
        self.assertEqual(len(last_valid_rows), 1)
        self.assertEqual(int(first_valid_rows[0]["Cycle"]), 1)
        self.assertEqual(int(last_valid_rows[0]["Cycle"]), 4)
        self.assertEqual([int(row["Cycle"]) for row in escaped_rows], [5, 6])

    def test_build_cycle_diagnostics_payload_uses_column_order_for_unlabeled_duplicate_masses(self) -> None:
        df = sample_processing_df()
        cycles_df = sample_cycles_df().rename(
            columns={
                "Cycle Intensity Samp 44": "Sample Intensities Sample Intensity 44.00 m/z",
                "Cycle Intensity Ref 44": "Standard Intensities Standard Intensity 44.00 m/z",
                "Cycle Intensity Samp 45": "45.00 m/z",
                "Cycle Intensity Ref 45": "45.00 m/z__dup2",
                "Cycle Intensity Samp 46": "46.00 m/z",
                "Cycle Intensity Ref 46": "46.00 m/z__dup2",
            }
        )
        cycles_df["Sample Intensities Sample Intensity 44.00 m/z"] = [1.55, 1.51, 1.48]
        cycles_df["Standard Intensities Standard Intensity 44.00 m/z"] = [3.00, 2.88, 2.77]
        cycles_df["45.00 m/z"] = [1.69, 1.64, 1.60]
        cycles_df["45.00 m/z__dup2"] = [3.20, 3.07, 2.95]
        cycles_df["46.00 m/z"] = [2.23, 2.17, 2.11]
        cycles_df["46.00 m/z__dup2"] = [4.27, 4.10, 3.94]

        target = build_target_info(df, 0, "d13C", {"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}})
        self.assertIsNotNone(target)

        payload = build_cycle_diagnostics_payload(
            session_id="session-1",
            df=df,
            cycles_df=cycles_df,
            target=target,
            config=RangeConfig(),
            edit_state={"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}},
        )

        self.assertEqual(len(payload.table), 2)
        first_row = payload.table[0]
        self.assertAlmostEqual(float(first_row["SMP Int m/z 45 (V)"]), 1.64, places=6)
        self.assertAlmostEqual(float(first_row["REF Int m/z 45 (V)"]), 3.07, places=6)
        self.assertAlmostEqual(float(first_row["SMP Int m/z 46 (V)"]), 2.17, places=6)
        self.assertAlmostEqual(float(first_row["REF Int m/z 46 (V)"]), 4.10, places=6)

    def test_statistical_outliers_include_rows_with_missing_species(self) -> None:
        df = pd.DataFrame(
            {
                "Identifier 1": ["A", "A", "A", "B", "B", "B", "B"],
                "Identifier 2": ["1", "2", "3", "1", "2", "3", "4"],
                "Species": ["Coral", "Coral", "Coral", np.nan, np.nan, np.nan, np.nan],
                "Label": ["A - Coral", "A - Coral", "A - Coral", "B - Shell", "B - Shell", "B - Shell", "B - Shell"],
                "d 13C/12C  Mean": [1.0, 1.1, 0.9, 1.0, 1.0, 1.1, 1.0],
                "d 18O/16O  Mean": [-4.0, -4.1, -3.9, -4.0, -4.0, -4.1, -1.7],
                "1  Cycle Int  Samp  44": [15.0] * 7,
                "leak_rate": [5.0] * 7,
                "Collector Status": [""] * 7,
                "d13C Cycles Excluded": [0] * 7,
                "d18O Cycles Excluded": [0] * 7,
            }
        )

        masks = build_category_masks(df, RangeConfig(), edit_state={"edited_rows": [], "original_delta_values": {}, "manual_outlier_overrides": {}}, sigma_level=1.0)
        # Identifier B rows have missing Species, but should still be grouped (Label/Identifier fallback)
        # and detect Identifier 2 == "4" as statistical outlier.
        self.assertTrue(bool(masks["Statistical"].loc[6]))


if __name__ == "__main__":
    unittest.main()
