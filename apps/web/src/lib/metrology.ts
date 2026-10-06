export const METROLOGY_API = "/api/irms/metrology";
export const isotopes = ["d13c", "d18o"] as const;
export type Isotope = typeof isotopes[number];
export const isotopeLabel: Record<Isotope, string> = { d13c: "δ¹³C", d18o: "δ¹⁸O" };
export type Pair<T> = Record<Isotope, T>;
export type Decision = { actor: string; reason: string };
export type Assigned = { value: number | null; uncertainty: number | null; uncertainty_type: "unset" | "standard" | "expanded"; k: number | null; scale: string; coverage_probability: number | null };
export type Material = { id: string; created_at: string; name: string; lot: string; supplier: string; aliases: string[]; assigned: Pair<Assigned>; certificate: string; issue_date: string; valid_until: string; traceability: string; verified: boolean; revision_of: string | null };
export type Limits = { internal_sd: Pair<number>; external_sd: Pair<number>; bias: Pair<number | null>; max_unknowns_between_qc: number; minimum_qc: number };
export type Component = { name: string; u: number; covers: string[]; rationale: string };
export type LinearCorrection = { name: string; predictor: "mass_ug" | "i44_v" | "pressure_mismatch_v" | "sequence"; slope: number; u_slope: number; center: number; domain: { low: number; high: number }; training_evidence: string; validation_evidence: string; independence_rationale: string; predictor_uncertainty_rationale: string; evidence_asset_ids: string[] };
export type MethodConfig = {
  name: string; intended_use: string; laboratory: string; instrument: string; configuration: string; tunebook: string;
  reaction_temperature_c: number | null; preparation: string; acquisition: string; acquisition_timezone?: string;
  anchor_ids: string[]; qc_id: string | null; ranges: Record<string, { low: number; high: number } | null>;
  qc: Limits; precision: Pair<number | null>; precision_evidence: string; precision_source: "validation" | "historical_qc";
  precision_period: string | null; independence_rationale: string; additional_components: Pair<Component[]>;
  coverage_factor: number; coverage_rationale: string; qualification_interval_days: number | null;
  qtegra_linearity: "recorded" | "enabled" | "disabled"; qtegra_drift_correction: false; required_tests: string[];
  normalization_covariance: Pair<number[][] | null>;
  corrections?: Pair<LinearCorrection | null>;
  correction_validation?: { minimum_qc:number; minimum_sd_reduction_fraction:number; practical_effect:Pair<number> };
};
export type Normalization = { assigned: number[]; measured: number[]; slope: number; intercept: number; formula: string; input_covariance: number[][]; parameter_covariance: number[][] };
export type Method = { id: string; status: string; version: number; revision: number; config: MethodConfig; normalization?: Pair<Normalization>; approval?: Decision & { at: string } };
export type Stats = { n: number; mean: number | null; sd: number | null; se_mean: number | null };
export type Fit = { screening_status?:string; practical_threshold?:number; status: string; n: number; slope?: number; intercept?: number; slope_se?: number; residual_standard_error?: number; covariance?: number[][]; effect_span?: number; r_squared?: number; slope_ci95?: number[]; interpretation?: string; points?: { id?: string; x: number; y: number }[]; residuals?: number[] };
export type Diagnostics = { materials: { material_id: string; label: string; n: number; intensity_pressure_correlation: number | null; collinearity_warning: boolean; mass_to_co2_pressure: Fit; co2_pressure_to_i44: Fit; mass_to_i44: Fit; isotopes: Pair<{ repeatability: Stats; repeatability_by_mass?: (Stats & { mass_ug: number })[]; within_mass_pooled_sd?: number | null; mass_dependence: Fit; intensity_dependence: Fit; pressure_residual: Fit; pressure_dependence?: Fit; drift: Fit; memory: Fit }> }[]; note: string };
export type IsotopeResult = { u_prec?: number; u_corr?: number; residual_to_assigned?: number; processing?: Record<string, unknown>; value: number; u_norm: number; extrapolated: boolean; budget?: { components: Component[]; u_combined: number; expanded_uncertainty: number; k: number } };
export type Measurement = { identifier1?: string; identifier2?: string; species?: string; material_id?: string; pressure_mismatch_v?: number; id: string; sequence: number; source_index: string; label: string; comment: string; reference: string; sample_type: string; source_role: string; d13c: number | null; d18o: number | null; d13c_sd: number | null; d18o_sd: number | null; i44_v: number | null; mass_ug: number | null; acquired_at: string | null; cycle_count: number; role?: string; issues?: string[]; accepted_issues?: string[]; reviews?: {actor: string; reason: string; at: string; issues: string[]}[]; excluded?: boolean; isotopes?: Partial<Pair<IsotopeResult>> };
export type CorrectionReview = { status:string; before:Stats; after:Stats; paired_n:number; total_qc:number; sd_reduction_fraction:number|null; reduction_interval95:number[]|null; reasons:string[]; criteria:{minimum_qc:number; minimum_sd_reduction_fraction:number}; points:{id:string;sequence:number;before:number;after:number;i44_v:number|null;pressure_mismatch_v:number|null}[] };
export type Evaluation = { correction_review?:Pair<CorrectionReview>; diagnostics_comparable?:Diagnostics; id: string; run_id: string; method_id: string; ready: boolean; blockers: string[]; warnings: string[]; diagnostics_after?: Diagnostics; diagnostics: Diagnostics; results: Measurement[]; normalization: Partial<Pair<Normalization>>; qc: { n: number; passed: boolean; isotopes: Pair<Stats & { bias: number | null; target: number | null; passed: boolean; imported?:Stats }> } };
export type Run = { source_kind?:string; calibration_verification?:string; synthetic?:boolean; input_basis?: string; processing_evidence?: string; external_method_id?: string; preapplied_corrections?: string[]; id: string; label: string; context: string; status: string; method_id: string | null; qualification_id: string | null; revision: number; analysis_count: number; type_counts: Record<string, number>; warnings: string[]; acquired_date: string | null; created_at: string; latest_evaluation_id: string | null; acquisition_complete: boolean; masses_ug: Record<string, number>; raw_import_id: string };
export type RunDetail = Run & { source: { sha256: string; filename: string; parser_version: string; analysis_count: number; cycle_count: number; mapping: Record<string, string | null>; isotope_basis: string; intensity_basis: string }; measurements: Measurement[]; evaluation: Evaluation | null; diagnostics?: Diagnostics; releases: { id: string; at: string }[]; release_blockers: string[]; exclusions: { measurement_id: string; reason: string; evidence: string }[] };
export type Qualification = { id: string; method_id: string; status: string; trigger: string; required_tests: string[]; carousel: { material_id: string; mass_ug: number; replicates: number }[]; tests: (Decision & { id: string; name: string; result: string; value: number | null; unit: string; criterion: string })[]; assets: { id: string; filename: string; interpretation: string; sha256: string }[]; effects: Record<string, Decision & { decision: string; evidence: string }>; approval?: Decision & { at: string } };
export type HistoryGroup = { population_label?:string; results_session_id?:string|null; data_origin?:string; value_basis?:string; key: string; method_id: string; method_version: number; material: Material; configuration: string; period_key: string; run_count: number; precision_eligible: boolean; isotopes: Pair<Stats & { status: string; target: number | null; limits: number[] | null; flags: { id: string; rule: string }[]; points: { id: string; value: number; at: string; run_id: string; qc_passed: boolean }[] }> };
export type Report = { id: string; kind: string; created_at: string; sha256: string; target_id: string | null };
export type OperationalManifest = { counts:Record<string,number>; sessions:{id:string;name:string;project:string;client:string;run_count:number;unique_analyses:number}[] };
export type State = { operational?:OperationalManifest|null; results_sessions?: ResultsSession[]; demo?: boolean; status: string; active_method: Method | null; next_review: string | null; methods: Method[]; materials: Material[]; qualifications: Qualification[]; runs: Run[]; history: HistoryGroup[]; reports: Report[]; periods: { id: string; name: string; statistics: Pair<Stats>; reason: string }[]; interventions: { id: string; kind: string; instrument: string; status: string; required_tests: string[]; reason: string; created_at: string }[]; audit: { id: number; at: string; action: string; actor: string; reason: string; hash: string; entity_id: string; before: unknown; after: unknown }[]; test_catalog: string[]; sample_types: Record<string, string>; event_tests: Record<string, string[]> };

export async function metroRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${METROLOGY_API}${path}`, { ...options, cache: "no-store", headers: { ...(options?.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...options?.headers } });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    const message = Array.isArray(payload.detail) ? payload.detail.map((d: { loc?: string[]; msg?: string }) => `${d.loc?.join(" · ")}: ${d.msg}`).join("; ") : payload.detail;
    throw new Error(message || `Request failed (${response.status})`);
  }
  return response.json();
}

export const fmt = (value: number | null | undefined, digits = 3) => value == null ? "Unset" : value.toFixed(digits);
export const human = (value: string) => value.replaceAll("_", " ");

export type ResultsSession = {
  chart_settings?: {carbonate_material: "calcite" | "aragonite"; diagnostic_material_id?: string | null};
  residual_overrides?: Record<string, ResidualOverride>;
  id: string; name: string; client: string; project: string; context: "routine" | "qualification";
  method_id: string; qualification_id: string | null; method_name: string; intended_use: string;
  input_basis: "already_vpdb" | "instrument_delta"; preapplied_corrections: Isotope[]; processing_evidence: string;
  calibration_verification?:string; notes: string; run_ids: string[]; groups: Record<string,string>; status: string; analysis_count: number;
  acquired_date: string | null; created_at: string; sample_groups: string[]; simulation: boolean;
};
export type SessionExport = { id: string; filename: string; format: string; group: string | null; rows: number; created_at: string };
export type ResultsSessionDetail = ResultsSession & { runs: RunDetail[]; method: Method | null; qualification: Qualification | null;
  qualification_run_id: string | null; history: HistoryGroup[]; exports: SessionExport[]; sources?:{id:string;filename:string;relative_path:string;sha256:string;size:number;disposition:string;run_id:string|null}[] };

export type SessionRow = Measurement & { run_id: string; run_label: string; workbook_sequence: number; sample_group: string; evaluation_id: string | null; accepted_issues?: string[] };
export type SessionAnalysis = { rows: SessionRow[]; diagnostics_before: Diagnostics; diagnostics_after: Diagnostics; correction_review: Pair<CorrectionReview>; workbooks: number;
  residual_previews?: Record<string, Fit & {model?:{slope:number;quad:number;degree:number;x_ref:number};before?:Stats;after?:Stats}>;
  outliers: { method: string; threshold: number; flags: { measurement_id: string; run_id: string; isotope: Isotope; value: number }[] };
  qc_statistics: Pair<{ imported: Stats; final: Stats }> };

export type ResidualOverride = {enabled:boolean;algorithm:"linear"|"quadratic";slope:number|null;quadratic:number|null;center:number|null;offset:number;practical_threshold:number};
