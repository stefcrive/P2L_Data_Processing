from __future__ import annotations

import asyncio
import io
import tempfile
import time
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi import UploadFile

from services.irms_api.api import main as api_main
from services.irms_api.domain.constants import CYCLE1_SIGNAL_SAMP44_COL
from services.irms_api.domain.contracts import (
    ImportFieldParsingRule,
    ImportNamingUpdate,
    ImportParsingConfig,
    ImportWorkbookParsingConfig,
)
from services.irms_api.domain.import_session import (
    _apply_import_parsing_config,
    _load_uploaded_workbooks,
)
from services.irms_api.session_store import FileSessionStore


class _NamedBytesIO(io.BytesIO):
    def __init__(self, content: bytes, name: str) -> None:
        super().__init__(content)
        self.name = name
        self.size = len(content)


def build_workbook_bytes(frame: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False)
    return buffer.getvalue()


class ImportSessionTests(unittest.TestCase):
    def test_species_naming_details_follow_source_software(self) -> None:
        frame = pd.DataFrame(
            {
                "Species": ["Coral", "Shell"],
                "Excel File": ["qtegra.xlsx", "isodat.xlsx"],
                "Raw Label": ["MD23-3678 - G. ruber", "Isodat composed label"],
                "Raw Identifier 1": [None, "SHP2L"],
                "Raw Identifier 2": [None, "901"],
                "Raw Comment": ["3678", "N. dutertrei"],
            }
        )
        metadata = {
            "source_files": [
                {"name": "qtegra.xlsx", "software": "qtegra"},
                {"name": "isodat.xlsx", "software": "isodat"},
            ]
        }

        details = api_main._build_species_source_details(frame, metadata)

        self.assertEqual(details["Coral"][0]["software"], "qtegra")
        self.assertEqual(details["Coral"][0]["raw_label"], "MD23-3678 - G. ruber")
        self.assertEqual(details["Coral"][0]["raw_comment"], "3678")
        self.assertEqual(details["Shell"][0]["software"], "isodat")
        self.assertEqual(details["Shell"][0]["raw_identifier1"], "SHP2L")
        self.assertEqual(details["Shell"][0]["raw_identifier2"], "901")
        self.assertEqual(details["Shell"][0]["raw_comment"], "N. dutertrei")

    def test_empty_workbook_reports_actionable_error(self) -> None:
        uploaded = _NamedBytesIO(b"", "empty.xlsx")

        df, cycles_df, specs, errors = _load_uploaded_workbooks([uploaded])

        self.assertIsNone(df)
        self.assertIsNone(cycles_df)
        self.assertEqual(specs, [])
        self.assertEqual(
            errors,
            [
                "Failed to read Excel file 'empty.xlsx': the file is empty (0 bytes). "
                "Download or save the workbook locally, then select it again."
            ],
        )

    def test_xlsx_error_is_not_masked_by_xlrd_fallback(self) -> None:
        uploaded = _NamedBytesIO(b"not an Excel workbook", "broken.xlsx")

        with patch(
            "services.irms_api.domain.import_session.pd.read_excel",
            side_effect=ValueError("invalid xlsx payload"),
        ) as read_excel:
            df, cycles_df, specs, errors = _load_uploaded_workbooks([uploaded])

        self.assertIsNone(df)
        self.assertIsNone(cycles_df)
        self.assertEqual(specs, [])
        self.assertEqual(
            errors,
            ["Failed to read Excel file 'broken.xlsx': invalid xlsx payload"],
        )
        self.assertEqual(read_excel.call_count, 1)
        self.assertEqual(read_excel.call_args.kwargs["engine"], "openpyxl")

    def test_load_uploaded_workbooks_normalizes_signal_intensity(self) -> None:
        frame = pd.DataFrame(
            {
                "Label": ["SampleA - Coral", "SampleB - Shell"],
                "Comment": ["1", "2"],
                "Date": ["01/01/25", "01/02/25"],
                "Pressure Adjust Result Intensity": [48000, 24000],
                "d 13C/12C  Mean": [1.1, 1.2],
                "d 18O/16O  Mean": [2.1, 2.2],
                "d 13C/12C  Std Dev": [0.1, 0.1],
                "d 18O/16O  Std Dev": [0.2, 0.2],
                "Line": [1, 1],
            }
        )
        uploaded = _NamedBytesIO(build_workbook_bytes(frame), "test-import.xlsx")

        df, cycles_df, specs, errors = _load_uploaded_workbooks([uploaded])

        self.assertEqual(errors, [])
        self.assertIsNotNone(df)
        self.assertIsNotNone(cycles_df)
        self.assertEqual(len(specs), 1)
        self.assertIn(CYCLE1_SIGNAL_SAMP44_COL, df.columns)
        self.assertAlmostEqual(float(df.loc[0, CYCLE1_SIGNAL_SAMP44_COL]), 48.0)
        self.assertEqual(str(df.loc[0, "Identifier 1"]), "SampleA")
        self.assertEqual(str(df.loc[0, "Species"]), "Coral")

    def test_configurable_identity_parsing_preserves_hyphens_inside_identifier(self) -> None:
        frame = pd.DataFrame(
            {
                "Metadata": ["MD23-3678 - G. ruber"],
                "Sequence": ["2129"],
            }
        )
        config = ImportWorkbookParsingConfig(
            file_index=0,
            file_name="qtegra.xlsx",
            software="qtegra",
            identifier1=ImportFieldParsingRule(
                source_column="Metadata",
                mode="split",
                delimiter=" - ",
                part_index=0,
            ),
            identifier2=ImportFieldParsingRule(
                source_column="Sequence",
                mode="direct",
            ),
            species=ImportFieldParsingRule(
                source_column="Metadata",
                mode="regex",
                regex_pattern=r"^.+?\s+-\s+(.+)$",
                regex_group=1,
            ),
        )

        parsed = _apply_import_parsing_config(frame, config)

        self.assertEqual(parsed.loc[0, "Identifier 1"], "MD23-3678")
        self.assertEqual(parsed.loc[0, "Identifier 2"], "2129")
        self.assertEqual(parsed.loc[0, "Species"], "G. ruber")

    def test_configurable_identity_parsing_preserves_original_identity_text(self) -> None:
        frame = pd.DataFrame(
            {
                "Identifier 1": ["  Original identifier  "],
                "Label": ["  Parsed identifier - PORITES LOBATA  "],
                "Comment": ["  Original comment  "],
            }
        )
        config = ImportWorkbookParsingConfig(
            file_index=0,
            file_name="raw-identities.xlsx",
            software="generic",
            identifier1=ImportFieldParsingRule(source_column="Label", mode="split", delimiter=" - ", part_index=0),
            identifier2=ImportFieldParsingRule(source_column="Comment", mode="direct"),
            species=ImportFieldParsingRule(source_column="Label", mode="split", delimiter=" - ", part_index=1),
        )

        parsed = _apply_import_parsing_config(frame, config)

        self.assertEqual(parsed.loc[0, "Identifier 1"], "Parsed identifier")
        self.assertEqual(parsed.loc[0, "Species"], "PORITES LOBATA")
        self.assertEqual(parsed.loc[0, "Raw Identifier 1"], "  Original identifier  ")
        self.assertEqual(parsed.loc[0, "Raw Label"], "  Parsed identifier - PORITES LOBATA  ")
        self.assertEqual(parsed.loc[0, "Raw Comment"], "  Original comment  ")

    def test_isodat_alternating_rows_are_standardized_to_cycle_pairs(self) -> None:
        frame = pd.DataFrame(
            {
                "Row": [4, 4, 4, 4, 4],
                "Identifier 1": ["DGL-1634"] * 5,
                "Identifier 2": ["39.33-39.35"] * 5,
                "Comment": ["up"] * 5,
                "Date": ["11/18/25"] * 5,
                "Time": ["13:38:15"] * 5,
                "d 13C/12C  Mean": [-1.207] * 5,
                "d 13C/12C  Std Dev": [0.009] * 5,
                "d 18O/16O  Mean": [4.493] * 5,
                "d 18O/16O  Std Dev": [0.012] * 5,
                "1  Cycle Int  Samp  44": [2528.841] * 5,
                "1  Cycle Int  Ref  44": [2560.193] * 5,
                "rIntensity 44": [2560.193, 2528.841, 2428.997, 2437.786, 2304.957],
                "rIntensity 45": [2735.405, 2749.327, 2595.168, 2650.296, 2462.652],
                "rIntensity 46": [3652.985, 3641.027, 3465.764, 3509.855, 3288.736],
                "Line": [2] * 5,
            }
        )
        uploaded = _NamedBytesIO(build_workbook_bytes(frame), "isodat-export.xlsx")

        df, cycles_df, specs, errors = _load_uploaded_workbooks([uploaded])

        self.assertEqual(errors, [])
        self.assertEqual(len(df), 1)
        self.assertEqual(len(cycles_df), 3)
        self.assertEqual(cycles_df["Cycle Number"].tolist(), ["Pre", "Cycle 1", "Cycle 2"])
        self.assertEqual(df.loc[0, "Identifier 1"], "DGL-1634")
        self.assertEqual(df.loc[0, "Identifier 2"], "39.33-39.35")
        self.assertEqual(df.loc[0, "Species"], "up")
        self.assertAlmostEqual(float(cycles_df.loc[1, "1  Cycle Int  Samp  44"]), 2.528841)
        self.assertAlmostEqual(float(cycles_df.loc[1, "1  Cycle Int  Ref  44"]), 2.560193)
        self.assertEqual(specs[0]["software"], "isodat")

    def test_load_uploaded_workbooks_accepts_explicit_per_file_parsing_config(self) -> None:
        frame = pd.DataFrame(
            {
                "Composite": ["Core-A|44.1|Species X"],
                "d 13C/12C  Mean": [1.1],
                "d 18O/16O  Mean": [2.1],
            }
        )
        uploaded = _NamedBytesIO(build_workbook_bytes(frame), "custom.xlsx")
        parsing_config = ImportParsingConfig(
            files=[
                ImportWorkbookParsingConfig(
                    file_index=0,
                    file_name="custom.xlsx",
                    identifier1=ImportFieldParsingRule(
                        source_column="Composite",
                        mode="split",
                        delimiter="|",
                        part_index=0,
                    ),
                    identifier2=ImportFieldParsingRule(
                        source_column="Composite",
                        mode="split",
                        delimiter="|",
                        part_index=1,
                    ),
                    species=ImportFieldParsingRule(
                        source_column="Composite",
                        mode="split",
                        delimiter="|",
                        part_index=2,
                    ),
                )
            ]
        )

        df, _, _, errors = _load_uploaded_workbooks(
            [uploaded],
            parsing_config=parsing_config,
        )

        self.assertEqual(errors, [])
        self.assertEqual(df.loc[0, "Identifier 1"], "Core-A")
        self.assertEqual(df.loc[0, "Identifier 2"], "44.1")
        self.assertEqual(df.loc[0, "Species"], "Species X")

    def test_import_background_job_returns_new_session(self) -> None:
        frame = pd.DataFrame(
            {
                "Label": ["SampleA - Coral"],
                "Comment": ["sample"],
                "Date": ["01/01/25"],
                "Pressure Adjust Result Intensity": [48000],
                "d 13C/12C  Mean": [1.1],
                "d 18O/16O  Mean": [2.1],
                "d 13C/12C  Std Dev": [0.1],
                "d 18O/16O  Std Dev": [0.2],
                "Line": [1],
            }
        )
        workbook = build_workbook_bytes(frame)
        with tempfile.TemporaryDirectory() as temp_dir:
            original_store = api_main.store
            api_main.store = FileSessionStore(temp_dir)
            try:
                submitted = asyncio.run(
                    api_main.submit_import_session_job(
                        [UploadFile(file=io.BytesIO(workbook), filename="job-import.xlsx")]
                    )
                )
                deadline = time.monotonic() + 10.0
                completed = api_main.job_registry.get(submitted.job_id)
                while completed.state not in {"succeeded", "failed", "cancelled"} and time.monotonic() < deadline:
                    time.sleep(0.02)
                    completed = api_main.job_registry.get(submitted.job_id)

                self.assertEqual(completed.state, "succeeded", completed.error)
                session_id = completed.result["session"]["session_id"]
                self.assertTrue(api_main.store.session_exists(session_id))
                naming = api_main.set_import_naming_workspace(
                    session_id,
                    ImportNamingUpdate(
                        species_name_map={"Coral": "Coral corrected"},
                        identifier1_name_map={"SampleA": "Sample Alpha"},
                    ),
                )
                self.assertEqual(naming.species_name_map, {"Coral": "Coral corrected"})
                self.assertEqual(naming.identifier1_name_map, {"SampleA": "Sample Alpha"})
                self.assertIn("Coral", naming.species_sources)
                self.assertIn("SampleA", naming.identifier1_sources)
            finally:
                api_main.store = original_store


if __name__ == "__main__":
    unittest.main()
