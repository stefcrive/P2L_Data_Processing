from __future__ import annotations

import io
import unittest

import pandas as pd

from services.irms_api.domain.constants import CYCLE1_SIGNAL_SAMP44_COL
from services.irms_api.domain.import_session import _load_uploaded_workbooks


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


if __name__ == "__main__":
    unittest.main()
