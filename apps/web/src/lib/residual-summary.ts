import type { CorrectionReview, Fit } from "./metrology";

export function residualEffectSummary(fit?: Fit) {
  const points = fit?.points?.filter(point => !point.excluded_from_fit && Number.isFinite(point.x) && Number.isFinite(point.y)) ?? [];
  const xs = points.map(point => point.x);
  const span = xs.length > 1 ? Math.max(...xs) - Math.min(...xs) : null;
  return {
    effect: fit?.effect_span ?? null,
    uncertainty: span != null && fit?.slope_se != null ? Math.abs(span) * fit.slope_se : null,
    interval: span != null && fit?.slope_ci95 ? fit.slope_ci95.map(value => value * span) : null,
    relevant: fit?.effect_span != null && fit.practical_threshold != null ? Math.abs(fit.effect_span) >= fit.practical_threshold : null,
  };
}

export function qcSdEvidence(review: CorrectionReview) {
  if (review.sd_reduction_fraction == null) return "Comparison unavailable";
  if (review.sd_reduction_fraction <= 0) return "No SD reduction";
  if (review.paired_n < review.criteria.minimum_qc || review.sd_reduction_fraction < review.criteria.minimum_sd_reduction_fraction) return "SD reduction below criterion";
  return review.reduction_interval95 && review.reduction_interval95[0] > 0 ? "SD reduction demonstrated" : "SD reduction inconclusive";
}

export function correctionUncertaintyRange(fit?: Fit) {
  const points = fit?.points?.filter(point => !point.excluded_from_fit) ?? [];
  // Missing uncertainty, including manual coefficients, must remain unavailable.
  if (!points.length || points.some(point => point.u_correction == null || !Number.isFinite(point.u_correction))) return null;
  const values = points.map(point => point.u_correction!);
  return [Math.min(...values), Math.max(...values)];
}
