"""One joint correction for the pressure-failed QC population.

Robust residual screening precedes the final OLS fit. Report that screening and
the in-sample slopes; neither is independent validation of corrected samples.
"""
import math

import numpy as np

from .science import regression, summary


def fit_pressure_failed_qc(points, minimum_qc, intensity_ref):
    """Fit delta = a + b*pressure + c*initial intensity on flagged QC only."""
    minimum = max(4, minimum_qc)
    if len(points) < minimum:
        return None
    predictors = np.array([[p["x"], p["intensity"]] for p in points], dtype=float)
    y = np.array([p["y"] for p in points], dtype=float)
    origin, scale = np.median(predictors, axis=0), np.ptp(predictors, axis=0)
    if np.any(scale <= 1e-12):
        return None
    design = np.column_stack((np.ones(len(points)), (predictors-origin)/scale))
    if np.linalg.matrix_rank(design) != 3:
        return None
    # Huber weights keep extreme isotope observations from defining the initial
    # trend. Screening is on regression residuals, not on raw deltas with a trend.
    weights = np.ones(len(points))
    floor = max(1e-12, float(np.max(np.abs(y)))*1e-12)
    for _ in range(100):
        weighted = design*np.sqrt(weights[:, None])
        beta = np.linalg.lstsq(weighted, y*np.sqrt(weights), rcond=None)[0]
        residual = y-design@beta
        spread = max(floor, 1.4826*float(np.median(np.abs(residual-np.median(residual)))))
        updated = np.minimum(1., 1.345*spread/np.maximum(np.abs(residual), floor))
        if np.max(np.abs(updated-weights)) < 1e-8:
            break
        weights = updated
    keep = np.abs(residual-np.median(residual)) <= 3*spread
    for _ in range(len(points)):
        if int(keep.sum()) < minimum or np.linalg.matrix_rank(design[keep]) != 3:
            return None
        beta = np.linalg.lstsq(design[keep], y[keep], rcond=None)[0]
        residual = y-design@beta
        center = float(np.median(residual[keep]))
        spread = max(floor, 1.4826*float(np.median(np.abs(residual[keep]-center))))
        updated = keep & (np.abs(residual-center) <= 3*spread)
        if np.array_equal(updated, keep):
            break
        keep = updated
    n, df = int(keep.sum()), int(keep.sum())-3
    inverse = np.linalg.pinv(design[keep])
    variance = float(residual[keep] @ residual[keep])/df
    transform = np.diag(1/scale)
    covariance = transform @ (inverse @ inverse.T)[1:, 1:] @ transform * variance
    coefficients = beta[1:]/scale
    ref = np.array([0., intensity_ref])
    contrast = predictors-ref
    adjustments = -(contrast @ coefficients)
    corrected = y+adjustments
    before, after = summary(y[keep].tolist()), summary(corrected[keep].tolist())
    improved = before["sd"] and before["sd"]-after["sd"] > max(1e-12, before["sd"]*1e-9)
    result = regression(predictors[keep, 0].tolist(), corrected[keep].tolist())
    intensity_after = regression(predictors[keep, 1].tolist(), corrected[keep].tolist())
    # Fitting removes these slopes by construction; do not present their
    # ordinary regression confidence intervals as independent evidence.
    for diagnostic in (result, intensity_after):
        diagnostic.update(slope_se=None, slope_ci95=None, covariance=None, hc3_covariance=None,
                          intercept_se=None, interpretation="In-sample corrected-QC diagnostic")
    result.update(
        status="ready" if improved else "not_improved", n=n,
        before=before, after=after, sd_reduction_fraction=1-after["sd"]/before["sd"] if before["sd"] else 0.,
        fit_range=[float(predictors[keep, 0].min()), float(predictors[keep, 0].max())],
        intensity_fit_range=[float(predictors[keep, 1].min()), float(predictors[keep, 1].max())],
        training_population="pressure_failed_qc", screening_status="robust_residual_screening",
        training_ids=[p["id"] for p, retained in zip(points, keep) if retained],
        fit_excluded_ids=[p["id"] for p, retained in zip(points, keep) if not retained],
        screening={"method": "Huber initialization, then iterative 3-MAD residual screening and OLS",
                   "candidate_n": len(points), "retained_n": n, "residual_threshold": 3*spread},
        intensity_before=regression(predictors[keep, 1].tolist(), y[keep].tolist()),
        intensity_after=intensity_after,
        model={"degree": 1, "slope": float(coefficients[0]), "quad": 0., "x_ref": 0.,
               "intensity_slope": float(coefficients[1]), "intensity_ref": float(intensity_ref),
               "intercept": float(beta[0]-coefficients@origin),
               "coefficient_order": ["pressure", "initial_intensity"],
               "coefficient_covariance": covariance.tolist(),
               "u_slope": math.sqrt(max(0., covariance[0, 0])),
               "u_intensity_slope": math.sqrt(max(0., covariance[1, 1])),
               "residual_degrees_of_freedom": df,
               "formula": "y_corrected = y - b*pressure - c*(initial_intensity-intensity_ref)"},
        uncertainty={"status": "estimated_coefficient_component", "formula": "u_correction^2 = g Cov(beta) g^T",
                     "limitation": "Conditional on residual screening; fitted QC slopes are in-sample diagnostics, not independent validation."},
    )
    result["points"] = [{**p, "y": float(value), "adjustment": float(adjustment),
                         "excluded_from_fit": not bool(retained)}
                        for p, value, adjustment, retained in zip(points, corrected, adjustments, keep)]
    return result
