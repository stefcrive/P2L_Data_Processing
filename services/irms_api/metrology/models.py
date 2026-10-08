from __future__ import annotations

import math
from typing import Literal
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

ISOTOPES = ("d13c", "d18o")
SAMPLE_TYPES = {
    "Conditioning": "conditioning",
    "Delta Standard (DualInlet)": "dual_inlet_standard",
    "Drift Correction": "unsupported_drift",
    "QC Standard": "qc",
    "Ref Gas Calibration": "gas_reference_calibration",
    "Unknown": "unknown",
}
TESTS = [
    "Vacuum", "Magnet scan / background", "Peak center", "Peak shape", "Sensitivity",
    "Signal stability", "Zero enrichment", "Dry CO2 linearity", "Sample/reference mismatch",
    "Dual Inlet", "Independent QC",
]
EVENT_TESTS = {
    "source_opening": ["Vacuum", "Peak center", "Peak shape", "Sensitivity", "Signal stability", "Dry CO2 linearity", "Independent QC"],
    "filament_replacement": ["Peak center", "Peak shape", "Sensitivity", "Signal stability", "Dry CO2 linearity", "Independent QC"],
    "dual_inlet_maintenance": ["Vacuum", "Zero enrichment", "Sample/reference mismatch", "Dual Inlet", "Independent QC"],
    "kiel_intervention": ["Vacuum", "Signal stability", "Independent QC"],
    "acid_system": ["Independent QC"],
    "configuration_change": TESTS,
    "qc_shift": ["Signal stability", "Independent QC"],
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class Decision(StrictModel):
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=3, max_length=4000)


class AssignedValue(StrictModel):
    value: float | None = None
    uncertainty: float | None = Field(default=None, ge=0)
    uncertainty_type: Literal["unset", "standard", "expanded"] = "unset"
    k: float | None = Field(default=None, gt=0)
    scale: str = "VPDB"
    coverage_probability: float | None = Field(default=None, gt=0, lt=1)

    def standard_uncertainty(self) -> float:
        if self.value is None or self.uncertainty is None or self.uncertainty_type == "unset":
            raise ValueError("Assigned value and uncertainty interpretation must be verified")
        if self.scale != "VPDB":
            raise ValueError("This method requires assigned values on VPDB")
        if self.uncertainty_type == "expanded":
            if self.k is None:
                raise ValueError("Expanded uncertainty requires its coverage factor")
            return self.uncertainty / self.k
        return self.uncertainty


class Material(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    lot: str = ""
    supplier: str = ""
    aliases: list[str] = Field(default_factory=list)
    assigned: dict[str, AssignedValue] = Field(default_factory=lambda: {i: AssignedValue() for i in ISOTOPES})
    certificate: str = ""
    issue_date: str = ""
    valid_until: str = ""
    traceability: str = ""
    verified: bool = False
    revision_of: str | None = None

    @model_validator(mode="after")
    def validate_isotopes(self):
        if set(self.assigned) != set(ISOTOPES):
            raise ValueError("Provide both d13c and d18o assigned-value records")
        for value in (self.issue_date, self.valid_until):
            if value:
                date.fromisoformat(value)
        if self.issue_date and self.valid_until and self.issue_date > self.valid_until:
            raise ValueError("Certificate expiry precedes its issue date")
        if self.verified:
            if not self.lot or not self.certificate:
                raise ValueError("A verified material requires a lot and certificate reference")
            for value in self.assigned.values():
                value.standard_uncertainty()
        return self


class Range(StrictModel):
    low: float
    high: float

    @model_validator(mode="after")
    def ordered(self):
        if self.low >= self.high:
            raise ValueError("Range minimum must be below maximum")
        return self


class Limits(StrictModel):
    internal_sd: dict[str, float] = Field(default_factory=lambda: {"d13c": .04, "d18o": .05})
    external_sd: dict[str, float] = Field(default_factory=lambda: {"d13c": .07, "d18o": .10})
    bias: dict[str, float | None] = Field(default_factory=lambda: {"d13c": None, "d18o": None})
    max_unknowns_between_qc: int = Field(default=4, ge=1, le=100)
    minimum_qc: int = Field(default=3, ge=2)

    @model_validator(mode="after")
    def positive_limits(self):
        for group in (self.internal_sd, self.external_sd, self.bias):
            if set(group) != set(ISOTOPES):
                raise ValueError("Specify limits for both isotopes")
            if any(v is not None and (not math.isfinite(v) or v <= 0) for v in group.values()):
                raise ValueError("Acceptance limits must be finite and positive")
        return self


class Component(StrictModel):
    name: str = Field(min_length=1)
    u: float = Field(ge=0)
    covers: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=3)


class LinearCorrection(StrictModel):
    """One centered, independently estimated effect per isotope, before anchoring."""
    name: str = Field(min_length=3)
    predictor: Literal["mass_ug", "i44_v", "pressure_mismatch_v", "sequence"]
    slope: float
    u_slope: float = Field(gt=0)
    center: float
    domain: Range
    training_evidence: str = Field(min_length=3)
    validation_evidence: str = Field(min_length=3)
    independence_rationale: str = Field(min_length=3)
    predictor_uncertainty_rationale: str = Field(min_length=3)
    evidence_asset_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def separate_evidence(self):
        if self.training_evidence == self.validation_evidence:
            raise ValueError("Correction training and independent validation evidence must be distinct")
        if not self.domain.low <= self.center <= self.domain.high:
            raise ValueError("Correction center must be inside its validated domain")
        return self

    @property
    def effect(self):
        return {"mass_ug": "mass_intensity", "i44_v": "mass_intensity",
                "pressure_mismatch_v": "pressure_adjustment", "sequence": "drift"}[self.predictor]


class CorrectionValidation(StrictModel):
    minimum_qc: int = Field(default=6, ge=3)
    minimum_sd_reduction_fraction: float = Field(default=.05, ge=0, lt=1)
    practical_effect: dict[str, float] = Field(default_factory=lambda: {"d13c":.01,"d18o":.02})

    @model_validator(mode="after")
    def complete(self):
        if set(self.practical_effect)!=set(ISOTOPES) or any(not math.isfinite(v) or v<=0 for v in self.practical_effect.values()):
            raise ValueError("Positive practical effect thresholds are required for both isotopes")
        return self


class MethodConfig(StrictModel):
    name: str = "Carbonate δ13C/δ18O"
    intended_use: str = "Carbonate isotope analysis on VPDB"
    laboratory: str = ""
    instrument: str = "Kiel IV + MAT253 Plus + Dual Inlet"
    configuration: str = ""
    tunebook: str = ""
    reaction_temperature_c: float | None = None
    preparation: str = ""
    acquisition: str = ""
    acquisition_timezone: str = "America/Sao_Paulo"
    anchor_ids: list[str] = Field(default_factory=list, max_length=2)
    qc_id: str | None = None
    ranges: dict[str, Range | None] = Field(default_factory=lambda: {k: None for k in ("mass_ug", "i44_v", "d13c", "d18o")})
    qc: Limits = Field(default_factory=Limits)
    precision: dict[str, float | None] = Field(default_factory=lambda: {i: None for i in ISOTOPES})
    precision_evidence: str = ""
    precision_source: Literal["validation", "historical_qc"] = "validation"
    precision_period: str | None = None
    independence_rationale: str = ""
    additional_components: dict[str, list[Component]] = Field(default_factory=lambda: {i: [] for i in ISOTOPES})
    coverage_factor: float = Field(default=2, gt=0)
    coverage_rationale: str = ""
    qualification_interval_days: int | None = Field(default=None, ge=1, le=3650)
    qtegra_linearity: Literal["recorded", "enabled", "disabled"] = "recorded"
    qtegra_drift_correction: Literal[False] = False
    required_tests: list[str] = Field(default_factory=lambda: list(TESTS), min_length=1)
    normalization_covariance: dict[str, list[list[float]] | None] = Field(default_factory=lambda: {i: None for i in ISOTOPES})
    corrections: dict[str, LinearCorrection | None] = Field(default_factory=lambda: {i: None for i in ISOTOPES})
    correction_validation: CorrectionValidation = Field(default_factory=CorrectionValidation)

    @model_validator(mode="after")
    def valid_config(self):
        try:
            ZoneInfo(self.acquisition_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Choose a valid IANA acquisition timezone") from exc
        if len(set(self.anchor_ids)) != len(self.anchor_ids) or self.qc_id in self.anchor_ids:
            raise ValueError("Two distinct anchor lots and an independent QC lot are required")
        if any(not name for name in self.required_tests) or len(set(self.required_tests)) != len(self.required_tests):
            raise ValueError("Qualification test names must be nonempty and distinct")
        for key in ("mass_ug", "i44_v"):
            bounds = self.ranges.get(key)
            if bounds and bounds.low < 0:
                raise ValueError("Mass and intensity ranges cannot include negative values")
        if not {"mass_ug", "i44_v", "d13c", "d18o"}.issubset(self.ranges):
            raise ValueError("Mass, I44 and both isotope ranges must be represented")
        if set(self.precision) != set(ISOTOPES):
            raise ValueError("Specify precision for both isotopes")
        if any(v is not None and (not math.isfinite(v) or v <= 0) for v in self.precision.values()):
            raise ValueError("Precision must be finite and positive")
        if set(self.additional_components) != set(ISOTOPES) or set(self.normalization_covariance) != set(ISOTOPES):
            raise ValueError("Specify uncertainty models for both isotopes")
        if set(self.corrections) != set(ISOTOPES):
            raise ValueError("Specify the correction or null for both isotopes")
        return self


class MethodCommand(Decision):
    config: MethodConfig


class MaterialCommand(Decision):
    material: Material


class CarouselSlot(StrictModel):
    material_id: str
    mass_ug: float = Field(gt=0)
    replicates: int = Field(ge=1, le=100)


class QualificationCommand(Decision):
    method_id: str
    trigger: Literal["periodic", "event"] = "periodic"
    intervention_id: str | None = None
    tests: list[str] = Field(default_factory=list)
    carousel: list[CarouselSlot] = Field(default_factory=list)


class TestCommand(Decision):
    name: str = Field(min_length=1)
    result: Literal["pass", "fail", "not_evaluated"]
    value: float | None = None
    unit: str = ""
    criterion: str = Field(min_length=3)


class EffectCommand(Decision):
    effect: Literal["mass_intensity", "pressure_adjustment", "drift", "memory", "bias"]
    decision: Literal["negligible", "monitor", "restrict_range", "investigate", "reject_correction", "approve_correction"]
    evidence: str = Field(min_length=3)


class InterventionCommand(Decision):
    kind: Literal["source_opening", "filament_replacement", "dual_inlet_maintenance", "kiel_intervention", "acid_system", "configuration_change", "qc_shift"]
    instrument: str = "Kiel IV + MAT253 Plus + Dual Inlet"


class RunCommand(Decision):
    carbonate_material: Literal["calcite", "aragonite"] = "calcite"
    carbonate_correction_preapplied: bool = False
    results_session_id: str | None = None
    sample_group: str = ""
    method_id: str | None = None
    qualification_id: str | None = None
    context: Literal["qualification", "routine"] = "routine"
    label: str = ""
    input_basis: Literal["instrument_delta", "already_vpdb"] = "instrument_delta"
    external_method_id: str | None = None
    processing_evidence: str = ""
    preapplied_corrections: list[Literal["d13c", "d18o"]] = Field(default_factory=list)


class AnnotationCommand(Decision):
    masses_ug: dict[str, float] = Field(default_factory=dict)
    acquisition_complete: bool | None = None
    input_basis: Literal["instrument_delta", "already_vpdb"] | None = None
    external_method_id: str | None = None
    processing_evidence: str | None = None
    preapplied_corrections: list[Literal["d13c", "d18o"]] | None = None

    @model_validator(mode="after")
    def valid_masses(self):
        if any(not math.isfinite(x) or x <= 0 for x in self.masses_ug.values()):
            raise ValueError("Masses must be finite positive micrograms")
        return self


class ExclusionCommand(Decision):
    measurement_id: str
    evidence: str = Field(min_length=3)


class EvaluateCommand(Decision):
    method_id: str | None = None


class ApproveCommand(Decision):
    qualification_id: str
    evaluation_id: str
    activate: bool = True


class ReleaseCommand(Decision):
    evaluation_id: str


class PeriodCommand(Decision):
    method_id: str
    name: str = Field(min_length=1)
    evaluation_ids: list[str] = Field(min_length=2)


class ResultsSessionCommand(Decision):
    name: str = Field(min_length=1, max_length=180)
    client: str = Field(min_length=1, max_length=180)
    project: str = ""
    method_id: str
    context: Literal["routine", "qualification"] = "routine"
    qualification_id: str | None = None
    notes: str = ""
    method_name: str = ""
    intended_use: str = ""
    input_basis: Literal["already_vpdb", "instrument_delta"] = "already_vpdb"
    preapplied_corrections: list[Literal["d13c", "d18o"]] = Field(default_factory=list)
    processing_evidence: str = Field(min_length=3)
    calibration_verification: Literal["documented", "simulation_assumption"] = "documented"


class SessionGroupsCommand(Decision):
    groups: dict[str, str]


class ResidualOverride(StrictModel):
    enabled: bool = False
    algorithm: Literal["linear", "quadratic"] = "linear"
    slope: float | None = None
    quadratic: float | None = None
    center: float | None = None
    offset: float = 0
    practical_threshold: float = Field(default=.01, gt=0)
    application_scope: Literal["fit_population", "all_data"] = "fit_population"
    extrapolate: bool = False
    include_statistical_outliers: bool = False
    u_slope: float | None = Field(default=None, ge=0)
    u_quadratic: float | None = Field(default=None, ge=0)


class ResidualOverrideCommand(Decision):
    material_id: str
    effect: Literal["sample_reference_dependence", "intensity_dependence", "pressure_adjusted_dependence", "pressure_dependence", "pressure_residual", "mass_dependence", "drift", "memory"]
    isotope: Literal["d13c", "d18o"]
    settings: ResidualOverride | None = None


class OutlierScreeningCommand(Decision):
    method: Literal["sigma", "iqr"] = "sigma"
    threshold: float = Field(default=3, ge=.5, le=10, allow_inf_nan=False)
    pressure_adjustment_as_outlier: bool = False


class SessionUncertaintyCommand(Decision):
    coverage_factor: float = Field(gt=0, allow_inf_nan=False)


class SessionFailedCorrectionCommand(Decision):
    enabled: bool


class SessionChartSettingsCommand(Decision):
    carbonate_material: Literal["calcite", "aragonite"] = "calcite"
    diagnostic_material_id: str | None = None


class SessionExportCommand(Decision):
    format: Literal["csv", "json", "zip", "pdf", "xlsx"] = "zip"
    output_type: Literal["combined", "client_output", "dataset"] = "combined"
    include_outliers: bool = False
    group: str | None = None
    client_name: str | None = Field(default=None, max_length=180)
    series_name: str | None = Field(default=None, max_length=180)
    identifier_source: Literal["raw_label", "raw_comment"] = "raw_label"
    sample_source: Literal["raw_label", "raw_comment"] = "raw_comment"


class RowReviewCommand(Decision):
    evaluation_id: str
    measurement_id: str
    issues: list[str] = Field(min_length=1)
