"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, SearchCheck, X } from "lucide-react";
import { type KeyboardEvent as ReactKeyboardEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";

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
import { Tooltip } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import type {
  CalibrationConfig,
  CalibrationPrecisionSummary,
  CalibrationWorkspace,
  ClientOutputDuplicateCheckResponse,
  CycleDiagnosticsPayload,
  EditAction,
  ExportRequest,
  OutlierTable,
  ProcessingConfig,
  ProcessingWorkspace,
  SaturationCorrectionMethod,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { useSessionStore } from "@/store/use-session-store";

type SelectedTarget = {
  rowLabel: string;
  isotopeKey: "d13C" | "d18O" | "cross";
  identifier1: string;
  identifier2: string;
  currentValue?: number | null;
  currentD13?: number | null;
  currentD18?: number | null;
  chartKey: string;
};

type IsotopeKey = "d13C" | "d18O";
const ISOTOPE_KEYS: IsotopeKey[] = ["d13C", "d18O"];
type IsotopeNumericMap = Record<IsotopeKey, number>;
type LinearityOffsetField = "line_1_offset_d13" | "line_1_offset_d18" | "line_2_offset_d13" | "line_2_offset_d18";
type LinearityOffsetDraftState = Record<LinearityOffsetField, string>;
type LinearityCycleIntensityAggregation = "run_median" | "first_valid_cycle" | "last_valid_cycle";
const SATURATION_METHOD_OPTIONS: Array<{ value: SaturationCorrectionMethod; label: string }> = [
  { value: "cycle_mean", label: "Cycle mean" },
  { value: "first_valid_cycle", label: "First valid cycle" },
  { value: "last_valid_cycle", label: "Last valid cycle" },
  { value: "reference_gas_intensity", label: "Reference-gas signal intensity" },
  { value: "first_cycle", label: "Stabilized cycle curve" },
  { value: "cycle_relative_mismatch", label: "Cycle relative mismatch" },
  { value: "cycle_symmetric_mismatch", label: "Cycle symmetric mismatch" },
  { value: "cycle_mean_intensity", label: "Cycle mean intensity" },
  { value: "cycle_intensity_weighted_mismatch", label: "Cycle intensity-weighted mismatch" },
  { value: "cycle_two_term_mean_mismatch", label: "Cycle two-term mean + mismatch" },
  { value: "cycle_plateau", label: "Cycle late plateau" },
];
const SELECTION_EDITOR_DEFAULT_OFFSET = 0.1;
const RESTORE_STDEV_DEFAULT_CAP = 0.04;
const HOVER_PREVIEW_SHOW_DELAY_MS = 500;
const SELECTION_EDITOR_CHART_DEFER_MS = 350;
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

type ChartDisplayState = {
  hideCalibrated: boolean;
  hideSymbols: boolean;
  runningAverage: boolean;
  runningAveragePeriod: number;
  rawOnly?: boolean;
};
type DisplayStateMap = Record<string, ChartDisplayState>;
type FigureShape = Record<string, unknown> & {
  data: Array<Record<string, unknown>>;
  layout: Record<string, unknown>;
};
type SelectionSourceChart = {
  title: string;
  description: string;
  chartKey?: string;
  figure?: Record<string, unknown>;
  stackedFigures?: Array<{
    key: string;
    chartKey: string;
    title: string;
    figure?: Record<string, unknown>;
  }>;
};
type ColorScaleBounds = {
  min: number;
  max: number;
};
type HoverPreviewState = {
  target: SelectedTarget;
  clientX: number;
  clientY: number;
};
const DEFAULT_CHART_DISPLAY_STATE: ChartDisplayState = {
  hideCalibrated: true,
  hideSymbols: false,
  runningAverage: false,
  runningAveragePeriod: 5,
};
const DEFAULT_PLOTLY_COLORWAY = [
  "#636EFA",
  "#EF553B",
  "#00CC96",
  "#AB63FA",
  "#FFA15A",
  "#19D3F3",
  "#FF6692",
  "#B6E880",
  "#FF97FF",
  "#FECB52",
];

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

function parseDecimalInput(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return 0;
  }
  const normalized = trimmed.replace(",", ".");
  if (!/^[-+]?(\d+(\.\d*)?|\.\d+)$/.test(normalized)) {
    return null;
  }
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatDecimalInput(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "0";
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
  return `${value.toFixed(3)} ‰`;
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

function clampRunningAveragePeriod(value: unknown): number {
  const parsed = Number.parseInt(String(value ?? DEFAULT_CHART_DISPLAY_STATE.runningAveragePeriod), 10);
  if (!Number.isFinite(parsed)) {
    return DEFAULT_CHART_DISPLAY_STATE.runningAveragePeriod;
  }
  return Math.min(999, Math.max(2, parsed));
}

function normalizeDisplayState(state?: Partial<ChartDisplayState> | null): ChartDisplayState {
  const hasCurrentShape =
    state != null && ("hideSymbols" in state || "runningAverage" in state || "runningAveragePeriod" in state);
  return {
    ...DEFAULT_CHART_DISPLAY_STATE,
    hideCalibrated: hasCurrentShape
      ? Boolean(state?.hideCalibrated || state?.rawOnly)
      : Boolean(state?.hideCalibrated || state?.rawOnly || DEFAULT_CHART_DISPLAY_STATE.hideCalibrated),
    hideSymbols: Boolean(state?.hideSymbols),
    runningAverage: Boolean(state?.runningAverage),
    runningAveragePeriod: clampRunningAveragePeriod(state?.runningAveragePeriod),
  };
}

function chartDisplayStateKey(state: ChartDisplayState): string {
  const display = normalizeDisplayState(state);
  return [
    display.hideCalibrated ? "hide-calibrated" : "show-calibrated",
    display.hideSymbols ? "hide-symbols" : "show-symbols",
    display.runningAverage ? "running-average" : "no-running-average",
    String(display.runningAveragePeriod),
  ].join("|");
}

function normalizeDisplayStateMap(value: unknown): DisplayStateMap {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  const next: DisplayStateMap = {};
  for (const [key, state] of Object.entries(value as Record<string, unknown>)) {
    if (!key || !state || typeof state !== "object" || Array.isArray(state)) {
      continue;
    }
    next[key] = normalizeDisplayState(state as Partial<ChartDisplayState>);
  }
  return next;
}

function hasTraceMode(trace: Record<string, unknown>, mode: string): boolean {
  return String(trace.mode ?? "").split("+").includes(mode);
}

function getColorwayColor(layout: Record<string, unknown>, index: number): string {
  const colorway = Array.isArray(layout.colorway) ? layout.colorway.filter((item): item is string => typeof item === "string") : [];
  const colors = colorway.length ? colorway : DEFAULT_PLOTLY_COLORWAY;
  return colors[index % colors.length];
}

function hideTraceSymbols(trace: Record<string, unknown>, traceIndex: number, layout: Record<string, unknown>): Record<string, unknown> | null {
  if (!hasTraceMode(trace, "markers")) {
    return trace;
  }
  if (!hasTraceMode(trace, "lines")) {
    return null;
  }
  const nextTrace = { ...trace };
  const line = trace.line && typeof trace.line === "object" ? (trace.line as Record<string, unknown>) : {};
  const marker = trace.marker && typeof trace.marker === "object" ? (trace.marker as Record<string, unknown>) : {};
  const markerColor = typeof marker.color === "string" ? marker.color : null;
  nextTrace.line = {
    ...line,
    ...(line.color == null ? { color: markerColor ?? getColorwayColor(layout, traceIndex) } : {}),
  };
  nextTrace.mode = String(trace.mode ?? "")
    .split("+")
    .filter((item) => item !== "markers")
    .join("+");
  if (trace.error_x && typeof trace.error_x === "object") {
    nextTrace.error_x = { ...(trace.error_x as Record<string, unknown>), visible: false };
  }
  if (trace.error_y && typeof trace.error_y === "object") {
    nextTrace.error_y = { ...(trace.error_y as Record<string, unknown>), visible: false };
  }
  return nextTrace;
}

function rollingAverage(values: unknown[], period: number): Array<number | null> {
  const averaged: Array<number | null> = [];
  const windowValues: number[] = [];
  let sum = 0;
  for (const value of values) {
    const numeric = toFiniteNumber(value);
    if (numeric == null) {
      averaged.push(null);
      continue;
    }
    windowValues.push(numeric);
    sum += numeric;
    if (windowValues.length > period) {
      sum -= windowValues.shift() ?? 0;
    }
    averaged.push(windowValues.length === period ? sum / period : null);
  }
  return averaged;
}

function buildRunningAverageTrace(
  trace: Record<string, unknown>,
  period: number,
  index: number,
): Record<string, unknown> | null {
  const name = String(trace.name ?? "");
  if (!name.startsWith("Raw ")) {
    return null;
  }
  const x = coerceVector(trace.x);
  const y = coerceVector(trace.y);
  if (!x || !y || x.length !== y.length || x.length < period) {
    return null;
  }
  const averaged = rollingAverage(y, period);
  if (!averaged.some((value) => value != null)) {
    return null;
  }
  const line = trace.line && typeof trace.line === "object" ? (trace.line as Record<string, unknown>) : {};
  const marker = trace.marker && typeof trace.marker === "object" ? (trace.marker as Record<string, unknown>) : {};
  const lineColor = typeof line.color === "string" ? line.color : typeof marker.color === "string" ? marker.color : "#111827";
  return {
    type: trace.type ?? "scatter",
    x,
    y: averaged,
    mode: "lines",
    name: `${name.replace(/^Raw\s+/, "")} running average (${period})`,
    showlegend: false,
    legendgroup: `running-average-${index}`,
    line: {
      color: lineColor,
      width: 2.5,
      dash: "dash",
    },
    hovertemplate: `Running average (${period})<br>x: %{x}<br>value: %{y:.3f}<extra></extra>`,
  };
}

function applyDisplayState(
  figure: Record<string, unknown> | undefined,
  state: ChartDisplayState,
) {
  const display = normalizeDisplayState(state);
  const cloned = cloneFigure(figure);
  if (!Array.isArray(cloned.data)) {
    return cloned;
  }
  let traces = (cloned.data as Array<Record<string, unknown>>).map((trace, index) => ({ trace, index }));
  if (display.hideCalibrated) {
    traces = traces.filter(({ trace }) => !String(trace.name ?? "").startsWith("Calibrated"));
  }
  if (display.hideSymbols) {
    traces = traces
      .map(({ trace, index }) => {
        const nextTrace = hideTraceSymbols(trace, index, cloned.layout);
        return nextTrace ? { trace: nextTrace, index } : null;
      })
      .filter((item): item is { trace: Record<string, unknown>; index: number } => item != null);
  }
  let displayTraces = traces.map(({ trace }) => trace);
  if (display.runningAverage) {
    const averageTraces = displayTraces
      .map((trace, index) => buildRunningAverageTrace(trace, display.runningAveragePeriod, index))
      .filter((trace): trace is Record<string, unknown> => trace != null);
    displayTraces = [...displayTraces, ...averageTraces];
  }
  return { ...cloned, data: displayTraces };
}

function TraceModeControl({
  state,
  hasCalibrated,
  onChange,
}: {
  state: ChartDisplayState;
  hasCalibrated: boolean;
  onChange: (patch: Partial<ChartDisplayState>) => void;
}) {
  const display = normalizeDisplayState(state);
  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm shadow-sm"
      role="group"
      aria-label="Chart display options"
    >
      <label className={cn("inline-flex items-center gap-2 font-medium", hasCalibrated ? "text-stone-700" : "text-stone-400")}>
        <input
          type="checkbox"
          checked={hasCalibrated && display.hideCalibrated}
          disabled={!hasCalibrated}
          onChange={(event) => onChange({ hideCalibrated: event.target.checked })}
          className="h-4 w-4"
        />
        Hide calibrated
      </label>
      <label className="inline-flex items-center gap-2 font-medium text-stone-700">
        <input
          type="checkbox"
          checked={display.hideSymbols}
          onChange={(event) => onChange({ hideSymbols: event.target.checked })}
          className="h-4 w-4"
        />
        Hide symbols
      </label>
      <label className="inline-flex items-center gap-2 font-medium text-stone-700">
        <input
          type="checkbox"
          checked={display.runningAverage}
          onChange={(event) => onChange({ runningAverage: event.target.checked })}
          className="h-4 w-4"
        />
        Running average
      </label>
      <label className="inline-flex items-center gap-2 font-medium text-stone-700">
        <span>Period</span>
        <input
          type="number"
          min={2}
          max={999}
          step={1}
          value={display.runningAveragePeriod}
          disabled={!display.runningAverage}
          onChange={(event) => onChange({ runningAveragePeriod: clampRunningAveragePeriod(event.target.value) })}
          className={cn(
            "h-8 w-20 rounded-md border border-stone-300 px-2 text-sm",
            display.runningAverage ? "bg-white" : "cursor-not-allowed bg-stone-100 text-stone-500",
          )}
        />
      </label>
    </div>
  );
}

function figureHasTracePrefix(figure: Record<string, unknown> | undefined, prefix: string): boolean {
  const dataCandidate = (figure as { data?: unknown } | undefined)?.data;
  if (!Array.isArray(dataCandidate)) {
    return false;
  }
  return dataCandidate.some((trace) => String((trace as Record<string, unknown>)?.name ?? "").startsWith(prefix));
}

function pointMatchesSelectedTarget(pointCustomData: unknown, target: SelectedTarget): boolean {
  if (Array.isArray(pointCustomData)) {
    const rowLabel = String(pointCustomData[0] ?? "").trim();
    const isotopeKey = String(pointCustomData[1] ?? "").trim();
    if (!rowLabel) {
      return false;
    }
    if (target.isotopeKey === "cross") {
      return rowLabel === target.rowLabel && (isotopeKey === "d13C" || isotopeKey === "d18O" || isotopeKey === "cross" || isotopeKey === "");
    }
    return rowLabel === target.rowLabel && (isotopeKey === target.isotopeKey || isotopeKey === "");
  }
  if (pointCustomData && typeof pointCustomData === "object") {
    const payload = pointCustomData as Record<string, unknown>;
    const rowLabel = String(payload.row_label ?? payload.rowLabel ?? "").trim();
    const isotopeKey = String(payload.isotope_key ?? payload.isotopeKey ?? "").trim();
    if (!rowLabel) {
      return false;
    }
    if (target.isotopeKey === "cross") {
      return rowLabel === target.rowLabel && (isotopeKey === "d13C" || isotopeKey === "d18O" || isotopeKey === "cross" || isotopeKey === "");
    }
    return rowLabel === target.rowLabel && (isotopeKey === target.isotopeKey || isotopeKey === "");
  }
  return false;
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

function highlightSelectionSourceFigure(
  figure: Record<string, unknown> | undefined,
  target: SelectedTarget | null,
): Record<string, unknown> | undefined {
  if (!figure || !target) {
    return figure;
  }
  const cloned = cloneFigure(figure);
  const highlightTraces: Array<Record<string, unknown>> = [];
  const traces = Array.isArray(cloned.data) ? cloned.data : [];
  const matchedTraces: Array<{
    trace: Record<string, unknown>;
    indexes: number[];
    customdata: unknown[];
    x: unknown[];
    y: unknown[];
    z: unknown[] | null;
    preferred: boolean;
  }> = [];
  for (const trace of traces) {
    const customdata = coerceVector(trace.customdata);
    const x = coerceVector(trace.x);
    const y = coerceVector(trace.y);
    const z = coerceVector(trace.z);
    if (!customdata || !x || !y) {
      continue;
    }
    const indexes: number[] = [];
    const pointCount = Math.min(customdata.length, x.length, y.length);
    for (let index = 0; index < pointCount; index += 1) {
      if (pointMatchesSelectedTarget(customdata[index], target)) {
        indexes.push(index);
      }
    }
    if (!indexes.length) {
      continue;
    }
    const traceName = String(trace.name ?? "").trim().toLowerCase();
    const preferred =
      target.isotopeKey === "cross"
        ? !traceName.startsWith("calibrated")
        : traceName.startsWith("raw ");
    matchedTraces.push({ trace, indexes, customdata, x, y, z, preferred });
  }
  const preferredMatches = matchedTraces.filter((item) => item.preferred);
  const highlightSources =
    preferredMatches.length
      ? preferredMatches
      : target.isotopeKey === "cross"
        ? matchedTraces
        : [];
  for (const source of highlightSources) {
    const { trace, indexes, customdata, x, y, z } = source;
    const traceType = String(trace.type ?? "scatter");
    const is3dTrace = traceType.includes("3d");
    const highlightColor = "#FF00FF";
    const traceMarker = trace.marker && typeof trace.marker === "object" ? (trace.marker as Record<string, unknown>) : {};
    const baseSize = typeof traceMarker.size === "number" ? traceMarker.size : 8;
    const highlightTrace: Record<string, unknown> = {
      type: trace.type ?? "scatter",
      mode: "markers",
      name: "Selected sample",
      showlegend: false,
      hoverinfo: "skip",
      x: indexes.map((index) => x[index]),
      y: indexes.map((index) => y[index]),
      customdata: indexes.map((index) => customdata[index]),
      marker: {
        color: is3dTrace ? highlightColor : "rgba(255, 0, 255, 0.28)",
        size: Math.max(baseSize + (is3dTrace ? 5 : 10), is3dTrace ? 14 : 18),
        symbol: "circle",
        line: {
          color: highlightColor,
          width: is3dTrace ? 2.5 : 3.5,
        },
      },
    };
    if (z) {
      highlightTrace.z = indexes.map((index) => z[index]);
    }
    highlightTraces.push(highlightTrace);
  }
  if (!highlightTraces.length) {
    return cloned;
  }
  return {
    ...cloned,
    data: [...traces, ...highlightTraces],
  };
}

function figureContainsRowLabel(figure: Record<string, unknown> | undefined, rowLabel: string): boolean {
  if (!figure || !rowLabel) {
    return false;
  }
  const traces = Array.isArray((figure as FigureShape).data) ? ((figure as FigureShape).data as Array<Record<string, unknown>>) : [];
  for (const trace of traces) {
    const customdata = Array.isArray(trace.customdata) ? trace.customdata : null;
    if (!customdata) {
      continue;
    }
    for (const point of customdata) {
      if (Array.isArray(point) && String(point[0] ?? "") === rowLabel) {
        return true;
      }
    }
  }
  return false;
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
          if (x == null || y == null) {
            continue;
          }
          if (Math.abs(x - cycleMarker.cycle) > 0.0001) {
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

function normalizeIsotopeKey(value: unknown): "d13C" | "d18O" | "cross" | null {
  const token = String(value ?? "").trim().toLowerCase();
  if (!token) {
    return null;
  }
  if (token === "d13c" || token === "d13") {
    return "d13C";
  }
  if (token === "d18o" || token === "d18") {
    return "d18O";
  }
  if (token === "cross") {
    return "cross";
  }
  return null;
}

function inferIsotopeKeyFromChartKey(chartKey: string): "d13C" | "d18O" | "cross" | null {
  const key = String(chartKey ?? "").trim();
  if (!key) {
    return null;
  }
  if (key === "crossplot" || key === "processing_3d") {
    return "cross";
  }
  if (key === "d13_summary" || key.endsWith("|d13C")) {
    return "d13C";
  }
  if (key === "d18_summary" || key.endsWith("|d18O")) {
    return "d18O";
  }
  return null;
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

function pointTraceName(point: PlotlyPoint): string {
  const payload = point as unknown as Record<string, unknown>;
  const dataCandidate = (payload.data ?? payload.fullData) as Record<string, unknown> | undefined;
  return String(dataCandidate?.name ?? "").trim();
}

function parseSelectedTargets(points: PlotlyPoint[], chartKey: string): SelectedTarget[] {
  const seen = new Set<string>();
  const targets: SelectedTarget[] = [];
  const inferredIsotope = inferIsotopeKeyFromChartKey(chartKey);
  for (const point of points) {
    const rawCustomdata = extractPointCustomData(point);
    const customdata = coercePointCustomDataArray(rawCustomdata);
    const customObj = rawCustomdata && typeof rawCustomdata === "object" ? (rawCustomdata as Record<string, unknown>) : null;
    const scalarRowLabel =
      typeof rawCustomdata === "string" || typeof rawCustomdata === "number" ? String(rawCustomdata).trim() : "";
    const hasArrayRowPayload = Boolean(customdata && customdata.length >= 1);
    const hasObjectRowPayload = Boolean(customObj && ("row_label" in customObj || "rowLabel" in customObj || "0" in customObj));
    if (!hasArrayRowPayload && !hasObjectRowPayload && !scalarRowLabel) {
      continue;
    }
    const pointPayload = point as unknown as Record<string, unknown>;
    const rowLabel = String(
      customdata?.[0] ??
        customObj?.row_label ??
        customObj?.rowLabel ??
        scalarRowLabel ??
        pointPayload.id ??
        "",
    ).trim();
    const isotopeKey = normalizeIsotopeKey(customdata?.[1] ?? customObj?.isotope_key ?? customObj?.isotopeKey) ?? inferredIsotope;
    const identifier1 = String(customdata?.[2] ?? customObj?.identifier_1 ?? customObj?.identifier1 ?? "").trim();
    const identifier2 = String(customdata?.[3] ?? customObj?.identifier_2 ?? customObj?.identifier2 ?? "").trim();
    if (!rowLabel || !isotopeKey) {
      continue;
    }
    const traceName = pointTraceName(point).toLowerCase();
    const primaryIsotopeTracePoint = isotopeKey !== "cross" && traceName.startsWith("raw ");
    const token = `${isotopeKey}|${rowLabel}`;
    if (seen.has(token)) {
      continue;
    }
    seen.add(token);
    targets.push({
      rowLabel,
      isotopeKey,
      identifier1,
      identifier2,
      currentValue: primaryIsotopeTracePoint && typeof point.y === "number" ? point.y : null,
      currentD13: isotopeKey === "cross" && typeof point.y === "number" ? point.y : null,
      currentD18: isotopeKey === "cross" && typeof point.x === "number" ? point.x : null,
      chartKey,
    });
  }
  return targets;
}

function isPartiallySaturatedCollectorStatus(value: unknown): boolean {
  return String(value ?? "").trim().toLowerCase() === "partially saturated collectors";
}

function isFailedSampleCollectorStatus(value: unknown): boolean {
  return String(value ?? "").trim().toLowerCase() === "failed sample";
}

function formatMethodLabel(value: unknown): string {
  const normalized = String(value ?? "").trim();
  if (!normalized) {
    return "Imported";
  }
  const labels: Record<string, string> = {
    imported: "Imported",
    edited: "Edited",
    cycle_mean: "Cycle mean",
    first_valid_cycle: "First valid cycle",
    last_valid_cycle: "Last valid cycle",
    reference_gas_intensity: "Reference-gas intensity",
    first_cycle: "First cycle",
  };
  return labels[normalized] ?? normalized.replaceAll("_", " ");
}

function targetNumberValue(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "null";
}

function targetSignature(target: SelectedTarget): string {
  return [
    target.chartKey,
    target.rowLabel,
    target.isotopeKey,
    target.identifier1,
    target.identifier2,
    targetNumberValue(target.currentValue),
    targetNumberValue(target.currentD13),
    targetNumberValue(target.currentD18),
  ].join("|");
}

function areSameSelectionTargets(current: SelectedTarget[], next: SelectedTarget[]): boolean {
  if (current.length !== next.length) {
    return false;
  }
  const currentSignatures = current.map(targetSignature).sort();
  const nextSignatures = next.map(targetSignature).sort();
  return currentSignatures.every((value, index) => value === nextSignatures[index]);
}

function coerceStoredSelectedTarget(value: unknown): SelectedTarget | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const payload = value as Record<string, unknown>;
  const rowLabel = String(payload.rowLabel ?? "").trim();
  const isotope = normalizeIsotopeKey(payload.isotopeKey);
  const chartKey = String(payload.chartKey ?? "").trim();
  if (!rowLabel || !isotope || !chartKey) {
    return null;
  }
  const toNumberOrNull = (candidate: unknown): number | null =>
    typeof candidate === "number" && Number.isFinite(candidate) ? candidate : null;
  return {
    rowLabel,
    isotopeKey: isotope,
    identifier1: String(payload.identifier1 ?? "").trim(),
    identifier2: String(payload.identifier2 ?? "").trim(),
    currentValue: toNumberOrNull(payload.currentValue),
    currentD13: toNumberOrNull(payload.currentD13),
    currentD18: toNumberOrNull(payload.currentD18),
    chartKey,
  };
}

function serializeCommentMap(commentMap: Record<string, string>) {
  return Object.entries(commentMap)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}

function parseCommentMap(raw: string) {
  const map: Record<string, string> = {};
  raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .forEach((line) => {
      const separator = line.includes("=") ? "=" : ":";
      const [source, ...rest] = line.split(separator);
      const target = rest.join(separator).trim();
      const sourceKey = source.trim();
      if (sourceKey && target) {
        map[sourceKey] = target;
      }
    });
  return map;
}

function fallbackTargetValue(target: SelectedTarget, isotopeKey: IsotopeKey): number {
  if (target.isotopeKey === "cross") {
    const crossValue = isotopeKey === "d13C" ? target.currentD13 : target.currentD18;
    return typeof crossValue === "number" && Number.isFinite(crossValue) ? crossValue : 0;
  }
  if (target.isotopeKey === isotopeKey && typeof target.currentValue === "number" && Number.isFinite(target.currentValue)) {
    return target.currentValue;
  }
  return 0;
}

function selectedTargetPointValue(target: SelectedTarget | null, isotopeKey: IsotopeKey): number | null {
  if (!target) {
    return null;
  }
  if (target.isotopeKey === "cross") {
    const value = isotopeKey === "d13C" ? target.currentD13 : target.currentD18;
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  }
  if (target.isotopeKey === isotopeKey && typeof target.currentValue === "number" && Number.isFinite(target.currentValue)) {
    return target.currentValue;
  }
  return null;
}

function configEquals(left: ProcessingConfig | null | undefined, right: ProcessingConfig | null | undefined) {
  if (!left || !right) {
    return false;
  }
  return JSON.stringify(left) === JSON.stringify(right);
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asString(value: unknown): string {
  return value == null ? "" : String(value);
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

function isDeltaColumnLabel(label: string): boolean {
  const normalized = label.trim().toLowerCase();
  return normalized.includes("d13") || normalized.includes("d18");
}

function isSignalIntensityColumnLabel(label: string): boolean {
  const normalized = label.trim().toLowerCase();
  return normalized.includes("int m/z") && normalized.includes("(v)");
}

function parseStrictNumber(value: string): number | null {
  const normalized = value.trim().replace(/,/g, "");
  if (!/^[-+]?\d+(\.\d+)?$/.test(normalized)) {
    return null;
  }
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function roundDeltaValue(value: number, precision = 3): number {
  return Number(value.toFixed(precision));
}

function formatDeltaValue(value: number | null | undefined, precision = 3): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(precision) : "N/A";
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

function DataTable({
  rows,
  emptyLabel,
  selectedRowLabels = [],
  onSelectedRowLabelsChange,
}: {
  rows: Array<Record<string, unknown>>;
  emptyLabel: string;
  selectedRowLabels?: string[];
  onSelectedRowLabelsChange?: (next: string[]) => void;
}) {
  if (!rows.length) {
    return <div className="rounded-lg border border-dashed border-stone-300 p-4 text-sm text-stone-500">{emptyLabel}</div>;
  }
  const selectable = typeof onSelectedRowLabelsChange === "function";
  const selectedSet = new Set(selectedRowLabels);
  const visibleRows = rows.slice(0, 25);
  const columns = Object.keys(rows[0] ?? {}).filter((column) => !column.startsWith("__"));

  function toggleRowSelection(rowLabel: string, checked: boolean) {
    if (!onSelectedRowLabelsChange) {
      return;
    }
    const next = new Set(selectedRowLabels);
    if (checked) {
      next.add(rowLabel);
    } else {
      next.delete(rowLabel);
    }
    onSelectedRowLabelsChange(Array.from(next));
  }

  function formatValue(value: unknown, column: string): string {
    if (value == null || value === "") {
      return "";
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      if (Number.isInteger(value)) {
        return String(value);
      }
      if (isDeltaColumnLabel(column)) {
        return formatDeltaValue(value);
      }
      return value.toFixed(6);
    }
    return String(value);
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-stone-200">
      <table className="min-w-full divide-y divide-stone-200 text-left text-sm">
        <thead className="bg-stone-50">
          <tr>
            {selectable ? <th className="w-12 px-3 py-2 font-medium text-stone-700">Sel</th> : null}
            {columns.map((column) => (
              <th key={column} className="px-3 py-2 font-medium text-stone-700">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-stone-100 bg-white">
          {visibleRows.map((row, rowIndex) => {
            const rowLabel = extractOutlierRowLabel(row);
            const canSelectRow = selectable && rowLabel != null;
            return (
              <tr key={rowLabel ?? rowIndex}>
                {selectable ? (
                  <td className="px-3 py-2 text-stone-600">
                    <input
                      type="checkbox"
                      aria-label={rowLabel ? `Select row ${rowLabel}` : `Select row ${rowIndex + 1}`}
                      checked={rowLabel != null ? selectedSet.has(rowLabel) : false}
                      disabled={!canSelectRow}
                      onChange={(event) => {
                        if (rowLabel) {
                          toggleRowSelection(rowLabel, event.target.checked);
                        }
                      }}
                      className="h-4 w-4"
                    />
                  </td>
                ) : null}
              {columns.map((column) => (
                <td key={column} className="px-3 py-2 text-stone-600">
                  {formatValue(row[column], column)}
                </td>
              ))}
            </tr>
            );
          })}
        </tbody>
      </table>
      {rows.length > 25 ? <div className="border-t border-stone-200 px-3 py-2 text-xs text-stone-500">Showing first 25 of {rows.length} rows.</div> : null}
    </div>
  );
}

function clampNumber(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function parseFinite(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseNumericDraft(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const normalized = trimmed.replace(",", ".");
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
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

function compactHoverDiagnosticsFigure(figure: Record<string, unknown> | undefined): Record<string, unknown> | undefined {
  if (!figure) {
    return figure;
  }
  const cloned = cloneFigure(figure);
  const nextLayout: Record<string, unknown> = {
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
  return {
    ...cloned,
    layout: nextLayout,
  };
}

function extractOutlierRowLabel(row: Record<string, unknown>): string | null {
  const direct = row["__row_label"];
  if (typeof direct === "string" && direct.trim()) {
    return direct.trim();
  }
  if (typeof direct === "number" && Number.isFinite(direct)) {
    return String(direct);
  }
  const fallback = row["Row Label"] ?? row["row_label"];
  if (typeof fallback === "string" && fallback.trim()) {
    return fallback.trim();
  }
  if (typeof fallback === "number" && Number.isFinite(fallback)) {
    return String(fallback);
  }
  return null;
}

function isFailedSampleOutlierTable(table: OutlierTable): boolean {
  const normalizedName = String(table.name || table.title || "").toLowerCase();
  return normalizedName.includes("failed sample");
}

function pickRandomSubset(values: string[], count: number): string[] {
  if (count <= 0) {
    return [];
  }
  const pool = [...values];
  for (let i = pool.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    const tmp = pool[i];
    pool[i] = pool[j];
    pool[j] = tmp;
  }
  return pool.slice(0, Math.min(count, pool.length));
}

function RangeSliderField({
  label,
  value,
  min,
  max,
  step = 0.1,
  precision = 2,
  showManualInputs = false,
  onChange,
}: {
  label: string;
  value: [number, number];
  min: number;
  max: number;
  step?: number;
  precision?: number;
  showManualInputs?: boolean;
  onChange: (next: [number, number]) => void;
}) {
  const resolvedMin = Math.min(min, max);
  const resolvedMax = Math.max(min, max);
  const low = clampNumber(Math.min(value[0], value[1]), resolvedMin, resolvedMax);
  const high = clampNumber(Math.max(value[0], value[1]), resolvedMin, resolvedMax);
  const [lowDraft, setLowDraft] = useState(low.toFixed(precision));
  const [highDraft, setHighDraft] = useState(high.toFixed(precision));

  useEffect(() => {
    setLowDraft(low.toFixed(precision));
  }, [low, precision]);

  useEffect(() => {
    setHighDraft(high.toFixed(precision));
  }, [high, precision]);

  const commitLowDraft = () => {
    const parsed = parseNumericDraft(lowDraft);
    if (parsed == null) {
      setLowDraft(low.toFixed(precision));
      return;
    }
    const nextLow = clampNumber(parsed, resolvedMin, high);
    onChange([nextLow, high]);
  };

  const commitHighDraft = () => {
    const parsed = parseNumericDraft(highDraft);
    if (parsed == null) {
      setHighDraft(high.toFixed(precision));
      return;
    }
    const nextHigh = clampNumber(parsed, low, resolvedMax);
    onChange([low, nextHigh]);
  };

  return (
    <div className="rounded-lg border border-stone-200 bg-white p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-stone-700">{label}</span>
        <span className="text-xs text-stone-500">
          {low.toFixed(precision)} to {high.toFixed(precision)}
        </span>
      </div>
      <div className="mt-3 space-y-2">
        <label className="block text-xs text-stone-600">
          Min
          <div className={cn("mt-1", showManualInputs ? "flex items-center gap-2" : "block")}>
            <input
              type="range"
              min={resolvedMin}
              max={resolvedMax}
              step={step}
              value={low}
              onInput={(event) => {
                const nextLow = parseFinite(event.currentTarget.value, low);
                onChange([Math.min(nextLow, high), high]);
              }}
              className={cn("w-full accent-stone-700", showManualInputs ? "min-w-0 flex-1" : "")}
            />
            {showManualInputs ? (
              <input
                type="number"
                min={resolvedMin}
                max={high}
                step={step}
                value={lowDraft}
                onChange={(event) => setLowDraft(event.currentTarget.value)}
                onBlur={commitLowDraft}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.currentTarget.blur();
                  }
                }}
                className="w-20 shrink-0 rounded border border-stone-300 px-2 py-1 text-right text-xs text-stone-700"
                aria-label={`${label} minimum value`}
              />
            ) : null}
          </div>
        </label>
        <label className="block text-xs text-stone-600">
          Max
          <div className={cn("mt-1", showManualInputs ? "flex items-center gap-2" : "block")}>
            <input
              type="range"
              min={resolvedMin}
              max={resolvedMax}
              step={step}
              value={high}
              onInput={(event) => {
                const nextHigh = parseFinite(event.currentTarget.value, high);
                onChange([low, Math.max(nextHigh, low)]);
              }}
              className={cn("w-full accent-stone-700", showManualInputs ? "min-w-0 flex-1" : "")}
            />
            {showManualInputs ? (
              <input
                type="number"
                min={low}
                max={resolvedMax}
                step={step}
                value={highDraft}
                onChange={(event) => setHighDraft(event.currentTarget.value)}
                onBlur={commitHighDraft}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.currentTarget.blur();
                  }
                }}
                className="w-20 shrink-0 rounded border border-stone-300 px-2 py-1 text-right text-xs text-stone-700"
                aria-label={`${label} maximum value`}
              />
            ) : null}
          </div>
        </label>
      </div>
      <div className="mt-2 flex items-center justify-between text-[11px] text-stone-400">
        <span>{resolvedMin.toFixed(precision)}</span>
        <span>{resolvedMax.toFixed(precision)}</span>
      </div>
    </div>
  );
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

function deriveColorScaleBounds(figures: Array<Record<string, unknown> | undefined>): ColorScaleBounds | null {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const figure of figures) {
    if (!figure) {
      continue;
    }
    for (const value of collectNumericColorValues(figure)) {
      if (value < min) {
        min = value;
      }
      if (value > max) {
        max = value;
      }
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

function deriveTwoSigmaColorScaleRange(
  figures: Array<Record<string, unknown> | undefined>,
  bounds: ColorScaleBounds,
): [number, number] | null {
  const values: number[] = [];
  for (const figure of figures) {
    values.push(...collectNumericColorValues(figure));
  }
  if (!values.length) {
    return null;
  }
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  if (!Number.isFinite(mean)) {
    return null;
  }
  const variance = values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / values.length;
  if (!Number.isFinite(variance) || variance < 0) {
    return null;
  }
  const sigma = Math.sqrt(variance);
  if (!Number.isFinite(sigma) || sigma <= 0) {
    return null;
  }
  const twoSigmaRange: [number, number] = [mean - 2 * sigma, mean + 2 * sigma];
  const normalized = normalizeColorScaleRange(twoSigmaRange, bounds);
  if (!Number.isFinite(normalized[0]) || !Number.isFinite(normalized[1])) {
    return null;
  }
  if (normalized[0] === normalized[1]) {
    return null;
  }
  return normalized;
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

const PYTHON_ORDINAL_UNIX_EPOCH = 719163;
const ISO_DATE_REGEX = /^\d{4}-\d{2}-\d{2}$/;

function extractTitleText(value: unknown): string {
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

function isIsoDateText(value: unknown): boolean {
  return typeof value === "string" && ISO_DATE_REGEX.test(value.trim());
}

function pythonOrdinalToIsoDate(value: number): string {
  const rounded = Math.round(value);
  const utcMs = (rounded - PYTHON_ORDINAL_UNIX_EPOCH) * 86_400_000;
  const date = new Date(utcMs);
  if (Number.isNaN(date.getTime())) {
    return String(rounded);
  }
  return date.toISOString().slice(0, 10);
}

function buildDateColorbarTicksForRange(cmin: number, cmax: number, maxTicks = 6): { tickvals: number[]; ticktext: string[] } {
  const low = Math.min(cmin, cmax);
  const high = Math.max(cmin, cmax);
  if (!Number.isFinite(low) || !Number.isFinite(high)) {
    return { tickvals: [], ticktext: [] };
  }
  const start = Math.floor(low);
  const end = Math.ceil(high);
  if (end <= start) {
    const only = [start];
    return { tickvals: only, ticktext: only.map((value) => pythonOrdinalToIsoDate(value)) };
  }

  const targetTicks = Math.max(2, Math.floor(maxTicks));
  let values: number[] = [];
  if (end - start + 1 <= targetTicks) {
    values = Array.from({ length: end - start + 1 }, (_, idx) => start + idx);
  } else {
    const raw = Array.from({ length: targetTicks }, (_, idx) => start + ((end - start) * idx) / (targetTicks - 1));
    const deduped: number[] = [];
    const seen = new Set<number>();
    for (const value of raw) {
      const rounded = Math.round(value);
      if (seen.has(rounded)) {
        continue;
      }
      seen.add(rounded);
      deduped.push(rounded);
    }
    if (!deduped.includes(start)) {
      deduped.unshift(start);
    }
    if (!deduped.includes(end)) {
      deduped.push(end);
    }
    values = deduped.sort((a, b) => a - b);
  }
  return {
    tickvals: values,
    ticktext: values.map((value) => pythonOrdinalToIsoDate(value)),
  };
}

function getColorbarRecord(container: Record<string, unknown>): Record<string, unknown> | null {
  const colorbar = container.colorbar;
  return colorbar && typeof colorbar === "object" ? (colorbar as Record<string, unknown>) : null;
}

function containerUsesDateColorbar(container: Record<string, unknown>): boolean {
  const colorbar = getColorbarRecord(container);
  if (!colorbar) {
    return false;
  }
  const title = extractTitleText(colorbar.title).trim().toLowerCase();
  if (title === "date" || title.includes("date")) {
    return true;
  }
  const ticktext = colorbar.ticktext;
  if (!Array.isArray(ticktext)) {
    return false;
  }
  return ticktext.some((value) => isIsoDateText(value));
}

function applyColorScaleRangeToFigure(
  figure: Record<string, unknown> | undefined,
  range: [number, number] | null,
): Record<string, unknown> | undefined {
  if (!figure || !range) {
    return figure;
  }
  const [cmin, cmax] = [Math.min(range[0], range[1]), Math.max(range[0], range[1])];
  const cloned = cloneFigure(figure);
  let hasColorMapping = false;
  const nextData = cloned.data.map((trace) => {
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
    const nextMarker: Record<string, unknown> = {
      ...marker,
      cauto: false,
      cmin,
      cmax,
    };
    if (containerUsesDateColorbar(marker)) {
      const { tickvals, ticktext } = buildDateColorbarTicksForRange(cmin, cmax);
      if (tickvals.length && ticktext.length) {
        const existingColorbar = getColorbarRecord(marker) ?? {};
        nextMarker.colorbar = {
          ...existingColorbar,
          tickmode: "array",
          tickvals,
          ticktext,
        };
      }
    }
    return {
      ...trace,
      marker: nextMarker,
    };
  });
  let nextLayout: Record<string, unknown> = cloned.layout;
  for (const key of Object.keys(cloned.layout)) {
    if (!key.toLowerCase().startsWith("coloraxis")) {
      continue;
    }
    const axis = cloned.layout[key];
    if (!axis || typeof axis !== "object") {
      continue;
    }
    hasColorMapping = true;
    const axisRecord = axis as Record<string, unknown>;
    let nextAxis: Record<string, unknown> = {
      ...axisRecord,
      cauto: false,
      cmin,
      cmax,
    };
    if (containerUsesDateColorbar(axisRecord)) {
      const { tickvals, ticktext } = buildDateColorbarTicksForRange(cmin, cmax);
      if (tickvals.length && ticktext.length) {
        const existingColorbar = getColorbarRecord(axisRecord) ?? {};
        nextAxis = {
          ...nextAxis,
          colorbar: {
            ...existingColorbar,
            tickmode: "array",
            tickvals,
            ticktext,
          },
        };
      }
    }
    nextLayout = {
      ...nextLayout,
      [key]: nextAxis,
    };
  }
  if (!hasColorMapping) {
    return figure;
  }
  return {
    ...cloned,
    data: nextData,
    layout: nextLayout,
  };
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

function OutlierTablesPanel({
  title,
  tables,
  renderTableControls,
}: {
  title: string;
  tables: OutlierTable[];
  renderTableControls?: (table: OutlierTable, context: { selectedRowLabels: string[] }) => ReactNode;
}) {
  const [selectedRowsByTable, setSelectedRowsByTable] = useState<Record<string, string[]>>({});

  useEffect(() => {
    setSelectedRowsByTable({});
  }, [tables]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>Backend-generated outlier tables for the current processing workspace.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {tables.length ? (
          tables.map((table, tableIndex) => {
            const tableKey = `${table.title ?? table.name}:${tableIndex}`;
            const failedSampleTable = isFailedSampleOutlierTable(table);
            const selectedRowLabels = selectedRowsByTable[tableKey] ?? [];
            return (
              <details key={table.title ?? table.name} className="rounded-lg border border-stone-200 bg-white p-3">
                <summary className="cursor-pointer text-sm font-medium text-stone-800">
                  {table.title ?? table.name} ({table.rows.length})
                </summary>
                <div className="mt-3">
                  <DataTable
                    rows={table.rows}
                    emptyLabel="No rows in this outlier category."
                    selectedRowLabels={failedSampleTable ? selectedRowLabels : undefined}
                    onSelectedRowLabelsChange={
                      failedSampleTable
                        ? (next) =>
                            setSelectedRowsByTable((current) => ({
                              ...current,
                              [tableKey]: next,
                            }))
                        : undefined
                    }
                  />
                  {renderTableControls ? (
                    <div className="mt-3">
                      {renderTableControls(table, { selectedRowLabels: failedSampleTable ? selectedRowLabels : [] })}
                    </div>
                  ) : null}
                </div>
              </details>
            );
          })
        ) : (
          <div className="rounded-lg border border-dashed border-stone-300 p-4 text-sm text-stone-500">No outlier tables returned for this scope.</div>
        )}
      </CardContent>
    </Card>
  );
}

function diagnosticsTargetPayload(target: SelectedTarget, isotopeKey?: "d13C" | "d18O") {
  return {
    target: {
      row_label: target.rowLabel,
      isotope_key: isotopeKey ?? (target.isotopeKey as "d13C" | "d18O"),
    },
  };
}

function CheckboxField({
  checked,
  label,
  description,
  onChange,
  disabled = false,
}: {
  checked: boolean;
  label: string;
  description?: string;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className={cn("flex items-start gap-3 rounded-lg border border-stone-200 p-3", disabled ? "cursor-not-allowed opacity-60" : "")}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 h-4 w-4"
      />
      <span className="space-y-1">
        <span className="block text-sm font-medium text-stone-800">{label}</span>
        {description ? <span className="block text-xs text-stone-500">{description}</span> : null}
      </span>
    </label>
  );
}

function ProcessingSummaryHero({ workspace }: { workspace: ProcessingWorkspace }) {
  if (!workspace.summary.metrics.length) {
    return null;
  }

  const summaryBadges = [
    { label: "Unique samples", value: workspace.summary.total_unique_samples },
    { label: "Measurements", value: workspace.summary.total_measurements },
    { label: "Outliers", value: workspace.summary.statistical_outliers },
    { label: "Final analyses", value: workspace.summary.final_analyses },
  ];

  return (
    <Card className="border-stone-200 bg-white/90">
      <CardHeader className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <CardTitle>Processing Summary</CardTitle>
          <CardDescription>Detailed backend metrics used to build the current workspace state.</CardDescription>
        </div>
        <div className="flex flex-wrap gap-2 text-sm text-stone-600">
          {summaryBadges.map((badge) => (
            <span key={badge.label} className="rounded-md bg-stone-50 px-3 py-1.5 ring-1 ring-stone-200">
              {badge.label}: {String(badge.value)}
            </span>
          ))}
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {workspace.summary.metrics.map((metric) => (
            <div key={metric.metric} className="rounded-lg border border-stone-200 bg-stone-50/70 px-4 py-3">
              <div className="text-xs font-semibold uppercase tracking-normal text-stone-500">{metric.metric}</div>
              <div className="mt-1 text-2xl font-semibold text-stone-900">{String(metric.value)}</div>
              <div className="mt-1 text-xs text-stone-600">{metric.details}</div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function DiagnosticsPanel({
  title,
  diagnostics,
  loading,
  displayDelta = 0,
  onPickDeltaValue,
}: {
  title: string;
  diagnostics?: CycleDiagnosticsPayload;
  loading: boolean;
  displayDelta?: number;
  onPickDeltaValue?: (value: number, valueSpace?: "raw" | "display", stdev?: number | null) => void;
}) {
  const [saturationColorAxis, setSaturationColorAxis] = useState<SaturationColorAxisKey>("mean44");
  const [saturationYAxis, setSaturationYAxis] = useState<SaturationAxisKey>("d13C");
  const cycleMean = diagnostics?.cycle_mean ?? {};
  const validMean = asNumber(cycleMean.valid_mean);
  const validStdDev = asNumber(cycleMean.valid_std_dev);
  const validCycleCount = asNumber(cycleMean.valid_cycles);
  const hasTooFewLinearityCycles = validCycleCount != null && validCycleCount < 4;
  const firstValidCycleRaw = asNumber(cycleMean.selected_value) ?? asNumber(cycleMean.mean);
  const lastValidCycleRaw = asNumber(cycleMean.last_valid_value);
  const referenceGasCorrectionRaw = asNumber(cycleMean.saturation_reference_gas_value);
  const firstCycleCorrectionRaw = asNumber(cycleMean.saturation_first_cycle_value);
  const saturationCorrection =
    diagnostics?.saturation_correction && typeof diagnostics.saturation_correction === "object"
      ? (diagnostics.saturation_correction as Record<string, unknown>)
      : {};
  const cycleLinearityValue = (key: string) => {
    const payload = saturationCorrection[key];
    return payload && typeof payload === "object" ? asNumber((payload as Record<string, unknown>).value) : null;
  };
  const cycleRelativeMismatchRaw = cycleLinearityValue("cycle_relative_mismatch");
  const cycleSymmetricMismatchRaw = cycleLinearityValue("cycle_symmetric_mismatch");
  const cycleMeanIntensityRaw = cycleLinearityValue("cycle_mean_intensity");
  const cycleIntensityWeightedMismatchRaw = cycleLinearityValue("cycle_intensity_weighted_mismatch");
  const cycleTwoTermRaw = cycleLinearityValue("cycle_two_term_mean_mismatch");
  const cyclePlateauPayload =
    saturationCorrection.cycle_plateau && typeof saturationCorrection.cycle_plateau === "object"
      ? (saturationCorrection.cycle_plateau as Record<string, unknown>)
      : {};
  const cyclePlateauRaw = asNumber(cyclePlateauPayload.value);
  const cyclePlateauStd = asNumber(cyclePlateauPayload.std_dev);
  const collectorStatus = asString((diagnostics?.target ?? {})["collector_status"]);
  const isPartiallySaturated = isPartiallySaturatedCollectorStatus(collectorStatus);
  const validMeanDisplay = validMean == null ? null : validMean + displayDelta;
  const validMeanCardValue = isPartiallySaturated ? validMean : validMeanDisplay;
  const firstValidCycleDisplay = firstValidCycleRaw == null ? null : firstValidCycleRaw + displayDelta;
  const firstValidCycleCardValue = isPartiallySaturated ? firstValidCycleRaw : firstValidCycleDisplay;
  const lastValidCycleDisplay = lastValidCycleRaw == null ? null : lastValidCycleRaw + displayDelta;
  const lastValidCycleCardValue = isPartiallySaturated ? lastValidCycleRaw : lastValidCycleDisplay;
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
    {
      key: "reference_gas_intensity",
      title: "Reference-gas saturation correction",
      description: saturationMethodDescriptions.reference_gas_intensity,
      figure:
        saturationFiguresRaw?.reference_gas_intensity && typeof saturationFiguresRaw.reference_gas_intensity === "object"
          ? (saturationFiguresRaw.reference_gas_intensity as Record<string, unknown>)
          : undefined,
    },
    {
      key: "first_cycle",
      title: "Stabilized-cycle correction",
      description: saturationMethodDescriptions.first_cycle,
      figure:
        saturationFiguresRaw?.first_cycle && typeof saturationFiguresRaw.first_cycle === "object"
          ? (saturationFiguresRaw.first_cycle as Record<string, unknown>)
          : undefined,
    },
    {
      key: "cycle_relative_mismatch",
      title: "Cycle relative mismatch correction",
      description: saturationMethodDescriptions.cycle_relative_mismatch,
      figure:
        saturationFiguresRaw?.cycle_relative_mismatch && typeof saturationFiguresRaw.cycle_relative_mismatch === "object"
          ? (saturationFiguresRaw.cycle_relative_mismatch as Record<string, unknown>)
          : undefined,
    },
    {
      key: "cycle_symmetric_mismatch",
      title: "Cycle symmetric mismatch correction",
      description: saturationMethodDescriptions.cycle_symmetric_mismatch,
      figure:
        saturationFiguresRaw?.cycle_symmetric_mismatch && typeof saturationFiguresRaw.cycle_symmetric_mismatch === "object"
          ? (saturationFiguresRaw.cycle_symmetric_mismatch as Record<string, unknown>)
          : undefined,
    },
    {
      key: "cycle_mean_intensity",
      title: "Cycle mean intensity correction",
      description: saturationMethodDescriptions.cycle_mean_intensity,
      figure:
        saturationFiguresRaw?.cycle_mean_intensity && typeof saturationFiguresRaw.cycle_mean_intensity === "object"
          ? (saturationFiguresRaw.cycle_mean_intensity as Record<string, unknown>)
          : undefined,
    },
    {
      key: "cycle_intensity_weighted_mismatch",
      title: "Cycle intensity-weighted mismatch correction",
      description: saturationMethodDescriptions.cycle_intensity_weighted_mismatch,
      figure:
        saturationFiguresRaw?.cycle_intensity_weighted_mismatch &&
        typeof saturationFiguresRaw.cycle_intensity_weighted_mismatch === "object"
          ? (saturationFiguresRaw.cycle_intensity_weighted_mismatch as Record<string, unknown>)
          : undefined,
    },
    {
      key: "cycle_two_term_mean_mismatch",
      title: "Cycle two-term mean + mismatch correction",
      description: saturationMethodDescriptions.cycle_two_term_mean_mismatch,
      figure:
        saturationFiguresRaw?.cycle_two_term_mean_mismatch &&
        typeof saturationFiguresRaw.cycle_two_term_mean_mismatch === "object"
          ? (saturationFiguresRaw.cycle_two_term_mean_mismatch as Record<string, unknown>)
          : undefined,
    },
    {
      key: "cycle_plateau",
      title: "Cycle late-plateau correction",
      description: saturationMethodDescriptions.cycle_plateau,
      figure:
        saturationFiguresRaw?.cycle_plateau && typeof saturationFiguresRaw.cycle_plateau === "object"
          ? (saturationFiguresRaw.cycle_plateau as Record<string, unknown>)
          : undefined,
    },
  ].filter((item) => item.figure);
  const suggestionCards = [
    { label: "Cycle mean", value: validMeanCardValue, stdev: validStdDev, linearity: false },
    { label: "First valid cycle", value: firstValidCycleCardValue, stdev: null, linearity: false },
    { label: "Last valid cycle", value: lastValidCycleCardValue, stdev: null, linearity: false },
    { label: "Lin. corr. to ref gas int", value: referenceGasCorrectionRaw, stdev: null, linearity: true },
    { label: "Lin. corr. to first cycle", value: firstCycleCorrectionRaw, stdev: null, linearity: true },
    { label: "Cycle relative mismatch", value: cycleRelativeMismatchRaw, stdev: null, linearity: true },
    { label: "Cycle symmetric mismatch", value: cycleSymmetricMismatchRaw, stdev: null, linearity: true },
    { label: "Cycle mean intensity", value: cycleMeanIntensityRaw, stdev: null, linearity: true },
    { label: "Cycle weighted mismatch", value: cycleIntensityWeightedMismatchRaw, stdev: null, linearity: true },
    { label: "Cycle two-term model", value: cycleTwoTermRaw, stdev: null, linearity: true },
    { label: "Cycle plateau", value: cyclePlateauRaw, stdev: cyclePlateauStd, linearity: false },
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
                const value = item.value;
                const blockedByLinearityCycleCount = item.linearity && hasTooFewLinearityCycles && value != null;
                const canPick = typeof onPickDeltaValue === "function" && value != null && !blockedByLinearityCycleCount;
                const displayValue = value == null ? "N/A" : formatDeltaValue(value);
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
                      if (canPick && value != null) {
                        onPickDeltaValue(value, "raw", item.stdev ?? null);
                      }
                    }}
                    disabled={value == null}
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

function FigureCard({
  chartKey,
  title,
  description,
  figure,
  headerActions,
  cardClassName,
  chartClassName,
  onPointClick,
  onSelection,
  onPointHover,
  onHoverEnd,
}: {
  chartKey?: string;
  title: string;
  description: string;
  figure?: Record<string, unknown>;
  headerActions?: ReactNode;
  cardClassName?: string;
  chartClassName?: string;
  onPointClick?: (points: PlotlyPoint[]) => void;
  onSelection?: (points: PlotlyPoint[]) => void;
  onPointHover?: (payload: PlotlyHoverPayload) => void;
  onHoverEnd?: () => void;
}) {
  return (
    <Card className={cardClassName}>
      <CardHeader className="gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <CardTitle className="text-base">{title}</CardTitle>
          {headerActions ? <div className="ml-auto">{headerActions}</div> : null}
        </div>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <PlotlyChart
          figure={figure}
          className={chartClassName ?? "min-h-[340px]"}
          uiRevision={chartKey ? `processing:${chartKey}` : undefined}
          onPointClick={onPointClick}
          onSelection={onSelection}
          onPointHover={onPointHover}
          onHoverEnd={onHoverEnd}
        />
      </CardContent>
    </Card>
  );
}

export default function ProcessingPage() {
  const sessionId = useSessionStore((state) => state.sessionId);
  const queryClient = useQueryClient();
  const [config, setConfig] = useState<ProcessingConfig | null>(null);
  const [sharedLinearityConfig, setSharedLinearityConfig] = useState<CalibrationConfig["linearity"] | null>(null);
  const [commentMapText, setCommentMapText] = useState("");
  const [selectedTargets, setSelectedTargets] = useState<SelectedTarget[]>([]);
  const [activeTargetIndex, setActiveTargetIndex] = useState(0);
  const [displayState, setDisplayState] = useState<DisplayStateMap>({});
  const [selectionEditorTab, setSelectionEditorTab] = useState<IsotopeKey>("d13C");
  const [singleValues, setSingleValues] = useState<IsotopeNumericMap>({ d13C: 0, d18O: 0 });
  const [singleValueSpaces, setSingleValueSpaces] = useState<Record<IsotopeKey, "raw" | "display">>({
    d13C: "raw",
    d18O: "raw",
  });
  const [singleStdevs, setSingleStdevs] = useState<Record<IsotopeKey, number | null>>({ d13C: null, d18O: null });
  const [singleOffsets, setSingleOffsets] = useState<IsotopeNumericMap>({
    d13C: SELECTION_EDITOR_DEFAULT_OFFSET,
    d18O: SELECTION_EDITOR_DEFAULT_OFFSET,
  });
  const [multiOffsetD13, setMultiOffsetD13] = useState(0);
  const [multiOffsetD18, setMultiOffsetD18] = useState(0);
  const [linearityOffsetDrafts, setLinearityOffsetDrafts] = useState<LinearityOffsetDraftState>({
    line_1_offset_d13: "0",
    line_1_offset_d18: "0",
    line_2_offset_d13: "0",
    line_2_offset_d18: "0",
  });
  const [linearityOffsetEditing, setLinearityOffsetEditing] = useState<LinearityOffsetField | null>(null);
  const [setValueHighlightNonce, setSetValueHighlightNonce] = useState(0);
  const [isSetValueInputHighlighted, setIsSetValueInputHighlighted] = useState(false);
  const [isSelectionEditorOpen, setSelectionEditorOpen] = useState(false);
  const [isExportModalOpen, setExportModalOpen] = useState(false);
  const [openSpeciesSections, setOpenSpeciesSections] = useState<Set<string>>(() => new Set());
  const [exportOutputType, setExportOutputType] = useState<"dataset" | "client_output">("dataset");
  const [duplicateCheckResult, setDuplicateCheckResult] = useState<ClientOutputDuplicateCheckResponse | null>(null);
  const [restoreStdevEnabled, setRestoreStdevEnabled] = useState(false);
  const [restoreStdevCap, setRestoreStdevCap] = useState(RESTORE_STDEV_DEFAULT_CAP);
  const [failedRestoreRate, setFailedRestoreRate] = useState(100);
  const [failedRestoreOffset, setFailedRestoreOffset] = useState(0);
  const [failedRestoreStdev, setFailedRestoreStdev] = useState(0);
  const [colorScaleRange, setColorScaleRange] = useState<[number, number] | null>(null);
  const [colorScaleRangeParam, setColorScaleRangeParam] = useState<string | null>(null);
  const [hoverPreview, setHoverPreview] = useState<HoverPreviewState | null>(null);
  const hoverPreviewHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hoverPreviewShowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingHoverPreviewRef = useRef<HoverPreviewState | null>(null);
  const colorScaleFigureCacheRef = useRef<
    WeakMap<Record<string, unknown>, { rangeKey: string; figure: Record<string, unknown> | undefined }>
  >(new WeakMap());
  const displayFigureCacheRef = useRef<WeakMap<Record<string, unknown>, Map<string, Record<string, unknown>>>>(
    new WeakMap(),
  );
  const openSpeciesSectionList = useMemo(() => Array.from(openSpeciesSections).sort(), [openSpeciesSections]);
  const openSpeciesSectionKey = useMemo(() => openSpeciesSectionList.join("||"), [openSpeciesSectionList]);

  const workspaceQuery = useQuery({
    queryKey: ["processing-workspace", sessionId, openSpeciesSectionKey],
    queryFn: () => api.getProcessingWorkspace(sessionId!, openSpeciesSectionList),
    enabled: Boolean(sessionId),
  });
  const calibrationWorkspaceQuery = useQuery({
    queryKey: ["calibration-workspace", sessionId],
    queryFn: () => api.getCalibrationWorkspace(sessionId!),
    enabled: Boolean(sessionId),
  });

  useEffect(() => {
    if (workspaceQuery.data) {
      setConfig(workspaceQuery.data.config);
    }
  }, [workspaceQuery.data]);

  useEffect(() => {
    if (calibrationWorkspaceQuery.data?.config?.linearity) {
      setSharedLinearityConfig(calibrationWorkspaceQuery.data.config.linearity);
    }
  }, [calibrationWorkspaceQuery.data]);

  useEffect(() => {
    const nextText = serializeCommentMap(config?.export.comment_map ?? {});
    setCommentMapText(nextText);
  }, [config?.export.comment_map]);

  useEffect(() => {
    const sourceLinearity = sharedLinearityConfig ?? calibrationWorkspaceQuery.data?.config.linearity;
    if (!sourceLinearity || linearityOffsetEditing) {
      return;
    }
    const nextDrafts: LinearityOffsetDraftState = {
      line_1_offset_d13: formatDecimalInput(readLinearityOffsetValue(sourceLinearity, "line_1_offset_d13")),
      line_1_offset_d18: formatDecimalInput(readLinearityOffsetValue(sourceLinearity, "line_1_offset_d18")),
      line_2_offset_d13: formatDecimalInput(readLinearityOffsetValue(sourceLinearity, "line_2_offset_d13")),
      line_2_offset_d18: formatDecimalInput(readLinearityOffsetValue(sourceLinearity, "line_2_offset_d18")),
    };
    setLinearityOffsetDrafts((current) => {
      if (
        current.line_1_offset_d13 === nextDrafts.line_1_offset_d13 &&
        current.line_1_offset_d18 === nextDrafts.line_1_offset_d18 &&
        current.line_2_offset_d13 === nextDrafts.line_2_offset_d13 &&
        current.line_2_offset_d18 === nextDrafts.line_2_offset_d18
      ) {
        return current;
      }
      return nextDrafts;
    });
  }, [calibrationWorkspaceQuery.data, linearityOffsetEditing, sharedLinearityConfig]);

  useEffect(() => {
    if (exportOutputType !== "client_output") {
      setDuplicateCheckResult(null);
    }
  }, [exportOutputType]);

  useEffect(() => {
    if (!sessionId || typeof window === "undefined") {
      return;
    }
    const raw = window.sessionStorage.getItem(`processing-display-state:${sessionId}`);
    if (raw) {
      try {
        setDisplayState(normalizeDisplayStateMap(JSON.parse(raw)));
      } catch {
        setDisplayState({});
      }
    }
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId || typeof window === "undefined") {
      return;
    }
    window.sessionStorage.setItem(`processing-display-state:${sessionId}`, JSON.stringify(displayState));
  }, [displayState, sessionId]);

  const saveConfigMutation = useMutation({
    mutationFn: (nextConfig: ProcessingConfig) => api.setProcessingConfig(sessionId!, nextConfig, openSpeciesSectionList),
    onSuccess: (workspace) => {
      queryClient.setQueryData(["processing-workspace", sessionId, openSpeciesSectionKey], workspace);
      setConfig(workspace.config);
    },
  });
  const saveSharedLinearityMutation = useMutation({
    mutationFn: (nextLinearity: CalibrationConfig["linearity"]) =>
      api.setCalibrationLinearity(
        sessionId!,
        nextLinearity,
        calibrationWorkspaceQuery.data?.config?.selected_standards ?? [],
        { summaryOnly: true },
      ),
    onSuccess: (workspace) => {
      queryClient.setQueryData<CalibrationWorkspace | undefined>(["calibration-workspace", sessionId], (current) =>
        current
          ? {
              ...current,
              config: workspace.config,
              available_values: workspace.available_values,
              precision_summaries: workspace.precision_summaries,
              selected_standard_official_values: workspace.selected_standard_official_values,
              linearity_fits: workspace.linearity_fits,
            }
          : workspace,
      );
      setSharedLinearityConfig(workspace.config.linearity);
      void queryClient.invalidateQueries({ queryKey: ["processing-workspace", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["processing-diagnostics", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["processing-diagnostics-cross-d13", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["processing-diagnostics-cross-d18", sessionId] });
    },
  });

  const editMutation = useMutation({
    mutationFn: (payload: EditAction) => api.editProcessing(sessionId!, payload, openSpeciesSectionList),
    onSuccess: (workspace) => {
      queryClient.setQueryData(["processing-workspace", sessionId, openSpeciesSectionKey], workspace);
      queryClient.invalidateQueries({ queryKey: ["processing-diagnostics", sessionId] });
      setSelectedTargets([]);
      setActiveTargetIndex(0);
      setSelectionEditorOpen(false);
    },
  });

  const resetAllMutation = useMutation({
    mutationFn: () =>
      api.editProcessing(sessionId!, {
        action: "reset_all",
        targets: [],
      }, openSpeciesSectionList),
    onSuccess: (workspace) => {
      queryClient.setQueryData(["processing-workspace", sessionId, openSpeciesSectionKey], workspace);
      setSelectedTargets([]);
      setActiveTargetIndex(0);
      setSelectionEditorOpen(false);
    },
  });

  const removeCalibrationMutation = useMutation({
    mutationFn: () => api.removeProcessingCalibration(sessionId!, openSpeciesSectionList),
    onSuccess: (workspace) => {
      queryClient.setQueryData(["processing-workspace", sessionId, openSpeciesSectionKey], workspace);
      setConfig(workspace.config);
      setSelectedTargets([]);
      setActiveTargetIndex(0);
      setSelectionEditorOpen(false);
    },
  });
  const duplicateCheckMutation = useMutation({
    mutationFn: (payload: ExportRequest) => api.checkClientOutputDuplicates(sessionId!, payload),
    onSuccess: (result) => {
      setDuplicateCheckResult(result);
    },
  });

  useEffect(() => {
    if ((!isSelectionEditorOpen && !isExportModalOpen) || typeof window === "undefined") {
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setSelectionEditorOpen(false);
        setSelectedTargets([]);
        setActiveTargetIndex(0);
        setExportModalOpen(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isSelectionEditorOpen, isExportModalOpen]);

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
    if (isSelectionEditorOpen || isExportModalOpen) {
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
  }, [isExportModalOpen, isSelectionEditorOpen]);

  useEffect(() => {
    if (!sessionId || !config || !workspaceQuery.data || saveConfigMutation.isPending) {
      return;
    }
    if (configEquals(config, workspaceQuery.data.config)) {
      return;
    }
    const timer = window.setTimeout(() => {
      saveConfigMutation.mutate(config);
    }, 500);
    return () => window.clearTimeout(timer);
  }, [config, saveConfigMutation, saveConfigMutation.isPending, sessionId, workspaceQuery.data]);

  useEffect(() => {
    if (!sessionId || !sharedLinearityConfig || !calibrationWorkspaceQuery.data || saveSharedLinearityMutation.isPending) {
      return;
    }
    if (linearityConfigEquals(sharedLinearityConfig, calibrationWorkspaceQuery.data.config.linearity)) {
      return;
    }
    const timer = window.setTimeout(() => {
      saveSharedLinearityMutation.mutate(sharedLinearityConfig);
    }, 450);
    return () => window.clearTimeout(timer);
  }, [
    calibrationWorkspaceQuery.data,
    saveSharedLinearityMutation,
    saveSharedLinearityMutation.isPending,
    sessionId,
    sharedLinearityConfig,
  ]);

  const activeTarget = selectedTargets.length ? selectedTargets[Math.min(activeTargetIndex, selectedTargets.length - 1)] : null;
  const activeSampleTarget = activeTarget;
  const hoverPreviewTarget = hoverPreview?.target ?? null;
  const hoverPreviewDiagnosticsTarget: SelectedTarget | null =
    hoverPreviewTarget == null
      ? null
      : hoverPreviewTarget.isotopeKey === "cross"
        ? { ...hoverPreviewTarget, isotopeKey: "d13C" }
        : hoverPreviewTarget;

  useEffect(() => {
    if (!setValueHighlightNonce) {
      return;
    }
    setIsSetValueInputHighlighted(true);
    const timer = window.setTimeout(() => {
      setIsSetValueInputHighlighted(false);
    }, 1400);
    return () => window.clearTimeout(timer);
  }, [setValueHighlightNonce]);

  const sampleD13DiagnosticsQuery = useQuery({
    queryKey: ["processing-diagnostics", sessionId, activeSampleTarget?.rowLabel, "d13C"],
    queryFn: () =>
      api.getProcessingCycleDiagnostics(sessionId!, diagnosticsTargetPayload(activeSampleTarget!, "d13C")),
    enabled: Boolean(sessionId && activeSampleTarget),
  });

  const sampleD18DiagnosticsQuery = useQuery({
    queryKey: ["processing-diagnostics", sessionId, activeSampleTarget?.rowLabel, "d18O"],
    queryFn: () =>
      api.getProcessingCycleDiagnostics(sessionId!, diagnosticsTargetPayload(activeSampleTarget!, "d18O")),
    enabled: Boolean(sessionId && activeSampleTarget),
  });

  const hoverDiagnosticsQuery = useQuery({
    queryKey: [
      "processing-diagnostics-hover",
      sessionId,
      hoverPreviewDiagnosticsTarget?.rowLabel,
      hoverPreviewDiagnosticsTarget?.isotopeKey,
    ],
    queryFn: () =>
      api.getProcessingCycleDiagnostics(
        sessionId!,
        diagnosticsTargetPayload(
          hoverPreviewDiagnosticsTarget!,
          hoverPreviewDiagnosticsTarget!.isotopeKey as "d13C" | "d18O",
        ),
      ),
    enabled: Boolean(sessionId && hoverPreviewDiagnosticsTarget && !isSelectionEditorOpen && !isExportModalOpen),
    staleTime: 60_000,
  });

  useEffect(() => {
    if (!activeSampleTarget) {
      return;
    }
    setSelectionEditorTab(activeSampleTarget.isotopeKey === "d18O" ? "d18O" : "d13C");
    setSingleOffsets({ d13C: SELECTION_EDITOR_DEFAULT_OFFSET, d18O: SELECTION_EDITOR_DEFAULT_OFFSET });
    setSingleValueSpaces({ d13C: "raw", d18O: "raw" });
    setSingleStdevs({ d13C: null, d18O: null });
  }, [activeSampleTarget?.rowLabel, activeSampleTarget?.isotopeKey]);

  useEffect(() => {
    if (!activeSampleTarget) {
      return;
    }
    const activeRowLabel = String(activeSampleTarget.rowLabel).trim();
    const selectedD13 = selectedTargetPointValue(activeSampleTarget, "d13C");
    const selectedD18 = selectedTargetPointValue(activeSampleTarget, "d18O");
    const d13Target = sampleD13DiagnosticsQuery.data?.target ?? {};
    const d18Target = sampleD18DiagnosticsQuery.data?.target ?? {};
    const d13MatchesActiveRow = asString(d13Target["row_label"]).trim() === activeRowLabel;
    const d18MatchesActiveRow = asString(d18Target["row_label"]).trim() === activeRowLabel;
    const d13Status = d13MatchesActiveRow ? asString(d13Target["collector_status"]).trim() : "";
    const d18Status = d18MatchesActiveRow ? asString(d18Target["collector_status"]).trim() : "";
    const d13Current = d13MatchesActiveRow ? asNumber(d13Target["current_value"]) : null;
    const d18Current = d18MatchesActiveRow ? asNumber(d18Target["current_value"]) : null;
    const d13SelectedCycleValue = asNumber((sampleD13DiagnosticsQuery.data?.cycle_mean ?? {})["selected_value"]);
    const d18SelectedCycleValue = asNumber((sampleD18DiagnosticsQuery.data?.cycle_mean ?? {})["selected_value"]);
    const d13SeedRawValue = d13Current ?? (isPartiallySaturatedCollectorStatus(d13Status) ? d13SelectedCycleValue : null);
    const d18SeedRawValue = d18Current ?? (isPartiallySaturatedCollectorStatus(d18Status) ? d18SelectedCycleValue : null);
    const nextValues: IsotopeNumericMap = {
      d13C: roundDeltaValue(
        d13SeedRawValue != null ? d13SeedRawValue : selectedD13 ?? fallbackTargetValue(activeSampleTarget, "d13C"),
      ),
      d18O: roundDeltaValue(
        d18SeedRawValue != null ? d18SeedRawValue : selectedD18 ?? fallbackTargetValue(activeSampleTarget, "d18O"),
      ),
    };
    setSingleValues(nextValues);
    setSingleValueSpaces({ d13C: "raw", d18O: "raw" });
    setSingleStdevs({ d13C: null, d18O: null });
  }, [
    activeSampleTarget,
    sampleD13DiagnosticsQuery.data?.target,
    sampleD13DiagnosticsQuery.data?.cycle_mean,
    sampleD18DiagnosticsQuery.data?.target,
    sampleD18DiagnosticsQuery.data?.cycle_mean,
  ]);

  const workspace = workspaceQuery.data;
  const activeConfig = config ?? workspace?.config ?? null;
  const colorScaleFigures = useMemo<Array<Record<string, unknown> | undefined>>(() => {
    if (!workspace) {
      return [];
    }
    const figures: Array<Record<string, unknown> | undefined> = [
      workspace.overview_figures.processing_3d,
      workspace.overview_figures.crossplot,
      workspace.overview_figures.d13_summary,
      workspace.overview_figures.d18_summary,
    ];
    for (const section of workspace.species_sections) {
      if (!openSpeciesSections.has(section.species)) {
        continue;
      }
      for (const figureSet of section.identifier_figures) {
        figures.push(figureSet.d13c, figureSet.d18o);
      }
    }
    return figures;
  }, [openSpeciesSections, workspace]);
  const colorScaleBounds = useMemo(() => deriveColorScaleBounds(colorScaleFigures), [colorScaleFigures]);
  const colorScaleTwoSigmaRange = useMemo(() => {
    if (!colorScaleBounds) {
      return null;
    }
    return deriveTwoSigmaColorScaleRange(colorScaleFigures, colorScaleBounds);
  }, [colorScaleFigures, colorScaleBounds]);

  useEffect(() => {
    if (!activeConfig || !colorScaleBounds) {
      return;
    }
    const bounds = colorScaleBounds;
    const param = activeConfig.color_param;
    const fullRange: [number, number] = [bounds.min, bounds.max];
    const defaultRange = colorScaleTwoSigmaRange ?? fullRange;
    const parameterChanged = colorScaleRangeParam !== param;
    setColorScaleRange((current) => {
      if (!current || parameterChanged) {
        return defaultRange;
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
      setColorScaleRangeParam(param);
    }
  }, [activeConfig, colorScaleBounds, colorScaleRangeParam, colorScaleTwoSigmaRange]);

  const colorSliderBounds: ColorScaleBounds = colorScaleBounds ?? { min: 0, max: 1 };
  const effectiveColorScaleRange = normalizeColorScaleRange(
    colorScaleRange ?? colorScaleTwoSigmaRange ?? [colorSliderBounds.min, colorSliderBounds.max],
    colorSliderBounds,
  );
  const colorScaleRangeKey = effectiveColorScaleRange
    ? `${effectiveColorScaleRange[0]}:${effectiveColorScaleRange[1]}`
    : "none";
  const withColorScaleRange = useMemo(() => {
    const range = effectiveColorScaleRange;
    const rangeKey = colorScaleRangeKey;
    const cache = colorScaleFigureCacheRef.current;
    return (figure: Record<string, unknown> | undefined) => {
      if (!figure || !range) {
        return figure;
      }
      const cached = cache.get(figure);
      if (cached?.rangeKey === rangeKey) {
        return cached.figure;
      }
      const nextFigure = applyColorScaleRangeToFigure(figure, range);
      cache.set(figure, { rangeKey, figure: nextFigure });
      return nextFigure;
    };
  }, [colorScaleRangeKey]);
  const withDisplayState = useMemo(() => {
    const cache = displayFigureCacheRef.current;
    return (figure: Record<string, unknown> | undefined, state: ChartDisplayState) => {
      if (!figure) {
        return applyDisplayState(figure, state);
      }
      const stateKey = chartDisplayStateKey(state);
      let stateCache = cache.get(figure);
      if (!stateCache) {
        stateCache = new Map<string, Record<string, unknown>>();
        cache.set(figure, stateCache);
      }
      const cached = stateCache.get(stateKey);
      if (cached) {
        return cached;
      }
      const nextFigure = applyDisplayState(figure, state);
      stateCache.set(stateKey, nextFigure);
      return nextFigure;
    };
  }, []);

  useEffect(() => {
    if (!sessionId || typeof window === "undefined" || !workspaceQuery.data) {
      return;
    }
    const storageKey = `processing-selection:${sessionId}`;
    const raw = window.sessionStorage.getItem(storageKey);
    if (!raw) {
      return;
    }
    window.sessionStorage.removeItem(storageKey);
    try {
      const parsed = JSON.parse(raw) as { targets?: unknown };
      const rawTargets = Array.isArray(parsed?.targets) ? parsed.targets : [];
      const targets = rawTargets.map(coerceStoredSelectedTarget).filter((item): item is SelectedTarget => item != null);
      if (targets.length) {
        setTargets(targets);
      }
    } catch {
      // Ignore invalid payloads and continue loading the page.
    }
  }, [sessionId, workspaceQuery.data]);

  function coerceSequenceNeighbor(
    value: unknown,
    isotopeKey: IsotopeKey,
  ): { rowLabel: string; identifier2: string; value: number | null; isotopeKey: IsotopeKey } | null {
    if (!value || typeof value !== "object") {
      return null;
    }
    const payload = value as Record<string, unknown>;
    const rowLabel = asString(payload.row_label ?? payload.rowLabel).trim();
    if (!rowLabel) {
      return null;
    }
    return {
      rowLabel,
      identifier2: asString(payload.identifier_2 ?? payload.identifier2).trim(),
      value: asNumber(payload.value),
      isotopeKey,
    };
  }

  const sequenceNavigationIsotopeKey: IsotopeKey | null =
    activeTarget?.isotopeKey === "d13C" || activeTarget?.isotopeKey === "d18O" ? activeTarget.isotopeKey : activeTarget ? selectionEditorTab : null;
  const sequenceNavigationCycleMean =
    sequenceNavigationIsotopeKey == null
      ? null
      : ((sequenceNavigationIsotopeKey === "d13C" ? sampleD13DiagnosticsQuery.data?.cycle_mean : sampleD18DiagnosticsQuery.data?.cycle_mean) ?? null);
  const prevSequenceNeighbor =
    sequenceNavigationIsotopeKey == null
      ? null
      : coerceSequenceNeighbor(
          (sequenceNavigationCycleMean as Record<string, unknown> | null | undefined)?.prev_neighbor,
          sequenceNavigationIsotopeKey,
        );
  const nextSequenceNeighbor =
    sequenceNavigationIsotopeKey == null
      ? null
      : coerceSequenceNeighbor(
          (sequenceNavigationCycleMean as Record<string, unknown> | null | undefined)?.next_neighbor,
          sequenceNavigationIsotopeKey,
        );
  const useSelectionIndexNavigation = selectedTargets.length > 1;
  const canMoveToPrevTarget = useSelectionIndexNavigation ? activeTargetIndex > 0 : Boolean(prevSequenceNeighbor);
  const canMoveToNextTarget = useSelectionIndexNavigation ? activeTargetIndex < selectedTargets.length - 1 : Boolean(nextSequenceNeighbor);

  function moveSelectionTarget(direction: "prev" | "next") {
    if (useSelectionIndexNavigation) {
      setActiveTargetIndex((index) =>
        direction === "prev" ? Math.max(0, index - 1) : Math.min(selectedTargets.length - 1, index + 1),
      );
      return;
    }
    if (!activeTarget) {
      return;
    }
    const neighbor = direction === "prev" ? prevSequenceNeighbor : nextSequenceNeighbor;
    if (!neighbor) {
      return;
    }
    const nextTarget: SelectedTarget = {
      ...activeTarget,
      rowLabel: neighbor.rowLabel,
      identifier2: neighbor.identifier2 || activeTarget.identifier2,
      currentValue: activeTarget.isotopeKey === "cross" ? null : neighbor.value,
      currentD13:
        activeTarget.isotopeKey === "cross" ? (neighbor.isotopeKey === "d13C" ? neighbor.value : null) : activeTarget.currentD13,
      currentD18:
        activeTarget.isotopeKey === "cross" ? (neighbor.isotopeKey === "d18O" ? neighbor.value : null) : activeTarget.currentD18,
    };
    setTargets([nextTarget]);
  }

  function setTargets(nextTargets: SelectedTarget[]) {
    const shouldOpen = nextTargets.length > 0;
    setSelectedTargets((current) => (areSameSelectionTargets(current, nextTargets) ? current : nextTargets));
    setActiveTargetIndex((current) => (current === 0 ? current : 0));
    setSelectionEditorOpen((current) => (current === shouldOpen ? current : shouldOpen));
  }

  function closeSelectionEditor() {
    setSelectionEditorOpen(false);
    setSelectedTargets([]);
    setActiveTargetIndex(0);
  }

  function setSpeciesSectionOpen(species: string, open: boolean) {
    setOpenSpeciesSections((current) => {
      if (current.has(species) === open) {
        return current;
      }
      const next = new Set(current);
      if (open) {
        next.add(species);
      } else {
        next.delete(species);
      }
      return next;
    });
  }

  function updateConfig<T extends keyof ProcessingConfig>(key: T, value: ProcessingConfig[T]) {
    setConfig((current) => (current ? { ...current, [key]: value } : current));
  }

  function updateSaturationMethod(isotopeKey: IsotopeKey, value: SaturationCorrectionMethod) {
    setConfig((current) => {
      if (!current) {
        return current;
      }
      return isotopeKey === "d13C"
        ? { ...current, saturation_correction_method: value, saturation_correction_method_d13: value }
        : { ...current, saturation_correction_method_d18: value };
    });
  }

  function updateOverlay(key: keyof ProcessingConfig["overlays"], value: boolean) {
    setConfig((current) =>
      current
        ? {
            ...current,
            overlays: {
              ...current.overlays,
              [key]: value,
            },
          }
        : current,
    );
  }

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

  function handleLinearityOffsetDraftChange(field: LinearityOffsetField, rawValue: string) {
    setLinearityOffsetEditing(field);
    setLinearityOffsetDrafts((current) => ({ ...current, [field]: rawValue }));
    const parsed = parseDecimalInput(rawValue);
    if (parsed == null) {
      return;
    }
    updateSharedLinearity(field, parsed);
  }

  function resetLinearityOffsetDraft(field: LinearityOffsetField) {
    const sourceLinearity = sharedLinearityConfig ?? calibrationWorkspaceQuery.data?.config.linearity;
    if (!sourceLinearity) {
      return;
    }
    const value = readLinearityOffsetValue(sourceLinearity, field);
    setLinearityOffsetDrafts((current) => ({ ...current, [field]: formatDecimalInput(value) }));
  }

  function commitLinearityOffsetDraft(field: LinearityOffsetField) {
    const parsed = parseDecimalInput(linearityOffsetDrafts[field]);
    if (parsed == null) {
      resetLinearityOffsetDraft(field);
      setLinearityOffsetEditing((current) => (current === field ? null : current));
      return;
    }
    updateSharedLinearity(field, parsed);
    setLinearityOffsetDrafts((current) => ({ ...current, [field]: formatDecimalInput(parsed) }));
    setLinearityOffsetEditing((current) => (current === field ? null : current));
  }

  function handleLinearityOffsetKeyDown(event: ReactKeyboardEvent<HTMLInputElement>, field: LinearityOffsetField) {
    if (event.key === "Enter") {
      event.preventDefault();
      commitLinearityOffsetDraft(field);
      event.currentTarget.blur();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      resetLinearityOffsetDraft(field);
      setLinearityOffsetEditing((current) => (current === field ? null : current));
      event.currentTarget.blur();
    }
  }

  function updateExport(
    key: keyof ProcessingConfig["export"],
    value: ProcessingConfig["export"][keyof ProcessingConfig["export"]],
  ) {
    setConfig((current) =>
      current
        ? {
            ...current,
            export: {
              ...current.export,
              [key]: value,
            },
          }
        : current,
    );
  }

  function updateChartDisplayState(key: string, patch: Partial<ChartDisplayState>) {
    setDisplayState((current) => ({
      ...current,
      [key]: normalizeDisplayState({ ...normalizeDisplayState(current[key]), ...patch }),
    }));
  }

  function rawToDisplayDelta(isotopeKey: IsotopeKey): number {
    const selectedDisplayValue = selectedTargetPointValue(activeSampleTarget, isotopeKey);
    const diagnosticsCurrentValue =
      isotopeKey === "d13C"
        ? asNumber((sampleD13DiagnosticsQuery.data?.target ?? {})["current_value"])
        : asNumber((sampleD18DiagnosticsQuery.data?.target ?? {})["current_value"]);
    if (selectedDisplayValue == null || diagnosticsCurrentValue == null) {
      return 0;
    }
    return selectedDisplayValue - diagnosticsCurrentValue;
  }

  function setSingleValueFromSuggestion(
    isotopeKey: IsotopeKey,
    value: number,
    valueSpace: "raw" | "display" = "raw",
    stdev: number | null = null,
  ) {
    setSelectionEditorTab(isotopeKey);
    setSingleValues((current) => ({ ...current, [isotopeKey]: roundDeltaValue(value) }));
    setSingleValueSpaces((current) => ({ ...current, [isotopeKey]: valueSpace }));
    setSingleStdevs((current) => ({ ...current, [isotopeKey]: stdev }));
    setSetValueHighlightNonce((current) => current + 1);
  }

  function resolveSetValuePayload(
    isotopeKey: IsotopeKey,
    requestedValue: number,
    valueSpace: "raw" | "display",
  ): number {
    void isotopeKey;
    void valueSpace;
    return requestedValue;
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

  function handleChartPointHover(chartKey: string, payload: PlotlyHoverPayload) {
    if (isSelectionEditorOpen || isExportModalOpen) {
      return;
    }
    const targets = parseSelectedTargets(payload.points, chartKey);
    if (!targets.length) {
      clearHoverPreviewShowTimer();
      pendingHoverPreviewRef.current = null;
      setHoverPreview(null);
      return;
    }
    clearHoverPreviewHideTimer();
    const firstTarget = targets[0];
    const normalizedTarget =
      firstTarget.isotopeKey === "cross"
        ? ({
            ...firstTarget,
            isotopeKey: "d13C",
          } as SelectedTarget)
        : firstTarget;
    pendingHoverPreviewRef.current = {
      target: normalizedTarget,
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

  function chartHoverProps(chartKey: string) {
    return {
      onPointHover: (payload: PlotlyHoverPayload) => handleChartPointHover(chartKey, payload),
      onHoverEnd: scheduleHoverPreviewHide,
    };
  }

  function handleChartClick(chartKey: string, points: PlotlyPoint[]) {
    const targets = parseSelectedTargets(points, chartKey);
    if (targets.length) {
      setTargets(targets.slice(0, 1));
    }
  }

  function handleChartSelection(chartKey: string, points: PlotlyPoint[]) {
    const targets = parseSelectedTargets(points, chartKey);
    if (targets.length) {
      setTargets(targets);
    }
  }

  function handleSelectionSourceChartClick(chartKey: string, points: PlotlyPoint[]) {
    const targets = parseSelectedTargets(points, chartKey);
    if (targets.length) {
      setTargets(targets.slice(0, 1));
    }
  }

  function handleSelectionSourceChartSelection(chartKey: string, points: PlotlyPoint[]) {
    const targets = parseSelectedTargets(points, chartKey);
    if (targets.length > 1) {
      setTargets(targets);
    }
  }

  function buildTargetsForAction(selection: SelectedTarget[], isotopeKey?: "d13C" | "d18O") {
    const targets: Array<{ row_label: string; isotope_key: "d13C" | "d18O" }> = [];
    const seen = new Set<string>();
    for (const target of selection) {
      if (target.isotopeKey === "cross") {
        if (!isotopeKey) {
          for (const iso of ["d13C", "d18O"] as const) {
            const token = `${iso}|${target.rowLabel}`;
            if (!seen.has(token)) {
              seen.add(token);
              targets.push({ row_label: target.rowLabel, isotope_key: iso });
            }
          }
        } else {
          const token = `${isotopeKey}|${target.rowLabel}`;
          if (!seen.has(token)) {
            seen.add(token);
            targets.push({ row_label: target.rowLabel, isotope_key: isotopeKey });
          }
        }
        continue;
      }
      const iso = isotopeKey ?? target.isotopeKey;
      const token = `${iso}|${target.rowLabel}`;
      if (!seen.has(token)) {
        seen.add(token);
        targets.push({ row_label: target.rowLabel, isotope_key: iso as "d13C" | "d18O" });
      }
    }
    return targets;
  }

  async function applyConfig() {
    if (!activeConfig) {
      return;
    }
    await saveConfigMutation.mutateAsync(activeConfig);
  }

  function buildExportRequestPayload(outputType: "dataset" | "client_output"): ExportRequest {
    return {
      ...activeConfig!.export,
      output_type: outputType,
      restore_stdev: outputType === "client_output" ? restoreStdevEnabled : false,
      restore_stdev_cap:
        outputType === "client_output"
          ? Math.min(RESTORE_STDEV_DEFAULT_CAP, Math.max(0, restoreStdevCap))
          : RESTORE_STDEV_DEFAULT_CAP,
    };
  }

  async function handleExport(outputType: "dataset" | "client_output") {
    if (!sessionId || !activeConfig) {
      return;
    }
    await saveConfigMutation.mutateAsync(activeConfig);
    const { blob, filename } = await api.exportDataset(sessionId, buildExportRequestPayload(outputType));
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download =
      filename ??
      (outputType === "client_output" ? "client_output.xlsx" : workspace?.export_state.filename ?? "dataset.xlsx");
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    setExportModalOpen(false);
  }

  async function handleDuplicateCheck() {
    if (!sessionId || !activeConfig) {
      return;
    }
    duplicateCheckMutation.reset();
    setDuplicateCheckResult(null);
    await saveConfigMutation.mutateAsync(activeConfig);
    await duplicateCheckMutation.mutateAsync(buildExportRequestPayload("client_output"));
  }

  async function applySingleValue(isotopeKey: IsotopeKey) {
    if (!sessionId || !activeSampleTarget) {
      return;
    }
    const payloadValue = resolveSetValuePayload(isotopeKey, singleValues[isotopeKey], singleValueSpaces[isotopeKey]);
    await editMutation.mutateAsync({
      action: "set_value",
      targets: [{ row_label: activeSampleTarget.rowLabel, isotope_key: isotopeKey }],
      value: payloadValue,
      stdev: singleStdevs[isotopeKey],
    });
  }

  async function applySingleOffset(isotopeKey: IsotopeKey) {
    if (!sessionId || !activeSampleTarget) {
      return;
    }
    await editMutation.mutateAsync({
      action: "offset",
      targets: [{ row_label: activeSampleTarget.rowLabel, isotope_key: isotopeKey }],
      offset: singleOffsets[isotopeKey],
    });
  }

  async function applySingleInterpolate(isotopeKey: IsotopeKey) {
    if (!sessionId || !activeSampleTarget) {
      return;
    }
    const activeRowLabel = String(activeSampleTarget.rowLabel).trim();
    const d13Target = sampleD13DiagnosticsQuery.data?.target ?? {};
    const d18Target = sampleD18DiagnosticsQuery.data?.target ?? {};
    const d13Status =
      asString(d13Target["row_label"]).trim() === activeRowLabel
        ? asString(d13Target["collector_status"]).trim()
        : "";
    const d18Status =
      asString(d18Target["row_label"]).trim() === activeRowLabel
        ? asString(d18Target["collector_status"]).trim()
        : "";
    const interpolateBothIsotopes =
      isFailedSampleCollectorStatus(d13Status) || isFailedSampleCollectorStatus(d18Status);
    await editMutation.mutateAsync({
      action: "interpolate",
      targets: interpolateBothIsotopes
        ? [
            { row_label: activeSampleTarget.rowLabel, isotope_key: "d13C" as const },
            { row_label: activeSampleTarget.rowLabel, isotope_key: "d18O" as const },
          ]
        : [{ row_label: activeSampleTarget.rowLabel, isotope_key: isotopeKey }],
      offset: singleOffsets[isotopeKey],
    });
  }

  async function applyMultiOffset(isotopeKey: "d13C" | "d18O", offset: number) {
    if (!sessionId) {
      return;
    }
    const targets = buildTargetsForAction(selectedTargets, isotopeKey);
    if (!targets.length) {
      return;
    }
    await editMutation.mutateAsync({
      action: "offset",
      targets,
      offset,
    });
  }

  async function applyMultiInterpolate() {
    if (!sessionId || !selectedTargets.length) {
      return;
    }
    await editMutation.mutateAsync({
      action: "interpolate",
      targets: buildTargetsForAction(selectedTargets),
    });
  }

  function buildRandomFailedSampleTargets(rows: Array<Record<string, unknown>>, ratePercent: number) {
    const uniqueRowLabels = Array.from(
      new Set(rows.map((row) => extractOutlierRowLabel(row)).filter((rowLabel): rowLabel is string => rowLabel != null && rowLabel !== "")),
    );
    if (!uniqueRowLabels.length) {
      return [];
    }
    const clampedRate = clampNumber(ratePercent, 0, 100);
    if (clampedRate <= 0) {
      return [];
    }
    const selectedCount = Math.min(uniqueRowLabels.length, Math.max(1, Math.ceil((uniqueRowLabels.length * clampedRate) / 100)));
    const sampledRows = pickRandomSubset(uniqueRowLabels, selectedCount);
    return sampledRows.flatMap((rowLabel) => [
      { row_label: rowLabel, isotope_key: "d13C" as const },
      { row_label: rowLabel, isotope_key: "d18O" as const },
    ]);
  }

  function buildExplicitFailedSampleTargets(rowLabels: string[]) {
    const uniqueRowLabels = Array.from(new Set(rowLabels.map((rowLabel) => rowLabel.trim()).filter(Boolean)));
    return uniqueRowLabels.flatMap((rowLabel) => [
      { row_label: rowLabel, isotope_key: "d13C" as const },
      { row_label: rowLabel, isotope_key: "d18O" as const },
    ]);
  }

  async function restoreFailedSamples(table: OutlierTable, selectedRowLabels: string[] = []) {
    if (!sessionId) {
      return;
    }
    const targets = selectedRowLabels.length
      ? buildExplicitFailedSampleTargets(selectedRowLabels)
      : buildRandomFailedSampleTargets(table.rows, failedRestoreRate);
    if (!targets.length) {
      return;
    }
    await editMutation.mutateAsync({
      action: "interpolate",
      targets,
      offset: Number.isFinite(failedRestoreOffset) ? failedRestoreOffset : 0,
      stdev: Number.isFinite(failedRestoreStdev) ? failedRestoreStdev : 0,
    });
  }

  async function applyOutlierOverride(isOutlier: boolean) {
    if (!sessionId || !activeTarget) {
      return;
    }
    await editMutation.mutateAsync({
      action: "set_outlier_override",
      targets: buildTargetsForAction([activeTarget], activeTarget.isotopeKey === "cross" ? "d13C" : undefined),
      is_outlier: isOutlier,
    });
  }

  async function resetManualOutliers() {
    if (!sessionId || !workspace) {
      return;
    }
    const overriddenRows = Object.keys(workspace.edit_state.manual_outlier_overrides ?? {});
    if (!overriddenRows.length) {
      return;
    }
    await editMutation.mutateAsync({
      action: "set_outlier_override",
      targets: overriddenRows.map((rowLabel) => ({ row_label: rowLabel, isotope_key: "d13C" as const })),
      is_outlier: false,
    });
  }

  async function resetSelected() {
    if (!sessionId || !selectedTargets.length) {
      return;
    }
    await editMutation.mutateAsync({
      action: "reset_to_original",
      targets: buildTargetsForAction(selectedTargets),
    });
  }

  if (!sessionId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>No Active Session</CardTitle>
          <CardDescription>Import data first to open the processing workspace.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (workspaceQuery.isLoading && !workspace) {
    return <div className="text-sm text-stone-500">Loading processing workspace...</div>;
  }

  if (workspaceQuery.error) {
    return <div className="text-sm text-red-600">Failed to load processing workspace.</div>;
  }

  if (!workspace || !activeConfig) {
    return null;
  }

  const busy =
    saveConfigMutation.isPending ||
    saveSharedLinearityMutation.isPending ||
    editMutation.isPending ||
    resetAllMutation.isPending ||
    removeCalibrationMutation.isPending ||
    duplicateCheckMutation.isPending;
  const activeLinearity = sharedLinearityConfig ?? calibrationWorkspaceQuery.data?.config.linearity ?? null;
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
  const d13FitSlope = asNumber(d13Fit.slope);
  const d18FitSlope = asNumber(d18Fit.slope);
  const d13FitQuad = asNumber(d13Fit.quad);
  const d18FitQuad = asNumber(d18Fit.quad);
  const selectedStandards = calibrationWorkspaceQuery.data?.config.selected_standards ?? [];
  const standardPrecisionRows = (calibrationWorkspaceQuery.data?.precision_summaries ?? [])
    .filter((summary) => selectedStandards.includes(summary.standard))
    .slice(0, 6);
  const coefficientOffsetEnabled = activeLinearity
    ? [
        Number(activeLinearity.manual_d13_per_10v ?? 0),
        Number(activeLinearity.manual_d18_per_10v ?? 0),
        ...(activeLinearity.quadratic || isTwoTermLinearityBasis
          ? [Number(activeLinearity.manual_d13_per_10v2 ?? 0), Number(activeLinearity.manual_d18_per_10v2 ?? 0)]
          : []),
      ].some((offset) => Number.isFinite(offset) && Math.abs(offset) > 1e-12)
    : false;
  const renderFailedSampleTableControls = (table: OutlierTable, context: { selectedRowLabels: string[] }) => {
    const isFailedSampleTable = isFailedSampleOutlierTable(table);
    if (!isFailedSampleTable) {
      return null;
    }
    const selectedRowLabels = context.selectedRowLabels ?? [];
    const hasSelectedRows = selectedRowLabels.length > 0;
    const restoreDisabled = busy || (!hasSelectedRows && (!table.rows.length || clampNumber(failedRestoreRate, 0, 100) <= 0));
    return (
      <div className="flex flex-wrap items-end gap-2 rounded-lg border border-stone-200 bg-stone-50 p-3">
        <label className="text-sm">
          <span className="mb-1 block text-stone-700">Rate (%)</span>
          <input
            type="number"
            min={0}
            max={100}
            step={1}
            value={failedRestoreRate}
            onChange={(event) => setFailedRestoreRate(clampNumber(parseFinite(event.target.value, 0), 0, 100))}
            disabled={hasSelectedRows}
            className={cn(
              "w-28 rounded-lg border border-stone-300 px-3 py-2",
              hasSelectedRows ? "cursor-not-allowed bg-stone-100 text-stone-500" : "",
            )}
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-stone-700">Offset</span>
          <input
            type="number"
            step="0.001"
            value={failedRestoreOffset}
            onChange={(event) => setFailedRestoreOffset(parseFinite(event.target.value, 0))}
            className="w-28 rounded-lg border border-stone-300 px-3 py-2"
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-stone-700">Stdev</span>
          <input
            type="number"
            min={0}
            step="0.001"
            value={failedRestoreStdev}
            onChange={(event) => setFailedRestoreStdev(Math.max(0, parseFinite(event.target.value, 0)))}
            className="w-28 rounded-lg border border-stone-300 px-3 py-2"
          />
        </label>
        <Button
          onClick={() => restoreFailedSamples(table, selectedRowLabels)}
          disabled={restoreDisabled}
        >
          Restore
        </Button>
        <Button variant="outline" onClick={() => resetAllMutation.mutate()} disabled={busy}>
          Reset
        </Button>
        {hasSelectedRows ? <div className="text-xs text-stone-500">{selectedRowLabels.length} row(s) selected for restore.</div> : null}
      </div>
    );
  };
  const manualOverrideCount = Object.keys(workspace.edit_state.manual_outlier_overrides ?? {}).length;
  const selectedRowLabels = selectedTargets.map((target) => `${target.rowLabel}:${target.isotopeKey}`);
  const hoverPreviewPosition = hoverPreview ? computeHoverPreviewPosition(hoverPreview.clientX, hoverPreview.clientY, 560, 560) : null;
  const hoverDiagnosticsFigure = compactHoverDiagnosticsFigure(
    ensureCollectorIntensityTraces(hoverDiagnosticsQuery.data?.figure, hoverDiagnosticsQuery.data?.table ?? []),
  );
  const hasHoverDiagnosticsFigureData = Boolean(
    hoverDiagnosticsFigure &&
      Array.isArray((hoverDiagnosticsFigure as FigureShape).data) &&
      ((hoverDiagnosticsFigure as FigureShape).data as Array<Record<string, unknown>>).length > 0,
  );
  const shouldShowHoverPreview =
    Boolean(hoverPreview) &&
    !isSelectionEditorOpen &&
    !isExportModalOpen &&
    hoverPreviewPosition != null;
  const diagnosticsByIsotope: Record<IsotopeKey, CycleDiagnosticsPayload | undefined> = {
    d13C: sampleD13DiagnosticsQuery.data,
    d18O: sampleD18DiagnosticsQuery.data,
  };
  const activeDiagnostics = diagnosticsByIsotope[selectionEditorTab];
  const activeDiagnosticsLoading =
    selectionEditorTab === "d13C" ? sampleD13DiagnosticsQuery.isLoading : sampleD18DiagnosticsQuery.isLoading;
  const selectedPointD13 = selectedTargetPointValue(activeSampleTarget, "d13C");
  const selectedPointD18 = selectedTargetPointValue(activeSampleTarget, "d18O");
  const activeTargetDiagnostics = (sampleD18DiagnosticsQuery.data ?? sampleD13DiagnosticsQuery.data) ?? null;
  const activeTargetInlineSummary = activeTargetDiagnostics?.inline_summary;
  const activeTargetSourceExcel = asString(activeTargetDiagnostics?.target?.source_excel).trim() || "Unknown";
  const activeRowLabel = activeSampleTarget ? String(activeSampleTarget.rowLabel).trim() : "";
  const parsedActiveTargetInlineItems = parseInlineDiagnosticsSummary(activeTargetInlineSummary);
  const hasInlineExcelProvenance = parsedActiveTargetInlineItems.some((item) => {
    const normalized = normalizeInlineLabel(item.label);
    return normalized === "excel file" || normalized === "original excel file" || normalized === "source excel";
  });
  const activeTargetInlineItems = hasInlineExcelProvenance
    ? parsedActiveTargetInlineItems
    : [...parsedActiveTargetInlineItems, { label: "Original Excel File", value: activeTargetSourceExcel }];
  const activeTargetInlineDisplayItems = activeTargetInlineItems.map((item) => {
    const numericValue = parseStrictNumber(item.value);
    const isDelta = isDeltaInlineLabel(item.label);
    const normalizedLabel = normalizeInlineLabel(item.label);
    const settableIsotope: IsotopeKey | null =
      normalizedLabel === "d13c values" ? "d13C" : normalizedLabel === "d18o values" ? "d18O" : null;
    const isotopeTargetPayload =
      settableIsotope === "d13C"
        ? (sampleD13DiagnosticsQuery.data?.target as Record<string, unknown> | undefined)
        : settableIsotope === "d18O"
          ? (sampleD18DiagnosticsQuery.data?.target as Record<string, unknown> | undefined)
          : undefined;
    const diagnosticsCurrentValue =
      settableIsotope != null &&
      isotopeTargetPayload != null &&
      asString(isotopeTargetPayload["row_label"]).trim() === activeRowLabel
        ? asNumber(isotopeTargetPayload["current_value"])
        : null;
    const selectedPointValue =
      normalizedLabel === "d13c values" ? selectedPointD13 : normalizedLabel === "d18o values" ? selectedPointD18 : null;
    const resolvedNumericValue = diagnosticsCurrentValue ?? numericValue ?? selectedPointValue;
    const canSetSingleValue = Boolean(activeSampleTarget) && settableIsotope != null && resolvedNumericValue != null;
    return {
      ...item,
      unit: unitForInlineLabel(item.label),
      value: isDelta && resolvedNumericValue != null ? formatDeltaValue(resolvedNumericValue) : item.value,
      canSetSingleValue,
      setValue: canSetSingleValue && resolvedNumericValue != null ? roundDeltaValue(resolvedNumericValue) : null,
      settableIsotope,
    };
  });
  const d13TargetPayload = sampleD13DiagnosticsQuery.data?.target ?? {};
  const d18TargetPayload = sampleD18DiagnosticsQuery.data?.target ?? {};
  const d13ActiveStatus =
    asString(d13TargetPayload["row_label"]).trim() === activeRowLabel
      ? asString(d13TargetPayload["collector_status"]).trim()
      : "";
  const d18ActiveStatus =
    asString(d18TargetPayload["row_label"]).trim() === activeRowLabel
      ? asString(d18TargetPayload["collector_status"]).trim()
      : "";
  const d13CurrentRawValue = asNumber(d13TargetPayload["current_value"]);
  const d18CurrentRawValue = asNumber(d18TargetPayload["current_value"]);
  const d13LinearityCorrectedRawValue = asNumber(d13TargetPayload["linearity_corrected_value"]);
  const d18LinearityCorrectedRawValue = asNumber(d18TargetPayload["linearity_corrected_value"]);
  const d13Method = formatMethodLabel(d13TargetPayload["current_method"]);
  const d18Method = formatMethodLabel(d18TargetPayload["current_method"]);
  const d13CurrentDisplayValue = d13CurrentRawValue ?? selectedPointD13;
  const d18CurrentDisplayValue = d18CurrentRawValue ?? selectedPointD18;
  const d13LinearityCorrectedDisplayValue = d13LinearityCorrectedRawValue;
  const d18LinearityCorrectedDisplayValue = d18LinearityCorrectedRawValue;
  const effectiveOutlier =
    typeof sampleD18DiagnosticsQuery.data?.target?.effective_outlier === "boolean"
      ? (sampleD18DiagnosticsQuery.data.target.effective_outlier as boolean)
      : typeof sampleD13DiagnosticsQuery.data?.target?.effective_outlier === "boolean"
        ? (sampleD13DiagnosticsQuery.data.target.effective_outlier as boolean)
        : false;
  const activeTargetCollectorStatus = d13ActiveStatus || d18ActiveStatus;
  const singleInterpolateLabel = isFailedSampleCollectorStatus(activeTargetCollectorStatus)
    ? "Interpolate d13C + d18O"
    : `Interpolate ${selectionEditorTab}`;
  const overviewCards = {
    processing3d: {
      key: "processing_3d",
      title: "3D Processing Overview",
      description: "Global 3D view for the filtered processing scope.",
      figure: withColorScaleRange(workspace.overview_figures.processing_3d),
    },
    d13Summary: {
      key: "d13_summary",
      title: "d13C Summary",
      description: "Summary curve for d13C across the active scope.",
      figure: withColorScaleRange(workspace.overview_figures.d13_summary),
    },
    d18Summary: {
      key: "d18_summary",
      title: "d18O Summary",
      description: "Summary curve for d18O across the active scope.",
      figure: withColorScaleRange(workspace.overview_figures.d18_summary),
    },
    crossplot: {
      key: "crossplot",
      title: "Crossplot",
      description: "d13C vs d18O selection surface for dual-isotope edits.",
      figure: withColorScaleRange(workspace.overview_figures.crossplot),
    },
  };
  const d13SummaryState = normalizeDisplayState(displayState[overviewCards.d13Summary.key]);
  const d18SummaryState = normalizeDisplayState(displayState[overviewCards.d18Summary.key]);
  const d13SummaryHasCalibrated = figureHasTracePrefix(overviewCards.d13Summary.figure, "Calibrated");
  const d18SummaryHasCalibrated = figureHasTracePrefix(overviewCards.d18Summary.figure, "Calibrated");
  const d13SummaryFigure = withDisplayState(overviewCards.d13Summary.figure, d13SummaryState);
  const d18SummaryFigure = withDisplayState(overviewCards.d18Summary.figure, d18SummaryState);
  const activeSelectionChartKey = isSelectionEditorOpen ? (activeTarget?.chartKey ?? selectedTargets[0]?.chartKey ?? null) : null;
  const selectionSourceChart: SelectionSourceChart | null = (() => {
    if (!activeSelectionChartKey) {
      return null;
    }
    const crossplotStackedSource: SelectionSourceChart | null = (() => {
      if (activeSelectionChartKey !== overviewCards.crossplot.key || !activeTarget || activeTarget.isotopeKey !== "cross") {
        return null;
      }
      for (const section of workspace.species_sections) {
        for (const figureSet of section.identifier_figures) {
          const d13Key = `${section.species}|${figureSet.identifier}|d13C`;
          const d18Key = `${section.species}|${figureSet.identifier}|d18O`;
          const d13State = normalizeDisplayState(displayState[d13Key]);
          const d18State = normalizeDisplayState(displayState[d18Key]);
          const d13FigureBase = withDisplayState(withColorScaleRange(figureSet.d13c), d13State);
          const d18FigureBase = withDisplayState(withColorScaleRange(figureSet.d18o), d18State);
          const containsSelectedRow =
            figureContainsRowLabel(d13FigureBase, activeTarget.rowLabel) || figureContainsRowLabel(d18FigureBase, activeTarget.rowLabel);
          if (!containsSelectedRow) {
            continue;
          }
          return {
            title: "Crossplot selection source",
            description: `${section.species} | ${figureSet.identifier} isotope series for the selected sample.`,
            chartKey: overviewCards.crossplot.key,
            figure: undefined,
            stackedFigures: [
              {
                key: `${d13Key}:selection-source`,
                chartKey: d13Key,
                title: "d13C series",
                figure: highlightSelectionSourceFigure(d13FigureBase, activeTarget),
              },
              {
                key: `${d18Key}:selection-source`,
                chartKey: d18Key,
                title: "d18O series",
                figure: highlightSelectionSourceFigure(d18FigureBase, activeTarget),
              },
            ],
          };
        }
      }
      return null;
    })();

    const overviewChartMap: Record<string, SelectionSourceChart> = {
      [overviewCards.processing3d.key]: {
        title: overviewCards.processing3d.title,
        description: overviewCards.processing3d.description,
        chartKey: overviewCards.processing3d.key,
        figure: highlightSelectionSourceFigure(overviewCards.processing3d.figure, activeTarget),
      },
      [overviewCards.crossplot.key]: {
        title: crossplotStackedSource?.title ?? overviewCards.crossplot.title,
        description: crossplotStackedSource?.description ?? overviewCards.crossplot.description,
        chartKey: overviewCards.crossplot.key,
        figure: crossplotStackedSource?.figure ?? highlightSelectionSourceFigure(overviewCards.crossplot.figure, activeTarget),
        stackedFigures: crossplotStackedSource?.stackedFigures,
      },
      [overviewCards.d13Summary.key]: {
        title: overviewCards.d13Summary.title,
        description: overviewCards.d13Summary.description,
        chartKey: overviewCards.d13Summary.key,
        figure: highlightSelectionSourceFigure(d13SummaryFigure, activeTarget),
      },
      [overviewCards.d18Summary.key]: {
        title: overviewCards.d18Summary.title,
        description: overviewCards.d18Summary.description,
        chartKey: overviewCards.d18Summary.key,
        figure: highlightSelectionSourceFigure(d18SummaryFigure, activeTarget),
      },
    };
    if (overviewChartMap[activeSelectionChartKey]) {
      return overviewChartMap[activeSelectionChartKey];
    }
    const parts = activeSelectionChartKey.split("|");
    if (parts.length < 3) {
      return null;
    }
    const isotopeKey = parts[parts.length - 1];
    const identifier = parts[parts.length - 2];
    const species = parts.slice(0, -2).join("|");
    if (isotopeKey !== "d13C" && isotopeKey !== "d18O") {
      return null;
    }
    const section = workspace.species_sections.find((item) => item.species === species);
    const figureSet = section?.identifier_figures.find((item) => item.identifier === identifier);
    if (!figureSet) {
      return null;
    }
    const state = normalizeDisplayState(displayState[activeSelectionChartKey]);
    return {
      title: `${species} | ${identifier} | ${isotopeKey}`,
      description: "Source chart used for the current selection.",
      chartKey: activeSelectionChartKey,
      figure: highlightSelectionSourceFigure(
        isotopeKey === "d13C"
          ? withDisplayState(withColorScaleRange(figureSet.d13c), state)
          : withDisplayState(withColorScaleRange(figureSet.d18o), state),
        activeTarget,
      ),
    };
  })();

  return (
    <div className="space-y-6">
      <Card className="border-stone-300 bg-stone-50">
        <CardHeader className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <CardTitle>Data Processing Workspace</CardTitle>
            <CardDescription>
              Scientific processing is backend-owned. This page only drives filtering, selection, edits, diagnostics, and export.
            </CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm text-stone-600">
            <span className="rounded-md bg-white px-3 py-1 ring-1 ring-stone-200">Edited rows: {workspace.edit_state.edited_rows.length}</span>
            <span className="rounded-md bg-white px-3 py-1 ring-1 ring-stone-200">
              Manual overrides: {manualOverrideCount}
            </span>
            <span className="rounded-md bg-white px-3 py-1 ring-1 ring-stone-200">Selection: {selectedTargets.length}</span>
            <Button variant="secondary" size="sm" onClick={() => setExportModalOpen(true)} disabled={busy}>
              <Download className="h-4 w-4" />
              Data export
            </Button>
          </div>
        </CardHeader>
      </Card>

      <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <aside className="space-y-6 xl:sticky xl:top-6 xl:self-start">
          <Card>
            <CardHeader>
              <CardTitle>Processing Controls</CardTitle>
              <CardDescription>Filters, outliers, and shared linearity controls synced with Calibration.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6 xl:max-h-[calc(100vh-12rem)] xl:overflow-y-auto xl:pr-4 [scrollbar-gutter:stable]">
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
                <label className="text-sm">
                  <span className="mb-1 block font-medium text-stone-700">Identifier scope</span>
                  <select
                    value={activeConfig.selected_identifier}
                    onChange={(event) => updateConfig("selected_identifier", event.target.value)}
                    className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                  >
                    {workspace.available_values.identifiers.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-sm">
                  <span className="mb-1 block font-medium text-stone-700">X axis</span>
                  <select
                    value={activeConfig.x_axis_option}
                    onChange={(event) => updateConfig("x_axis_option", event.target.value as ProcessingConfig["x_axis_option"])}
                    className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                  >
                    <option value="By Identifier 2">By Identifier 2</option>
                    <option value="By Sequence">By Sequence</option>
                  </select>
                </label>
                <div className="text-sm">
                  <span className="mb-1 block font-medium text-stone-700">Color parameter</span>
                  <select
                    value={activeConfig.color_param}
                    onChange={(event) => updateConfig("color_param", event.target.value)}
                    className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                  >
                    {workspace.available_values.color_params
                      .filter((option) => option !== "Date_ordinal")
                      .map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                  <div className="mt-2">
                    <RangeSliderField
                      label="Color scale interval"
                      value={effectiveColorScaleRange}
                      min={colorSliderBounds.min}
                      max={colorSliderBounds.max}
                      step={sliderStep(colorSliderBounds)}
                      precision={sliderPrecision(colorSliderBounds)}
                      onChange={(nextRange) => setColorScaleRange(nextRange)}
                    />
                  </div>
                </div>
                <label className="text-sm">
                  <span className="mb-1 block font-medium text-stone-700">3D Z axis</span>
                  <select
                    value={activeConfig.z_axis}
                    onChange={(event) => updateConfig("z_axis", event.target.value)}
                    className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                  >
                    {workspace.available_values.z_axis_options.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="space-y-3">
                <div className="text-sm font-medium text-stone-800">Range filters</div>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
                  <RangeSliderField
                    label="Signal range"
                    value={activeConfig.signal_range}
                    min={Math.min(0, activeConfig.signal_range[0], activeConfig.signal_range[1])}
                    max={Math.max(100, activeConfig.signal_range[0], activeConfig.signal_range[1])}
                    step={0.1}
                    precision={2}
                    showManualInputs
                    onChange={(nextRange) => updateConfig("signal_range", nextRange)}
                  />
                  <RangeSliderField
                    label="Leak range"
                    value={activeConfig.leak_range}
                    min={Math.min(0, activeConfig.leak_range[0], activeConfig.leak_range[1])}
                    max={Math.max(2000, activeConfig.leak_range[0], activeConfig.leak_range[1])}
                    step={1}
                    precision={1}
                    showManualInputs
                    onChange={(nextRange) => updateConfig("leak_range", nextRange)}
                  />
                  <RangeSliderField
                    label="d13C range"
                    value={activeConfig.d13c_range}
                    min={Math.min(-50, activeConfig.d13c_range[0], activeConfig.d13c_range[1])}
                    max={Math.max(50, activeConfig.d13c_range[0], activeConfig.d13c_range[1])}
                    step={0.001}
                    precision={3}
                    showManualInputs
                    onChange={(nextRange) => updateConfig("d13c_range", nextRange)}
                  />
                  <RangeSliderField
                    label="d18O range"
                    value={activeConfig.d18o_range}
                    min={Math.min(-50, activeConfig.d18o_range[0], activeConfig.d18o_range[1])}
                    max={Math.max(50, activeConfig.d18o_range[0], activeConfig.d18o_range[1])}
                    step={0.001}
                    precision={3}
                    showManualInputs
                    onChange={(nextRange) => updateConfig("d18o_range", nextRange)}
                  />
                </div>
                <label className="text-sm">
                  <span className="mb-1 block text-stone-700">Statistical outlier method</span>
                  <select
                    value={activeConfig.statistical_outlier_method}
                    onChange={(event) => updateConfig("statistical_outlier_method", event.target.value as ProcessingConfig["statistical_outlier_method"])}
                    className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                  >
                    <option value="Z-Score">Z-Score</option>
                    <option value="IQR">IQR</option>
                  </select>
                </label>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="text-sm">
                    <span className="mb-1 block text-stone-700">Sigma level</span>
                    <input
                      type="number"
                      step="0.1"
                      value={activeConfig.sigma_level_data}
                      onChange={(event) => updateConfig("sigma_level_data", Number(event.target.value))}
                      className="w-full rounded-lg border border-stone-300 px-3 py-2"
                    />
                  </label>
                  <label className="text-sm">
                    <span className="mb-1 block text-stone-700">IQR multiplier</span>
                    <input
                      type="number"
                      step="0.1"
                      value={activeConfig.iqr_multiplier_data}
                      onChange={(event) => updateConfig("iqr_multiplier_data", Number(event.target.value))}
                      className="w-full rounded-lg border border-stone-300 px-3 py-2"
                    />
                  </label>
                </div>
              </div>

              <div className="space-y-3">
                <div className="text-sm font-medium text-stone-800">Show on chart</div>
                <CheckboxField checked={activeConfig.overlays.show_statistical_outliers} label="Statistical outliers" onChange={(checked) => updateOverlay("show_statistical_outliers", checked)} />
                <CheckboxField checked={activeConfig.overlays.show_range_outliers} label="Range outliers" onChange={(checked) => updateOverlay("show_range_outliers", checked)} />
                <CheckboxField checked={activeConfig.overlays.show_manual_outliers} label="Manual outliers" onChange={(checked) => updateOverlay("show_manual_outliers", checked)} />
                <CheckboxField
                  checked={activeConfig.overlays.show_saturated_collectors}
                  label="Partially saturated collectors"
                  description="Checked keeps partially saturated samples on the curve. Unchecked treats them as outliers."
                  onChange={(checked) => updateOverlay("show_saturated_collectors", checked)}
                />
                <CheckboxField checked={activeConfig.overlays.show_saturated_samples} label="Fully saturated samples" onChange={(checked) => updateOverlay("show_saturated_samples", checked)} />
                <CheckboxField checked={activeConfig.overlays.show_failed_samples} label="Failed samples" onChange={(checked) => updateOverlay("show_failed_samples", checked)} />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void resetManualOutliers()}
                  disabled={busy || manualOverrideCount === 0}
                >
                  Reset manual outliers
                </Button>
              </div>

              <div className="space-y-3">
                <div className="text-sm font-medium text-stone-800">Saturation correction</div>
                <CheckboxField
                  checked={Boolean(activeConfig.enable_saturation_correction)}
                  label="Enable saturation correction"
                  description="Applies only to unedited partially saturated samples before shared linearity."
                  onChange={(checked) => updateConfig("enable_saturation_correction", checked)}
                />
                {activeConfig.enable_saturation_correction ? (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="text-sm">
                      <span className="mb-1 block text-stone-700">d13C default method</span>
                      <select
                        value={activeConfig.saturation_correction_method_d13 ?? activeConfig.saturation_correction_method}
                        onChange={(event) => updateSaturationMethod("d13C", event.target.value as SaturationCorrectionMethod)}
                        className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                      >
                        {SATURATION_METHOD_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="text-sm">
                      <span className="mb-1 block text-stone-700">d18O default method</span>
                      <select
                        value={activeConfig.saturation_correction_method_d18 ?? activeConfig.saturation_correction_method}
                        onChange={(event) => updateSaturationMethod("d18O", event.target.value as SaturationCorrectionMethod)}
                        className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                      >
                        {SATURATION_METHOD_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                ) : null}
              </div>

              <div className="space-y-4 rounded-lg border border-stone-200 bg-white/80 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-sm font-medium text-stone-800">Linearity (shared with calibration)</div>
                  <span className="rounded-md bg-stone-100 px-2 py-1 text-xs text-stone-600">Basis: {selectedLinearityBasisLabel}</span>
                </div>
                {activeLinearity ? (
                  <>
                    <CheckboxField
                      checked={activeLinearity.apply}
                      label="Enable linearity correction"
                      description="Uses the same basis, fits, and offsets as Calibration."
                      onChange={(checked) => updateSharedLinearity("apply", checked)}
                    />
                    <CheckboxField
                      checked={activeConfig.apply_shared_linearity_to_partially_saturated}
                      label="Apply to partially saturated samples"
                      description="Also corrects recovered partially saturated values with the shared linearity fit."
                      onChange={(checked) => updateConfig("apply_shared_linearity_to_partially_saturated", checked)}
                    />
                    {!isTwoTermLinearityBasis ? (
                      <CheckboxField
                        checked={Boolean(activeLinearity.quadratic)}
                        label="Use quadratic linearity relationship"
                        description="Fits and applies y = a + b*I + c*I^2 instead of y = a + b*I."
                        onChange={(checked) => updateSharedLinearity("quadratic", checked)}
                      />
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
                        <input
                          type="number"
                          step="0.01"
                          value={activeLinearity.manual_d13_per_10v ?? 0}
                          onChange={(event) => updateLinearityCoefficientOffset("d13C", "primary", Number(event.target.value))}
                          className="w-full rounded-lg border border-stone-300 px-3 py-2"
                        />
                      </label>
                      <label className="text-sm">
                        <span className="mb-1 block text-stone-700">
                              {getLinearityCoefficientLabel("d18O", selectedLinearityIntensityCol, "primary", selectedLinearityCycleIntensityAggregation)}
                        </span>
                        <input
                          type="number"
                          step="0.01"
                          value={activeLinearity.manual_d18_per_10v ?? 0}
                          onChange={(event) => updateLinearityCoefficientOffset("d18O", "primary", Number(event.target.value))}
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
                          <input
                            type="number"
                            step="0.01"
                            value={activeLinearity.manual_d13_per_10v2 ?? 0}
                            onChange={(event) => updateLinearityCoefficientOffset("d13C", "secondary", Number(event.target.value))}
                            className="w-full rounded-lg border border-stone-300 px-3 py-2"
                          />
                        </label>
                        <label className="text-sm">
                          <span className="mb-1 block text-stone-700">
                                {getLinearityCoefficientLabel("d18O", selectedLinearityIntensityCol, "secondary", selectedLinearityCycleIntensityAggregation)}
                          </span>
                          <input
                            type="number"
                            step="0.01"
                            value={activeLinearity.manual_d18_per_10v2 ?? 0}
                            onChange={(event) => updateLinearityCoefficientOffset("d18O", "secondary", Number(event.target.value))}
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
                            <input
                              type="text"
                              inputMode="decimal"
                              value={linearityOffsetDrafts.line_1_offset_d13}
                              onFocus={() => setLinearityOffsetEditing("line_1_offset_d13")}
                              onChange={(event) => handleLinearityOffsetDraftChange("line_1_offset_d13", event.target.value)}
                              onBlur={() => commitLinearityOffsetDraft("line_1_offset_d13")}
                              onKeyDown={(event) => handleLinearityOffsetKeyDown(event, "line_1_offset_d13")}
                              className="w-full rounded-lg border border-stone-300 px-3 py-2"
                            />
                          </label>
                          <label className="text-sm">
                            <span className="mb-1 block text-stone-700">d18O</span>
                            <input
                              type="text"
                              inputMode="decimal"
                              value={linearityOffsetDrafts.line_1_offset_d18}
                              onFocus={() => setLinearityOffsetEditing("line_1_offset_d18")}
                              onChange={(event) => handleLinearityOffsetDraftChange("line_1_offset_d18", event.target.value)}
                              onBlur={() => commitLinearityOffsetDraft("line_1_offset_d18")}
                              onKeyDown={(event) => handleLinearityOffsetKeyDown(event, "line_1_offset_d18")}
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
                            <input
                              type="text"
                              inputMode="decimal"
                              value={linearityOffsetDrafts.line_2_offset_d13}
                              onFocus={() => setLinearityOffsetEditing("line_2_offset_d13")}
                              onChange={(event) => handleLinearityOffsetDraftChange("line_2_offset_d13", event.target.value)}
                              onBlur={() => commitLinearityOffsetDraft("line_2_offset_d13")}
                              onKeyDown={(event) => handleLinearityOffsetKeyDown(event, "line_2_offset_d13")}
                              className="w-full rounded-lg border border-stone-300 px-3 py-2"
                            />
                          </label>
                          <label className="text-sm">
                            <span className="mb-1 block text-stone-700">d18O</span>
                            <input
                              type="text"
                              inputMode="decimal"
                              value={linearityOffsetDrafts.line_2_offset_d18}
                              onFocus={() => setLinearityOffsetEditing("line_2_offset_d18")}
                              onChange={(event) => handleLinearityOffsetDraftChange("line_2_offset_d18", event.target.value)}
                              onBlur={() => commitLinearityOffsetDraft("line_2_offset_d18")}
                              onKeyDown={(event) => handleLinearityOffsetKeyDown(event, "line_2_offset_d18")}
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
                          standardPrecisionRows.map((summary: CalibrationPrecisionSummary) => (
                            <div key={summary.standard} className="grid grid-cols-[1fr_auto_auto] items-center gap-3 text-xs text-stone-700">
                              <span className="font-medium text-stone-800">{summary.standard}</span>
                              <span>δ13C: {formatPrecisionMetric(summary.d13_linearity_corrected_precision)}</span>
                              <span>δ18O: {formatPrecisionMetric(summary.d18_linearity_corrected_precision)}</span>
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
              </div>

              <div className="flex flex-wrap gap-2">
                <Button onClick={applyConfig} disabled={busy}>
                  Apply config
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    setConfig(workspace.config);
                    if (calibrationWorkspaceQuery.data?.config?.linearity) {
                      setSharedLinearityConfig(calibrationWorkspaceQuery.data.config.linearity);
                    }
                  }}
                  disabled={busy}
                >
                  Restore saved
                </Button>
                <Button variant="outline" onClick={() => removeCalibrationMutation.mutate()} disabled={busy}>
                  {removeCalibrationMutation.isPending ? "Removing calibration..." : "Remove calibration"}
                </Button>
                <Button variant="outline" onClick={() => resetAllMutation.mutate()} disabled={busy}>
                  Reset all edits
                </Button>
              </div>
            </CardContent>
          </Card>

        </aside>

        <div className="space-y-6">
          <ProcessingSummaryHero workspace={workspace} />

          <div className="space-y-6">
            <div className="grid gap-6 xl:grid-cols-2">
              <FigureCard
                key={overviewCards.processing3d.key}
                chartKey={overviewCards.processing3d.key}
                title={overviewCards.processing3d.title}
                description={overviewCards.processing3d.description}
                figure={overviewCards.processing3d.figure}
                chartClassName="min-h-[520px] xl:aspect-square"
                {...chartHoverProps(overviewCards.processing3d.key)}
                onPointClick={(points) => handleChartClick(overviewCards.processing3d.key, points)}
                onSelection={(points) => handleChartSelection(overviewCards.processing3d.key, points)}
              />
              <FigureCard
                key={overviewCards.crossplot.key}
                chartKey={overviewCards.crossplot.key}
                title={overviewCards.crossplot.title}
                description={overviewCards.crossplot.description}
                figure={overviewCards.crossplot.figure}
                chartClassName="min-h-[520px] xl:aspect-square"
                {...chartHoverProps(overviewCards.crossplot.key)}
                onPointClick={(points) => handleChartClick(overviewCards.crossplot.key, points)}
                onSelection={(points) => handleChartSelection(overviewCards.crossplot.key, points)}
              />
            </div>
          </div>

          {isExportModalOpen ? (
            <div className="fixed inset-0 z-40 flex items-start justify-center bg-stone-950/40 p-3 pt-4 sm:p-6 sm:pt-8" onClick={() => setExportModalOpen(false)}>
              <div
                className="flex max-h-[calc(100vh-2rem)] w-full max-w-4xl flex-col overflow-hidden rounded-lg border border-stone-300 bg-white shadow-2xl"
                onClick={(event) => event.stopPropagation()}
              >
                <div className="flex items-center justify-between border-b border-stone-200 px-4 py-3">
                  <div>
                    <div className="text-base font-semibold text-stone-900">Export</div>
                    <div className="text-sm text-stone-500">Configure export options, then download either the entire dataset or client output.</div>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => setExportModalOpen(false)}>
                    <X className="h-4 w-4" />
                    Close
                  </Button>
                </div>
                <div className="min-h-0 space-y-4 overflow-y-auto p-4">
                  <div className="space-y-2">
                    <div className="text-sm font-medium text-stone-800">Export type</div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant={exportOutputType === "dataset" ? "default" : "outline"}
                        size="sm"
                        onClick={() => setExportOutputType("dataset")}
                        disabled={busy}
                      >
                        Entire dataset
                      </Button>
                      <Button
                        type="button"
                        variant={exportOutputType === "client_output" ? "default" : "outline"}
                        size="sm"
                        onClick={() => setExportOutputType("client_output")}
                        disabled={busy}
                      >
                        Client output
                      </Button>
                    </div>
                  </div>

                  <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
                    <div className="space-y-4">
                      <CheckboxField
                        checked={activeConfig.export.include_outliers}
                        label="Include outliers in export"
                        onChange={(checked) => {
                          updateExport("include_outliers", checked);
                          if (!checked) {
                            updateExport("interpolate_outliers", false);
                          }
                        }}
                      />
                      <CheckboxField
                        checked={activeConfig.export.interpolate_outliers}
                        label="Interpolate before export"
                        description={!activeConfig.export.include_outliers ? "Enable Include outliers first." : undefined}
                        onChange={(checked) => updateExport("interpolate_outliers", checked)}
                        disabled={!activeConfig.export.include_outliers}
                      />
                      {exportOutputType === "client_output" ? (
                        <div className="space-y-3 rounded-lg border border-stone-200 p-3">
                          <div className="space-y-2">
                            <CheckboxField
                              checked={restoreStdevEnabled}
                              label="Restore stdev"
                              description="Cap high stdev values in client output to the maximum below."
                              onChange={setRestoreStdevEnabled}
                            />
                            <label className="text-sm">
                              <span className="mb-1 block text-stone-700">Max stdev</span>
                              <input
                                type="number"
                                min={0}
                                max={RESTORE_STDEV_DEFAULT_CAP}
                                step="0.001"
                                value={restoreStdevCap}
                                onChange={(event) =>
                                  setRestoreStdevCap(
                                    Math.min(
                                      RESTORE_STDEV_DEFAULT_CAP,
                                      Math.max(0, parseFinite(event.target.value, RESTORE_STDEV_DEFAULT_CAP)),
                                    ),
                                  )
                                }
                                disabled={!restoreStdevEnabled}
                                className="w-32 rounded-lg border border-stone-300 px-3 py-2 disabled:cursor-not-allowed disabled:bg-stone-100"
                              />
                            </label>
                          </div>
                          <div className="space-y-2 rounded-lg border border-stone-200 bg-stone-50 p-2.5">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-sm font-medium text-stone-800">Duplicate check</span>
                              <Button type="button" variant="outline" size="sm" onClick={() => void handleDuplicateCheck()} disabled={busy}>
                                <SearchCheck className="h-4 w-4" />
                                {duplicateCheckMutation.isPending ? "Checking..." : "Check for duplicates"}
                              </Button>
                            </div>
                            <div className="text-xs text-stone-600">
                              Checks for repeated Identifier 1 + Identifier 2 + Species in the current client-output scope.
                            </div>
                            {duplicateCheckMutation.isError ? (
                              <div className="text-xs font-medium text-red-700">Duplicate check failed.</div>
                            ) : null}
                            {duplicateCheckResult ? (
                              <div
                                className={cn(
                                  "space-y-1 rounded-md border px-2 py-1.5 text-xs",
                                  duplicateCheckResult.duplicate_row_count > 0
                                    ? "border-amber-300 bg-amber-50 text-amber-900"
                                    : "border-emerald-300 bg-emerald-50 text-emerald-900",
                                )}
                              >
                                <div>
                                  {duplicateCheckResult.duplicate_row_count > 0
                                    ? `${duplicateCheckResult.duplicate_row_count} duplicate row(s) found.`
                                    : "No duplicates found."}
                                </div>
                                {duplicateCheckResult.duplicate_identifier1_identifier2_species_values.length ? (
                                  <div>
                                    Identifier 1 + Identifier 2 + Species:{" "}
                                    {duplicateCheckResult.duplicate_identifier1_identifier2_species_values.slice(0, 8).join(", ")}
                                    {duplicateCheckResult.duplicate_identifier1_identifier2_species_values.length > 8 ? "..." : ""}
                                  </div>
                                ) : null}
                              </div>
                            ) : null}
                          </div>
                        </div>
                      ) : null}
                      <label className="text-sm">
                        <span className="mb-1 block text-stone-700">Export identifiers</span>
                        <select
                          multiple
                          value={activeConfig.export.selected_ids}
                          onChange={(event) =>
                            updateExport(
                              "selected_ids",
                              Array.from(event.currentTarget.selectedOptions).map((option) => option.value),
                            )
                          }
                          className="min-h-[150px] w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                        >
                          {workspace.available_values.export_identifiers.map((option) => (
                            <option key={option} value={option}>
                              {option}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="text-sm">
                        <span className="mb-1 block text-stone-700">Client name</span>
                        <input
                          type="text"
                          value={activeConfig.export.client_name ?? ""}
                          onChange={(event) => updateExport("client_name", event.target.value || null)}
                          className="w-full rounded-lg border border-stone-300 px-3 py-2"
                        />
                      </label>
                      <label className="text-sm">
                        <span className="mb-1 block text-stone-700">Comment map</span>
                        <textarea
                          value={commentMapText}
                          onChange={(event) => {
                            const nextText = event.target.value;
                            setCommentMapText(nextText);
                            updateExport("comment_map", parseCommentMap(nextText));
                          }}
                          rows={6}
                          className="w-full rounded-lg border border-stone-300 px-3 py-2"
                          placeholder={"old=value\nflag=client label"}
                        />
                      </label>
                    </div>

                    <div className="space-y-3 rounded-lg border border-stone-200 bg-stone-50 p-3 text-sm text-stone-700">
                      <div className="font-medium text-stone-900">Export summary</div>
                      <div>Filename (dataset): generated from client name and current date.</div>
                      <div>
                        Filename (client output): generated from client name, exported series/species, and current date.
                      </div>
                      <div>Rows in workspace scope: {workspace.summary.total_measurements}</div>
                      <div>Final analyses: {workspace.summary.final_analyses}</div>
                    </div>
                  </div>
                </div>
                <div className="flex justify-end gap-2 border-t border-stone-200 px-4 py-3">
                  <Button variant="outline" onClick={() => setExportModalOpen(false)} disabled={busy}>
                    Cancel
                  </Button>
                  <Button onClick={() => handleExport(exportOutputType)} disabled={busy}>
                    <Download className="h-4 w-4" />
                    {exportOutputType === "client_output" ? "Download client output" : "Download dataset"}
                  </Button>
                </div>
              </div>
            </div>
          ) : null}

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
                  {selectionSourceChart?.figure || selectionSourceChart?.stackedFigures?.length ? (
                    <Card className="border-stone-300">
                      <CardHeader>
                        <CardTitle className="text-base">Selection Source Chart</CardTitle>
                        <CardDescription>
                          {selectionSourceChart.title} {selectionSourceChart.description}
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        {selectionSourceChart.stackedFigures?.length ? (
                          <div className="space-y-3">
                            {selectionSourceChart.stackedFigures.map((item) => (
                              <div key={item.key} className="rounded-lg border border-stone-200 p-2">
                                <div className="px-1 pb-2 text-sm font-medium text-stone-700">{item.title}</div>
                                <PlotlyChart
                                  figure={item.figure}
                                  className="h-[280px] w-full"
                                  deferRenderMs={SELECTION_EDITOR_CHART_DEFER_MS}
                                  onPointClick={(points) => handleSelectionSourceChartClick(item.chartKey, points)}
                                  onSelection={(points) => handleSelectionSourceChartSelection(item.chartKey, points)}
                                />
                              </div>
                            ))}
                          </div>
                        ) : selectionSourceChart.figure ? (
                          <PlotlyChart
                            figure={selectionSourceChart.figure}
                            className="h-[360px] w-full"
                            deferRenderMs={SELECTION_EDITOR_CHART_DEFER_MS}
                            onPointClick={(points) =>
                              handleSelectionSourceChartClick(selectionSourceChart.chartKey ?? activeSelectionChartKey ?? "", points)
                            }
                            onSelection={(points) =>
                              handleSelectionSourceChartSelection(selectionSourceChart.chartKey ?? activeSelectionChartKey ?? "", points)
                            }
                          />
                        ) : (
                          <div className="rounded-lg border border-dashed border-stone-300 p-4 text-sm text-stone-500">
                            Source chart preview unavailable for this selection.
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  ) : null}

                  {selectedTargets.length ? (
                    <>
                      <div className="space-y-3 rounded-lg border border-stone-200 bg-stone-50/50 p-4">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="space-y-1">
                            <div className="text-sm font-semibold text-stone-700">
                              Active target {activeTargetIndex + 1} / {selectedTargets.length}
                            </div>
                            <div className="text-xl font-semibold text-stone-900">
                              {(activeTarget?.identifier1 || "No Identifier 1").trim()} | {(activeTarget?.identifier2 || "No Identifier 2").trim()} |{" "}
                              {(activeTarget?.rowLabel || "").trim()}
                            </div>
                          </div>
                          <div className="flex gap-2">
                            <Button variant="outline" size="sm" onClick={() => moveSelectionTarget("prev")} disabled={!canMoveToPrevTarget}>
                              Prev
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => moveSelectionTarget("next")}
                              disabled={!canMoveToNextTarget}
                            >
                              Next
                            </Button>
                          </div>
                        </div>
                        {activeTargetInlineDisplayItems.length ? (
                          <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4">
                            {activeTargetInlineDisplayItems.map((item, index) => (
                              <div
                                key={`${item.label}:${item.value}:${index}`}
                                onClick={() => {
                                  if (item.canSetSingleValue && item.setValue != null && item.settableIsotope) {
                                    setSingleValueFromSuggestion(item.settableIsotope, item.setValue, "raw");
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
                        <div className="flex flex-wrap gap-2">
                          {selectedRowLabels.map((label) => (
                            <span
                              key={label}
                              className={cn(
                                "rounded-md px-3 py-1 text-xs ring-1 ring-stone-200",
                                label === `${activeTarget?.rowLabel}:${activeTarget?.isotopeKey}` ? "bg-stone-900 text-white" : "bg-white text-stone-700",
                              )}
                            >
                              {label}
                            </span>
                          ))}
                        </div>
                      </div>

                      {activeTarget ? (
                        <div className="space-y-4">
                          <div className="space-y-2">
                            <div className="text-xs font-semibold uppercase tracking-normal text-stone-500">Details</div>
                            <div className="grid gap-3 md:grid-cols-2">
                              <div className="rounded-lg border border-stone-200 bg-stone-50/50 p-4">
                                <div className="text-xs font-semibold uppercase tracking-normal text-stone-600">d13C</div>
                                <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
                                  <div className="text-stone-500">Current</div>
                                  <div className="text-right font-medium text-stone-900">
                                    {d13CurrentDisplayValue == null ? "N/A" : formatDeltaValue(d13CurrentDisplayValue)}
                                  </div>
                                  <div className="text-stone-500">Linearity corrected</div>
                                  <div className="text-right font-medium text-stone-900">
                                    {d13LinearityCorrectedDisplayValue == null ? "N/A" : formatDeltaValue(d13LinearityCorrectedDisplayValue)}
                                  </div>
                                  <div className="text-stone-500">Method</div>
                                  <div className="text-right font-medium text-stone-900">{d13Method}</div>
                                </div>
                              </div>
                              <div className="rounded-lg border border-stone-200 bg-stone-50/50 p-4">
                                <div className="text-xs font-semibold uppercase tracking-normal text-stone-600">d18O</div>
                                <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
                                  <div className="text-stone-500">Current</div>
                                  <div className="text-right font-medium text-stone-900">
                                    {d18CurrentDisplayValue == null ? "N/A" : formatDeltaValue(d18CurrentDisplayValue)}
                                  </div>
                                  <div className="text-stone-500">Linearity corrected</div>
                                  <div className="text-right font-medium text-stone-900">
                                    {d18LinearityCorrectedDisplayValue == null ? "N/A" : formatDeltaValue(d18LinearityCorrectedDisplayValue)}
                                  </div>
                                  <div className="text-stone-500">Method</div>
                                  <div className="text-right font-medium text-stone-900">{d18Method}</div>
                                </div>
                              </div>
                            </div>
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
                                  className={cn(
                                    "min-w-[92px] rounded-lg px-4 py-2 text-sm font-semibold transition",
                                    isActive ? "bg-stone-900 text-white shadow-sm" : "text-stone-700 hover:bg-stone-100",
                                  )}
                                >
                                  {isotopeKey}
                                </button>
                              );
                            })}
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
                                className={cn(
                                  "w-full rounded-lg border px-3 py-2 transition-all duration-200",
                                  isSetValueInputHighlighted
                                    ? "border-fuchsia-500 bg-fuchsia-50 ring-2 ring-fuchsia-300"
                                    : "border-stone-300",
                                )}
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
                              {singleInterpolateLabel}
                            </Button>
                            <Button variant="outline" onClick={resetSelected} disabled={busy}>
                              Reset selected
                            </Button>
                            <Button variant="outline" onClick={() => setTargets([])} disabled={busy}>
                              Clear selection
                            </Button>
                            <Button variant={effectiveOutlier ? "secondary" : "outline"} onClick={() => applyOutlierOverride(true)} disabled={busy}>
                              Force outlier
                            </Button>
                            <Button variant={!effectiveOutlier ? "secondary" : "outline"} onClick={() => applyOutlierOverride(false)} disabled={busy}>
                              Force keep
                            </Button>
                          </div>

                          <DiagnosticsPanel
                            title={`${selectionEditorTab} cycle diagnostics (shared intensity chart/table)`}
                            diagnostics={activeDiagnostics}
                            loading={activeDiagnosticsLoading}
                            displayDelta={rawToDisplayDelta(selectionEditorTab)}
                            onPickDeltaValue={(value, valueSpace = "raw", stdev = null) =>
                              setSingleValueFromSuggestion(selectionEditorTab, value, valueSpace, stdev)
                            }
                          />
                        </div>
                      ) : null}

                      {selectedTargets.length > 1 ? (
                        <div className="space-y-4 rounded-lg border border-stone-200 p-4">
                          <div className="text-sm font-medium text-stone-800">Multi-point actions</div>
                          <div className="grid gap-3 sm:grid-cols-2">
                            <label className="text-sm">
                              <span className="mb-1 block text-stone-700">d13C offset for selection</span>
                              <input
                                type="number"
                                step="0.001"
                                value={multiOffsetD13}
                                onChange={(event) => setMultiOffsetD13(Number(event.target.value))}
                                className="w-full rounded-lg border border-stone-300 px-3 py-2"
                              />
                            </label>
                            <label className="text-sm">
                              <span className="mb-1 block text-stone-700">d18O offset for selection</span>
                              <input
                                type="number"
                                step="0.001"
                                value={multiOffsetD18}
                                onChange={(event) => setMultiOffsetD18(Number(event.target.value))}
                                className="w-full rounded-lg border border-stone-300 px-3 py-2"
                              />
                            </label>
                          </div>
                          <div className="flex flex-wrap gap-2">
                            <Button variant="outline" onClick={() => applyMultiOffset("d13C", multiOffsetD13)} disabled={busy}>
                              Offset selected d13C
                            </Button>
                            <Button variant="outline" onClick={() => applyMultiOffset("d18O", multiOffsetD18)} disabled={busy}>
                              Offset selected d18O
                            </Button>
                            <Button variant="outline" onClick={() => applyMultiInterpolate()} disabled={busy}>
                              Interpolate selected
                            </Button>
                          </div>
                        </div>
                      ) : null}
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

          <div className="space-y-6">
            <FigureCard
              key={overviewCards.d13Summary.key}
              chartKey={overviewCards.d13Summary.key}
              title={overviewCards.d13Summary.title}
              description={overviewCards.d13Summary.description}
              figure={d13SummaryFigure}
              headerActions={
                <TraceModeControl
                  state={d13SummaryState}
                  hasCalibrated={d13SummaryHasCalibrated}
                  onChange={(patch) => updateChartDisplayState(overviewCards.d13Summary.key, patch)}
                />
              }
              chartClassName="h-[460px] w-full"
              {...chartHoverProps(overviewCards.d13Summary.key)}
              onPointClick={(points) => handleChartClick(overviewCards.d13Summary.key, points)}
              onSelection={(points) => handleChartSelection(overviewCards.d13Summary.key, points)}
            />
            <FigureCard
              key={overviewCards.d18Summary.key}
              chartKey={overviewCards.d18Summary.key}
              title={overviewCards.d18Summary.title}
              description={overviewCards.d18Summary.description}
              figure={d18SummaryFigure}
              headerActions={
                <TraceModeControl
                  state={d18SummaryState}
                  hasCalibrated={d18SummaryHasCalibrated}
                  onChange={(patch) => updateChartDisplayState(overviewCards.d18Summary.key, patch)}
                />
              }
              chartClassName="h-[460px] w-full"
              {...chartHoverProps(overviewCards.d18Summary.key)}
              onPointClick={(points) => handleChartClick(overviewCards.d18Summary.key, points)}
              onSelection={(points) => handleChartSelection(overviewCards.d18Summary.key, points)}
            />
          </div>

          <OutlierTablesPanel
            title="Data outlier tables"
            tables={workspace.outlier_tables}
            renderTableControls={renderFailedSampleTableControls}
          />

          <div className="space-y-6">
            {workspace.species_sections.map((section) => {
              const isSectionOpen = openSpeciesSections.has(section.species);
              const identifierCount = section.identifier_count ?? section.identifier_figures.length;
              const isLoadingSectionFigures = workspaceQuery.isFetching && isSectionOpen && identifierCount > 0 && section.identifier_figures.length === 0;
              return (
                <details
                  key={section.species}
                  className="rounded-lg border border-stone-200 bg-white shadow-sm"
                  open={isSectionOpen}
                  onToggle={(event) => setSpeciesSectionOpen(section.species, event.currentTarget.open)}
                >
                  <summary className="cursor-pointer px-6 py-4 text-lg font-semibold text-stone-900">
                    {section.species} ({identifierCount} identifiers)
                  </summary>
                  {isSectionOpen ? (
                    <div className="space-y-6 p-6 pt-0">
                  {isLoadingSectionFigures ? (
                    <div className="rounded-lg border border-dashed border-stone-300 p-4 text-sm text-stone-500">
                      Loading species charts...
                    </div>
                  ) : null}
                  {section.identifier_figures.map((figureSet) => {
                    const d13Key = `${section.species}|${figureSet.identifier}|d13C`;
                    const d18Key = `${section.species}|${figureSet.identifier}|d18O`;
                    const d13State = normalizeDisplayState(displayState[d13Key]);
                    const d18State = normalizeDisplayState(displayState[d18Key]);
                    return (
                      <Card key={`${section.species}-${figureSet.identifier}`} className="border-stone-300">
                        <CardHeader>
                          <CardTitle>{figureSet.identifier}</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4">
                          <div className="space-y-6">
                            <div className="space-y-3">
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <div>
                                  <div className="text-sm font-medium text-stone-800">d13C chart</div>
                                </div>
                                <TraceModeControl
                                  state={d13State}
                                  hasCalibrated={figureSet.has_calibrated_d13c}
                                  onChange={(patch) => updateChartDisplayState(d13Key, patch)}
                                />
                              </div>
                              <div className="h-[380px] w-full overflow-hidden rounded-lg border border-stone-200/80">
                                <PlotlyChart
                                  figure={withDisplayState(withColorScaleRange(figureSet.d13c), d13State)}
                                  className="h-full w-full"
                                  uiRevision={`processing:${d13Key}`}
                                  {...chartHoverProps(d13Key)}
                                  onPointClick={(points) => handleChartClick(d13Key, points)}
                                  onSelection={(points) => handleChartSelection(d13Key, points)}
                                />
                              </div>
                            </div>

                            <div className="space-y-3">
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <div>
                                  <div className="text-sm font-medium text-stone-800">d18O chart</div>
                                </div>
                                <TraceModeControl
                                  state={d18State}
                                  hasCalibrated={figureSet.has_calibrated_d18o}
                                  onChange={(patch) => updateChartDisplayState(d18Key, patch)}
                                />
                              </div>
                              <div className="h-[380px] w-full overflow-hidden rounded-lg border border-stone-200/80">
                                <PlotlyChart
                                  figure={withDisplayState(withColorScaleRange(figureSet.d18o), d18State)}
                                  className="h-full w-full"
                                  uiRevision={`processing:${d18Key}`}
                                  {...chartHoverProps(d18Key)}
                                  onPointClick={(points) => handleChartClick(d18Key, points)}
                                  onSelection={(points) => handleChartSelection(d18Key, points)}
                                />
                              </div>
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    );
                  })}

                  <OutlierTablesPanel
                    title={`${section.species} outlier tables`}
                    tables={section.outlier_tables}
                    renderTableControls={renderFailedSampleTableControls}
                  />
                    </div>
                  ) : null}
                </details>
              );
            })}
          </div>
        </div>
      </div>
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




