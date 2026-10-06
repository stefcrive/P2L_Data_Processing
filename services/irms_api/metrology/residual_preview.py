"""Operator review previews using the original IRMS linearity calculation.

Each effect is fitted independently to the recorded final observations. A preview
never replaces a qualified correction, released result, or uncertainty budget.
"""
import math

import pandas as pd

from ..domain.calibration.core import _compute_linearity_fit, _linearity_correction_delta
from .models import ResidualOverride
from .science import regression, summary


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
                points = fit.get("points", [])
                frame = pd.DataFrame(points, columns=["id", "x", "y"])
                minimum = 4 if settings.algorithm == "quadratic" else 3
                if len(frame) < minimum or frame.x.nunique() < (3 if settings.algorithm == "quadratic" else 2):
                    output[key] = {"status": "insufficient_evidence", "points": [], "reason": "Insufficient independent points or predictor range for this fit."}
                    continue
                model = _compute_linearity_fit(frame, "y", "x", quadratic=settings.algorithm == "quadratic")
                for field, target in (("slope", "slope"), ("quadratic", "quad"), ("center", "x_ref")):
                    value = getattr(settings, field)
                    if value is not None:
                        model[target] = value
                if not all(math.isfinite(model[k]) for k in ("slope", "quad", "x_ref")):
                    output[key] = {"status": "insufficient_evidence", "points": []}
                    continue
                corrected = frame.y - _linearity_correction_delta(frame.x, model) + settings.offset
                residual = regression(frame.x.tolist(), corrected.tolist())
                for point, original in zip(residual["points"], points):
                    point["id"] = original.get("id")
                interval = residual.get("slope_ci95")
                resolved = interval and (interval[0] > 0 or interval[1] < 0)
                residual.update(
                    screening_status="effect_detected" if resolved and abs(residual["effect_span"]) >= settings.practical_threshold else "small_effect" if resolved else "inconclusive",
                    practical_threshold=settings.practical_threshold,
                    model={k: model[k] for k in ("slope", "quad", "degree", "x_ref")},
                    before=summary(frame.y.tolist()), after=summary(corrected.tolist()), scope="review_preview",
                )
                output[key] = residual
    return output
