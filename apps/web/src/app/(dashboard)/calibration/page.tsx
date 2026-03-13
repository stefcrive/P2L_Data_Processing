"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useEffect, useState } from "react";

import { PlotlyChart, type PlotlyPoint } from "@/components/charts/plotly-chart";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { MultiSelectDropdown } from "@/components/ui/multi-select-dropdown";
import { api } from "@/lib/api";
import type { CalibrationConfig, CalibrationPrecisionSummary, CalibrationWorkspace, CycleDiagnosticsPayload } from "@/lib/types";
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
type FigureShape = Record<string, unknown> & {
  data: Array<Record<string, unknown>>;
  layout: Record<string, unknown>;
};
type SelectionSourceChart = {
  title: string;
  description: string;
  figure?: Record<string, unknown>;
};

const PRECISION_PASS_THRESHOLD = 0.07;
const INCLUSION_PASS_THRESHOLD = 80;

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
  const normalized = key.toLowerCase();
  if (!normalized) {
    return null;
  }
  if (normalized === "crossplot" || normalized === "calibration_3d" || normalized === "processing_3d") {
    return "cross";
  }
  if (normalized === "vpdb(13c)" || normalized.endsWith("|d13c") || normalized.includes("13c")) {
    return "d13C";
  }
  if (normalized === "vsmow(18o)" || normalized.endsWith("|d18o") || normalized.includes("18o")) {
    return "d18O";
  }
  return null;
}

function coerceVector(values: unknown): unknown[] | null {
  if (Array.isArray(values)) {
    return values;
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

function extractPointCustomData(point: PlotlyPoint): unknown {
  if (point.customdata != null) {
    return point.customdata;
  }
  const payload = point as unknown as Record<string, unknown>;
  const pointNumber = typeof payload.pointNumber === "number" ? payload.pointNumber : typeof payload.pointIndex === "number" ? payload.pointIndex : null;
  if (pointNumber == null) {
    return null;
  }
  const dataCandidate = (payload.data ?? payload.fullData) as Record<string, unknown> | undefined;
  const traceCustomdata = coerceVector(dataCandidate?.customdata);
  if (!traceCustomdata || pointNumber < 0 || pointNumber >= traceCustomdata.length) {
    return null;
  }
  return traceCustomdata[pointNumber];
}

function parseSelectedTargets(points: PlotlyPoint[], chartKey: string): SelectedTarget[] {
  const seen = new Set<string>();
  const targets: SelectedTarget[] = [];
  const inferredIsotope = inferIsotopeKeyFromChartKey(chartKey);
  for (const point of points) {
    const rawCustomdata = extractPointCustomData(point);
    const customdata = Array.isArray(rawCustomdata) ? rawCustomdata : null;
    const customObj = rawCustomdata && typeof rawCustomdata === "object" ? (rawCustomdata as Record<string, unknown>) : null;
    const hasArrayRowPayload = Boolean(customdata && customdata.length >= 4);
    const hasObjectRowPayload = Boolean(customObj && ("row_label" in customObj || "rowLabel" in customObj));
    if (!hasArrayRowPayload && !hasObjectRowPayload) {
      continue;
    }
    const rowLabel = String(customdata?.[0] ?? customObj?.row_label ?? customObj?.rowLabel ?? "").trim();
    const isotopeKey = normalizeIsotopeKey(customdata?.[1] ?? customObj?.isotope_key ?? customObj?.isotopeKey) ?? inferredIsotope;
    const identifier1 = String(customdata?.[2] ?? customObj?.identifier_1 ?? customObj?.identifier1 ?? "").trim();
    const identifier2 = String(customdata?.[3] ?? customObj?.identifier_2 ?? customObj?.identifier2 ?? "").trim();
    if (!rowLabel || !isotopeKey) {
      continue;
    }
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
      currentValue: typeof point.y === "number" ? point.y : null,
      currentD13: isotopeKey === "cross" && typeof point.y === "number" ? point.y : null,
      currentD18: isotopeKey === "cross" && typeof point.x === "number" ? point.x : null,
      chartKey,
    });
  }
  return targets;
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
      // Fall through.
    }
  }
  return {
    ...figure,
    data: Array.isArray(figure.data) ? (figure.data as Array<Record<string, unknown>>).map((trace) => ({ ...trace })) : [],
    layout: typeof figure.layout === "object" && figure.layout ? { ...(figure.layout as Record<string, unknown>) } : {},
  };
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

function highlightSelectionSourceFigure(
  figure: Record<string, unknown> | undefined,
  target: SelectedTarget | null,
): Record<string, unknown> | undefined {
  if (!figure || !target) {
    return figure;
  }
  const cloned = cloneFigure(figure);
  const traces = Array.isArray(cloned.data) ? cloned.data : [];
  const highlightTraces: Array<Record<string, unknown>> = [];
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
    const traceType = String(trace.type ?? "scatter");
    const is3dTrace = traceType.includes("3d");
    const highlightColor = "#FF00FF";
    const traceMarker = trace.marker && typeof trace.marker === "object" ? (trace.marker as Record<string, unknown>) : {};
    const baseSize = typeof traceMarker.size === "number" ? traceMarker.size : 8;
    trace.selectedpoints = indexes;
    trace.selected = {
      marker: {
        symbol: "circle",
        size: Math.max(baseSize + 5, 13),
        color: "rgba(255, 0, 255, 0.28)",
        line: {
          color: highlightColor,
          width: 3,
        },
      },
    };
    trace.unselected = {
      marker: {
        opacity: 1,
      },
    };
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
        size: is3dTrace ? 14 : 18,
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

function roundDeltaValue(value: number, precision = 3): number {
  return Number(value.toFixed(precision));
}

function formatDeltaValue(value: number | null | undefined, precision = 3): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(precision) : "N/A";
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

function isDeltaColumnLabel(label: string): boolean {
  const normalized = label.trim().toLowerCase();
  return normalized.includes("d13") || normalized.includes("d18");
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

  if (traces.length === existingTraceCount) {
    return cloned;
  }
  return {
    ...cloned,
    data: traces,
  };
}

function buildLinearityPreviewFigure(
  cycleMean: Record<string, unknown>,
  isotopeKey: string,
  tableRows: Array<Record<string, unknown>>,
) {
  const fitPairs: Array<[number, number]> = [];
  const rawFitX = Array.isArray(cycleMean.fit_x) ? cycleMean.fit_x : [];
  const rawFitY = Array.isArray(cycleMean.fit_y) ? cycleMean.fit_y : [];
  const pairCount = Math.min(rawFitX.length, rawFitY.length);
  for (let i = 0; i < pairCount; i += 1) {
    const x = toFiniteNumber(rawFitX[i]);
    const y = toFiniteNumber(rawFitY[i]);
    if (x == null || y == null) {
      continue;
    }
    fitPairs.push([x, y]);
  }

  if (fitPairs.length < 2) {
    const yCol = isotopeKey === "d18O" ? "d18O" : isotopeKey === "d13C" ? "d13C" : null;
    const excludedCol = isotopeKey === "d18O" ? "Excluded d18O" : isotopeKey === "d13C" ? "Excluded d13C" : null;
    if (yCol) {
      for (const row of tableRows) {
        if (excludedCol && asBoolean(row[excludedCol])) {
          continue;
        }
        const x = toFiniteNumber(row["SMP Int m/z 44 (V)"]);
        const y = toFiniteNumber(row[yCol]);
        if (x == null || y == null) {
          continue;
        }
        fitPairs.push([x, y]);
      }
    }
  }

  if (fitPairs.length < 2) {
    return null;
  }

  const fitX = fitPairs.map(([x]) => x);
  const fitY = fitPairs.map(([, y]) => y);
  const slope = toFiniteNumber(cycleMean.linearity_slope);
  const intercept = toFiniteNumber(cycleMean.linearity_intercept);
  const targetIntensity = toFiniteNumber(cycleMean.linearity_target_intensity);
  const prediction = toFiniteNumber(cycleMean.linearity_prediction);
  const xMin = Math.min(...fitX);
  const xMax = Math.max(...fitX);
  const lineX = [xMin, xMax];
  const lineY =
    slope != null && intercept != null
      ? [slope * xMin + intercept, slope * xMax + intercept]
      : [fitY[0], fitY[fitY.length - 1]];

  const traces: Array<Record<string, unknown>> = [
    {
      type: "scatter",
      mode: "markers",
      name: "Valid cycles",
      x: fitX,
      y: fitY,
      marker: { color: "#334155", size: 7 },
    },
    {
      type: "scatter",
      mode: "lines",
      name: "Linear fit",
      x: lineX,
      y: lineY,
      line: { color: "#0F766E", width: 2 },
    },
  ];

  if (targetIntensity != null && prediction != null) {
    traces.push({
      type: "scatter",
      mode: "markers",
      name: "Target prediction",
      x: [targetIntensity],
      y: [prediction],
      marker: { color: "#B91C1C", size: 10, symbol: "diamond" },
    });
  }

  return {
    data: traces,
    layout: {
      title: `${isotopeKey || "Isotope"} linearity preview`,
      xaxis: { title: "Intensity (V)" },
      yaxis: { title: "Cycle delta" },
      height: 300,
      margin: { l: 30, r: 20, t: 45, b: 35 },
      legend: { orientation: "h", yanchor: "top", y: -0.22, x: 0 },
    },
  };
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

function CycleDiagnosticsTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  if (!rows.length) {
    return <div className="rounded-lg border border-dashed border-stone-300 p-4 text-sm text-stone-500">No cycle rows returned for this point.</div>;
  }

  const statusRows: Array<Record<string, unknown>> = rows.map((row) => {
    const excludedD13 = asBoolean(row["Excluded d13C"]);
    const excludedD18 = asBoolean(row["Excluded d18O"]);
    const excludedAny = asBoolean(row["Excluded (Saturation)"]) || excludedD13 || excludedD18;
    return {
      ...row,
      "Cycle status": excludedAny ? "Saturated" : "Successful",
    };
  });

  const preferredColumns = [
    "Cycle",
    "Cycle status",
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
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-full bg-emerald-100 px-2 py-1 text-emerald-800">Successful cycle</span>
        <span className="rounded-full bg-rose-100 px-2 py-1 text-rose-800">Saturated cycle</span>
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
              return (
                <tr key={rowIndex} className={cn(saturated ? "bg-rose-50/80" : "bg-emerald-50/70")}>
                  {columns.map((column) => {
                    const cellValue = row[column];
                    const flaggedColumn = column.startsWith("Excluded");
                    const flaggedValue = flaggedColumn ? asBoolean(cellValue) : false;
                    return (
                      <td
                        key={column}
                        className={cn(
                          "px-3 py-2",
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

function diagnosticsTargetPayload(target: SelectedTarget, isotopeKey?: "d13C" | "d18O") {
  return {
    target: {
      row_label: target.rowLabel,
      isotope_key: isotopeKey ?? (target.isotopeKey as "d13C" | "d18O"),
    },
  };
}

function DiagnosticsPanel({
  title,
  diagnostics,
  loading,
  showLinearityChart = false,
  linearityEnabled,
  onLinearityEnabledChange,
  linearityTargetIntensity,
  onLinearityTargetIntensityChange,
  onPickDeltaValue,
}: {
  title: string;
  diagnostics?: CycleDiagnosticsPayload;
  loading: boolean;
  showLinearityChart?: boolean;
  linearityEnabled?: boolean;
  onLinearityEnabledChange?: (value: boolean) => void;
  linearityTargetIntensity?: number;
  onLinearityTargetIntensityChange?: (value: number) => void;
  onPickDeltaValue?: (value: number) => void;
}) {
  const cycleMean = diagnostics?.cycle_mean ?? {};
  const validMean = asNumber(cycleMean.valid_mean);
  const finalMean = asNumber(cycleMean.mean);
  const targetIntensity = asNumber(cycleMean.linearity_target_intensity);
  const prediction = asNumber(cycleMean.linearity_prediction);
  const prevNeighbor = cycleMean.prev_neighbor as Record<string, unknown> | undefined;
  const nextNeighbor = cycleMean.next_neighbor as Record<string, unknown> | undefined;
  const reason = asString(cycleMean.reason);
  const diagnosticsFigure = ensureCollectorIntensityTraces(diagnostics?.figure, diagnostics?.table ?? []);
  const isotopeKey = asString((diagnostics?.target ?? {})["isotope_key"]);
  const linearityPreviewFigure = buildLinearityPreviewFigure(cycleMean, isotopeKey, diagnostics?.table ?? []);
  const showLinearityControls =
    typeof linearityEnabled === "boolean" &&
    typeof onLinearityEnabledChange === "function" &&
    typeof linearityTargetIntensity === "number" &&
    typeof onLinearityTargetIntensityChange === "function";
  const shouldRenderLinearityPreview = showLinearityChart || Boolean(linearityEnabled);
  const canPickValidMean = typeof onPickDeltaValue === "function" && validMean != null;
  const canPickFinalMean = typeof onPickDeltaValue === "function" && finalMean != null;

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
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              <button
                type="button"
                onClick={() => {
                  if (canPickValidMean) {
                    onPickDeltaValue(validMean);
                  }
                }}
                disabled={!canPickValidMean}
                className={cn(
                  "rounded-lg border border-stone-200 p-3 text-left transition",
                  canPickValidMean ? "cursor-pointer hover:border-fuchsia-400 hover:bg-fuchsia-50" : "",
                )}
              >
                <div className="text-xs uppercase tracking-wide text-stone-500">Valid Cycle Mean</div>
                <div className="mt-1 text-lg font-semibold text-stone-900">{formatDeltaValue(validMean)}</div>
              </button>
              <button
                type="button"
                onClick={() => {
                  if (canPickFinalMean) {
                    onPickDeltaValue(finalMean);
                  }
                }}
                disabled={!canPickFinalMean}
                className={cn(
                  "rounded-lg border border-stone-200 p-3 text-left transition",
                  canPickFinalMean ? "cursor-pointer hover:border-fuchsia-400 hover:bg-fuchsia-50" : "",
                )}
              >
                <div className="text-xs uppercase tracking-wide text-stone-500">Final Mean</div>
                <div className="mt-1 text-lg font-semibold text-stone-900">{formatDeltaValue(finalMean)}</div>
              </button>
              <div className="rounded-lg border border-stone-200 p-3">
                <div className="text-xs uppercase tracking-wide text-stone-500">Method</div>
                <div className="mt-1 text-sm font-medium text-stone-900">{asString(cycleMean.method) || "N/A"}</div>
              </div>
            </div>

            {showLinearityControls ? (
              <div className="space-y-3 rounded-lg border border-stone-200 p-3">
                <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_220px] md:items-end">
                  <label className={cn("flex items-start gap-3 rounded-lg border border-stone-200 p-3")}>
                    <input
                      type="checkbox"
                      checked={Boolean(linearityEnabled)}
                      onChange={(event) => onLinearityEnabledChange(event.target.checked)}
                      className="mt-1 h-4 w-4"
                    />
                    <span className="space-y-1">
                      <span className="block text-sm font-medium text-stone-800">Preview linearity-corrected cycle mean</span>
                    </span>
                  </label>
                  <label className="text-sm">
                    <span className="mb-1 block text-stone-700">Target intensity (V)</span>
                    <input
                      type="number"
                      step="0.1"
                      value={linearityTargetIntensity}
                      onChange={(event) => onLinearityTargetIntensityChange(Number(event.target.value))}
                      className="w-full rounded-lg border border-stone-300 px-3 py-2"
                    />
                  </label>
                </div>
              </div>
            ) : null}

            {shouldRenderLinearityPreview ? (
              linearityPreviewFigure ? (
                <PlotlyChart figure={linearityPreviewFigure} className="h-[300px] w-full" />
              ) : (
                <div className="rounded-lg border border-dashed border-stone-300 p-3 text-sm text-stone-500">
                  No linearity fit available for this selection.
                </div>
              )
            ) : null}

            {prediction != null || targetIntensity != null ? (
              <div className="grid gap-3 md:grid-cols-2">
                <div className="rounded-lg border border-stone-200 p-3 text-sm text-stone-700">
                  <div className="font-medium text-stone-800">Linearity Target Intensity</div>
                  <div>{targetIntensity == null ? "N/A" : `${targetIntensity.toFixed(2)} V`}</div>
                </div>
                <div className="rounded-lg border border-stone-200 p-3 text-sm text-stone-700">
                  <div className="font-medium text-stone-800">Linearity Prediction</div>
                  <div>{formatDeltaValue(prediction)}</div>
                </div>
              </div>
            ) : null}

            {prevNeighbor || nextNeighbor ? (
              <div className="grid gap-3 md:grid-cols-2">
                <div className="rounded-lg border border-stone-200 p-3 text-sm text-stone-700">
                  <div className="font-medium text-stone-800">Previous interpolation neighbor</div>
                  <div>{prevNeighbor ? `${asString(prevNeighbor.identifier_2)} -> ${asString(prevNeighbor.value)}` : "Not available"}</div>
                </div>
                <div className="rounded-lg border border-stone-200 p-3 text-sm text-stone-700">
                  <div className="font-medium text-stone-800">Next interpolation neighbor</div>
                  <div>{nextNeighbor ? `${asString(nextNeighbor.identifier_2)} -> ${asString(nextNeighbor.value)}` : "Not available"}</div>
                </div>
              </div>
            ) : null}

            {reason ? <div className="text-sm text-stone-500">Diagnostics note: {reason}</div> : null}

            <div className="grid gap-4 xl:grid-cols-2 xl:items-start">
              <PlotlyChart figure={diagnosticsFigure} className="mx-auto aspect-square min-h-[320px] w-full max-w-[560px]" />
              <div className="min-w-0">
                <CycleDiagnosticsTable rows={diagnostics.table ?? []} />
              </div>
            </div>
          </>
        ) : loading ? null : (
          <div className="text-sm text-stone-500">Cycle diagnostics appear here once a point is selected.</div>
        )}
      </CardContent>
    </Card>
  );
}

function formatMetric(value?: number | null, digits = 3) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "N/A";
  }
  return value.toFixed(digits);
}

function formatMetricWithUnit(value?: number | null, digits = 3) {
  const formatted = formatMetric(value, digits);
  return formatted === "N/A" ? formatted : `${formatted} ‰`;
}

function classifyPrecision(value?: number | null) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "neutral" as const;
  }
  return value < PRECISION_PASS_THRESHOLD ? ("pass" as const) : ("fail" as const);
}

function classifyInclusion(pct?: number | null) {
  if (typeof pct !== "number" || Number.isNaN(pct)) {
    return "neutral" as const;
  }
  return pct > INCLUSION_PASS_THRESHOLD ? ("pass" as const) : ("fail" as const);
}

function toneClasses(tone: "pass" | "fail" | "neutral") {
  if (tone === "pass") {
    return {
      shell: "border-emerald-200 bg-emerald-50/90",
      value: "text-emerald-800",
      subtle: "text-emerald-700",
    };
  }
  if (tone === "fail") {
    return {
      shell: "border-rose-200 bg-rose-50/90",
      value: "text-rose-800",
      subtle: "text-rose-700",
    };
  }
  return {
    shell: "border-stone-200 bg-white",
    value: "text-stone-900",
    subtle: "text-stone-600",
  };
}

function DataTable({ rows, emptyLabel }: { rows: Array<Record<string, unknown>>; emptyLabel: string }) {
  if (!rows.length) {
    return <div className="rounded-lg border border-dashed border-stone-300 p-4 text-sm text-stone-500">{emptyLabel}</div>;
  }
  const columns = Object.keys(rows[0] ?? {});
  return (
    <div className="overflow-x-auto rounded-lg border border-stone-200">
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
        <tbody className="divide-y divide-stone-100 bg-white">
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} className="px-3 py-2 text-stone-600">
                  {String(row[column] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SummaryChip({ label, value, hint, tone = "neutral" }: { label: string; value: string; hint?: string; tone?: "pass" | "fail" | "neutral" }) {
  const styles = toneClasses(tone);
  return (
    <div className={`min-w-[140px] rounded-xl border px-3 py-2 text-left shadow-sm ${styles.shell}`}>
      <div className="text-[11px] uppercase tracking-[0.14em] text-stone-500">{label}</div>
      <div className={`mt-1 text-base font-semibold tabular-nums ${styles.value}`}>{value}</div>
      {hint ? <div className={`mt-0.5 text-xs ${styles.subtle}`}>{hint}</div> : null}
    </div>
  );
}

function PrecisionMetricPanel({ label, value }: { label: string; value?: number | null }) {
  const styles = toneClasses(classifyPrecision(value));
  return (
    <div className={`rounded-xl border px-3 py-3 ${styles.shell}`}>
      <div className="text-[11px] uppercase tracking-[0.14em] text-stone-500">{label}</div>
      <div className={`mt-2 text-2xl font-semibold leading-none tabular-nums ${styles.value}`}>{formatMetricWithUnit(value)}</div>
    </div>
  );
}

function IsotopeSummaryTile({
  label,
  precision,
  average,
  correctedPrecision,
}: {
  label: string;
  precision?: number | null;
  average?: number | null;
  correctedPrecision?: number | null;
}) {
  return (
    <div className="rounded-xl border border-stone-200 bg-stone-50/80 p-4">
      <div className="text-xs uppercase tracking-[0.14em] text-stone-500">{label} precision</div>
      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <PrecisionMetricPanel label="Normal" value={precision} />
        <PrecisionMetricPanel label="Linearity corrected" value={correctedPrecision} />
      </div>
      <div className="mt-4 space-y-2 text-sm">
        <div className="flex items-center justify-between border-t border-stone-200 pt-2 text-stone-600">
          <span>{label} average</span>
          <span className="font-medium tabular-nums text-stone-900">{formatMetricWithUnit(average)}</span>
        </div>
      </div>
    </div>
  );
}

function PrecisionCard({ summary }: { summary: CalibrationPrecisionSummary }) {
  const linePrecisionEntries = Object.entries(summary.line_precisions).sort(([left], [right]) => {
    const leftNumber = Number(left);
    const rightNumber = Number(right);
    if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
      return leftNumber - rightNumber;
    }
    return left.localeCompare(right);
  });

  return (
    <Card className="w-full overflow-hidden border-stone-300">
      <CardHeader className="gap-4 border-b border-stone-200 bg-gradient-to-r from-stone-50 via-white to-stone-50 pb-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-1">
            <CardTitle className="text-2xl tracking-tight">{summary.standard}</CardTitle>
            <CardDescription>
              Calibration quality snapshot across included standards and isotope precision metrics. Green marks precision &lt; 0.07 ‰ and inclusion &gt; 80%.
            </CardDescription>
          </div>
          <div className="grid gap-2 sm:grid-cols-3">
            <SummaryChip label="Measurements" value={String(summary.total_rows)} />
            <SummaryChip
              label="δ13C included"
              value={`${summary.included_d13}/${summary.total_rows}`}
              hint={`${summary.included_pct_d13.toFixed(1)}% retained`}
              tone={classifyInclusion(summary.included_pct_d13)}
            />
            <SummaryChip
              label="δ18O included"
              value={`${summary.included_d18}/${summary.total_rows}`}
              hint={`${summary.included_pct_d18.toFixed(1)}% retained`}
              tone={classifyInclusion(summary.included_pct_d18)}
            />
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5 p-6">
        <div className="grid gap-4 md:grid-cols-2">
          <IsotopeSummaryTile
            label="δ13C (‰)"
            precision={summary.d13_precision}
            average={summary.d13_average}
            correctedPrecision={summary.d13_linearity_corrected_precision}
          />
          <IsotopeSummaryTile
            label="δ18O (‰)"
            precision={summary.d18_precision}
            average={summary.d18_average}
            correctedPrecision={summary.d18_linearity_corrected_precision}
          />
        </div>
        {linePrecisionEntries.length ? (
          <div className="rounded-xl border border-stone-200 p-4">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-xs uppercase tracking-[0.14em] text-stone-500">Line precision breakdown</div>
              <div className="text-xs text-stone-500">{linePrecisionEntries.length} lines</div>
            </div>
            <div className="overflow-hidden rounded-lg border border-stone-200">
              <div className="grid grid-cols-[100px_minmax(0,1fr)_minmax(0,1fr)] bg-stone-100 px-3 py-2 text-xs font-medium uppercase tracking-wide text-stone-600">
                <span>Line</span>
                <span>δ13C (‰)</span>
                <span>δ18O (‰)</span>
              </div>
              {linePrecisionEntries.map(([line, values], index) => {
                const d13Tone = toneClasses(classifyPrecision(values.d13_precision));
                const d18Tone = toneClasses(classifyPrecision(values.d18_precision));
                return (
                  <div
                    key={line}
                    className={`grid grid-cols-[100px_minmax(0,1fr)_minmax(0,1fr)] px-3 py-2 text-sm tabular-nums text-stone-700 ${
                      index % 2 ? "bg-stone-50" : "bg-white"
                    }`}
                  >
                    <span className="font-medium text-stone-900">Line {line}</span>
                    <span className={`inline-flex max-w-fit rounded-md border px-2 py-1 text-base font-semibold ${d13Tone.shell} ${d13Tone.value}`}>
                      {formatMetricWithUnit(values.d13_precision)}
                    </span>
                    <span className={`inline-flex max-w-fit rounded-md border px-2 py-1 text-base font-semibold ${d18Tone.shell} ${d18Tone.value}`}>
                      {formatMetricWithUnit(values.d18_precision)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export default function CalibrationPage() {
  const sessionId = useSessionStore((state) => state.sessionId);
  const queryClient = useQueryClient();
  const [config, setConfig] = useState<CalibrationConfig | null>(null);
  const [hasLoadedDraft, setHasLoadedDraft] = useState(false);
  const [selectedTargets, setSelectedTargets] = useState<SelectedTarget[]>([]);
  const [activeTargetIndex, setActiveTargetIndex] = useState(0);
  const [isSelectionEditorOpen, setSelectionEditorOpen] = useState(false);
  const [singleValue, setSingleValue] = useState(0);
  const [singleOffset, setSingleOffset] = useState(0);
  const [crossD13Value, setCrossD13Value] = useState(0);
  const [crossD18Value, setCrossD18Value] = useState(0);
  const [multiOffsetD13, setMultiOffsetD13] = useState(0);
  const [multiOffsetD18, setMultiOffsetD18] = useState(0);
  const [linearityEnabled, setLinearityEnabled] = useState(false);
  const [linearityTargetIntensity, setLinearityTargetIntensity] = useState(15);
  const [setValueHighlightNonce, setSetValueHighlightNonce] = useState(0);
  const [isSetValueInputHighlighted, setIsSetValueInputHighlighted] = useState(false);
  const draftStorageKey = sessionId ? `calibration-config:${sessionId}` : null;
  const activeTarget = selectedTargets.length ? selectedTargets[Math.min(activeTargetIndex, selectedTargets.length - 1)] : null;
  const activeIsotopeTarget = activeTarget && activeTarget.isotopeKey !== "cross" ? activeTarget : null;
  const activeCrossTarget = activeTarget && activeTarget.isotopeKey === "cross" ? activeTarget : null;

  useEffect(() => {
    if (!isSelectionEditorOpen || typeof window === "undefined") {
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setSelectionEditorOpen(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isSelectionEditorOpen]);

  useEffect(() => {
    if (!activeIsotopeTarget) {
      return;
    }
    setSingleOffset(0);
    setLinearityEnabled(false);
  }, [activeIsotopeTarget?.rowLabel, activeIsotopeTarget?.isotopeKey]);

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

  const workspaceQuery = useQuery({
    queryKey: ["calibration-workspace", sessionId],
    queryFn: () => api.getCalibrationWorkspace(sessionId!),
    enabled: Boolean(sessionId),
  });

  const singleDiagnosticsQuery = useQuery({
    queryKey: [
      "processing-diagnostics",
      sessionId,
      activeIsotopeTarget?.rowLabel,
      "d18O",
      linearityEnabled,
      linearityTargetIntensity,
    ],
    queryFn: () =>
      api.getProcessingCycleDiagnostics(sessionId!, {
        ...diagnosticsTargetPayload(activeIsotopeTarget!, "d18O"),
        correct_linearity: linearityEnabled,
        target_intensity: linearityEnabled ? linearityTargetIntensity : null,
      }),
    enabled: Boolean(sessionId && activeIsotopeTarget),
  });

  const crossD13DiagnosticsQuery = useQuery({
    queryKey: ["processing-diagnostics-cross-d13", sessionId, activeCrossTarget?.rowLabel],
    queryFn: () => api.getProcessingCycleDiagnostics(sessionId!, diagnosticsTargetPayload(activeCrossTarget!, "d13C")),
    enabled: Boolean(sessionId && activeCrossTarget),
  });

  const crossD18DiagnosticsQuery = useQuery({
    queryKey: ["processing-diagnostics-cross-d18", sessionId, activeCrossTarget?.rowLabel],
    queryFn: () => api.getProcessingCycleDiagnostics(sessionId!, diagnosticsTargetPayload(activeCrossTarget!, "d18O")),
    enabled: Boolean(sessionId && activeCrossTarget),
  });

  useEffect(() => {
    if (!activeIsotopeTarget) {
      return;
    }
    setSingleValue(
      activeIsotopeTarget.isotopeKey === "d18O" && typeof singleDiagnosticsQuery.data?.target?.current_value === "number"
        ? roundDeltaValue(singleDiagnosticsQuery.data.target.current_value as number)
        : roundDeltaValue(activeIsotopeTarget.currentValue ?? 0),
    );
  }, [activeIsotopeTarget, singleDiagnosticsQuery.data?.target]);

  useEffect(() => {
    if (activeCrossTarget) {
      const nextD13 =
        typeof crossD13DiagnosticsQuery.data?.target?.current_value === "number"
          ? roundDeltaValue(crossD13DiagnosticsQuery.data.target.current_value as number)
          : roundDeltaValue(activeCrossTarget.currentD13 ?? 0);
      const nextD18 =
        typeof crossD18DiagnosticsQuery.data?.target?.current_value === "number"
          ? roundDeltaValue(crossD18DiagnosticsQuery.data.target.current_value as number)
          : roundDeltaValue(activeCrossTarget.currentD18 ?? 0);
      setCrossD13Value(nextD13);
      setCrossD18Value(nextD18);
    }
  }, [activeCrossTarget, crossD13DiagnosticsQuery.data?.target, crossD18DiagnosticsQuery.data?.target]);

  useEffect(() => {
    setConfig(null);
    setHasLoadedDraft(false);
  }, [sessionId]);

  useEffect(() => {
    if (!draftStorageKey || typeof window === "undefined") {
      return;
    }
    const raw = window.sessionStorage.getItem(draftStorageKey);
    if (!raw) {
      setHasLoadedDraft(true);
      return;
    }
    try {
      setConfig(JSON.parse(raw) as CalibrationConfig);
    } catch {
      window.sessionStorage.removeItem(draftStorageKey);
    } finally {
      setHasLoadedDraft(true);
    }
  }, [draftStorageKey]);

  useEffect(() => {
    if (!workspaceQuery.data || !hasLoadedDraft) {
      return;
    }
    setConfig((current) => current ?? workspaceQuery.data.config);
  }, [hasLoadedDraft, workspaceQuery.data]);

  useEffect(() => {
    if (!draftStorageKey || typeof window === "undefined" || !config) {
      return;
    }
    window.sessionStorage.setItem(draftStorageKey, JSON.stringify(config));
  }, [config, draftStorageKey]);

  const deferredConfig = useDeferredValue(config);

  const previewQuery = useQuery({
    queryKey: ["calibration-workspace-preview", sessionId, deferredConfig],
    queryFn: () => api.previewCalibrationWorkspace(sessionId!, deferredConfig!),
    enabled: Boolean(sessionId && deferredConfig),
  });

  const runMutation = useMutation({
    mutationFn: (payload: CalibrationConfig) => api.runCalibration(sessionId!, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration-workspace", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration-workspace-preview", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration", sessionId] });
    },
  });

  const editMutation = useMutation({
    mutationFn: (payload: {
      action: "set_value" | "offset" | "interpolate" | "reset_to_original" | "set_outlier_override";
      targets: Array<{ row_label: string; isotope_key: "d13C" | "d18O" }>;
      value?: number | null;
      offset?: number | null;
      is_outlier?: boolean | null;
    }) => api.editProcessing(sessionId!, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-workspace", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration-workspace", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration-workspace-preview", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration", sessionId] });
    },
  });

  function updateConfig<T extends keyof CalibrationConfig>(key: T, value: CalibrationConfig[T]) {
    setConfig((current) => (current ? { ...current, [key]: value } : current));
  }

  function updateLinearity(key: keyof CalibrationConfig["linearity"], value: boolean | number) {
    setConfig((current) =>
      current
        ? {
            ...current,
            linearity: {
              ...current.linearity,
              [key]: value,
            },
          }
        : current,
    );
  }

  function setTargets(nextTargets: SelectedTarget[]) {
    setSelectedTargets(nextTargets);
    setActiveTargetIndex(0);
    setSelectionEditorOpen(nextTargets.length > 0);
  }

  function openProcessingSelectionEditor(chartKey: string, points: PlotlyPoint[], multi = false) {
    const targets = parseSelectedTargets(points, chartKey);
    if (!targets.length) {
      return;
    }
    setTargets(multi ? targets : targets.slice(0, 1));
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

  function setSingleValueFromSuggestion(value: number) {
    setSingleValue(roundDeltaValue(value));
    setSetValueHighlightNonce((current) => current + 1);
  }

  async function applySingleValue() {
    if (!sessionId || !activeIsotopeTarget) {
      return;
    }
    await editMutation.mutateAsync({
      action: "set_value",
      targets: [{ row_label: activeIsotopeTarget.rowLabel, isotope_key: activeIsotopeTarget.isotopeKey as "d13C" | "d18O" }],
      value: singleValue,
    });
  }

  async function applySingleOffset() {
    if (!sessionId || !activeIsotopeTarget) {
      return;
    }
    await editMutation.mutateAsync({
      action: "offset",
      targets: [{ row_label: activeIsotopeTarget.rowLabel, isotope_key: activeIsotopeTarget.isotopeKey as "d13C" | "d18O" }],
      offset: singleOffset,
    });
  }

  async function applySingleInterpolate() {
    if (!sessionId || !activeIsotopeTarget) {
      return;
    }
    await editMutation.mutateAsync({
      action: "interpolate",
      targets: [{ row_label: activeIsotopeTarget.rowLabel, isotope_key: activeIsotopeTarget.isotopeKey as "d13C" | "d18O" }],
    });
  }

  async function applyCrossValues() {
    if (!sessionId || !activeCrossTarget) {
      return;
    }
    await editMutation.mutateAsync({
      action: "set_value",
      targets: [{ row_label: activeCrossTarget.rowLabel, isotope_key: "d13C" }],
      value: crossD13Value,
    });
    await editMutation.mutateAsync({
      action: "set_value",
      targets: [{ row_label: activeCrossTarget.rowLabel, isotope_key: "d18O" }],
      value: crossD18Value,
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
    if (!sessionId) {
      return;
    }
    const targets = buildTargetsForAction(selectedTargets);
    if (!targets.length) {
      return;
    }
    await editMutation.mutateAsync({
      action: "interpolate",
      targets,
    });
  }

  async function applyOutlierOverride(isOutlier: boolean) {
    if (!sessionId || !selectedTargets.length) {
      return;
    }
    await editMutation.mutateAsync({
      action: "set_outlier_override",
      targets: buildTargetsForAction(selectedTargets),
      is_outlier: isOutlier,
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
          <CardDescription>Import data first to unlock calibration.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (workspaceQuery.isLoading && !workspaceQuery.data) {
    return <div className="text-sm text-stone-500">Loading calibration workspace...</div>;
  }

  if (workspaceQuery.error) {
    return <div className="text-sm text-red-600">Failed to load calibration workspace.</div>;
  }

  const workspace = workspaceQuery.data as CalibrationWorkspace | undefined;
  const activeConfig = config ?? workspace?.config ?? null;
  const displayedWorkspace = (previewQuery.data as CalibrationWorkspace | undefined) ?? workspace;

  if (!workspace || !activeConfig || !displayedWorkspace) {
    return null;
  }

  const minDate = displayedWorkspace.available_values.min_date ?? undefined;
  const maxDate = displayedWorkspace.available_values.max_date ?? undefined;
  const selectedStandards = activeConfig.selected_standards;
  const lineIntensityBasis = String(displayedWorkspace.linearity_fits?.intensity_col ?? activeConfig.z_axis ?? "N/A");
  const previewError = previewQuery.error instanceof Error ? previewQuery.error.message : null;
  const runError = runMutation.error instanceof Error ? runMutation.error.message : null;
  const hasUnsavedPreview = JSON.stringify(activeConfig) !== JSON.stringify(workspace.config);
  const precisionSummaries = displayedWorkspace.precision_summaries;
  const linePrecisionCount = precisionSummaries.reduce((count, summary) => count + Object.keys(summary.line_precisions).length, 0);
  const busy = runMutation.isPending || editMutation.isPending;
  const selectedRowLabels = selectedTargets.map((target) => `${target.rowLabel}:${target.isotopeKey}`);
  const crossSharedDiagnostics = crossD18DiagnosticsQuery.data ?? crossD13DiagnosticsQuery.data;
  const crossSharedDiagnosticsLoading =
    (crossD18DiagnosticsQuery.isLoading && !crossD18DiagnosticsQuery.data) ||
    (!crossD18DiagnosticsQuery.data && crossD13DiagnosticsQuery.isLoading && !crossD13DiagnosticsQuery.data);
  const activeTargetInlineSummary =
    activeIsotopeTarget?.rowLabel === activeTarget?.rowLabel
      ? singleDiagnosticsQuery.data?.inline_summary
      : crossSharedDiagnostics?.inline_summary;
  const activeTargetInlineItems = parseInlineDiagnosticsSummary(activeTargetInlineSummary);
  const activeTargetInlineDisplayItems = activeTargetInlineItems.map((item) => {
    const numericValue = parseStrictNumber(item.value);
    const isDelta = isDeltaInlineLabel(item.label);
    const normalizedLabel = normalizeInlineLabel(item.label);
    const canSetSingleValue = Boolean(activeIsotopeTarget) && normalizedLabel === "d18o values" && numericValue != null;
    return {
      ...item,
      unit: unitForInlineLabel(item.label),
      value: isDelta && numericValue != null ? formatDeltaValue(numericValue) : item.value,
      canSetSingleValue,
      setValue: canSetSingleValue && numericValue != null ? roundDeltaValue(numericValue) : null,
    };
  });
  const effectiveOutlier =
    typeof singleDiagnosticsQuery.data?.target?.effective_outlier === "boolean"
      ? (singleDiagnosticsQuery.data.target.effective_outlier as boolean)
      : typeof crossD13DiagnosticsQuery.data?.target?.effective_outlier === "boolean"
        ? (crossD13DiagnosticsQuery.data.target.effective_outlier as boolean)
        : false;
  const selectionSourceChart: SelectionSourceChart | null = (() => {
    if (!activeTarget) {
      return null;
    }
    const chartKey = activeTarget.chartKey;
    const figureMap: Record<string, SelectionSourceChart> = {
      "VPDB(13C)": {
        title: "d13C Calibration",
        description: "Source chart for current selection.",
        figure: displayedWorkspace.figures["VPDB(13C)"],
      },
      "VSMOW(18O)": {
        title: "d18O Calibration",
        description: "Source chart for current selection.",
        figure: displayedWorkspace.figures["VSMOW(18O)"],
      },
      calibration_3d: {
        title: "Calibration 3D Chart",
        description: "Source chart for current selection.",
        figure: displayedWorkspace.figures.calibration_3d,
      },
      crossplot: {
        title: "Calibration Crossplot",
        description: "Source chart for current selection.",
        figure: displayedWorkspace.figures.crossplot,
      },
      "linearity|d13_raw": {
        title: "Linearity d13C Raw",
        description: "Source chart for current selection.",
        figure: displayedWorkspace.linearity_figures.d13_raw,
      },
      "linearity|d13_corrected": {
        title: "Linearity d13C Corrected",
        description: "Source chart for current selection.",
        figure: displayedWorkspace.linearity_figures.d13_corrected,
      },
      "linearity|d18_raw": {
        title: "Linearity d18O Raw",
        description: "Source chart for current selection.",
        figure: displayedWorkspace.linearity_figures.d18_raw,
      },
      "linearity|d18_corrected": {
        title: "Linearity d18O Corrected",
        description: "Source chart for current selection.",
        figure: displayedWorkspace.linearity_figures.d18_corrected,
      },
    };
    if (figureMap[chartKey]) {
      const item = figureMap[chartKey];
      return {
        ...item,
        figure: highlightSelectionSourceFigure(item.figure, activeTarget),
      };
    }
    const standardSuffix = chartKey.endsWith("|d13C") ? "d13C" : chartKey.endsWith("|d18O") ? "d18O" : null;
    if (!standardSuffix) {
      return null;
    }
    const standardName = chartKey.slice(0, -`|${standardSuffix}`.length);
    const section = displayedWorkspace.standard_sections.find((item) => item.standard === standardName);
    if (!section) {
      return null;
    }
    const figure = standardSuffix === "d13C" ? section.d13_figure : section.d18_figure;
    return {
      title: `${standardName} ${standardSuffix} Outlier Trace`,
      description: "Source chart for current selection.",
      figure: highlightSelectionSourceFigure(figure, activeTarget),
    };
  })();

  return (
    <div className="space-y-6">
      <Card className="border-stone-200 bg-white/90">
        <CardHeader className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <CardTitle>Calibration Workspace</CardTitle>
            <CardDescription>
              Streamlit-parity calibration controls, standard filtering, precision summaries, linearity diagnostics, and calibration charts.
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2 text-sm text-stone-600">
            <span className="rounded-full bg-stone-50 px-3 py-1.5 ring-1 ring-stone-200">Standards: {selectedStandards.length}</span>
            <span className="rounded-full bg-stone-50 px-3 py-1.5 ring-1 ring-stone-200">Method: {activeConfig.calibration_type}</span>
            <span className="rounded-full bg-stone-50 px-3 py-1.5 ring-1 ring-stone-200">Linearity basis: {lineIntensityBasis}</span>
            <span className="rounded-full bg-stone-50 px-3 py-1.5 ring-1 ring-stone-200">
              {previewQuery.isFetching ? "Refreshing preview..." : hasUnsavedPreview ? "Preview mode" : "Saved config"}
            </span>
          </div>
        </CardHeader>
      </Card>

      {isSelectionEditorOpen ? (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-stone-950/40 p-3 pt-4 sm:p-6 sm:pt-8" onClick={() => setSelectionEditorOpen(false)}>
          <div
            className="flex max-h-[calc(100vh-2rem)] w-full max-w-7xl flex-col overflow-hidden rounded-xl border border-stone-300 bg-white shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-stone-200 px-4 py-3">
              <div>
                <div className="text-base font-semibold text-stone-900">Selection Editor</div>
                <div className="text-sm text-stone-500">Click a point for single-point editing or box-select multiple points for multi-point actions.</div>
              </div>
              <Button variant="outline" size="sm" onClick={() => setSelectionEditorOpen(false)}>
                Close
              </Button>
            </div>
            <div className="min-h-0 space-y-4 overflow-y-auto p-4">
              {selectionSourceChart?.figure ? (
                <Card className="border-stone-300">
                  <CardHeader>
                    <CardTitle className="text-base">Selection Source Chart</CardTitle>
                    <CardDescription>
                      {selectionSourceChart.title} {selectionSourceChart.description}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <PlotlyChart figure={selectionSourceChart.figure} className="h-[360px] w-full" />
                  </CardContent>
                </Card>
              ) : null}

              {selectedTargets.length ? (
                <>
                  <div className="space-y-3 rounded-xl border border-stone-200 bg-stone-50/50 p-4">
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
                        <Button variant="outline" size="sm" onClick={() => setActiveTargetIndex((index) => Math.max(0, index - 1))} disabled={activeTargetIndex === 0}>
                          Prev
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setActiveTargetIndex((index) => Math.min(selectedTargets.length - 1, index + 1))}
                          disabled={activeTargetIndex >= selectedTargets.length - 1}
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
                              if (item.canSetSingleValue && item.setValue != null) {
                                setSingleValueFromSuggestion(item.setValue);
                              }
                            }}
                            className={cn(
                              "min-h-[88px] rounded-lg border border-stone-200 bg-white px-3 py-2.5",
                              item.canSetSingleValue ? "cursor-pointer hover:border-fuchsia-300 hover:bg-fuchsia-50/50" : "",
                            )}
                          >
                            <div className="text-[11px] font-semibold uppercase tracking-wide text-stone-500">
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
                          className={`rounded-full px-3 py-1 text-xs ring-1 ring-stone-200 ${
                            label === `${activeTarget?.rowLabel}:${activeTarget?.isotopeKey}` ? "bg-stone-900 text-white" : "bg-white text-stone-700"
                          }`}
                        >
                          {label}
                        </span>
                      ))}
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2">
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

                  {activeIsotopeTarget ? (
                    <div className="space-y-4">
                      <div className="grid gap-3 sm:grid-cols-2">
                        <label className="text-sm">
                          <span className="mb-1 block text-stone-700">Set value</span>
                          <input
                            type="number"
                            step="0.001"
                            value={singleValue}
                            onChange={(event) => setSingleValue(Number(event.target.value))}
                            className={cn(
                              "w-full rounded-lg border px-3 py-2 transition-all duration-200",
                              isSetValueInputHighlighted
                                ? "border-fuchsia-500 bg-fuchsia-50 ring-2 ring-fuchsia-300"
                                : "border-stone-300",
                            )}
                          />
                        </label>
                        <label className="text-sm">
                          <span className="mb-1 block text-stone-700">Offset</span>
                          <input
                            type="number"
                            step="0.001"
                            value={singleOffset}
                            onChange={(event) => setSingleOffset(Number(event.target.value))}
                            className="w-full rounded-lg border border-stone-300 px-3 py-2"
                          />
                        </label>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Button onClick={applySingleValue} disabled={busy}>
                          Set {activeIsotopeTarget.isotopeKey}
                        </Button>
                        <Button variant="outline" onClick={applySingleOffset} disabled={busy}>
                          Offset {activeIsotopeTarget.isotopeKey}
                        </Button>
                        <Button variant="outline" onClick={applySingleInterpolate} disabled={busy}>
                          Interpolate {activeIsotopeTarget.isotopeKey}
                        </Button>
                      </div>
                      <DiagnosticsPanel
                        title="d18O cycle diagnostics"
                        diagnostics={singleDiagnosticsQuery.data}
                        loading={singleDiagnosticsQuery.isLoading}
                        showLinearityChart={linearityEnabled}
                        linearityEnabled={linearityEnabled}
                        onLinearityEnabledChange={setLinearityEnabled}
                        linearityTargetIntensity={linearityTargetIntensity}
                        onLinearityTargetIntensityChange={setLinearityTargetIntensity}
                        onPickDeltaValue={setSingleValueFromSuggestion}
                      />
                    </div>
                  ) : null}

                  {activeCrossTarget ? (
                    <div className="space-y-4">
                      <div className="grid gap-3 sm:grid-cols-2">
                        <label className="text-sm">
                          <span className="mb-1 block text-stone-700">Set d13C</span>
                          <input
                            type="number"
                            step="0.001"
                            value={crossD13Value}
                            onChange={(event) => setCrossD13Value(Number(event.target.value))}
                            className="w-full rounded-lg border border-stone-300 px-3 py-2"
                          />
                        </label>
                        <label className="text-sm">
                          <span className="mb-1 block text-stone-700">Set d18O</span>
                          <input
                            type="number"
                            step="0.001"
                            value={crossD18Value}
                            onChange={(event) => setCrossD18Value(Number(event.target.value))}
                            className="w-full rounded-lg border border-stone-300 px-3 py-2"
                          />
                        </label>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Button onClick={applyCrossValues} disabled={busy}>
                          Apply crossplot values
                        </Button>
                        <Button variant="outline" onClick={() => applyMultiInterpolate()} disabled={busy}>
                          Interpolate both isotopes
                        </Button>
                      </div>
                      <div className="grid gap-3 md:grid-cols-2">
                        <div className="rounded-lg border border-stone-200 p-3">
                          <div className="text-xs uppercase tracking-wide text-stone-500">d13C details</div>
                          <div className="mt-1 text-sm text-stone-800">
                            Current:{" "}
                            {asNumber((crossD13DiagnosticsQuery.data?.target ?? {})["current_value"]) == null
                              ? "N/A"
                              : formatDeltaValue(asNumber((crossD13DiagnosticsQuery.data?.target ?? {})["current_value"]))}
                          </div>
                          <div className="text-sm text-stone-700">
                            Cycle mean:{" "}
                            {asNumber((crossD13DiagnosticsQuery.data?.cycle_mean ?? {})["mean"]) == null
                              ? "N/A"
                              : formatDeltaValue(asNumber((crossD13DiagnosticsQuery.data?.cycle_mean ?? {})["mean"]))}
                          </div>
                          <div className="text-xs text-stone-500">
                            Method: {asString((crossD13DiagnosticsQuery.data?.cycle_mean ?? {})["method"]) || "N/A"}
                          </div>
                        </div>
                        <div className="rounded-lg border border-stone-200 p-3">
                          <div className="text-xs uppercase tracking-wide text-stone-500">d18O details</div>
                          <div className="mt-1 text-sm text-stone-800">
                            Current:{" "}
                            {asNumber((crossD18DiagnosticsQuery.data?.target ?? {})["current_value"]) == null
                              ? "N/A"
                              : formatDeltaValue(asNumber((crossD18DiagnosticsQuery.data?.target ?? {})["current_value"]))}
                          </div>
                          <div className="text-sm text-stone-700">
                            Cycle mean:{" "}
                            {asNumber((crossD18DiagnosticsQuery.data?.cycle_mean ?? {})["mean"]) == null
                              ? "N/A"
                              : formatDeltaValue(asNumber((crossD18DiagnosticsQuery.data?.cycle_mean ?? {})["mean"]))}
                          </div>
                          <div className="text-xs text-stone-500">
                            Method: {asString((crossD18DiagnosticsQuery.data?.cycle_mean ?? {})["method"]) || "N/A"}
                          </div>
                        </div>
                      </div>
                      <DiagnosticsPanel
                        title="Crossplot cycle diagnostics (shared intensity chart/table, d18O)"
                        diagnostics={crossSharedDiagnostics}
                        loading={crossSharedDiagnosticsLoading}
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
                  No active selection. Click any chart point or use a plot selection tool to populate the editor.
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <aside className="space-y-6 xl:sticky xl:top-6 xl:self-start">
          <Card>
            <CardHeader>
              <CardTitle>Calibration Controls</CardTitle>
              <CardDescription>Configure standards, outlier detection, visualization, date range, and linearity settings.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <MultiSelectDropdown
                label="Selected standards"
                options={displayedWorkspace.available_values.standards}
                selected={activeConfig.selected_standards}
                onChange={(next) => updateConfig("selected_standards", next)}
                placeholder="Select standards"
              />

              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-2">
                <label className="form-field">
                  <span className="form-label">Sigma level</span>
                  <input
                    type="number"
                    min="0.1"
                    max="5"
                    step="0.1"
                    value={activeConfig.sigma_level}
                    onChange={(event) => updateConfig("sigma_level", Number(event.target.value))}
                    className="form-control"
                  />
                </label>
                <label className="form-field">
                  <span className="form-label">IQR multiplier</span>
                  <input
                    type="number"
                    min="1"
                    max="10"
                    step="0.1"
                    value={activeConfig.iqr_multiplier}
                    onChange={(event) => updateConfig("iqr_multiplier", Number(event.target.value))}
                    className="form-control"
                  />
                </label>
              </div>

              <label className="form-field">
                <span className="form-label">Outlier method</span>
                <select
                  value={activeConfig.calibration_type}
                  onChange={(event) => updateConfig("calibration_type", event.target.value as CalibrationConfig["calibration_type"])}
                  className="form-control"
                >
                  <option value="Z-Score">Z-Score</option>
                  <option value="IQR">IQR</option>
                </select>
              </label>

              <label className="flex items-start gap-3 rounded-xl border border-stone-200 bg-white/80 p-4">
                <input
                  type="checkbox"
                  checked={activeConfig.independent_isotope_outliers}
                  onChange={(event) => updateConfig("independent_isotope_outliers", event.target.checked)}
                  className="mt-1 h-4 w-4 accent-stone-900"
                />
                <span>
                  <span className="block text-sm font-semibold tracking-[0.01em] text-stone-800">Independent isotope outliers</span>
                  <span className="mt-1 block text-xs leading-relaxed text-stone-500">
                    d13C and d18O outlier filtering stays independent per standard row.
                  </span>
                </span>
              </label>

              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
                <label className="form-field">
                  <span className="form-label">Color parameter</span>
                  <select
                    value={activeConfig.color_param}
                    onChange={(event) => updateConfig("color_param", event.target.value)}
                    className="form-control"
                  >
                    {displayedWorkspace.available_values.color_params.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="form-field">
                  <span className="form-label">3D Z axis</span>
                  <select
                    value={activeConfig.z_axis}
                    onChange={(event) => updateConfig("z_axis", event.target.value)}
                    className="form-control"
                  >
                    {displayedWorkspace.available_values.z_axis_options.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="space-y-4 rounded-xl border border-stone-200 bg-white/80 p-4">
                <div className="form-section-title">Precision date range</div>
                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
                  <label className="form-field">
                    <span className="form-label">Start date</span>
                    <input
                      type="date"
                      min={minDate}
                      max={maxDate}
                      value={activeConfig.precision_date_range?.[0] ?? ""}
                      onChange={(event) =>
                        updateConfig("precision_date_range", [event.target.value || null, activeConfig.precision_date_range?.[1] ?? null])
                      }
                      className="form-control"
                    />
                  </label>
                  <label className="form-field">
                    <span className="form-label">End date</span>
                    <input
                      type="date"
                      min={minDate}
                      max={maxDate}
                      value={activeConfig.precision_date_range?.[1] ?? ""}
                      onChange={(event) =>
                        updateConfig("precision_date_range", [activeConfig.precision_date_range?.[0] ?? null, event.target.value || null])
                      }
                      className="form-control"
                    />
                  </label>
                </div>
              </div>

              <div className="space-y-3 rounded-xl border border-stone-200 bg-white/80 p-4">
                <div className="form-section-title">Linearity</div>
                <label className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={activeConfig.linearity.apply}
                    onChange={(event) => updateLinearity("apply", event.target.checked)}
                    className="mt-1 h-4 w-4 accent-stone-900"
                  />
                  <span>
                    <span className="block text-sm font-semibold tracking-[0.01em] text-stone-800">Apply linearity correction on calibration run</span>
                    <span className="mt-1 block text-xs leading-relaxed text-stone-500">Uses the currently selected standards and intensity basis.</span>
                  </span>
                </label>
                <label className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={activeConfig.linearity.use_diff_intensity}
                    onChange={(event) => updateLinearity("use_diff_intensity", event.target.checked)}
                    className="mt-1 h-4 w-4 accent-stone-900"
                  />
                  <span>
                    <span className="block text-sm font-semibold tracking-[0.01em] text-stone-800">Use Samp-Ref intensity difference</span>
                    <span className="mt-1 block text-xs leading-relaxed text-stone-500">
                      Switches the linearity basis from sample intensity to cycle-1 sample-reference difference.
                    </span>
                  </span>
                </label>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button
                  onClick={() => runMutation.mutate(activeConfig)}
                  disabled={runMutation.isPending || selectedStandards.length < 1 || selectedStandards.length > 2}
                >
                  {runMutation.isPending ? "Running..." : "Calibrate results"}
                </Button>
                <Button variant="outline" onClick={() => setConfig(workspace.config)} disabled={runMutation.isPending}>
                  Restore saved
                </Button>
              </div>
              <div className="text-xs text-stone-500">Select exactly one or two standards to run calibration.</div>
              {previewError ? <div className="text-xs text-red-600">Preview error: {previewError}</div> : null}
              {runError ? <div className="text-xs text-red-600">Calibration error: {runError}</div> : null}
            </CardContent>
          </Card>
        </aside>

        <div className="space-y-6">
          {precisionSummaries.length ? (
            <div className="space-y-4">
              <Card className="border-stone-200 bg-gradient-to-r from-stone-50 via-white to-stone-50">
                <CardHeader className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                  <div>
                    <CardTitle>Calibration Summary</CardTitle>
                    <CardDescription>Included standards, isotope precision, and per-line precision for the current preview configuration.</CardDescription>
                  </div>
                  <div className="flex flex-wrap gap-2 text-sm text-stone-600">
                    <span className="rounded-full bg-white px-3 py-1 ring-1 ring-stone-200">Standards summarized: {precisionSummaries.length}</span>
                    <span className="rounded-full bg-white px-3 py-1 ring-1 ring-stone-200">Line entries: {linePrecisionCount}</span>
                  </div>
                </CardHeader>
              </Card>
              <div className="grid gap-4">
                {precisionSummaries.map((summary) => (
                  <PrecisionCard key={summary.standard} summary={summary} />
                ))}
              </div>
            </div>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>No Standards Selected</CardTitle>
                <CardDescription>Select one or two standards to populate calibration summaries and charts.</CardDescription>
              </CardHeader>
            </Card>
          )}

          {selectedStandards.length ? (
            <>
              <div className="grid gap-6 2xl:grid-cols-2">
                <Card className="min-w-0 overflow-hidden">
                  <CardHeader>
                    <CardTitle>d13C Calibration</CardTitle>
                  </CardHeader>
                  <CardContent className="min-w-0 overflow-hidden">
                    <PlotlyChart
                      figure={displayedWorkspace.figures["VPDB(13C)"]}
                      className="aspect-square w-full"
                      onPointClick={(points) => openProcessingSelectionEditor("VPDB(13C)", points, false)}
                      onSelection={(points) => openProcessingSelectionEditor("VPDB(13C)", points, true)}
                    />
                  </CardContent>
                </Card>
                <Card className="min-w-0 overflow-hidden">
                  <CardHeader>
                    <CardTitle>d18O Calibration</CardTitle>
                  </CardHeader>
                  <CardContent className="min-w-0 overflow-hidden">
                    <PlotlyChart
                      figure={displayedWorkspace.figures["VSMOW(18O)"]}
                      className="aspect-square w-full"
                      onPointClick={(points) => openProcessingSelectionEditor("VSMOW(18O)", points, false)}
                      onSelection={(points) => openProcessingSelectionEditor("VSMOW(18O)", points, true)}
                    />
                  </CardContent>
                </Card>
              </div>

              <div className="grid gap-6 xl:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle>Calibration 3D Chart</CardTitle>
                    <CardDescription>Filtered standards in calibration space using the active color and Z-axis parameters.</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <PlotlyChart
                      figure={displayedWorkspace.figures.calibration_3d}
                      className="min-h-[520px] xl:aspect-square"
                      onPointClick={(points) => openProcessingSelectionEditor("calibration_3d", points, false)}
                      onSelection={(points) => openProcessingSelectionEditor("calibration_3d", points, true)}
                    />
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader>
                    <CardTitle>Calibration Crossplot</CardTitle>
                    <CardDescription>d13C vs d18O crossplot for the filtered standards set.</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <PlotlyChart
                      figure={displayedWorkspace.figures.crossplot}
                      className="min-h-[520px] xl:aspect-square"
                      onPointClick={(points) => openProcessingSelectionEditor("crossplot", points, false)}
                      onSelection={(points) => openProcessingSelectionEditor("crossplot", points, true)}
                    />
                  </CardContent>
                </Card>
              </div>

              <Card>
                <CardHeader>
                  <CardTitle>Linearity Correction</CardTitle>
                  <CardDescription>Standards-only linearity fits built from the active precision date window and intensity basis.</CardDescription>
                </CardHeader>
                <CardContent className="grid gap-6 2xl:grid-cols-2">
                  <PlotlyChart
                    figure={displayedWorkspace.linearity_figures.d13_raw}
                    className="min-h-[440px]"
                    onPointClick={(points) => openProcessingSelectionEditor("linearity|d13_raw", points, false)}
                    onSelection={(points) => openProcessingSelectionEditor("linearity|d13_raw", points, true)}
                  />
                  <PlotlyChart
                    figure={displayedWorkspace.linearity_figures.d13_corrected}
                    className="min-h-[440px]"
                    onPointClick={(points) => openProcessingSelectionEditor("linearity|d13_corrected", points, false)}
                    onSelection={(points) => openProcessingSelectionEditor("linearity|d13_corrected", points, true)}
                  />
                  <PlotlyChart
                    figure={displayedWorkspace.linearity_figures.d18_raw}
                    className="min-h-[440px]"
                    onPointClick={(points) => openProcessingSelectionEditor("linearity|d18_raw", points, false)}
                    onSelection={(points) => openProcessingSelectionEditor("linearity|d18_raw", points, true)}
                  />
                  <PlotlyChart
                    figure={displayedWorkspace.linearity_figures.d18_corrected}
                    className="min-h-[440px]"
                    onPointClick={(points) => openProcessingSelectionEditor("linearity|d18_corrected", points, false)}
                    onSelection={(points) => openProcessingSelectionEditor("linearity|d18_corrected", points, true)}
                  />
                </CardContent>
              </Card>

              <div className="space-y-6">
                {displayedWorkspace.standard_sections.map((section) => (
                  <details key={section.standard} className="rounded-xl border border-stone-200 bg-white shadow-sm" open>
                    <summary className="cursor-pointer px-6 py-4 text-lg font-semibold text-stone-900">{section.standard}</summary>
                    <div className="space-y-6 p-6 pt-0">
                      <div className="grid gap-6">
                        <Card>
                          <CardHeader>
                            <CardTitle className="text-base">d13C Outlier Trace</CardTitle>
                          </CardHeader>
                          <CardContent>
                            <PlotlyChart
                              figure={section.d13_figure}
                              className="min-h-[340px]"
                              onPointClick={(points) => openProcessingSelectionEditor(`${section.standard}|d13C`, points, false)}
                              onSelection={(points) => openProcessingSelectionEditor(`${section.standard}|d13C`, points, true)}
                            />
                          </CardContent>
                        </Card>
                        <Card>
                          <CardHeader>
                            <CardTitle className="text-base">d18O Outlier Trace</CardTitle>
                          </CardHeader>
                          <CardContent>
                            <PlotlyChart
                              figure={section.d18_figure}
                              className="min-h-[340px]"
                              onPointClick={(points) => openProcessingSelectionEditor(`${section.standard}|d18O`, points, false)}
                              onSelection={(points) => openProcessingSelectionEditor(`${section.standard}|d18O`, points, true)}
                            />
                          </CardContent>
                        </Card>
                      </div>
                      <div className="grid gap-6 2xl:grid-cols-2">
                        <details className="rounded-xl border border-stone-200 bg-white shadow-sm">
                          <summary className="cursor-pointer px-6 py-4 text-base font-semibold text-stone-900">
                            d13C Outliers ({section.d13_outliers.length})
                          </summary>
                          <div className="px-6 pb-6">
                            <DataTable rows={section.d13_outliers} emptyLabel="No d13C outliers for this standard." />
                          </div>
                        </details>
                        <details className="rounded-xl border border-stone-200 bg-white shadow-sm">
                          <summary className="cursor-pointer px-6 py-4 text-base font-semibold text-stone-900">
                            d18O Outliers ({section.d18_outliers.length})
                          </summary>
                          <div className="px-6 pb-6">
                            <DataTable rows={section.d18_outliers} emptyLabel="No d18O outliers for this standard." />
                          </div>
                        </details>
                      </div>
                    </div>
                  </details>
                ))}
              </div>
            </>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>Calibration Preview</CardTitle>
                <CardDescription>Select one or more standards to build the calibration figures, precision summaries, and linearity diagnostics.</CardDescription>
              </CardHeader>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
