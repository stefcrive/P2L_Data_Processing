#!/usr/bin/env python3
"""
interpolate_outliers.py
========================

Run with: python interpolate_outliers.py --input /path/to/dataset.xlsx --output /path/to/output_file.xlsx


This script processes an Excel workbook containing a sheet named ``Data`` with
isotope measurements and flags indicating outlier rows.  For any row where
the ``Outlier Types`` column is not empty, the script replaces the values
in the following columns by a linear interpolation using the values from
the previous and next non‐outlier rows:

    * ``d 13C/12C  Mean``
    * ``d 13C/12C  Std Dev``
    * ``d 18O/16O  Mean``
    * ``d 18O/16O  Std Dev``
    * ``d13C_calibrated``
    * ``d18O_calibrated``

The script preserves all other sheets and columns.  The modified workbook
is written to a new file whose name is derived from the input filename
with ``_interpolated`` appended before the extension unless explicitly
specified.

Usage::

    python interpolate_outliers.py --input INPUT.xlsx [--output OUTPUT.xlsx]

Requirements:
    - Python 3.8 or newer
    - ``pandas`` and ``openpyxl`` packages installed

Example::

    python interpolate_outliers.py \
        --input dataset_with_outliers(1).xlsx \
        --output dataset_with_outliers_interpolated.xlsx

This will produce ``dataset_with_outliers_interpolated.xlsx`` containing
interpolated values for the specified columns.
"""

import argparse
import os
from typing import List

import pandas as pd

from services.irms_api.domain.processing.core import _interpolate_outliers_by_identifier2


def interpolate_columns(df: pd.DataFrame, outlier_mask: pd.Series, cols: List[str]) -> pd.DataFrame:
    """Interpolate outlier rows through the shared processing core helper."""
    return _interpolate_outliers_by_identifier2(df, outlier_mask, cols)


def process_workbook(input_path: str, output_path: str) -> None:
    """Read an Excel workbook, interpolate outliers on the ``Data`` sheet,
    and write the result to a new workbook.

    Parameters
    ----------
    input_path : str
        Path to the input Excel file.
    output_path : str
        Path to write the interpolated Excel file.
    """
    # Load the workbook
    xl = pd.ExcelFile(input_path)
    sheets = {name: xl.parse(name) for name in xl.sheet_names}

    # Only modify the Data sheet if it exists
    if "Data" in sheets:
        df = sheets["Data"].copy()

        # Determine which rows have non-empty outlier types
        # We treat any non-NA value as an outlier; empty strings count as NA
        outlier_mask = df["Outlier Types"].astype(str).str.strip().replace({"": np.nan}).notna()

        # Columns to interpolate
        cols_to_interp = [
            "d 13C/12C  Mean",
            "d 13C/12C  Std Dev",
            "d 18O/16O  Mean",
            "d 18O/16O  Std Dev",
            "d13C_calibrated",
            "d18O_calibrated",
        ]

        # Perform interpolation
        sheets["Data"] = interpolate_columns(df, outlier_mask, cols_to_interp)

    # Write out all sheets to the new workbook
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)


def build_output_path(input_path: str) -> str:
    """Generate a default output filename by appending ``_interpolated``
    before the file extension.

    Parameters
    ----------
    input_path : str
        Path to the input Excel file.

    Returns
    -------
    str
        Suggested output path.
    """
    root, ext = os.path.splitext(input_path)
    return f"{root}_interpolated{ext}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interpolate outlier rows in an Excel workbook.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the input Excel (.xlsx) file",
    )
    parser.add_argument(
        "--output",
        help="Path to save the interpolated Excel file (default: <input>_interpolated.xlsx)",
    )

    args = parser.parse_args()

    input_path = args.input
    output_path = args.output if args.output else build_output_path(input_path)

    # Process workbook
    process_workbook(input_path, output_path)

    print(f"Interpolated workbook saved to: {output_path}")


if __name__ == "__main__":
    main()
