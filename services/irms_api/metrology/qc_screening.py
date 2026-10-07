"""Persist derived QC screening, scoped to immutable evaluation populations."""
from __future__ import annotations

import hashlib
import math

import pandas as pd

from ..domain.calibration.core import identify_outliers, identify_outliers_iqr
from .models import ISOTOPES
from .repository import encode, uid


def detect_qc_outliers(rows, method="sigma", threshold=3.0):
    if method not in ("sigma", "iqr") or not math.isfinite(threshold) or not .5 <= threshold <= 10:
        raise ValueError("Choose sigma or IQR screening and a threshold between 0.5 and 10")
    flags = []
    for iso in ISOTOPES:
        selected = [r for r in rows if r.get("role") == "qc" and not r.get("excluded")
                    and r.get(iso) is not None and math.isfinite(r[iso])]
        for material in sorted({r.get("material_id") for r in selected}, key=str):
            subset = [r for r in selected if r.get("material_id") == material]
            frame = pd.DataFrame({"value": [r[iso] for r in subset]})
            values = frame["value"]
            if method == "sigma":
                mask = identify_outliers(frame, "value", threshold)
                lower, upper = values.mean() - threshold * values.std(), values.mean() + threshold * values.std()
            else:
                mask = identify_outliers_iqr(frame, "value", threshold)
                q1, q3 = values.quantile(.25), values.quantile(.75)
                lower, upper = q1 - threshold * (q3-q1), q3 + threshold * (q3-q1)
            flags.extend({"measurement_id": r["id"], "run_id": r["run_id"],
                          "evaluation_id": r.get("evaluation_id"), "isotope": iso, "value": r[iso],
                          "material_id": material, "lower": float(lower), "upper": float(upper),
                          "population_n": len(subset), "type": iso, "category": "statistical"}
                         for r, flagged in zip(subset, mask) if flagged)
    return {"method": method, "threshold": threshold, "flags": flags,
            "basis": "Final session QC, separate material and isotope populations; excluded from long-term QC statistics, retained in source results"}


def qc_review_flags(rows, config=None):
    """Expose recorded review categories; do not reinterpret them as statistical tests."""
    def missing_metadata(row, reason, iso):
        if " missing or outside validated range" in reason:
            return row.get(reason.split(" missing or outside validated range")[0]) is None
        if reason == f"{iso} internal SD missing or at/above limit":
            return row.get(iso + "_sd") is None
        if "correction predictor" in reason.lower() and config is not None:
            correction = config.corrections.get(iso)
            return correction is not None and row.get(correction.predictor) is None
        return False

    flags = []
    for row in rows:
        if row.get("role") != "qc":
            continue
        for iso in ISOTOPES:
            issues = [issue for issue in row.get("issues", [])
                      if not any(other in issue for other in ISOTOPES if other != iso)]
            categories = {
                "manual": ["Manually excluded analysis"] if row.get("excluded") else [],
                "range": [s for s in issues if "outside validated" in s or "internal SD" in s],
                "failed": [s for s in issues if s in ("Qtegra Evaluate is disabled", "Qtegra pressure adjustment failed", "Qtegra reports an acquisition failure")],
            }
            flags.extend({"measurement_id": row["id"], "run_id": row["run_id"], "evaluation_id": row.get("evaluation_id"),
                          "isotope": iso, "category": category, "value": row.get("isotopes", {}).get(iso, {}).get("value"),
                          "reasons": reasons, "metadata_only": category == "range" and all(missing_metadata(row, reason, iso) for reason in reasons)}
                         for category, reasons in categories.items() if reasons)
    return flags


def stored_qc_screening(repo, db, session, runs=None):
    runs = runs if runs is not None else [repo.get(db, "runs", rid) for rid in session["run_ids"]]
    settings = session.get("outlier_screening", {"method": "sigma", "threshold": 3.0})
    population = sorted((r["id"], r.get("latest_evaluation_id"), r.get("revision")) for r in runs)
    fingerprint = hashlib.sha256(encode({"version": 2, "population": population, "settings": settings}).encode()).hexdigest()
    previous = db.execute("SELECT * FROM qc_screenings WHERE session_id=? AND fingerprint=?", (session["id"], fingerprint)).fetchone()
    if previous:
        return repo.unpack(previous)
    rows = []
    for run in runs:
        if not run.get("latest_evaluation_id"):
            continue
        evaluation = repo.get(db, "evaluations", run["latest_evaluation_id"])
        rows.extend({**r, "run_id": run["id"], "evaluation_id": evaluation["id"],
                     **{iso: r.get("isotopes", {}).get(iso, {}).get("value") for iso in ISOTOPES}}
                    for r in evaluation["results"])
    result = detect_qc_outliers(rows, **settings)
    db.execute("INSERT INTO qc_screenings(id,session_id,fingerprint,data,created_at) VALUES(?,?,?,?,?) ON CONFLICT(session_id,fingerprint) DO NOTHING",
               (uid(), session["id"], fingerprint, encode({**result, "population": population}), repo.timestamp()))
    return repo.unpack(db.execute("SELECT * FROM qc_screenings WHERE session_id=? AND fingerprint=?", (session["id"], fingerprint)).fetchone())
