from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from ..domain.calibration.core import CARBONATE_ACID_FRACTIONATION_FACTORS, convert_d18o_carbonate_material
from .models import ISOTOPES, Material, MethodConfig
from .science import anchor_model, budget, corrected_normalize, regression, partial_regression, summary
from .correction_review import comparison_rows, correction_review, screen_effects


def identify(record: dict, config: MethodConfig, materials: dict) -> tuple[str, str | None]:
    # Identifier 1 identifies the session QC even when the export says Unknown
    # or the full label contains an incorrectly attributed species.
    from .importer import measurement_identity

    identifier1 = record.get("identifier1")
    if identifier1 is None:
        identifier1 = measurement_identity(record).get("identifier1")
    qc = materials.get(config.qc_id)
    if qc and str(identifier1 or "").strip().casefold() in {
        str(name).strip().casefold() for name in [qc["name"], *qc.get("aliases", [])]
        if str(name).strip()
    }:
        return "qc", config.qc_id
    role = record["source_role"]
    if role not in ("unknown", "qc", "carbonate_standard"):
        return role, None
    # A Reference cell can name the processing reference for an unknown, not its identity.
    # Only the sample label is used for material matching.
    identity = record["label"].strip().casefold()
    matches = [key for key, material in materials.items()
               if identity in {s.strip().casefold() for s in [material["name"], *material["aliases"]]}]
    if len(matches) > 1:
        return "ambiguous_material", None
    material_id = matches[0] if matches else None
    if material_id == config.qc_id:
        return "qc", material_id
    if role == "qc":
        return "unassigned_qc", material_id
    if material_id in config.anchor_ids:
        return "anchor", material_id
    if role == "carbonate_standard":
        return "unassigned_standard", material_id
    return "unknown", None


def pair_fit(rows: list[dict], x: str, y: str, excluded_ids=None) -> dict:
    valid = [r for r in rows if r.get(x) is not None and r.get(y) is not None
             and math.isfinite(r[x]) and math.isfinite(r[y])]
    excluded_ids = excluded_ids or set()
    retained = [r for r in valid if r.get("id") not in excluded_ids]
    fit = regression([r[x] for r in retained], [r[y] for r in retained])
    fit["points"] = [{"x": r[x], "y": r[y], "id": r.get("id"),
                      "excluded_from_fit": r.get("id") in excluded_ids} for r in valid]
    return fit


def partial_pair_fit(rows, x, y, control, excluded_ids):
    valid = [r for r in rows if all(isinstance(r.get(k), (int, float)) and math.isfinite(r[k]) for k in (x, y, control))]
    retained = [r for r in valid if r.get("id") not in excluded_ids]
    fit = partial_regression([r[x] for r in retained], [r[y] for r in retained], [r[control] for r in retained])
    if fit["status"] == "estimated":
        mx, my = fit["detrending"]["x"], fit["detrending"]["y"]
        fit["points"] = [{"id": r.get("id"), "x": r[x]-mx["intercept"]-mx["slope"]*r[control],
                          "y": r[y]-my["intercept"]-my["slope"]*r[control], "control": r[control],
                          "excluded_from_fit": r.get("id") in excluded_ids} for r in valid]
    return fit


def diagnostics(rows: list[dict], excluded_outlier_ids=None, *, include_all_data=False) -> dict:
    excluded_outlier_ids = excluded_outlier_ids or {}
    from ..domain.shared.dataframe import _ensure_cycle1_pressure_weighted_mismatch_column
    from ..domain.constants import CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL
    import pandas as pd
    frame = pd.DataFrame({"1  Cycle Int  Samp  44": [r.get("i44_v") for r in rows],
                          "1  Cycle Int  Ref  44": [r.get("reference_i44_v") for r in rows],
                          "1  Cycle Int  Diff Samp-Ref  44": [r.get("sample_reference_difference_v") for r in rows]})
    _ensure_cycle1_pressure_weighted_mismatch_column(frame)
    rows = [{**row, "pressure_weighted_mismatch": float(value) if pd.notna(value) else None}
            for row, value in zip(rows, frame[CYCLE1_SIGNAL_PRESSURE_WEIGHTED_MISMATCH44_COL])]
    groups = defaultdict(list)
    by_sequence = {r["sequence"]: r for r in rows}
    for r in rows:
        if r.get("excluded") or r["role"] not in ("anchor", "qc"):
            continue
        groups[r.get("material_id") or r["label"]].append(r)
    if include_all_data:
        groups["__all__"] = [r for r in rows if not r.get("excluded") and r.get("role") in ("unknown", "qc", "anchor")]
    result = {"materials": [], "automatic_corrections": False,
              "note": "Fits use one material at a time. Associations are evidence for review, not proof of causation or a correction."}
    for material_id, points in groups.items():
        if not points:
            continue
        item = {"material_id": material_id, "label": "All available data" if material_id == "__all__" else points[0]["label"], "n": len(points),
                "mass_to_co2_pressure": pair_fit(points, "mass_ug", "co2_pressure_ubar"),
                "co2_pressure_to_i44": pair_fit(points, "co2_pressure_ubar", "i44_v"),
                "mass_to_i44": pair_fit(points, "mass_ug", "i44_v"), "isotopes": {}}
        paired = [r for r in points if r.get("i44_v") is not None and r.get("pressure_mismatch_v") is not None]
        correlation = None
        if len(paired) >= 3:
            xx = [r["i44_v"] for r in paired]
            yy = [r["pressure_mismatch_v"] for r in paired]
            if np.ptp(xx) > 1e-12 and np.ptp(yy) > 1e-12:
                correlation = float(np.corrcoef(xx, yy)[0, 1])
        item["intensity_pressure_correlation"] = correlation
        item["collinearity_warning"] = correlation is not None and abs(correlation) >= .9
        for iso in ISOTOPES:
            excluded_ids = set(excluded_outlier_ids.get(iso, ()))
            retained = [p for p in points if p.get("id") not in excluded_ids]
            intensity = pair_fit(points, "i44_v", iso, excluded_ids)
            qc = [p for p in points if p["role"] == "qc"]
            # Use the QC group's fixed mean, not each QC value, to avoid mathematical coupling.
            mean = summary([p[iso] for p in qc if p[iso] is not None and p.get("id") not in excluded_ids])["mean"]
            memory_rows = []
            for point in qc:
                previous = by_sequence.get(point["sequence"] - 1)
                if mean is not None and previous and not previous.get("excluded") and previous.get("id") not in excluded_ids and previous.get(iso) is not None and point.get(iso) is not None:
                    memory_rows.append({"id": point["id"], "contrast": previous[iso] - mean, "response": point[iso]})
            memory_n = sum(p.get("id") not in excluded_ids for p in memory_rows)
            memory = pair_fit(memory_rows, "contrast", "response", excluded_ids) if memory_n >= 6 else {"status": "insufficient_evidence", "n": memory_n}
            memory["interpretation"] = "Screening against the immediately preceding analysis. A designed contrasting sequence and independent confirmation are required before attributing memory."
            mass_groups = defaultdict(list)
            for point in retained:
                if point.get("mass_ug") is not None and point.get(iso) is not None:
                    mass_groups[point["mass_ug"]].append(point[iso])
            strata = [{"mass_ug": mass, **summary(values)} for mass, values in sorted(mass_groups.items())]
            pooled_df = sum(s["n"] - 1 for s in strata if s["sd"] is not None)
            pooled_sd = math.sqrt(sum((s["n"] - 1) * s["sd"] ** 2 for s in strata if s["sd"] is not None) / pooled_df) if pooled_df else None
            item["isotopes"][iso] = {
                "repeatability": summary([p[iso] for p in retained if p[iso] is not None]),
                "repeatability_by_mass": strata, "within_mass_pooled_sd": pooled_sd,
                "repeatability_note": "Overall dispersion can include deliberate mass effects. Use within-mass replicate SD to assess repeatability at a fixed mass.",
                "mass_dependence": pair_fit(points, "mass_ug", iso, excluded_ids), "intensity_dependence": intensity,
                "pressure_residual": partial_pair_fit(points, "pressure_mismatch_v", iso, "i44_v", excluded_ids),
                "pressure_dependence": pair_fit(points, "pressure_mismatch_v", iso, excluded_ids),
                "sample_reference_dependence": pair_fit(points, "sample_reference_difference_v", iso, excluded_ids),
                "pressure_adjusted_dependence": pair_fit(points, "pressure_weighted_mismatch", iso, excluded_ids),
                "drift": pair_fit(qc, "sequence", iso, excluded_ids), "memory": memory,
            }
        result["materials"].append(item)
    return result


def evaluate(run: dict, method: dict, materials: dict, measurements: list[dict], exclusions: list[dict], *, demo=False) -> dict:
    config = MethodConfig.model_validate(method["config"])
    blockers, warnings, results = [], [], []
    external = run.get("input_basis") == "already_vpdb"
    preapplied = run.get("preapplied_corrections", [])
    if (external or preapplied) and not run.get("processing_evidence"):
        blockers.append("Document the Qtegra processing already applied and its model provenance")
    if external and (method["status"] == "draft" or run.get("external_method_id") != method["id"]):
        blockers.append("Externally referenced data requires the same frozen method model and covariance; raw pre-normalization data is required to establish a new method")
    if preapplied and (method["status"] == "draft" or run.get("external_method_id") != method["id"]):
        blockers.append("Pre-applied corrections require the same frozen method and documented matching coefficients")
    excluded = {r["measurement_id"] for r in exclusions}
    for row in sorted(measurements, key=lambda r: r["sequence"]):
        role, material_id = identify(row, config, materials)
        result = {k: v for k, v in row.items() if k != "raw_rows"}
        result.update(role=role, material_id=material_id, excluded=row["id"] in excluded,
                      mass_ug=run.get("masses_ug", {}).get(row["id"], row["mass_ug"]), issues=[], isotopes={})
        results.append(result)
        if role in ("unsupported_drift", "unrecognized", "ambiguous_material", "unassigned_qc", "unassigned_standard"):
            blockers.append(f"Analysis {row['source_index']}: resolve {role.replace('_', ' ')}")
        if result["excluded"] or role not in ("anchor", "qc", "unknown"):
            continue
        issues = result["issues"]
        if not row["label"].strip():
            issues.append("Missing sample identifier")
        if row.get("pressure_failed"):
            issues.append("Qtegra pressure adjustment failed")
        status = str(row.get("status") or "").lower()
        if any(word in status for word in ("fail", "abort", "error", "cancel")):
            issues.append("Qtegra reports an acquisition failure")
        if status == "running" and not run.get("acquisition_complete"):
            issues.append("Acquisition completion needs confirmation")
        for key in ("i44_v", "pressure_mismatch_v"):
            limits = config.ranges.get(key)
            value = result.get(key)
            if key != "pressure_mismatch_v" and limits is None:
                issues.append(f"Validated {key} range is unset")
            elif limits is not None and (value is None or not limits.low <= value <= limits.high):
                issues.append(f"{key} missing or outside validated range")
        for iso in ISOTOPES:
            sd = row.get(iso + "_sd")
            if row.get(iso) is None:
                issues.append(f"Missing {iso} elemental mean")
            if sd is None or sd < 0 or sd >= config.qc.internal_sd[iso]:
                issues.append(f"{iso} internal SD missing or at/above limit")
            correction = config.corrections[iso]
            if correction:
                predictor = result.get(correction.predictor)
                if predictor is None or not correction.domain.low <= predictor <= correction.domain.high:
                    issues.append(f"{iso} correction predictor missing or outside validated domain")

    models = method.get("normalization", {}) if method["status"] != "draft" else {}
    if method["status"] == "draft" and run["context"] != "qualification":
        blockers.append("Routine processing requires an approved method version")
    if run["context"] == "qualification" and method["status"] == "draft" and not external and not preapplied:
        for iso in ISOTOPES:
            try:
                if len(config.anchor_ids) != 2:
                    raise ValueError("Select two independent anchor lots")
                measured, assigned, u_measured, u_assigned, evidence, predictor_means = [], [], [], [], [], []
                correction = config.corrections[iso]
                for material_id in config.anchor_ids:
                    material = Material.model_validate({k: v for k, v in materials[material_id].items() if k not in ("id", "created_at")})
                    if not material.verified:
                        raise ValueError(f"{material.name}: certificate and lot not verified")
                    points = [r for r in results if r["role"] == "anchor" and r["material_id"] == material_id and not r["excluded"] and not r["issues"] and r[iso] is not None]
                    stats = summary([r[iso] - correction.slope * (r[correction.predictor] - correction.center) if correction else r[iso] for r in points])
                    if stats["n"] < 2:
                        raise ValueError(f"{material.name}: at least two accepted aliquots required")
                    measured.append(stats["mean"])
                    assigned.append(material.assigned[iso].value)
                    u_measured.append(stats["se_mean"])
                    u_assigned.append(material.assigned[iso].standard_uncertainty())
                    evidence.append({"material_id": material_id, "measurement_ids": [r["id"] for r in points], **stats})
                    if correction:
                        predictor_means.append(summary([r[correction.predictor] for r in points])["mean"])
                models[iso] = anchor_model(assigned, measured, [*u_assigned, *u_measured], config.normalization_covariance[iso])
                models[iso]["anchors"] = evidence
                if correction:
                    models[iso]["correction"] = {**correction.model_dump(), "anchor_predictor_means": predictor_means}
            except (ValueError, KeyError) as exc:
                blockers.append(f"{iso} normalization: {exc}")
    for iso in ISOTOPES:
        if iso not in models:
            blockers.append(f"No approved {iso} normalization available")
        if config.precision[iso] is None:
            blockers.append(f"{iso} intermediate precision is unset")
        if config.qc.bias[iso] is None:
            blockers.append(f"{iso} independent QC bias limit is unset")
    qc_material = materials.get(config.qc_id)
    if run.get("calibration_verification")=="simulation_assumption":
        blockers.append("Historical calibration transfer is unverified. Simulated qualification supports workflow review only.")
    if run.get("synthetic") and not demo:
        blockers.append("Synthetic data cannot approve laboratory qualification or client release")
    if not qc_material or not qc_material.get("verified"):
        blockers.append("Independent QC lot and assigned-value metadata are not verified")
    for result in results:
        if result["excluded"] or result["role"] not in ("anchor", "qc", "unknown"):
            continue
        for iso, model in models.items():
            if result[iso] is None:
                continue
            correction = model.get("correction")
            try:
                norm = process_value(result, iso, model, run)
            except ValueError as exc:
                result["issues"].append(f"{iso}: {exc}")
                continue
            result["isotopes"][iso] = norm
            if norm["extrapolated"] and result["role"] != "anchor":
                result["issues"].append(f"{iso} extrapolates beyond anchor means")
            bounds = config.ranges.get(iso)
            if bounds is None or not bounds.low <= norm["value"] <= bounds.high:
                result["issues"].append(f"{iso} outside validated isotope range or range unset")
            factor = norm.get("processing", {}).get("carbonate", {}).get("factor", 1.)
            precision = config.precision.get(iso)
            precision = precision * factor if precision is not None else None
            if precision is not None:
                try:
                    components = [
                        {"name": "precision", "u": precision, "covers": ["routine_precision", "qtegra_linearity", "reaction", "transfer", "instrument_stability"], "rationale": config.precision_evidence},
                        {"name": "normalization", "u": norm["u_norm"], "covers": ["assigned_values", "anchor_means"], "rationale": "Propagated from both assigned values and measured anchor means"},
                        *[{**item.model_dump(), "u": item.u * factor} for item in config.additional_components[iso]],
                    ]
                    if correction:
                        components.append({"name": "secondary_correction", "u": norm["correction"]["u"],
                                           "covers": ["secondary_correction_coefficient"],
                                           "rationale": correction["independence_rationale"]})
                    norm["budget"] = budget(components, config.coverage_factor)
                    norm["u_prec"] = precision
                    norm["u_corr"] = norm.get("correction", {}).get("u", 0.)
                except ValueError as exc:
                    blockers.append(f"{iso} uncertainty budget: {exc}")
        for issue in result["issues"]:
            blockers.append(f"Analysis {result['source_index']}: {issue}")

    qc_rows = [r for r in results if r["role"] == "qc" and not r["excluded"]]
    qc = {"n": len(qc_rows), "passed": True, "isotopes": {}, "cadence": {"maximum_unknowns": config.qc.max_unknowns_between_qc}}
    if len(qc_rows) < config.qc.minimum_qc:
        blockers.append(f"At least {config.qc.minimum_qc} independent QC aliquots are required")
        qc["passed"] = False
    streak, maximum = 0, 0
    for r in results:
        if r["role"] == "qc" and not r["excluded"]:
            streak = 0
        elif r["role"] == "unknown":
            streak += 1
            maximum = max(maximum, streak)
    qc["cadence"]["observed_maximum"] = maximum
    if maximum > config.qc.max_unknowns_between_qc:
        qc["passed"] = False
        blockers.append("QC cadence exceeded, including sequence edges")
    for iso in ISOTOPES:
        values = [r["isotopes"][iso]["value"] for r in qc_rows if iso in r["isotopes"]]
        stats = summary(values)
        target = qc_material["assigned"][iso]["value"] if qc_material else None
        bias = stats["mean"] - target if stats["mean"] is not None and target is not None else None
        sd_pass = stats["sd"] is not None and stats["sd"] < config.qc.external_sd[iso]
        bias_limit = config.qc.bias[iso]
        bias_pass = bias is not None and bias_limit is not None and abs(bias) < bias_limit
        internal_pass = all(not r["issues"] for r in qc_rows)
        passed = len(values) >= config.qc.minimum_qc and sd_pass and bias_pass and internal_pass
        qc["isotopes"][iso] = {**stats, "target": target, "bias": bias, "sd_limit": config.qc.external_sd[iso],
                                "bias_limit": bias_limit, "passed": passed,
                                "imported":summary([r[iso] for r in qc_rows if r.get(iso) is not None])}
        if not passed:
            qc["passed"] = False
            blockers.append(f"{iso} independent QC did not pass precision, bias and individual-analysis criteria")
    if not config.independence_rationale or not config.precision_evidence or not config.coverage_rationale:
        blockers.append("Document precision evidence, uncertainty independence and coverage-factor rationale")
    for material in materials.values():
        if material.get("valid_until") and material["valid_until"] < str(run.get("acquired_date") or run["created_at"][:10]):
            blockers.append(f"{material['name']} certificate was expired at acquisition")
    if any(r["role"] == "gas_reference_calibration" for r in results):
        warnings.append("Ref Gas Calibration is retained as reference-gas evidence and excluded from carbonate anchoring")
    warnings.append("Drift and memory are diagnostic hypotheses; no automatic correction is applied")
    if demo:
        warnings.append("DEMONSTRATION ONLY: simulated data and scientific approvals; not laboratory results" if run.get("synthetic") else
                        "Real raw observations evaluated against simulated qualification; historical calibration and uncertainty remain unverified.")
    after = [{**r, **{iso: r["isotopes"].get(iso, {}).get("value") for iso in ISOTOPES}} for r in results]
    for result in results:
        material = materials.get(result.get("material_id"))
        for iso, value in result["isotopes"].items():
            target = material["assigned"][iso]["value"] if material else None
            value["assigned_value"] = target
            value["residual_to_assigned"] = value["value"] - target if target is not None else None
    comparable = comparison_rows(results, models, run)
    screened_before = screen_effects(diagnostics(comparable), config.correction_validation.practical_effect)
    screened_after = screen_effects(diagnostics(after), config.correction_validation.practical_effect)
    return {"method_snapshot": method, "material_snapshots": materials, "method_revision": method["revision"],
            "run_revision": run["revision"], "normalization": models, "results": results, "qc": qc,
            "diagnostics": diagnostics(results), "diagnostics_comparable": screened_before, "diagnostics_after": screened_after,
            "correction_review": correction_review(results,models,config,run,qc), "blockers": list(dict.fromkeys(blockers)), "warnings": warnings,
            "ready": not blockers, "corrections": [{"isotope": iso, **c.model_dump()} for iso, c in config.corrections.items() if c], "qtegra_drift_correction": False,
            "internal_sd_basis": "Qtegra exported elemental SD, before application normalization",
            "simulation": demo, "input_basis": run.get("input_basis", "instrument_delta"),
            "normalization_uncertainty_basis": "A1,A2,M1,M2 with full input covariance. No residual regression uncertainty from two means."}


def process_value(result, isotope, model, run, *, monte_carlo=False, allow_extrapolation=False):
    """Evaluate the documented processing chain once, including imported stages."""
    value = result[isotope]
    imported_value = value
    mineral = run.get("carbonate_material", "calcite") if result.get("role") == "unknown" else "calcite"
    carbonate_applied = isotope == "d18o" and mineral != "calcite"
    retained_carbonate = carbonate_applied and run.get("carbonate_correction_preapplied", False)
    if retained_carbonate:
        if run.get("input_basis") != "already_vpdb":
            raise ValueError("Pre-applied carbonate correction requires already normalized VPDB input")
        value = convert_d18o_carbonate_material(value, source_material=mineral, target_material="calcite")
    external = run.get("input_basis") == "already_vpdb"
    correction = model.get("correction")
    supplied_x = (value - model["intercept"]) / model["slope"] if external else value
    raw_x = supplied_x
    preapplied = isotope in run.get("preapplied_corrections", [])
    predictor = result.get(correction["predictor"]) if correction else None
    if preapplied and not correction:
        raise ValueError("Pre-applied correction has no matching approved coefficient")
    if preapplied and predictor is not None:
        raw_x += correction["slope"] * (predictor - correction["center"])
    norm = corrected_normalize(raw_x, predictor, model, monte_carlo=monte_carlo, allow_extrapolation=allow_extrapolation)
    if external and (not correction or preapplied):
        norm["value"] = value  # Retain the exported value exactly, including its rounding.
    norm["processing"] = {"input_value": value, "input_basis": run.get("input_basis", "instrument_delta"),
                           "reconstructed_instrument_value": raw_x, "normalization_applied": not external,
                           "normalization_source": ("Externally referenced export retained; transfer to simulated method unverified" if run.get("calibration_verification")=="simulation_assumption" else "Qtegra: documented matching frozen method") if external else "IRMS Metrology",
                           "correction_source": "Qtegra: documented matching coefficient" if preapplied else ("IRMS Metrology" if correction else "None"),
                           "correction_applied": bool(correction and not preapplied),
                           "evidence": run.get("processing_evidence", "")}
    if isotope == "d18o" and result.get("role") == "unknown":
        factor = CARBONATE_ACID_FRACTIONATION_FACTORS["calcite"] / CARBONATE_ACID_FRACTIONATION_FACTORS[mineral]
        before_carbonate = norm["value"]
        norm["value"] = convert_d18o_carbonate_material(before_carbonate, source_material="calcite", target_material=mineral)
        if retained_carbonate and external and (not correction or preapplied):
            norm["value"] = imported_value
        norm["u_norm"] *= factor
        norm["jacobian"] = [v * factor for v in norm["jacobian"]]
        if "correction" in norm:
            norm["correction"]["u"] *= factor
            norm["correction"]["coefficient_sensitivity"] *= factor
            norm["u_normalization_and_correction"] *= factor
        if "monte_carlo" in norm:
            mc = norm["monte_carlo"]
            mc["mean"] = convert_d18o_carbonate_material(mc["mean"], source_material="calcite", target_material=mineral)
            mc["u"] *= factor
            mc["interval95"] = [convert_d18o_carbonate_material(v, source_material="calcite", target_material=mineral) for v in mc["interval95"]]
        norm["processing"].update(input_value=imported_value, carbonate={
            "material": mineral, "source_material": mineral if retained_carbonate else "calcite",
            "alpha_calcite": CARBONATE_ACID_FRACTIONATION_FACTORS["calcite"],
            "alpha_material": CARBONATE_ACID_FRACTIONATION_FACTORS[mineral],
            "factor": factor, "applied": carbonate_applied and not retained_carbonate,
            "preapplied": retained_carbonate, "before": before_carbonate, "after": norm["value"],
            "formula": "delta_material = (delta_calcite + 1000) * alpha_calcite / alpha_material - 1000",
            "factor_basis": "Fixed factors from the original IRMS carbonate conversion",})
    return norm
