from __future__ import annotations

import io
import tempfile
import unittest

import pandas as pd

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
        self.assertIn("Porites", set(client_sheet["Species"].astype(str)))
        self.assertEqual(client_sheet["Identifier"].notna().sum(), len(sample_processing_df()))
        disposition_headers = {key.lower(): value for key, value in export_client_output.headers.items()}
        self.assertIn("content-disposition", disposition_headers)
        self.assertIn("Client A BTS Stable C&O isosopes results P2L", disposition_headers["content-disposition"])

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


if __name__ == "__main__":
    unittest.main()
