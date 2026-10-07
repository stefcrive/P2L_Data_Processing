"""Measurement models. SD is always sample SD; no implicit outlier deletion."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import stats


def summary(values: list[float]) -> dict[str, Any]:
    data = np.asarray(values, dtype=float)
    data = data[np.isfinite(data)]
    n = len(data)
    sd = float(np.std(data, ddof=1)) if n > 1 else None
    return {"n": n, "mean": float(np.mean(data)) if n else None, "sd": sd,
            "se_mean": sd / math.sqrt(n) if sd is not None else None}


def regression(x: list[float], y: list[float]) -> dict[str, Any]:
    a, b = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    finite = np.isfinite(a) & np.isfinite(b)
    a, b = a[finite], b[finite]
    n = len(a)
    if n < 3 or float(np.ptp(a)) <= 1e-12:
        return {"status": "insufficient_evidence", "n": n, "reason": "At least three paired observations and distinct predictor values are required",
                "points": [{"x": float(xx), "y": float(yy)} for xx, yy in zip(a, b)]}
    # Center the predictor to avoid unstable inversion for large sequence/date values.
    center = float(np.mean(a))
    design = np.column_stack((np.ones(n), a - center))
    fit, *_ = np.linalg.lstsq(design, b, rcond=None)
    residuals = b - design @ fit
    sse = float(residuals @ residuals)
    inv = np.linalg.inv(design.T @ design)
    cov_center = inv * sse / (n - 2)
    transform = np.array([[1., -center], [0., 1.]])
    cov = transform @ cov_center @ transform.T
    intercept, slope = transform @ fit
    h = np.sum((design @ inv) * design, axis=1)
    weights = (residuals / np.maximum(1 - h, 1e-10)) ** 2
    robust = transform @ (inv @ (design.T @ (weights[:, None] * design)) @ inv) @ transform.T
    se_b, se_a = np.sqrt(np.maximum(0, np.diag(cov)))[::-1]
    t = float(stats.t.ppf(.975, n - 2))
    total = float(np.sum((b - np.mean(b)) ** 2))
    return {"status": "estimated", "n": n, "slope": float(slope), "intercept": float(intercept),
            "residuals": residuals.tolist(), "residual_standard_error": math.sqrt(sse / (n - 2)),
            "covariance": cov.tolist(), "hc3_covariance": robust.tolist(),
            "slope_se": float(se_b), "intercept_se": float(se_a),
            "slope_ci95": [float(slope - t * se_b), float(slope + t * se_b)],
            "r_squared": 1 - sse / total if total > 1e-24 else None,
            "x_range": [float(a.min()), float(a.max())], "effect_span": float(slope * np.ptp(a)),
            "points": [{"x": float(xx), "y": float(yy)} for xx, yy in zip(a, b)],
            "interpretation": "Association only. No correction is automatically applied."}


def partial_regression(x: list[float], y: list[float], control: list[float]) -> dict:
    """Pressure effect conditional on intensity, using Frisch-Waugh-Lovell.

    Both axes are residualized on the same complete-case population. Slope
    covariance and leverage come from the full three-parameter model.
    """
    x, y, control = (np.asarray(values, dtype=float) for values in (x, y, control))
    finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(control)
    x, y, control = x[finite], y[finite], control[finite]
    n = len(x)
    if n < 4 or np.ptp(control) <= 1e-12:
        return {"status": "insufficient_evidence", "n": n, "points": []}
    x_model, y_model = regression(control.tolist(), x.tolist()), regression(control.tolist(), y.tolist())
    xr = x - x_model["intercept"] - x_model["slope"] * control
    yr = y - y_model["intercept"] - y_model["slope"] * control
    if np.ptp(xr) <= max(1e-12, float(np.ptp(x))*1e-10):
        return {"status": "insufficient_evidence", "n": n, "points": [],
                "reason": "Pressure and intensity do not identify independent effects"}
    design = np.column_stack((np.ones(n), control-control.mean(), xr))
    beta, _, rank, _ = np.linalg.lstsq(design, yr, rcond=None)
    if rank != 3:
        return {"status": "insufficient_evidence", "n": n, "points": []}
    inverse = np.linalg.pinv(design)
    residual = yr-design@beta
    df = n-3
    variance = float(residual@residual)/df
    cov = inverse@inverse.T*variance
    leverage = np.sum(design*inverse.T, axis=1)
    weights = (residual/np.maximum(1-leverage, 1e-10))**2
    robust = (inverse*weights)@inverse.T
    result = regression(xr.tolist(), yr.tolist())
    se = math.sqrt(max(0., cov[2, 2]))
    t = float(stats.t.ppf(.975, df))
    result.update(slope=float(beta[2]), slope_se=se,
                  slope_ci95=[float(beta[2]-t*se), float(beta[2]+t*se)],
                  intercept=float(beta[0]), intercept_se=math.sqrt(max(0., cov[0, 0])),
                  covariance=cov[np.ix_([0, 2], [0, 2])].tolist(),
                  hc3_covariance=robust[np.ix_([0, 2], [0, 2])].tolist(),
                  residuals=residual.tolist(), residual_standard_error=math.sqrt(variance),
                  residual_degrees_of_freedom=df, nuisance_parameters=1,
                  effect_span=float(beta[2]*np.ptp(xr)),
                  detrending={"x": {k: x_model[k] for k in ("intercept", "slope")},
                              "y": {k: y_model[k] for k in ("intercept", "slope")}},
                  interpretation="Partial pressure effect after removing intensity from both axes")
    return result


def covariance_matrix(matrix: list[list[float]], n: int) -> np.ndarray:
    cov = np.asarray(matrix, dtype=float)
    if cov.shape != (n, n) or not np.isfinite(cov).all():
        raise ValueError(f"Covariance must be a finite {n} by {n} matrix")
    if not np.allclose(cov, cov.T, atol=1e-12) or np.min(np.linalg.eigvalsh(cov)) < -1e-12:
        raise ValueError("Covariance must be symmetric and positive semidefinite")
    if np.any(np.diag(cov) < 0):
        raise ValueError("Covariance variances cannot be negative")
    return cov


def anchor_model(assigned: list[float], measured: list[float], uncertainties: list[float], covariance=None) -> dict:
    # Input order: assigned low, assigned high, measured low, measured high.
    values = np.asarray([*assigned, *measured], dtype=float)
    uncertainties = np.asarray(uncertainties, dtype=float)
    if values.shape != (4,) or uncertainties.shape != (4,) or not np.isfinite(values).all() or not np.isfinite(uncertainties).all() or np.any(uncertainties < 0):
        raise ValueError("Four finite anchor inputs and nonnegative standard uncertainties are required")
    cov = covariance_matrix(covariance if covariance is not None else np.diag(uncertainties ** 2).tolist(), 4)
    if not np.allclose(np.diag(cov), uncertainties ** 2, rtol=1e-6, atol=1e-12):
        raise ValueError("Covariance diagonal must match the supplied anchor standard uncertainties")
    d = measured[1] - measured[0]
    separation_u = math.sqrt(max(0., cov[2, 2] + cov[3, 3] - 2 * cov[2, 3]))
    if abs(d) <= max(1e-9, 6 * separation_u) or abs(assigned[1] - assigned[0]) <= 1e-9:
        raise ValueError("Anchor separation is too small relative to its uncertainty")
    slope = (assigned[1] - assigned[0]) / d
    intercept = assigned[0] - slope * measured[0]
    jb = np.array([-1 / d, 1 / d, slope / d, -slope / d])
    ja = np.array([1., 0., -slope, 0.]) - measured[0] * jb
    jac = np.vstack([ja, jb])
    return {"assigned": assigned, "measured": measured, "input_covariance": cov.tolist(),
            "intercept": intercept, "slope": slope, "parameter_covariance": (jac @ cov @ jac.T).tolist(),
            "formula": "y = A1 + (x - M1) * (A2 - A1) / (M2 - M1)",
            "input_order": ["A1", "A2", "M1", "M2"], "residual_uncertainty": None,
            "includes_assigned_value_uncertainty": True}


def normalize(x: float, model: dict, *, monte_carlo: bool = False, draws: int = 20000, seed: int = 253) -> dict:
    a1, a2 = model["assigned"]
    m1, m2 = model["measured"]
    slope = model["slope"]
    t = (x - m1) / (m2 - m1)
    jac = np.array([1 - t, t, slope * (t - 1), -slope * t])
    cov = covariance_matrix(model["input_covariance"], 4)
    result = {"value": float(a1 + t * (a2 - a1)), "u_norm": math.sqrt(max(0., float(jac @ cov @ jac))),
              "jacobian": jac.tolist(), "extrapolated": t < 0 or t > 1}
    if monte_carlo:
        if not 1000 <= draws <= 200000:
            raise ValueError("Monte Carlo draws must be between 1000 and 200000")
        rng = np.random.default_rng(seed)
        samples = rng.multivariate_normal([a1, a2, m1, m2], cov, size=draws)
        denominator = samples[:, 3] - samples[:, 2]
        if np.any(denominator * (m2 - m1) <= 0):
            raise ValueError("Monte Carlo anchor order changed; improve anchor separation")
        ys = samples[:, 0] + (x - samples[:, 2]) * (samples[:, 1] - samples[:, 0]) / denominator
        result["monte_carlo"] = {"seed": seed, "draws": draws, "mean": float(np.mean(ys)),
                                "u": float(np.std(ys, ddof=1)), "interval95": np.quantile(ys, [.025, .975]).tolist(),
                                "distribution": "Joint normal anchor inputs; covariance as recorded"}
    return result


def corrected_normalize(x: float, predictor: float | None, model: dict, *, monte_carlo=False, draws=20000, seed=253) -> dict:
    """Propagate a shared correction coefficient through sample AND both anchors.

    Anchor covariance is conditional on the independently estimated coefficient.
    Conditional replicate scatter supplies anchor mean uncertainty; the shared
    coefficient is counted once with its complete measurement-model derivative.
    """
    correction = model.get("correction")
    if correction is None:
        return normalize(x, model, monte_carlo=monte_carlo, draws=draws, seed=seed)
    if predictor is None or not correction["domain"]["low"] <= predictor <= correction["domain"]["high"]:
        raise ValueError("Correction predictor is missing or outside its validated domain")
    c, uc = correction["slope"], correction["u_slope"]
    d0 = predictor - correction["center"]
    d1, d2 = (p - correction["center"] for p in correction["anchor_predictor_means"])
    corrected_x = x - c * d0
    result = normalize(corrected_x, model)
    m1, m2 = model["measured"]
    t = (corrected_x - m1) / (m2 - m1)
    sensitivity = model["slope"] * ((1 - t) * d1 + t * d2 - d0)
    separation_u = math.sqrt(max(0., model["input_covariance"][2][2] + model["input_covariance"][3][3]
                                   - 2 * model["input_covariance"][2][3]) + (uc * (d2 - d1)) ** 2)
    if abs(m2 - m1) <= max(1e-9, 6 * separation_u):
        raise ValueError("Anchor separation is too small including correction coefficient uncertainty")
    result["correction"] = {"name": correction["name"], "raw_value": x, "predictor": predictor,
                            "adjustment": -c * d0, "corrected_value": corrected_x,
                            "coefficient_sensitivity": sensitivity, "u": abs(sensitivity) * uc,
                            "formula": "z=x-c(p-p0); dy/dc=b*((1-t)*(P1-p0)+t*(P2-p0)-(p-p0))"}
    result["u_normalization_and_correction"] = math.hypot(result["u_norm"], abs(sensitivity) * uc)
    if monte_carlo:
        if not 1000 <= draws <= 200000:
            raise ValueError("Monte Carlo draws must be between 1000 and 200000")
        rng = np.random.default_rng(seed)
        samples = rng.multivariate_normal([*model["assigned"], m1, m2], model["input_covariance"], size=draws)
        dc = rng.normal(0, uc, draws)
        sample_x = corrected_x - dc * d0
        samples[:, 2] -= dc * d1
        samples[:, 3] -= dc * d2
        denominator = samples[:, 3] - samples[:, 2]
        if np.any(denominator * (m2 - m1) <= 0):
            raise ValueError("Monte Carlo anchor order changed including correction uncertainty")
        ys = samples[:, 0] + (sample_x - samples[:, 2]) * (samples[:, 1] - samples[:, 0]) / denominator
        result["monte_carlo"] = {"seed": seed, "draws": draws, "mean": float(np.mean(ys)),
                                "u": float(np.std(ys, ddof=1)), "interval95": np.quantile(ys, [.025, .975]).tolist(),
                                "distribution": "Joint normal anchor inputs plus independent normal correction coefficient, shared by sample and anchors"}
    return result


def budget(components: list[dict], k: float, covariance=None) -> dict:
    names, coverage = set(), set()
    for item in components:
        if item["name"] in names:
            raise ValueError("Uncertainty component names must be unique")
        names.add(item["name"])
        overlap = coverage.intersection(item["covers"])
        if overlap:
            raise ValueError("Possible double counting: " + ", ".join(sorted(overlap)))
        coverage.update(item["covers"])
    u = np.asarray([c["u"] for c in components], dtype=float)
    if not np.isfinite(u).all() or np.any(u < 0) or not math.isfinite(k) or k <= 0:
        raise ValueError("Uncertainties and coverage factor must be finite and valid")
    cov = covariance_matrix(covariance, len(u)) if covariance is not None else np.diag(u * u)
    if not np.allclose(np.diag(cov), u * u):
        raise ValueError("Covariance diagonal must match component variances")
    uc = math.sqrt(max(0., float(np.sum(cov))))
    return {"components": components, "u_combined": uc, "expanded_uncertainty": k * uc, "k": k,
            "covariance": cov.tolist(), "unit": "per mille"}


def control_summary(points: list[dict], target: float | None, baseline_sd: float | None, *, exclude_outliers: bool = False) -> dict:
    values = [p["value"] for p in points]
    stats_ = summary(values)
    flags = []
    if target is not None and baseline_sd is not None and baseline_sd > 0:
        for i, p in enumerate(points):
            if abs(p["value"] - target) > 3 * baseline_sd:
                flags.append({"id": p["id"], "rule": "outside_3sd"})
            if i >= 7:
                window = values[i - 7:i + 1]
                if all(v > target for v in window) or all(v < target for v in window):
                    flags.append({"id": p["id"], "rule": "eight_same_side"})
            if i >= 5:
                diff = np.diff(values[i - 5:i + 1])
                if np.all(diff > 0) or np.all(diff < 0):
                    flags.append({"id": p["id"], "rule": "six_point_trend"})
    # A trend/run signal is not an individual outlier. Keep those observations.
    outlier_ids = {flag["id"] for flag in flags if flag["rule"] == "outside_3sd"} if exclude_outliers else set()
    if exclude_outliers:
        for point in points:
            if point.get("session_outlier"):
                outlier_ids.add(point["id"])
                flags.append({"id": point["id"], "rule": "session_qc_outlier"})
        stats_ = summary([p["value"] for p in points if p["id"] not in outlier_ids])
    return {**stats_, "total_n": len(points), "outlier_ids": sorted(outlier_ids),
            "outlier_rule": "outside frozen target ±3 SD or saved session QC screening" if exclude_outliers else None,
            "target": target, "baseline_sd": baseline_sd, "flags": flags, "points": points,
            "limits": [target - 3 * baseline_sd, target + 3 * baseline_sd] if target is not None and baseline_sd else None,
            "status": "out_of_control" if flags else ("no_signal_detected" if baseline_sd and len(points) >= 2 else "insufficient_history")}
