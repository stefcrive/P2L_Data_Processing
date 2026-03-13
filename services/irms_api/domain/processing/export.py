from __future__ import annotations

import io
import re

import pandas as pd

MATERIALS_METHODS_TEXT = (
    'When results produced at P2L are being published, we suggest using the following text in the "Material and Methods" section of the publication:\n\n'
    '"Analyses on (your samples) for determination of d13C and d18O were performed at the Paleoceanography and Paleoclimatology '
    "Laboratory, School of Arts, Sciences and Humanities of the University of Sao Paulo, Brazil. The laboratory is equipped with a Thermo "
    "Fisher Scientific MAT253 isotope ratio mass spectrometer (IRMS) coupled with a Thermo Fisher Scientific Kiel IV carbonate "
    "preparation device. The details on the laboratory analytical setup and performance are described in Crivellari et al. (2021). The IRMS "
    "measures the isotopic composition of the CO2 developed by the reaction between the sample carbonate and orthophosphoric acid at "
    "70\u00b0C. Measurements were calibrated against repeated analyses of SHP2L reference material which is used as internal working "
    "standard (Crivellari et al., 2021). SHP2L is in turn calibrated against international reference material NBS19 and values are anchored to "
    "the Vienna Pee Dee Belemnite (VPDB) scale. Analytical precision was better than (please use the value informed by P2L) \u2030 for d13C "
    "and (please use the value informed by P2L) \u2030 for d18O (\u00b11 s, n = please use the value informed by P2L).\"\n\n"
    "Reference\n"
    "Crivellari, S., Viana, P.J., Campos, M.D., Kuhnert, H., Lopes, A.B.M., da Cruz, F.W., Chiessi, C.M., 2021. Development and "
    "characterization of a new in-house reference material for stable carbon and oxygen isotopes analyses. Journal of Analytical Atomic "
    "Spectrometry 36, 1125-1134. DOI: 10.1039/D1JA00030F."
)


def _sanitize_filename(name: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]', "_", str(name))
    value = re.sub(r"\s+", " ", value).strip()
    return value or "output"


def _build_client_filename(client_name: str, client_df: pd.DataFrame) -> str:
    del client_df  # filename is based on requested client name and export date
    client_part = _sanitize_filename(client_name) if client_name else "Client"
    date_str = pd.Timestamp.today().strftime("%d%m%Y")
    title = "BTS Stable C&O isosopes results P2L"
    return f"{client_part} {title} {date_str}.xlsx"


def _build_dataset_filename(client_name: str | None) -> str:
    client_part = _sanitize_filename(client_name) if client_name else "Client"
    date_str = pd.Timestamp.today().strftime("%d%m%Y")
    title = "all data BTS Stable C&O isosopes results P2L"
    return f"{client_part} {title} {date_str}.xlsx"


def _build_data_sheet(df: pd.DataFrame, selected_standards: list[str]) -> pd.DataFrame:
    standards_mask = (
        df["Identifier 1"].isin(selected_standards)
        if selected_standards and "Identifier 1" in df.columns
        else pd.Series(False, index=df.index)
    )
    return df.loc[~standards_mask].copy()


def _build_client_output_frame(data_sheet: pd.DataFrame, comment_map: dict[str, str] | None = None) -> pd.DataFrame:
    species_series = (
        data_sheet.get("Species", pd.Series(index=data_sheet.index, dtype=object))
        .fillna("")
        .astype(str)
    )
    if comment_map:
        species_series = species_series.map(lambda value: comment_map.get(value, value))
    return pd.DataFrame(
        {
            "Identifier": data_sheet.get("Identifier 1", pd.Series(index=data_sheet.index, dtype=object)),
            "Sample #": data_sheet.get("Identifier 2", pd.Series(index=data_sheet.index, dtype=object)),
            "Species": species_series,
            "d13C (\u2030, VPDB)  Mean": pd.to_numeric(data_sheet.get("d 13C/12C  Mean"), errors="coerce"),
            "d13C (\u2030, VPDB)  Std Dev": pd.to_numeric(data_sheet.get("d 13C/12C  Std Dev"), errors="coerce"),
            "d18O (\u2030, VPDB)  Mean": pd.to_numeric(data_sheet.get("d 18O/16O  Mean"), errors="coerce"),
            "d18O (\u2030, VPDB)  Std Dev": pd.to_numeric(data_sheet.get("d 18O/16O  Std Dev"), errors="coerce"),
            "Corrected d13C (\u2030, VPDB)": pd.to_numeric(
                data_sheet.get("d13C_calibrated_linearity_corrected", data_sheet.get("d13C_calibrated")),
                errors="coerce",
            ),
            "Corrected d18O (\u2030, VPDB)": pd.to_numeric(
                data_sheet.get("d18O_calibrated_linearity_corrected", data_sheet.get("d18O_calibrated")),
                errors="coerce",
            ),
        }
    )


def _round_client_output_columns(client_df: pd.DataFrame) -> pd.DataFrame:
    rounded = client_df.copy()
    numeric_cols = [
        "d13C (\u2030, VPDB)  Mean",
        "d13C (\u2030, VPDB)  Std Dev",
        "d18O (\u2030, VPDB)  Mean",
        "d18O (\u2030, VPDB)  Std Dev",
        "Corrected d13C (\u2030, VPDB)",
        "Corrected d18O (\u2030, VPDB)",
    ]
    for col in numeric_cols:
        if col in rounded.columns:
            rounded[col] = pd.to_numeric(rounded[col], errors="coerce").round(2)
    return rounded


def _compute_shp2l_precision(df: pd.DataFrame) -> tuple[float, float, int, int]:
    if "Identifier 1" not in df.columns:
        return (0.0, 0.0, 0, 0)
    shp = df[df["Identifier 1"].astype(str) == "SHP2L"].copy()
    if shp.empty:
        return (0.0, 0.0, 0, 0)
    d13 = pd.to_numeric(shp.get("d 13C/12C  Mean"), errors="coerce").dropna()
    d18 = pd.to_numeric(shp.get("d 18O/16O  Mean"), errors="coerce").dropna()
    d13_std = float(d13.std()) if not d13.empty and pd.notna(d13.std()) else 0.0
    d18_std = float(d18.std()) if not d18.empty and pd.notna(d18.std()) else 0.0
    return (d13_std, d18_std, int(d13.shape[0]), int(d18.shape[0]))


def _format_client_output_worksheet(
    writer: pd.ExcelWriter,
    sheet_name: str,
    client_df: pd.DataFrame,
    source_df: pd.DataFrame,
) -> None:
    workbook = writer.book
    worksheet = writer.sheets[sheet_name]
    header_fmt = workbook.add_format({"bold": True})
    corrected_hdr_fmt = workbook.add_format({"bold": True, "font_color": "#6A1B9A"})
    num_fmt = workbook.add_format({"num_format": "0.00"})
    numeric_cols = {
        "d13C (\u2030, VPDB)  Mean",
        "d13C (\u2030, VPDB)  Std Dev",
        "d18O (\u2030, VPDB)  Mean",
        "d18O (\u2030, VPDB)  Std Dev",
        "Corrected d13C (\u2030, VPDB)",
        "Corrected d18O (\u2030, VPDB)",
    }
    for col_idx, col_name in enumerate(client_df.columns):
        worksheet.write(0, col_idx, col_name, corrected_hdr_fmt if "Corrected" in col_name else header_fmt)
        width = 15
        if col_name in ("Identifier", "Species"):
            width = 18
        elif "Corrected" in col_name:
            width = 22
        worksheet.set_column(col_idx, col_idx, width, num_fmt if col_name in numeric_cols else None)
    equip_title_fmt = workbook.add_format({"bold": True})
    d13_std, d18_std, n13, n18 = _compute_shp2l_precision(source_df)
    worksheet.write(1, 10, "Equiment:", equip_title_fmt)
    worksheet.write(1, 11, "ThermoFisher Scientific MAT253 gas isotope ratio mass spectrometer")
    worksheet.write(2, 11, "Kiel IV automated carbonate preparation device")
    worksheet.write(4, 10, "Standard deviation of SHP2L over measurement period:", equip_title_fmt)
    worksheet.write(5, 11, f"{d13_std:.2f} \u2030 for d13C")
    worksheet.write(6, 11, f"{d18_std:.2f} \u2030 for d18O")
    worksheet.write(7, 11, f"d13C n={n13}, d18O n={n18}")
    worksheet.insert_textbox(
        "L10",
        MATERIALS_METHODS_TEXT,
        {"width": 820, "height": 580, "line": {"color": "#4F81BD"}},
    )


def build_dataset_workbook_bytes(
    df: pd.DataFrame,
    outliers: pd.DataFrame | None = None,
    selected_standards: list[str] | None = None,
    client_name: str | None = None,
    statistics_rows: list[dict[str, object]] | None = None,
) -> tuple[bytes, str]:
    towrite = io.BytesIO()
    outliers = outliers if outliers is not None else pd.DataFrame()
    selected_standards = selected_standards or []
    with pd.ExcelWriter(towrite, engine="xlsxwriter") as writer:
        data_sheet = _build_data_sheet(df, selected_standards)
        data_sheet.to_excel(writer, index=False, sheet_name="Data")
        if not outliers.empty:
            outliers.to_excel(writer, index=False, sheet_name="Outliers")
        stats_rows = statistics_rows or [
            {"Metric": "Total rows", "Value": int(len(df)), "Details": ""},
            {"Metric": "Exported rows", "Value": int(len(data_sheet)), "Details": ""},
            {"Metric": "Outlier rows", "Value": int(len(outliers)), "Details": ""},
            {
                "Metric": "Selected standards",
                "Value": ", ".join(selected_standards) if selected_standards else "",
                "Details": "",
            },
        ]
        stats = pd.DataFrame(stats_rows)
        stats.to_excel(writer, index=False, sheet_name="Statistics")
    towrite.seek(0)
    return towrite.getvalue(), _build_dataset_filename(client_name)


def build_client_output_workbook_bytes(
    df: pd.DataFrame,
    selected_standards: list[str] | None = None,
    client_name: str | None = None,
    comment_map: dict[str, str] | None = None,
) -> tuple[bytes, str]:
    towrite = io.BytesIO()
    selected_standards = selected_standards or []
    data_sheet = _build_data_sheet(df, selected_standards)
    client_df = _round_client_output_columns(_build_client_output_frame(data_sheet, comment_map=comment_map))
    with pd.ExcelWriter(towrite, engine="xlsxwriter") as writer:
        sheet_name = "Client Output"
        client_df.to_excel(writer, index=False, sheet_name=sheet_name)
        _format_client_output_worksheet(writer, sheet_name, client_df, source_df=df)
    towrite.seek(0)
    return towrite.getvalue(), _build_client_filename(client_name or "", client_df)
