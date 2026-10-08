"""Session consultation derived from immutable evaluations, never a second calibration."""
from __future__ import annotations

from .qc_screening import detect_qc_outliers, qc_review_flags, failure_category
from .correction_review import comparison_rows, correction_review, screen_effects
from .models import ISOTOPES, MethodConfig
from .pipeline import diagnostics
from .science import summary
from .importer import measurement_identity


def session_analysis(detail, *, outlier_method="sigma", threshold=3.0, outliers=None, range_exclusions=None, include_all_data=False):
    if outlier_method not in ("sigma", "iqr") or not .5 <= threshold <= 10:
        raise ValueError("Choose sigma or IQR screening and a threshold between 0.5 and 10")
    config = MethodConfig.model_validate(detail["method"]["config"])
    rows, before, after = [], [], []
    offset = 0
    for run in detail["runs"]:
        evaluation = run.get("evaluation")
        source = evaluation["results"] if evaluation else run["measurements"]
        source = [{**r, **measurement_identity(r, run.get("source_kind", "qtegra_raw")), "run_id": run["id"], "run_label": run["label"],
                   "workbook_sequence": r["sequence"], "sequence": offset + r["sequence"],
                   "sample_group": detail["groups"].get(r["id"], "Main batch"),
                   "evaluation_id": evaluation["id"] if evaluation else None,
                   "failure_categories": {iso: failure_category(r, iso) for iso in ISOTOPES}} for r in source]
        rows.extend(source)
        if evaluation:
            before.extend({**r, **{iso: r.get("isotopes", {}).get(iso, {}).get("session_baseline_value", r.get("isotopes", {}).get(iso, {}).get("value")) for iso in ISOTOPES}} for r in source)
            after.extend({**r, **{iso: r.get("isotopes", {}).get(iso, {}).get("value") for iso in ISOTOPES}} for r in source)
        # No artificial memory pair between the end of one workbook and the next.
        offset += max((r["workbook_sequence"] for r in source), default=0) + 1
    paired_ids = {r["id"] for r in after if all(r.get(iso) is not None for iso in ISOTOPES)}
    outliers = outliers if outliers is not None else detect_qc_outliers(after, outlier_method, threshold,
        detail.get("outlier_screening", {}).get("pressure_adjustment_as_outlier", False))
    review_flags = qc_review_flags(rows, config)
    classified = {(f["measurement_id"], f["isotope"], f.get("category", "statistical")) for f in outliers["flags"]}
    review_flags = [f for f in review_flags if (f["measurement_id"], f["isotope"], f["category"]) not in classified]
    failed_ids = {r["id"] for r in rows if r.get("excluded") or any(failure_category(r, iso) for iso in ISOTOPES)}
    excluded_outlier_ids = {
        iso: {flag["measurement_id"] for flag in [*outliers["flags"], *(f for f in review_flags if f["category"] in ("manual", "pressure_adjustment", "no_signal"))] if flag["isotope"] == iso}
             | failed_ids | set((range_exclusions or {}).get(iso, ()))
        for iso in ISOTOPES
    }
    admitted_ids = {iso: {r["id"] for r in rows if r.get("role") == "qc" and
                         r.get("isotopes", {}).get(iso, {}).get("session_qc_admitted")} for iso in ISOTOPES}
    final_excluded_ids = {
        iso: {flag["measurement_id"] for flag in outliers["flags"] if flag["isotope"] == iso}
             | {r["id"] for r in rows if r.get("excluded")}
             | (failed_ids - admitted_ids[iso]) | set((range_exclusions or {}).get(iso, ()))
        for iso in ISOTOPES
    }
    filtered_qc = {
        iso: [r for r in after if r.get("role") == "qc" and not r.get("excluded")
              and r["id"] not in final_excluded_ids[iso] and r.get(iso) is not None]
        for iso in ISOTOPES
    }
    qc = {"isotopes": {}}
    for iso in ISOTOPES:
        stats = summary([r[iso] for r in filtered_qc[iso]])
        target = next((run["evaluation"]["qc"]["isotopes"][iso].get("target") for run in detail["runs"]
                       if run.get("evaluation") and run["evaluation"]["qc"]["isotopes"][iso].get("target") is not None), None)
        bias = stats["mean"] - target if stats["mean"] is not None and target is not None else None
        bias_limit = config.qc.bias[iso]
        qc["isotopes"][iso] = {
            "passed": stats["n"] >= config.qc.minimum_qc
                      and stats["sd"] is not None and stats["sd"] < config.qc.external_sd[iso]
                      and bias is not None and bias_limit is not None and abs(bias) < bias_limit
                      and all(not [issue for issue in r.get("issues", [])
                                   if not (r["id"] in admitted_ids[iso] and issue == "Qtegra pressure adjustment failed")]
                              for r in filtered_qc[iso])
        }
    # Reuse the paired verification algorithm, with the actual per-workbook baselines.
    models = next((r["evaluation"]["normalization"] for r in detail["runs"] if r.get("evaluation")), {})
    by_id = {r["id"]: r for r in rows}
    comparison = [{**r, "isotopes": by_id[r["id"]].get("isotopes", {})} for r in before]
    review = correction_review(comparison, models, config,
        {"input_basis": "already_vpdb", "preapplied_corrections": detail.get("preapplied_corrections", []),
         "calibration_verification": detail.get("calibration_verification")}, qc,
        excluded_outlier_ids=final_excluded_ids)
    practical = config.correction_validation.practical_effect
    final_by_id = {r["id"]: r for r in after}
    paired_before = [{**r, **{iso: r.get(iso) if final_by_id.get(r["id"], {}).get(iso) is not None else None for iso in ISOTOPES}} for r in before]
    def linearity_rows(population):
        prepared = []
        for row in population:
            sample = row.get("initial_i44_v", row.get("i44_v"))
            reference = row.get("initial_reference_i44_v", row.get("reference_i44_v"))
            prepared.append({**row, "i44_v": sample, "reference_i44_v": reference,
                             "sample_reference_difference_v": sample-reference if sample is not None and reference is not None else None})
        return prepared
    final_diagnostics = screen_effects(diagnostics(linearity_rows(after), final_excluded_ids, include_all_data=include_all_data), practical)
    return {"rows": rows, "diagnostics_before": screen_effects(diagnostics(linearity_rows(before if include_all_data else paired_before), excluded_outlier_ids, include_all_data=include_all_data), practical),
            "diagnostics_after": final_diagnostics, "correction_review": review,
            "residual_corrections": detail.get("residual_corrections", {}),
            "failed_analysis_corrections": detail.get("failed_analysis_corrections", {}),
            "residual_qc_id": config.qc_id,
            "outliers": outliers, "qc_review_flags": review_flags,
            "workbooks": len(detail["runs"]), "paired_results": len(paired_ids),
            "qc_statistics": {iso: {"imported": summary([r[iso] for r in rows if r.get("role") == "qc" and not r.get("excluded") and r["id"] not in excluded_outlier_ids[iso] and r.get(iso) is not None]),
                                   "final": summary([r[iso] for r in filtered_qc[iso]])} for iso in ISOTOPES}}
