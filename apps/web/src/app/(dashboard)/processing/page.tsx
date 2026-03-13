"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { PlotlyChart, type PlotlyPoint } from "@/components/charts/plotly-chart";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type {
  CycleDiagnosticsPayload,
  EditAction,
  OutlierTable,
  ProcessingConfig,
  ProcessingWorkspace,
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

type DisplayStateMap = Record<string, { rawOnly: boolean; hideCalibrated: boolean }>;
type FigureShape = Record<string, unknown> & {
  data: Array<Record<string, unknown>>;
  layout: Record<string, unknown>;
};
type SelectionSourceChart = {
  title: string;
  description: string;
  figure?: Record<string, unknown>;
  stackedFigures?: Array<{
    key: string;
    title: string;
    figure?: Record<string, unknown>;
  }>;
};

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

function applyDisplayState(
  figure: Record<string, unknown> | undefined,
  rawOnly: boolean,
  hideCalibrated: boolean,
) {
  const cloned = cloneFigure(figure);
  if (!Array.isArray(cloned.data)) {
    return cloned;
  }
  let traces = cloned.data as Array<Record<string, unknown>>;
  if (rawOnly) {
    traces = traces.filter((trace) => String(trace.name ?? "").startsWith("Raw "));
  } else if (hideCalibrated) {
    traces = traces.filter((trace) => !String(trace.name ?? "").startsWith("Calibrated "));
  }
  return { ...cloned, data: traces };
}

function pointMatchesSelectedTarget(pointCustomData: unknown, target: SelectedTarget): boolean {
  if (!Array.isArray(pointCustomData) || pointCustomData.length < 2) {
    return false;
  }
  const rowLabel = String(pointCustomData[0] ?? "");
  const isotopeKey = String(pointCustomData[1] ?? "");
  if (target.isotopeKey === "cross") {
    return rowLabel === target.rowLabel && (isotopeKey === "d13C" || isotopeKey === "d18O" || isotopeKey === "cross");
  }
  return rowLabel === target.rowLabel && isotopeKey === target.isotopeKey;
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
  for (const trace of traces) {
    const customdata = Array.isArray(trace.customdata) ? trace.customdata : null;
    const x = Array.isArray(trace.x) ? trace.x : null;
    const y = Array.isArray(trace.y) ? trace.y : null;
    const z = Array.isArray(trace.z) ? trace.z : null;
    if (!customdata || !x || !y) {
      continue;
    }
    const indexes: number[] = [];
    for (let index = 0; index < customdata.length; index += 1) {
      if (pointMatchesSelectedTarget(customdata[index], target)) {
        indexes.push(index);
      }
    }
    if (!indexes.length) {
      continue;
    }
    const traceType = String(trace.type ?? "scatter");
    const is3dTrace = traceType.includes("3d");
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
        color: is3dTrace ? "#FF00FF" : "rgba(0,0,0,0)",
        size: is3dTrace ? 14 : 22,
        symbol: is3dTrace ? "circle" : "circle-open",
        line: {
          color: "#FF00FF",
          width: is3dTrace ? 2.5 : 4,
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

function parseSelectedTargets(points: PlotlyPoint[], chartKey: string): SelectedTarget[] {
  const seen = new Set<string>();
  const targets: SelectedTarget[] = [];
  for (const point of points) {
    const customdata = Array.isArray(point.customdata) ? point.customdata : null;
    if (!customdata || customdata.length < 4) {
      continue;
    }
    const rowLabel = String(customdata[0] ?? "");
    const isotopeKey = String(customdata[1] ?? "") as "d13C" | "d18O" | "cross";
    const identifier1 = String(customdata[2] ?? "");
    const identifier2 = String(customdata[3] ?? "");
    if (!rowLabel || !["d13C", "d18O", "cross"].includes(isotopeKey)) {
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

function DataTable({ rows, emptyLabel }: { rows: Array<Record<string, unknown>>; emptyLabel: string }) {
  if (!rows.length) {
    return <div className="rounded-lg border border-dashed border-stone-300 p-4 text-sm text-stone-500">{emptyLabel}</div>;
  }
  const columns = Object.keys(rows[0] ?? {});

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
            {columns.map((column) => (
              <th key={column} className="px-3 py-2 font-medium text-stone-700">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-stone-100 bg-white">
          {rows.slice(0, 25).map((row, rowIndex) => (
            <tr key={rowIndex}>
              {columns.map((column) => (
                <td key={column} className="px-3 py-2 text-stone-600">
                  {formatValue(row[column], column)}
                </td>
              ))}
            </tr>
          ))}
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

function RangeSliderField({
  label,
  value,
  min,
  max,
  step = 0.1,
  precision = 2,
  onChange,
}: {
  label: string;
  value: [number, number];
  min: number;
  max: number;
  step?: number;
  precision?: number;
  onChange: (next: [number, number]) => void;
}) {
  const resolvedMin = Math.min(min, max);
  const resolvedMax = Math.max(min, max);
  const low = clampNumber(Math.min(value[0], value[1]), resolvedMin, resolvedMax);
  const high = clampNumber(Math.max(value[0], value[1]), resolvedMin, resolvedMax);

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
            className="mt-1 w-full accent-stone-700"
          />
        </label>
        <label className="block text-xs text-stone-600">
          Max
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
            className="mt-1 w-full accent-stone-700"
          />
        </label>
      </div>
      <div className="mt-2 flex items-center justify-between text-[11px] text-stone-400">
        <span>{resolvedMin.toFixed(precision)}</span>
        <span>{resolvedMax.toFixed(precision)}</span>
      </div>
    </div>
  );
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

function OutlierTablesPanel({ title, tables }: { title: string; tables: OutlierTable[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>Backend-generated outlier tables for the current processing workspace.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {tables.length ? (
          tables.map((table) => (
            <details key={table.title ?? table.name} className="rounded-lg border border-stone-200 bg-white p-3">
              <summary className="cursor-pointer text-sm font-medium text-stone-800">
                {table.title ?? table.name} ({table.rows.length})
              </summary>
              <div className="mt-3">
                <DataTable rows={table.rows} emptyLabel="No rows in this outlier category." />
              </div>
            </details>
          ))
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

function MetricTable({ workspace }: { workspace: ProcessingWorkspace }) {
  if (!workspace.summary.metrics.length) {
    return null;
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Processing Summary</CardTitle>
        <CardDescription>Detailed backend metrics used to build the current workspace state.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-lg border border-stone-200">
          <table className="min-w-full divide-y divide-stone-200 text-left text-sm">
            <thead className="bg-stone-50">
              <tr>
                <th className="px-3 py-2 font-medium text-stone-700">Metric</th>
                <th className="px-3 py-2 font-medium text-stone-700">Value</th>
                <th className="px-3 py-2 font-medium text-stone-700">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-stone-100 bg-white">
              {workspace.summary.metrics.map((metric) => (
                <tr key={metric.metric}>
                  <td className="px-3 py-2 font-medium text-stone-800">{metric.metric}</td>
                  <td className="px-3 py-2 text-stone-700">{String(metric.value)}</td>
                  <td className="px-3 py-2 text-stone-600">{metric.details}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
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
                  <CheckboxField
                    checked={Boolean(linearityEnabled)}
                    label="Preview linearity-corrected cycle mean"
                    onChange={(checked) => onLinearityEnabledChange(checked)}
                  />
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

function FigureCard({
  title,
  description,
  figure,
  cardClassName,
  chartClassName,
  onPointClick,
  onSelection,
}: {
  title: string;
  description: string;
  figure?: Record<string, unknown>;
  cardClassName?: string;
  chartClassName?: string;
  onPointClick?: (points: PlotlyPoint[]) => void;
  onSelection?: (points: PlotlyPoint[]) => void;
}) {
  return (
    <Card className={cardClassName}>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <PlotlyChart figure={figure} className={chartClassName ?? "min-h-[340px]"} onPointClick={onPointClick} onSelection={onSelection} />
      </CardContent>
    </Card>
  );
}

export default function ProcessingPage() {
  const sessionId = useSessionStore((state) => state.sessionId);
  const queryClient = useQueryClient();
  const [config, setConfig] = useState<ProcessingConfig | null>(null);
  const [commentMapText, setCommentMapText] = useState("");
  const [selectedTargets, setSelectedTargets] = useState<SelectedTarget[]>([]);
  const [activeTargetIndex, setActiveTargetIndex] = useState(0);
  const [displayState, setDisplayState] = useState<DisplayStateMap>({});
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
  const [isSelectionEditorOpen, setSelectionEditorOpen] = useState(false);
  const [isExportModalOpen, setExportModalOpen] = useState(false);
  const [exportOutputType, setExportOutputType] = useState<"dataset" | "client_output">("dataset");

  const workspaceQuery = useQuery({
    queryKey: ["processing-workspace", sessionId],
    queryFn: () => api.getProcessingWorkspace(sessionId!),
    enabled: Boolean(sessionId),
  });

  useEffect(() => {
    if (workspaceQuery.data) {
      setConfig(workspaceQuery.data.config);
    }
  }, [workspaceQuery.data]);

  useEffect(() => {
    const nextText = serializeCommentMap(config?.export.comment_map ?? {});
    setCommentMapText(nextText);
  }, [config?.export.comment_map]);

  useEffect(() => {
    if (!sessionId || typeof window === "undefined") {
      return;
    }
    const raw = window.sessionStorage.getItem(`processing-display-state:${sessionId}`);
    if (raw) {
      try {
        setDisplayState(JSON.parse(raw) as DisplayStateMap);
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
    mutationFn: (nextConfig: ProcessingConfig) => api.setProcessingConfig(sessionId!, nextConfig),
    onSuccess: (workspace) => {
      queryClient.setQueryData(["processing-workspace", sessionId], workspace);
      setConfig(workspace.config);
    },
  });

  const editMutation = useMutation({
    mutationFn: (payload: EditAction) => api.editProcessing(sessionId!, payload),
    onSuccess: (workspace) => {
      queryClient.setQueryData(["processing-workspace", sessionId], workspace);
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
      }),
    onSuccess: (workspace) => {
      queryClient.setQueryData(["processing-workspace", sessionId], workspace);
      setSelectedTargets([]);
      setActiveTargetIndex(0);
      setSelectionEditorOpen(false);
    },
  });

  useEffect(() => {
    if ((!isSelectionEditorOpen && !isExportModalOpen) || typeof window === "undefined") {
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setSelectionEditorOpen(false);
        setExportModalOpen(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isSelectionEditorOpen, isExportModalOpen]);

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

  const activeTarget = selectedTargets.length ? selectedTargets[Math.min(activeTargetIndex, selectedTargets.length - 1)] : null;
  const activeIsotopeTarget = activeTarget && activeTarget.isotopeKey !== "cross" ? activeTarget : null;
  const activeCrossTarget = activeTarget && activeTarget.isotopeKey === "cross" ? activeTarget : null;

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
    setSingleOffset(0);
    setLinearityEnabled(false);
  }, [activeIsotopeTarget?.rowLabel, activeIsotopeTarget?.isotopeKey]);

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

  const workspace = workspaceQuery.data;
  const activeConfig = config ?? workspace?.config ?? null;

  function setTargets(nextTargets: SelectedTarget[]) {
    setSelectedTargets(nextTargets);
    setActiveTargetIndex(0);
    setSelectionEditorOpen(nextTargets.length > 0);
  }

  function updateConfig<T extends keyof ProcessingConfig>(key: T, value: ProcessingConfig[T]) {
    setConfig((current) => (current ? { ...current, [key]: value } : current));
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

  function updateManualLinearity(key: keyof ProcessingConfig["manual_linearity_override"], value: boolean | number) {
    setConfig((current) =>
      current
        ? {
            ...current,
            manual_linearity_override: {
              ...current.manual_linearity_override,
              [key]: value,
            },
          }
        : current,
    );
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

  function toggleDisplayState(key: string, field: "rawOnly" | "hideCalibrated") {
    setDisplayState((current) => ({
      ...current,
      [key]: {
        rawOnly: current[key]?.rawOnly ?? false,
        hideCalibrated: current[key]?.hideCalibrated ?? false,
        [field]: !(current[key]?.[field] ?? false),
      },
    }));
  }

  function setSingleValueFromSuggestion(value: number) {
    setSingleValue(roundDeltaValue(value));
    setSetValueHighlightNonce((current) => current + 1);
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

  async function handleExport(outputType: "dataset" | "client_output") {
    if (!sessionId || !activeConfig) {
      return;
    }
    await saveConfigMutation.mutateAsync(activeConfig);
    const { blob, filename } = await api.exportDataset(sessionId, { ...activeConfig.export, output_type: outputType });
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

  async function applySingleValue() {
    if (!sessionId || !activeIsotopeTarget) {
      return;
    }
    const isotopeKey = activeIsotopeTarget.isotopeKey as "d13C" | "d18O";
    await editMutation.mutateAsync({
      action: "set_value",
      targets: [{ row_label: activeIsotopeTarget.rowLabel, isotope_key: isotopeKey }],
      value: singleValue,
    });
  }

  async function applySingleOffset() {
    if (!sessionId || !activeIsotopeTarget) {
      return;
    }
    const isotopeKey = activeIsotopeTarget.isotopeKey as "d13C" | "d18O";
    await editMutation.mutateAsync({
      action: "offset",
      targets: [{ row_label: activeIsotopeTarget.rowLabel, isotope_key: isotopeKey }],
      offset: singleOffset,
    });
  }

  async function applySingleInterpolate() {
    if (!sessionId || !activeIsotopeTarget) {
      return;
    }
    const isotopeKey = activeIsotopeTarget.isotopeKey as "d13C" | "d18O";
    await editMutation.mutateAsync({
      action: "interpolate",
      targets: [{ row_label: activeIsotopeTarget.rowLabel, isotope_key: isotopeKey }],
    });
  }

  async function applyCrossValues() {
    if (!sessionId || !activeCrossTarget) {
      return;
    }
    const rowLabel = activeCrossTarget.rowLabel;
    await editMutation.mutateAsync({
      action: "set_value",
      targets: [{ row_label: rowLabel, isotope_key: "d13C" }],
      value: crossD13Value,
    });
    await editMutation.mutateAsync({
      action: "set_value",
      targets: [{ row_label: rowLabel, isotope_key: "d18O" }],
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
    if (!sessionId || !selectedTargets.length) {
      return;
    }
    await editMutation.mutateAsync({
      action: "interpolate",
      targets: buildTargetsForAction(selectedTargets),
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

  const busy = saveConfigMutation.isPending || editMutation.isPending || resetAllMutation.isPending;
  const manualOverrideCount = Object.keys(workspace.edit_state.manual_outlier_overrides ?? {}).length;
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
  const overviewCards = {
    processing3d: {
      key: "processing_3d",
      title: "3D Processing Overview",
      description: "Global 3D view for the filtered processing scope.",
      figure: workspace.overview_figures.processing_3d,
    },
    d13Summary: {
      key: "d13_summary",
      title: "d13C Summary",
      description: "Summary curve for d13C across the active scope.",
      figure: workspace.overview_figures.d13_summary,
    },
    d18Summary: {
      key: "d18_summary",
      title: "d18O Summary",
      description: "Summary curve for d18O across the active scope.",
      figure: workspace.overview_figures.d18_summary,
    },
    crossplot: {
      key: "crossplot",
      title: "Crossplot",
      description: "d13C vs d18O selection surface for dual-isotope edits.",
      figure: workspace.overview_figures.crossplot,
    },
  };
  const activeSelectionChartKey = activeTarget?.chartKey ?? selectedTargets[0]?.chartKey ?? null;
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
          const d13State = displayState[d13Key] ?? { rawOnly: false, hideCalibrated: false };
          const d18State = displayState[d18Key] ?? { rawOnly: false, hideCalibrated: false };
          const d13FigureBase = applyDisplayState(figureSet.d13c, d13State.rawOnly, d13State.hideCalibrated);
          const d18FigureBase = applyDisplayState(figureSet.d18o, d18State.rawOnly, d18State.hideCalibrated);
          const containsSelectedRow =
            figureContainsRowLabel(d13FigureBase, activeTarget.rowLabel) || figureContainsRowLabel(d18FigureBase, activeTarget.rowLabel);
          if (!containsSelectedRow) {
            continue;
          }
          return {
            title: "Crossplot selection source",
            description: `${section.species} | ${figureSet.identifier} isotope series for the selected sample.`,
            figure: undefined,
            stackedFigures: [
              {
                key: `${d13Key}:selection-source`,
                title: "d13C series",
                figure: highlightSelectionSourceFigure(d13FigureBase, activeTarget),
              },
              {
                key: `${d18Key}:selection-source`,
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
        figure: highlightSelectionSourceFigure(overviewCards.processing3d.figure, activeTarget),
      },
      [overviewCards.crossplot.key]: {
        title: crossplotStackedSource?.title ?? overviewCards.crossplot.title,
        description: crossplotStackedSource?.description ?? overviewCards.crossplot.description,
        figure: crossplotStackedSource?.figure ?? highlightSelectionSourceFigure(overviewCards.crossplot.figure, activeTarget),
        stackedFigures: crossplotStackedSource?.stackedFigures,
      },
      [overviewCards.d13Summary.key]: {
        title: overviewCards.d13Summary.title,
        description: overviewCards.d13Summary.description,
        figure: highlightSelectionSourceFigure(overviewCards.d13Summary.figure, activeTarget),
      },
      [overviewCards.d18Summary.key]: {
        title: overviewCards.d18Summary.title,
        description: overviewCards.d18Summary.description,
        figure: highlightSelectionSourceFigure(overviewCards.d18Summary.figure, activeTarget),
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
    const state = displayState[activeSelectionChartKey] ?? { rawOnly: false, hideCalibrated: false };
    return {
      title: `${species} | ${identifier} | ${isotopeKey}`,
      description: "Source chart used for the current selection.",
      figure: highlightSelectionSourceFigure(
        isotopeKey === "d13C"
          ? applyDisplayState(figureSet.d13c, state.rawOnly, state.hideCalibrated)
          : applyDisplayState(figureSet.d18o, state.rawOnly, state.hideCalibrated),
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
            <span className="rounded-full bg-white px-3 py-1 ring-1 ring-stone-200">Edited rows: {workspace.edit_state.edited_rows.length}</span>
            <span className="rounded-full bg-white px-3 py-1 ring-1 ring-stone-200">
              Manual overrides: {manualOverrideCount}
            </span>
            <span className="rounded-full bg-white px-3 py-1 ring-1 ring-stone-200">Selection: {selectedTargets.length}</span>
            <Button variant="secondary" size="sm" onClick={() => setExportModalOpen(true)} disabled={busy}>
              Data Export
            </Button>
          </div>
        </CardHeader>
      </Card>

      <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <aside className="space-y-6 xl:sticky xl:top-6 xl:self-start">
          <Card>
            <CardHeader>
              <CardTitle>Processing Controls</CardTitle>
              <CardDescription>Filters, outliers, and manual linearity override.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6 xl:max-h-[calc(100vh-12rem)] xl:overflow-y-auto xl:pr-2">
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
                <label className="text-sm">
                  <span className="mb-1 block font-medium text-stone-700">Color parameter</span>
                  <select
                    value={activeConfig.color_param}
                    onChange={(event) => updateConfig("color_param", event.target.value)}
                    className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                  >
                    {workspace.available_values.color_params.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
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
                    onChange={(nextRange) => updateConfig("signal_range", nextRange)}
                  />
                  <RangeSliderField
                    label="Leak range"
                    value={activeConfig.leak_range}
                    min={Math.min(0, activeConfig.leak_range[0], activeConfig.leak_range[1])}
                    max={Math.max(2000, activeConfig.leak_range[0], activeConfig.leak_range[1])}
                    step={1}
                    precision={1}
                    onChange={(nextRange) => updateConfig("leak_range", nextRange)}
                  />
                  <RangeSliderField
                    label="d13C range"
                    value={activeConfig.d13c_range}
                    min={Math.min(-50, activeConfig.d13c_range[0], activeConfig.d13c_range[1])}
                    max={Math.max(50, activeConfig.d13c_range[0], activeConfig.d13c_range[1])}
                    step={0.001}
                    precision={3}
                    onChange={(nextRange) => updateConfig("d13c_range", nextRange)}
                  />
                  <RangeSliderField
                    label="d18O range"
                    value={activeConfig.d18o_range}
                    min={Math.min(-50, activeConfig.d18o_range[0], activeConfig.d18o_range[1])}
                    max={Math.max(50, activeConfig.d18o_range[0], activeConfig.d18o_range[1])}
                    step={0.001}
                    precision={3}
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
                <div className="text-sm font-medium text-stone-800">Outliers</div>
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
                <div className="text-sm font-medium text-stone-800">Manual linearity override</div>
                <CheckboxField
                  checked={activeConfig.manual_linearity_override.enabled}
                  label="Enable manual linearity transform"
                  description="This is a derived workspace/export transform only. Raw stored values are not overwritten."
                  onChange={(checked) => updateManualLinearity("enabled", checked)}
                />
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-2">
                  <label className="text-sm">
                    <span className="mb-1 block text-stone-700">d13 per 10V</span>
                    <input
                      type="number"
                      step="0.01"
                      value={activeConfig.manual_linearity_override.d13_per_10v}
                      onChange={(event) => updateManualLinearity("d13_per_10v", Number(event.target.value))}
                      className="w-full rounded-lg border border-stone-300 px-3 py-2"
                    />
                  </label>
                  <label className="text-sm">
                    <span className="mb-1 block text-stone-700">d18 per 10V</span>
                    <input
                      type="number"
                      step="0.01"
                      value={activeConfig.manual_linearity_override.d18_per_10v}
                      onChange={(event) => updateManualLinearity("d18_per_10v", Number(event.target.value))}
                      className="w-full rounded-lg border border-stone-300 px-3 py-2"
                    />
                  </label>
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button onClick={applyConfig} disabled={busy}>
                  Apply config
                </Button>
                <Button variant="outline" onClick={() => setConfig(workspace.config)} disabled={busy}>
                  Restore saved
                </Button>
                <Button variant="outline" onClick={() => resetAllMutation.mutate()} disabled={busy}>
                  Reset all edits
                </Button>
              </div>
            </CardContent>
          </Card>

        </aside>

        <div className="space-y-6">
          <MetricTable workspace={workspace} />

          <div className="space-y-6">
            <div className="grid gap-6 xl:grid-cols-2">
              <FigureCard
                key={overviewCards.processing3d.key}
                title={overviewCards.processing3d.title}
                description={overviewCards.processing3d.description}
                figure={overviewCards.processing3d.figure}
                chartClassName="min-h-[520px] xl:aspect-square"
                onPointClick={(points) => handleChartClick(overviewCards.processing3d.key, points)}
                onSelection={(points) => handleChartSelection(overviewCards.processing3d.key, points)}
              />
              <FigureCard
                key={overviewCards.crossplot.key}
                title={overviewCards.crossplot.title}
                description={overviewCards.crossplot.description}
                figure={overviewCards.crossplot.figure}
                chartClassName="min-h-[520px] xl:aspect-square"
                onPointClick={(points) => handleChartClick(overviewCards.crossplot.key, points)}
                onSelection={(points) => handleChartSelection(overviewCards.crossplot.key, points)}
              />
            </div>
          </div>

          {isExportModalOpen ? (
            <div className="fixed inset-0 z-40 flex items-start justify-center bg-stone-950/40 p-3 pt-4 sm:p-6 sm:pt-8" onClick={() => setExportModalOpen(false)}>
              <div
                className="flex max-h-[calc(100vh-2rem)] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-stone-300 bg-white shadow-2xl"
                onClick={(event) => event.stopPropagation()}
              >
                <div className="flex items-center justify-between border-b border-stone-200 px-4 py-3">
                  <div>
                    <div className="text-base font-semibold text-stone-900">Export</div>
                    <div className="text-sm text-stone-500">Configure export options, then download either the entire dataset or client output.</div>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => setExportModalOpen(false)}>
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
                        Filename (client output): generated from client name and current date.
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
                    {exportOutputType === "client_output" ? "Download client output" : "Download dataset"}
                  </Button>
                </div>
              </div>
            </div>
          ) : null}

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
                                <PlotlyChart figure={item.figure} className="h-[280px] w-full" />
                              </div>
                            ))}
                          </div>
                        ) : selectionSourceChart.figure ? (
                          <PlotlyChart figure={selectionSourceChart.figure} className="h-[360px] w-full" />
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
                              className={cn(
                                "rounded-full px-3 py-1 text-xs ring-1 ring-stone-200",
                                label === `${activeTarget?.rowLabel}:${activeTarget?.isotopeKey}` ? "bg-stone-900 text-white" : "bg-white text-stone-700",
                              )}
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

          <div className="grid gap-6 xl:grid-cols-2">
            <FigureCard
              key={overviewCards.d13Summary.key}
              title={overviewCards.d13Summary.title}
              description={overviewCards.d13Summary.description}
              figure={overviewCards.d13Summary.figure}
              onPointClick={(points) => handleChartClick(overviewCards.d13Summary.key, points)}
              onSelection={(points) => handleChartSelection(overviewCards.d13Summary.key, points)}
            />
            <FigureCard
              key={overviewCards.d18Summary.key}
              title={overviewCards.d18Summary.title}
              description={overviewCards.d18Summary.description}
              figure={overviewCards.d18Summary.figure}
              onPointClick={(points) => handleChartClick(overviewCards.d18Summary.key, points)}
              onSelection={(points) => handleChartSelection(overviewCards.d18Summary.key, points)}
            />
          </div>

          <OutlierTablesPanel title="Selected data outlier tables" tables={workspace.outlier_tables} />

          <div className="space-y-6">
            {workspace.species_sections.map((section) => (
              <details key={section.species} className="rounded-xl border border-stone-200 bg-white shadow-sm" open>
                <summary className="cursor-pointer px-6 py-4 text-lg font-semibold text-stone-900">
                  {section.species} ({section.identifier_figures.length} identifiers)
                </summary>
                <div className="space-y-6 p-6 pt-0">
                  {section.identifier_figures.map((figureSet) => {
                    const d13Key = `${section.species}|${figureSet.identifier}|d13C`;
                    const d18Key = `${section.species}|${figureSet.identifier}|d18O`;
                    const d13State = displayState[d13Key] ?? { rawOnly: false, hideCalibrated: false };
                    const d18State = displayState[d18Key] ?? { rawOnly: false, hideCalibrated: false };
                    return (
                      <Card key={`${section.species}-${figureSet.identifier}`} className="border-stone-300">
                        <CardHeader>
                          <CardTitle>{figureSet.identifier}</CardTitle>
                          <CardDescription>
                            Species section with per-isotope charts, calibrated/raw display toggles, and chart-driven editing.
                          </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                          <div className="space-y-6">
                            <div className="space-y-3">
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <div>
                                  <div className="text-sm font-medium text-stone-800">d13C chart</div>
                                  <div className="text-xs text-stone-500">Click for single edit. Box-select for multi-point edits.</div>
                                </div>
                                <div className="flex flex-wrap gap-2">
                                  <Button variant={d13State.rawOnly ? "secondary" : "outline"} size="sm" onClick={() => toggleDisplayState(d13Key, "rawOnly")}>
                                    Raw only
                                  </Button>
                                  <Button
                                    variant={d13State.hideCalibrated ? "secondary" : "outline"}
                                    size="sm"
                                    onClick={() => toggleDisplayState(d13Key, "hideCalibrated")}
                                    disabled={!figureSet.has_calibrated_d13c}
                                  >
                                    Hide calibrated
                                  </Button>
                                </div>
                              </div>
                              <div className="h-[380px] w-full overflow-hidden rounded-lg border border-stone-200/80">
                                <PlotlyChart
                                  figure={applyDisplayState(figureSet.d13c, d13State.rawOnly, d13State.hideCalibrated)}
                                  className="h-full w-full"
                                  onPointClick={(points) => handleChartClick(d13Key, points)}
                                  onSelection={(points) => handleChartSelection(d13Key, points)}
                                />
                              </div>
                            </div>

                            <div className="space-y-3">
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <div>
                                  <div className="text-sm font-medium text-stone-800">d18O chart</div>
                                  <div className="text-xs text-stone-500">Same selection semantics, backed by the processing workspace.</div>
                                </div>
                                <div className="flex flex-wrap gap-2">
                                  <Button variant={d18State.rawOnly ? "secondary" : "outline"} size="sm" onClick={() => toggleDisplayState(d18Key, "rawOnly")}>
                                    Raw only
                                  </Button>
                                  <Button
                                    variant={d18State.hideCalibrated ? "secondary" : "outline"}
                                    size="sm"
                                    onClick={() => toggleDisplayState(d18Key, "hideCalibrated")}
                                    disabled={!figureSet.has_calibrated_d18o}
                                  >
                                    Hide calibrated
                                  </Button>
                                </div>
                              </div>
                              <div className="h-[380px] w-full overflow-hidden rounded-lg border border-stone-200/80">
                                <PlotlyChart
                                  figure={applyDisplayState(figureSet.d18o, d18State.rawOnly, d18State.hideCalibrated)}
                                  className="h-full w-full"
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

                  <OutlierTablesPanel title={`${section.species} outlier tables`} tables={section.outlier_tables} />
                </div>
              </details>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}




