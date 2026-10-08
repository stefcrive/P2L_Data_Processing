"""Persist derived QC screening, scoped to immutable evaluation populations."""
from __future__ import annotations

import hashlib
import math

import pandas as pd

from ..domain.calibration.core import identify_outliers, identify_outliers_iqr
from .models import ISOTOPES
from .repository import encode, uid


def failure_category(row, iso=None):
    """Classify acquisition evidence before method-stage values replace raw deltas."""
    if iso in row.get("failure_categories", {}):
        return row["failure_categories"][iso]
    issues = row.get("issues", [])
    signal = row.get("i44_v")
    if ("Qtegra reports an acquisition failure" in issues or row.get("collector_status") == "Failed Sample"
            or (isinstance(signal, (int, float)) and (not math.isfinite(signal) or signal <= 0))
            or (iso in row and row[iso] is None and row.get("isotopes", {}).get(iso, {}).get("value") is None)):
        return "no_signal"
    # Qualification range warnings are not acquisition failure evidence.
    if row.get("pressure_failed") or "Qtegra pressure adjustment failed" in issues:
        return "pressure_adjustment"
    return None


def detect_qc_outliers(rows, method="sigma", threshold=3.0, pressure_adjustment_as_outlier=False):
    if method not in ("sigma", "iqr") or not math.isfinite(threshold) or not .5 <= threshold <= 10:
        raise ValueError("Choose sigma or IQR screening and a threshold between 0.5 and 10")
    flags = []
    for iso in ISOTOPES:
        selected = [r for r in rows if r.get("role") == "qc" and not r.get("excluded")
                    and not failure_category(r, iso) and r.get(iso) is not None and math.isfinite(r[iso])]
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
        for row in rows:
            category = failure_category(row, iso)
            if row.get("excluded") or row.get("role") not in ("qc", "unknown", "anchor"):
                continue
            if category == "no_signal" or (category == "pressure_adjustment" and pressure_adjustment_as_outlier):
                flags.append({"measurement_id": row["id"], "run_id": row["run_id"], "evaluation_id": row.get("evaluation_id"),
                              "isotope": iso, "value": row.get(iso), "material_id": row.get("material_id"),
                              "type": iso, "category": category, "is_outlier": True,
                              "reasons": ["No signal or failed acquisition" if category == "no_signal" else "Poor pressure adjustment classified as an outlier by session setting"]})
    return {"method": method, "threshold": threshold, "pressure_adjustment_as_outlier": pressure_adjustment_as_outlier, "flags": flags,
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
                "pressure_adjustment": ["Qtegra pressure adjustment failed"] if failure_category(row, iso) == "pressure_adjustment" else [],
                "no_signal": ["No signal or failed acquisition"] if failure_category(row, iso) == "no_signal" else [],
            }
            flags.extend({"measurement_id": row["id"], "run_id": row["run_id"], "evaluation_id": row.get("evaluation_id"),
                          "isotope": iso, "category": category, "value": row.get("isotopes", {}).get(iso, {}).get("value"),
                          "reasons": reasons, "metadata_only": category == "range" and all(missing_metadata(row, reason, iso) for reason in reasons)}
                         | ({"session_qc_admitted": True} if category == "pressure_adjustment" and row.get("isotopes", {}).get(iso, {}).get("session_qc_admitted") else {})
                         for category, reasons in categories.items() if reasons)
    return flags


def stored_qc_screening(repo, db, session, runs=None):
    runs = runs if runs is not None else [repo.get(db, "runs", rid) for rid in session["run_ids"]]
    settings = session.get("outlier_screening", {"method": "sigma", "threshold": 3.0})
    population = sorted((r["id"], r.get("latest_evaluation_id"), r.get("revision")) for r in runs)
    # Screen the same complete method-stage population used by residual fitting.
    # Replace screenings that incorrectly classified qualification range warnings as failures.
    fingerprint = hashlib.sha256(encode({"version": 7, "population": population, "settings": settings}).encode()).hexdigest()
    previous = db.execute("SELECT * FROM qc_screenings WHERE session_id=? AND fingerprint=?", (session["id"], fingerprint)).fetchone()
    if previous:
        return repo.unpack(previous)
    rows = []
    for run in runs:
        if not run.get("latest_evaluation_id"):
            continue
        evaluation = repo.get(db, "evaluations", run["latest_evaluation_id"])
        from .pipeline import process_value
        for row in evaluation["results"]:
            if row.get("excluded"):
                continue
            values = {}
            for iso in ISOTOPES:
                value = row.get("isotopes", {}).get(iso, {}).get("value")
                model = evaluation.get("normalization", {}).get(iso)
                if value is None and row.get(iso) is not None and model:
                    try:
                        value = process_value(row, iso, model, run, allow_extrapolation=True)["value"]
                    except (ValueError, KeyError):
                        pass
                values[iso] = value
            rows.append({**row, "run_id": run["id"], "evaluation_id": evaluation["id"],
                         "failure_categories": {iso: failure_category(row, iso) for iso in ISOTOPES}, **values})
    result = detect_qc_outliers(rows, **settings)
    result["basis"] = "Session method-stage QC; pressure-adjustment and no-signal analyses excluded from statistical estimation. No-signal analyses are outliers; poor pressure adjustment is classified as an outlier only when enabled."
    db.execute("INSERT INTO qc_screenings(id,session_id,fingerprint,data,created_at) VALUES(?,?,?,?,?) ON CONFLICT(session_id,fingerprint) DO NOTHING",
               (uid(), session["id"], fingerprint, encode({**result, "population": population}), repo.timestamp()))
    return repo.unpack(db.execute("SELECT * FROM qc_screenings WHERE session_id=? AND fingerprint=?", (session["id"], fingerprint)).fetchone())
