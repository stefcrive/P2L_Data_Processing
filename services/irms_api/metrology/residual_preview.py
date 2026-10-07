"""Residual previews on recorded final observations, never released results.

OLS covariance uses n-p residual degrees of freedom. Correction uncertainty is a
contrast of fitted coefficients, g Cov(beta) g.T (JCGM 100, section 5.2). Because
QC observations also estimate beta, their corrected scatter is not independent
validation or a complete result uncertainty budget.
"""
import math

import numpy as np

from .models import ResidualOverride
from .science import regression, summary


def _fit_preview(points, settings):
    degree = 2 if settings.algorithm == "quadratic" else 1
    retained = [p for p in points if not p.get("excluded_from_fit")]
    x = np.array([p["x"] for p in retained], dtype=float)
    y = np.array([p["y"] for p in retained], dtype=float)
    has_control = bool(retained) and all("control" in p for p in retained)
    n, parameters = len(x), degree + 1 + int(has_control)
    if n <= parameters or len(np.unique(x)) < degree+1 or np.ptp(x) <= 1e-12:
        return {"status": "insufficient_evidence", "points": [],
                "reason": "Insufficient independent points or predictor range for this fit."}

    # Solve in a centered/scaled basis, including large sequence predictors.
    origin, scale = float(np.median(x)), float(np.ptp(x))
    z = (x - origin) / scale
    design = np.polynomial.polynomial.polyvander(z, degree)
    if has_control:
        control = np.array([p["control"] for p in retained], dtype=float)
        if not np.isfinite(control).all() or np.ptp(control) <= 1e-12:
            return {"status": "insufficient_evidence", "points": [], "reason": "Rank-deficient correction model"}
        design = np.column_stack((design, (control-control.mean())/np.ptp(control)))
    beta, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
    if rank != parameters:
        return {"status": "insufficient_evidence", "points": [], "reason": "Rank-deficient correction model"}
    inverse = np.linalg.pinv(design)
    residuals = y - design @ beta
    df = n - parameters
    variance = float(residuals @ residuals) / df
    covariance = (inverse @ inverse.T) * variance
    quadratic = float(beta[2] / scale**2) if degree == 2 else 0.
    slope = float(beta[1] / scale - 2 * origin * quadratic)
    center = settings.center if settings.center is not None else origin
    fitted_slope, fitted_quadratic = slope, quadratic
    if settings.slope is not None:
        slope = settings.slope
    if degree == 2 and settings.quadratic is not None:
        quadratic = settings.quadratic
    manual = settings.slope is not None or (degree == 2 and settings.quadratic is not None)
    if not all(math.isfinite(v) for v in (slope, quadratic, center, settings.offset)):
        return {"status": "insufficient_evidence", "points": [], "reason": "Non-finite correction parameters"}

    all_x = np.array([p["x"] for p in points], dtype=float)
    all_y = np.array([p["y"] for p in points], dtype=float)
    z_all, z_ref = (all_x - origin) / scale, (center - origin) / scale
    contrast = np.polynomial.polynomial.polyvander(z_all, degree)
    contrast -= np.array([z_ref**k for k in range(degree+1)])
    if has_control:
        contrast = np.column_stack((contrast, np.zeros(len(all_x))))
    delta = contrast @ beta
    # Overrides are per-unit coefficients, with no factor of 10 or 100.
    delta += (slope - fitted_slope) * (all_x - center)
    if degree == 2:
        delta += (quadratic - fitted_quadratic) * (all_x - center) * (all_x + center)
    corrected = all_y - delta + settings.offset
    u_correction = np.sqrt(np.maximum(0, np.einsum("ij,jk,ik->i", contrast, covariance, contrast)))
    keep = np.array([not p.get("excluded_from_fit") for p in points])
    result = regression(all_x[keep].tolist(), corrected[keep].tolist())
    result["points"] = [{**point, "y": float(value), "adjustment": float(-change + settings.offset),
                         "u_correction": None if manual else float(u),
                         "extrapolated": bool(point["x"] < x.min() or point["x"] > x.max())}
                        for point, value, change, u in zip(points, corrected, delta, u_correction)]

    # An after-slope fitted on the training QC is descriptive, not validation.
    result.update(slope_se=None, slope_ci95=None, covariance=None, hc3_covariance=None,
                  intercept_se=None, residual_standard_error=math.sqrt(variance),
                  residual_degrees_of_freedom=df, screening_status="review_preview",
                  practical_threshold=settings.practical_threshold,
                  before=summary(y.tolist()), after=summary(corrected[keep].tolist()),
                  scope="review_preview", independent_validation=False)
    transform = np.zeros((degree, parameters))
    transform[0, 1] = 1 / scale
    if degree == 2:
        transform[0, 2] = -2 * origin / scale**2
        transform[1, 2] = 1 / scale**2
    coefficient_covariance = transform @ covariance @ transform.T
    result["model"] = {"slope": slope, "quad": quadratic, "degree": degree, "x_ref": center,
                       "coefficient_order": ["slope", "quad"][:degree],
                       "coefficient_covariance": None if manual else coefficient_covariance.tolist(),
                       "u_slope": None if manual else math.sqrt(max(0., coefficient_covariance[0, 0])),
                       "u_quad": None if manual or degree == 1 else math.sqrt(max(0., coefficient_covariance[1, 1])),
                       "residual_degrees_of_freedom": df,
                       "formula": "y_corrected = y - b*(x-x_ref) - q*(x^2-x_ref^2) + offset"}
    result["uncertainty"] = {
        "status": "unavailable_manual_coefficients" if manual else "estimated_coefficient_component",
        "component": "Fitted correction relative to the fixed reference predictor",
        "formula": "u_correction^2 = g Cov(beta) g^T",
        "assumptions": "Independent homoscedastic fit residuals; predictor, reference and offset treated as fixed",
        "limitation": "Same QC used for fitting; coefficient uncertainty is not independent of the same QC scatter or a complete result budget",
    }
    return result


def residual_previews(diagnostics, overrides):
    output = {}
    for material in diagnostics["materials"]:
        for iso, effects in material["isotopes"].items():
            for effect, fit in effects.items():
                key = f"{material['material_id']}:{effect}:{iso}"
                if key not in overrides:
                    continue
                settings = ResidualOverride.model_validate(overrides[key])
                if not settings.enabled:
                    continue
                points = [p for p in fit.get("points", [])
                          if all(isinstance(p.get(k), (int, float)) and math.isfinite(p[k]) for k in ("x", "y"))]
                output[key] = _fit_preview(points, settings)
    return output


def linearity_preview_views(diagnostics, previews):
    """Plot one intensity-corrected population against each signal predictor.

    Do not independently flatten each panel: that would show three different
    corrections as though they were the same observations. Join by measurement
    ID, retaining exclusions and correction uncertainty on each point.
    """
    output = {}
    for material in diagnostics["materials"]:
        for iso, effects in material["isotopes"].items():
            prefix = f"{material['material_id']}:"
            source_key = f"{prefix}intensity_dependence:{iso}"
            source = previews.get(source_key)
            if not source or not source.get("model"):
                continue
            corrected = {p["id"]: p for p in source["points"] if p.get("id") is not None}
            output[source_key] = source
            for effect in ("sample_reference_dependence", "pressure_adjusted_dependence"):
                points = [{**p, **{k: corrected[p["id"]].get(k) for k in
                                  ("y", "adjustment", "u_correction", "extrapolated")},
                           "excluded_from_fit": p.get("excluded_from_fit", False)
                               or corrected[p["id"]].get("excluded_from_fit", False)}
                          for p in effects.get(effect, {}).get("points", []) if p.get("id") in corrected]
                retained = [p for p in points if not p["excluded_from_fit"]]
                fit = regression([p["x"] for p in retained], [p["y"] for p in retained])
                fit.update(points=points, source_correction=source_key, scope="review_preview",
                           independent_validation=False, screening_status="review_preview",
                           slope_se=None, slope_ci95=None, covariance=None, hc3_covariance=None,
                           intercept_se=None)
                output[f"{prefix}{effect}:{iso}"] = fit
    return output
