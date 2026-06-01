"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { PlotlyChart, type PlotlyHoverPayload, type PlotlyPoint } from "@/components/charts/plotly-chart";
import { SharedCycleDiagnosticsTable } from "@/components/diagnostics/cycle-diagnostics-table";
import {
  SATURATION_COLOR_AXIS_OPTIONS,
  SaturationAxisHelpTooltip,
  SaturationSharedColorbar,
  SaturationFigureCard,
  type SaturationAxisKey,
  type SaturationColorAxisKey,
} from "@/components/diagnostics/saturation-figure-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DecimalInput } from "@/components/ui/decimal-input";
import { MultiSelectDropdown } from "@/components/ui/multi-select-dropdown";
import { Tooltip } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import type { CalibrationConfig, CalibrationPrecisionSummary, CycleDiagnosticsPayload, EditAction } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useSessionStore } from "@/store/use-session-store";

const RANGE_FETCH_DEBOUNCE_MS = 300;
const HOVER_PREVIEW_SHOW_DELAY_MS = 500;
const SELECTION_EDITOR_CHART_DEFER_MS = 350;
type LinearityOffsetField = "line_1_offset_d13" | "line_1_offset_d18" | "line_2_offset_d13" | "line_2_offset_d18";
type LinearityCycleIntensityAggregation = "run_median" | "first_valid_cycle" | "last_valid_cycle";
const LINEARITY_INTENSITY_SAMP44 = "1  Cycle Int  Samp  44";
const LINEARITY_INTENSITY_DIFF44 = "1  Cycle Int  Diff Samp-Ref  44";
const LINEARITY_INTENSITY_MISMATCH44 = "1  Cycle Int  Pressure-Weighted Mismatch Samp-Ref  44";
const LINEARITY_INTENSITY_RELATIVE_MISMATCH44 = "1  Cycle Int  Relative Mismatch Samp-Ref/Ref  44";
const LINEARITY_INTENSITY_SYMMETRIC_MISMATCH44 = "1  Cycle Int  Symmetric Relative Mismatch Samp-Ref  44";
const LINEARITY_INTENSITY_MEAN44 = "1  Cycle Int  Mean Samp-Ref  44";
const LINEARITY_INTENSITY_TWO_TERM44 = "Linearity Two-Term Mean Intensity + Symmetric Mismatch 44";
const LINEARITY_INTENSITY_OPTIONS = [
  LINEARITY_INTENSITY_SAMP44,
  LINEARITY_INTENSITY_DIFF44,
  LINEARITY_INTENSITY_SYMMETRIC_MISMATCH44,
  LINEARITY_INTENSITY_MEAN44,
  LINEARITY_INTENSITY_TWO_TERM44,
] as const;
const LINEARITY_INTENSITY_OPTION_LABELS: Record<(typeof LINEARITY_INTENSITY_OPTIONS)[number], string> = {
  [LINEARITY_INTENSITY_SAMP44]: "Sample intensity",
  [LINEARITY_INTENSITY_DIFF44]: "Samp-Ref difference",
  [LINEARITY_INTENSITY_SYMMETRIC_MISMATCH44]: "Symmetric mismatch",
  [LINEARITY_INTENSITY_MEAN44]: "Mean Samp-Ref intensity",
  [LINEARITY_INTENSITY_TWO_TERM44]: "Two-term: mean + mismatch",
};
const LINEARITY_CYCLE_INTENSITY_AGGREGATION_OPTIONS: Array<{ value: LinearityCycleIntensityAggregation; label: string }> = [
  { value: "run_median", label: "Run median intensities" },
  { value: "first_valid_cycle", label: "First valid cycle intensity" },
  { value: "last_valid_cycle", label: "Last valid cycle intensity" },
];
type LinearityCoefficientTerm = "primary" | "secondary";
type ColorScaleBounds = {
  min: number;
  max: number;
};
type FigureShape = Record<string, unknown> & {
  data: Array<Record<string, unknown>>;
  layout: Record<string, unknown>;
};
type StoredSelectedTarget = {
  rowLabel: string;
  isotopeKey: "d13C" | "d18O" | "cross";
  identifier1: string;
  identifier2: string;
  currentValue: number | null;
  currentD13: number | null;
  currentD18: number | null;
  chartKey: string;
};
type IsotopeKey = "d13C" | "d18O";
type IsotopeNumericMap = Record<IsotopeKey, number>;
const ISOTOPE_KEYS: IsotopeKey[] = ["d13C", "d18O"];
type HoverPreviewState = {
  target: StoredSelectedTarget;
  clientX: number;
  clientY: number;
};

function getLinearityIntensityOptionLabel(value: string): string {
  if (value in LINEARITY_INTENSITY_OPTION_LABELS) {
    return LINEARITY_INTENSITY_OPTION_LABELS[value as (typeof LINEARITY_INTENSITY_OPTIONS)[number]];
  }
  return value;
}

function getLinearityCycleAggregationLabel(value: string | null | undefined): string {
  return LINEARITY_CYCLE_INTENSITY_AGGREGATION_OPTIONS.find((option) => option.value === value)?.label ?? "Run median intensities";
}

function getLinearityAggregationExpression(expression: string, aggregation: LinearityCycleIntensityAggregation = "run_median"): string {
  if (aggregation === "first_valid_cycle") {
    return `first_valid_cycle(${expression})`;
  }
  if (aggregation === "last_valid_cycle") {
    return `last_valid_cycle(${expression})`;
  }
  return `median(${expression})`;
}

function getLinearityBasisFormula(intensityCol: string, aggregation: LinearityCycleIntensityAggregation = "run_median"): string {
  if (intensityCol === LINEARITY_INTENSITY_MISMATCH44) {
    return "Legacy cycle basis: I = 10 * (Samp44 - Ref44) / Ref44 * (Samp44 / median(Samp44))";
  }
  if (intensityCol === LINEARITY_INTENSITY_RELATIVE_MISMATCH44) {
    return "Legacy cycle basis: I = (Samp44 - Ref44) / Ref44";
  }
  if (intensityCol === LINEARITY_INTENSITY_SYMMETRIC_MISMATCH44) {
    return `x = ${getLinearityAggregationExpression("(Samp44 - Ref44) / ((Samp44 + Ref44) / 2)", aggregation)} for each analysis`;
  }
  if (intensityCol === LINEARITY_INTENSITY_MEAN44) {
    return `x = ${getLinearityAggregationExpression("(Samp44 + Ref44) / 2", aggregation)} for each analysis`;
  }
  if (intensityCol === LINEARITY_INTENSITY_TWO_TERM44) {
    return `Residual = a + b1 * ${getLinearityAggregationExpression("(Samp44 + Ref44) / 2", aggregation)} + b2 * ${getLinearityAggregationExpression("(Samp44 - Ref44) / ((Samp44 + Ref44) / 2)", aggregation)}`;
  }
  if (intensityCol === LINEARITY_INTENSITY_DIFF44) {
    return `x = ${getLinearityAggregationExpression("Samp44 - Ref44", aggregation)} for each analysis`;
  }
  return `x = ${getLinearityAggregationExpression("Samp44", aggregation)} for each analysis`;
}

function getLinearityBasisDescription(intensityCol: string, aggregation: LinearityCycleIntensityAggregation = "run_median"): string {
  if (intensityCol === LINEARITY_INTENSITY_TWO_TERM44) {
    return "Fits residuals across standards with one row per analysis. Correction is centered as delta = b1 * (I_mean - median(I_mean across the calibration set)) + b2 * (Mismatch - median(Mismatch across the calibration set)).";
  }
  const formula = getLinearityBasisFormula(intensityCol, aggregation);
  if (intensityCol === LINEARITY_INTENSITY_RELATIVE_MISMATCH44 || intensityCol === LINEARITY_INTENSITY_SYMMETRIC_MISMATCH44) {
    return `${formula}. The fit is across analyses, not cycle-by-cycle. Correction is centered at the calibration-set median basis value: delta = b * (x - median(x)); quadratic mode adds c * (x^2 - median(x)^2).`;
  }
  return `${formula}. The fit is across analyses, not cycle-by-cycle. Correction is centered at the calibration-set median basis value: delta = b * (x - median(x)); quadratic mode adds c * (x^2 - median(x)^2). Coefficients for intensity-like bases are entered per 10V.`;
}

function getLinearityBasisTerm(intensityCol: string, aggregation: LinearityCycleIntensityAggregation = "run_median"): string {
  const prefix = aggregation === "first_valid_cycle" ? "first valid cycle" : aggregation === "last_valid_cycle" ? "last valid cycle" : "run median";
  if (intensityCol === LINEARITY_INTENSITY_MISMATCH44) {
    return "intensity-weighted mismatch";
  }
  if (intensityCol === LINEARITY_INTENSITY_RELATIVE_MISMATCH44) {
    return "relative mismatch";
  }
  if (intensityCol === LINEARITY_INTENSITY_SYMMETRIC_MISMATCH44) {
    return `${prefix} symmetric mismatch`;
  }
  if (intensityCol === LINEARITY_INTENSITY_MEAN44) {
    return `${prefix} mean intensity`;
  }
  if (intensityCol === LINEARITY_INTENSITY_TWO_TERM44) {
    return `${prefix} two-term model`;
  }
  if (intensityCol === LINEARITY_INTENSITY_DIFF44) {
    return `${prefix} intensity-diff`;
  }
  return `${prefix} sample-intensity`;
}

function getLinearityCoefficientTermLabel(term: LinearityCoefficientTerm, intensityCol?: string): string {
  if (intensityCol === LINEARITY_INTENSITY_TWO_TERM44) {
    return term === "primary" ? "mean-intensity coefficient (b1)" : "mismatch coefficient (b2)";
  }
  return term === "primary" ? "primary coefficient (b)" : "secondary coefficient (c)";
}

function getLinearityCoefficientUnit(term: LinearityCoefficientTerm, intensityCol: string): string {
  if (intensityCol === LINEARITY_INTENSITY_TWO_TERM44) {
    return term === "primary" ? "per 10V" : "per unit mismatch";
  }
  if (intensityCol === LINEARITY_INTENSITY_RELATIVE_MISMATCH44 || intensityCol === LINEARITY_INTENSITY_SYMMETRIC_MISMATCH44) {
    return term === "primary" ? "per unit mismatch" : "per unit mismatch^2";
  }
  return term === "primary" ? "per 10V" : "per (10V)^2";
}

function getLinearityCoefficientLabel(
  isotope: "d13C" | "d18O",
  intensityCol: string,
  term: LinearityCoefficientTerm,
  aggregation: LinearityCycleIntensityAggregation = "run_median",
): string {
  const prefix = isotope === "d13C" ? "d13C" : "d18O";
  const coefficient = getLinearityCoefficientTermLabel(term, intensityCol).replace(" coefficient", "");
  return `${prefix} ${coefficient} offset, ${getLinearityBasisTerm(intensityCol, aggregation)} ${getLinearityCoefficientUnit(term, intensityCol)}`;
}

function linearityOffsetWithFallback(value: number | null | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function readLinearityOffsetValue(linearity: CalibrationConfig["linearity"], field: LinearityOffsetField): number {
  if (field === "line_1_offset_d13") {
    return linearityOffsetWithFallback(linearity.line_1_offset_d13, linearityOffsetWithFallback(linearity.line_1_offset, 0));
  }
  if (field === "line_1_offset_d18") {
    return linearityOffsetWithFallback(linearity.line_1_offset_d18, linearityOffsetWithFallback(linearity.line_1_offset, 0));
  }
  if (field === "line_2_offset_d13") {
    return linearityOffsetWithFallback(linearity.line_2_offset_d13, linearityOffsetWithFallback(linearity.line_2_offset, 0));
  }
  return linearityOffsetWithFallback(linearity.line_2_offset_d18, linearityOffsetWithFallback(linearity.line_2_offset, 0));
}

function normalizeLinearityConfigForCompare(linearity: CalibrationConfig["linearity"] | null | undefined) {
  if (!linearity) {
    return null;
  }
  return {
    ...linearity,
    max_sample_intensity: linearity.max_sample_intensity ?? null,
    line_1_offset_d13: linearity.line_1_offset_d13 ?? null,
    line_1_offset_d18: linearity.line_1_offset_d18 ?? null,
    line_2_offset_d13: linearity.line_2_offset_d13 ?? null,
    line_2_offset_d18: linearity.line_2_offset_d18 ?? null,
  };
}

function linearityConfigEquals(
  left: CalibrationConfig["linearity"] | null | undefined,
  right: CalibrationConfig["linearity"] | null | undefined,
): boolean {
  return JSON.stringify(normalizeLinearityConfigForCompare(left)) === JSON.stringify(normalizeLinearityConfigForCompare(right));
}

function formatPrecisionMetric(value?: number | null): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "N/A";
  }
  return `${value.toFixed(3)} permil`;
}

function formatCoefficient(value?: number | null): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "N/A";
  }
  return value.toFixed(3);
}

function formatFirstNonZeroDigits(value: number | null | undefined, significantDigits = 2): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "N/A";
  }
  if (value === 0) {
    return "0";
  }
  const sign = value < 0 ? "-" : "";
  const absValue = Math.abs(value);
  const magnitude = Math.floor(Math.log10(absValue));
  const factor = Math.pow(10, magnitude - significantDigits + 1);
  const truncated = Math.trunc(absValue / factor) * factor;
  if (!Number.isFinite(truncated) || truncated === 0) {
    return "0";
  }
  const decimals = factor < 1 ? Math.max(0, Math.ceil(-Math.log10(factor))) : 0;
  const fixed = truncated.toFixed(decimals);
  return `${sign}${fixed.replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1")}`;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item)).filter((item) => item.trim().length > 0);
}

function asString(value: unknown): string {
  return value == null ? "" : String(value);
}

function titleText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (value && typeof value === "object") {
    const text = (value as { text?: unknown }).text;
    if (typeof text === "string") {
      return text;
    }
  }
  return "";
}

function asRange(value: unknown): [number, number] | null {
  if (!Array.isArray(value) || value.length !== 2) {
    return null;
  }
  const low = Number(value[0]);
  const high = Number(value[1]);
  if (!Number.isFinite(low) || !Number.isFinite(high)) {
    return null;
  }
  return [low, high];
}

function formatDeltaValue(value?: number | null): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "N/A";
  }
  return value.toFixed(3);
}

function roundDeltaValue(value: number, precision = 3): number {
  return Number(value.toFixed(precision));
}

function asBoolean(value: unknown): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    return value !== 0;
  }
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    return normalized === "true" || normalized === "1" || normalized === "yes";
  }
  return false;
}

function isDeltaColumnLabel(label: string): boolean {
  const normalized = label.trim().toLowerCase();
  return normalized.includes("d13") || normalized.includes("d18");
}

function isSignalIntensityColumnLabel(label: string): boolean {
  const normalized = label.trim().toLowerCase();
  return normalized.includes("int m/z") && normalized.includes("(v)");
}

function clampNumber(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function parseFinite(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function computeHoverPreviewPosition(
  clientX: number,
  clientY: number,
  tooltipWidth = 440,
  tooltipHeight = 340,
): { left: number; top: number } {
  if (typeof window === "undefined") {
    return { left: clientX + 220, top: clientY - 24 };
  }
  // Keep the diagnostics card to the right of Plotly's native hover label.
  const horizontalOffset = 220;
  const fallbackLeftOffset = 24;
  const verticalOffset = -24;
  const edgePadding = 10;
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;

  let left = clientX + horizontalOffset;
  if (left + tooltipWidth > viewportWidth - edgePadding) {
    left = clientX - tooltipWidth - fallbackLeftOffset;
  }
  if (left < edgePadding) {
    left = edgePadding;
  }

  let top = clientY + verticalOffset;
  if (top + tooltipHeight > viewportHeight - edgePadding) {
    top = viewportHeight - tooltipHeight - edgePadding;
  }
  if (top < edgePadding) {
    top = edgePadding;
  }

  return { left, top };
}

function figureTitleText(layout: Record<string, unknown>): string {
  const title = layout.title;
  if (typeof title === "string") {
    return title;
  }
  if (title && typeof title === "object") {
    const text = (title as { text?: unknown }).text;
    if (typeof text === "string") {
      return text;
    }
  }
  return "";
}

function ensureFigureUiRevision(
  figure: Record<string, unknown> | undefined,
  revisionScope: string,
): Record<string, unknown> | undefined {
  if (!figure) {
    return figure;
  }
  const sourceLayout =
    typeof (figure as FigureShape).layout === "object" && (figure as FigureShape).layout
      ? ((figure as FigureShape).layout as Record<string, unknown>)
      : {};
  if (typeof sourceLayout.uirevision !== "undefined") {
    return figure;
  }
  const traceTokens = Array.isArray((figure as FigureShape).data)
    ? ((figure as FigureShape).data as Array<Record<string, unknown>>).map((trace, index) => {
        const traceType = typeof trace.type === "string" ? trace.type : `trace${index}`;
        const traceName = typeof trace.name === "string" ? trace.name : "";
        return `${traceType}:${traceName}`;
      })
    : ["no-data"];
  const title = figureTitleText(sourceLayout);
  return {
    ...(figure as FigureShape),
    layout: {
      ...sourceLayout,
      uirevision: [revisionScope, title, ...traceTokens].join("|"),
    },
  };
}

function cloneFigure(figure?: Record<string, unknown>): FigureShape {
  if (!figure) {
    return { data: [], layout: {} };
  }
  if (typeof structuredClone === "function") {
    try {
      const cloned = structuredClone(figure) as FigureShape;
      return {
        ...cloned,
        data: Array.isArray(cloned.data) ? cloned.data : [],
        layout: typeof cloned.layout === "object" && cloned.layout ? cloned.layout : {},
      };
    } catch {
      // Fall back to shallow copy below.
    }
  }
  return {
    ...figure,
    data: Array.isArray(figure.data) ? (figure.data as Array<Record<string, unknown>>).map((trace) => ({ ...trace })) : [],
    layout: typeof figure.layout === "object" && figure.layout ? { ...(figure.layout as Record<string, unknown>) } : {},
  };
}

function compactHoverDiagnosticsFigure(figure: Record<string, unknown> | undefined): Record<string, unknown> | undefined {
  if (!figure) {
    return figure;
  }
  const cloned: FigureShape = {
    ...(figure as FigureShape),
    data: Array.isArray((figure as FigureShape).data)
      ? ((figure as FigureShape).data as Array<Record<string, unknown>>).map((trace) => ({ ...trace }))
      : [],
    layout:
      typeof (figure as FigureShape).layout === "object" && (figure as FigureShape).layout
        ? { ...((figure as FigureShape).layout as Record<string, unknown>) }
        : {},
  };
  cloned.layout = {
    ...cloned.layout,
    title: {
      text: "Cycle Intensities (Sample vs Reference Gas)",
      x: 0.5,
      xanchor: "center",
      font: { size: 14 },
    },
    margin: { l: 42, r: 12, t: 46, b: 126 },
    legend: { orientation: "h", yanchor: "top", y: -0.28, x: 0, xanchor: "left", font: { size: 10 } },
    hovermode: "closest",
    height: 460,
  };
  return ensureFigureUiRevision(cloned, "diagnostics:hover-preview");
}

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return null;
}

function isPartiallySaturatedCollectorStatus(value: unknown): boolean {
  return String(value ?? "").trim().toLowerCase() === "partially saturated collectors";
}

function hasSampleCollectorTrace(trace: Record<string, unknown>, mass: 44 | 45 | 46): boolean {
  const traceName = String(trace.name ?? "").toLowerCase();
  return traceName.includes(String(mass)) && (traceName.includes("smp") || traceName.includes("sample"));
}

function hasReferenceCollectorTrace(trace: Record<string, unknown>, mass: 44 | 45 | 46): boolean {
  const traceName = String(trace.name ?? "").toLowerCase();
  return traceName.includes(String(mass)) && (traceName.includes("ref") || traceName.includes("std") || traceName.includes("reference"));
}

type CycleMarker = {
  cycle: number;
  label: string;
  color: string;
  symbol: string;
  dash: "dot" | "dash";
  textColor: string;
};

function getCycleMarkers(tableRows: Array<Record<string, unknown>>): CycleMarker[] {
  const markers: CycleMarker[] = [];
  const addMarker = (column: string, marker: Omit<CycleMarker, "cycle">) => {
    const selectedRow = tableRows.find((row) => asBoolean(row[column]));
    const cycle = selectedRow ? toFiniteNumber(selectedRow["Cycle"]) : null;
    if (cycle == null) {
      return;
    }
    const existing = markers.find((item) => Math.abs(item.cycle - cycle) <= 0.0001);
    if (existing) {
      existing.label = "First and last valid cycle";
      existing.color = "#0F766E";
      existing.textColor = "#115E59";
      return;
    }
    markers.push({ ...marker, cycle });
  };
  addMarker("First Valid Cycle", {
    label: "First valid cycle",
    color: "#0284C7",
    symbol: "diamond-open",
    dash: "dot",
    textColor: "#075985",
  });
  addMarker("Last Valid Cycle", {
    label: "Last valid cycle",
    color: "#D97706",
    symbol: "square-open",
    dash: "dash",
    textColor: "#92400E",
  });
  return markers;
}

function ensureCollectorIntensityTraces(
  figure: Record<string, unknown> | undefined,
  tableRows: Array<Record<string, unknown>>,
) {
  const cloned = cloneFigure(figure);
  const existingTraceCount = cloned.data.length;
  const traces = [...cloned.data];
  const massColors: Record<44 | 45 | 46, string> = {
    44: "#E67E22",
    45: "#1E7D2B",
    46: "#D4A017",
  };

  for (const mass of [44, 45, 46] as const) {
    const sampleMissing = !traces.some((trace) => hasSampleCollectorTrace(trace, mass));
    const refMissing = !traces.some((trace) => hasReferenceCollectorTrace(trace, mass));
    if (!sampleMissing && !refMissing) {
      continue;
    }
    if (sampleMissing) {
      const intensityCol = `SMP Int m/z ${mass} (V)`;
      const x: number[] = [];
      const y: number[] = [];
      for (const row of tableRows) {
        const cycle = toFiniteNumber(row["Cycle"]);
        const intensity = toFiniteNumber(row[intensityCol]);
        if (cycle == null || intensity == null) {
          continue;
        }
        x.push(cycle);
        y.push(intensity);
      }
      if (x.length) {
        traces.push({
          type: "scatter",
          mode: "lines+markers",
          x,
          y,
          name: `${mass.toFixed(2)} m/z SMP`,
          line: {
            color: massColors[mass],
            width: 2,
            dash: "solid",
          },
          marker: { size: 6 },
        });
      }
    }
    if (refMissing) {
      const intensityCol = `REF Int m/z ${mass} (V)`;
      const legacyStdCol = `STD Int m/z ${mass} (V)`;
      const x: number[] = [];
      const y: number[] = [];
      for (const row of tableRows) {
        const cycle = toFiniteNumber(row["Cycle"]);
        const intensity = toFiniteNumber(row[intensityCol] ?? row[legacyStdCol]);
        if (cycle == null || intensity == null) {
          continue;
        }
        x.push(cycle);
        y.push(intensity);
      }
      if (x.length) {
        traces.push({
          type: "scatter",
          mode: "lines+markers",
          x,
          y,
          name: `${mass.toFixed(2)} m/z REF`,
          line: {
            color: massColors[mass],
            width: 2,
            dash: "dash",
          },
          marker: { size: 6 },
        });
      }
    }
  }

  let hasChanges = traces.length !== existingTraceCount;
  let nextLayout: Record<string, unknown> = cloned.layout;
  const cycleMarkers = getCycleMarkers(tableRows);
  if (cycleMarkers.length) {
    const existingShapes = Array.isArray(cloned.layout.shapes) ? [...(cloned.layout.shapes as Array<Record<string, unknown>>)] : [];
    const existingAnnotations = Array.isArray(cloned.layout.annotations)
      ? [...(cloned.layout.annotations as Array<Record<string, unknown>>)]
      : [];
    for (const cycleMarker of cycleMarkers) {
      const highlightX: number[] = [];
      const highlightY: number[] = [];
      for (const trace of traces) {
        const xVals = coerceVector(trace.x);
        const yVals = coerceVector(trace.y);
        if (!xVals || !yVals || xVals.length !== yVals.length) {
          continue;
        }
        for (let index = 0; index < xVals.length; index += 1) {
          const x = toFiniteNumber(xVals[index]);
          const y = toFiniteNumber(yVals[index]);
          if (x == null || y == null || Math.abs(x - cycleMarker.cycle) > 0.0001) {
            continue;
          }
          highlightX.push(x);
          highlightY.push(y);
        }
      }
      if (highlightX.length) {
        traces.push({
          type: "scatter",
          mode: "markers",
          name: cycleMarker.label,
          x: highlightX,
          y: highlightY,
          marker: {
            size: 11,
            color: cycleMarker.color,
            symbol: cycleMarker.symbol,
            line: { color: cycleMarker.color, width: 2 },
          },
        });
        hasChanges = true;
      }
      existingShapes.push({
        type: "line",
        x0: cycleMarker.cycle,
        x1: cycleMarker.cycle,
        y0: 0,
        y1: 1,
        xref: "x",
        yref: "paper",
        line: { color: cycleMarker.color, width: 2, dash: cycleMarker.dash },
      });
      existingAnnotations.push({
        x: cycleMarker.cycle,
        y: 1,
        xref: "x",
        yref: "paper",
        yanchor: "bottom",
        showarrow: false,
        text: cycleMarker.label,
        font: { color: cycleMarker.textColor, size: 11 },
      });
    }
    nextLayout = {
      ...cloned.layout,
      shapes: existingShapes,
      annotations: existingAnnotations,
    };
    hasChanges = true;
  }

  if (!hasChanges) {
    return cloned;
  }
  return {
    ...cloned,
    data: traces,
    layout: nextLayout,
  };
}

function formatCycleCell(value: unknown): string {
  const numericValue = toFiniteNumber(value);
  if (numericValue != null) {
    return Math.abs(numericValue) >= 1000 ? numericValue.toFixed(2) : numericValue.toFixed(3);
  }
  const text = asString(value).trim();
  if (!text) {
    return "N/A";
  }
  return text.length > 48 ? `${text.slice(0, 45)}...` : text;
}

function coerceVector(values: unknown): unknown[] | null {
  if (Array.isArray(values)) {
    return values;
  }
  if (values && typeof values === "object") {
    const encoded = values as { dtype?: unknown; bdata?: unknown };
    if (typeof encoded.dtype === "string" && typeof encoded.bdata === "string") {
      const decoded = decodeBinaryVector(encoded.dtype, encoded.bdata);
      if (decoded) {
        return decoded;
      }
    }
  }
  if (values && typeof values === "object") {
    const arrayLike = values as unknown as { [index: number]: unknown; length: number };
    if (ArrayBuffer.isView(values)) {
      return Array.from(arrayLike);
    }
    const candidate = values as { length?: unknown };
    if (typeof candidate.length === "number") {
      try {
        return Array.from(arrayLike);
      } catch {
        return null;
      }
    }
  }
  return null;
}

function decodeBinaryVector(dtype: string, bdata: string): number[] | null {
  if (typeof window === "undefined" || typeof window.atob !== "function") {
    return null;
  }
  let binary: string;
  try {
    binary = window.atob(bdata);
  } catch {
    return null;
  }
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  const view = new DataView(bytes.buffer);
  const littleEndian = true;
  const values: number[] = [];
  const pushNumbers = (size: number, reader: (offset: number) => number) => {
    for (let offset = 0; offset + size <= view.byteLength; offset += size) {
      values.push(reader(offset));
    }
  };
  if (dtype === "f8") {
    pushNumbers(8, (offset) => view.getFloat64(offset, littleEndian));
    return values;
  }
  if (dtype === "f4") {
    pushNumbers(4, (offset) => view.getFloat32(offset, littleEndian));
    return values;
  }
  if (dtype === "i4") {
    pushNumbers(4, (offset) => view.getInt32(offset, littleEndian));
    return values;
  }
  if (dtype === "u4") {
    pushNumbers(4, (offset) => view.getUint32(offset, littleEndian));
    return values;
  }
  if (dtype === "i2") {
    pushNumbers(2, (offset) => view.getInt16(offset, littleEndian));
    return values;
  }
  if (dtype === "u2") {
    pushNumbers(2, (offset) => view.getUint16(offset, littleEndian));
    return values;
  }
  if (dtype === "i1") {
    pushNumbers(1, (offset) => view.getInt8(offset));
    return values;
  }
  if (dtype === "u1") {
    pushNumbers(1, (offset) => view.getUint8(offset));
    return values;
  }
  return null;
}

function CycleDiagnosticsTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  if (!rows.length) {
    return <div className="rounded-lg border border-dashed border-stone-300 p-4 text-sm text-stone-500">No cycle rows returned for this point.</div>;
  }

  const statusRows: Array<Record<string, unknown>> = rows.map((row) => {
    const excludedD13 = asBoolean(row["Excluded d13C"]);
    const excludedD18 = asBoolean(row["Excluded d18O"]);
    const excludedSaturation = asBoolean(row["Excluded (Saturation)"]);
    const excludedSampleGasEscape = asBoolean(row["Excluded (Sample Gas Escape)"]);
    const excludedAny = excludedSaturation || excludedSampleGasEscape || excludedD13 || excludedD18;
    const firstValidCycle = asBoolean(row["First Valid Cycle"]);
    const lastValidCycle = asBoolean(row["Last Valid Cycle"]);
    return {
      ...row,
      "Cycle status": excludedSampleGasEscape ? "Sample gas escape" : excludedSaturation ? "Saturated" : excludedAny ? "Excluded" : "Successful",
      "First Valid Cycle": firstValidCycle,
      "Last Valid Cycle": lastValidCycle,
    };
  });

  const preferredColumns = [
    "Cycle",
    "Cycle status",
    "First Valid Cycle",
    "Last Valid Cycle",
    "SMP Int m/z 44 (V)",
    "REF Int m/z 44 (V)",
    "SMP Int m/z 45 (V)",
    "REF Int m/z 45 (V)",
    "SMP Int m/z 46 (V)",
    "REF Int m/z 46 (V)",
    "d13C",
    "d18O",
    "Excluded d13C",
    "Excluded d18O",
    "Excluded (Saturation)",
    "Excluded (Sample Gas Escape)",
  ];
  const discoveredColumns = Object.keys(statusRows[0] ?? {});
  const columns = [
    ...preferredColumns.filter((column) => discoveredColumns.includes(column)),
    ...discoveredColumns.filter((column) => !preferredColumns.includes(column)),
  ];

  function formatCell(value: unknown, column: string): string {
    if (value == null || value === "") {
      return "None";
    }
    if (column === "Cycle status") {
      return String(value);
    }
    if (typeof value === "boolean") {
      return value ? "Yes" : "No";
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      if (isDeltaColumnLabel(column)) {
        return formatDeltaValue(value);
      }
      if (isSignalIntensityColumnLabel(column)) {
        return value.toFixed(2);
      }
      if (Number.isInteger(value)) {
        return String(value);
      }
      return value.toFixed(6);
    }
    return String(value);
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-md bg-sky-100 px-2 py-1 text-sky-800">First valid cycle</span>
        <span className="rounded-md bg-amber-100 px-2 py-1 text-amber-800">Last valid cycle</span>
        <span className="rounded-md bg-emerald-100 px-2 py-1 text-emerald-800">Successful cycle</span>
        <span className="rounded-md bg-rose-100 px-2 py-1 text-rose-800">Saturated cycle</span>
        <span className="rounded-md bg-orange-100 px-2 py-1 text-orange-800">Sample gas escape</span>
      </div>
      <div className="max-h-[560px] overflow-auto rounded-lg border border-stone-200">
        <table className="min-w-full divide-y divide-stone-200 text-left text-sm">
          <thead className="bg-stone-50">
            <tr>
              {columns.map((column) => (
                <th key={column} className="px-3 py-2 font-medium text-stone-700">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-stone-100">
            {statusRows.slice(0, 25).map((row, rowIndex) => {
              const saturated = String(row["Cycle status"]) === "Saturated";
              const sampleGasEscape = String(row["Cycle status"]) === "Sample gas escape";
              const firstValidCycle = asBoolean(row["First Valid Cycle"]);
              const lastValidCycle = asBoolean(row["Last Valid Cycle"]);
              return (
                <tr
                  key={rowIndex}
                  className={cn(
                    firstValidCycle && lastValidCycle
                      ? "bg-teal-100/85"
                      : firstValidCycle
                        ? "bg-sky-100/85"
                        : lastValidCycle
                          ? "bg-amber-100/80"
                          : sampleGasEscape
                            ? "bg-orange-50/85"
                          : saturated
                            ? "bg-rose-50/80"
                            : "bg-emerald-50/70",
                  )}
                >
                  {columns.map((column) => {
                    const cellValue = row[column];
                    const flaggedColumn = column.startsWith("Excluded");
                    const flaggedValue = flaggedColumn ? asBoolean(cellValue) : false;
                    const validCycleColumn = column === "First Valid Cycle" || column === "Last Valid Cycle";
                    const validCycleColumnValue = validCycleColumn ? asBoolean(cellValue) : false;
                    return (
                      <td
                        key={column}
                        className={cn(
                          "px-3 py-2",
                          validCycleColumn
                            ? validCycleColumnValue
                              ? "font-semibold text-stone-900"
                              : "font-medium text-stone-500"
                            : "",
                          flaggedColumn
                            ? flaggedValue
                              ? "font-medium text-rose-700"
                              : "font-medium text-emerald-700"
                            : "text-stone-700",
                        )}
                      >
                        {formatCell(cellValue, column)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length > 25 ? <div className="border-t border-stone-200 px-3 py-2 text-xs text-stone-500">Showing first 25 of {rows.length} rows.</div> : null}
      </div>
    </div>
  );
}

const INLINE_DIAGNOSTIC_UNITS: Record<string, string> = {
  line: "index",
  "signal intensity": "V",
  "d18o values": "per mil VPDB",
  "d13c values": "per mil VPDB",
  "leak rate": "instrument units",
  "total co2": "instrument units",
  "p gasses": "mbar",
  "p no acid": "mbar",
};

function normalizeInlineLabel(label: string): string {
  return label.trim().toLowerCase().replace(/\s+/g, " ");
}

function unitForInlineLabel(label: string): string {
  return INLINE_DIAGNOSTIC_UNITS[normalizeInlineLabel(label)] ?? "";
}

function isDeltaInlineLabel(label: string): boolean {
  const normalized = normalizeInlineLabel(label);
  return normalized === "d13c values" || normalized === "d18o values";
}

function parseStrictNumber(value: string): number | null {
  const normalized = value.trim().replace(/,/g, "");
  if (!/^[-+]?\d+(\.\d+)?$/.test(normalized)) {
    return null;
  }
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseInlineDiagnosticsSummary(summary: string | undefined): Array<{ label: string; value: string }> {
  if (!summary || !summary.trim()) {
    return [];
  }
  const cleanToken = (token: string) => token.replace(/\*\*/g, "").replace(/`/g, "").trim();
  return summary
    .split(/\s+\|\s+/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const markdownMatch = part.match(/^\*\*(.+?)\*\*\s*:\s*`([^`]*)`$/);
      if (markdownMatch) {
        return { label: cleanToken(markdownMatch[1]), value: cleanToken(markdownMatch[2]) };
      }
      const genericMatch = part.match(/^([^:]+)\s*:\s*(.+)$/);
      if (genericMatch) {
        return { label: cleanToken(genericMatch[1]), value: cleanToken(genericMatch[2]) };
      }
      return null;
    })
    .filter((item): item is { label: string; value: string } => item != null);
}

function DiagnosticsPanel({
  title,
  diagnostics,
  loading,
  onPickDeltaValue,
}: {
  title: string;
  diagnostics?: CycleDiagnosticsPayload;
  loading: boolean;
  onPickDeltaValue?: (value: number, stdev?: number | null) => void;
}) {
  const [saturationColorAxis, setSaturationColorAxis] = useState<SaturationColorAxisKey>("mean44");
  const [saturationYAxis, setSaturationYAxis] = useState<SaturationAxisKey>("d13C");
  const cycleMean = diagnostics?.cycle_mean ?? {};
  const validMean = toFiniteNumber(cycleMean.valid_mean);
  const validStdDev = toFiniteNumber(cycleMean.valid_std_dev);
  const validCycleCount = toFiniteNumber(cycleMean.valid_cycles);
  const hasTooFewLinearityCycles = validCycleCount != null && validCycleCount < 4;
  const firstValidCycle = toFiniteNumber(cycleMean.selected_value) ?? toFiniteNumber(cycleMean.mean);
  const lastValidCycle = toFiniteNumber(cycleMean.last_valid_value);
  const referenceGasCorrection = toFiniteNumber(cycleMean.saturation_reference_gas_value);
  const firstCycleCorrection = toFiniteNumber(cycleMean.saturation_first_cycle_value);
  const saturationCorrection =
    diagnostics?.saturation_correction && typeof diagnostics.saturation_correction === "object"
      ? (diagnostics.saturation_correction as Record<string, unknown>)
      : {};
  const cycleLinearityValue = (key: string) => {
    const payload = saturationCorrection[key];
    return payload && typeof payload === "object" ? toFiniteNumber((payload as Record<string, unknown>).value) : null;
  };
  const cycleLinearityStd = (key: string) => {
    const payload = saturationCorrection[key];
    return payload && typeof payload === "object" ? toFiniteNumber((payload as Record<string, unknown>).std_dev) : null;
  };
  const reason = asString(cycleMean.reason);
  const diagnosticsFigure = ensureCollectorIntensityTraces(diagnostics?.figure, diagnostics?.table ?? []);
  const saturationFiguresRaw =
    Object.keys(saturationCorrection).length
      ? (saturationCorrection.figures as Record<string, unknown> | undefined)
      : undefined;
  const targetIsotopeKey = asString((diagnostics?.target ?? {})["isotope_key"]);
  const defaultSaturationYAxis: SaturationAxisKey = targetIsotopeKey === "d18O" ? "d18O" : "d13C";
  useEffect(() => {
    setSaturationYAxis(defaultSaturationYAxis);
  }, [defaultSaturationYAxis]);
  const saturationMethodDescriptions: Record<string, string> = {
    reference_gas_intensity:
      "Fits isotope value versus the reference-gas intensity from valid cycles, then predicts the value at the saturated cycle's reference intensity. Points are colored by cycle number.",
    first_cycle:
      "Fits a quadratic curve of isotope value versus cycle number from valid cycles, then predicts where the curve becomes horizontal.",
    cycle_relative_mismatch:
      "Fits a quadratic curve of isotope value versus (Samp44 - Ref44) / Ref44, then predicts where that curve becomes horizontal.",
    cycle_symmetric_mismatch:
      "Fits a quadratic curve of isotope value versus (Samp44 - Ref44) / ((Samp44 + Ref44) / 2), then predicts where that curve becomes horizontal.",
    cycle_mean_intensity:
      "Fits a quadratic curve of isotope value versus mean intensity, (Samp44 + Ref44) / 2, then predicts where that curve becomes horizontal.",
    cycle_intensity_weighted_mismatch:
      "Fits a quadratic curve of isotope value versus the cycle-level weighted mismatch term, then predicts where that curve becomes horizontal.",
    cycle_two_term_mean_mismatch:
      "Fits isotope value with two predictors: mean intensity and symmetric mismatch. The green line connects the model-fitted values for the valid cycles using each cycle's own mismatch; dot color also shows mismatch.",
    cycle_plateau:
      "Measures the signed cycle-to-cycle isotope change in the latest valid cycles, fits isotope value versus that change rate, then predicts the asymptote where the change rate reaches zero. The highlighted circles are the cycles used.",
  };
  const saturationFigureItems = [
    ["reference_gas_intensity", "Reference-gas saturation correction"],
    ["first_cycle", "Stabilized-cycle correction"],
    ["cycle_relative_mismatch", "Cycle relative mismatch correction"],
    ["cycle_symmetric_mismatch", "Cycle symmetric mismatch correction"],
    ["cycle_mean_intensity", "Cycle mean intensity correction"],
    ["cycle_intensity_weighted_mismatch", "Cycle intensity-weighted mismatch correction"],
    ["cycle_two_term_mean_mismatch", "Cycle two-term mean + mismatch correction"],
    ["cycle_plateau", "Cycle late-plateau correction"],
  ]
    .map(([key, itemTitle]) => ({
      key,
      title: itemTitle,
      description: saturationMethodDescriptions[key],
      figure:
        saturationFiguresRaw?.[key] && typeof saturationFiguresRaw[key] === "object"
          ? (saturationFiguresRaw[key] as Record<string, unknown>)
          : undefined,
    }))
    .filter((item) => item.figure);
  const suggestionCards = [
    { label: "Cycle Mean", value: validMean, stdev: validStdDev, linearity: false },
    { label: "First valid cycle", value: firstValidCycle, stdev: null, linearity: false },
    { label: "Last valid cycle", value: lastValidCycle, stdev: null, linearity: false },
    { label: "Lin. corr. to ref gas int", value: referenceGasCorrection, stdev: null, linearity: true },
    { label: "Lin. corr. to first cycle", value: firstCycleCorrection, stdev: null, linearity: true },
    { label: "Cycle relative mismatch", value: cycleLinearityValue("cycle_relative_mismatch"), stdev: null, linearity: true },
    { label: "Cycle symmetric mismatch", value: cycleLinearityValue("cycle_symmetric_mismatch"), stdev: null, linearity: true },
    { label: "Cycle mean intensity", value: cycleLinearityValue("cycle_mean_intensity"), stdev: null, linearity: true },
    { label: "Cycle weighted mismatch", value: cycleLinearityValue("cycle_intensity_weighted_mismatch"), stdev: null, linearity: true },
    { label: "Cycle two-term model", value: cycleLinearityValue("cycle_two_term_mean_mismatch"), stdev: null, linearity: true },
    { label: "Cycle plateau", value: cycleLinearityValue("cycle_plateau"), stdev: cycleLinearityStd("cycle_plateau"), linearity: false },
  ];

  return (
    <Card className="border-stone-300">
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>Cycle-level intensity and exclusion diagnostics for the active sample.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? <div className="text-sm text-stone-500">Loading cycle diagnostics...</div> : null}

        {diagnostics ? (
          <>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
              {suggestionCards.map((item) => {
                const blockedByLinearityCycleCount = item.linearity && hasTooFewLinearityCycles && item.value != null;
                const canPick = typeof onPickDeltaValue === "function" && item.value != null && !blockedByLinearityCycleCount;
                const displayValue = formatDeltaValue(item.value);
                const valueElement = (
                  <span
                    className={cn(
                      "inline-block",
                      blockedByLinearityCycleCount ? "cursor-help text-stone-400" : "text-stone-900",
                    )}
                  >
                    {displayValue}
                  </span>
                );
                return (
                  <button
                    key={item.label}
                    type="button"
                    onClick={() => {
                      if (canPick && item.value != null) {
                        onPickDeltaValue(item.value, item.stdev ?? null);
                      }
                    }}
                    disabled={item.value == null}
                    aria-disabled={!canPick}
                    className={cn(
                      "rounded-lg border border-stone-200 p-3 text-left transition",
                      canPick ? "cursor-pointer hover:border-fuchsia-400 hover:bg-fuchsia-50" : "",
                      blockedByLinearityCycleCount ? "cursor-help bg-stone-50/70" : "",
                    )}
                  >
                    <div className="text-xs uppercase tracking-normal text-stone-500">{item.label}</div>
                    <div className="mt-1 text-lg font-semibold">
                      {blockedByLinearityCycleCount ? (
                        <Tooltip label="not enough cycles for linearity calculation" align="start">
                          {valueElement}
                        </Tooltip>
                      ) : (
                        valueElement
                      )}
                    </div>
                    {item.stdev != null ? (
                      <div className="mt-1 text-xs text-stone-500">Std dev: {formatDeltaValue(item.stdev)}</div>
                    ) : null}
                  </button>
                );
              })}
              <div className="rounded-lg border border-stone-200 p-3">
                <div className="text-xs uppercase tracking-normal text-stone-500">Method</div>
                <div className="mt-1 text-sm font-medium text-stone-900">{asString(cycleMean.method) || "N/A"}</div>
              </div>
            </div>

            {reason ? <div className="text-sm text-stone-500">Diagnostics note: {reason}</div> : null}

            <div className="grid gap-4 xl:grid-cols-2 xl:items-start">
              <PlotlyChart
                figure={diagnosticsFigure}
                className="mx-auto aspect-square min-h-[320px] w-full max-w-[560px]"
                deferRenderMs={SELECTION_EDITOR_CHART_DEFER_MS}
              />
              <div className="min-w-0">
                <SharedCycleDiagnosticsTable rows={diagnostics.table ?? []} />
              </div>
            </div>

            {saturationFigureItems.length ? (
              <>
                <div className="flex flex-wrap items-end gap-4">
                  <label className="block w-full max-w-xs text-sm">
                    <SaturationAxisHelpTooltip label="Chart color axis" />
                    <select
                      value={saturationColorAxis}
                      onChange={(event) => setSaturationColorAxis(event.target.value as SaturationColorAxisKey)}
                      className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                    >
                      {SATURATION_COLOR_AXIS_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="block w-full max-w-xs text-sm">
                    <SaturationAxisHelpTooltip label="Chart y axis" />
                    <select
                      value={saturationYAxis}
                      onChange={(event) => setSaturationYAxis(event.target.value as SaturationAxisKey)}
                      className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                    >
                      {SATURATION_COLOR_AXIS_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <SaturationSharedColorbar figures={saturationFigureItems.map((item) => item.figure)} colorAxis={saturationColorAxis} />
                </div>
                <div className="grid gap-4 xl:grid-cols-2">
                  {saturationFigureItems.map((item) => (
                    <SaturationFigureCard
                      key={item.key}
                      chartKey={item.key}
                      title={item.title}
                      description={item.description}
                      figure={item.figure}
                      colorAxis={saturationColorAxis}
                      yAxis={saturationYAxis}
                      deferRenderMs={SELECTION_EDITOR_CHART_DEFER_MS}
                    />
                  ))}
                </div>
              </>
            ) : null}
          </>
        ) : loading ? null : (
          <div className="text-sm text-stone-500">Cycle diagnostics appear here once a point is selected.</div>
        )}
      </CardContent>
    </Card>
  );
}

function coerceIndexedObjectToArray(value: unknown): unknown[] | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const numericKeys = Object.keys(record)
    .filter((key) => /^\d+$/.test(key))
    .map((key) => Number(key))
    .sort((left, right) => left - right);
  if (!numericKeys.length || !numericKeys.every((key, index) => key === index)) {
    return null;
  }
  return numericKeys.map((key) => record[String(key)]);
}

function coercePointCustomDataArray(value: unknown): unknown[] | null {
  if (Array.isArray(value)) {
    return value;
  }
  return coerceVector(value) ?? coerceIndexedObjectToArray(value);
}

function hasPointCustomDataPayload(value: unknown): boolean {
  const customArray = coercePointCustomDataArray(value);
  if (customArray && customArray.length > 0) {
    return true;
  }
  if (value && typeof value === "object") {
    const payload = value as Record<string, unknown>;
    return "row_label" in payload || "rowLabel" in payload || "0" in payload;
  }
  return false;
}

function extractPointCustomData(point: PlotlyPoint): unknown {
  if (point.customdata != null && hasPointCustomDataPayload(point.customdata)) {
    return point.customdata;
  }
  const payload = point as unknown as Record<string, unknown>;
  const pointNumberRaw = payload.pointNumber ?? payload.pointIndex;
  const pointNumber =
    typeof pointNumberRaw === "number"
      ? pointNumberRaw
      : Array.isArray(pointNumberRaw) && typeof pointNumberRaw[0] === "number"
        ? pointNumberRaw[0]
        : null;
  if (pointNumber == null) {
    return null;
  }
  const dataCandidate = (payload.data ?? payload.fullData) as Record<string, unknown> | undefined;
  const traceCustomdata = coercePointCustomDataArray(dataCandidate?.customdata);
  if (!traceCustomdata || pointNumber < 0 || pointNumber >= traceCustomdata.length) {
    return null;
  }
  return traceCustomdata[pointNumber];
}

function traceAxisLayoutKey(axisRef: unknown, axis: "x" | "y"): string {
  const ref = String(axisRef ?? axis).trim().toLowerCase();
  if (ref === axis || ref === "") {
    return `${axis}axis`;
  }
  const suffix = ref.replace(axis, "");
  return `${axis}axis${suffix}`;
}

function isotopeKeyFromAxisTitle(value: unknown): IsotopeKey | null {
  const label = titleText(value).toLowerCase();
  if (label.includes("d18") || label.includes("18o")) {
    return "d18O";
  }
  if (label.includes("d13") || label.includes("13c")) {
    return "d13C";
  }
  return null;
}

function inferDiagnosticsIsotopeFromPoint(
  point: PlotlyPoint | undefined,
  figure: Record<string, unknown> | undefined,
): IsotopeKey {
  if (!point || !figure) {
    return "d13C";
  }
  const traces = Array.isArray((figure as FigureShape).data) ? ((figure as FigureShape).data as Array<Record<string, unknown>>) : [];
  const traceIndex = typeof point.curveNumber === "number" ? point.curveNumber : -1;
  const trace = traceIndex >= 0 && traceIndex < traces.length ? traces[traceIndex] : null;
  const layout =
    typeof (figure as FigureShape).layout === "object" && (figure as FigureShape).layout
      ? ((figure as FigureShape).layout as Record<string, unknown>)
      : {};
  if (trace) {
    const yAxisKey = traceAxisLayoutKey(trace.yaxis, "y");
    const yAxis = layout[yAxisKey];
    const yIsotope = yAxis && typeof yAxis === "object" ? isotopeKeyFromAxisTitle((yAxis as Record<string, unknown>).title) : null;
    if (yIsotope) {
      return yIsotope;
    }

    const xAxisKey = traceAxisLayoutKey(trace.xaxis, "x");
    const xAxis = layout[xAxisKey];
    const xIsotope = xAxis && typeof xAxis === "object" ? isotopeKeyFromAxisTitle((xAxis as Record<string, unknown>).title) : null;
    if (xIsotope) {
      return xIsotope;
    }
  }
  return "d13C";
}

function parseDiagnosticsSelectedTargets(points: PlotlyPoint[]): StoredSelectedTarget[] {
  const targets: StoredSelectedTarget[] = [];
  const seen = new Set<string>();
  for (const point of points) {
    const rawCustomdata = extractPointCustomData(point);
    const customdata = coercePointCustomDataArray(rawCustomdata);
    const customObj = rawCustomdata && typeof rawCustomdata === "object" ? (rawCustomdata as Record<string, unknown>) : null;
    const scalarRowLabel =
      typeof rawCustomdata === "string" || typeof rawCustomdata === "number" ? String(rawCustomdata).trim() : "";
    const rowLabel = String(customdata?.[0] ?? customObj?.row_label ?? customObj?.rowLabel ?? scalarRowLabel ?? "").trim();
    if (!rowLabel || seen.has(rowLabel)) {
      continue;
    }
    seen.add(rowLabel);
    targets.push({
      rowLabel,
      isotopeKey: "cross",
      identifier1: String(customdata?.[1] ?? customObj?.identifier_1 ?? customObj?.identifier1 ?? "").trim(),
      identifier2: String(customdata?.[2] ?? customObj?.identifier_2 ?? customObj?.identifier2 ?? "").trim(),
      currentValue: null,
      currentD13: null,
      currentD18: null,
      chartKey: "diagnostics",
    });
  }
  return targets;
}

function collectNumericColorValues(figure?: Record<string, unknown>): number[] {
  if (!figure) {
    return [];
  }
  const traces = Array.isArray((figure as FigureShape).data) ? ((figure as FigureShape).data as Array<Record<string, unknown>>) : [];
  const values: number[] = [];
  for (const trace of traces) {
    const marker = trace.marker && typeof trace.marker === "object" ? (trace.marker as Record<string, unknown>) : null;
    if (!marker) {
      continue;
    }
    const colorVector = coerceVector(marker.color);
    if (!colorVector) {
      continue;
    }
    for (const item of colorVector) {
      const numericValue = toFiniteNumber(item);
      if (numericValue != null) {
        values.push(numericValue);
      }
    }
  }
  return values;
}

function deriveColorScaleBounds(figure?: Record<string, unknown>): ColorScaleBounds | null {
  if (!figure) {
    return null;
  }
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const value of collectNumericColorValues(figure)) {
    min = Math.min(min, value);
    max = Math.max(max, value);
  }
  const traces = Array.isArray((figure as FigureShape).data) ? ((figure as FigureShape).data as Array<Record<string, unknown>>) : [];
  for (const trace of traces) {
    const marker = trace.marker && typeof trace.marker === "object" ? (trace.marker as Record<string, unknown>) : null;
    const markerMin = marker ? toFiniteNumber(marker.cmin) : null;
    const markerMax = marker ? toFiniteNumber(marker.cmax) : null;
    if (markerMin != null) {
      min = Math.min(min, markerMin);
    }
    if (markerMax != null) {
      max = Math.max(max, markerMax);
    }
  }
  const layout = (figure as FigureShape).layout ?? {};
  for (const key of Object.keys(layout)) {
    if (!key.toLowerCase().startsWith("coloraxis")) {
      continue;
    }
    const axis = layout[key];
    if (!axis || typeof axis !== "object") {
      continue;
    }
    const axisMin = toFiniteNumber((axis as Record<string, unknown>).cmin);
    const axisMax = toFiniteNumber((axis as Record<string, unknown>).cmax);
    if (axisMin != null) {
      min = Math.min(min, axisMin);
    }
    if (axisMax != null) {
      max = Math.max(max, axisMax);
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return null;
  }
  if (min === max) {
    const pad = Math.max(Math.abs(min) * 0.01, 0.001);
    return { min: min - pad, max: max + pad };
  }
  return { min, max };
}

function normalizeColorScaleRange(range: [number, number], bounds: ColorScaleBounds): [number, number] {
  const low = clampNumber(Math.min(range[0], range[1]), bounds.min, bounds.max);
  const high = clampNumber(Math.max(range[0], range[1]), bounds.min, bounds.max);
  return [Math.min(low, high), Math.max(low, high)];
}

function sliderPrecision(bounds: ColorScaleBounds): number {
  const span = Math.abs(bounds.max - bounds.min);
  if (span >= 1000) {
    return 0;
  }
  if (span >= 100) {
    return 1;
  }
  if (span >= 10) {
    return 2;
  }
  return 3;
}

function sliderStep(bounds: ColorScaleBounds): number {
  const span = Math.abs(bounds.max - bounds.min);
  if (!Number.isFinite(span) || span <= 0) {
    return 0.001;
  }
  const step = span / 400;
  if (step >= 1) {
    return Math.round(step);
  }
  if (step >= 0.1) {
    return Number(step.toFixed(2));
  }
  if (step >= 0.01) {
    return Number(step.toFixed(3));
  }
  return Number(step.toFixed(4));
}

function applyColorScaleRangeToFigure(
  figure: Record<string, unknown> | undefined,
  range: [number, number],
  revisionScope = "diagnostics:matrix",
): Record<string, unknown> | undefined {
  if (!figure) {
    return figure;
  }
  const [cmin, cmax] = [Math.min(range[0], range[1]), Math.max(range[0], range[1])];
  const cloned: FigureShape = {
    ...(figure as FigureShape),
    data: Array.isArray((figure as FigureShape).data)
      ? ((figure as FigureShape).data as Array<Record<string, unknown>>).map((trace) => ({ ...trace }))
      : [],
    layout: typeof (figure as FigureShape).layout === "object" && (figure as FigureShape).layout
      ? { ...((figure as FigureShape).layout as Record<string, unknown>) }
      : {},
  };
  let hasColorMapping = false;
  cloned.data = cloned.data.map((trace) => {
    const marker = trace.marker && typeof trace.marker === "object" ? (trace.marker as Record<string, unknown>) : null;
    if (!marker) {
      return trace;
    }
    const colorVector = coerceVector(marker.color);
    const hasNumericVector = Boolean(colorVector && colorVector.some((value) => toFiniteNumber(value) != null));
    const hasNumericBounds = toFiniteNumber(marker.cmin) != null || toFiniteNumber(marker.cmax) != null;
    if (!hasNumericVector && !hasNumericBounds) {
      return trace;
    }
    hasColorMapping = true;
    return {
      ...trace,
      marker: {
        ...marker,
        cauto: false,
        cmin,
        cmax,
      },
    };
  });
  for (const key of Object.keys(cloned.layout)) {
    if (!key.toLowerCase().startsWith("coloraxis")) {
      continue;
    }
    const axis = cloned.layout[key];
    if (!axis || typeof axis !== "object") {
      continue;
    }
    hasColorMapping = true;
    cloned.layout[key] = {
      ...(axis as Record<string, unknown>),
      cauto: false,
      cmin,
      cmax,
    };
  }
  return ensureFigureUiRevision(hasColorMapping ? cloned : figure, revisionScope);
}

function resolveFigureHeight(figure: unknown, fallback: number): number {
  if (!figure || typeof figure !== "object") {
    return fallback;
  }
  const layout = (figure as { layout?: unknown }).layout;
  if (!layout || typeof layout !== "object") {
    return fallback;
  }
  const rawHeight = Number((layout as { height?: unknown }).height);
  if (!Number.isFinite(rawHeight) || rawHeight < 200) {
    return fallback;
  }
  return rawHeight;
}

function RangeSliderControl({
  label,
  bounds,
  value,
  step = 0.001,
  precision = 3,
  onChange,
}: {
  label: string;
  bounds: [number, number] | null;
  value: [number, number] | null;
  step?: number;
  precision?: number;
  onChange: (nextRange: [number, number]) => void;
}) {
  const fallbackBounds: [number, number] = bounds ?? value ?? [0, 1];
  const minBound = Math.min(fallbackBounds[0], fallbackBounds[1], value?.[0] ?? fallbackBounds[0], value?.[1] ?? fallbackBounds[1]);
  const maxBound = Math.max(fallbackBounds[0], fallbackBounds[1], value?.[0] ?? fallbackBounds[0], value?.[1] ?? fallbackBounds[1]);
  const low = clampNumber(Math.min(value?.[0] ?? minBound, value?.[1] ?? maxBound), minBound, maxBound);
  const high = clampNumber(Math.max(value?.[0] ?? minBound, value?.[1] ?? maxBound), minBound, maxBound);

  return (
    <div className="rounded-lg border border-stone-200 bg-white/80 p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-medium tracking-normal text-stone-700">{label}</div>
        <div className="text-xs text-stone-500">
          {low.toFixed(precision)} to {high.toFixed(precision)}
        </div>
      </div>
      <div className="mt-3 space-y-2">
        <label className="block text-xs text-stone-600">
          Min
          <input
            type="range"
            min={minBound}
            max={maxBound}
            step={step}
            value={low}
            onInput={(event) => {
              const nextLow = parseFinite(event.currentTarget.value, low);
              onChange([Math.min(nextLow, high), high]);
            }}
            className="mt-1.5 w-full accent-stone-900"
          />
        </label>
        <label className="block text-xs text-stone-600">
          Max
          <input
            type="range"
            min={minBound}
            max={maxBound}
            step={step}
            value={high}
            onInput={(event) => {
              const nextHigh = parseFinite(event.currentTarget.value, high);
              onChange([low, Math.max(nextHigh, low)]);
            }}
            className="mt-1.5 w-full accent-stone-900"
          />
        </label>
      </div>
      {bounds ? (
        <div className="mt-2 text-xs text-stone-500">
          Data bounds: {bounds[0].toFixed(precision)} to {bounds[1].toFixed(precision)}
        </div>
      ) : null}
    </div>
  );
}

export default function DiagnosticsPage() {
  const sessionId = useSessionStore((state) => state.sessionId);
  const queryClient = useQueryClient();
  const [colorParam, setColorParam] = useState("Date");
  const [identifierFilter, setIdentifierFilter] = useState<string[]>([]);
  const [d13Range, setD13Range] = useState<[number, number] | null>(null);
  const [d18Range, setD18Range] = useState<[number, number] | null>(null);
  const [appliedD13Range, setAppliedD13Range] = useState<[number, number] | null>(null);
  const [appliedD18Range, setAppliedD18Range] = useState<[number, number] | null>(null);
  const [colorScaleRange, setColorScaleRange] = useState<[number, number] | null>(null);
  const [colorScaleRangeParam, setColorScaleRangeParam] = useState<string | null>(null);
  const [sharedLinearityConfig, setSharedLinearityConfig] = useState<CalibrationConfig["linearity"] | null>(null);
  const [selectionTarget, setSelectionTarget] = useState<StoredSelectedTarget | null>(null);
  const [isSelectionEditorOpen, setSelectionEditorOpen] = useState(false);
  const [selectionEditorTab, setSelectionEditorTab] = useState<IsotopeKey>("d13C");
  const [singleValues, setSingleValues] = useState<IsotopeNumericMap>({ d13C: 0, d18O: 0 });
  const [singleStdevs, setSingleStdevs] = useState<Record<IsotopeKey, number | null>>({ d13C: null, d18O: null });
  const [singleOffsets, setSingleOffsets] = useState<IsotopeNumericMap>({ d13C: 0, d18O: 0 });
  const [hoverPreview, setHoverPreview] = useState<HoverPreviewState | null>(null);
  const hoverPreviewHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hoverPreviewShowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingHoverPreviewRef = useRef<HoverPreviewState | null>(null);
  const hoverPreviewTarget = hoverPreview?.target ?? null;
  const hoverPreviewDiagnosticsTarget =
    hoverPreviewTarget == null
      ? null
      : {
          rowLabel: hoverPreviewTarget.rowLabel,
          isotopeKey: (hoverPreviewTarget.isotopeKey === "cross" ? "d13C" : hoverPreviewTarget.isotopeKey) as "d13C" | "d18O",
        };

  const { data, error } = useQuery({
    queryKey: [
      "diagnostics",
      sessionId,
      colorParam,
      identifierFilter.join("|"),
      appliedD13Range ? `${appliedD13Range[0]}:${appliedD13Range[1]}` : "",
      appliedD18Range ? `${appliedD18Range[0]}:${appliedD18Range[1]}` : "",
    ],
    queryFn: () =>
      api.getDiagnostics(sessionId!, {
        color_param: colorParam,
        identifier_filter: identifierFilter,
        d13_range: appliedD13Range,
        d18_range: appliedD18Range,
      }),
    enabled: Boolean(sessionId),
  });
  const calibrationWorkspaceQuery = useQuery({
    queryKey: ["calibration-workspace", sessionId],
    queryFn: () => api.getCalibrationWorkspace(sessionId!),
    enabled: Boolean(sessionId),
  });
  const saveSharedLinearityMutation = useMutation({
    mutationFn: (nextLinearity: CalibrationConfig["linearity"]) =>
      api.setCalibrationLinearity(
        sessionId!,
        nextLinearity,
        calibrationWorkspaceQuery.data?.config?.selected_standards ?? [],
      ),
    onSuccess: async (workspace) => {
      queryClient.setQueryData(["calibration-workspace", sessionId], workspace);
      setSharedLinearityConfig(workspace.config.linearity);
      await queryClient.invalidateQueries({ queryKey: ["diagnostics", sessionId] });
    },
  });
  const selectionRowLabel = selectionTarget?.rowLabel ?? null;
  const sampleD13DiagnosticsQuery = useQuery({
    queryKey: ["diagnostics-selection-cycle", sessionId, selectionRowLabel, "d13C"],
    queryFn: () =>
      api.getProcessingCycleDiagnostics(sessionId!, {
        target: {
          row_label: selectionRowLabel!,
          isotope_key: "d13C",
        },
      }),
    enabled: Boolean(sessionId && isSelectionEditorOpen && selectionRowLabel),
  });
  const sampleD18DiagnosticsQuery = useQuery({
    queryKey: ["diagnostics-selection-cycle", sessionId, selectionRowLabel, "d18O"],
    queryFn: () =>
      api.getProcessingCycleDiagnostics(sessionId!, {
        target: {
          row_label: selectionRowLabel!,
          isotope_key: "d18O",
        },
      }),
    enabled: Boolean(sessionId && isSelectionEditorOpen && selectionRowLabel),
  });
  const hoverDiagnosticsQuery = useQuery({
    queryKey: [
      "diagnostics-hover-cycle",
      sessionId,
      hoverPreviewDiagnosticsTarget?.rowLabel,
      hoverPreviewDiagnosticsTarget?.isotopeKey,
    ],
    queryFn: () =>
      api.getProcessingCycleDiagnostics(sessionId!, {
        target: {
          row_label: hoverPreviewDiagnosticsTarget!.rowLabel,
          isotope_key: hoverPreviewDiagnosticsTarget!.isotopeKey,
        },
      }),
    enabled: Boolean(sessionId && hoverPreviewDiagnosticsTarget && !isSelectionEditorOpen),
    staleTime: 60_000,
  });
  const editMutation = useMutation({
    mutationFn: (payload: EditAction) => api.editProcessing(sessionId!, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["diagnostics", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["diagnostics-selection-cycle", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-workspace", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-diagnostics", sessionId] });
    },
  });

  const summary = useMemo(() => (data?.summary ?? {}) as Record<string, unknown>, [data?.summary]);
  const availableColorParams = useMemo(
    () => asStringArray(summary.available_color_params).filter((value) => value !== "Date_ordinal"),
    [summary.available_color_params],
  );
  const availableIdentifiers = useMemo(() => asStringArray(summary.available_identifiers), [summary.available_identifiers]);
  const d13Bounds = useMemo(() => asRange(summary.d13_bounds), [summary.d13_bounds]);
  const d18Bounds = useMemo(() => asRange(summary.d18_bounds), [summary.d18_bounds]);
  const diagnosticsFigure = data?.figures?.diagnostics as Record<string, unknown> | undefined;
  const colorScaleBounds = useMemo(() => deriveColorScaleBounds(diagnosticsFigure), [diagnosticsFigure]);
  const colorSliderBounds: ColorScaleBounds = colorScaleBounds ?? { min: 0, max: 1 };
  const effectiveColorScaleRange = normalizeColorScaleRange(
    colorScaleRange ?? [colorSliderBounds.min, colorSliderBounds.max],
    colorSliderBounds,
  );
  const displayedDiagnosticsFigure = useMemo(
    () => applyColorScaleRangeToFigure(diagnosticsFigure, effectiveColorScaleRange, "diagnostics:matrix"),
    [diagnosticsFigure, effectiveColorScaleRange],
  );
  const diagnosticsMatrixHeight = useMemo(() => resolveFigureHeight(data?.figures?.diagnostics, 2600), [data?.figures?.diagnostics]);
  const activeLinearity = sharedLinearityConfig ?? calibrationWorkspaceQuery.data?.config?.linearity ?? null;
  const selectedLinearityIntensityCol = activeLinearity
    ? LINEARITY_INTENSITY_OPTIONS.includes(activeLinearity.intensity_col as (typeof LINEARITY_INTENSITY_OPTIONS)[number])
      ? activeLinearity.intensity_col
      : activeLinearity.use_diff_intensity
        ? LINEARITY_INTENSITY_DIFF44
        : LINEARITY_INTENSITY_SAMP44
    : LINEARITY_INTENSITY_SAMP44;
  const selectedLinearityCycleIntensityAggregation = LINEARITY_CYCLE_INTENSITY_AGGREGATION_OPTIONS.some(
    (option) => option.value === activeLinearity?.cycle_intensity_aggregation,
  )
    ? (activeLinearity?.cycle_intensity_aggregation as LinearityCycleIntensityAggregation)
    : "run_median";
  const selectedLinearityBasisLabel = `${getLinearityIntensityOptionLabel(selectedLinearityIntensityCol)} · ${getLinearityCycleAggregationLabel(
    selectedLinearityCycleIntensityAggregation,
  )}`;
  const isTwoTermLinearityBasis = selectedLinearityIntensityCol === LINEARITY_INTENSITY_TWO_TERM44;
  const showSecondaryCoefficientOffset = Boolean(activeLinearity?.quadratic) || isTwoTermLinearityBasis;
  const d13Fit = (calibrationWorkspaceQuery.data?.linearity_fits?.d13C ?? {}) as Record<string, unknown>;
  const d18Fit = (calibrationWorkspaceQuery.data?.linearity_fits?.d18O ?? {}) as Record<string, unknown>;
  const d13FitSlope = toFiniteNumber(d13Fit.slope);
  const d18FitSlope = toFiniteNumber(d18Fit.slope);
  const d13FitQuad = toFiniteNumber(d13Fit.quad);
  const d18FitQuad = toFiniteNumber(d18Fit.quad);
  const selectedStandards = calibrationWorkspaceQuery.data?.config.selected_standards ?? [];
  const standardPrecisionRows = (calibrationWorkspaceQuery.data?.precision_summaries ?? [])
    .filter((item: CalibrationPrecisionSummary) => selectedStandards.includes(item.standard))
    .sort((left: CalibrationPrecisionSummary, right: CalibrationPrecisionSummary) => left.standard.localeCompare(right.standard));
  const coefficientOffsetEnabled = activeLinearity
    ? [
        Number(activeLinearity.manual_d13_per_10v ?? 0),
        Number(activeLinearity.manual_d18_per_10v ?? 0),
        ...(activeLinearity.quadratic || isTwoTermLinearityBasis
          ? [Number(activeLinearity.manual_d13_per_10v2 ?? 0), Number(activeLinearity.manual_d18_per_10v2 ?? 0)]
          : []),
      ].some((offset) => Number.isFinite(offset) && Math.abs(offset) > 1e-12)
    : false;
  const activeSelectionDiagnostics: CycleDiagnosticsPayload | undefined =
    selectionEditorTab === "d13C" ? sampleD13DiagnosticsQuery.data : sampleD18DiagnosticsQuery.data;
  const activeSelectionTargetPayload = (activeSelectionDiagnostics?.target ?? {}) as Record<string, unknown>;
  const activeSelectionCycleMean = (activeSelectionDiagnostics?.cycle_mean ?? {}) as Record<string, unknown>;
  const activeSelectionCurrentValue = toFiniteNumber(activeSelectionTargetPayload.current_value);
  const activeSelectionCycleMeanValue = toFiniteNumber(activeSelectionCycleMean.valid_mean);
  const activeSelectionFirstValidCycleValue = toFiniteNumber(activeSelectionCycleMean.selected_value) ?? toFiniteNumber(activeSelectionCycleMean.mean);
  const activeSelectionMethod = asString(activeSelectionCycleMean.method) || "N/A";
  const activeSelectionStatus = asString(activeSelectionTargetPayload.collector_status) || "N/A";
  const activeSelectionLoading = selectionEditorTab === "d13C" ? sampleD13DiagnosticsQuery.isLoading : sampleD18DiagnosticsQuery.isLoading;
  const effectiveOutlier = typeof activeSelectionTargetPayload.effective_outlier === "boolean"
    ? (activeSelectionTargetPayload.effective_outlier as boolean)
    : false;
  const busy = saveSharedLinearityMutation.isPending || editMutation.isPending;
  const hoverPreviewPosition = hoverPreview ? computeHoverPreviewPosition(hoverPreview.clientX, hoverPreview.clientY, 560, 560) : null;
  const hoverDiagnosticsFigure = compactHoverDiagnosticsFigure(
    ensureCollectorIntensityTraces(hoverDiagnosticsQuery.data?.figure, hoverDiagnosticsQuery.data?.table ?? []),
  );
  const hasHoverDiagnosticsFigureData = Boolean(
    hoverDiagnosticsFigure &&
      Array.isArray((hoverDiagnosticsFigure as FigureShape).data) &&
      ((hoverDiagnosticsFigure as FigureShape).data as Array<Record<string, unknown>>).length > 0,
  );
  const shouldShowHoverPreview = Boolean(hoverPreview) && !isSelectionEditorOpen && hoverPreviewPosition != null;
  const activeTargetDiagnostics = (sampleD18DiagnosticsQuery.data ?? sampleD13DiagnosticsQuery.data) ?? null;
  const activeTargetInlineSummary = activeTargetDiagnostics?.inline_summary;
  const activeTargetSourceExcel = asString(activeTargetDiagnostics?.target?.source_excel).trim() || "Unknown";
  const activeTargetInlineRawItems = parseInlineDiagnosticsSummary(activeTargetInlineSummary);
  const hasInlineExcelProvenance = activeTargetInlineRawItems.some((item) => {
    const normalized = normalizeInlineLabel(item.label);
    return normalized === "excel file" || normalized === "original excel file" || normalized === "source excel";
  });
  const activeTargetInlineItems = hasInlineExcelProvenance
    ? activeTargetInlineRawItems
    : [...activeTargetInlineRawItems, { label: "Original Excel File", value: activeTargetSourceExcel }];
  const activeTargetInlineDisplayItems = activeTargetInlineItems.map((item) => {
    const numericValue = parseStrictNumber(item.value);
    const isDelta = isDeltaInlineLabel(item.label);
    const normalizedLabel = normalizeInlineLabel(item.label);
    const settableIsotope: IsotopeKey | null =
      normalizedLabel === "d13c values" ? "d13C" : normalizedLabel === "d18o values" ? "d18O" : null;
    const canSetSingleValue = Boolean(selectionTarget) && settableIsotope != null && numericValue != null;
    return {
      ...item,
      unit: unitForInlineLabel(item.label),
      value: isDelta && numericValue != null ? formatDeltaValue(numericValue) : item.value,
      canSetSingleValue,
      setValue: canSetSingleValue && numericValue != null ? roundDeltaValue(numericValue) : null,
      settableIsotope,
    };
  });

  useEffect(() => {
    if (availableColorParams.length && !availableColorParams.includes(colorParam)) {
      setColorParam(availableColorParams[0]);
    }
  }, [availableColorParams, colorParam]);

  useEffect(() => {
    if (calibrationWorkspaceQuery.data?.config?.linearity) {
      setSharedLinearityConfig(calibrationWorkspaceQuery.data.config.linearity);
    }
  }, [calibrationWorkspaceQuery.data]);

  useEffect(() => {
    if (
      !sessionId ||
      !sharedLinearityConfig ||
      !calibrationWorkspaceQuery.data ||
      saveSharedLinearityMutation.isPending
    ) {
      return;
    }
    if (linearityConfigEquals(sharedLinearityConfig, calibrationWorkspaceQuery.data.config.linearity)) {
      return;
    }
    saveSharedLinearityMutation.mutate(sharedLinearityConfig);
  }, [
    calibrationWorkspaceQuery.data,
    saveSharedLinearityMutation,
    saveSharedLinearityMutation.isPending,
    sessionId,
    sharedLinearityConfig,
  ]);

  useEffect(() => {
    if (!colorScaleBounds) {
      return;
    }
    const bounds = colorScaleBounds;
    const parameterChanged = colorScaleRangeParam !== colorParam;
    const fullRange: [number, number] = [bounds.min, bounds.max];
    setColorScaleRange((current) => {
      if (!current || parameterChanged) {
        return fullRange;
      }
      const isOutsideBounds = current[1] < bounds.min || current[0] > bounds.max;
      if (isOutsideBounds) {
        return fullRange;
      }
      const normalized = normalizeColorScaleRange(current, bounds);
      if (normalized[0] === current[0] && normalized[1] === current[1]) {
        return current;
      }
      return normalized;
    });
    if (parameterChanged) {
      setColorScaleRangeParam(colorParam);
    }
  }, [colorParam, colorScaleBounds, colorScaleRangeParam]);

  useEffect(() => {
    return () => {
      if (hoverPreviewHideTimerRef.current != null) {
        clearTimeout(hoverPreviewHideTimerRef.current);
      }
      if (hoverPreviewShowTimerRef.current != null) {
        clearTimeout(hoverPreviewShowTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!isSelectionEditorOpen || typeof window === "undefined") {
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setSelectionEditorOpen(false);
        setSelectionTarget(null);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isSelectionEditorOpen]);

  useEffect(() => {
    if (isSelectionEditorOpen) {
      if (hoverPreviewHideTimerRef.current != null) {
        clearTimeout(hoverPreviewHideTimerRef.current);
        hoverPreviewHideTimerRef.current = null;
      }
      if (hoverPreviewShowTimerRef.current != null) {
        clearTimeout(hoverPreviewShowTimerRef.current);
        hoverPreviewShowTimerRef.current = null;
      }
      pendingHoverPreviewRef.current = null;
      setHoverPreview(null);
    }
  }, [isSelectionEditorOpen]);

  useEffect(() => {
    if (!d13Range && d13Bounds) {
      setD13Range(d13Bounds);
      setAppliedD13Range(d13Bounds);
    }
  }, [d13Bounds, d13Range]);

  useEffect(() => {
    if (!d18Range && d18Bounds) {
      setD18Range(d18Bounds);
      setAppliedD18Range(d18Bounds);
    }
  }, [d18Bounds, d18Range]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setAppliedD13Range(d13Range);
    }, RANGE_FETCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [d13Range]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setAppliedD18Range(d18Range);
    }, RANGE_FETCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [d18Range]);

  useEffect(() => {
    if (!d13Bounds || !d13Range) {
      return;
    }
    const clamped: [number, number] = [
      clampNumber(d13Range[0], d13Bounds[0], d13Bounds[1]),
      clampNumber(d13Range[1], d13Bounds[0], d13Bounds[1]),
    ];
    const normalized: [number, number] = [Math.min(clamped[0], clamped[1]), Math.max(clamped[0], clamped[1])];
    if (normalized[0] !== d13Range[0] || normalized[1] !== d13Range[1]) {
      setD13Range(normalized);
    }
  }, [d13Bounds, d13Range]);

  useEffect(() => {
    if (!d18Bounds || !d18Range) {
      return;
    }
    const clamped: [number, number] = [
      clampNumber(d18Range[0], d18Bounds[0], d18Bounds[1]),
      clampNumber(d18Range[1], d18Bounds[0], d18Bounds[1]),
    ];
    const normalized: [number, number] = [Math.min(clamped[0], clamped[1]), Math.max(clamped[0], clamped[1])];
    if (normalized[0] !== d18Range[0] || normalized[1] !== d18Range[1]) {
      setD18Range(normalized);
    }
  }, [d18Bounds, d18Range]);

  useEffect(() => {
    if (!selectionTarget) {
      return;
    }
    setSelectionEditorTab("d13C");
    setSingleOffsets({ d13C: 0, d18O: 0 });
    setSingleStdevs({ d13C: null, d18O: null });
  }, [selectionTarget?.rowLabel]);

  useEffect(() => {
    if (!selectionTarget) {
      return;
    }
    const d13Target = (sampleD13DiagnosticsQuery.data?.target ?? {}) as Record<string, unknown>;
    const d18Target = (sampleD18DiagnosticsQuery.data?.target ?? {}) as Record<string, unknown>;
    const d13Status = asString(d13Target.collector_status).trim();
    const d18Status = asString(d18Target.collector_status).trim();
    const d13Current = toFiniteNumber(d13Target.current_value);
    const d18Current = toFiniteNumber(d18Target.current_value);
    const d13SelectedCycleValue = toFiniteNumber((sampleD13DiagnosticsQuery.data?.cycle_mean ?? {})["selected_value"]);
    const d18SelectedCycleValue = toFiniteNumber((sampleD18DiagnosticsQuery.data?.cycle_mean ?? {})["selected_value"]);
    const d13SeedValue =
      isPartiallySaturatedCollectorStatus(d13Status) && d13SelectedCycleValue != null ? d13SelectedCycleValue : d13Current;
    const d18SeedValue =
      isPartiallySaturatedCollectorStatus(d18Status) && d18SelectedCycleValue != null ? d18SelectedCycleValue : d18Current;
    setSingleValues({
      d13C: roundDeltaValue(d13SeedValue ?? 0),
      d18O: roundDeltaValue(d18SeedValue ?? 0),
    });
    setSingleStdevs({ d13C: null, d18O: null });
  }, [
    selectionTarget,
    sampleD13DiagnosticsQuery.data?.target,
    sampleD13DiagnosticsQuery.data?.cycle_mean,
    sampleD18DiagnosticsQuery.data?.target,
    sampleD18DiagnosticsQuery.data?.cycle_mean,
  ]);

  function updateSharedLinearity(
    key: keyof CalibrationConfig["linearity"],
    value: boolean | number | string | null,
  ) {
    setSharedLinearityConfig((current) =>
      current
        ? {
            ...current,
            [key]: value,
          }
        : current,
    );
  }

  function updateSharedLinearityIntensityCol(intensityCol: string) {
    setSharedLinearityConfig((current) =>
      current
        ? {
            ...current,
            intensity_col: intensityCol,
            use_diff_intensity: intensityCol === LINEARITY_INTENSITY_DIFF44,
          }
        : current,
    );
  }

  function updateLinearityCoefficientOffset(
    isotopeKey: "d13C" | "d18O",
    term: LinearityCoefficientTerm,
    value: number,
  ) {
    setSharedLinearityConfig((current) => {
      if (!current) {
        return current;
      }
      const next = { ...current };
      if (term === "primary" && isotopeKey === "d13C") {
        next.manual_d13_per_10v = value;
      } else if (term === "primary") {
        next.manual_d18_per_10v = value;
      } else if (isotopeKey === "d13C") {
        next.manual_d13_per_10v2 = value;
      } else {
        next.manual_d18_per_10v2 = value;
      }
      const activeOffsets = [
        Number(next.manual_d13_per_10v ?? 0),
        Number(next.manual_d18_per_10v ?? 0),
        ...(next.quadratic || selectedLinearityIntensityCol === LINEARITY_INTENSITY_TWO_TERM44
          ? [Number(next.manual_d13_per_10v2 ?? 0), Number(next.manual_d18_per_10v2 ?? 0)]
          : []),
      ];
      const hasOffset = activeOffsets.some((offset) => Number.isFinite(offset) && Math.abs(offset) > 1e-12);
      next.manual_override_enabled = hasOffset;
      return next;
    });
  }

  function buildTargetsForAction(isotopeKey?: IsotopeKey): Array<{ row_label: string; isotope_key: IsotopeKey }> {
    if (!selectionTarget) {
      return [];
    }
    if (isotopeKey) {
      return [{ row_label: selectionTarget.rowLabel, isotope_key: isotopeKey }];
    }
    return [
      { row_label: selectionTarget.rowLabel, isotope_key: "d13C" },
      { row_label: selectionTarget.rowLabel, isotope_key: "d18O" },
    ];
  }

  async function applySingleValue(isotopeKey: IsotopeKey) {
    if (!sessionId || !selectionTarget) {
      return;
    }
    await editMutation.mutateAsync({
      action: "set_value",
      targets: buildTargetsForAction(isotopeKey),
      value: singleValues[isotopeKey],
      stdev: singleStdevs[isotopeKey],
    });
  }

  async function applySingleOffset(isotopeKey: IsotopeKey) {
    if (!sessionId || !selectionTarget) {
      return;
    }
    await editMutation.mutateAsync({
      action: "offset",
      targets: buildTargetsForAction(isotopeKey),
      offset: singleOffsets[isotopeKey],
    });
  }

  async function applySingleInterpolate(isotopeKey: IsotopeKey) {
    if (!sessionId || !selectionTarget) {
      return;
    }
    await editMutation.mutateAsync({
      action: "interpolate",
      targets: buildTargetsForAction(isotopeKey),
    });
  }

  async function resetSelected() {
    if (!sessionId || !selectionTarget) {
      return;
    }
    await editMutation.mutateAsync({
      action: "reset_to_original",
      targets: buildTargetsForAction(),
    });
  }

  async function applyOutlierOverride(isOutlier: boolean) {
    if (!sessionId || !selectionTarget) {
      return;
    }
    await editMutation.mutateAsync({
      action: "set_outlier_override",
      targets: buildTargetsForAction(selectionEditorTab),
      is_outlier: isOutlier,
    });
  }

  function clearHoverPreviewHideTimer() {
    if (hoverPreviewHideTimerRef.current != null) {
      clearTimeout(hoverPreviewHideTimerRef.current);
      hoverPreviewHideTimerRef.current = null;
    }
  }

  function clearHoverPreviewShowTimer() {
    if (hoverPreviewShowTimerRef.current != null) {
      clearTimeout(hoverPreviewShowTimerRef.current);
      hoverPreviewShowTimerRef.current = null;
    }
  }

  function scheduleHoverPreviewHide() {
    clearHoverPreviewShowTimer();
    pendingHoverPreviewRef.current = null;
    clearHoverPreviewHideTimer();
    hoverPreviewHideTimerRef.current = setTimeout(() => {
      setHoverPreview(null);
    }, 140);
  }

  function handleDiagnosticsPointHover(payload: PlotlyHoverPayload) {
    if (isSelectionEditorOpen) {
      return;
    }
    const targets = parseDiagnosticsSelectedTargets(payload.points);
    if (!targets.length) {
      clearHoverPreviewShowTimer();
      pendingHoverPreviewRef.current = null;
      setHoverPreview(null);
      return;
    }
    clearHoverPreviewHideTimer();
    const inferredIsotope = inferDiagnosticsIsotopeFromPoint(payload.points[0], displayedDiagnosticsFigure);
    const target = {
      ...targets[0],
      isotopeKey: inferredIsotope,
    };
    pendingHoverPreviewRef.current = {
      target,
      clientX: payload.clientX,
      clientY: payload.clientY,
    };
    clearHoverPreviewShowTimer();
    hoverPreviewShowTimerRef.current = setTimeout(() => {
      const pending = pendingHoverPreviewRef.current;
      if (!pending) {
        return;
      }
      setHoverPreview((current) => {
        if (
          current &&
          current.target.rowLabel === pending.target.rowLabel &&
          current.target.isotopeKey === pending.target.isotopeKey &&
          current.target.chartKey === pending.target.chartKey
        ) {
          return current;
        }
        return pending;
      });
    }, HOVER_PREVIEW_SHOW_DELAY_MS);
  }

  function handleDiagnosticsPointClick(points: PlotlyPoint[]) {
    if (!sessionId) {
      return;
    }
    const targets = parseDiagnosticsSelectedTargets(points);
    if (!targets.length) {
      return;
    }
    setSelectionTarget(targets[0]);
    setSelectionEditorOpen(true);
  }

  function closeSelectionEditor() {
    setSelectionEditorOpen(false);
    setSelectionTarget(null);
  }

  if (!sessionId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>No Active Session</CardTitle>
          <CardDescription>Import data first to unlock diagnostics.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <aside className="space-y-6 xl:sticky xl:top-6 xl:max-h-[calc(100vh-2rem)] xl:self-start xl:overflow-y-auto xl:pr-1">
          <Card>
            <CardHeader>
              <CardTitle>Diagnostics Controls</CardTitle>
              <CardDescription>Configure filters and visual encoding for the diagnostics charts.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="space-y-3">
                <div className="form-section-title">Parameter Selection</div>
                <label className="form-field">
                  <span className="form-label">Choose a parameter to color the dots</span>
                  <select value={colorParam} onChange={(event) => setColorParam(event.target.value)} className="form-control">
                    {(availableColorParams.length ? availableColorParams : [colorParam]).map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
                <RangeSliderControl
                  label="Color scale interval"
                  bounds={[colorSliderBounds.min, colorSliderBounds.max]}
                  value={effectiveColorScaleRange}
                  step={sliderStep(colorSliderBounds)}
                  precision={sliderPrecision(colorSliderBounds)}
                  onChange={(nextRange) => setColorScaleRange(nextRange)}
                />
                <MultiSelectDropdown
                  label="Filter by Identifier 1"
                  options={availableIdentifiers}
                  selected={identifierFilter}
                  onChange={setIdentifierFilter}
                  placeholder="All identifiers"
                />
              </div>

              <div className="space-y-4">
                <div className="form-section-title">Value Ranges</div>
                <RangeSliderControl
                  label="d13C/12C Mean"
                  bounds={d13Bounds}
                  value={d13Range}
                  step={0.001}
                  precision={3}
                  onChange={(nextRange) => setD13Range(nextRange)}
                />
                <RangeSliderControl
                  label="d18O/16O Mean"
                  bounds={d18Bounds}
                  value={d18Range}
                  step={0.001}
                  precision={3}
                  onChange={(nextRange) => setD18Range(nextRange)}
                />
              </div>

              <div className="text-xs font-medium tracking-normal text-stone-500">
                Rows in scope: {Number(summary.row_count_after ?? 0)} / {Number(summary.row_count_before ?? 0)}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Linearity (Shared with Processing and Calibration)</CardTitle>
              <CardDescription>Edits here update the same shared calibration linearity configuration.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="rounded-md bg-stone-100 px-2 py-1 text-xs text-stone-600">Basis: {selectedLinearityBasisLabel}</span>
                {saveSharedLinearityMutation.isPending ? <span className="text-xs text-stone-500">Saving...</span> : null}
              </div>
              {activeLinearity ? (
                <>
                  <label className="flex items-start gap-2 text-sm text-stone-700">
                    <input
                      type="checkbox"
                      className="mt-0.5 h-4 w-4 rounded border-stone-300 text-stone-900"
                      checked={activeLinearity.apply}
                      onChange={(event) => updateSharedLinearity("apply", event.target.checked)}
                    />
                    <span>Enable linearity correction</span>
                  </label>
                  {!isTwoTermLinearityBasis ? (
                    <label className="flex items-start gap-2 text-sm text-stone-700">
                      <input
                        type="checkbox"
                        className="mt-0.5 h-4 w-4 rounded border-stone-300 text-stone-900"
                        checked={Boolean(activeLinearity.quadratic)}
                        onChange={(event) => updateSharedLinearity("quadratic", event.target.checked)}
                      />
                      <span>Use quadratic linearity relationship</span>
                    </label>
                  ) : null}
                  <label className="text-sm">
                    <span className="mb-1 block text-stone-700">Linearity basis</span>
                      <select
                        value={selectedLinearityIntensityCol}
                        onChange={(event) => updateSharedLinearityIntensityCol(event.target.value)}
                        title={getLinearityBasisDescription(selectedLinearityIntensityCol, selectedLinearityCycleIntensityAggregation)}
                        className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                      >
                        {LINEARITY_INTENSITY_OPTIONS.map((option) => (
                          <option key={option} value={option} title={getLinearityBasisDescription(option, selectedLinearityCycleIntensityAggregation)}>
                            {getLinearityIntensityOptionLabel(option)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="text-sm">
                      <span className="mb-1 block text-stone-700">Linearity cycle intensity</span>
                      <select
                        value={selectedLinearityCycleIntensityAggregation}
                        onChange={(event) => updateSharedLinearity("cycle_intensity_aggregation", event.target.value)}
                        title="Choose which cycle intensity is used when building the selected linearity basis for each analysis."
                        className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                      >
                        {LINEARITY_CYCLE_INTENSITY_AGGREGATION_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-xs text-stone-600">
                      <span className="font-medium text-stone-700">Basis formula:</span>{" "}
                      <code className="font-mono">
                        {getLinearityBasisFormula(selectedLinearityIntensityCol, selectedLinearityCycleIntensityAggregation)}
                      </code>
                    </div>
                  {selectedLinearityIntensityCol === LINEARITY_INTENSITY_SAMP44 ? (
                    <label className="text-sm">
                      <span className="mb-1 block text-stone-700">Max sample intensity</span>
                      <input
                        type="number"
                        step="0.1"
                        min={0}
                        value={activeLinearity.max_sample_intensity ?? ""}
                        onChange={(event) => {
                          const rawValue = event.target.value.trim();
                          if (rawValue === "") {
                            updateSharedLinearity("max_sample_intensity", null);
                            return;
                          }
                          const parsed = Number(rawValue);
                          updateSharedLinearity("max_sample_intensity", Number.isFinite(parsed) ? parsed : null);
                        }}
                        className="w-full rounded-lg border border-stone-300 px-3 py-2"
                      />
                    </label>
                  ) : null}
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="rounded-lg border border-stone-200 p-3 text-sm">
                      <div className="text-xs uppercase tracking-normal text-stone-500">d13C fitted coefficients</div>
                      <div className="mt-1 space-y-1 font-semibold text-stone-900">
                        <div>
                          <span className="font-medium text-stone-500">{getLinearityCoefficientTermLabel("primary", selectedLinearityIntensityCol)}:</span>{" "}
                          {formatFirstNonZeroDigits(d13FitSlope)}
                        </div>
                        {showSecondaryCoefficientOffset ? (
                          <div>
                            <span className="font-medium text-stone-500">{getLinearityCoefficientTermLabel("secondary", selectedLinearityIntensityCol)}:</span>{" "}
                            {formatFirstNonZeroDigits(d13FitQuad)}
                          </div>
                        ) : null}
                      </div>
                    </div>
                    <div className="rounded-lg border border-stone-200 p-3 text-sm">
                      <div className="text-xs uppercase tracking-normal text-stone-500">d18O fitted coefficients</div>
                      <div className="mt-1 space-y-1 font-semibold text-stone-900">
                        <div>
                          <span className="font-medium text-stone-500">{getLinearityCoefficientTermLabel("primary", selectedLinearityIntensityCol)}:</span>{" "}
                          {formatFirstNonZeroDigits(d18FitSlope)}
                        </div>
                        {showSecondaryCoefficientOffset ? (
                          <div>
                            <span className="font-medium text-stone-500">{getLinearityCoefficientTermLabel("secondary", selectedLinearityIntensityCol)}:</span>{" "}
                            {formatFirstNonZeroDigits(d18FitQuad)}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="text-sm">
                      <span className="mb-1 block text-stone-700">
                        {getLinearityCoefficientLabel("d13C", selectedLinearityIntensityCol, "primary", selectedLinearityCycleIntensityAggregation)}
                      </span>
                      <DecimalInput
                        value={activeLinearity.manual_d13_per_10v ?? 0}
                        onValueChange={(value) => updateLinearityCoefficientOffset("d13C", "primary", value)}
                        className="w-full rounded-lg border border-stone-300 px-3 py-2"
                      />
                    </label>
                    <label className="text-sm">
                      <span className="mb-1 block text-stone-700">
                        {getLinearityCoefficientLabel("d18O", selectedLinearityIntensityCol, "primary", selectedLinearityCycleIntensityAggregation)}
                      </span>
                      <DecimalInput
                        value={activeLinearity.manual_d18_per_10v ?? 0}
                        onValueChange={(value) => updateLinearityCoefficientOffset("d18O", "primary", value)}
                        className="w-full rounded-lg border border-stone-300 px-3 py-2"
                      />
                    </label>
                  </div>
                  {showSecondaryCoefficientOffset ? (
                    <div className="grid gap-3 sm:grid-cols-2">
                      <label className="text-sm">
                        <span className="mb-1 block text-stone-700">
                          {getLinearityCoefficientLabel("d13C", selectedLinearityIntensityCol, "secondary", selectedLinearityCycleIntensityAggregation)}
                        </span>
                        <DecimalInput
                          value={activeLinearity.manual_d13_per_10v2 ?? 0}
                          onValueChange={(value) => updateLinearityCoefficientOffset("d13C", "secondary", value)}
                          className="w-full rounded-lg border border-stone-300 px-3 py-2"
                        />
                      </label>
                      <label className="text-sm">
                        <span className="mb-1 block text-stone-700">
                          {getLinearityCoefficientLabel("d18O", selectedLinearityIntensityCol, "secondary", selectedLinearityCycleIntensityAggregation)}
                        </span>
                        <DecimalInput
                          value={activeLinearity.manual_d18_per_10v2 ?? 0}
                          onValueChange={(value) => updateLinearityCoefficientOffset("d18O", "secondary", value)}
                          className="w-full rounded-lg border border-stone-300 px-3 py-2"
                        />
                      </label>
                    </div>
                  ) : null}
                  <div className="text-xs text-stone-500">
                    Coefficient offset active: {coefficientOffsetEnabled ? "Yes" : "No"}
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-3">
                      <span className="text-sm font-medium text-stone-800">Line 1 offset</span>
                      <div className="grid gap-3 sm:grid-cols-2">
                        <label className="text-sm">
                          <span className="mb-1 block text-stone-700">d13C</span>
                          <DecimalInput
                            value={readLinearityOffsetValue(activeLinearity, "line_1_offset_d13")}
                            onValueChange={(value) => updateSharedLinearity("line_1_offset_d13", value)}
                            className="w-full rounded-lg border border-stone-300 px-3 py-2"
                          />
                        </label>
                        <label className="text-sm">
                          <span className="mb-1 block text-stone-700">d18O</span>
                          <DecimalInput
                            value={readLinearityOffsetValue(activeLinearity, "line_1_offset_d18")}
                            onValueChange={(value) => updateSharedLinearity("line_1_offset_d18", value)}
                            className="w-full rounded-lg border border-stone-300 px-3 py-2"
                          />
                        </label>
                      </div>
                    </div>
                    <div className="space-y-3">
                      <span className="text-sm font-medium text-stone-800">Line 2 offset</span>
                      <div className="grid gap-3 sm:grid-cols-2">
                        <label className="text-sm">
                          <span className="mb-1 block text-stone-700">d13C</span>
                          <DecimalInput
                            value={readLinearityOffsetValue(activeLinearity, "line_2_offset_d13")}
                            onValueChange={(value) => updateSharedLinearity("line_2_offset_d13", value)}
                            className="w-full rounded-lg border border-stone-300 px-3 py-2"
                          />
                        </label>
                        <label className="text-sm">
                          <span className="mb-1 block text-stone-700">d18O</span>
                          <DecimalInput
                            value={readLinearityOffsetValue(activeLinearity, "line_2_offset_d18")}
                            onValueChange={(value) => updateSharedLinearity("line_2_offset_d18", value)}
                            className="w-full rounded-lg border border-stone-300 px-3 py-2"
                          />
                        </label>
                      </div>
                    </div>
                  </div>
                  {activeLinearity.apply ? (
                    <div className="space-y-2 rounded-lg border border-stone-200 bg-stone-50 p-3">
                      <div className="text-sm font-medium text-stone-800">Linearity-corrected standard precision</div>
                      {standardPrecisionRows.length ? (
                        standardPrecisionRows.map((item: CalibrationPrecisionSummary) => (
                          <div key={item.standard} className="grid grid-cols-[1fr_auto_auto] items-center gap-3 text-xs text-stone-700">
                            <span className="font-medium text-stone-800">{item.standard}</span>
                            <span>d13C: {formatPrecisionMetric(item.d13_linearity_corrected_precision)}</span>
                            <span>d18O: {formatPrecisionMetric(item.d18_linearity_corrected_precision)}</span>
                          </div>
                        ))
                      ) : (
                        <div className="text-xs text-stone-500">No selected standards available for precision.</div>
                      )}
                    </div>
                  ) : null}
                </>
              ) : (
                <div className="rounded-lg border border-dashed border-stone-300 p-3 text-sm text-stone-500">
                  Load calibration workspace to edit shared linearity parameters.
                </div>
              )}
            </CardContent>
          </Card>
        </aside>

        <div className="space-y-6">
          {error && <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{String(error)}</div>}
          <Card>
            <CardHeader>
              <CardTitle>Diagnostics Matrix</CardTitle>
              <CardDescription>Click any sample point to open the Selection Editor modal on this page.</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="w-full" style={{ height: diagnosticsMatrixHeight }}>
                <PlotlyChart
                  figure={displayedDiagnosticsFigure}
                  className="h-full w-full"
                  onPointClick={handleDiagnosticsPointClick}
                  onPointHover={handleDiagnosticsPointHover}
                  onHoverEnd={scheduleHoverPreviewHide}
                />
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {isSelectionEditorOpen ? (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-stone-950/40 p-3 pt-4 sm:p-6 sm:pt-8" onClick={closeSelectionEditor}>
          <div
            className="flex max-h-[calc(100vh-2rem)] w-full max-w-7xl flex-col overflow-hidden rounded-lg border border-stone-300 bg-white shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-stone-200 px-4 py-3">
              <div>
                <div className="text-base font-semibold text-stone-900">Selection Editor</div>
                <div className="text-sm text-stone-500">Sample editing and cycle diagnostics.</div>
              </div>
              <Button variant="outline" size="sm" onPointerDown={closeSelectionEditor} onClick={closeSelectionEditor}>
                <X className="h-4 w-4" />
                Close
              </Button>
            </div>

            <div className="min-h-0 space-y-4 overflow-y-auto p-4">
              {selectionTarget ? (
                <>
                  <div className="rounded-lg border border-stone-200 bg-stone-50/60 p-4">
                    <div className="text-sm font-semibold text-stone-700">Active sample</div>
                    <div className="mt-1 text-lg font-semibold text-stone-900">
                      {(selectionTarget.identifier1 || "No Identifier 1").trim()} | {(selectionTarget.identifier2 || "No Identifier 2").trim()} |{" "}
                      {selectionTarget.rowLabel}
                    </div>
                    {activeTargetInlineDisplayItems.length ? (
                      <div className="mt-4 grid gap-2 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4">
                        {activeTargetInlineDisplayItems.map((item, index) => (
                          <div
                            key={`${item.label}:${item.value}:${index}`}
                            onClick={() => {
                              if (item.canSetSingleValue && item.setValue != null && item.settableIsotope) {
                                setSelectionEditorTab(item.settableIsotope);
                                setSingleValues((current) => ({ ...current, [item.settableIsotope!]: item.setValue! }));
                              }
                            }}
                            className={cn(
                              "min-h-[88px] rounded-lg border border-stone-200 bg-white px-3 py-2.5",
                              item.canSetSingleValue ? "cursor-pointer hover:border-fuchsia-300 hover:bg-fuchsia-50/50" : "",
                            )}
                          >
                            <div className="text-[11px] font-semibold uppercase tracking-normal text-stone-500">
                              {item.label}
                              {item.unit ? ` (${item.unit})` : ""}
                            </div>
                            <div className="mt-1.5 text-xl font-semibold leading-tight text-stone-900">{item.value}</div>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </div>

                  <div className="inline-flex rounded-lg border border-stone-300 bg-white p-1 shadow-sm">
                    {ISOTOPE_KEYS.map((isotopeKey) => {
                      const isActive = selectionEditorTab === isotopeKey;
                      return (
                        <button
                          key={isotopeKey}
                          type="button"
                          aria-pressed={isActive}
                          onClick={() => setSelectionEditorTab(isotopeKey)}
                          disabled={busy}
                          className={
                            isActive
                              ? "min-w-[92px] rounded-lg bg-stone-900 px-4 py-2 text-sm font-semibold text-white shadow-sm"
                              : "min-w-[92px] rounded-lg px-4 py-2 text-sm font-semibold text-stone-700 hover:bg-stone-100"
                          }
                        >
                          {isotopeKey}
                        </button>
                      );
                    })}
                  </div>

                  <div className="space-y-2">
                    <div className="text-xs font-semibold uppercase tracking-normal text-stone-500">Details</div>
                    <div className="grid gap-3 md:grid-cols-2">
                      <div className="rounded-lg border border-stone-200 bg-stone-50/50 p-4">
                        <div className="text-xs font-semibold uppercase tracking-normal text-stone-600">{selectionEditorTab}</div>
                        <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
                          <div className="text-stone-500">Current</div>
                          <div className="text-right font-medium text-stone-900">{formatDeltaValue(activeSelectionCurrentValue)}</div>
                          <div className="text-stone-500">Cycle mean</div>
                          <div className="text-right font-medium text-stone-900">{formatDeltaValue(activeSelectionCycleMeanValue)}</div>
                          <div className="text-stone-500">First valid cycle</div>
                          <div className="text-right font-medium text-stone-900">{formatDeltaValue(activeSelectionFirstValidCycleValue)}</div>
                          <div className="text-stone-500">Status</div>
                          <div className="text-right font-medium text-stone-900">{activeSelectionStatus}</div>
                        </div>
                        <div className="mt-3 text-xs text-stone-500">Method: {activeSelectionMethod}</div>
                      </div>
                    </div>
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="text-sm">
                      <span className="mb-1 block text-stone-700">Set value ({selectionEditorTab})</span>
                      <input
                        type="number"
                        step="0.001"
                        value={singleValues[selectionEditorTab]}
                        onChange={(event) => {
                          setSingleValues((current) => ({ ...current, [selectionEditorTab]: Number(event.target.value) }));
                          setSingleStdevs((current) => ({ ...current, [selectionEditorTab]: null }));
                        }}
                        className="w-full rounded-lg border border-stone-300 px-3 py-2"
                      />
                    </label>
                    <label className="text-sm">
                      <span className="mb-1 block text-stone-700">Offset ({selectionEditorTab})</span>
                      <input
                        type="number"
                        step="0.001"
                        value={singleOffsets[selectionEditorTab]}
                        onChange={(event) =>
                          setSingleOffsets((current) => ({ ...current, [selectionEditorTab]: Number(event.target.value) }))
                        }
                        className="w-full rounded-lg border border-stone-300 px-3 py-2"
                      />
                    </label>
                  </div>

                  <div className="flex flex-wrap gap-2">
                    <Button onClick={() => applySingleValue(selectionEditorTab)} disabled={busy}>
                      Set {selectionEditorTab}
                    </Button>
                    <Button variant="outline" onClick={() => applySingleOffset(selectionEditorTab)} disabled={busy}>
                      Offset {selectionEditorTab}
                    </Button>
                    <Button variant="outline" onClick={() => applySingleInterpolate(selectionEditorTab)} disabled={busy}>
                      Interpolate {selectionEditorTab}
                    </Button>
                    <Button variant="outline" onClick={resetSelected} disabled={busy}>
                      Reset selected
                    </Button>
                    <Button variant={effectiveOutlier ? "secondary" : "outline"} onClick={() => applyOutlierOverride(true)} disabled={busy}>
                      Force outlier
                    </Button>
                    <Button variant={!effectiveOutlier ? "secondary" : "outline"} onClick={() => applyOutlierOverride(false)} disabled={busy}>
                      Force keep
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => {
                        setSelectionTarget(null);
                        setSelectionEditorOpen(false);
                      }}
                      disabled={busy}
                    >
                      Clear
                    </Button>
                  </div>

                  <DiagnosticsPanel
                    title={`${selectionEditorTab} cycle diagnostics (shared intensity chart/table)`}
                    diagnostics={activeSelectionDiagnostics}
                    loading={activeSelectionLoading}
                    onPickDeltaValue={(value, stdev = null) => {
                      setSingleValues((current) => ({ ...current, [selectionEditorTab]: roundDeltaValue(value) }));
                      setSingleStdevs((current) => ({ ...current, [selectionEditorTab]: stdev }));
                    }}
                  />
                </>
              ) : (
                <div className="rounded-lg border border-dashed border-stone-300 p-4 text-sm text-stone-500">
                  No active selection.
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}
      {shouldShowHoverPreview && hoverPreview && hoverPreviewPosition ? (
        <div
          className="pointer-events-none fixed z-[80] w-[min(560px,calc(100vw-20px))] rounded-lg border border-stone-300 bg-white/95 p-3 shadow-2xl backdrop-blur-[1px]"
          style={{ left: `${hoverPreviewPosition.left}px`, top: `${hoverPreviewPosition.top}px` }}
        >
          <div className="mb-2 flex items-center justify-between gap-2 text-xs text-stone-600">
            <span className="font-medium text-stone-800">
              {hoverPreview.target.identifier1 || "Sample"} | {hoverPreview.target.identifier2 || "N/A"}
            </span>
            <span className="rounded-md bg-stone-100 px-2 py-0.5 font-medium uppercase tracking-normal text-stone-700">
              {hoverPreview.target.isotopeKey}
            </span>
          </div>
          {hoverDiagnosticsQuery.isLoading || hoverDiagnosticsQuery.isFetching ? (
            <div className="rounded-lg border border-dashed border-stone-300 p-4 text-sm text-stone-500">Loading hover preview...</div>
          ) : hasHoverDiagnosticsFigureData ? (
            <PlotlyChart figure={hoverDiagnosticsFigure} className="w-full" />
          ) : (
            <div className="rounded-lg border border-dashed border-stone-300 p-4 text-sm text-stone-500">
              Cycle-intensity preview unavailable for this point.
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
