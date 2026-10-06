"""Paired QC verification of a fixed, independently characterized correction.

No coefficients are estimated from the verification QC. A smaller in-sample SD
does not activate a correction or replace qualification approval.
"""
from __future__ import annotations

import numpy as np

from .models import ISOTOPES
from .science import summary


def comparison_rows(results, models, run):
    rows=[]
    for row in results:
        values={}
        for iso, model in models.items():
            value=row.get(iso)
            # Compare on one reporting scale with the same frozen normalization.
            # Changing b between stages would confound the correction SD comparison.
            values[iso]=(value if run.get("input_basis")=="already_vpdb" else model["intercept"]+model["slope"]*value) if value is not None else None
        rows.append({**row,**values})
    return rows


def correction_review(results, models, config, run, qc):
    baseline={r["id"]:r for r in comparison_rows(results,models,run)}
    output={}
    for iso in ISOTOPES:
        model=models.get(iso,{})
        correction=model.get("correction")
        points=[]
        qc_rows=[r for r in results if r["role"]=="qc" and not r["excluded"]]
        for r in qc_rows:
            before=baseline[r["id"]].get(iso)
            after=r.get("isotopes",{}).get(iso,{}).get("value")
            if before is not None and after is not None:
                points.append({"id":r["id"],"sequence":r["sequence"],"before":before,"after":after,
                               "i44_v":r.get("i44_v"),"pressure_mismatch_v":r.get("pressure_mismatch_v")})
        before,after=summary([p["before"] for p in points]),summary([p["after"] for p in points])
        reduction=1-after["sd"]/before["sd"] if before["sd"] and after["sd"] is not None else None
        criteria=config.correction_validation
        interval=None
        if len(points)>=criteria.minimum_qc and before["sd"]:
            data=np.array([[p["before"],p["after"]] for p in points])
            samples=data[np.random.default_rng(253).integers(0,len(points),(2048,len(points)))]
            sds=np.std(samples,axis=1,ddof=1)
            valid=sds[:,0]>1e-12
            if valid.sum()>=1800:
                interval=np.quantile(1-sds[valid,1]/sds[valid,0],[.025,.975]).tolist()
        reasons=[]
        if not correction:
            status="no_correction"
        elif iso in run.get("preapplied_corrections",[]):
            status="already_applied"
            reasons.append("The export already includes this correction; an uncorrected paired baseline is unavailable.")
            reduction,interval=None,None
        else:
            if len(points)<criteria.minimum_qc: reasons.append("Insufficient paired independent QC aliquots.")
            if len(points)!=len(qc_rows): reasons.append("Some QC observations cannot be corrected within the validated domain.")
            if reduction is None or reduction<criteria.minimum_sd_reduction_fraction: reasons.append("QC SD reduction does not reach the configured minimum.")
            if interval is None or interval[0]<=0: reasons.append("Paired bootstrap interval does not demonstrate an SD reduction.")
            if not qc["isotopes"][iso]["passed"]: reasons.append("Final QC fails precision, bias or individual-analysis acceptance.")
            evidence=bool(correction.get("training_evidence") and correction.get("validation_evidence") and
                          correction["training_evidence"]!=correction["validation_evidence"] and
                          correction.get("independence_rationale") and correction.get("evidence_asset_ids"))
            if not evidence: reasons.append("Separate correction training and validation evidence is missing.")
            span=abs(correction["slope"])*(correction["domain"]["high"]-correction["domain"]["low"])
            if span<criteria.practical_effect[iso]: reasons.append("Correction effect is below the configured practical threshold.")
            if abs(correction["slope"])<=1.96*correction["u_slope"]: reasons.append("The correction coefficient is not resolved from zero at approximately 95% coverage.")
            if run.get("calibration_verification")=="simulation_assumption": reasons.append("The linked qualification is simulated; historical calibration transfer is unverified.")
            status="eligible_for_review" if not reasons else ("not_improved" if reduction is not None and reduction<=0 else "review_required")
        output[iso]={"status":status,"before":before,"after":after,"paired_n":len(points),"total_qc":len(qc_rows),
                     "sd_reduction_fraction":reduction,"reduction_interval95":interval,"points":points,"reasons":reasons,
                     "criteria":criteria.model_dump(),"automatic_approval":False,
                     "basis":"Same QC aliquots, same VPDB scale and frozen normalization slope; before removes only the station's residual correction.",
                     "interval_basis":"2048 paired bootstrap resamples, seed 253. Descriptive support assuming independent QC aliquots; drift/memory require separate review."}
    return output


def screen_effects(diagnostics, practical):
    for material in diagnostics["materials"]:
        for iso, fits in material["isotopes"].items():
            for key in ("mass_dependence","intensity_dependence","pressure_dependence","pressure_residual","drift","memory"):
                fit=fits[key]
                interval=fit.get("slope_ci95")
                resolved=bool(interval and (interval[0]>0 or interval[1]<0))
                relevant=abs(fit.get("effect_span",0))>=practical[iso]
                fit["screening_status"]="effect_detected" if resolved and relevant else "small_effect" if resolved else "inconclusive" if interval else "insufficient_evidence"
                fit["practical_threshold"]=practical[iso]
    return diagnostics
