from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .constants import CYCLE1_SIGNAL_SAMP44_COL


SaturationCorrectionMethod = Literal[
    "cycle_mean",
    "first_valid_cycle",
    "last_valid_cycle",
    "reference_gas_intensity",
    "first_cycle",
    "cycle_relative_mismatch",
    "cycle_symmetric_mismatch",
    "cycle_mean_intensity",
    "cycle_intensity_weighted_mismatch",
    "cycle_two_term_mean_mismatch",
    "cycle_plateau",
]

JobState = Literal["queued", "running", "succeeded", "failed", "cancel_requested", "cancelled"]


class JobSnapshot(BaseModel):
    job_id: str
    kind: str
    state: JobState
    progress: float = Field(default=0.0, ge=0.0, le=100.0)
    phase: str = "queued"
    message: str = ""
    session_id: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    revision: int = 0
    cancellable: bool = True


class SessionSnapshot(BaseModel):
    session_id: str
    session_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    source_files: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    cycles_row_count: int = 0
    errors: list[str] = Field(default_factory=list)
    calibration: dict[str, Any] = Field(default_factory=dict)
    processing: dict[str, Any] = Field(default_factory=dict)
    autosave: dict[str, Any] = Field(default_factory=dict)
    preview: list[dict[str, Any]] = Field(default_factory=list)


class ImportResult(BaseModel):
    session: SessionSnapshot


class ImportFieldParsingRule(BaseModel):
    source_column: str | None = None
    mode: Literal["direct", "split", "regex"] = "direct"
    delimiter: str = " - "
    part_index: int = 0
    regex_pattern: str = ""
    regex_group: int | str = 1


class ImportWorkbookParsingConfig(BaseModel):
    file_index: int | None = Field(default=None, ge=0)
    file_name: str = ""
    software: Literal["qtegra", "isodat", "generic"] = "generic"
    identifier1: ImportFieldParsingRule = Field(default_factory=ImportFieldParsingRule)
    identifier2: ImportFieldParsingRule = Field(default_factory=ImportFieldParsingRule)
    species: ImportFieldParsingRule = Field(default_factory=ImportFieldParsingRule)


class ImportParsingConfig(BaseModel):
    files: list[ImportWorkbookParsingConfig] = Field(default_factory=list)


class ImportFilePreview(BaseModel):
    file_index: int
    file_name: str
    software: Literal["qtegra", "isodat", "generic"] = "generic"
    columns: list[str] = Field(default_factory=list)
    row_count: int = 0
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    suggested_config: ImportWorkbookParsingConfig


class ImportPreviewResponse(BaseModel):
    files: list[ImportFilePreview] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ImportNamingUpdate(BaseModel):
    species_name_map: dict[str, str] = Field(default_factory=dict)
    identifier1_name_map: dict[str, str] = Field(default_factory=dict)


class ImportNamingWorkspace(ImportNamingUpdate):
    identifier1_sources: list[str] = Field(default_factory=list)
    species_sources: list[str] = Field(default_factory=list)


class AutosaveSettingsUpdate(BaseModel):
    enabled: bool


class FilterConfig(BaseModel):
    identifier_filter: list[str] = Field(default_factory=list)
    d13c_range: tuple[float, float] | None = None
    d18o_range: tuple[float, float] | None = None
    color_param: str | None = None


class LinearityConfig(BaseModel):
    apply: bool = False
    intensity_col: str = CYCLE1_SIGNAL_SAMP44_COL
    use_diff_intensity: bool = False
    cycle_intensity_aggregation: Literal["run_median", "first_valid_cycle", "last_valid_cycle"] = "run_median"
    quadratic: bool = False
    max_sample_intensity: float | None = None
    manual_override_enabled: bool = False
    line_1_offset: float = 0.0
    line_2_offset: float = 0.0
    line_1_offset_d13: float | None = None
    line_1_offset_d18: float | None = None
    line_2_offset_d13: float | None = None
    line_2_offset_d18: float | None = None
    manual_d13_per_10v: float = 0.0
    manual_d18_per_10v: float = 0.0
    manual_d13_per_10v2: float = 0.0
    manual_d18_per_10v2: float = 0.0


class CalibrationLinearityUpdateRequest(BaseModel):
    linearity: LinearityConfig
    selected_standards: list[str] | None = None


class CalibrationConfig(BaseModel):
    selected_standards: list[str] = Field(default_factory=list)
    calibration_type: Literal["Z-Score", "IQR"] = "IQR"
    carbonate_material: Literal["calcite", "aragonite"] = "calcite"
    sigma_level: float = 1.0
    iqr_multiplier: float = 1.5
    independent_isotope_outliers: bool = True
    color_param: str = "d 18O/16O  Mean"
    z_axis: str = "1  Cycle Int  Samp  44"
    precision_date_range: tuple[str | None, str | None] | None = None
    linearity: LinearityConfig = Field(default_factory=LinearityConfig)


class CalibrationAvailableValues(BaseModel):
    standards: list[str] = Field(default_factory=list)
    color_params: list[str] = Field(default_factory=list)
    z_axis_options: list[str] = Field(default_factory=list)
    min_date: str | None = None
    max_date: str | None = None


class CalibrationPrecisionSummary(BaseModel):
    standard: str
    total_rows: int = 0
    included_d13: int = 0
    included_d18: int = 0
    included_pct_d13: float = 0.0
    included_pct_d18: float = 0.0
    d13_precision: float | None = None
    d18_precision: float | None = None
    d13_average: float | None = None
    d18_average: float | None = None
    d13_linearity_corrected_precision: float | None = None
    d18_linearity_corrected_precision: float | None = None
    line_precisions: dict[str, dict[str, float | None]] = Field(default_factory=dict)


class CalibrationStandardSection(BaseModel):
    standard: str
    d13_outliers: list[dict[str, Any]] = Field(default_factory=list)
    d18_outliers: list[dict[str, Any]] = Field(default_factory=list)
    d13_figure: dict[str, Any] = Field(default_factory=dict)
    d18_figure: dict[str, Any] = Field(default_factory=dict)


class CalibrationOfficialValue(BaseModel):
    standard: str
    isotopic_value_type: str
    value: float | None = None
    source: str | None = None


class CalibrationOfficialValueUpsertRequest(BaseModel):
    standard: str
    isotopic_value_type: str
    value: float
    source: str | None = None


class CalibrationOfficialValueDeleteResult(BaseModel):
    standard: str
    isotopic_value_type: str | None = None
    deleted_rows: int = 0


class CalibrationWorkspace(BaseModel):
    session_id: str
    config: CalibrationConfig = Field(default_factory=CalibrationConfig)
    available_values: CalibrationAvailableValues = Field(default_factory=CalibrationAvailableValues)
    figures: dict[str, dict[str, Any]] = Field(default_factory=dict)
    linearity_figures: dict[str, dict[str, Any]] = Field(default_factory=dict)
    precision_summaries: list[CalibrationPrecisionSummary] = Field(default_factory=list)
    standard_sections: list[CalibrationStandardSection] = Field(default_factory=list)
    selected_standard_official_values: list[CalibrationOfficialValue] = Field(default_factory=list)
    linearity_fits: dict[str, Any] = Field(default_factory=dict)


class ProcessingOverlayConfig(BaseModel):
    show_statistical_outliers: bool = False
    show_range_outliers: bool = False
    show_manual_outliers: bool = False
    show_saturated_collectors: bool = True
    show_saturated_samples: bool = True
    show_failed_samples: bool = True


class ProcessingLinearityOverrideConfig(BaseModel):
    enabled: bool = False
    use_diff_intensity: bool = False
    quadratic: bool = False
    max_sample_signal: float | None = Field(default=None, ge=0.0)
    d13_per_10v: float = 0.0
    d18_per_10v: float = 0.0
    d13_per_10v2: float = 0.0
    d18_per_10v2: float = 0.0


class ProcessingExportConfig(BaseModel):
    include_outliers: bool = False
    selected_ids: list[str] = Field(default_factory=lambda: ["All"])
    interpolate_outliers: bool = False
    client_name: str | None = None
    comment_map: dict[str, str] = Field(default_factory=dict)


class ProcessingWorkspaceConfig(BaseModel):
    selected_identifier: str = "All"
    x_axis_option: Literal["By Identifier 2", "By Sequence"] = "By Sequence"
    color_param: str = "Date"
    z_axis: str = "1  Cycle Int  Samp  44"
    species_name_map: dict[str, str] = Field(default_factory=dict)
    identifier1_name_map: dict[str, str] = Field(default_factory=dict)
    apply_shared_linearity_to_partially_saturated: bool = True
    enable_saturation_correction: bool = False
    saturation_correction_method: SaturationCorrectionMethod = "reference_gas_intensity"
    saturation_correction_method_d13: SaturationCorrectionMethod = "reference_gas_intensity"
    saturation_correction_method_d18: SaturationCorrectionMethod = "reference_gas_intensity"
    signal_range: tuple[float, float] = (0.0, 50.0)
    leak_range: tuple[float, float] = (0.0, 1000.0)
    d13c_range: tuple[float, float] = (-10.0, 10.0)
    d18o_range: tuple[float, float] = (-10.0, 10.0)
    statistical_outlier_method: Literal["Z-Score", "IQR"] = "Z-Score"
    sigma_level_data: float = 4.0
    iqr_multiplier_data: float = 1.5
    overlays: ProcessingOverlayConfig = Field(default_factory=ProcessingOverlayConfig)
    manual_linearity_override: ProcessingLinearityOverrideConfig = Field(
        default_factory=ProcessingLinearityOverrideConfig
    )
    export: ProcessingExportConfig = Field(default_factory=ProcessingExportConfig)


class ProcessingConfig(ProcessingWorkspaceConfig):
    pass


class EditTarget(BaseModel):
    row_label: str
    isotope_key: Literal["d13C", "d18O"]


class EditAction(BaseModel):
    action: Literal[
        "set_value",
        "offset",
        "interpolate",
        "reset_to_original",
        "reset_all",
        "set_outlier_override",
    ]
    targets: list[EditTarget] = Field(default_factory=list)
    value: float | None = None
    offset: float | None = None
    stdev: float | None = None
    is_outlier: bool | None = None


class EditBatchRequest(BaseModel):
    """A group of edits that should be committed as one session update."""

    edits: list[EditAction] = Field(min_length=1, max_length=100)


class ChartBundle(BaseModel):
    session_id: str
    figures: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tables: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)


class OutlierTable(BaseModel):
    name: str
    title: str | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)


class ExportRequest(BaseModel):
    include_outliers: bool = False
    selected_ids: list[str] = Field(default_factory=lambda: ["All"])
    interpolate_outliers: bool = False
    client_name: str | None = None
    comment_map: dict[str, str] = Field(default_factory=dict)
    restore_stdev: bool = False
    restore_stdev_cap: float = Field(default=0.2, ge=0.0)
    output_type: Literal["dataset", "client_output"] = "dataset"


class ClientOutputDuplicateCheckResponse(BaseModel):
    duplicate_row_count: int = 0
    duplicate_identifier1_identifier2_species_values: list[str] = Field(default_factory=list)
    duplicate_rows: list[dict[str, Any]] = Field(default_factory=list)


class ProcessingSummaryMetric(BaseModel):
    metric: str
    value: int | float | str
    details: str = ""


class ProcessingSummary(BaseModel):
    total_unique_samples: int = 0
    total_measurements: int = 0
    statistical_outliers: int = 0
    d13c_range_outliers: int = 0
    d18o_range_outliers: int = 0
    signal_intensity_outliers: int = 0
    leak_rate_outliers: int = 0
    failed_samples: int = 0
    partially_failed_recovered_mean: int = 0
    fully_saturated_collectors: int = 0
    final_analyses: int = 0
    metrics: list[ProcessingSummaryMetric] = Field(default_factory=list)


class ProcessingAvailableValues(BaseModel):
    identifiers: list[str] = Field(default_factory=list)
    export_identifiers: list[str] = Field(default_factory=list)
    identifier1_sources: list[str] = Field(default_factory=list)
    species: list[str] = Field(default_factory=list)
    color_params: list[str] = Field(default_factory=list)
    z_axis_options: list[str] = Field(default_factory=list)


class IdentifierFigureSet(BaseModel):
    identifier: str
    d13c: dict[str, Any] = Field(default_factory=dict)
    d18o: dict[str, Any] = Field(default_factory=dict)
    has_calibrated_d13c: bool = False
    has_calibrated_d18o: bool = False


class SpeciesSection(BaseModel):
    species: str
    identifier_count: int = 0
    identifier_figures: list[IdentifierFigureSet] = Field(default_factory=list)
    outlier_tables: list[OutlierTable] = Field(default_factory=list)


class ProcessingExportState(BaseModel):
    filename: str = "dataset_without_outliers.xlsx"
    client_name: str | None = None
    selected_ids: list[str] = Field(default_factory=lambda: ["All"])
    include_outliers: bool = False
    interpolate_outliers: bool = False


class ProcessingEditState(BaseModel):
    edited_rows: list[str] = Field(default_factory=list)
    original_delta_values: dict[str, float] = Field(default_factory=dict)
    original_missing_delta_tokens: list[str] = Field(default_factory=list)
    original_std_values: dict[str, float] = Field(default_factory=dict)
    original_missing_std_tokens: list[str] = Field(default_factory=list)
    manual_outlier_overrides: dict[str, bool] = Field(default_factory=dict)
    restored_delta_tokens: list[str] = Field(default_factory=list)


class CycleDiagnosticsPayload(BaseModel):
    session_id: str
    target: dict[str, Any] = Field(default_factory=dict)
    inline_summary: str = ""
    figure: dict[str, Any] = Field(default_factory=dict)
    saturation_correction: dict[str, Any] = Field(default_factory=dict)
    intensity_linearity: dict[str, Any] = Field(default_factory=dict)
    table: list[dict[str, Any]] = Field(default_factory=list)
    cycle_mean: dict[str, Any] = Field(default_factory=dict)


class CycleDiagnosticsRequest(BaseModel):
    target: EditTarget


class ProcessingWorkspace(BaseModel):
    session_id: str
    config: ProcessingWorkspaceConfig = Field(default_factory=ProcessingWorkspaceConfig)
    summary: ProcessingSummary = Field(default_factory=ProcessingSummary)
    available_values: ProcessingAvailableValues = Field(default_factory=ProcessingAvailableValues)
    overview_figures: dict[str, dict[str, Any]] = Field(default_factory=dict)
    species_sections: list[SpeciesSection] = Field(default_factory=list)
    outlier_tables: list[OutlierTable] = Field(default_factory=list)
    edit_state: ProcessingEditState = Field(default_factory=ProcessingEditState)
    export_state: ProcessingExportState = Field(default_factory=ProcessingExportState)


class ProcessingLinearityPreviewRow(BaseModel):
    row_label: str
    identifier1: str = ""
    identifier2: str = ""
    species: str = ""
    collector_status: str = ""
    line: float | None = None
    d13_raw: float | None = None
    d18_raw: float | None = None
    d13_calibrated: float | None = None
    d18_calibrated: float | None = None
    signal: float | None = None
    leak_rate: float | None = None
    d13_cycles_excluded: float | None = None
    d18_cycles_excluded: float | None = None
    intensities: dict[str, float | None] = Field(default_factory=dict)
    attributes: dict[str, str | float | None] = Field(default_factory=dict)


class ProcessingLinearityPreviewData(BaseModel):
    session_id: str
    intensity_col: str = CYCLE1_SIGNAL_SAMP44_COL
    fits: dict[str, Any] = Field(default_factory=dict)
    coefficients: dict[str, Any] = Field(default_factory=dict)
    rows: list[ProcessingLinearityPreviewRow] = Field(default_factory=list)
