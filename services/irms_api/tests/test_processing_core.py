from __future__ import annotations

import base64
import unittest

import numpy as np
import pandas as pd

from services.irms_api.domain.constants import CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL
from services.irms_api.domain.contracts import EditAction
from services.irms_api.domain.processing.core import (
    RangeConfig,
    _interpolate_outliers_by_identifier2,
    _range_outlier_mask,
    build_category_masks,
)
from services.irms_api.domain.processing.cycles import build_cycle_diagnostics_payload, build_target_info
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

    def test_apply_edit_action_interpolate_uses_raw_domain_values(self) -> None:
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

        # Neighbors are selected by the interpolation source frame, but interpolation value remains raw-domain.
        self.assertAlmostEqual(float(updated_df.loc[1, "d 13C/12C  Mean"]), 3.0, places=6)

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
        self.assertIn(CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL, workspace.available_values.color_params)
        self.assertIn(CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL, workspace.available_values.z_axis_options)

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

        self.assertAlmostEqual(float(sample_intensity_work.loc[0, "d 13C/12C  Mean"]), 1.025, places=6)
        self.assertAlmostEqual(float(diff_intensity_work.loc[0, "d 13C/12C  Mean"]), 1.1081967213, places=6)

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

        self.assertGreater(float(work.loc[0, "d 13C/12C  Mean"]), float(work.loc[1, "d 13C/12C  Mean"]))
        self.assertAlmostEqual(float(work.loc[2, "d 13C/12C  Mean"]), 1.4508196721, places=6)

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

        self.assertAlmostEqual(float(linear_work.loc[0, "d 13C/12C  Mean"]), 1.025, places=6)
        self.assertAlmostEqual(float(quadratic_work.loc[0, "d 13C/12C  Mean"]), 1.075625, places=6)
        self.assertNotEqual(float(linear_work.loc[0, "d 13C/12C  Mean"]), float(quadratic_work.loc[0, "d 13C/12C  Mean"]))

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

    def test_derive_working_frame_recalibrates_after_modifications_for_non_outliers_only(self) -> None:
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
        self.assertTrue(pd.isna(work.loc[1, "d13C_calibrated"]))
        self.assertTrue(pd.isna(work.loc[1, "d13C_calibrated_linearity_corrected"]))
        self.assertTrue(pd.isna(work.loc[2, "d13C_calibrated"]))
        self.assertTrue(pd.isna(work.loc[2, "d13C_calibrated_linearity_corrected"]))

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
