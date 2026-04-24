"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { PlotlyChart } from "@/components/charts/plotly-chart";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { MultiSelectDropdown } from "@/components/ui/multi-select-dropdown";
import { api } from "@/lib/api";
import type { CalibrationConfig, CalibrationPrecisionSummary } from "@/lib/types";
import { useSessionStore } from "@/store/use-session-store";

const RANGE_FETCH_DEBOUNCE_MS = 300;
type LinearityOffsetField = "line_1_offset_d13" | "line_1_offset_d18" | "line_2_offset_d13" | "line_2_offset_d18";
const LINEARITY_INTENSITY_SAMP44 = "1  Cycle Int  Samp  44";
const LINEARITY_INTENSITY_DIFF44 = "1  Cycle Int  Diff Samp-Ref  44";
const LINEARITY_INTENSITY_MISMATCH44 = "1  Cycle Int  Pressure-Weighted Mismatch Samp-Ref  44";
const LINEARITY_INTENSITY_OPTIONS = [
  LINEARITY_INTENSITY_SAMP44,
  LINEARITY_INTENSITY_DIFF44,
  LINEARITY_INTENSITY_MISMATCH44,
] as const;
const LINEARITY_INTENSITY_OPTION_LABELS: Record<(typeof LINEARITY_INTENSITY_OPTIONS)[number], string> = {
  [LINEARITY_INTENSITY_SAMP44]: "Sample intensity",
  [LINEARITY_INTENSITY_DIFF44]: "Intensity diff",
  [LINEARITY_INTENSITY_MISMATCH44]: "Pressure-adjusted int diff",
};
type ColorScaleBounds = {
  min: number;
  max: number;
};
type FigureShape = Record<string, unknown> & {
  data: Array<Record<string, unknown>>;
  layout: Record<string, unknown>;
};

function getLinearityIntensityOptionLabel(value: string): string {
  if (value in LINEARITY_INTENSITY_OPTION_LABELS) {
    return LINEARITY_INTENSITY_OPTION_LABELS[value as (typeof LINEARITY_INTENSITY_OPTIONS)[number]];
  }
  return value;
}

function getLinearityCoefficientLabel(
  isotope: "d13C" | "d18O",
  intensityCol: string,
  quadratic: boolean,
): string {
  const prefix = isotope === "d13C" ? "d13C" : "d18O";
  if (intensityCol === LINEARITY_INTENSITY_MISMATCH44) {
    return quadratic
      ? `${prefix} pressure-weighted mismatch coefficient per (10V)^2`
      : `${prefix} pressure-weighted mismatch coefficient`;
  }
  if (intensityCol === LINEARITY_INTENSITY_DIFF44) {
    return quadratic ? `${prefix} intensity-diff coefficient per (10V)^2` : `${prefix} intensity-diff coefficient per 10V`;
  }
  return quadratic ? `${prefix} coefficient per (10V)^2` : `${prefix} coefficient per 10V`;
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

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item)).filter((item) => item.trim().length > 0);
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

function clampNumber(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function parseFinite(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
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
  return hasColorMapping ? cloned : figure;
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
    <div className="rounded-xl border border-stone-200 bg-white/80 p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-medium tracking-[0.01em] text-stone-700">{label}</div>
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
    () => applyColorScaleRangeToFigure(diagnosticsFigure, effectiveColorScaleRange),
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
  const selectedLinearityBasisLabel = getLinearityIntensityOptionLabel(selectedLinearityIntensityCol);
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
  const coefficientOffsetEnabled = Boolean(activeLinearity?.manual_override_enabled);

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

  function updateLinearityCoefficientOffset(isotopeKey: "d13C" | "d18O", value: number) {
    setSharedLinearityConfig((current) => {
      if (!current) {
        return current;
      }
      const next = { ...current };
      if (next.quadratic) {
        if (isotopeKey === "d13C") {
          next.manual_d13_per_10v2 = value;
        } else {
          next.manual_d18_per_10v2 = value;
        }
      } else if (isotopeKey === "d13C") {
        next.manual_d13_per_10v = value;
      } else {
        next.manual_d18_per_10v = value;
      }
      const d13Offset = next.quadratic ? Number(next.manual_d13_per_10v2 ?? 0) : Number(next.manual_d13_per_10v ?? 0);
      const d18Offset = next.quadratic ? Number(next.manual_d18_per_10v2 ?? 0) : Number(next.manual_d18_per_10v ?? 0);
      const hasOffset = Math.abs(d13Offset) > 1e-12 || Math.abs(d18Offset) > 1e-12;
      next.manual_override_enabled = hasOffset;
      return next;
    });
  }

  function handleLinearityOffsetChange(field: LinearityOffsetField, rawValue: string) {
    const trimmed = rawValue.trim();
    if (trimmed === "") {
      updateSharedLinearity(field, null);
      return;
    }
    const parsed = Number(trimmed);
    updateSharedLinearity(field, Number.isFinite(parsed) ? parsed : 0);
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

              <div className="text-xs font-medium tracking-[0.01em] text-stone-500">
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
                <span className="rounded-full bg-stone-100 px-2 py-1 text-xs text-stone-600">Basis: {selectedLinearityBasisLabel}</span>
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
                  <label className="flex items-start gap-2 text-sm text-stone-700">
                    <input
                      type="checkbox"
                      className="mt-0.5 h-4 w-4 rounded border-stone-300 text-stone-900"
                      checked={Boolean(activeLinearity.quadratic)}
                      onChange={(event) => updateSharedLinearity("quadratic", event.target.checked)}
                    />
                    <span>Use quadratic linearity relationship</span>
                  </label>
                  <label className="text-sm">
                    <span className="mb-1 block text-stone-700">Linearity basis</span>
                    <select
                      value={selectedLinearityIntensityCol}
                      onChange={(event) => updateSharedLinearityIntensityCol(event.target.value)}
                      className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                    >
                      {LINEARITY_INTENSITY_OPTIONS.map((option) => (
                        <option key={option} value={option}>
                          {getLinearityIntensityOptionLabel(option)}
                        </option>
                      ))}
                    </select>
                  </label>
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
                      <div className="text-xs uppercase tracking-wide text-stone-500">d13C fitted coefficient</div>
                      <div className="mt-1 font-semibold text-stone-900">
                        {activeLinearity.quadratic
                          ? `${formatCoefficient(d13FitSlope)} (linear), ${formatCoefficient(d13FitQuad)} (quadratic)`
                          : formatCoefficient(d13FitSlope)}
                      </div>
                    </div>
                    <div className="rounded-lg border border-stone-200 p-3 text-sm">
                      <div className="text-xs uppercase tracking-wide text-stone-500">d18O fitted coefficient</div>
                      <div className="mt-1 font-semibold text-stone-900">
                        {activeLinearity.quadratic
                          ? `${formatCoefficient(d18FitSlope)} (linear), ${formatCoefficient(d18FitQuad)} (quadratic)`
                          : formatCoefficient(d18FitSlope)}
                      </div>
                    </div>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="text-sm">
                      <span className="mb-1 block text-stone-700">
                        {getLinearityCoefficientLabel("d13C", selectedLinearityIntensityCol, Boolean(activeLinearity.quadratic))}
                      </span>
                      <input
                        type="number"
                        step="0.01"
                        value={activeLinearity.quadratic ? (activeLinearity.manual_d13_per_10v2 ?? 0) : (activeLinearity.manual_d13_per_10v ?? 0)}
                        onChange={(event) => updateLinearityCoefficientOffset("d13C", Number(event.target.value))}
                        className="w-full rounded-lg border border-stone-300 px-3 py-2"
                      />
                    </label>
                    <label className="text-sm">
                      <span className="mb-1 block text-stone-700">
                        {getLinearityCoefficientLabel("d18O", selectedLinearityIntensityCol, Boolean(activeLinearity.quadratic))}
                      </span>
                      <input
                        type="number"
                        step="0.01"
                        value={activeLinearity.quadratic ? (activeLinearity.manual_d18_per_10v2 ?? 0) : (activeLinearity.manual_d18_per_10v ?? 0)}
                        onChange={(event) => updateLinearityCoefficientOffset("d18O", Number(event.target.value))}
                        className="w-full rounded-lg border border-stone-300 px-3 py-2"
                      />
                    </label>
                  </div>
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
                            type="number"
                            step="0.01"
                            value={readLinearityOffsetValue(activeLinearity, "line_1_offset_d13")}
                            onChange={(event) => handleLinearityOffsetChange("line_1_offset_d13", event.target.value)}
                            className="w-full rounded-lg border border-stone-300 px-3 py-2"
                          />
                        </label>
                        <label className="text-sm">
                          <span className="mb-1 block text-stone-700">d18O</span>
                          <input
                            type="number"
                            step="0.01"
                            value={readLinearityOffsetValue(activeLinearity, "line_1_offset_d18")}
                            onChange={(event) => handleLinearityOffsetChange("line_1_offset_d18", event.target.value)}
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
                            type="number"
                            step="0.01"
                            value={readLinearityOffsetValue(activeLinearity, "line_2_offset_d13")}
                            onChange={(event) => handleLinearityOffsetChange("line_2_offset_d13", event.target.value)}
                            className="w-full rounded-lg border border-stone-300 px-3 py-2"
                          />
                        </label>
                        <label className="text-sm">
                          <span className="mb-1 block text-stone-700">d18O</span>
                          <input
                            type="number"
                            step="0.01"
                            value={readLinearityOffsetValue(activeLinearity, "line_2_offset_d18")}
                            onChange={(event) => handleLinearityOffsetChange("line_2_offset_d18", event.target.value)}
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
            </CardHeader>
            <CardContent>
              <div className="w-full" style={{ height: diagnosticsMatrixHeight }}>
                <PlotlyChart figure={displayedDiagnosticsFigure} className="h-full w-full" />
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
