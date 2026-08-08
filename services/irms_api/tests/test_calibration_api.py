from __future__ import annotations

import os
import tempfile
import time
import unittest
import base64
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import HTTPException

from services.irms_api.api import main as api_main
from services.irms_api.domain.calibration.core import convert_d18o_carbonate_material
from services.irms_api.domain.calibration.workspace import _sequence_axis
from services.irms_api.domain.constants import (
    CYCLE1_SIGNAL_REF44_COL,
    CYCLE1_SIGNAL_SAMP44_COL,
    ISOTYPE_D13C,
    VALID_CYCLES_COL,
)
from services.irms_api.domain.contracts import CalibrationConfig, CalibrationOfficialValueUpsertRequest, LinearityConfig
from services.irms_api.session_store import FileSessionStore


def wait_for_calibration_job(job_id: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = api_main.job_registry.get(job_id)
        if snapshot.state in {"succeeded", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"Calibration job {job_id} did not finish")


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
            "d13C Cycles Used": [8, 8, 7, 8, 8, 7, 8, 8],
            "d18O Cycles Used": [8, 7, 7, 8, 7, 6, 8, 7],
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
        self.original_standards_db_path = os.getenv("IRMS_STANDARDS_DB_PATH")
        self.original_standards_csv_path = os.getenv("IRMS_STANDARDS_CSV_PATH")
        standards_db_path = Path(self.temp_dir.name) / "standards.db"
        standards_csv_path = Path(self.temp_dir.name) / "standards.csv"
        standards_csv_path.write_text(
            "\n".join(
                [
                    "Standard,Isotopic_Value_Type,Value",
                    "SHP2L,VPDB(13C),-0.77",
                    "SHP2L,VSMOW(18O),-5.75",
                    "NBS18,VPDB(13C),-5.01",
                    "NBS18,VSMOW(18O),-23.01",
                    "NBS19,VPDB(13C),1.95",
                    "NBS19,VSMOW(18O),-2.2",
                ]
            ),
            encoding="utf-8",
        )
        os.environ["IRMS_STANDARDS_DB_PATH"] = str(standards_db_path)
        os.environ["IRMS_STANDARDS_CSV_PATH"] = str(standards_csv_path)
        self.original_store = api_main.store
        api_main.store = FileSessionStore(self.temp_dir.name)
        self.session_id = api_main.store.create_session()
        api_main.store.save_frames(self.session_id, sample_calibration_df(), pd.DataFrame())

    def tearDown(self) -> None:
        api_main.store = self.original_store
        if self.original_standards_db_path is None:
            os.environ.pop("IRMS_STANDARDS_DB_PATH", None)
        else:
            os.environ["IRMS_STANDARDS_DB_PATH"] = self.original_standards_db_path
        if self.original_standards_csv_path is None:
            os.environ.pop("IRMS_STANDARDS_CSV_PATH", None)
        else:
            os.environ["IRMS_STANDARDS_CSV_PATH"] = self.original_standards_csv_path
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
        self.assertEqual(len(preview.selected_standard_official_values), 4)
        outlier_colorbar = preview.standard_sections[0].d13_figure["layout"]["coloraxis"]["colorbar"]
        self.assertEqual(outlier_colorbar["title"]["side"], "right")
        self.assertIn(VALID_CYCLES_COL, preview.available_values.color_params)
        self.assertIn(CYCLE1_SIGNAL_SAMP44_COL, preview.available_values.color_params)
        self.assertIn(CYCLE1_SIGNAL_REF44_COL, preview.available_values.color_params)
        shpl2_summary = next(item for item in preview.precision_summaries if item.standard == "SHP2L")
        self.assertIn("1", shpl2_summary.line_precisions)
        line1 = shpl2_summary.line_precisions["1"]
        self.assertIn("d13_linearity_corrected_precision", line1)
        self.assertIn("d18_linearity_corrected_precision", line1)
        self.assertTrue(preview.model_dump_json())

        metadata = api_main.store.load_metadata(self.session_id)
        self.assertEqual(metadata.get("calibration", {}), {})

    def test_available_standards_include_dataset_identifiers_and_database_materials(self) -> None:
        preview = api_main.calibration_workspace_preview(
            self.session_id,
            CalibrationConfig(),
        )

        self.assertIn("SampleA", preview.available_values.standards)
        self.assertIn("NBS18", preview.available_values.standards)
        self.assertEqual(preview.available_values.standards.count("SHP2L"), 1)

    def test_standard_sequence_axis_keeps_repeated_alphanumeric_identifiers_visible(self) -> None:
        rows = pd.DataFrame(
            {
                "Identifier 2": ["CaCO3", "CaCO3", "NBS-19", "NBS-18", "IAEA-CO-8"],
            }
        )

        self.assertEqual(_sequence_axis(rows).tolist(), [1.0, 2.0, 3.0, 4.0, 5.0])

    def test_import_identifier_alias_is_used_for_calibration_without_rewriting_snapshot(self) -> None:
        source_df = sample_calibration_df()
        source_df.loc[source_df["Identifier 1"] == "SHP2L", "Identifier 1"] = "Reference batch"
        api_main.store.save_frames(self.session_id, source_df, pd.DataFrame())
        metadata = api_main.store.load_metadata(self.session_id)
        metadata["processing"] = {
            "config": {
                "identifier1_name_map": {"Reference batch": "SHP2L"},
            }
        }
        api_main.store.write_metadata(self.session_id, metadata)

        config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
        )
        preview = api_main.calibration_workspace_preview(self.session_id, config)

        shp2l_summary = next(item for item in preview.precision_summaries if item.standard == "SHP2L")
        self.assertEqual(shp2l_summary.total_rows, 3)

        api_main.run_calibration(self.session_id, config)
        stored_df = api_main.store.load_frame(self.session_id)
        self.assertEqual(
            int((stored_df["Identifier 1"].astype(str) == "Reference batch").sum()),
            3,
        )
        reference_rows = stored_df["Identifier 1"].astype(str) == "Reference batch"
        self.assertTrue(stored_df.loc[reference_rows, "d13C_calibrated"].isna().all())
        self.assertTrue(stored_df.loc[reference_rows, "d18O_calibrated"].isna().all())

    def test_calibration_background_job_persists_and_returns_snapshot(self) -> None:
        submitted = api_main.submit_calibration_job(
            self.session_id,
            CalibrationConfig(
                selected_standards=["SHP2L", "NBS19"],
                calibration_type="IQR",
                sigma_level=1.0,
                iqr_multiplier=1.5,
                independent_isotope_outliers=True,
                color_param="Date_ordinal",
                z_axis="1  Cycle Int  Samp  44",
            ),
        )
        completed = wait_for_calibration_job(submitted.job_id)

        self.assertEqual(completed.state, "succeeded", completed.error)
        self.assertEqual(completed.result["session_id"], self.session_id)
        metadata = api_main.store.load_metadata(self.session_id)
        self.assertEqual(metadata["calibration"]["selected_standards"], ["SHP2L", "NBS19"])

    def test_set_calibration_linearity_config_persists_shared_linearity_parameters(self) -> None:
        api_main.run_calibration(
            self.session_id,
            CalibrationConfig(
                selected_standards=["SHP2L", "NBS19"],
                calibration_type="IQR",
                sigma_level=1.0,
                iqr_multiplier=1.5,
                independent_isotope_outliers=True,
                color_param="Date_ordinal",
                z_axis="1  Cycle Int  Samp  44",
                precision_date_range=("2025-01-01", "2025-01-03"),
                linearity={"apply": True, "use_diff_intensity": False},
            ),
        )
        baseline_metadata = api_main.store.load_metadata(self.session_id)
        baseline_coeffs = baseline_metadata.get("calibration", {}).get("coefficients", {})

        updated_workspace = api_main.set_calibration_linearity_config(
            self.session_id,
            LinearityConfig(
                apply=True,
                intensity_col="1  Cycle Int  Diff Samp-Ref  44",
                use_diff_intensity=True,
                quadratic=True,
                max_sample_intensity=None,
                line_1_offset_d13=0.11,
                line_1_offset_d18=-0.22,
                manual_d13_per_10v2=0.33,
                manual_d18_per_10v2=-0.44,
            ),
        )

        self.assertTrue(updated_workspace.config.linearity.apply)
        self.assertTrue(updated_workspace.config.linearity.use_diff_intensity)
        self.assertTrue(updated_workspace.config.linearity.quadratic)
        self.assertEqual(updated_workspace.config.linearity.intensity_col, "1  Cycle Int  Diff Samp-Ref  44")
        self.assertAlmostEqual(float(updated_workspace.config.linearity.line_1_offset_d13 or 0.0), 0.11, places=6)
        self.assertAlmostEqual(float(updated_workspace.config.linearity.manual_d13_per_10v2 or 0.0), 0.33, places=6)

        metadata = api_main.store.load_metadata(self.session_id)
        stored_linearity = metadata.get("calibration", {}).get("config", {}).get("linearity", {})
        self.assertTrue(bool(stored_linearity.get("apply")))
        self.assertTrue(bool(stored_linearity.get("use_diff_intensity")))
        self.assertTrue(bool(stored_linearity.get("quadratic")))
        self.assertEqual(stored_linearity.get("intensity_col"), "1  Cycle Int  Diff Samp-Ref  44")
        stored_fits = metadata.get("calibration", {}).get("linearity_fits", {})
        self.assertEqual(stored_fits.get("intensity_col"), "1  Cycle Int  Diff Samp-Ref  44")
        stored_coeffs = metadata.get("calibration", {}).get("coefficients", {})
        self.assertTrue(bool(stored_coeffs))
        self.assertIn("d13C", stored_coeffs)
        self.assertIn("d18O", stored_coeffs)
        self.assertNotEqual(stored_coeffs, baseline_coeffs)

    def test_preview_workspace_manual_linearity_override_does_not_update_measurements_when_disabled(self) -> None:
        base_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": False,
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )
        baseline = api_main.calibration_workspace_preview(self.session_id, base_config)

        override_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": False,
                "use_diff_intensity": False,
                "manual_override_enabled": True,
                "manual_d13_per_10v": 3.0,
                "manual_d18_per_10v": -2.0,
            },
        )
        overridden = api_main.calibration_workspace_preview(self.session_id, override_config)

        base_summary = next(item for item in baseline.precision_summaries if item.standard == "SHP2L")
        override_summary = next(item for item in overridden.precision_summaries if item.standard == "SHP2L")
        self.assertEqual(base_summary.d13_average, override_summary.d13_average)
        self.assertEqual(base_summary.d18_average, override_summary.d18_average)

    def test_preview_workspace_manual_linearity_override_changes_corrected_precision_when_linearity_is_applied(self) -> None:
        base_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": True,
                "intensity_col": "1  Cycle Int  Pressure-Weighted Mismatch Samp-Ref  44",
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )
        override_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": True,
                "intensity_col": "1  Cycle Int  Pressure-Weighted Mismatch Samp-Ref  44",
                "use_diff_intensity": False,
                "manual_override_enabled": True,
                "manual_d13_per_10v": -0.1,
                "manual_d18_per_10v": 0.0,
            },
        )

        baseline = api_main.calibration_workspace_preview(self.session_id, base_config)
        overridden = api_main.calibration_workspace_preview(self.session_id, override_config)
        base_summary = next(item for item in baseline.precision_summaries if item.standard == "SHP2L")
        override_summary = next(item for item in overridden.precision_summaries if item.standard == "SHP2L")
        self.assertNotAlmostEqual(
            float(base_summary.d13_linearity_corrected_precision or 0.0),
            float(override_summary.d13_linearity_corrected_precision or 0.0),
            places=9,
        )

    def test_preview_workspace_linearity_apply_toggle_does_not_change_standard_precision(self) -> None:
        base_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": False,
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )
        apply_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
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
        )

        baseline = api_main.calibration_workspace_preview(self.session_id, base_config)
        applied = api_main.calibration_workspace_preview(self.session_id, apply_config)

        base_summary = next(item for item in baseline.precision_summaries if item.standard == "SHP2L")
        apply_summary = next(item for item in applied.precision_summaries if item.standard == "SHP2L")
        self.assertEqual(base_summary.d13_precision, apply_summary.d13_precision)
        self.assertEqual(base_summary.d18_precision, apply_summary.d18_precision)
        self.assertEqual(base_summary.d13_average, apply_summary.d13_average)
        self.assertEqual(base_summary.d18_average, apply_summary.d18_average)

    def test_preview_workspace_linearity_apply_uses_corrected_standard_values_in_calibration_charts(self) -> None:
        base_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": False,
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )
        apply_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
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
        )

        baseline = api_main.calibration_workspace_preview(self.session_id, base_config)
        applied = api_main.calibration_workspace_preview(self.session_id, apply_config)

        def parse_numeric_vector(payload: object) -> list[float]:
            if isinstance(payload, list):
                values: list[float] = []
                for item in payload:
                    try:
                        values.append(float(item))
                    except (TypeError, ValueError):
                        continue
                return values
            if isinstance(payload, dict) and "bdata" in payload:
                dtype_name = str(payload.get("dtype", "f8"))
                encoded = str(payload.get("bdata", ""))
                if not encoded:
                    return []
                try:
                    decoded = base64.b64decode(encoded)
                    return np.frombuffer(decoded, dtype=np.dtype(dtype_name)).astype(float).tolist()
                except Exception:
                    return []
            return []

        def extract_row_to_y(figure: dict) -> dict[str, float]:
            rows: dict[str, float] = {}
            for trace in figure.get("data", []):
                y_values = parse_numeric_vector(trace.get("y"))
                customdata = trace.get("customdata")
                if not y_values or not isinstance(customdata, list):
                    continue
                for idx, payload in enumerate(customdata):
                    if not isinstance(payload, list) or not payload or idx >= len(y_values):
                        continue
                    row_label = str(payload[0]).strip()
                    if not row_label:
                        continue
                    try:
                        rows[row_label] = float(y_values[idx])
                    except (TypeError, ValueError):
                        continue
            return rows

        for isotope_key in ("VPDB(13C)", "VSMOW(18O)"):
            baseline_rows = extract_row_to_y(baseline.figures.get(isotope_key, {}))
            applied_rows = extract_row_to_y(applied.figures.get(isotope_key, {}))
            shared_labels = sorted(set(baseline_rows).intersection(applied_rows))
            self.assertTrue(shared_labels, f"Expected shared plotted rows for {isotope_key}")
            self.assertTrue(
                any(abs(applied_rows[label] - baseline_rows[label]) > 1e-9 for label in shared_labels),
                f"Expected at least one corrected plotted value change for {isotope_key}",
            )

    def test_preview_workspace_applies_linearity_before_outlier_detection(self) -> None:
        trend_df = pd.DataFrame(
            {
                "Identifier 1": ["SHP2L", "SHP2L", "SHP2L", "SHP2L"],
                "Identifier 2": ["1", "2", "3", "4"],
                "Species": ["Std", "Std", "Std", "Std"],
                "Label": ["SHP2L-1", "SHP2L-2", "SHP2L-3", "SHP2L-4"],
                "Comment": ["std", "std", "std", "std"],
                "d 13C/12C  Mean": [0.0, 10.0, 20.0, 30.0],
                "d 18O/16O  Mean": [-5.0, -5.0, -5.0, -5.0],
                "1  Cycle Int  Samp  44": [10.0, 15.0, 20.0, 25.0],
                "1  Cycle Int  Ref  44": [10.0, 10.0, 10.0, 10.0],
                "1  Cycle Int  Diff Samp-Ref  44": [0.0, 5.0, 10.0, 15.0],
                "Date": ["2025-01-01", "2025-01-01", "2025-01-01", "2025-01-01"],
                "Date_ordinal": [739252, 739252, 739252, 739252],
            }
        )
        api_main.store.save_frames(self.session_id, trend_df, pd.DataFrame())

        base_config = CalibrationConfig(
            selected_standards=["SHP2L"],
            calibration_type="Z-Score",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            linearity={
                "apply": False,
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )
        apply_config = CalibrationConfig(
            selected_standards=["SHP2L"],
            calibration_type="Z-Score",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            linearity={
                "apply": True,
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )

        baseline = api_main.calibration_workspace_preview(self.session_id, base_config)
        applied = api_main.calibration_workspace_preview(self.session_id, apply_config)

        base_summary = next(item for item in baseline.precision_summaries if item.standard == "SHP2L")
        apply_summary = next(item for item in applied.precision_summaries if item.standard == "SHP2L")
        self.assertEqual(base_summary.included_d13, 2)
        self.assertEqual(apply_summary.included_d13, 4)

        base_section = next(item for item in baseline.standard_sections if item.standard == "SHP2L")
        apply_section = next(item for item in applied.standard_sections if item.standard == "SHP2L")
        self.assertEqual(len(base_section.d13_outliers), 2)
        self.assertEqual(len(apply_section.d13_outliers), 0)
        self.assertNotEqual(base_section.d13_figure.get("data"), apply_section.d13_figure.get("data"))

    def test_preview_workspace_linearity_basis_selector_updates_correction_basis(self) -> None:
        config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            linearity={
                "apply": True,
                "intensity_col": "1  Cycle Int  Pressure-Weighted Mismatch Samp-Ref  44",
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )

        preview = api_main.calibration_workspace_preview(self.session_id, config)
        self.assertEqual(
            preview.linearity_fits.get("intensity_col"),
            "1  Cycle Int  Pressure-Weighted Mismatch Samp-Ref  44",
        )
        d13_layout = preview.linearity_figures.get("d13_raw", {}).get("layout", {})
        self.assertIn(
            "1  Cycle Int  Pressure-Weighted Mismatch Samp-Ref  44",
            str(((d13_layout.get("xaxis") or {}).get("title") or {}).get("text", "")),
        )

    def test_preview_workspace_linearity_max_sample_intensity_filters_sample_basis_fits(self) -> None:
        baseline = api_main.calibration_workspace_preview(
            self.session_id,
            CalibrationConfig(
                selected_standards=["SHP2L", "NBS19"],
                calibration_type="IQR",
                sigma_level=1.0,
                iqr_multiplier=1.5,
                independent_isotope_outliers=True,
                color_param="Date_ordinal",
                z_axis="1  Cycle Int  Samp  44",
                precision_date_range=("2025-01-01", "2025-01-03"),
                linearity={
                    "apply": True,
                    "intensity_col": "1  Cycle Int  Samp  44",
                    "use_diff_intensity": False,
                    "manual_override_enabled": False,
                    "manual_d13_per_10v": 0.0,
                    "manual_d18_per_10v": 0.0,
                },
            ),
        )
        filtered = api_main.calibration_workspace_preview(
            self.session_id,
            CalibrationConfig(
                selected_standards=["SHP2L", "NBS19"],
                calibration_type="IQR",
                sigma_level=1.0,
                iqr_multiplier=1.5,
                independent_isotope_outliers=True,
                color_param="Date_ordinal",
                z_axis="1  Cycle Int  Samp  44",
                precision_date_range=("2025-01-01", "2025-01-03"),
                linearity={
                    "apply": True,
                    "intensity_col": "1  Cycle Int  Samp  44",
                    "use_diff_intensity": False,
                    "max_sample_intensity": 15.2,
                    "manual_override_enabled": False,
                    "manual_d13_per_10v": 0.0,
                    "manual_d18_per_10v": 0.0,
                },
            ),
        )

        baseline_d13_n = int((baseline.linearity_fits.get("d13C", {}) or {}).get("n", 0))
        baseline_d18_n = int((baseline.linearity_fits.get("d18O", {}) or {}).get("n", 0))
        filtered_d13_n = int((filtered.linearity_fits.get("d13C", {}) or {}).get("n", 0))
        filtered_d18_n = int((filtered.linearity_fits.get("d18O", {}) or {}).get("n", 0))
        self.assertEqual(baseline_d13_n, 6)
        self.assertEqual(baseline_d18_n, 6)
        self.assertEqual(filtered_d13_n, 4)
        self.assertEqual(filtered_d18_n, 4)

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

    def test_run_calibration_applies_selected_carbonate_material_to_d18o(self) -> None:
        calcite_session = api_main.store.create_session()
        aragonite_session = api_main.store.create_session()
        api_main.store.save_frames(calcite_session, sample_calibration_df(), pd.DataFrame())
        api_main.store.save_frames(aragonite_session, sample_calibration_df(), pd.DataFrame())

        base_config = {
            "selected_standards": ["SHP2L", "NBS19"],
            "calibration_type": "IQR",
            "sigma_level": 1.0,
            "iqr_multiplier": 1.5,
            "independent_isotope_outliers": True,
            "color_param": "Date_ordinal",
            "z_axis": "1  Cycle Int  Samp  44",
        }
        api_main.run_calibration(
            calcite_session,
            CalibrationConfig(**base_config, carbonate_material="calcite"),
        )
        api_main.run_calibration(
            aragonite_session,
            CalibrationConfig(**base_config, carbonate_material="aragonite"),
        )

        calcite_df = api_main.store.load_frame(calcite_session)
        aragonite_df = api_main.store.load_frame(aragonite_session)
        sample_mask = calcite_df["Identifier 1"].astype(str).eq("SampleA")
        calcite_d18 = pd.to_numeric(
            calcite_df.loc[sample_mask, "d18O_calibrated"],
            errors="coerce",
        )
        aragonite_d18 = pd.to_numeric(
            aragonite_df.loc[sample_mask, "d18O_calibrated"],
            errors="coerce",
        )
        expected_d18 = calcite_d18.map(
            lambda value: convert_d18o_carbonate_material(value, target_material="aragonite")
        )
        pd.testing.assert_series_equal(
            aragonite_d18.reset_index(drop=True),
            expected_d18.reset_index(drop=True),
        )

        calcite_d13 = pd.to_numeric(
            calcite_df.loc[sample_mask, "d13C_calibrated"],
            errors="coerce",
        )
        aragonite_d13 = pd.to_numeric(
            aragonite_df.loc[sample_mask, "d13C_calibrated"],
            errors="coerce",
        )
        pd.testing.assert_series_equal(
            aragonite_d13.reset_index(drop=True),
            calcite_d13.reset_index(drop=True),
        )
        metadata = api_main.store.load_metadata(aragonite_session)
        self.assertEqual(metadata["calibration"]["config"]["carbonate_material"], "aragonite")

    def test_run_calibration_honors_selected_linearity_basis(self) -> None:
        baseline_session = api_main.store.create_session()
        selector_session = api_main.store.create_session()
        api_main.store.save_frames(baseline_session, sample_calibration_df(), pd.DataFrame())
        api_main.store.save_frames(selector_session, sample_calibration_df(), pd.DataFrame())

        baseline_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            linearity={
                "apply": True,
                "intensity_col": "1  Cycle Int  Samp  44",
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )
        selector_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            linearity={
                "apply": True,
                "intensity_col": "1  Cycle Int  Pressure-Weighted Mismatch Samp-Ref  44",
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )

        api_main.run_calibration(baseline_session, baseline_config)
        api_main.run_calibration(selector_session, selector_config)

        baseline_meta = api_main.store.load_metadata(baseline_session)
        selector_meta = api_main.store.load_metadata(selector_session)
        self.assertEqual(
            baseline_meta.get("calibration", {}).get("linearity_fits", {}).get("intensity_col"),
            "1  Cycle Int  Samp  44",
        )
        self.assertEqual(
            selector_meta.get("calibration", {}).get("linearity_fits", {}).get("intensity_col"),
            "1  Cycle Int  Pressure-Weighted Mismatch Samp-Ref  44",
        )

        baseline_df = api_main.store.load_frame(baseline_session)
        selector_df = api_main.store.load_frame(selector_session)
        self.assertFalse(
            pd.to_numeric(baseline_df.get("d13C_calibrated"), errors="coerce").equals(
                pd.to_numeric(selector_df.get("d13C_calibrated"), errors="coerce")
            )
        )
        self.assertFalse(
            pd.to_numeric(baseline_df.get("d18O_calibrated"), errors="coerce").equals(
                pd.to_numeric(selector_df.get("d18O_calibrated"), errors="coerce")
            )
        )

    def test_run_calibration_linearity_max_sample_intensity_filters_sample_basis_fits(self) -> None:
        baseline_session = api_main.store.create_session()
        filtered_session = api_main.store.create_session()
        api_main.store.save_frames(baseline_session, sample_calibration_df(), pd.DataFrame())
        api_main.store.save_frames(filtered_session, sample_calibration_df(), pd.DataFrame())

        baseline_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": True,
                "intensity_col": "1  Cycle Int  Samp  44",
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )
        filtered_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": True,
                "intensity_col": "1  Cycle Int  Samp  44",
                "use_diff_intensity": False,
                "max_sample_intensity": 15.2,
                "manual_override_enabled": False,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )

        api_main.run_calibration(baseline_session, baseline_config)
        api_main.run_calibration(filtered_session, filtered_config)

        baseline_fits = api_main.store.load_metadata(baseline_session).get("calibration", {}).get("linearity_fits", {})
        filtered_fits = api_main.store.load_metadata(filtered_session).get("calibration", {}).get("linearity_fits", {})
        self.assertEqual(int((baseline_fits.get("d13C", {}) or {}).get("n", 0)), 6)
        self.assertEqual(int((baseline_fits.get("d18O", {}) or {}).get("n", 0)), 6)
        self.assertEqual(int((filtered_fits.get("d13C", {}) or {}).get("n", 0)), 4)
        self.assertEqual(int((filtered_fits.get("d18O", {}) or {}).get("n", 0)), 4)

    def test_reset_calibration_clears_metadata_and_derived_columns(self) -> None:
        api_main.run_calibration(
            self.session_id,
            CalibrationConfig(
                selected_standards=["SHP2L", "NBS19"],
                calibration_type="IQR",
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

        snapshot = api_main.reset_calibration(self.session_id)
        self.assertEqual(snapshot.session_id, self.session_id)
        metadata = api_main.store.load_metadata(self.session_id)
        self.assertEqual(metadata.get("calibration"), {})

        stored_df = api_main.store.load_frame(self.session_id)
        for removed in (
            "d13C_calibrated",
            "d18O_calibrated",
            "d13C_calibrated_linearity_corrected",
            "d18O_calibrated_linearity_corrected",
            "d13C_linearity_corrected",
            "d18O_linearity_corrected",
        ):
            self.assertNotIn(removed, stored_df.columns)

        for raw_col in ("d 13C/12C  Mean", "d 18O/16O  Mean"):
            self.assertIn(raw_col, stored_df.columns)

    def test_run_calibration_keeps_standard_rows_uncalibrated_and_raw_values_unchanged(self) -> None:
        baseline_df = api_main.store.load_frame(self.session_id).copy()
        api_main.run_calibration(
            self.session_id,
            CalibrationConfig(
                selected_standards=["SHP2L", "NBS19"],
                calibration_type="IQR",
                sigma_level=1.0,
                iqr_multiplier=1.5,
                independent_isotope_outliers=True,
                color_param="Date_ordinal",
                z_axis="1  Cycle Int  Samp  44",
                precision_date_range=("2025-01-01", "2025-01-03"),
                linearity={
                    "apply": True,
                    "use_diff_intensity": False,
                    "manual_override_enabled": True,
                    "manual_d13_per_10v": 3.0,
                    "manual_d18_per_10v": -2.0,
                },
            ),
        )
        stored_df = api_main.store.load_frame(self.session_id)
        standards_mask = stored_df["Identifier 1"].astype(str).isin({"SHP2L", "NBS19"})
        samples_mask = ~standards_mask
        self.assertTrue(bool(standards_mask.any()))
        self.assertTrue(bool(samples_mask.any()))

        for raw_col in ("d 13C/12C  Mean", "d 18O/16O  Mean"):
            baseline_raw = pd.to_numeric(baseline_df.loc[standards_mask, raw_col], errors="coerce").reset_index(drop=True)
            stored_raw = pd.to_numeric(stored_df.loc[standards_mask, raw_col], errors="coerce").reset_index(drop=True)
            pd.testing.assert_series_equal(stored_raw, baseline_raw, check_names=False)

        for cal_col in (
            "d13C_calibrated",
            "d18O_calibrated",
            "d13C_calibrated_linearity_corrected",
            "d18O_calibrated_linearity_corrected",
        ):
            self.assertIn(cal_col, stored_df.columns)
            self.assertTrue(pd.to_numeric(stored_df.loc[standards_mask, cal_col], errors="coerce").isna().all())

        self.assertTrue(pd.to_numeric(stored_df.loc[samples_mask, "d13C_calibrated"], errors="coerce").notna().any())
        self.assertTrue(pd.to_numeric(stored_df.loc[samples_mask, "d18O_calibrated"], errors="coerce").notna().any())

    def test_run_calibration_with_isotope_line_offsets_keeps_raw_measurements_unchanged(self) -> None:
        baseline_df = api_main.store.load_frame(self.session_id).copy()
        api_main.run_calibration(
            self.session_id,
            CalibrationConfig(
                selected_standards=["SHP2L", "NBS19"],
                calibration_type="IQR",
                sigma_level=1.0,
                iqr_multiplier=1.5,
                independent_isotope_outliers=True,
                color_param="Date_ordinal",
                z_axis="1  Cycle Int  Samp  44",
                precision_date_range=("2025-01-01", "2025-01-03"),
                linearity={
                    "apply": True,
                    "use_diff_intensity": True,
                    "manual_override_enabled": False,
                    "line_1_offset": 0.0,
                    "line_2_offset": 0.0,
                    "line_1_offset_d13": 2.5,
                    "line_2_offset_d13": -1.5,
                    "line_1_offset_d18": -0.8,
                    "line_2_offset_d18": 1.2,
                    "manual_d13_per_10v": 0.0,
                    "manual_d18_per_10v": 0.0,
                },
            ),
        )
        stored_df = api_main.store.load_frame(self.session_id)
        for raw_col in ("d 13C/12C  Mean", "d 18O/16O  Mean"):
            baseline_raw = pd.to_numeric(baseline_df[raw_col], errors="coerce").reset_index(drop=True)
            stored_raw = pd.to_numeric(stored_df[raw_col], errors="coerce").reset_index(drop=True)
            pd.testing.assert_series_equal(stored_raw, baseline_raw, check_names=False)

    def test_run_calibration_manual_linearity_override_does_not_change_coefficients_when_disabled(self) -> None:
        baseline_session = api_main.store.create_session()
        override_session = api_main.store.create_session()
        api_main.store.save_frames(baseline_session, sample_calibration_df(), pd.DataFrame())
        api_main.store.save_frames(override_session, sample_calibration_df(), pd.DataFrame())

        baseline_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": False,
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )
        override_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": False,
                "use_diff_intensity": False,
                "manual_override_enabled": True,
                "manual_d13_per_10v": 3.0,
                "manual_d18_per_10v": -2.0,
            },
        )

        api_main.run_calibration(baseline_session, baseline_config)
        api_main.run_calibration(override_session, override_config)

        baseline_meta = api_main.store.load_metadata(baseline_session)
        override_meta = api_main.store.load_metadata(override_session)

        baseline_coeffs = baseline_meta["calibration"]["coefficients"]
        override_coeffs = override_meta["calibration"]["coefficients"]

        self.assertEqual(baseline_coeffs["d13C"]["slope"], override_coeffs["d13C"]["slope"])
        self.assertEqual(baseline_coeffs["d18O"]["slope"], override_coeffs["d18O"]["slope"])
        self.assertTrue(bool(override_meta["calibration"]["config"]["linearity"]["manual_override_enabled"]))

    def test_run_calibration_linearity_changes_calibrated_base_and_keeps_corrected_column_in_sync(self) -> None:
        baseline_session = api_main.store.create_session()
        linearity_session = api_main.store.create_session()
        api_main.store.save_frames(baseline_session, sample_calibration_df(), pd.DataFrame())
        api_main.store.save_frames(linearity_session, sample_calibration_df(), pd.DataFrame())

        baseline_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": False,
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )
        linearity_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
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
        )

        api_main.run_calibration(baseline_session, baseline_config)
        api_main.run_calibration(linearity_session, linearity_config)

        baseline_df = api_main.store.load_frame(baseline_session)
        linearity_df = api_main.store.load_frame(linearity_session)
        sample_mask = (
            baseline_df["Identifier 1"].astype(str).eq("SampleA")
            & baseline_df["Identifier 2"].astype(str).eq("1")
        )
        self.assertTrue(bool(sample_mask.any()))
        baseline_value = float(pd.to_numeric(baseline_df.loc[sample_mask, "d13C_calibrated"], errors="coerce").iloc[0])
        linearity_value = float(pd.to_numeric(linearity_df.loc[sample_mask, "d13C_calibrated"], errors="coerce").iloc[0])
        linearity_corrected_value = float(
            pd.to_numeric(
                linearity_df.loc[sample_mask, "d13C_calibrated_linearity_corrected"],
                errors="coerce",
            ).iloc[0]
        )
        self.assertNotAlmostEqual(baseline_value, linearity_value, places=6)
        self.assertAlmostEqual(linearity_value, linearity_corrected_value, places=6)

    def test_run_calibration_line_offsets_change_selected_basis_before_correction(self) -> None:
        baseline_session = api_main.store.create_session()
        offset_session = api_main.store.create_session()
        api_main.store.save_frames(baseline_session, sample_calibration_df(), pd.DataFrame())
        api_main.store.save_frames(offset_session, sample_calibration_df(), pd.DataFrame())

        baseline_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": True,
                "intensity_col": "1  Cycle Int  Samp  44",
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "line_1_offset": 0.0,
                "line_2_offset": 0.0,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )
        offset_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": True,
                "intensity_col": "1  Cycle Int  Samp  44",
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "line_1_offset": 1.5,
                "line_2_offset": -0.5,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )

        api_main.run_calibration(baseline_session, baseline_config)
        api_main.run_calibration(offset_session, offset_config)

        baseline_df = api_main.store.load_frame(baseline_session)
        offset_df = api_main.store.load_frame(offset_session)
        sample_mask = (
            baseline_df["Identifier 1"].astype(str).eq("SampleA")
            & baseline_df["Identifier 2"].astype(str).eq("1")
        )
        self.assertTrue(bool(sample_mask.any()))
        baseline_value = float(pd.to_numeric(baseline_df.loc[sample_mask, "d13C_calibrated"], errors="coerce").iloc[0])
        offset_value = float(pd.to_numeric(offset_df.loc[sample_mask, "d13C_calibrated"], errors="coerce").iloc[0])
        self.assertNotAlmostEqual(baseline_value, offset_value, places=6)

    def test_run_calibration_isotope_specific_line_offsets_are_applied(self) -> None:
        baseline_session = api_main.store.create_session()
        offset_session = api_main.store.create_session()
        api_main.store.save_frames(baseline_session, sample_calibration_df(), pd.DataFrame())
        api_main.store.save_frames(offset_session, sample_calibration_df(), pd.DataFrame())

        baseline_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": True,
                "intensity_col": "1  Cycle Int  Samp  44",
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "line_1_offset": 0.0,
                "line_2_offset": 0.0,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )
        offset_config = CalibrationConfig(
            selected_standards=["SHP2L", "NBS19"],
            calibration_type="IQR",
            sigma_level=1.0,
            iqr_multiplier=1.5,
            independent_isotope_outliers=True,
            color_param="Date_ordinal",
            z_axis="1  Cycle Int  Samp  44",
            precision_date_range=("2025-01-01", "2025-01-03"),
            linearity={
                "apply": True,
                "intensity_col": "1  Cycle Int  Samp  44",
                "use_diff_intensity": False,
                "manual_override_enabled": False,
                "line_1_offset": 0.0,
                "line_2_offset": 0.0,
                "line_1_offset_d13": 1.5,
                "line_2_offset_d13": -0.5,
                "manual_d13_per_10v": 0.0,
                "manual_d18_per_10v": 0.0,
            },
        )

        api_main.run_calibration(baseline_session, baseline_config)
        api_main.run_calibration(offset_session, offset_config)

        baseline_df = api_main.store.load_frame(baseline_session)
        offset_df = api_main.store.load_frame(offset_session)
        sample_mask = (
            baseline_df["Identifier 1"].astype(str).eq("SampleA")
            & baseline_df["Identifier 2"].astype(str).eq("1")
        )
        self.assertTrue(bool(sample_mask.any()))
        baseline_value = float(pd.to_numeric(baseline_df.loc[sample_mask, "d13C_calibrated"], errors="coerce").iloc[0])
        offset_value = float(pd.to_numeric(offset_df.loc[sample_mask, "d13C_calibrated"], errors="coerce").iloc[0])
        self.assertNotAlmostEqual(baseline_value, offset_value, places=6)

    def test_run_calibration_supports_quadratic_linearity_fit(self) -> None:
        quadratic_session = api_main.store.create_session()
        api_main.store.save_frames(quadratic_session, sample_calibration_df(), pd.DataFrame())

        api_main.run_calibration(
            quadratic_session,
            CalibrationConfig(
                selected_standards=["SHP2L", "NBS19"],
                calibration_type="IQR",
                sigma_level=1.0,
                iqr_multiplier=1.5,
                independent_isotope_outliers=True,
                color_param="Date_ordinal",
                z_axis="1  Cycle Int  Samp  44",
                precision_date_range=("2025-01-01", "2025-01-03"),
                linearity={
                    "apply": True,
                    "use_diff_intensity": False,
                    "quadratic": True,
                    "manual_override_enabled": False,
                    "manual_d13_per_10v": 0.0,
                    "manual_d18_per_10v": 0.0,
                },
            ),
        )

        metadata = api_main.store.load_metadata(quadratic_session)
        fits = metadata.get("calibration", {}).get("linearity_fits", {})
        self.assertEqual(int((fits.get("d13C", {}) or {}).get("degree", 1)), 2)
        self.assertEqual(int((fits.get("d18O", {}) or {}).get("degree", 1)), 2)
        self.assertIn("quad", fits.get("d13C", {}))
        self.assertIn("quad", fits.get("d18O", {}))

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

    def test_standards_official_values_endpoints_support_list_edit_and_delete(self) -> None:
        values = api_main.list_official_standard_values()
        standards = {item.standard for item in values}
        self.assertIn("NBS18", standards)
        self.assertIn("NBS19", standards)

        updated = api_main.upsert_official_standard_value(
            CalibrationOfficialValueUpsertRequest(
                standard="nbs18",
                isotopic_value_type=ISOTYPE_D13C,
                value=-5.25,
                source="manual",
            )
        )
        self.assertEqual(updated.standard, "NBS18")
        self.assertEqual(updated.isotopic_value_type, ISOTYPE_D13C)
        self.assertAlmostEqual(float(updated.value), -5.25, places=6)

        values_after_upsert = api_main.list_official_standard_values()
        updated_lookup = {
            (item.standard, item.isotopic_value_type): item.value
            for item in values_after_upsert
        }
        self.assertAlmostEqual(float(updated_lookup[("NBS18", ISOTYPE_D13C)]), -5.25, places=6)

        delete_result = api_main.delete_standard_official_values("NBS18")
        self.assertGreaterEqual(delete_result.deleted_rows, 1)
        values_after_delete = api_main.list_official_standard_values()
        remaining_standards = {item.standard for item in values_after_delete}
        self.assertNotIn("NBS18", remaining_standards)


if __name__ == "__main__":
    unittest.main()
