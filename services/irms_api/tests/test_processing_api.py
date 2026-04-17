from __future__ import annotations

import io
import tempfile
import unittest

import pandas as pd
from openpyxl import load_workbook

from services.irms_api.api import main as api_main
from services.irms_api.domain.contracts import CycleDiagnosticsRequest, EditAction, ExportRequest
from services.irms_api.session_store import FileSessionStore


def sample_processing_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Identifier 1": ["SampleA", "SampleA", "SampleA", "SampleB"],
            "Identifier 2": ["1", "2", "3", "1"],
            "Species": ["Coral", "Coral", "Coral", "Shell"],
            "Label": ["SampleA - Coral", "SampleA - Coral", "SampleA - Coral", "SampleB - Shell"],
            "Comment": ["ok", "range", "partial", "failed"],
            "d 13C/12C  Mean": [1.0, 50.0, 2.0, 3.0],
            "d 18O/16O  Mean": [2.0, 2.2, 2.4, 1.5],
            "d 13C/12C  Std Dev": [0.10, 0.10, 0.15, 0.20],
            "d 18O/16O  Std Dev": [0.20, 0.20, 0.25, 0.25],
            "d13C_calibrated": [1.5, 50.5, 2.5, 3.5],
            "d18O_calibrated": [2.5, 2.7, 2.9, 2.0],
            "d13C_calibrated_linearity_corrected": [1.4, 50.4, 2.4, 3.4],
            "d18O_calibrated_linearity_corrected": [2.4, 2.6, 2.8, 1.9],
            "1  Cycle Int  Samp  44": [15.0, 15.5, 16.0, 12.0],
            "1  Cycle Int  Ref  44": [10.0, 10.0, 10.0, 10.0],
            "1  Cycle Int  Diff Samp-Ref  44": [5.0, 5.5, 6.0, 2.0],
            "leak_rate": [5.0, 5.0, 5.0, 5.0],
            "Collector Status": ["", "", "Partially Saturated Collectors", ""],
            "d13C Cycles Excluded": [0, 0, 1, 0],
            "d18O Cycles Excluded": [0, 0, 0, 0],
            "d13C Cycles Used": [8, 8, 7, 8],
            "d18O Cycles Used": [8, 8, 6, 8],
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


def processing_config_payload() -> dict[str, object]:
    return {
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
            "show_manual_outliers": False,
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


class ProcessingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_store = api_main.store
        api_main.store = FileSessionStore(self.temp_dir.name)
        self.session_id = api_main.store.create_session()
        metadata = api_main.store.load_metadata(self.session_id)
        metadata["processing"] = {"config": processing_config_payload()}
        metadata["calibration"] = {
            "selected_standards": [],
            "coefficients": {"d13C": {"slope": 1.0, "intercept": 1.0}},
            "linearity_fits": {},
        }
        api_main.store.write_metadata(self.session_id, metadata)
        api_main.store.save_frames(self.session_id, sample_processing_df(), sample_cycles_df())

    def tearDown(self) -> None:
        api_main.store = self.original_store
        self.temp_dir.cleanup()

    def test_processing_workspace_and_edit_endpoint(self) -> None:
        workspace = api_main.processing_workspace(self.session_id)
        self.assertEqual(workspace.session_id, self.session_id)
        self.assertIn("processing_3d", workspace.overview_figures)
        self.assertGreaterEqual(len(workspace.species_sections), 1)

        edit_response = api_main.edit_processing(
            self.session_id,
            EditAction(action="set_value", targets=[{"row_label": "0", "isotope_key": "d13C"}], value=8.0),
        )
        self.assertEqual(edit_response.session_id, self.session_id)
        updated_df = api_main.store.load_frame(self.session_id)
        self.assertEqual(float(updated_df.loc[0, "d 13C/12C  Mean"]), 8.0)
        self.assertEqual(float(updated_df.loc[0, "d13C_calibrated"]), 9.0)

    def test_calibrated_traces_include_point_customdata_for_selection_editing(self) -> None:
        workspace = api_main.processing_workspace(self.session_id)

        d13_summary = workspace.overview_figures.get("d13_summary", {})
        summary_calibrated = next(
            (
                trace
                for trace in d13_summary.get("data", [])
                if str(trace.get("name", "")).startswith("Calibrated d13C")
            ),
            None,
        )
        self.assertIsNotNone(summary_calibrated)
        summary_customdata = summary_calibrated.get("customdata")
        self.assertIsInstance(summary_customdata, list)
        self.assertGreater(len(summary_customdata), 0)
        first_summary_point = summary_customdata[0]
        self.assertIsInstance(first_summary_point, list)
        self.assertEqual(str(first_summary_point[1]), "d13C")

        figure_set = next(
            (
                item
                for section in workspace.species_sections
                for item in section.identifier_figures
                if item.has_calibrated_d13c
            ),
            None,
        )
        self.assertIsNotNone(figure_set)
        identifier_calibrated = next(
            (
                trace
                for trace in figure_set.d13c.get("data", [])
                if str(trace.get("name", "")).startswith("Calibrated d13C")
            ),
            None,
        )
        self.assertIsNotNone(identifier_calibrated)
        identifier_customdata = identifier_calibrated.get("customdata")
        self.assertIsInstance(identifier_customdata, list)
        self.assertGreater(len(identifier_customdata), 0)
        self.assertEqual(len(identifier_customdata), len(identifier_calibrated.get("y", [])))

    def test_remove_processing_calibration_drops_applied_columns_only(self) -> None:
        workspace = api_main.remove_processing_calibration(self.session_id)
        self.assertEqual(workspace.session_id, self.session_id)

        updated_df = api_main.store.load_frame(self.session_id)
        self.assertNotIn("d13C_calibrated", updated_df.columns)
        self.assertNotIn("d18O_calibrated", updated_df.columns)
        self.assertNotIn("d13C_calibrated_linearity_corrected", updated_df.columns)
        self.assertNotIn("d18O_calibrated_linearity_corrected", updated_df.columns)

        metadata = api_main.store.load_metadata(self.session_id)
        self.assertIn("coefficients", metadata.get("calibration", {}))
        self.assertFalse(bool(metadata.get("processing", {}).get("apply_calibration", True)))

        has_any_calibrated = any(
            figure_set.has_calibrated_d13c or figure_set.has_calibrated_d18o
            for section in workspace.species_sections
            for figure_set in section.identifier_figures
        )
        self.assertFalse(has_any_calibrated)

    def test_interpolate_uses_linearity_corrected_processing_domain(self) -> None:
        df = sample_processing_df().copy()
        df.loc[0, "d 13C/12C  Mean"] = 1.0
        df.loc[1, "d 13C/12C  Mean"] = 99.0
        df.loc[2, "d 13C/12C  Mean"] = 5.0
        df.loc[0, "1  Cycle Int  Samp  44"] = 15.0
        df.loc[1, "1  Cycle Int  Samp  44"] = 15.1
        df.loc[2, "1  Cycle Int  Samp  44"] = 18.0
        api_main.store.save_frames(self.session_id, df, sample_cycles_df())

        metadata = api_main.store.load_metadata(self.session_id)
        metadata["calibration"] = {
            **metadata.get("calibration", {}),
            "config": {"linearity": {"apply": True, "use_diff_intensity": False}},
            "linearity_fits": {
                "d13C": {"slope": 3.0, "x_ref": 15.0, "intercept": 0.0, "n": 3},
                "d18O": {"slope": 0.0, "x_ref": 15.0, "intercept": 0.0, "n": 3},
                "intensity_col": "1  Cycle Int  Samp  44",
            },
        }
        api_main.store.write_metadata(self.session_id, metadata)

        api_main.edit_processing(
            self.session_id,
            EditAction(action="interpolate", targets=[{"row_label": "1", "isotope_key": "d13C"}]),
        )

        updated_df = api_main.store.load_frame(self.session_id)
        # Raw persistence is mapped from corrected-domain interpolation back to stored raw values.
        self.assertAlmostEqual(float(updated_df.loc[1, "d 13C/12C  Mean"]), -1.2, places=6)

    def test_interpolate_missing_d13_uses_target_specific_line_offset_mapping(self) -> None:
        df = sample_processing_df().copy()
        df = df.iloc[:3].copy()
        df["Identifier 1"] = ["861", "861", "861"]
        df["Species"] = ["Uvigerina", "Uvigerina", "Uvigerina"]
        df["Identifier 2"] = [1, 2, 3]
        df["Line"] = [1, 2, 2]
        df["Collector Status"] = ["", "Failed Sample", ""]
        df["d 13C/12C  Mean"] = [1.0, float("nan"), 3.0]
        df["d 18O/16O  Mean"] = [2.0, float("nan"), 4.0]
        api_main.store.save_frames(self.session_id, df, sample_cycles_df())

        metadata = api_main.store.load_metadata(self.session_id)
        metadata["calibration"] = {
            **metadata.get("calibration", {}),
            "config": {
                "linearity": {
                    "apply": False,
                    "use_diff_intensity": False,
                    "line_1_offset_d13": 2.0,
                    "line_2_offset_d13": -2.0,
                    "line_1_offset_d18": 0.0,
                    "line_2_offset_d18": 0.0,
                }
            },
            "linearity_fits": {},
        }
        api_main.store.write_metadata(self.session_id, metadata)

        api_main.edit_processing(
            self.session_id,
            EditAction(action="interpolate", targets=[{"row_label": "1", "isotope_key": "d13C"}]),
        )

        updated_df = api_main.store.load_frame(self.session_id)
        # Source-domain neighbors become 3.0 (x=1) and 1.0 (x=3), so interpolated source at x=2 is 2.0.
        # Target row is line 2 with d13 offset -2.0, therefore persisted raw should be 4.0.
        self.assertAlmostEqual(float(updated_df.loc[1, "d 13C/12C  Mean"]), 4.0, places=6)

    def test_cycle_diagnostics_and_export_endpoints(self) -> None:
        diagnostics = api_main.processing_cycle_diagnostics(
            self.session_id,
            CycleDiagnosticsRequest(target={"row_label": "0", "isotope_key": "d13C"}),
        )
        self.assertEqual(len(diagnostics.table), 2)
        self.assertEqual(diagnostics.target["row_label"], 0)

        export_without_outliers = api_main.export_dataset(
            self.session_id,
            ExportRequest(
                include_outliers=False,
                selected_ids=["All"],
                interpolate_outliers=False,
                client_name="Client A",
                comment_map={"Coral": "Porites"},
                output_type="dataset",
            ),
        )
        workbook_without = pd.ExcelFile(io.BytesIO(export_without_outliers.body))
        self.assertIn("Data", workbook_without.sheet_names)
        self.assertIn("Outliers", workbook_without.sheet_names)
        self.assertIn("Statistics", workbook_without.sheet_names)
        self.assertNotIn("Client Output", workbook_without.sheet_names)
        data_without = pd.read_excel(io.BytesIO(export_without_outliers.body), sheet_name="Data")
        self.assertIn("3", set(data_without["Identifier 2"].astype(str)))

        export_client_output = api_main.export_dataset(
            self.session_id,
            ExportRequest(
                include_outliers=False,
                selected_ids=["All"],
                interpolate_outliers=False,
                client_name="Client A",
                comment_map={"Coral": "Porites"},
                output_type="client_output",
            ),
        )
        client_workbook = pd.ExcelFile(io.BytesIO(export_client_output.body))
        self.assertEqual(client_workbook.sheet_names, ["Client Output"])
        client_sheet = pd.read_excel(io.BytesIO(export_client_output.body), sheet_name="Client Output")
        self.assertIn("Species", client_sheet.columns)
        self.assertIn("Sequence", client_sheet.columns)
        self.assertIn("Porites", set(client_sheet["Species"].astype(str)))
        self.assertEqual(client_sheet["Identifier"].notna().sum(), 3)
        sample_values = set(client_sheet["Sample #"].dropna().astype(str))
        self.assertNotIn("range", sample_values)
        self.assertEqual(sample_values, {"ok", "partial", "failed"})
        self.assertEqual(client_sheet["Corrected d13C (‰, VPDB)"].notna().sum(), 3)
        self.assertEqual(client_sheet["Corrected d18O (‰, VPDB)"].notna().sum(), 3)
        disposition_headers = {key.lower(): value for key, value in export_client_output.headers.items()}
        self.assertIn("content-disposition", disposition_headers)
        self.assertIn("Client A", disposition_headers["content-disposition"])
        self.assertIn("stable C&O isotopes P2L", disposition_headers["content-disposition"])
        duplicate_check = api_main.check_client_output_duplicates(
            self.session_id,
            ExportRequest(
                include_outliers=False,
                selected_ids=["All"],
                interpolate_outliers=False,
                client_name="Client A",
                comment_map={"Coral": "Porites"},
                output_type="client_output",
            ),
        )
        self.assertEqual(duplicate_check.duplicate_row_count, 2)
        self.assertEqual(
            {str(value) for value in duplicate_check.duplicate_identifier2_values},
            {"1"},
        )
        self.assertEqual(duplicate_check.duplicate_sequence_values, [])
        self.assertEqual(len(duplicate_check.duplicate_rows), 2)

        workbook_with_styles = load_workbook(io.BytesIO(export_client_output.body))
        worksheet = workbook_with_styles["Client Output"]
        self.assertGreater(len(list(worksheet.conditional_formatting)), 0)

        export_client_output_capped = api_main.export_dataset(
            self.session_id,
            ExportRequest(
                include_outliers=False,
                selected_ids=["All"],
                interpolate_outliers=False,
                client_name="Client A",
                comment_map={"Coral": "Porites"},
                restore_stdev=True,
                restore_stdev_cap=0.18,
                output_type="client_output",
            ),
        )
        capped_client_sheet = pd.read_excel(io.BytesIO(export_client_output_capped.body), sheet_name="Client Output")
        d13_std_col = next((col for col in capped_client_sheet.columns if str(col).startswith("d13C") and "Std Dev" in str(col)), None)
        d18_std_col = next((col for col in capped_client_sheet.columns if str(col).startswith("d18O") and "Std Dev" in str(col)), None)
        self.assertIsNotNone(d13_std_col)
        self.assertIsNotNone(d18_std_col)
        self.assertLessEqual(float(capped_client_sheet[d13_std_col].max()), 0.18)
        self.assertLessEqual(float(capped_client_sheet[d18_std_col].max()), 0.18)

        export_with_interpolation = api_main.export_dataset(
            self.session_id,
            ExportRequest(
                include_outliers=True,
                selected_ids=["All"],
                interpolate_outliers=True,
                client_name="Client B",
                comment_map={"Coral": "Porites"},
                output_type="dataset",
            ),
        )
        workbook_with = pd.ExcelFile(io.BytesIO(export_with_interpolation.body))
        data_sheet = pd.read_excel(io.BytesIO(export_with_interpolation.body), sheet_name="Data")
        self.assertIn("Outlier Types", data_sheet.columns)
        self.assertIn("Original d 13C/12C  Mean", data_sheet.columns)
        self.assertIn("Statistics", workbook_with.sheet_names)

    def test_client_output_precision_uses_full_working_frame(self) -> None:
        df = sample_processing_df()
        shp_rows = df.iloc[[0, 0]].copy()
        shp_rows["Identifier 1"] = ["SHP2L", "SHP2L"]
        shp_rows["Identifier 2"] = ["900", "901"]
        shp_rows["Comment"] = ["SHP2L-900", "SHP2L-901"]
        shp_rows["d 13C/12C  Mean"] = [0.10, 0.40]
        shp_rows["d 18O/16O  Mean"] = [1.10, 1.70]
        shp_rows["d 13C/12C  Std Dev"] = [0.05, 0.05]
        shp_rows["d 18O/16O  Std Dev"] = [0.05, 0.05]
        merged = pd.concat([df, shp_rows], ignore_index=True)
        api_main.store.save_frames(self.session_id, merged, sample_cycles_df())

        export_client_output = api_main.export_dataset(
            self.session_id,
            ExportRequest(
                include_outliers=False,
                selected_ids=["SampleA"],
                interpolate_outliers=False,
                client_name="Client A",
                comment_map={"Coral": "Porites"},
                output_type="client_output",
            ),
        )

        sheet = pd.read_excel(io.BytesIO(export_client_output.body), sheet_name="Client Output", header=None)
        text_cells = {str(value) for value in sheet.to_numpy().ravel() if isinstance(value, str) and value.strip()}
        self.assertIn("d13C n=2, d18O n=2", text_cells)
        self.assertNotIn("0.00 ‰ for d13C", text_cells)
        self.assertNotIn("0.00 ‰ for d18O", text_cells)


    def test_client_output_precision_matches_calibration_workspace_linearity_mode(self) -> None:
        df = sample_processing_df()
        shp_rows = df.iloc[[0, 0, 0, 0]].copy()
        shp_rows["Identifier 1"] = ["SHP2L", "SHP2L", "SHP2L", "SHP2L"]
        shp_rows["Identifier 2"] = ["900", "901", "902", "903"]
        shp_rows["Comment"] = ["SHP2L-900", "SHP2L-901", "SHP2L-902", "SHP2L-903"]
        shp_rows["1  Cycle Int  Samp  44"] = [10.0, 20.0, 30.0, 40.0]
        shp_rows["d 13C/12C  Mean"] = [1.00, 2.08, 2.92, 4.03]
        shp_rows["d 18O/16O  Mean"] = [-5.00, -4.10, -3.05, -2.02]
        merged = pd.concat([df, shp_rows], ignore_index=True)
        api_main.store.save_frames(self.session_id, merged, sample_cycles_df())

        metadata = api_main.store.load_metadata(self.session_id)
        metadata.setdefault("calibration", {})
        metadata["calibration"]["selected_standards"] = ["SHP2L"]
        metadata["calibration"]["config"] = {
            "selected_standards": ["SHP2L"],
            "linearity": {
                "apply": True,
                "intensity_col": "1  Cycle Int  Samp  44",
                "use_diff_intensity": False,
            },
        }
        api_main.store.write_metadata(self.session_id, metadata)

        calibration_workspace = api_main.build_calibration_workspace(self.session_id, merged, metadata)
        shp_summary = next(
            summary for summary in calibration_workspace.precision_summaries if str(summary.standard).strip().upper() == "SHP2L"
        )
        expected_d13 = (
            shp_summary.d13_linearity_corrected_precision
            if calibration_workspace.config.linearity.apply and shp_summary.d13_linearity_corrected_precision is not None
            else shp_summary.d13_precision
        )
        expected_d18 = (
            shp_summary.d18_linearity_corrected_precision
            if calibration_workspace.config.linearity.apply and shp_summary.d18_linearity_corrected_precision is not None
            else shp_summary.d18_precision
        )
        self.assertIsNotNone(expected_d13)
        self.assertIsNotNone(expected_d18)

        export_client_output = api_main.export_dataset(
            self.session_id,
            ExportRequest(
                include_outliers=False,
                selected_ids=["SampleA"],
                interpolate_outliers=False,
                client_name="Client A",
                comment_map={"Coral": "Porites"},
                output_type="client_output",
            ),
        )

        sheet = pd.read_excel(io.BytesIO(export_client_output.body), sheet_name="Client Output", header=None)
        text_cells = [str(value) for value in sheet.to_numpy().ravel() if isinstance(value, str) and value.strip()]
        d13_line = next((cell for cell in text_cells if "for d13C" in cell), "")
        d18_line = next((cell for cell in text_cells if "for d18O" in cell), "")
        n_line = next((cell for cell in text_cells if cell.startswith("d13C n=")), "")
        self.assertTrue(d13_line.startswith(f"{float(expected_d13):.2f}"))
        self.assertTrue(d18_line.startswith(f"{float(expected_d18):.2f}"))
        self.assertEqual(n_line, f"d13C n={int(shp_summary.included_d13)}, d18O n={int(shp_summary.included_d18)}")

    def test_client_output_matches_chart_scoped_statistical_filtering(self) -> None:
        df = sample_processing_df().copy()
        df["Identifier 1"] = ["SampleA", "SampleA", "SampleA", "SampleA"]
        df["Identifier 2"] = ["1", "2", "3", "4"]
        df["Species"] = ["Coral", "Coral", "Coral", "Coral"]
        df["Label"] = ["SampleA - Coral", "SampleA - Coral", "SampleA - Coral", "SampleA - Coral"]
        df["Comment"] = ["base_1", "base_2", "stat_only", "range_only"]
        df["d 13C/12C  Mean"] = [0.0, 0.0, 0.0, 0.0]
        df["d 18O/16O  Mean"] = [0.0, 0.1, 0.3, 50.0]
        df["d13C_calibrated"] = [0.0, 0.0, 0.0, 0.0]
        df["d18O_calibrated"] = [0.0, 0.1, 0.3, 50.0]
        df["d13C_calibrated_linearity_corrected"] = [0.0, 0.0, 0.0, 0.0]
        df["d18O_calibrated_linearity_corrected"] = [0.0, 0.1, 0.3, 50.0]
        df["Collector Status"] = ["", "", "", ""]
        api_main.store.save_frames(self.session_id, df, sample_cycles_df())

        metadata = api_main.store.load_metadata(self.session_id)
        cfg = dict(metadata.get("processing", {}).get("config", {}))
        cfg["sigma_level_data"] = 1.0
        cfg["d18o_range"] = [-1.0, 1.0]
        metadata.setdefault("processing", {})
        metadata["processing"]["config"] = cfg
        api_main.store.write_metadata(self.session_id, metadata)

        workspace = api_main.processing_workspace(self.session_id)
        figure_set = workspace.species_sections[0].identifier_figures[0]
        raw_d18_trace = next(
            trace
            for trace in figure_set.d18o.get("data", [])
            if str(trace.get("name", "")).startswith("Raw d18O")
        )
        chart_row_labels = {
            str(point[0])
            for point in raw_d18_trace.get("customdata", [])
            if isinstance(point, list) and point
        }
        # row label "2" is "stat_only"; it should be filtered from the chart base.
        self.assertNotIn("2", chart_row_labels)

        export_client_output = api_main.export_dataset(
            self.session_id,
            ExportRequest(
                include_outliers=False,
                selected_ids=["All"],
                interpolate_outliers=False,
                client_name="Client A",
                comment_map={"Coral": "Porites"},
                output_type="client_output",
            ),
        )
        client_sheet = pd.read_excel(io.BytesIO(export_client_output.body), sheet_name="Client Output")
        sample_values = set(client_sheet["Sample #"].dropna().astype(str))
        self.assertNotIn("range_only", sample_values)
        self.assertNotIn("stat_only", sample_values)
        self.assertIn("base_1", sample_values)
        self.assertIn("base_2", sample_values)


if __name__ == "__main__":
    unittest.main()
