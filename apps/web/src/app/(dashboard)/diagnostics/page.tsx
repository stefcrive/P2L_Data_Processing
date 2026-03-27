"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { PlotlyChart } from "@/components/charts/plotly-chart";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { MultiSelectDropdown } from "@/components/ui/multi-select-dropdown";
import { api } from "@/lib/api";
import { useSessionStore } from "@/store/use-session-store";

const RANGE_FETCH_DEBOUNCE_MS = 300;

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
  const [colorParam, setColorParam] = useState("Date_ordinal");
  const [identifierFilter, setIdentifierFilter] = useState<string[]>([]);
  const [d13Range, setD13Range] = useState<[number, number] | null>(null);
  const [d18Range, setD18Range] = useState<[number, number] | null>(null);
  const [appliedD13Range, setAppliedD13Range] = useState<[number, number] | null>(null);
  const [appliedD18Range, setAppliedD18Range] = useState<[number, number] | null>(null);

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
  const availableColorParams = useMemo(() => asStringArray(summary.available_color_params), [summary.available_color_params]);
  const availableIdentifiers = useMemo(() => asStringArray(summary.available_identifiers), [summary.available_identifiers]);
  const d13Bounds = useMemo(() => asRange(summary.d13_bounds), [summary.d13_bounds]);
  const d18Bounds = useMemo(() => asRange(summary.d18_bounds), [summary.d18_bounds]);
  const diagnosticsMatrixHeight = useMemo(() => resolveFigureHeight(data?.figures?.diagnostics, 2600), [data?.figures?.diagnostics]);

  useEffect(() => {
    if (availableColorParams.length && !availableColorParams.includes(colorParam)) {
      setColorParam(availableColorParams[0]);
    }
  }, [availableColorParams, colorParam]);

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
                <PlotlyChart figure={data?.figures?.diagnostics} className="h-full w-full" />
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
