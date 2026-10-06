import type { LinearityConfig, ProcessingLinearityPreviewData } from "@/lib/types";

export type LinearityIsotopeKey = "d13C" | "d18O";

export type LinearityPreviewValue = {
  raw: number | null;
  calibrated: number | null;
};

export type LinearityPreviewValues = Map<
  string,
  Record<LinearityIsotopeKey, LinearityPreviewValue>
>;

const ISOTOPE_KEYS: LinearityIsotopeKey[] = ["d13C", "d18O"];
const SAMPLE_INTENSITY_COL = "1  Cycle Int  Samp  44";
const REFERENCE_INTENSITY_COL = "1  Cycle Int  Ref  44";
const DIFF_INTENSITY_COL = "1  Cycle Int  Diff Samp-Ref  44";
const SYMMETRIC_MISMATCH_COL = "1  Cycle Int  Symmetric Relative Mismatch Samp-Ref  44";
const RELATIVE_MISMATCH_COL = "1  Cycle Int  Relative Mismatch Samp-Ref/Ref  44";
const TWO_TERM_COL = "Linearity Two-Term Mean Intensity + Symmetric Mismatch 44";

function finiteNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function primaryOffsetScale(intensityCol: string | null | undefined): number {
  return intensityCol === SYMMETRIC_MISMATCH_COL || intensityCol === RELATIVE_MISMATCH_COL ? 1 : 10;
}

function secondaryOffsetScale(intensityCol: string | null | undefined): number {
  return intensityCol === SYMMETRIC_MISMATCH_COL || intensityCol === RELATIVE_MISMATCH_COL || intensityCol === TWO_TERM_COL
    ? 1
    : 100;
}

function median(values: Array<number | null>): number | null {
  const finite = values.filter((value): value is number => value != null && Number.isFinite(value)).sort((a, b) => a - b);
  if (!finite.length) {
    return null;
  }
  const middle = Math.floor(finite.length / 2);
  return finite.length % 2 === 0 ? (finite[middle - 1] + finite[middle]) / 2 : finite[middle];
}

function buildManualOverrideDriver(
  previewData: ProcessingLinearityPreviewData,
  linearity: LinearityConfig,
): { values: Map<string, number | null>; intensityCol: string } {
  const intensityCol = String(linearity.intensity_col ?? previewData.intensity_col ?? "").trim();
  const values = new Map<string, number | null>();
  const sampleMedian = median(previewData.rows.map((row) => finiteNumber(row.intensities[SAMPLE_INTENSITY_COL])));

  for (const row of previewData.rows) {
    let value = finiteNumber(row.intensities[intensityCol]);
    if (linearity.use_diff_intensity && intensityCol === DIFF_INTENSITY_COL) {
      const diff = finiteNumber(row.intensities[DIFF_INTENSITY_COL]);
      const reference = finiteNumber(row.intensities[REFERENCE_INTENSITY_COL]);
      const sample = finiteNumber(row.intensities[SAMPLE_INTENSITY_COL]);
      if (diff != null && reference != null && Math.abs(reference) > 1e-12) {
        value = (diff / reference) * 10;
        if (sample != null && sampleMedian != null && Math.abs(sampleMedian) > 1e-12) {
          value *= sample / sampleMedian;
        }
      } else {
        value = null;
      }
    }
    values.set(String(row.row_label), value);
  }
  return { values, intensityCol };
}

function manualOverrideDelta(
  driver: number | null,
  reference: number | null,
  isotopeKey: LinearityIsotopeKey,
  linearity: LinearityConfig,
  intensityCol: string,
): number | null {
  if (!linearity.manual_override_enabled || driver == null || reference == null || intensityCol === TWO_TERM_COL) {
    return null;
  }
  const primary = finiteNumber(isotopeKey === "d13C" ? linearity.manual_d13_per_10v : linearity.manual_d18_per_10v);
  const secondary = finiteNumber(isotopeKey === "d13C" ? linearity.manual_d13_per_10v2 : linearity.manual_d18_per_10v2);
  let delta = primary == null ? 0 : (primary / primaryOffsetScale(intensityCol)) * (driver - reference);
  if (linearity.quadratic && secondary != null) {
    delta += (secondary / secondaryOffsetScale(intensityCol)) * (driver ** 2 - reference ** 2);
  }
  return Number.isFinite(delta) ? delta : null;
}

function lineOffset(linearity: LinearityConfig, isotopeKey: LinearityIsotopeKey, line: number | null): number {
  if (line !== 1 && line !== 2) {
    return 0;
  }
  if (isotopeKey === "d13C") {
    return line === 1
      ? finiteNumber(linearity.line_1_offset_d13) ?? 0
      : finiteNumber(linearity.line_2_offset_d13) ?? 0;
  }
  return line === 1
    ? finiteNumber(linearity.line_1_offset_d18) ?? 0
    : finiteNumber(linearity.line_2_offset_d18) ?? 0;
}

export function applyManualLinearityOffsets(
  fits: Record<string, unknown> | undefined,
  linearity: LinearityConfig,
): Record<string, unknown> {
  const adjusted: Record<string, unknown> = {
    ...(fits ?? {}),
    d13C: { ...(((fits ?? {}).d13C as Record<string, unknown> | undefined) ?? {}) },
    d18O: { ...(((fits ?? {}).d18O as Record<string, unknown> | undefined) ?? {}) },
  };
  if (!linearity.manual_override_enabled) {
    return adjusted;
  }

  const basisCol = String(adjusted.intensity_col ?? linearity.intensity_col ?? "");
  const offsets: Record<LinearityIsotopeKey, { primary: number; secondary: number }> = {
    d13C: {
      primary: finiteNumber(linearity.manual_d13_per_10v) ?? 0,
      secondary: finiteNumber(linearity.manual_d13_per_10v2) ?? 0,
    },
    d18O: {
      primary: finiteNumber(linearity.manual_d18_per_10v) ?? 0,
      secondary: finiteNumber(linearity.manual_d18_per_10v2) ?? 0,
    },
  };

  for (const isotopeKey of ISOTOPE_KEYS) {
    const fit = { ...((adjusted[isotopeKey] as Record<string, unknown> | undefined) ?? {}) };
    const xRef = finiteNumber(fit.x_ref) ?? 0;
    const baseIntercept = finiteNumber(fit.intercept);
    const primaryRaw = offsets[isotopeKey].primary;
    const secondaryRaw = offsets[isotopeKey].secondary;

    if (String(fit.model ?? "") === "two_term") {
      let interceptShift = 0;
      if (Math.abs(primaryRaw) > 1e-15) {
        const primary = primaryRaw / primaryOffsetScale(TWO_TERM_COL);
        fit.slope = (finiteNumber(fit.slope) ?? 0) + primary;
        interceptShift += primary * xRef;
      }
      if (Math.abs(secondaryRaw) > 1e-15) {
        const secondary = secondaryRaw / secondaryOffsetScale(TWO_TERM_COL);
        fit.quad = (finiteNumber(fit.quad) ?? 0) + secondary;
        const secondaryRef = finiteNumber(fit.secondary_x_ref);
        if (secondaryRef != null) {
          interceptShift += secondary * secondaryRef;
        }
      }
      if (baseIntercept != null && Math.abs(interceptShift) > 1e-15) {
        fit.intercept = baseIntercept - interceptShift;
      }
      adjusted[isotopeKey] = fit;
      continue;
    }

    if (Math.abs(primaryRaw) > 1e-15) {
      const primary = primaryRaw / primaryOffsetScale(basisCol);
      fit.slope = (finiteNumber(fit.slope) ?? 0) + primary;
      if (baseIntercept != null) {
        fit.intercept = baseIntercept - primary * xRef;
      }
    }
    if (linearity.quadratic && Math.abs(secondaryRaw) > 1e-15) {
      const secondary = secondaryRaw / secondaryOffsetScale(basisCol);
      fit.quad = (finiteNumber(fit.quad) ?? 0) + secondary;
      const currentIntercept = finiteNumber(fit.intercept);
      if (currentIntercept != null) {
        fit.intercept = currentIntercept - secondary * xRef ** 2;
      }
      fit.degree = Math.max(Number(fit.degree ?? 1), 2);
    }
    adjusted[isotopeKey] = fit;
  }
  return adjusted;
}

function fitDegree(fit: Record<string, unknown>): number {
  const degree = finiteNumber(fit.degree);
  if ((degree != null && degree >= 2) || fit.quadratic === true) {
    return 2;
  }
  const quad = finiteNumber(fit.quad);
  return quad != null && Math.abs(quad) > 1e-15 ? 2 : 1;
}

function correctionDelta(
  fit: Record<string, unknown>,
  intensity: number | null,
  secondaryIntensity: number | null,
): number | null {
  const slope = finiteNumber(fit.slope);
  const xRef = finiteNumber(fit.x_ref);
  if (slope == null || xRef == null || intensity == null) {
    return null;
  }
  if (String(fit.model ?? "") === "two_term") {
    const secondaryRef = finiteNumber(fit.secondary_x_ref);
    const secondarySlope = finiteNumber(fit.quad);
    if (secondaryRef == null || secondarySlope == null || secondaryIntensity == null) {
      return null;
    }
    return slope * (intensity - xRef) + secondarySlope * (secondaryIntensity - secondaryRef);
  }
  let delta = slope * (intensity - xRef);
  const quad = finiteNumber(fit.quad);
  if (fitDegree(fit) >= 2 && quad != null) {
    delta += quad * (intensity ** 2 - xRef ** 2);
  }
  return Number.isFinite(delta) ? delta : null;
}

export function buildLinearityPreviewValues(
  previewData: ProcessingLinearityPreviewData | undefined,
  linearity: LinearityConfig | null | undefined,
): LinearityPreviewValues {
  const values: LinearityPreviewValues = new Map();
  if (!previewData || !linearity) {
    return values;
  }
  const effectiveFits = applyManualLinearityOffsets(previewData.fits, linearity);
  const manualDriver = buildManualOverrideDriver(previewData, linearity);
  const manualReference = median(Array.from(manualDriver.values.values()));

  for (const row of previewData.rows) {
    const isotopeValues = {} as Record<LinearityIsotopeKey, LinearityPreviewValue>;
    for (const isotopeKey of ISOTOPE_KEYS) {
      const baseRaw = finiteNumber(isotopeKey === "d13C" ? row.d13_raw : row.d18_raw);
      let raw = baseRaw == null ? null : baseRaw + lineOffset(linearity, isotopeKey, finiteNumber(row.line));
      const directManualDelta = manualOverrideDelta(
        manualDriver.values.get(String(row.row_label)) ?? null,
        manualReference,
        isotopeKey,
        linearity,
        manualDriver.intensityCol,
      );
      const fit = (effectiveFits[isotopeKey] as Record<string, unknown> | undefined) ?? {};
      if (raw != null && linearity.apply) {
        const intensityCol = String(
          (String(fit.model ?? "") === "two_term"
            ? fit.primary_col
            : effectiveFits[isotopeKey === "d13C" ? "d13_intensity_col" : "d18_intensity_col"]) ??
            previewData.intensity_col ??
            linearity.intensity_col ??
            "",
        ).trim();
        const fallbackCol = String(previewData.intensity_col ?? linearity.intensity_col ?? "").trim();
        const intensity = finiteNumber(row.intensities[intensityCol]) ?? finiteNumber(row.intensities[fallbackCol]);
        const secondaryCol = String(fit.secondary_col ?? SYMMETRIC_MISMATCH_COL);
        const delta = correctionDelta(fit, intensity, finiteNumber(row.intensities[secondaryCol]));
        if (delta != null) {
          raw -= delta;
        } else if (directManualDelta != null) {
          // When no standard-derived fit exists, keep manual coefficients useful
          // by anchoring their correction to the median preview intensity.
          raw -= directManualDelta;
        }
      }
      const coefficients = (previewData.coefficients?.[isotopeKey] as Record<string, unknown> | undefined) ?? {};
      const slope = finiteNumber(coefficients.slope);
      const intercept = finiteNumber(coefficients.intercept);
      const fallbackCalibrated = finiteNumber(isotopeKey === "d13C" ? row.d13_calibrated : row.d18_calibrated);
      const calibrated = raw != null && slope != null && intercept != null ? slope * raw + intercept : fallbackCalibrated;
      isotopeValues[isotopeKey] = { raw, calibrated };
    }
    values.set(String(row.row_label), isotopeValues);
  }
  return values;
}

function axisTitle(layout: Record<string, unknown>, axisKey: "xaxis" | "yaxis"): string {
  const axis = layout[axisKey];
  if (!axis || typeof axis !== "object") {
    return "";
  }
  const title = (axis as Record<string, unknown>).title;
  if (typeof title === "string") {
    return title.toLowerCase();
  }
  return title && typeof title === "object" ? String((title as Record<string, unknown>).text ?? "").toLowerCase() : "";
}

function isotopeForAxis(title: string): LinearityIsotopeKey | null {
  if (title.includes("d13c") || title.includes("13c")) {
    return "d13C";
  }
  if (title.includes("d18o") || title.includes("18o")) {
    return "d18O";
  }
  return null;
}

function rowLabelFromCustomData(value: unknown): string {
  if (Array.isArray(value)) {
    return String(value[0] ?? "").trim();
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return String(record.row_label ?? record.rowLabel ?? record[0] ?? "").trim();
  }
  return String(value ?? "").trim();
}

function patchAxisValues(
  source: unknown,
  customData: unknown[],
  isotopeKey: LinearityIsotopeKey | null,
  previewValues: LinearityPreviewValues,
): { values: unknown; changed: boolean } {
  if (!isotopeKey || !Array.isArray(source) || source.length !== customData.length) {
    return { values: source, changed: false };
  }
  let changed = false;
  const values = source.map((current, index) => {
    const next = previewValues.get(rowLabelFromCustomData(customData[index]))?.[isotopeKey].raw;
    if (next == null) {
      return current;
    }
    if (finiteNumber(current) == null || Math.abs(Number(current) - next) > 1e-12) {
      changed = true;
      return next;
    }
    return current;
  });
  return { values: changed ? values : source, changed };
}

export function applyLinearityPreviewToDiagnosticsFigure(
  figure: Record<string, unknown> | undefined,
  previewValues: LinearityPreviewValues,
): Record<string, unknown> | undefined {
  if (!figure || previewValues.size === 0 || !Array.isArray(figure.data)) {
    return figure;
  }
  const layout = figure.layout && typeof figure.layout === "object" ? (figure.layout as Record<string, unknown>) : {};
  const xIsotope = isotopeForAxis(axisTitle(layout, "xaxis"));
  const yIsotope = isotopeForAxis(axisTitle(layout, "yaxis"));
  if (!xIsotope && !yIsotope) {
    return figure;
  }

  let changed = false;
  const data = (figure.data as Array<Record<string, unknown>>).map((trace) => {
    const customData = Array.isArray(trace.customdata) ? trace.customdata : null;
    if (!customData?.length) {
      return trace;
    }
    const x = patchAxisValues(trace.x, customData, xIsotope, previewValues);
    const y = patchAxisValues(trace.y, customData, yIsotope, previewValues);
    if (!x.changed && !y.changed) {
      return trace;
    }
    changed = true;
    return { ...trace, ...(x.changed ? { x: x.values } : {}), ...(y.changed ? { y: y.values } : {}) };
  });
  return changed ? { ...figure, data } : figure;
}
