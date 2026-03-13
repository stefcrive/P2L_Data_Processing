from __future__ import annotations

import tempfile
import unittest

import pandas as pd
from fastapi import HTTPException

from services.irms_api.api import main as api_main
from services.irms_api.domain.contracts import CalibrationConfig
from services.irms_api.session_store import FileSessionStore


def sample_calibration_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Identifier 1": ["SHP2L", "SHP2L", "SHP2L", "NBS19", "NBS19", "NBS19", "SampleA", "SampleA"],
            "Identifier 2": ["1", "2", "3", "1", "2", "3", "1", "2"],
            "Species": ["Std", "Std", "Std", "Std", "Std", "Std", "Coral", "Coral"],
            "Label": [
                "SHP2L-1",
                "SHP2L-2",
                "SHP2L-3",
                "NBS19-1",
                "NBS19-2",
                "NBS19-3",
                "SampleA-1",
                "SampleA-2",
            ],
            "Comment": ["std", "std", "std", "std", "std", "std", "sample", "sample"],
            "d 13C/12C  Mean": [-0.80, -0.78, -0.74, 1.90, 2.00, 1.97, 0.50, 0.55],
            "d 18O/16O  Mean": [-5.90, -5.70, -5.65, -2.40, -2.25, -2.15, -4.10, -4.00],
            "1  Cycle Int  Samp  44": [15.0, 15.4, 15.8, 13.8, 14.2, 14.6, 14.0, 14.4],
            "1  Cycle Int  Ref  44": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
            "1  Cycle Int  Diff Samp-Ref  44": [5.0, 5.4, 5.8, 3.8, 4.2, 4.6, 4.0, 4.4],
            "leak_rate": [5.0, 4.8, 5.1, 5.3, 5.2, 5.1, 4.9, 5.0],
            "Line": [1, 1, 2, 1, 1, 2, 1, 2],
            "Date": [
                "2025-01-01",
                "2025-01-02",
                "2025-01-03",
                "2025-01-01",
                "2025-01-02",
                "2025-01-03",
                "2025-01-04",
                "2025-01-05",
            ],
            "Date_ordinal": [739252, 739253, 739254, 739252, 739253, 739254, 739255, 739256],
        }
    )


class CalibrationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_store = api_main.store
        api_main.store = FileSessionStore(self.temp_dir.name)
        self.session_id = api_main.store.create_session()
        api_main.store.save_frames(self.session_id, sample_calibration_df(), pd.DataFrame())

    def tearDown(self) -> None:
        api_main.store = self.original_store
        self.temp_dir.cleanup()

    def test_preview_workspace_uses_request_config_without_persisting(self) -> None:
        preview = api_main.calibration_workspace_preview(
            self.session_id,
            CalibrationConfig(
                selected_standards=["SHP2L", "NBS19"],
                calibration_type="IQR",
                sigma_level=1.0,
                iqr_multiplier=1.5,
                independent_isotope_outliers=True,
                color_param="Date_ordinal",
                z_axis="1  Cycle Int  Diff Samp-Ref  44",
                precision_date_range=("2025-01-01", "2025-01-03"),
            ),
        )
        self.assertEqual(preview.config.selected_standards, ["SHP2L", "NBS19"])
        self.assertIn("VPDB(13C)", preview.figures)
        self.assertIn("calibration_3d", preview.figures)
        self.assertEqual(len(preview.precision_summaries), 2)
        self.assertEqual(len(preview.standard_sections), 2)
        self.assertTrue(preview.model_dump_json())

        metadata = api_main.store.load_metadata(self.session_id)
        self.assertEqual(metadata.get("calibration", {}), {})

    def test_run_calibration_persists_results_and_workspace_reflects_saved_state(self) -> None:
        snapshot = api_main.run_calibration(
            self.session_id,
            CalibrationConfig(
                selected_standards=["SHP2L", "NBS19"],
                calibration_type="Z-Score",
                sigma_level=1.0,
                iqr_multiplier=1.5,
                independent_isotope_outliers=True,
                color_param="Date_ordinal",
                z_axis="1  Cycle Int  Samp  44",
                precision_date_range=("2025-01-01", "2025-01-03"),
                linearity={
                    "apply": True,
                    "use_diff_intensity": False,
                    "manual_override_enabled": False,
                    "manual_d13_per_10v": 0.0,
                    "manual_d18_per_10v": 0.0,
                },
            ),
        )
        self.assertEqual(snapshot.session_id, self.session_id)

        stored_df = api_main.store.load_frame(self.session_id)
        self.assertIn("d13C_calibrated", stored_df.columns)
        self.assertIn("d18O_calibrated", stored_df.columns)
        self.assertIn("d13C_calibrated_linearity_corrected", stored_df.columns)
        self.assertIn("d18O_calibrated_linearity_corrected", stored_df.columns)

        metadata = api_main.store.load_metadata(self.session_id)
        self.assertEqual(metadata["calibration"]["selected_standards"], ["SHP2L", "NBS19"])
        self.assertIn("coefficients", metadata["calibration"])
        self.assertIn("linearity_fits", metadata["calibration"])

        workspace = api_main.calibration_workspace(self.session_id)
        self.assertEqual(workspace.config.selected_standards, ["SHP2L", "NBS19"])
        self.assertIn("d13_raw", workspace.linearity_figures)
        self.assertGreaterEqual(len(workspace.precision_summaries), 2)

    def test_run_calibration_rejects_invalid_standard_count(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            api_main.run_calibration(
                self.session_id,
                CalibrationConfig(
                    selected_standards=["SHP2L", "NBS19", "NBS18"],
                    calibration_type="IQR",
                ),
            )
        self.assertEqual(str(ctx.exception.detail), "Please select either one or two standards for calibration.")


if __name__ == "__main__":
    unittest.main()
