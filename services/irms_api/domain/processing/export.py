from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd

from ..shared.dataframe import _parse_numeric_token

MATERIALS_METHODS_TEXT = (
    'When results produced at P2L are being published, we suggest using the following text in the "Material and Methods" section of the publication:\n\n'
    '"Analyses on (your samples) for determination of δ¹³C and δ¹⁸O were performed at the Paleoceanography and Paleoclimatology '
    "Laboratory, School of Arts, Sciences and Humanities of the University of Sao Paulo, Brazil. The laboratory is equipped with a Thermo "
    "Fisher Scientific MAT253 isotope ratio mass spectrometer (IRMS) coupled with a Thermo Fisher Scientific Kiel IV carbonate "
    "preparation device. The details on the laboratory analytical setup and performance are described in Crivellari et al. (2021). The IRMS "
    "measures the isotopic composition of the CO₂ developed by the reaction between the sample carbonate and orthophosphoric acid at "
    "70\u00b0C. Measurements were calibrated against repeated analyses of SHP2L reference material which is used as internal working "
    "standard (Crivellari et al., 2021). SHP2L is in turn calibrated against international reference material NBS19 and values are anchored to "
    "the Vienna Pee Dee Belemnite (VPDB) scale. Analytical precision was better than (please use the value informed by P2L) \u2030 for δ¹³C "
    "and (please use the value informed by P2L) \u2030 for δ¹⁸O (\u00b11 s, n = please use the value informed by P2L).\"\n\n"
    "Reference\n"
    "Crivellari, S., Viana, P.J., Campos, M.D., Kuhnert, H., Lopes, A.B.M., da Cruz, F.W., Chiessi, C.M., 2021. Development and "
    "characterization of a new in-house reference material for stable carbon and oxygen isotopes analyses. Journal of Analytical Atomic "
    "Spectrometry 36, 1125-1134. DOI: 10.1039/D1JA00030F."
)

CLIENT_OUTPUT_NUMERIC_COLUMNS = [
    "d13C (\u2030, VPDB)  Mean",
    "d13C (\u2030, VPDB)  Std Dev",
    "d18O (\u2030, VPDB)  Mean",
    "d18O (\u2030, VPDB)  Std Dev",
    "Corrected d13C (\u2030, VPDB)",
    "Corrected d18O (\u2030, VPDB)",
]

LOW_SIGNAL_THRESHOLD_V = 2.0
DUPLICATE_QUALITY_KEY_COLUMN = "__duplicate_failed_or_low_signal"


def _sanitize_filename(name: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]', "_", str(name))
    value = re.sub(r"\s+", " ", value).strip()
    return value or "output"


def _compact_client_identifier(identifier: str, species_values: list[str]) -> str:
    value = str(identifier).strip()
    if not value:
        return ""
    replicate_match = re.match(r"^(.+?)\s*#\s*\d+(?:\s*-\s*.*)?$", value)
    if replicate_match and replicate_match.group(1).strip():
        return replicate_match.group(1).strip()
    for species in sorted(species_values, key=len, reverse=True):
        normalized_species = str(species).strip()
        if not normalized_species:
            continue
        species_match = re.match(
            rf"^(.*?)\s*-\s*{re.escape(normalized_species)}\s*$",
            value,
            flags=re.IGNORECASE,
        )
        if species_match and species_match.group(1).strip():
            return species_match.group(1).strip()
    return value


def _client_identifier_tokens(client_df: pd.DataFrame) -> list[str]:
    identifier_values = (
        client_df.get("Identifier", pd.Series(dtype=object))
        .dropna()
        .astype(str)
        .map(str.strip)
    )
    species_values = [
        value
        for value in pd.unique(
            client_df.get("Species", pd.Series(dtype=object)).dropna().astype(str).map(str.strip)
        )
        if value
    ]
    compact_values = identifier_values.map(lambda value: _compact_client_identifier(value, species_values))
    tokens = [value for value in pd.unique(compact_values) if value]
    return sorted(tokens, key=int, reverse=True) if tokens and all(value.isdigit() for value in tokens) else tokens


def build_client_email_subject(client_name: str, client_df: pd.DataFrame, language: str = "en") -> str:
    identifier_tokens = _client_identifier_tokens(client_df)
    series_part = ", ".join(identifier_tokens) or "selected"
    client_part = str(client_name).strip()
    date_str = pd.Timestamp.today().strftime("%d%m%Y")
    if language == "pt":
        subject = f"Resultados das séries {series_part} de isótopos estáveis de C e O - P2L"
    elif language == "es":
        subject = f"Resultados de las series {series_part} de isótopos estables de C y O - P2L"
    else:
        subject = f"Results for {series_part} series - stable C & O isotopes - P2L"
    if client_part:
        subject = f"{subject} - {client_part}"
    return f"{subject} - {date_str}"


def _build_client_filename(client_name: str, client_df: pd.DataFrame) -> str:
    subject = build_client_email_subject(client_name, client_df, language="en")
    return f"{_sanitize_filename(subject)}.xlsx"


def _build_dataset_filename(client_name: str | None) -> str:
    client_part = _sanitize_filename(client_name) if client_name else "Client"
    date_str = pd.Timestamp.today().strftime("%d%m%Y")
    return f"Results all data stable C&O isotopes P2L client {client_part} {date_str}.xlsx"


def _build_data_sheet(df: pd.DataFrame, selected_standards: list[str]) -> pd.DataFrame:
    standards_mask = (
        df["Identifier 1"].isin(selected_standards)
        if selected_standards and "Identifier 1" in df.columns
        else pd.Series(False, index=df.index)
    )
    return df.loc[~standards_mask].copy()


def _client_output_source_series(data_sheet: pd.DataFrame, source: str) -> pd.Series:
    source_columns = {
        "identifier1": "Identifier 1",
        "identifier2": "Identifier 2",
        "comment": "Comment",
        "species": "Species",
        "raw_identifier1": "Raw Identifier 1",
        "raw_label": "Raw Label",
        "raw_comment": "Raw Comment",
    }
    fallback_columns = {
        "raw_identifier1": "Identifier 1",
        "raw_label": "Label",
        "raw_comment": "Comment",
    }
    source_key = str(source)
    column = source_columns.get(source_key, "")
    if column not in data_sheet.columns:
        column = fallback_columns.get(source_key, column)
    return data_sheet.get(column, pd.Series("", index=data_sheet.index, dtype=object)).fillna("").astype(str)


def is_raw_client_output_source(source: str) -> bool:
    return str(source) in {"raw_identifier1", "raw_label", "raw_comment"}


def format_academic_species_name(value: Any) -> str:
    tokens = re.sub(r"\s+", " ", str(value or "")).strip().split(" ")
    if not tokens or not tokens[0]:
        return ""
    first = tokens[0][:1].upper() + tokens[0][1:].lower()
    qualifiers = {"sp.", "spp.", "cf.", "aff.", "subsp.", "var."}
    remainder = [token.lower() if token.lower() in qualifiers or token.isalpha() else token for token in tokens[1:]]
    return " ".join([first, *remainder])


def _build_client_output_frame(
    data_sheet: pd.DataFrame,
    comment_map: dict[str, str] | None = None,
    *,
    identifier_source: str = "identifier1",
    sample_source: str = "comment",
    species_source: str = "species",
    show_sequence: bool = True,
) -> pd.DataFrame:
    sample_series = _client_output_source_series(data_sheet, sample_source)
    sequence_series = sample_series.map(_parse_numeric_token)
    species_series = _client_output_source_series(data_sheet, species_source)
    if not is_raw_client_output_source(species_source):
        if comment_map:
            species_series = species_series.map(lambda value: comment_map.get(value, value))
        species_series = species_series.map(format_academic_species_name)
    columns: dict[str, pd.Series] = {
            "Identifier": _client_output_source_series(data_sheet, identifier_source),
            "Sample #": sample_series,
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
            DUPLICATE_QUALITY_KEY_COLUMN: (
                data_sheet.get("Collector Status", pd.Series("", index=data_sheet.index, dtype=object))
                .fillna("")
                .astype(str)
                .str.strip()
                .str.casefold()
                .eq("failed sample")
                | pd.to_numeric(
                    data_sheet.get(
                        "1  Cycle Int  Samp  44",
                        pd.Series(float("nan"), index=data_sheet.index, dtype=float),
                    ),
                    errors="coerce",
                ).lt(LOW_SIGNAL_THRESHOLD_V)
            ),
        }
    if show_sequence:
        columns = {"Identifier": columns.pop("Identifier"), "Sample #": columns.pop("Sample #"), "Sequence": sequence_series, **columns}
    return pd.DataFrame(columns)


def _round_client_output_columns(client_df: pd.DataFrame) -> pd.DataFrame:
    rounded = client_df.copy()
    for col in CLIENT_OUTPUT_NUMERIC_COLUMNS:
        if col in rounded.columns:
            rounded[col] = pd.to_numeric(rounded[col], errors="coerce").round(2)
    return rounded


def _normalize_duplicate_key_series(values: pd.Series) -> pd.Series:
    return values.fillna("").astype(str).map(str.strip)


def _build_duplicate_identity_key(identifier1: str, identifier2: str, species: str) -> str:
    return f"{identifier1} | {identifier2} | {species}"


def summarize_client_output_duplicates(client_df: pd.DataFrame) -> dict[str, Any]:
    if client_df.empty:
        return {
            "duplicate_row_mask": pd.Series(dtype=bool),
            "duplicate_rows": [],
            "duplicate_identifier1_identifier2_species_values": [],
            "duplicate_row_count": 0,
        }

    identifier1_series = _normalize_duplicate_key_series(
        client_df.get("Identifier", pd.Series("", index=client_df.index, dtype=object))
    )
    identifier2_series = _normalize_duplicate_key_series(
        client_df.get("__identifier_2_key", pd.Series("", index=client_df.index, dtype=object))
    )
    species_series = _normalize_duplicate_key_series(
        client_df.get("Species", pd.Series("", index=client_df.index, dtype=object))
    )
    failed_or_low_signal_series = (
        client_df.get(
            DUPLICATE_QUALITY_KEY_COLUMN,
            pd.Series(False, index=client_df.index, dtype=bool),
        )
        .fillna(False)
        .astype(bool)
    )
    duplicate_identity_series = pd.Series(
        (
            _build_duplicate_identity_key(identifier1_value, identifier2_value, species_value)
            for identifier1_value, identifier2_value, species_value in zip(
                identifier1_series.tolist(), identifier2_series.tolist(), species_series.tolist()
            )
        ),
        index=client_df.index,
        dtype=object,
    )
    # A failed/low-signal attempt followed by a valid reanalysis is intentional, so
    # compare duplicates only among rows with the same result-quality classification.
    duplicate_comparison_key = pd.Series(
        zip(duplicate_identity_series.tolist(), failed_or_low_signal_series.tolist()),
        index=client_df.index,
        dtype=object,
    )
    duplicate_identity_mask = identifier2_series.ne("") & duplicate_comparison_key.duplicated(keep=False)
    duplicate_row_mask = duplicate_identity_mask.astype(bool)

    duplicate_columns = [column for column in ["Identifier", "Sample #", "Species", "Sequence"] if column in client_df.columns]
    duplicate_rows_df = client_df.loc[duplicate_row_mask, duplicate_columns].copy()
    duplicate_rows_df["Duplicate Identifier 1 + Identifier 2 + Species"] = (
        duplicate_identity_mask.loc[duplicate_rows_df.index].astype(bool)
    )
    duplicate_rows_df = duplicate_rows_df.fillna("")

    duplicate_identity_values = sorted(
        {
            token
            for token in duplicate_identity_series.loc[duplicate_identity_mask].tolist()
            if str(token).strip()
        },
    )
    return {
        "duplicate_row_mask": duplicate_row_mask,
        "duplicate_rows": duplicate_rows_df.to_dict(orient="records"),
        "duplicate_identifier1_identifier2_species_values": duplicate_identity_values,
        "duplicate_row_count": int(duplicate_row_mask.sum()),
    }


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
    precision_override: tuple[float, float, int, int] | None = None,
    duplicate_row_mask: pd.Series | None = None,
) -> None:
    workbook = writer.book
    worksheet = writer.sheets[sheet_name]
    header_fmt = workbook.add_format({"bold": True})
    corrected_hdr_fmt = workbook.add_format({"bold": True, "font_color": "#6A1B9A"})
    num_fmt = workbook.add_format({"num_format": "0.00"})
    species_fmt = workbook.add_format({"italic": True})
    numeric_cols = set(CLIENT_OUTPUT_NUMERIC_COLUMNS)
    for col_idx, col_name in enumerate(client_df.columns):
        worksheet.write(0, col_idx, col_name, corrected_hdr_fmt if "Corrected" in col_name else header_fmt)
        width = 15
        if col_name in ("Identifier", "Species"):
            width = 18
        elif "Corrected" in col_name:
            width = 22
        cell_format = num_fmt if col_name in numeric_cols else species_fmt if col_name == "Species" else None
        worksheet.set_column(col_idx, col_idx, width, cell_format)
    if duplicate_row_mask is not None and not client_df.empty:
        duplicate_row_fmt = workbook.add_format({"bg_color": "#FDE68A"})
        last_col = len(client_df.columns) - 1
        for row_idx, is_duplicate in duplicate_row_mask.fillna(False).astype(bool).items():
            if not bool(is_duplicate):
                continue
            excel_row = int(row_idx) + 1
            worksheet.conditional_format(
                excel_row,
                0,
                excel_row,
                last_col,
                {
                    "type": "formula",
                    "criteria": "=TRUE",
                    "format": duplicate_row_fmt,
                },
            )

    # Keep one blank separator column between data columns and equipment/method text.
    equip_title_col = len(client_df.columns) + 1
    equip_value_col = equip_title_col + 1

    def _excel_col_name(col_idx: int) -> str:
        name = ""
        index = int(col_idx)
        while index >= 0:
            index, rem = divmod(index, 26)
            name = chr(65 + rem) + name
            index -= 1
        return name

    equip_title_fmt = workbook.add_format({"bold": True})
    equip_text_fmt = workbook.add_format({"bold": True})
    d13_std, d18_std, n13, n18 = precision_override if precision_override is not None else _compute_shp2l_precision(source_df)
    worksheet.write(1, equip_title_col, "Equiment:", equip_title_fmt)
    worksheet.write(1, equip_value_col, "ThermoFisher Scientific MAT253 gas isotope ratio mass spectrometer", equip_text_fmt)
    worksheet.write(2, equip_value_col, "Kiel IV automated carbonate preparation device", equip_text_fmt)
    worksheet.write(4, equip_title_col, "Standard deviation of SHP2L over measurement period:", equip_title_fmt)
    worksheet.write(5, equip_value_col, f"{d13_std:.2f} \u2030 for δ¹³C")
    worksheet.write(6, equip_value_col, f"{d18_std:.2f} \u2030 for δ¹⁸O")
    worksheet.write(7, equip_value_col, f"δ¹³C n={n13}, δ¹⁸O n={n18}")
    textbox_anchor = f"{_excel_col_name(equip_value_col)}10"
    worksheet.insert_textbox(
        textbox_anchor,
        MATERIALS_METHODS_TEXT,
        {"width": 620, "height": 580, "line": {"color": "#4F81BD"}},
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
    precision_source_df: pd.DataFrame | None = None,
    precision_override: tuple[float, float, int, int] | None = None,
    identifier_source: str = "identifier1",
    sample_source: str = "comment",
    species_source: str = "species",
    show_sequence: bool = True,
    client_output_df: pd.DataFrame | None = None,
) -> tuple[bytes, str]:
    towrite = io.BytesIO()
    selected_standards = selected_standards or []
    data_sheet = _build_data_sheet(df, selected_standards)
    if client_output_df is None:
        client_df = _round_client_output_columns(
            _build_client_output_frame(
                data_sheet,
                comment_map=comment_map,
                identifier_source=identifier_source,
                sample_source=sample_source,
                species_source=species_source,
                show_sequence=show_sequence,
            )
        )
        client_df["__identifier_2_key"] = data_sheet.get(
            "Identifier 2",
            pd.Series(index=data_sheet.index, dtype=object),
        )
    else:
        client_df = client_output_df.copy()
        if "Species" in client_df.columns and not is_raw_client_output_source(species_source):
            client_df["Species"] = client_df["Species"].map(format_academic_species_name)
        client_df = _round_client_output_columns(client_df)
    if client_output_df is None and "Sequence" in client_df.columns:
        client_df = client_df.sort_values(
            by=["Sequence", "Identifier", "Sample #"],
            ascending=[True, True, True],
            na_position="last",
            kind="mergesort",
        ).reset_index(drop=True)
    duplicate_summary = summarize_client_output_duplicates(client_df)
    export_df = client_df.drop(
        columns=[column for column in client_df.columns if str(column).startswith("__")],
        errors="ignore",
    )
    precision_df = precision_source_df if precision_source_df is not None else df
    with pd.ExcelWriter(towrite, engine="xlsxwriter") as writer:
        sheet_name = "Client Output"
        export_df.to_excel(writer, index=False, sheet_name=sheet_name)
        _format_client_output_worksheet(
            writer,
            sheet_name,
            export_df,
            source_df=precision_df,
            precision_override=precision_override,
            duplicate_row_mask=duplicate_summary.get("duplicate_row_mask"),
        )
    towrite.seek(0)
    return towrite.getvalue(), _build_client_filename(client_name or "", export_df)
