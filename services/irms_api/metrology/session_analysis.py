"""Session consultation derived from immutable evaluations, never a second calibration."""
from __future__ import annotations

import pandas as pd

from ..domain.calibration.core import identify_outliers, identify_outliers_iqr
from .correction_review import comparison_rows, correction_review, screen_effects
from .models import ISOTOPES, MethodConfig
from .pipeline import diagnostics
from .science import summary


def session_analysis(detail, *, outlier_method="sigma", threshold=3.0):
    if outlier_method not in ("sigma", "iqr") or not .5 <= threshold <= 10:
        raise ValueError("Choose sigma or IQR screening and a threshold between 0.5 and 10")
    config = MethodConfig.model_validate(detail["method"]["config"])
    rows, before, after = [], [], []
    offset = 0
    for run in detail["runs"]:
        evaluation = run.get("evaluation")
        source = evaluation["results"] if evaluation else run["measurements"]
        source = [{**r, "run_id": run["id"], "run_label": run["label"],
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
    qc = {"isotopes": {iso: {"passed": all(r.get("evaluation") and r["evaluation"]["qc"]["isotopes"][iso]["passed"] for r in detail["runs"])} for iso in ISOTOPES}}
    # Reuse the paired verification algorithm, with the actual per-workbook baselines.
    models = next((r["evaluation"]["normalization"] for r in detail["runs"] if r.get("evaluation")), {})
    by_id = {r["id"]: r for r in rows}
    comparison = [{**r, "isotopes": by_id[r["id"]].get("isotopes", {})} for r in before]
    review = correction_review(comparison, models, config,
        {"input_basis": "already_vpdb", "preapplied_corrections": detail.get("preapplied_corrections", []),
         "calibration_verification": detail.get("calibration_verification")}, qc)
    flags = []
    for iso in ISOTOPES:
        # QC only, on the final scale. Missing final values are not silently replaced.
        selected = [r for r in after if r.get("role") == "qc" and not r.get("excluded") and r.get(iso) is not None]
        for material in {r.get("material_id") for r in selected}:
            subset = [r for r in selected if r.get("material_id") == material]
            frame = pd.DataFrame({"value": [r[iso] for r in subset]})
            mask = identify_outliers(frame, "value", threshold) if outlier_method == "sigma" else identify_outliers_iqr(frame, "value", threshold)
            flags.extend({"measurement_id": r["id"], "run_id": r["run_id"], "isotope": iso,
                          "value": r[iso], "material_id": material} for r, flagged in zip(subset, mask) if flagged)
    practical = config.correction_validation.practical_effect
    final_by_id = {r["id"]: r for r in after}
    paired_before = [{**r, **{iso: r.get(iso) if final_by_id.get(r["id"], {}).get(iso) is not None else None for iso in ISOTOPES}} for r in before]
    return {"rows": rows, "diagnostics_before": screen_effects(diagnostics(paired_before), practical),
            "diagnostics_after": screen_effects(diagnostics(after), practical), "correction_review": review,
            "outliers": {"method": outlier_method, "threshold": threshold, "basis": "Final session QC, separate material populations; flags do not exclude observations", "flags": flags},
            "workbooks": len(detail["runs"]), "paired_results": len(paired_ids),
            "qc_statistics": {iso: {"imported": summary([r[iso] for r in rows if r.get("role") == "qc" and not r.get("excluded") and r.get(iso) is not None]),
                                   "final": summary([r[iso] for r in after if r.get("role") == "qc" and not r.get("excluded") and r.get(iso) is not None])} for iso in ISOTOPES}}
