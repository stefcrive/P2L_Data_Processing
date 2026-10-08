"""Effective session results: one QC-SD-selected residual correction per isotope.

Always start from immutable run evaluations. Session settings and provenance are
exported with these results; recomputation never compounds an earlier correction.
"""
import math

import numpy as np

from .importer import measurement_identity
from .models import ISOTOPES, MethodConfig, ResidualOverride
from .pipeline import diagnostics, process_value
from .qc_screening import detect_qc_outliers, failure_category
from .residual_preview import _fit_preview
from .pressure_correction import fit_pressure_failed_qc
from .science import budget, summary

SIGNAL_EFFECTS = ("intensity_dependence", "sample_reference_dependence", "pressure_adjusted_dependence")


def linearity_rows(rows):
    output = []
    for row in rows:
        sample = row.get("initial_i44_v", row.get("i44_v"))
        reference = row.get("initial_reference_i44_v", row.get("reference_i44_v"))
        output.append({**row, "i44_v": sample, "reference_i44_v": reference,
                       "sample_reference_difference_v": sample-reference if sample is not None and reference is not None else None})
    return output


def complete_budget(norm, config, iso, k):
    if norm.get("budget"):
        norm["budget"]["k"] = k
        norm["budget"]["expanded_uncertainty"] = k*norm["budget"]["u_combined"]
        return
    precision = config.precision.get(iso)
    if precision is None:
        norm["budget_issue"] = "Intermediate precision is missing from the method."
        return
    factor = norm.get("processing", {}).get("carbonate", {}).get("factor", 1.)
    components = [
        {"name": "precision", "u": precision*factor,
         "covers": ["routine_precision", "qtegra_linearity", "reaction", "transfer", "instrument_stability"],
         "rationale": config.precision_evidence},
        {"name": "normalization", "u": norm["u_norm"], "covers": ["assigned_values", "anchor_means"],
         "rationale": "Propagated from both assigned values and measured anchor means"},
        *[{**item.model_dump(), "u": item.u*factor} for item in config.additional_components[iso]],
    ]
    if norm.get("correction"):
        components.append({"name": "secondary_correction", "u": norm["correction"]["u"],
                           "covers": ["secondary_correction_coefficient"], "rationale": "Registered method coefficient"})
    try:
        norm["budget"] = budget(components, k)
        norm.update(u_prec=precision*factor, u_corr=norm.get("correction", {}).get("u", 0.))
    except ValueError as exc:
        norm["budget_issue"] = str(exc)


def apply_residual_correction(row, iso, x, selected, k, *, failed_analysis=False, pressure_correction=False):
    norm = row["isotopes"].get(iso)
    if not norm or not norm.get("budget"):
        return None
    model, ref = selected["model"], selected["model"]["x_ref"]
    intensity = row.get("initial_i44_v", row.get("i44_v"))
    joint = "intensity_slope" in model
    g = (np.array([x-ref, intensity-model["intensity_ref"]]) if joint else
         np.array([x-ref, (x-ref)*(x+ref)][:model["degree"]]))
    adjustment = -model["slope"]*g[0]+selected["settings"]["offset"]
    if joint:
        adjustment -= model["intensity_slope"]*g[1]
    if model["degree"] == 2:
        adjustment -= model["quad"]*g[1]
    u = math.sqrt(max(0., float(g @ np.asarray(model["coefficient_covariance"]) @ g)))
    outside = not selected["fit_range"][0] <= x <= selected["fit_range"][1]
    if joint:
        outside |= not selected["intensity_fit_range"][0] <= intensity <= selected["intensity_fit_range"][1]
    component = {"name": "pressure_adjustment_linearity" if pressure_correction else "residual_linearity", "u": u,
                 "covers": ["session_pressure_coefficient" if pressure_correction else "session_residual_coefficient"],
                 "rationale": "Session QC coefficient covariance: g Cov(beta) gT; combined in quadrature with the base result budget. QC SD is an in-sample selection criterion."}
    old_budget = norm["budget"]
    covariance = np.zeros((len(old_budget["components"])+1,)*2)
    covariance[:-1, :-1] = old_budget["covariance"]
    covariance[-1, -1] = u*u
    norm["budget"] = budget([*old_budget["components"], component], k, covariance.tolist())
    norm["residual_correction"] = {"effect": selected["source_effect"], "before": norm["value"], "adjustment": float(adjustment),
                                   "u": u, "predictor": x, "extrapolated": outside, "failed_analysis": failed_analysis,
                                   "coefficient_model": model, "qc_n": selected["n"]}
    if pressure_correction:
        norm["residual_correction"]["training_population"] = selected.get("training_population", "nonfailed_qc_fallback")
    if joint:
        norm["residual_correction"]["initial_intensity"] = intensity
    norm["value"] += float(adjustment)
    norm["u_residual"] = u
    norm["u_corr"] = math.hypot(norm.get("u_corr", 0.), u)
    if norm.get("assigned_value") is not None:
        norm["residual_to_assigned"] = norm["value"]-norm["assigned_value"]
    return outside


def correct_failed_results(rows, qc, failed, statistical, config, k):
    """Estimate pressure-mismatch bias on eligible QC; apply only to pressure issues."""
    decisions = {}
    for iso in ISOTOPES:
        targets = [row for row in rows if not row.get("excluded")
                   and failure_category(row, iso) == "pressure_adjustment"]
        points = [{**p, "excluded_from_fit": p["id"] in failed | statistical[iso]}
                  for p in (qc or {}).get("isotopes", {}).get(iso, {}).get("pressure_dependence", {}).get("points", [])]
        retained = [p for p in points if not p["excluded_from_fit"]]
        n = len(retained)
        decision = {"status": "insufficient_evidence", "n": n, "applied_n": 0,
                    "source_effect": "pressure_dependence", "scope": "pressure_affected_analyses",
                    "training_population": "nonfailed_qc_fallback",
                    "unknown_applied_n": 0, "failed_applied_n": 0}
        if n >= max(3, config.qc.minimum_qc):
            settings = ResidualOverride(enabled=True, center=0., application_scope="all_data", extrapolate=True)
            decision.update(_fit_preview(points, settings))
            decision.update(settings=settings.model_dump(), scope="pressure_affected_analyses", screening_status="qc_sd_selection", n=n)
            if decision.get("model"):
                decision["fit_range"] = [min(p["x"] for p in retained), max(p["x"] for p in retained)]
                before_sd, after_sd = decision["before"]["sd"], decision["after"]["sd"]
                decision["sd_reduction_fraction"] = 1-after_sd/before_sd if before_sd else 0.
                decision["status"] = "ready" if before_sd and before_sd-after_sd > max(1e-12, before_sd*1e-9) else "not_improved"
        by_id = {r["id"]: r for r in rows}
        affected_points = []
        for point in (qc or {}).get("isotopes", {}).get(iso, {}).get("pressure_dependence", {}).get("points", []):
            row = by_id.get(point["id"], {})
            intensity = row.get("initial_i44_v", row.get("i44_v"))
            if (row.get("role") == "qc" and not row.get("excluded") and row["id"] not in statistical[iso]
                    and failure_category(row, iso) == "pressure_adjustment"
                    and intensity is not None and math.isfinite(intensity) and intensity > 0):
                affected_points.append({**point, "intensity": intensity})
        reference_intensities = [by_id[p["id"]].get("initial_i44_v", by_id[p["id"]].get("i44_v"))
                                 for p in retained if p["id"] in by_id]
        reference_intensities = [x for x in reference_intensities if x is not None and math.isfinite(x) and x > 0]
        if reference_intensities:
            joint_fit = fit_pressure_failed_qc(affected_points, config.qc.minimum_qc, float(np.median(reference_intensities)))
            if joint_fit is not None:
                decision.update(joint_fit)
                decision["settings"] = ResidualOverride(enabled=True, center=0., application_scope="all_data", extrapolate=True).model_dump()
        extrapolated = 0
        for row in targets:
            reason = None
            x = row.get("pressure_mismatch_v")
            if decision["status"] != "ready":
                reason = "No usable QC relationship between pressure-adjustment difference and delta."
            elif x is None or not math.isfinite(x):
                reason = "Pressure-adjustment difference is missing."
            elif "intensity_slope" in decision.get("model", {}) and (
                    row.get("initial_i44_v", row.get("i44_v")) is None or
                    not math.isfinite(row.get("initial_i44_v", row.get("i44_v"))) or
                    row.get("initial_i44_v", row.get("i44_v")) <= 0):
                reason = "Initial sample intensity is missing or nonpositive."
            elif not row.get("isotopes", {}).get(iso, {}).get("budget"):
                reason = "Isotope value or complete uncertainty budget is missing."
            if reason:
                row.setdefault("failed_correction_attempts", {})[iso] = {"status": "not_corrected", "reason": reason}
                continue
            outside = apply_residual_correction(row, iso, x, decision, k, failed_analysis=True, pressure_correction=True)
            decision["applied_n"] += 1
            decision["unknown_applied_n"] += int(row.get("role") == "unknown")
            decision["failed_applied_n"] += 1
            extrapolated += int(outside)
            row.setdefault("failed_correction_attempts", {})[iso] = {"status": "applied", "extrapolated": outside}
        decision.update(extrapolated_n=extrapolated, attempted_n=len(targets))
        if decision["status"] == "ready":
            decision["status"] = "applied" if decision["applied_n"] else "unavailable_results"
        decisions[iso] = decision
    return decisions


def admit_pressure_corrected_qc(rows, failed, statistical, decisions):
    """Admit the corrected pressure-failure group only if it lowers pooled QC SD."""
    for iso, decision in decisions.items():
        eligible = [r for r in rows if r.get("role") == "qc" and not r.get("excluded")
                    and r["id"] not in statistical[iso] and iso in r.get("isotopes", {})]
        existing = [r for r in eligible if r["id"] not in failed]
        candidates = [r for r in eligible if failure_category(r, iso) == "pressure_adjustment"
                      and r["isotopes"][iso].get("residual_correction", {}).get("effect") == "pressure_dependence"]
        before = summary([r["isotopes"][iso]["value"] for r in existing])
        after = summary([r["isotopes"][iso]["value"] for r in [*existing, *candidates]])
        improved = bool(candidates) and before["sd"] is not None and after["sd"] is not None and \
            before["sd"]-after["sd"] > max(1e-12, before["sd"]*1e-9)
        admitted = [r["id"] for r in candidates] if improved else []
        for row in candidates:
            row["isotopes"][iso]["session_qc_admitted"] = improved
        decision["qc_pool"] = {"status": "admitted" if improved else "not_improved" if candidates else "no_eligible_qc",
                               "before": before, "after": after,
                               "candidate_ids": [r["id"] for r in candidates], "admitted_ids": admitted}


def process_session_results(detail):
    """Mutate only the freshly loaded session detail, never its source records."""
    config = MethodConfig.model_validate(detail["method"]["config"])
    k = detail.get("coverage_factor") or config.coverage_factor
    rows, baseline = [], []
    offset = 0
    edits = detail.get("identity_overrides", {})
    for run in detail["runs"]:
        evaluation = run.get("evaluation")
        for record in run["measurements"]:
            record.update(measurement_identity(record, run.get("source_kind", "qtegra_raw")))
            record.update(edits.get(record["id"], {}))
        if not evaluation:
            continue
        for row in evaluation["results"]:
            row.update(measurement_identity(row, run.get("source_kind", "qtegra_raw")))
            row.update(edits.get(row["id"], {}))
            # Keep parsed identities independent of material matching and source labels.
            row.setdefault("initial_i44_v", row.get("i44_v"))
            row.setdefault("initial_reference_i44_v", row.get("reference_i44_v"))
            row.setdefault("isotopes", {})
            row["calculation_issues"] = {}
            if detail.get("correct_failed_analyses"):
                row["failed_correction_attempts"] = {}
            for iso in ISOTOPES:
                norm = row["isotopes"].get(iso)
                if norm is None and not row.get("excluded") and row.get(iso) is not None:
                    model = evaluation["normalization"].get(iso)
                    if model:
                        try:
                            norm = process_value(row, iso, model, run, allow_extrapolation=True)
                            material = evaluation.get("material_snapshots", {}).get(row.get("material_id"), {})
                            target = material.get("assigned", {}).get(iso, {}).get("value")
                            norm.update(assigned_value=target, residual_to_assigned=norm["value"]-target if target is not None else None)
                            row["isotopes"][iso] = norm
                        except (ValueError, KeyError) as exc:
                            row["calculation_issues"][iso] = str(exc)
                    else:
                        row["calculation_issues"][iso] = "No normalization model is available."
                if norm is not None:
                    complete_budget(norm, config, iso, k)
                    norm["session_baseline_value"] = norm["value"]
                    if norm.get("budget_issue"):
                        row["calculation_issues"][iso] = norm["budget_issue"]
                elif iso not in row["calculation_issues"]:
                    row["calculation_issues"][iso] = "Analysis is excluded." if row.get("excluded") else "The imported isotope value is missing."
            entry = {**row, "run_id": run["id"], "evaluation_id": evaluation["id"],
                     "sequence": row["sequence"]+offset,
                     "failure_categories": {iso: failure_category(row, iso) for iso in ISOTOPES}}
            rows.append(entry)
            baseline.append({**entry, **{iso: row["isotopes"].get(iso, {}).get("value") for iso in ISOTOPES}})
        offset += max((r["sequence"] for r in evaluation["results"]), default=0)+1
    screening = detail.get("outlier_screening", {"method": "sigma", "threshold": 3.})
    failed = {r["id"] for r in rows if r.get("excluded") or any(failure_category(r, iso) for iso in ISOTOPES)}
    outliers = detail.get("saved_outliers") or detect_qc_outliers(baseline, **screening)
    statistical = {iso: {f["measurement_id"] for f in outliers["flags"] if f["isotope"] == iso} for iso in ISOTOPES}
    diagnostics_ = diagnostics(linearity_rows(baseline), include_all_data=True)
    materials = {m["material_id"]: m for m in diagnostics_["materials"]}
    qc = materials.get(config.qc_id)
    all_data = materials.get("__all__")
    by_id = {r["id"]: r for r in rows}
    pressure_decisions = correct_failed_results(rows, qc, failed, statistical, config, k) if detail.get("correct_failed_analyses") else {}
    decisions = {iso: {} for iso in ISOTOPES}
    for iso in ISOTOPES:
        for effect in SIGNAL_EFFECTS:
            key = f"{config.qc_id}:{effect}:{iso}"
            saved = detail.get("residual_overrides", {}).get(key)
            settings = ResidualOverride.model_validate(saved or {"enabled": True})
            if not settings.enabled:
                decisions[iso][effect] = {"status": "disabled", "n": 0}
                continue
            excluded = failed | (set() if settings.include_statistical_outliers else statistical[iso])
            points = [{**p, "excluded_from_fit": p["id"] in excluded}
                      for p in (qc or {}).get("isotopes", {}).get(iso, {}).get(effect, {}).get("points", [])]
            n = sum(not p["excluded_from_fit"] for p in points)
            if n < max(3, config.qc.minimum_qc):
                decisions[iso][effect] = {"status": "insufficient_evidence", "n": n}
                continue
            fit = _fit_preview(points, settings)
            fit.update(settings={**settings.model_dump(), "application_scope": "all_data", "extrapolate": True},
                       source_effect=effect, n=n, scope="session_results", screening_status="qc_sd_selection")
            decisions[iso][effect] = fit
            if not fit.get("model"):
                continue
            model = fit["model"]
            # Manual coefficients need explicit uncertainties. Automatic estimates
            # retain the complete fitted coefficient covariance.
            if model["coefficient_covariance"] is None:
                if settings.u_slope is not None and (model["degree"] == 1 or settings.u_quadratic is not None):
                    u = [settings.u_slope, settings.u_quadratic][:model["degree"]]
                    model.update(coefficient_covariance=np.diag(np.square(u)).tolist(),
                                 u_slope=settings.u_slope, u_quad=settings.u_quadratic)
                    fit["uncertainty"]["status"] = "provided_coefficient_component"
                    for point in fit["points"]:
                        g = np.array([point["x"]-model["x_ref"], (point["x"]-model["x_ref"])*(point["x"]+model["x_ref"])][:model["degree"]])
                        point["u_correction"] = math.sqrt(max(0., float(g @ np.asarray(model["coefficient_covariance"]) @ g)))
                else:
                    fit["status"] = "uncertainty_required"
                    continue
            before_sd, after_sd = fit["before"]["sd"], fit["after"]["sd"]
            fit["sd_reduction_fraction"] = 1-after_sd/before_sd if before_sd else 0.
            fit["status"] = "not_selected" if before_sd and before_sd-after_sd > max(1e-12, before_sd*1e-9) else "not_improved"
            retained = [p for p in points if not p["excluded_from_fit"]]
            fit["fit_range"] = [min(p["x"] for p in retained), max(p["x"] for p in retained)]
        candidates = [(effect, fit) for effect, fit in decisions[iso].items() if fit["status"] == "not_selected"]
        if not candidates or not all_data:
            continue
        effect, selected = max(candidates, key=lambda item: (item[1]["sd_reduction_fraction"], item[1]["n"]))
        selected["status"] = "applied"
        applied, extrapolated = 0, 0
        for point in all_data["isotopes"][iso][effect].get("points", []):
            row = by_id[point["id"]]
            # Pressure issues stay ineligible even if their pressure correction is unavailable.
            if row["id"] in failed:
                continue
            if row["isotopes"].get(iso, {}).get("residual_correction", {}).get("effect") == "pressure_dependence":
                continue
            outside = apply_residual_correction(row, iso, point["x"], selected, k)
            if outside is None:
                continue
            applied += 1
            extrapolated += int(outside)
        selected.update(applied_n=applied, extrapolated_n=extrapolated)
        if not applied:
            selected["status"] = "unavailable_results"
    detail["residual_corrections"] = decisions
    admit_pressure_corrected_qc(rows, failed, statistical, pressure_decisions)
    detail["failed_analysis_corrections"] = pressure_decisions
    detail["residual_processing_version"] = 9
    detail["residual_outliers"] = outliers
    for run in detail["runs"]:
        evaluation = run.get("evaluation")
        if evaluation:
            evaluation["session_residual_corrections"] = decisions
            evaluation["session_qc"] = {iso: summary([r["isotopes"][iso]["value"] for r in evaluation["results"]
                if r["role"] == "qc" and r["id"] not in statistical[iso] and iso in r.get("isotopes", {})
                and (r["id"] not in failed or r["isotopes"][iso].get("session_qc_admitted"))]) for iso in ISOTOPES}
    return detail
