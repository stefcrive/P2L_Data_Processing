"""Session consultation derived from immutable evaluations, never a second calibration."""
from __future__ import annotations

from .qc_screening import detect_qc_outliers, qc_review_flags
from .correction_review import comparison_rows, correction_review, screen_effects
from .models import ISOTOPES, MethodConfig
from .pipeline import diagnostics
from .science import summary
from .importer import measurement_identity
from .residual_preview import residual_previews


def session_analysis(detail, *, outlier_method="sigma", threshold=3.0, outliers=None):
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
                   "evaluation_id": evaluation["id"] if evaluation else None} for r in source]
        rows.extend(source)
        if evaluation:
            before.extend(comparison_rows(source, evaluation["normalization"], run))
            after.extend({**r, **{iso: r.get("isotopes", {}).get(iso, {}).get("value") for iso in ISOTOPES}} for r in source)
        # No artificial memory pair between the end of one workbook and the next.
        offset += max((r["workbook_sequence"] for r in source), default=0) + 1
    paired_ids = {r["id"] for r in after if all(r.get(iso) is not None for iso in ISOTOPES)}
    outliers = outliers if outliers is not None else detect_qc_outliers(after, outlier_method, threshold)
    excluded_outlier_ids = {
        iso: {flag["measurement_id"] for flag in outliers["flags"] if flag["isotope"] == iso}
        for iso in ISOTOPES
    }
    filtered_qc = {
        iso: [r for r in after if r.get("role") == "qc" and not r.get("excluded")
              and r["id"] not in excluded_outlier_ids[iso] and r.get(iso) is not None]
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
                      and all(not r.get("issues") for r in filtered_qc[iso])
        }
    # Reuse the paired verification algorithm, with the actual per-workbook baselines.
    models = next((r["evaluation"]["normalization"] for r in detail["runs"] if r.get("evaluation")), {})
    by_id = {r["id"]: r for r in rows}
    comparison = [{**r, "isotopes": by_id[r["id"]].get("isotopes", {})} for r in before]
    review = correction_review(comparison, models, config,
        {"input_basis": "already_vpdb", "preapplied_corrections": detail.get("preapplied_corrections", []),
         "calibration_verification": detail.get("calibration_verification")}, qc,
        excluded_outlier_ids=excluded_outlier_ids)
    practical = config.correction_validation.practical_effect
    final_by_id = {r["id"]: r for r in after}
    paired_before = [{**r, **{iso: r.get(iso) if final_by_id.get(r["id"], {}).get(iso) is not None else None for iso in ISOTOPES}} for r in before]
    final_diagnostics = screen_effects(diagnostics(after), practical)
    return {"rows": rows, "diagnostics_before": screen_effects(diagnostics(paired_before), practical),
            "diagnostics_after": final_diagnostics, "correction_review": review,
            "residual_previews": residual_previews(final_diagnostics, detail.get("residual_overrides", {})),
            "outliers": outliers, "qc_review_flags": qc_review_flags(rows),
            "workbooks": len(detail["runs"]), "paired_results": len(paired_ids),
            "qc_statistics": {iso: {"imported": summary([r[iso] for r in rows if r.get("role") == "qc" and not r.get("excluded") and r["id"] not in excluded_outlier_ids[iso] and r.get(iso) is not None]),
                                   "final": summary([r[iso] for r in filtered_qc[iso]])} for iso in ISOTOPES}}
