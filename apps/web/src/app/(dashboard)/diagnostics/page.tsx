"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { PlotlyChart } from "@/components/charts/plotly-chart";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { MultiSelectDropdown } from "@/components/ui/multi-select-dropdown";
import { api } from "@/lib/api";
import { useSessionStore } from "@/store/use-session-store";

const RANGE_FETCH_DEBOUNCE_MS = 300;
type ColorScaleBounds = {
  min: number;
  max: number;
};
type FigureShape = Record<string, unknown> & {
  data: Array<Record<string, unknown>>;
  layout: Record<string, unknown>;
};

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
  const [colorParam, setColorParam] = useState("Date");
  const [identifierFilter, setIdentifierFilter] = useState<string[]>([]);
  const [d13Range, setD13Range] = useState<[number, number] | null>(null);
  const [d18Range, setD18Range] = useState<[number, number] | null>(null);
  const [appliedD13Range, setAppliedD13Range] = useState<[number, number] | null>(null);
  const [appliedD18Range, setAppliedD18Range] = useState<[number, number] | null>(null);
  const [colorScaleRange, setColorScaleRange] = useState<[number, number] | null>(null);
  const [colorScaleRangeParam, setColorScaleRangeParam] = useState<string | null>(null);

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

  useEffect(() => {
    if (availableColorParams.length && !availableColorParams.includes(colorParam)) {
      setColorParam(availableColorParams[0]);
    }
  }, [availableColorParams, colorParam]);

  useEffect(() => {
    const bounds = colorScaleBounds ?? { min: 0, max: 1 };
    const parameterChanged = colorScaleRangeParam !== colorParam;
    const fullRange: [number, number] = [bounds.min, bounds.max];
    setColorScaleRange((current) => {
      if (!current || parameterChanged) {
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
        <aside className="space-y-6 xl:sticky xl:top-6 xl:self-start">
          <Card>
            <CardHeader>
              <CardTitle>Diagnostics Controls</CardTitle>
              <CardDescription>Configure filters and visual encoding for the diagnostics charts.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6 xl:max-h-[calc(100vh-12rem)] xl:overflow-y-auto xl:pr-2">
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
