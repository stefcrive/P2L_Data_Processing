from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from services.irms_api.domain.contracts import EditAction
from services.irms_api.domain.processing.core import (
    RangeConfig,
    _interpolate_outliers_by_identifier2,
    _range_outlier_mask,
    build_category_masks,
)
from services.irms_api.domain.processing.cycles import build_cycle_diagnostics_payload, build_target_info
from services.irms_api.domain.processing.edits import apply_edit_action
from services.irms_api.domain.processing.workspace import build_processing_workspace


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
