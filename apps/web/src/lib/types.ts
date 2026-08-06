export type JsonRecord = Record<string, unknown>;

export type FigurePayload = JsonRecord & {
  data?: Array<JsonRecord>;
  layout?: JsonRecord;
};

export type ChartBundle = {
  session_id: string;
  figures: Record<string, FigurePayload>;
  tables?: Record<string, Array<JsonRecord>>;
  summary?: JsonRecord;
};

export type SessionSnapshot = {
  session_id: string;
  session_name?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  source_files: Array<Record<string, unknown>>;
  row_count: number;
  cycles_row_count: number;
  errors: string[];
  calibration: JsonRecord;
  processing: JsonRecord;
  autosave: JsonRecord;
  preview: Array<JsonRecord>;
};

export type SessionArtifactKind = "events" | "snapshot" | "cycles" | "metadata" | "state";

export type SessionArtifactPayload = {
  kind: SessionArtifactKind;
  label: string;
  format: "events" | "table" | "object";
  items?: Array<JsonRecord>;
  columns?: string[];
  rows?: Array<JsonRecord>;
  data?: unknown;
  row_count?: number;
  truncated?: boolean;
};

export type ImportResult = {
  session: SessionSnapshot;
};

export type SaturationCorrectionMethod =
  | "cycle_mean"
  | "first_valid_cycle"
  | "last_valid_cycle"
  | "reference_gas_intensity"
  | "first_cycle"
  | "cycle_relative_mismatch"
  | "cycle_symmetric_mismatch"
  | "cycle_mean_intensity"
  | "cycle_intensity_weighted_mismatch"
  | "cycle_two_term_mean_mismatch"
  | "cycle_plateau";

export type LinearityConfig = {
  apply: boolean;
  intensity_col: string;
  use_diff_intensity: boolean;
  cycle_intensity_aggregation: "run_median" | "first_valid_cycle" | "last_valid_cycle";
  quadratic: boolean;
  max_sample_intensity?: number | null;
  manual_override_enabled: boolean;
  line_1_offset: number;
  line_2_offset: number;
  line_1_offset_d13?: number | null;
  line_1_offset_d18?: number | null;
  line_2_offset_d13?: number | null;
  line_2_offset_d18?: number | null;
  manual_d13_per_10v: number;
  manual_d18_per_10v: number;
  manual_d13_per_10v2: number;
  manual_d18_per_10v2: number;
  [key: string]: unknown;
};

export type CalibrationConfig = {
  selected_standards: string[];
  calibration_type: "Z-Score" | "IQR";
  carbonate_material: "calcite" | "aragonite";
  sigma_level: number;
  iqr_multiplier: number;
  independent_isotope_outliers: boolean;
  color_param: string;
  z_axis: string;
  precision_date_range?: [string | null, string | null] | null;
  linearity: LinearityConfig;
  [key: string]: unknown;
};

export type CalibrationPrecisionSummary = {
  standard: string;
  total_rows: number;
  included_d13: number;
  included_d18: number;
  included_pct_d13: number;
  included_pct_d18: number;
  d13_precision?: number | null;
  d18_precision?: number | null;
  d13_average?: number | null;
  d18_average?: number | null;
  d13_linearity_corrected_precision?: number | null;
  d18_linearity_corrected_precision?: number | null;
  line_precisions: Record<string, Record<string, number | null>>;
  [key: string]: unknown;
};

export type CalibrationOfficialValue = {
  standard: string;
  isotopic_value_type: string;
  value?: number | null;
  source?: string | null;
};

export type CalibrationWorkspace = {
  session_id: string;
  config: CalibrationConfig;
  available_values: {
    standards: string[];
    color_params: string[];
    z_axis_options: string[];
    min_date?: string | null;
    max_date?: string | null;
    [key: string]: unknown;
  };
  figures: Record<string, FigurePayload>;
  linearity_figures: Record<string, FigurePayload>;
  precision_summaries: CalibrationPrecisionSummary[];
  standard_sections: Array<JsonRecord & {
    standard: string;
    d13_outliers: Array<JsonRecord>;
    d18_outliers: Array<JsonRecord>;
    d13_figure?: FigurePayload;
    d18_figure?: FigurePayload;
  }>;
  selected_standard_official_values: CalibrationOfficialValue[];
  linearity_fits: JsonRecord;
};

export type ProcessingConfig = {
  selected_identifier: string;
  x_axis_option: "By Identifier 2" | "By Sequence";
  color_param: string;
  z_axis: string;
  species_name_map: Record<string, string>;
  identifier1_name_map: Record<string, string>;
  apply_shared_linearity_to_partially_saturated: boolean;
  enable_saturation_correction: boolean;
  saturation_correction_method: SaturationCorrectionMethod;
  saturation_correction_method_d13: SaturationCorrectionMethod;
  saturation_correction_method_d18: SaturationCorrectionMethod;
  signal_range: [number, number];
  leak_range: [number, number];
  d13c_range: [number, number];
  d18o_range: [number, number];
  statistical_outlier_method: "Z-Score" | "IQR";
  sigma_level_data: number;
  iqr_multiplier_data: number;
  overlays: {
    show_statistical_outliers: boolean;
    show_range_outliers: boolean;
    show_manual_outliers: boolean;
    show_saturated_collectors: boolean;
    show_saturated_samples: boolean;
    show_failed_samples: boolean;
    [key: string]: unknown;
  };
  manual_linearity_override: JsonRecord;
  export: {
    include_outliers: boolean;
    selected_ids: string[];
    interpolate_outliers: boolean;
    client_name?: string | null;
    comment_map: Record<string, string>;
    [key: string]: unknown;
  };
  [key: string]: unknown;
};

export type OutlierTable = {
  name: string;
  title?: string;
  rows: Array<JsonRecord>;
  [key: string]: unknown;
};

export type ProcessingSummaryMetric = {
  metric: string;
  value: number | string;
  details: string;
};

export type ProcessingSummary = {
  total_unique_samples: number;
  total_measurements: number;
  statistical_outliers: number;
  d13c_range_outliers: number;
  d18o_range_outliers: number;
  signal_intensity_outliers: number;
  leak_rate_outliers: number;
  failed_samples: number;
  partially_failed_recovered_mean: number;
  fully_saturated_collectors: number;
  final_analyses: number;
  metrics: ProcessingSummaryMetric[];
};

export type ProcessingAvailableValues = {
  identifiers: string[];
  export_identifiers: string[];
  identifier1_sources: string[];
  species: string[];
  color_params: string[];
  z_axis_options: string[];
};

export type IdentifierFigureSet = {
  identifier: string;
  d13c: FigurePayload;
  d18o: FigurePayload;
  has_calibrated_d13c: boolean;
  has_calibrated_d18o: boolean;
};

export type SpeciesSection = {
  species: string;
  identifier_count: number;
  identifier_figures: IdentifierFigureSet[];
  outlier_tables: OutlierTable[];
};

export type ProcessingWorkspace = {
  session_id: string;
  config: ProcessingConfig;
  summary: ProcessingSummary;
  available_values: ProcessingAvailableValues;
  overview_figures: Record<string, FigurePayload>;
  species_sections: SpeciesSection[];
  outlier_tables: OutlierTable[];
  edit_state: {
    edited_rows: string[];
    original_delta_values: Record<string, number>;
    original_missing_delta_tokens: string[];
    original_std_values: Record<string, number>;
    original_missing_std_tokens: string[];
    manual_outlier_overrides: Record<string, boolean>;
    restored_delta_tokens: string[];
    [key: string]: unknown;
  };
  export_state: {
    filename: string;
    client_name?: string | null;
    selected_ids: string[];
    include_outliers: boolean;
    interpolate_outliers: boolean;
    [key: string]: unknown;
  };
  [key: string]: unknown;
};

export type LinearityPreviewRow = {
  row_label: string;
  identifier1?: string;
  identifier2?: string;
  species?: string;
  collector_status?: string;
  line?: number | null;
  d13_raw?: number | null;
  d18_raw?: number | null;
  d13_calibrated?: number | null;
  d18_calibrated?: number | null;
  signal?: number | null;
  leak_rate?: number | null;
  d13_cycles_excluded?: number | null;
  d18_cycles_excluded?: number | null;
  intensities: Record<string, number | null>;
  attributes: Record<string, string | number | null>;
};

export type LinearityPreviewTrace = {
  chart_key?: string;
  trace_index?: number;
  trace_name?: string;
  row_labels?: string[];
  isotope_key?: "d13C" | "d18O" | "cross";
};

export type LinearityPreviewChart = {
  chart_key: string;
  traces: LinearityPreviewTrace[];
};

export type ProcessingLinearityPreviewData = {
  session_id: string;
  intensity_col: string;
  fits: JsonRecord;
  coefficients: JsonRecord;
  rows: LinearityPreviewRow[];
  charts?: LinearityPreviewChart[];
};

export type EditTarget = {
  row_label: string;
  isotope_key: "d13C" | "d18O";
};

export type EditAction = {
  action:
    | "set_value"
    | "offset"
    | "interpolate"
    | "reset_to_original"
    | "set_outlier_override"
    | "manual_outlier"
    | "clear_manual_outlier"
    | "reset_all";
  targets: EditTarget[];
  value?: number | null;
  offset?: number | null;
  stdev?: number | null;
  [key: string]: unknown;
};

export type CycleDiagnosticsPayload = {
  session_id: string;
  target: JsonRecord;
  inline_summary?: string;
  figure: FigurePayload;
  saturation_correction: JsonRecord;
  table: Array<JsonRecord>;
  cycle_mean?: JsonRecord;
  [key: string]: unknown;
};

export type ExportRequest = {
  output_type?: "dataset" | "client_output";
  include_outliers: boolean;
  selected_ids: string[];
  interpolate_outliers: boolean;
  client_name?: string | null;
  comment_map: Record<string, string>;
  restore_stdev?: boolean;
  restore_stdev_cap?: number;
  [key: string]: unknown;
};

export type ClientOutputDuplicateCheckResponse = {
  duplicate_row_count: number;
  duplicate_identifier1_identifier2_species_values: string[];
  duplicate_rows: Array<JsonRecord>;
};
