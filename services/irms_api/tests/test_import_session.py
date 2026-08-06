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
from services.irms_api.domain.import_session import _load_uploaded_workbooks
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
            finally:
                api_main.store = original_store


if __name__ == "__main__":
    unittest.main()
