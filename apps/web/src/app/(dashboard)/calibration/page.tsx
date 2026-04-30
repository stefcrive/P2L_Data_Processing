"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

import { PlotlyChart, type PlotlyHoverPayload, type PlotlyPoint } from "@/components/charts/plotly-chart";
import { SharedCycleDiagnosticsTable } from "@/components/diagnostics/cycle-diagnostics-table";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { MultiSelectDropdown } from "@/components/ui/multi-select-dropdown";
import { api } from "@/lib/api";
import type {
  CalibrationConfig,
  CalibrationOfficialValue,
  CalibrationPrecisionSummary,
  CalibrationWorkspace,
  CycleDiagnosticsPayload,
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
type HoverPreviewState = {
  target: SelectedTarget;
  clientX: number;
  clientY: number;
};
type FigureShape = Record<string, unknown> & {
  data: Array<Record<string, unknown>>;
  layout: Record<string, unknown>;
};
type SelectionSourceChart = {
  title: string;
  description: string;
  chartKey?: string;
  figure?: Record<string, unknown>;
};
type ColorScaleBounds = {
  min: number;
  max: number;
};
type LinearityOffsetField = "line_1_offset_d13" | "line_1_offset_d18" | "line_2_offset_d13" | "line_2_offset_d18";
type LinearityOffsetDraftState = Record<LinearityOffsetField, string>;

const PRECISION_PASS_THRESHOLD = 0.07;
const INCLUSION_PASS_THRESHOLD = 80;
const OFFICIAL_VALUE_TYPE_D13 = "VPDB(13C)";
const OFFICIAL_VALUE_TYPE_D18 = "VSMOW(18O)";
const SELECTION_EDITOR_DEFAULT_OFFSET = 0.1;
const HOVER_PREVIEW_SHOW_DELAY_MS = 500;
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
const CARBONATE_MATERIAL_OPTIONS = [
  { value: "calcite", label: "Calcite" },
  { value: "aragonite", label: "Aragonite" },
] as const;

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

function formatOfficialValue(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(3)} ‰` : "Not set";
}

type OfficialValuesRow = {
  standard: string;
  d13Value: number | null;
  d18Value: number | null;
  source: string | null;
};

function buildOfficialValuesRows(values: CalibrationOfficialValue[]): OfficialValuesRow[] {
  const rowsByStandard = new Map<string, OfficialValuesRow>();
  for (const item of values) {
    const standard = String(item.standard ?? "").trim().toUpperCase();
    const isotopicType = String(item.isotopic_value_type ?? "").trim();
    if (!standard) {
      continue;
    }
    const current =
      rowsByStandard.get(standard) ??
      {
        standard,
        d13Value: null,
        d18Value: null,
        source: null,
      };
    const value = typeof item.value === "number" && Number.isFinite(item.value) ? item.value : null;
    if (isotopicType === OFFICIAL_VALUE_TYPE_D13) {
      current.d13Value = value;
    } else if (isotopicType === OFFICIAL_VALUE_TYPE_D18) {
      current.d18Value = value;
    }
    if (item.source && item.source.trim()) {
      current.source = item.source.trim();
    }
    rowsByStandard.set(standard, current);
  }
  return Array.from(rowsByStandard.values()).sort((a, b) => a.standard.localeCompare(b.standard));
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

function isPartiallySaturatedCollectorStatus(value: unknown): boolean {
  return String(value ?? "").trim().toLowerCase() === "partially saturated collectors";
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

function getSetValueCycleNumber(tableRows: Array<Record<string, unknown>>): number | null {
  const selectedRow = tableRows.find((row) => asBoolean(row["Set Value Cycle"]));
  if (!selectedRow) {
    return null;
  }
  return toFiniteNumber(selectedRow["Cycle"]);
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
  const setValueCycle = getSetValueCycleNumber(tableRows);
  if (setValueCycle != null) {
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
        if (Math.abs(x - setValueCycle) > 0.0001) {
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
        name: "Set-value cycle",
        x: highlightX,
        y: highlightY,
        marker: {
          size: 11,
          color: "#7C3AED",
          symbol: "diamond-open",
          line: { color: "#7C3AED", width: 2 },
        },
      });
      hasChanges = true;
    }
    const existingShapes = Array.isArray(cloned.layout.shapes) ? [...(cloned.layout.shapes as Array<Record<string, unknown>>)] : [];
    existingShapes.push({
      type: "line",
      x0: setValueCycle,
      x1: setValueCycle,
      y0: 0,
      y1: 1,
      xref: "x",
      yref: "paper",
      line: { color: "#7C3AED", width: 2, dash: "dot" },
    });
    const existingAnnotations = Array.isArray(cloned.layout.annotations)
      ? [...(cloned.layout.annotations as Array<Record<string, unknown>>)]
      : [];
    existingAnnotations.push({
      x: setValueCycle,
      y: 1,
      xref: "x",
      yref: "paper",
      yanchor: "bottom",
      showarrow: false,
      text: "Set value cycle",
      font: { color: "#5B21B6", size: 11 },
    });
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
  return ensureFigureUiRevision(
    {
      ...cloned,
      layout: nextLayout,
    },
    "calibration:hover-preview",
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

function linearityOffsetWithFallback(value: number | null | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
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

function normalizeSelectedStandardsForCompare(values: string[] | null | undefined): string[] {
  if (!Array.isArray(values)) {
    return [];
  }
  return values
    .map((value) => String(value ?? "").trim().toUpperCase())
    .filter((value) => value.length > 0)
    .sort((left, right) => left.localeCompare(right));
}

function selectedStandardsEquals(left: string[] | null | undefined, right: string[] | null | undefined): boolean {
  return JSON.stringify(normalizeSelectedStandardsForCompare(left)) === JSON.stringify(normalizeSelectedStandardsForCompare(right));
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

function clampNumber(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function computeHoverPreviewPosition(
  clientX: number,
  clientY: number,
  tooltipWidth = 420,
  tooltipHeight = 320,
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

function applyColorScaleRangeToFigure(
  figure: Record<string, unknown> | undefined,
  range: [number, number] | null,
  revisionScope = "calibration:chart",
): Record<string, unknown> | undefined {
  if (!figure || !range) {
    return ensureFigureUiRevision(figure, revisionScope);
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
    nextLayout = {
      ...nextLayout,
      [key]: {
        ...(axis as Record<string, unknown>),
        cauto: false,
        cmin,
        cmax,
      },
    };
  }
  if (!hasColorMapping) {
    return ensureFigureUiRevision(figure, revisionScope);
  }
  return ensureFigureUiRevision(
    {
      ...cloned,
      data: nextData,
      layout: nextLayout,
    },
    revisionScope,
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
    const setValueCycle = asBoolean(row["Set Value Cycle"]);
    return {
      ...row,
      "Cycle status": excludedAny ? "Saturated" : "Successful",
      "Set Value Cycle": setValueCycle,
    };
  });

  const preferredColumns = [
    "Cycle",
    "Cycle status",
    "Set Value Cycle",
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
        <span className="rounded-full bg-violet-100 px-2 py-1 text-violet-800">Set-value cycle</span>
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
              const setValueCycle = asBoolean(row["Set Value Cycle"]);
              return (
                <tr key={rowIndex} className={cn(setValueCycle ? "bg-violet-100/90" : saturated ? "bg-rose-50/80" : "bg-emerald-50/70")}>
                  {columns.map((column) => {
                    const cellValue = row[column];
                    const flaggedColumn = column.startsWith("Excluded");
                    const flaggedValue = flaggedColumn ? asBoolean(cellValue) : false;
                    const setValueColumn = column === "Set Value Cycle";
                    const setValueColumnValue = setValueColumn ? asBoolean(cellValue) : false;
                    return (
                      <td
                        key={column}
                        className={cn(
                          "px-3 py-2",
                          setValueColumn
                            ? setValueColumnValue
                              ? "font-semibold text-violet-800"
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
  onPickDeltaValue,
}: {
  title: string;
  diagnostics?: CycleDiagnosticsPayload;
  loading: boolean;
  onPickDeltaValue?: (value: number) => void;
}) {
  const cycleMean = diagnostics?.cycle_mean ?? {};
  const validMean = asNumber(cycleMean.valid_mean);
  const firstValidCycle = asNumber(cycleMean.selected_value) ?? asNumber(cycleMean.mean);
  const reason = asString(cycleMean.reason);
  const diagnosticsFigure = ensureCollectorIntensityTraces(diagnostics?.figure, diagnostics?.table ?? []);
  const canPickValidMean = typeof onPickDeltaValue === "function" && validMean != null;
  const canPickFinalMean = typeof onPickDeltaValue === "function" && firstValidCycle != null;

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
                <div className="text-xs uppercase tracking-wide text-stone-500">Cycle Mean</div>
                <div className="mt-1 text-lg font-semibold text-stone-900">{formatDeltaValue(validMean)}</div>
              </button>
              <button
                type="button"
                onClick={() => {
                  if (canPickFinalMean) {
                    onPickDeltaValue(firstValidCycle);
                  }
                }}
                disabled={!canPickFinalMean}
                className={cn(
                  "rounded-lg border border-stone-200 p-3 text-left transition",
                  canPickFinalMean ? "cursor-pointer hover:border-fuchsia-400 hover:bg-fuchsia-50" : "",
                )}
              >
                <div className="text-xs uppercase tracking-wide text-stone-500">First valid cycle</div>
                <div className="mt-1 text-lg font-semibold text-stone-900">{formatDeltaValue(firstValidCycle)}</div>
              </button>
              <div className="rounded-lg border border-stone-200 p-3">
                <div className="text-xs uppercase tracking-wide text-stone-500">Method</div>
                <div className="mt-1 text-sm font-medium text-stone-900">{asString(cycleMean.method) || "N/A"}</div>
              </div>
            </div>

            {reason ? <div className="text-sm text-stone-500">Diagnostics note: {reason}</div> : null}

            <div className="grid gap-4 xl:grid-cols-2 xl:items-start">
              <PlotlyChart
                figure={ensureFigureUiRevision(diagnosticsFigure, "calibration:selection-cycle")}
                className="mx-auto aspect-square min-h-[320px] w-full max-w-[560px]"
              />
              <div className="min-w-0">
                <SharedCycleDiagnosticsTable rows={diagnostics.table ?? []} />
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

function PrecisionMetricPanel({
  label,
  value,
  disabled = false,
  disabledText = "(not enabled)",
}: {
  label: string;
  value?: number | null;
  disabled?: boolean;
  disabledText?: string;
}) {
  const styles = toneClasses(disabled ? "neutral" : classifyPrecision(value));
  return (
    <div className={`rounded-xl border px-3 py-3 ${styles.shell}`}>
      <div className="text-[11px] uppercase tracking-[0.14em] text-stone-500">{label}</div>
      <div className={`mt-2 text-2xl font-semibold leading-none ${disabled ? "" : "tabular-nums"} ${styles.value}`}>
        {disabled ? disabledText : formatMetricWithUnit(value)}
      </div>
    </div>
  );
}

function IsotopeSummaryTile({
  label,
  precision,
  average,
  correctedPrecision,
  linearityEnabled,
}: {
  label: string;
  precision?: number | null;
  average?: number | null;
  correctedPrecision?: number | null;
  linearityEnabled: boolean;
}) {
  return (
    <div className="rounded-xl border border-stone-200 bg-stone-50/80 p-4">
      <div className="text-xs uppercase tracking-[0.14em] text-stone-500">{label} precision</div>
      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <PrecisionMetricPanel label="Normal" value={precision} />
        <PrecisionMetricPanel label="Linearity corrected" value={correctedPrecision} disabled={!linearityEnabled} />
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

function PrecisionCard({ summary, linearityEnabled }: { summary: CalibrationPrecisionSummary; linearityEnabled: boolean }) {
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
            linearityEnabled={linearityEnabled}
          />
          <IsotopeSummaryTile
            label="δ18O (‰)"
            precision={summary.d18_precision}
            average={summary.d18_average}
            correctedPrecision={summary.d18_linearity_corrected_precision}
            linearityEnabled={linearityEnabled}
          />
        </div>
        {linePrecisionEntries.length ? (
          <div className="rounded-xl border border-stone-200 p-4">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-xs uppercase tracking-[0.14em] text-stone-500">Line precision breakdown</div>
              <div className="text-xs text-stone-500">{linePrecisionEntries.length} lines</div>
            </div>
            <div className="overflow-x-auto rounded-lg border border-stone-200">
              <div className="grid min-w-[760px] grid-cols-[100px_repeat(4,minmax(0,1fr))] bg-stone-100 px-3 py-2 text-xs font-medium uppercase tracking-wide text-stone-600">
                <span>Line</span>
                <span className="normal-case">δ13C raw (‰)</span>
                <span className="normal-case">δ13C linearity corr (‰)</span>
                <span className="normal-case">δ18O raw (‰)</span>
                <span className="normal-case">δ18O linearity corr (‰)</span>
              </div>
              {linePrecisionEntries.map(([line, values], index) => {
                const d13Tone = toneClasses(classifyPrecision(values.d13_precision));
                const d13LinearityTone = toneClasses(
                  linearityEnabled ? classifyPrecision(values.d13_linearity_corrected_precision) : "neutral",
                );
                const d18Tone = toneClasses(classifyPrecision(values.d18_precision));
                const d18LinearityTone = toneClasses(
                  linearityEnabled ? classifyPrecision(values.d18_linearity_corrected_precision) : "neutral",
                );
                return (
                  <div
                    key={line}
                    className={`grid min-w-[760px] grid-cols-[100px_repeat(4,minmax(0,1fr))] px-3 py-2 text-sm tabular-nums text-stone-700 ${
                      index % 2 ? "bg-stone-50" : "bg-white"
                    }`}
                  >
                    <span className="font-medium text-stone-900">Line {line}</span>
                    <span className={`inline-flex max-w-fit rounded-md border px-2 py-1 text-base font-semibold ${d13Tone.shell} ${d13Tone.value}`}>
                      {formatMetricWithUnit(values.d13_precision)}
                    </span>
                    <span className={`inline-flex max-w-fit rounded-md border px-2 py-1 text-base font-semibold ${d13LinearityTone.shell} ${d13LinearityTone.value}`}>
                      {linearityEnabled ? formatMetricWithUnit(values.d13_linearity_corrected_precision) : "(not enabled)"}
                    </span>
                    <span className={`inline-flex max-w-fit rounded-md border px-2 py-1 text-base font-semibold ${d18Tone.shell} ${d18Tone.value}`}>
                      {formatMetricWithUnit(values.d18_precision)}
                    </span>
                    <span className={`inline-flex max-w-fit rounded-md border px-2 py-1 text-base font-semibold ${d18LinearityTone.shell} ${d18LinearityTone.value}`}>
                      {linearityEnabled ? formatMetricWithUnit(values.d18_linearity_corrected_precision) : "(not enabled)"}
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="mt-2 text-xs text-stone-500">
              Overall precision is computed from all included rows, so it is not the arithmetic mean of per-line precision values.
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
  const [isOfficialValuesModalOpen, setOfficialValuesModalOpen] = useState(false);
  const [isOfficialValuesEditMode, setOfficialValuesEditMode] = useState(false);
  const [officialValuesDraftRows, setOfficialValuesDraftRows] = useState<Record<string, { d13: string; d18: string }>>({});
  const [newStandardName, setNewStandardName] = useState("");
  const [newStandardD13, setNewStandardD13] = useState("");
  const [newStandardD18, setNewStandardD18] = useState("");
  const [officialValuesError, setOfficialValuesError] = useState<string | null>(null);
  const [singleValue, setSingleValue] = useState(0);
  const [singleOffset, setSingleOffset] = useState(SELECTION_EDITOR_DEFAULT_OFFSET);
  const [crossD13Value, setCrossD13Value] = useState(0);
  const [crossD18Value, setCrossD18Value] = useState(0);
  const [multiOffsetD13, setMultiOffsetD13] = useState(0);
  const [multiOffsetD18, setMultiOffsetD18] = useState(0);
  const [linearityTouched, setLinearityTouched] = useState(false);
  const [setValueHighlightNonce, setSetValueHighlightNonce] = useState(0);
  const [isSetValueInputHighlighted, setIsSetValueInputHighlighted] = useState(false);
  const [colorScaleRange, setColorScaleRange] = useState<[number, number] | null>(null);
  const [colorScaleRangeParam, setColorScaleRangeParam] = useState<string | null>(null);
  const [linearityOffsetDrafts, setLinearityOffsetDrafts] = useState<LinearityOffsetDraftState>({
    line_1_offset_d13: "0",
    line_1_offset_d18: "0",
    line_2_offset_d13: "0",
    line_2_offset_d18: "0",
  });
  const [linearityOffsetEditing, setLinearityOffsetEditing] = useState<LinearityOffsetField | null>(null);
  const [hoverPreview, setHoverPreview] = useState<HoverPreviewState | null>(null);
  const hoverPreviewHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hoverPreviewShowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingHoverPreviewRef = useRef<HoverPreviewState | null>(null);
  const colorScaledFigureCacheRef = useRef<WeakMap<Record<string, unknown>, Record<string, unknown>>>(new WeakMap());
  const colorScaleSignatureRef = useRef<string>("");
  const draftStorageKey = sessionId ? `calibration-config:${sessionId}` : null;
  const activeTarget = selectedTargets.length ? selectedTargets[Math.min(activeTargetIndex, selectedTargets.length - 1)] : null;
  const activeIsotopeTarget = activeTarget && activeTarget.isotopeKey !== "cross" ? activeTarget : null;
  const activeCrossTarget = activeTarget && activeTarget.isotopeKey === "cross" ? activeTarget : null;
  const hoverPreviewTarget = hoverPreview?.target ?? null;
  const hoverPreviewDiagnosticsTarget: SelectedTarget | null =
    hoverPreviewTarget == null
      ? null
      : hoverPreviewTarget.isotopeKey === "cross"
        ? { ...hoverPreviewTarget, isotopeKey: "d13C" }
        : hoverPreviewTarget;

  useEffect(() => {
    if (!(isSelectionEditorOpen || isOfficialValuesModalOpen) || typeof window === "undefined") {
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setSelectionEditorOpen(false);
        setOfficialValuesModalOpen(false);
        setOfficialValuesEditMode(false);
        setOfficialValuesError(null);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isOfficialValuesModalOpen, isSelectionEditorOpen]);

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
    if (isSelectionEditorOpen || isOfficialValuesModalOpen) {
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
  }, [isOfficialValuesModalOpen, isSelectionEditorOpen]);

  useEffect(() => {
    if (!activeIsotopeTarget) {
      return;
    }
    setSingleOffset(SELECTION_EDITOR_DEFAULT_OFFSET);
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

  const officialValuesQuery = useQuery({
    queryKey: ["official-standard-values"],
    queryFn: () => api.listOfficialStandardValues(),
    enabled: isOfficialValuesModalOpen,
  });

  const singleDiagnosticsQuery = useQuery({
    queryKey: ["processing-diagnostics", sessionId, activeIsotopeTarget?.rowLabel, activeIsotopeTarget?.isotopeKey],
    queryFn: () =>
      api.getProcessingCycleDiagnostics(
        sessionId!,
        diagnosticsTargetPayload(activeIsotopeTarget!, activeIsotopeTarget!.isotopeKey as "d13C" | "d18O"),
      ),
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
    enabled: Boolean(sessionId && hoverPreviewDiagnosticsTarget && !isSelectionEditorOpen && !isOfficialValuesModalOpen),
    staleTime: 60_000,
  });

  useEffect(() => {
    if (!activeIsotopeTarget) {
      return;
    }
    const collectorStatus = asString(singleDiagnosticsQuery.data?.target?.collector_status);
    const selectedCycleValue = asNumber(singleDiagnosticsQuery.data?.cycle_mean?.selected_value);
    const diagnosticsCurrentValue = asNumber(singleDiagnosticsQuery.data?.target?.current_value);
    const seedValue =
      isPartiallySaturatedCollectorStatus(collectorStatus) && selectedCycleValue != null
        ? selectedCycleValue
        : diagnosticsCurrentValue;
    setSingleValue(
      seedValue != null ? roundDeltaValue(seedValue) : roundDeltaValue(activeIsotopeTarget.currentValue ?? 0),
    );
  }, [activeIsotopeTarget, singleDiagnosticsQuery.data?.cycle_mean, singleDiagnosticsQuery.data?.target]);

  useEffect(() => {
    if (activeCrossTarget) {
      const d13CollectorStatus = asString(crossD13DiagnosticsQuery.data?.target?.collector_status);
      const d18CollectorStatus = asString(crossD18DiagnosticsQuery.data?.target?.collector_status);
      const d13SelectedCycleValue = asNumber(crossD13DiagnosticsQuery.data?.cycle_mean?.selected_value);
      const d18SelectedCycleValue = asNumber(crossD18DiagnosticsQuery.data?.cycle_mean?.selected_value);
      const nextD13 =
        isPartiallySaturatedCollectorStatus(d13CollectorStatus) && d13SelectedCycleValue != null
          ? roundDeltaValue(d13SelectedCycleValue)
          : typeof crossD13DiagnosticsQuery.data?.target?.current_value === "number"
            ? roundDeltaValue(crossD13DiagnosticsQuery.data.target.current_value as number)
          : roundDeltaValue(activeCrossTarget.currentD13 ?? 0);
      const nextD18 =
        isPartiallySaturatedCollectorStatus(d18CollectorStatus) && d18SelectedCycleValue != null
          ? roundDeltaValue(d18SelectedCycleValue)
          : typeof crossD18DiagnosticsQuery.data?.target?.current_value === "number"
            ? roundDeltaValue(crossD18DiagnosticsQuery.data.target.current_value as number)
          : roundDeltaValue(activeCrossTarget.currentD18 ?? 0);
      setCrossD13Value(nextD13);
      setCrossD18Value(nextD18);
    }
  }, [
    activeCrossTarget,
    crossD13DiagnosticsQuery.data?.cycle_mean,
    crossD13DiagnosticsQuery.data?.target,
    crossD18DiagnosticsQuery.data?.cycle_mean,
    crossD18DiagnosticsQuery.data?.target,
  ]);

  useEffect(() => {
    setConfig(null);
    setHasLoadedDraft(false);
    setLinearityTouched(false);
    setOfficialValuesModalOpen(false);
    setOfficialValuesEditMode(false);
    setOfficialValuesDraftRows({});
    setOfficialValuesError(null);
    setNewStandardName("");
    setNewStandardD13("");
    setNewStandardD18("");
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
    setConfig((current) =>
      current
        ? {
            ...workspaceQuery.data.config,
            ...current,
            linearity: {
              ...workspaceQuery.data.config.linearity,
            },
          }
        : workspaceQuery.data.config,
    );
  }, [hasLoadedDraft, workspaceQuery.data]);

  useEffect(() => {
    const sourceConfig = config ?? workspaceQuery.data?.config;
    if (!sourceConfig || linearityOffsetEditing) {
      return;
    }
    const nextDrafts: LinearityOffsetDraftState = {
      line_1_offset_d13: formatDecimalInput(readLinearityOffsetValue(sourceConfig.linearity, "line_1_offset_d13")),
      line_1_offset_d18: formatDecimalInput(readLinearityOffsetValue(sourceConfig.linearity, "line_1_offset_d18")),
      line_2_offset_d13: formatDecimalInput(readLinearityOffsetValue(sourceConfig.linearity, "line_2_offset_d13")),
      line_2_offset_d18: formatDecimalInput(readLinearityOffsetValue(sourceConfig.linearity, "line_2_offset_d18")),
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
  }, [config, workspaceQuery.data, linearityOffsetEditing]);

  useEffect(() => {
    if (!draftStorageKey || typeof window === "undefined" || !config) {
      return;
    }
    window.sessionStorage.setItem(draftStorageKey, JSON.stringify(config));
  }, [config, draftStorageKey]);

  useEffect(() => {
    if (!isOfficialValuesModalOpen) {
      return;
    }
    const rows = buildOfficialValuesRows(officialValuesQuery.data ?? []);
    const drafts: Record<string, { d13: string; d18: string }> = {};
    for (const row of rows) {
      drafts[row.standard] = {
        d13: row.d13Value == null ? "" : row.d13Value.toFixed(3),
        d18: row.d18Value == null ? "" : row.d18Value.toFixed(3),
      };
    }
    setOfficialValuesDraftRows(drafts);
  }, [isOfficialValuesModalOpen, officialValuesQuery.data]);

  const deferredConfig = useDeferredValue(config);

  const previewQuery = useQuery({
    queryKey: ["calibration-workspace-preview", sessionId, deferredConfig],
    queryFn: () => api.previewCalibrationWorkspace(sessionId!, deferredConfig!),
    enabled: Boolean(sessionId && deferredConfig),
  });
  const previewWorkspace = previewQuery.data as CalibrationWorkspace | undefined;
  const persistedWorkspace = workspaceQuery.data as CalibrationWorkspace | undefined;
  const workspaceForColorScale = previewWorkspace ?? persistedWorkspace;
  const activeColorParam = config?.color_param ?? workspaceForColorScale?.config.color_param ?? null;
  const colorScaleFigures = useMemo<Array<Record<string, unknown> | undefined>>(() => {
    if (!workspaceForColorScale) {
      return [];
    }
    const figures: Array<Record<string, unknown> | undefined> = [
      workspaceForColorScale.figures["VPDB(13C)"],
      workspaceForColorScale.figures["VSMOW(18O)"],
      workspaceForColorScale.figures.calibration_3d,
      workspaceForColorScale.figures.crossplot,
      workspaceForColorScale.linearity_figures.d13_raw,
      workspaceForColorScale.linearity_figures.d13_corrected,
      workspaceForColorScale.linearity_figures.d18_raw,
      workspaceForColorScale.linearity_figures.d18_corrected,
    ];
    for (const section of workspaceForColorScale.standard_sections) {
      figures.push(section.d13_figure, section.d18_figure);
    }
    return figures;
  }, [workspaceForColorScale]);
  const colorScaleBounds = useMemo(() => deriveColorScaleBounds(colorScaleFigures), [colorScaleFigures]);
  const colorScaleTwoSigmaRange = useMemo(() => {
    if (!colorScaleBounds) {
      return null;
    }
    return deriveTwoSigmaColorScaleRange(colorScaleFigures, colorScaleBounds);
  }, [colorScaleBounds, colorScaleFigures]);

  useEffect(() => {
    if (!activeColorParam || !colorScaleBounds) {
      return;
    }
    const bounds = colorScaleBounds;
    const fullRange: [number, number] = [bounds.min, bounds.max];
    const defaultRange = colorScaleTwoSigmaRange ?? fullRange;
    const parameterChanged = colorScaleRangeParam !== activeColorParam;
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
      setColorScaleRangeParam(activeColorParam);
    }
  }, [activeColorParam, colorScaleBounds, colorScaleRangeParam, colorScaleTwoSigmaRange]);

  const saveLinearityMutation = useMutation({
    mutationFn: (payload: { linearity: CalibrationConfig["linearity"]; selectedStandards: string[] }) =>
      api.setCalibrationLinearity(sessionId!, payload.linearity, payload.selectedStandards),
    onSuccess: async (workspace) => {
      queryClient.setQueryData(["calibration-workspace", sessionId], workspace);
      await queryClient.invalidateQueries({ queryKey: ["processing-workspace", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-diagnostics", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-diagnostics-cross-d13", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-diagnostics-cross-d18", sessionId] });
    },
  });

  useEffect(() => {
    if (!sessionId || !hasLoadedDraft || !linearityTouched || !config || !workspaceQuery.data || saveLinearityMutation.isPending) {
      return;
    }
    if (
      linearityConfigEquals(config.linearity, workspaceQuery.data.config.linearity) &&
      selectedStandardsEquals(config.selected_standards, workspaceQuery.data.config.selected_standards)
    ) {
      return;
    }
    const timer = window.setTimeout(() => {
      saveLinearityMutation.mutate({
        linearity: config.linearity,
        selectedStandards: config.selected_standards,
      });
    }, 450);
    return () => window.clearTimeout(timer);
  }, [
    config,
    hasLoadedDraft,
    linearityTouched,
    saveLinearityMutation,
    saveLinearityMutation.isPending,
    sessionId,
    workspaceQuery.data,
  ]);

  const runMutation = useMutation({
    mutationFn: (payload: CalibrationConfig) => api.runCalibration(sessionId!, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration-workspace", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration-workspace-preview", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration", sessionId] });
    },
  });

  const resetCalibrationMutation = useMutation({
    mutationFn: () => api.resetCalibration(sessionId!),
    onSuccess: async () => {
      if (draftStorageKey && typeof window !== "undefined") {
        window.sessionStorage.removeItem(draftStorageKey);
      }
      setConfig(null);
      await queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration-workspace", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration-workspace-preview", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-workspace", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-diagnostics", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-diagnostics-cross-d13", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-diagnostics-cross-d18", sessionId] });
    },
  });

  const upsertOfficialValueMutation = useMutation({
    mutationFn: (payload: { standard: string; isotopic_value_type: string; value: number; source?: string | null }) =>
      api.upsertOfficialStandardValue(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["official-standard-values"] });
      if (sessionId) {
        await queryClient.invalidateQueries({ queryKey: ["calibration-workspace", sessionId] });
        await queryClient.invalidateQueries({ queryKey: ["calibration-workspace-preview", sessionId] });
      }
    },
  });

  const deleteOfficialStandardMutation = useMutation({
    mutationFn: (standard: string) => api.deleteOfficialStandard(standard),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["official-standard-values"] });
      if (sessionId) {
        await queryClient.invalidateQueries({ queryKey: ["calibration-workspace", sessionId] });
        await queryClient.invalidateQueries({ queryKey: ["calibration-workspace-preview", sessionId] });
      }
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
      await queryClient.invalidateQueries({ queryKey: ["processing-diagnostics", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-diagnostics-cross-d13", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-diagnostics-cross-d18", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration-workspace", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration-workspace-preview", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration", sessionId] });
    },
  });

  function updateConfig<T extends keyof CalibrationConfig>(key: T, value: CalibrationConfig[T]) {
    setConfig((current) => (current ? { ...current, [key]: value } : current));
  }

  function updateLinearity(key: keyof CalibrationConfig["linearity"], value: boolean | number | string | null) {
    setLinearityTouched(true);
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

  function updateLinearityIntensityCol(intensityCol: string) {
    setLinearityTouched(true);
    setConfig((current) =>
      current
        ? {
            ...current,
            linearity: {
              ...current.linearity,
              intensity_col: intensityCol,
              use_diff_intensity: intensityCol === LINEARITY_INTENSITY_DIFF44,
            },
          }
        : current,
    );
  }

  function updateLinearityCoefficientOffset(isotopeKey: "d13C" | "d18O", value: number) {
    setLinearityTouched(true);
    setConfig((current) => {
      if (!current) {
        return current;
      }
      const next = { ...current };
      if (next.linearity.quadratic) {
        if (isotopeKey === "d13C") {
          next.linearity.manual_d13_per_10v2 = value;
        } else {
          next.linearity.manual_d18_per_10v2 = value;
        }
      } else if (isotopeKey === "d13C") {
        next.linearity.manual_d13_per_10v = value;
      } else {
        next.linearity.manual_d18_per_10v = value;
      }
      const d13Offset = next.linearity.quadratic
        ? Number(next.linearity.manual_d13_per_10v2 ?? 0)
        : Number(next.linearity.manual_d13_per_10v ?? 0);
      const d18Offset = next.linearity.quadratic
        ? Number(next.linearity.manual_d18_per_10v2 ?? 0)
        : Number(next.linearity.manual_d18_per_10v ?? 0);
      const hasOffset = Math.abs(d13Offset) > 1e-12 || Math.abs(d18Offset) > 1e-12;
      next.linearity.manual_override_enabled = hasOffset;
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
    updateLinearity(field, parsed);
  }

  function resetLinearityOffsetDraft(field: LinearityOffsetField) {
    const sourceConfig = config ?? workspaceQuery.data?.config;
    if (!sourceConfig) {
      return;
    }
    const value = readLinearityOffsetValue(sourceConfig.linearity, field);
    setLinearityOffsetDrafts((current) => ({ ...current, [field]: formatDecimalInput(value) }));
  }

  function commitLinearityOffsetDraft(field: LinearityOffsetField) {
    const parsed = parseDecimalInput(linearityOffsetDrafts[field]);
    if (parsed == null) {
      resetLinearityOffsetDraft(field);
      setLinearityOffsetEditing((current) => (current === field ? null : current));
      return;
    }
    updateLinearity(field, parsed);
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

  function coerceSequenceNeighbor(
    value: unknown,
    isotopeKey: "d13C" | "d18O",
  ): { rowLabel: string; identifier2: string; value: number | null; isotopeKey: "d13C" | "d18O" } | null {
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

  const isotopeCycleMean = (singleDiagnosticsQuery.data?.cycle_mean ?? null) as Record<string, unknown> | null;
  const crossD13CycleMean = (crossD13DiagnosticsQuery.data?.cycle_mean ?? null) as Record<string, unknown> | null;
  const crossD18CycleMean = (crossD18DiagnosticsQuery.data?.cycle_mean ?? null) as Record<string, unknown> | null;
  const prevSequenceNeighbor = activeCrossTarget
    ? coerceSequenceNeighbor(crossD13CycleMean?.prev_neighbor, "d13C") ??
      coerceSequenceNeighbor(crossD18CycleMean?.prev_neighbor, "d18O")
    : activeIsotopeTarget
      ? coerceSequenceNeighbor(isotopeCycleMean?.prev_neighbor, activeIsotopeTarget.isotopeKey as "d13C" | "d18O")
      : null;
  const nextSequenceNeighbor = activeCrossTarget
    ? coerceSequenceNeighbor(crossD13CycleMean?.next_neighbor, "d13C") ??
      coerceSequenceNeighbor(crossD18CycleMean?.next_neighbor, "d18O")
    : activeIsotopeTarget
      ? coerceSequenceNeighbor(isotopeCycleMean?.next_neighbor, activeIsotopeTarget.isotopeKey as "d13C" | "d18O")
      : null;
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

  function openProcessingSelectionEditor(chartKey: string, points: PlotlyPoint[], multi = false) {
    const targets = parseSelectedTargets(points, chartKey);
    if (!targets.length) {
      return;
    }
    setTargets(multi ? targets : targets.slice(0, 1));
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
    if (isSelectionEditorOpen || isOfficialValuesModalOpen) {
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

  function resolveSetValuePayload(
    isotopeKey: "d13C" | "d18O",
    requestedDisplayValue: number,
    selectedDisplayValue: number | null,
    diagnosticsCurrentValue: number | null,
  ): number {
    if (selectedDisplayValue == null || diagnosticsCurrentValue == null) {
      return requestedDisplayValue;
    }
    const displayToRawDelta = selectedDisplayValue - diagnosticsCurrentValue;
    return requestedDisplayValue - displayToRawDelta;
  }

  async function applySingleValue() {
    if (!sessionId || !activeIsotopeTarget) {
      return;
    }
    const isotopeKey = activeIsotopeTarget.isotopeKey as "d13C" | "d18O";
    const selectedDisplayValue = typeof activeIsotopeTarget.currentValue === "number" ? activeIsotopeTarget.currentValue : null;
    const diagnosticsCurrentValue = asNumber(singleDiagnosticsQuery.data?.target?.current_value);
    const payloadValue = resolveSetValuePayload(isotopeKey, singleValue, selectedDisplayValue, diagnosticsCurrentValue);
    await editMutation.mutateAsync({
      action: "set_value",
      targets: [{ row_label: activeIsotopeTarget.rowLabel, isotope_key: isotopeKey }],
      value: payloadValue,
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
      offset: singleOffset,
    });
  }

  async function applyCrossValues() {
    if (!sessionId || !activeCrossTarget) {
      return;
    }
    const d13DiagnosticsCurrent = asNumber(crossD13DiagnosticsQuery.data?.target?.current_value);
    const d18DiagnosticsCurrent = asNumber(crossD18DiagnosticsQuery.data?.target?.current_value);
    const d13SelectedDisplay = typeof activeCrossTarget.currentD13 === "number" ? activeCrossTarget.currentD13 : null;
    const d18SelectedDisplay = typeof activeCrossTarget.currentD18 === "number" ? activeCrossTarget.currentD18 : null;
    const payloadD13 = resolveSetValuePayload("d13C", crossD13Value, d13SelectedDisplay, d13DiagnosticsCurrent);
    const payloadD18 = resolveSetValuePayload("d18O", crossD18Value, d18SelectedDisplay, d18DiagnosticsCurrent);
    await editMutation.mutateAsync({
      action: "set_value",
      targets: [{ row_label: activeCrossTarget.rowLabel, isotope_key: "d13C" }],
      value: payloadD13,
    });
    await editMutation.mutateAsync({
      action: "set_value",
      targets: [{ row_label: activeCrossTarget.rowLabel, isotope_key: "d18O" }],
      value: payloadD18,
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

  function updateOfficialValueDraft(standard: string, isotopeKey: "d13" | "d18", value: string) {
    setOfficialValuesDraftRows((current) => ({
      ...current,
      [standard]: {
        d13: current[standard]?.d13 ?? "",
        d18: current[standard]?.d18 ?? "",
        [isotopeKey]: value,
      },
    }));
  }

  async function saveOfficialValuesRow(standard: string) {
    const draft = officialValuesDraftRows[standard];
    if (!draft) {
      return;
    }
    const d13 = parseStrictNumber(draft.d13);
    const d18 = parseStrictNumber(draft.d18);
    if (d13 == null || d18 == null) {
      setOfficialValuesError(`Enter valid numeric d13C and d18O values for ${standard}.`);
      return;
    }
    try {
      setOfficialValuesError(null);
      await Promise.all([
        upsertOfficialValueMutation.mutateAsync({
          standard,
          isotopic_value_type: OFFICIAL_VALUE_TYPE_D13,
          value: d13,
          source: "manual",
        }),
        upsertOfficialValueMutation.mutateAsync({
          standard,
          isotopic_value_type: OFFICIAL_VALUE_TYPE_D18,
          value: d18,
          source: "manual",
        }),
      ]);
    } catch (error) {
      setOfficialValuesError(error instanceof Error ? error.message : `Failed to save values for ${standard}.`);
    }
  }

  async function addOfficialValuesItem() {
    const standard = String(newStandardName ?? "").trim().toUpperCase();
    const d13 = parseStrictNumber(newStandardD13);
    const d18 = parseStrictNumber(newStandardD18);
    if (!standard) {
      setOfficialValuesError("Standard name is required.");
      return;
    }
    if (d13 == null || d18 == null) {
      setOfficialValuesError("Enter valid numeric d13C and d18O values for the new standard.");
      return;
    }
    try {
      setOfficialValuesError(null);
      await Promise.all([
        upsertOfficialValueMutation.mutateAsync({
          standard,
          isotopic_value_type: OFFICIAL_VALUE_TYPE_D13,
          value: d13,
          source: "manual",
        }),
        upsertOfficialValueMutation.mutateAsync({
          standard,
          isotopic_value_type: OFFICIAL_VALUE_TYPE_D18,
          value: d18,
          source: "manual",
        }),
      ]);
      setNewStandardName("");
      setNewStandardD13("");
      setNewStandardD18("");
    } catch (error) {
      setOfficialValuesError(error instanceof Error ? error.message : `Failed to add standard ${standard}.`);
    }
  }

  async function removeOfficialValuesItem(standard: string) {
    if (!standard) {
      return;
    }
    try {
      setOfficialValuesError(null);
      await deleteOfficialStandardMutation.mutateAsync(standard);
    } catch (error) {
      setOfficialValuesError(error instanceof Error ? error.message : `Failed to remove standard ${standard}.`);
    }
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

  const workspace = persistedWorkspace;
  const activeConfig = config ?? workspace?.config ?? null;
  const displayedWorkspace = workspaceForColorScale;

  if (!workspace || !activeConfig || !displayedWorkspace) {
    return null;
  }
  const colorSliderBounds: ColorScaleBounds = colorScaleBounds ?? { min: 0, max: 1 };
  const effectiveColorScaleRange = normalizeColorScaleRange(
    colorScaleRange ?? colorScaleTwoSigmaRange ?? [colorSliderBounds.min, colorSliderBounds.max],
    colorSliderBounds,
  );
  const colorScaleSignature = `${effectiveColorScaleRange[0]}:${effectiveColorScaleRange[1]}`;
  if (colorScaleSignatureRef.current !== colorScaleSignature) {
    colorScaleSignatureRef.current = colorScaleSignature;
    colorScaledFigureCacheRef.current = new WeakMap();
  }
  const withColorScaleRange = (figure: Record<string, unknown> | undefined) => {
    if (!figure) {
      return figure;
    }
    const cached = colorScaledFigureCacheRef.current.get(figure);
    if (cached) {
      return cached;
    }
    const nextFigure = applyColorScaleRangeToFigure(figure, effectiveColorScaleRange) ?? figure;
    colorScaledFigureCacheRef.current.set(figure, nextFigure);
    return nextFigure;
  };

  const minDate = displayedWorkspace.available_values.min_date ?? undefined;
  const maxDate = displayedWorkspace.available_values.max_date ?? undefined;
  const selectedStandards = activeConfig.selected_standards;
  const selectedStandardOfficialValues = displayedWorkspace.selected_standard_official_values ?? [];
  const selectedStandardsSet = new Set(selectedStandards.map((item) => String(item ?? "").trim().toUpperCase()).filter(Boolean));
  const allOfficialValueRows = buildOfficialValuesRows(officialValuesQuery.data ?? selectedStandardOfficialValues);
  const selectedOfficialValueRows = allOfficialValueRows.filter((row) => selectedStandardsSet.has(row.standard));
  const hasMissingOfficialValues =
    selectedStandardsSet.size > selectedOfficialValueRows.length ||
    selectedOfficialValueRows.some((row) => row.d13Value == null || row.d18Value == null);
  const standardsValuesBusy = upsertOfficialValueMutation.isPending || deleteOfficialStandardMutation.isPending;
  const officialValuesLoading = isOfficialValuesModalOpen && officialValuesQuery.isLoading && !officialValuesQuery.data;
  const officialValuesQueryError = officialValuesQuery.error instanceof Error ? officialValuesQuery.error.message : null;
  const selectedLinearityIntensityCol = LINEARITY_INTENSITY_OPTIONS.includes(
    activeConfig.linearity.intensity_col as (typeof LINEARITY_INTENSITY_OPTIONS)[number],
  )
    ? activeConfig.linearity.intensity_col
    : activeConfig.linearity.use_diff_intensity
      ? LINEARITY_INTENSITY_DIFF44
      : LINEARITY_INTENSITY_SAMP44;
  const selectedLinearityBasisLabel = getLinearityIntensityOptionLabel(selectedLinearityIntensityCol);
  const d13Fit = (displayedWorkspace.linearity_fits?.d13C ?? {}) as Record<string, unknown>;
  const d18Fit = (displayedWorkspace.linearity_fits?.d18O ?? {}) as Record<string, unknown>;
  const d13FitSlope = asNumber(d13Fit.slope);
  const d18FitSlope = asNumber(d18Fit.slope);
  const d13FitQuad = asNumber(d13Fit.quad);
  const d18FitQuad = asNumber(d18Fit.quad);
  const lineIntensityBasis = String(displayedWorkspace.linearity_fits?.intensity_col ?? selectedLinearityIntensityCol ?? "N/A");
  const previewError = previewQuery.error instanceof Error ? previewQuery.error.message : null;
  const runError = runMutation.error instanceof Error ? runMutation.error.message : null;
  const resetError = resetCalibrationMutation.error instanceof Error ? resetCalibrationMutation.error.message : null;
  const hasUnsavedPreview = JSON.stringify(activeConfig) !== JSON.stringify(workspace.config);
  const precisionSummaries = displayedWorkspace.precision_summaries;
  const standardPrecisionRows = precisionSummaries.filter((summary) => selectedStandards.includes(summary.standard)).slice(0, 6);
  const coefficientOffsetEnabled = Boolean(activeConfig.linearity.manual_override_enabled);
  const linePrecisionCount = precisionSummaries.reduce((count, summary) => count + Object.keys(summary.line_precisions).length, 0);
  const busy = runMutation.isPending || editMutation.isPending || resetCalibrationMutation.isPending || saveLinearityMutation.isPending;
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
    !isOfficialValuesModalOpen &&
    hoverPreviewPosition != null;
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
        chartKey: "VPDB(13C)",
        figure: withColorScaleRange(displayedWorkspace.figures["VPDB(13C)"]),
      },
      "VSMOW(18O)": {
        title: "d18O Calibration",
        description: "Source chart for current selection.",
        chartKey: "VSMOW(18O)",
        figure: withColorScaleRange(displayedWorkspace.figures["VSMOW(18O)"]),
      },
      calibration_3d: {
        title: "Calibration 3D Chart",
        description: "Source chart for current selection.",
        chartKey: "calibration_3d",
        figure: withColorScaleRange(displayedWorkspace.figures.calibration_3d),
      },
      crossplot: {
        title: "Calibration Crossplot",
        description: "Source chart for current selection.",
        chartKey: "crossplot",
        figure: withColorScaleRange(displayedWorkspace.figures.crossplot),
      },
      "linearity|d13_raw": {
        title: "Linearity d13C Raw",
        description: "Source chart for current selection.",
        chartKey: "linearity|d13_raw",
        figure: withColorScaleRange(displayedWorkspace.linearity_figures.d13_raw),
      },
      "linearity|d13_corrected": {
        title: "Linearity d13C Corrected",
        description: "Source chart for current selection.",
        chartKey: "linearity|d13_corrected",
        figure: withColorScaleRange(displayedWorkspace.linearity_figures.d13_corrected),
      },
      "linearity|d18_raw": {
        title: "Linearity d18O Raw",
        description: "Source chart for current selection.",
        chartKey: "linearity|d18_raw",
        figure: withColorScaleRange(displayedWorkspace.linearity_figures.d18_raw),
      },
      "linearity|d18_corrected": {
        title: "Linearity d18O Corrected",
        description: "Source chart for current selection.",
        chartKey: "linearity|d18_corrected",
        figure: withColorScaleRange(displayedWorkspace.linearity_figures.d18_corrected),
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
    const figure = standardSuffix === "d13C" ? withColorScaleRange(section.d13_figure) : withColorScaleRange(section.d18_figure);
    return {
      title: `${standardName} ${standardSuffix} Outlier Trace`,
      description: "Source chart for current selection.",
      chartKey,
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
            <span className="rounded-full bg-stone-50 px-3 py-1.5 ring-1 ring-stone-200">
              Linearity basis: {getLinearityIntensityOptionLabel(lineIntensityBasis)}
            </span>
            <span className="rounded-full bg-stone-50 px-3 py-1.5 ring-1 ring-stone-200">
              {previewQuery.isFetching ? "Refreshing preview..." : hasUnsavedPreview ? "Preview mode" : "Saved config"}
            </span>
          </div>
        </CardHeader>
      </Card>

      {isOfficialValuesModalOpen ? (
        <div
          className="fixed inset-0 z-40 flex items-start justify-center bg-stone-950/40 p-3 pt-8 sm:p-6"
          onClick={() => {
            setOfficialValuesModalOpen(false);
            setOfficialValuesEditMode(false);
            setOfficialValuesError(null);
          }}
        >
          <div
            className="w-full max-w-5xl rounded-xl border border-stone-300 bg-white shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-stone-200 px-4 py-3">
              <div>
                <div className="text-base font-semibold text-stone-900">Official Standard Values</div>
                <div className="text-sm text-stone-500">Values from the standards database used by calibration calculations.</div>
              </div>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setOfficialValuesEditMode((current) => !current);
                    setOfficialValuesError(null);
                  }}
                >
                  {isOfficialValuesEditMode ? "Done" : "Edit"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setOfficialValuesModalOpen(false);
                    setOfficialValuesEditMode(false);
                    setOfficialValuesError(null);
                  }}
                >
                  Close
                </Button>
              </div>
            </div>
            <div className="space-y-4 p-4">
              {officialValuesLoading ? (
                <div className="text-sm text-stone-500">Loading official values...</div>
              ) : officialValuesQueryError ? (
                <div className="text-sm text-red-600">Failed to load official values: {officialValuesQueryError}</div>
              ) : allOfficialValueRows.length ? (
                <>
                  <div className="overflow-x-auto rounded-lg border border-stone-200">
                    <table className="min-w-full border-collapse text-sm">
                      <thead className="bg-stone-50 text-left text-xs uppercase tracking-wide text-stone-500">
                        <tr>
                          <th className="px-3 py-2.5 font-semibold">Standard</th>
                          <th className="px-3 py-2.5 font-semibold">d13C ({OFFICIAL_VALUE_TYPE_D13})</th>
                          <th className="px-3 py-2.5 font-semibold">d18O ({OFFICIAL_VALUE_TYPE_D18})</th>
                          <th className="px-3 py-2.5 font-semibold">Source</th>
                          {isOfficialValuesEditMode ? <th className="px-3 py-2.5 font-semibold">Actions</th> : null}
                        </tr>
                      </thead>
                      <tbody>
                        {allOfficialValueRows.map((row, index) => {
                          const draft = officialValuesDraftRows[row.standard] ?? { d13: "", d18: "" };
                          return (
                          <tr key={`${row.standard}:${index}`} className={index % 2 ? "bg-stone-50/60" : "bg-white"}>
                            <td className="px-3 py-2.5 font-semibold text-stone-800">
                              <div className="flex items-center gap-2">
                                <span>{row.standard}</span>
                                {selectedStandardsSet.has(row.standard) ? (
                                  <span className="rounded-full bg-stone-900 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white">
                                    selected
                                  </span>
                                ) : null}
                              </div>
                            </td>
                            <td className="px-3 py-2.5 text-stone-700">
                              {isOfficialValuesEditMode ? (
                                <input
                                  type="number"
                                  step="0.001"
                                  value={draft.d13}
                                  onChange={(event) => updateOfficialValueDraft(row.standard, "d13", event.target.value)}
                                  className="w-full rounded-md border border-stone-300 px-2 py-1"
                                />
                              ) : (
                                formatOfficialValue(row.d13Value)
                              )}
                            </td>
                            <td className="px-3 py-2.5 text-stone-700">
                              {isOfficialValuesEditMode ? (
                                <input
                                  type="number"
                                  step="0.001"
                                  value={draft.d18}
                                  onChange={(event) => updateOfficialValueDraft(row.standard, "d18", event.target.value)}
                                  className="w-full rounded-md border border-stone-300 px-2 py-1"
                                />
                              ) : (
                                formatOfficialValue(row.d18Value)
                              )}
                            </td>
                            <td className="px-3 py-2.5 text-stone-700">{row.source ?? "database"}</td>
                            {isOfficialValuesEditMode ? (
                              <td className="px-3 py-2.5 text-stone-700">
                                <div className="flex gap-2">
                                  <Button size="sm" variant="outline" disabled={standardsValuesBusy} onClick={() => saveOfficialValuesRow(row.standard)}>
                                    Save
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    disabled={standardsValuesBusy}
                                    onClick={() => removeOfficialValuesItem(row.standard)}
                                  >
                                    Remove
                                  </Button>
                                </div>
                              </td>
                            ) : null}
                          </tr>
                        );
                        })}
                      </tbody>
                    </table>
                  </div>
                  {hasMissingOfficialValues ? (
                    <div className="text-xs text-red-600">
                      One or more selected standards are missing official values in the database. Calibration may fail until these values are added.
                    </div>
                  ) : null}
                </>
              ) : (
                <div className="rounded-lg border border-dashed border-stone-300 p-4 text-sm text-stone-600">
                  No official standard values found in the database yet.
                </div>
              )}
              {officialValuesError ? <div className="text-sm text-red-600">{officialValuesError}</div> : null}

              {isOfficialValuesEditMode ? (
                <div className="space-y-3 rounded-xl border border-stone-200 bg-stone-50/60 p-3">
                  <div className="text-sm font-semibold text-stone-800">Add Standard</div>
                  <div className="grid gap-3 md:grid-cols-4">
                    <label className="form-field">
                      <span className="form-label">Standard</span>
                      <input
                        type="text"
                        value={newStandardName}
                        onChange={(event) => setNewStandardName(event.target.value.toUpperCase())}
                        className="form-control"
                        placeholder="NBS18"
                      />
                    </label>
                    <label className="form-field">
                      <span className="form-label">d13C</span>
                      <input
                        type="number"
                        step="0.001"
                        value={newStandardD13}
                        onChange={(event) => setNewStandardD13(event.target.value)}
                        className="form-control"
                        placeholder="-5.010"
                      />
                    </label>
                    <label className="form-field">
                      <span className="form-label">d18O</span>
                      <input
                        type="number"
                        step="0.001"
                        value={newStandardD18}
                        onChange={(event) => setNewStandardD18(event.target.value)}
                        className="form-control"
                        placeholder="-23.010"
                      />
                    </label>
                    <div className="flex items-end">
                      <Button onClick={addOfficialValuesItem} disabled={standardsValuesBusy} className="w-full">
                        Add
                      </Button>
                    </div>
                  </div>
                </div>
              ) : null}
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
              {selectionSourceChart?.figure ? (
                <Card className="border-stone-300">
                  <CardHeader>
                    <CardTitle className="text-base">Selection Source Chart</CardTitle>
                    <CardDescription>
                      {selectionSourceChart.title} {selectionSourceChart.description}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <PlotlyChart
                      figure={selectionSourceChart.figure}
                      className="h-[360px] w-full"
                      {...chartHoverProps(selectionSourceChart.chartKey ?? activeTarget?.chartKey ?? "")}
                      onPointClick={(points) =>
                        openProcessingSelectionEditor(selectionSourceChart.chartKey ?? activeTarget?.chartKey ?? "", points, false)
                      }
                      onSelection={(points) =>
                        openProcessingSelectionEditor(selectionSourceChart.chartKey ?? activeTarget?.chartKey ?? "", points, true)
                      }
                    />
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
                        title={`${activeIsotopeTarget.isotopeKey} cycle diagnostics`}
                        diagnostics={singleDiagnosticsQuery.data}
                        loading={singleDiagnosticsQuery.isLoading}
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
                            {asNumber((crossD13DiagnosticsQuery.data?.cycle_mean ?? {})["valid_mean"]) == null
                              ? "N/A"
                              : formatDeltaValue(asNumber((crossD13DiagnosticsQuery.data?.cycle_mean ?? {})["valid_mean"]))}
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
                            {asNumber((crossD18DiagnosticsQuery.data?.cycle_mean ?? {})["valid_mean"]) == null
                              ? "N/A"
                              : formatDeltaValue(asNumber((crossD18DiagnosticsQuery.data?.cycle_mean ?? {})["valid_mean"]))}
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
        <aside className="space-y-6 xl:sticky xl:top-6 xl:max-h-[calc(100vh-2rem)] xl:self-start xl:overflow-y-auto xl:pr-1">
          <Card>
            <CardHeader>
              <CardTitle>Calibration Controls</CardTitle>
              <CardDescription>Configure standards, visualization, linearity, outlier detection, and precision date range settings.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <MultiSelectDropdown
                label="Selected standards"
                options={displayedWorkspace.available_values.standards}
                selected={activeConfig.selected_standards}
                onChange={(next) => updateConfig("selected_standards", next)}
                placeholder="Select standards"
              />

              <label className="form-field">
                <span className="form-label">Carbonate material</span>
                <select
                  value={activeConfig.carbonate_material ?? "calcite"}
                  onChange={(event) =>
                    updateConfig(
                      "carbonate_material",
                      event.target.value as CalibrationConfig["carbonate_material"],
                    )
                  }
                  className="form-control"
                >
                  {CARBONATE_MATERIAL_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              <div className="grid gap-4">
                <label className="form-field">
                  <span className="form-label">Color parameter</span>
                  <select
                    value={activeConfig.color_param}
                    onChange={(event) => updateConfig("color_param", event.target.value)}
                    className="form-control"
                  >
                    {displayedWorkspace.available_values.color_params
                      .filter((option) => option !== "Date_ordinal")
                      .map((option) => (
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
                <div className="form-field">
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

              <div className="space-y-4 rounded-xl border border-stone-200 bg-white/80 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-sm font-medium text-stone-800">Linearity (shared with processing)</div>
                  <span className="rounded-full bg-stone-100 px-2 py-1 text-xs text-stone-600">Basis: {selectedLinearityBasisLabel}</span>
                </div>
                <CheckboxField
                  checked={activeConfig.linearity.apply}
                  label="Enable linearity correction"
                  description="Uses the same basis, fits, and offsets as Processing."
                  onChange={(checked) => updateLinearity("apply", checked)}
                />
                <CheckboxField
                  checked={Boolean(activeConfig.linearity.quadratic)}
                  label="Use quadratic linearity relationship"
                  description="Fits and applies y = a + b*I + c*I^2 instead of y = a + b*I."
                  onChange={(checked) => updateLinearity("quadratic", checked)}
                />
                <label className="text-sm">
                  <span className="mb-1 block text-stone-700">Linearity basis</span>
                  <select
                    value={selectedLinearityIntensityCol}
                    onChange={(event) => updateLinearityIntensityCol(event.target.value)}
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
                      min={0}
                      step="0.1"
                      value={activeConfig.linearity.max_sample_intensity ?? ""}
                      onChange={(event) => {
                        const rawValue = event.target.value.trim();
                        if (rawValue === "") {
                          updateLinearity("max_sample_intensity", null);
                          return;
                        }
                        const parsed = Number(rawValue);
                        updateLinearity("max_sample_intensity", Number.isFinite(parsed) ? parsed : null);
                      }}
                      className="w-full rounded-lg border border-stone-300 px-3 py-2"
                    />
                  </label>
                ) : null}
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-lg border border-stone-200 p-3 text-sm">
                    <div className="text-xs uppercase tracking-wide text-stone-500">d13C fitted coefficient</div>
                    <div className="mt-1 font-semibold text-stone-900">
                      {activeConfig.linearity.quadratic
                        ? `${formatDeltaValue(d13FitSlope, 6)} (linear), ${formatDeltaValue(d13FitQuad, 8)} (quadratic)`
                        : formatDeltaValue(d13FitSlope, 6)}
                    </div>
                  </div>
                  <div className="rounded-lg border border-stone-200 p-3 text-sm">
                    <div className="text-xs uppercase tracking-wide text-stone-500">d18O fitted coefficient</div>
                    <div className="mt-1 font-semibold text-stone-900">
                      {activeConfig.linearity.quadratic
                        ? `${formatDeltaValue(d18FitSlope, 6)} (linear), ${formatDeltaValue(d18FitQuad, 8)} (quadratic)`
                        : formatDeltaValue(d18FitSlope, 6)}
                    </div>
                  </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="text-sm">
                    <span className="mb-1 block text-stone-700">
                      {getLinearityCoefficientLabel("d13C", selectedLinearityIntensityCol, Boolean(activeConfig.linearity.quadratic))}
                    </span>
                    <input
                      type="number"
                      step="0.01"
                      value={activeConfig.linearity.quadratic ? (activeConfig.linearity.manual_d13_per_10v2 ?? 0) : (activeConfig.linearity.manual_d13_per_10v ?? 0)}
                      onChange={(event) => updateLinearityCoefficientOffset("d13C", Number(event.target.value))}
                      className="w-full rounded-lg border border-stone-300 px-3 py-2"
                    />
                  </label>
                  <label className="text-sm">
                    <span className="mb-1 block text-stone-700">
                      {getLinearityCoefficientLabel("d18O", selectedLinearityIntensityCol, Boolean(activeConfig.linearity.quadratic))}
                    </span>
                    <input
                      type="number"
                      step="0.01"
                      value={activeConfig.linearity.quadratic ? (activeConfig.linearity.manual_d18_per_10v2 ?? 0) : (activeConfig.linearity.manual_d18_per_10v ?? 0)}
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
                {activeConfig.linearity.apply ? (
                  <div className="space-y-2 rounded-lg border border-stone-200 bg-stone-50 p-3">
                    <div className="text-sm font-medium text-stone-800">Linearity-corrected standard precision</div>
                    {standardPrecisionRows.length ? (
                      standardPrecisionRows.map((summary: CalibrationPrecisionSummary) => (
                        <div key={summary.standard} className="grid grid-cols-[1fr_auto_auto] items-center gap-3 text-xs text-stone-700">
                          <span className="font-medium text-stone-800">{summary.standard}</span>
                          <span>d13C: {formatMetricWithUnit(summary.d13_linearity_corrected_precision)}</span>
                          <span>d18O: {formatMetricWithUnit(summary.d18_linearity_corrected_precision)}</span>
                        </div>
                      ))
                    ) : (
                      <div className="text-xs text-stone-500">No selected standards available for precision.</div>
                    )}
                  </div>
                ) : null}
              </div>

              <div className="space-y-4 rounded-xl border border-stone-200 bg-white/80 p-4">
                <div className="form-section-title">Outlier detection</div>
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

              <div className="flex items-center justify-between rounded-xl border border-stone-200 bg-white/80 p-3">
                <div className="pr-3">
                  <div className="text-sm font-semibold tracking-[0.01em] text-stone-800">Official standard values</div>
                  <div className="text-xs text-stone-500">Open database values used in calibration equations.</div>
                </div>
                <Button variant="outline" size="sm" onClick={() => setOfficialValuesModalOpen(true)}>
                  View
                </Button>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button
                  onClick={() => runMutation.mutate(activeConfig)}
                  disabled={runMutation.isPending || selectedStandards.length < 1 || selectedStandards.length > 2}
                >
                  {runMutation.isPending ? "Running..." : "Calibrate results"}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => resetCalibrationMutation.mutate()}
                  disabled={runMutation.isPending || resetCalibrationMutation.isPending}
                >
                  {resetCalibrationMutation.isPending ? "Resetting..." : "Reset calibration"}
                </Button>
              </div>
              <div className="text-xs text-stone-500">Select exactly one or two standards to run calibration.</div>
              {previewError ? <div className="text-xs text-red-600">Preview error: {previewError}</div> : null}
              {runError ? <div className="text-xs text-red-600">Calibration error: {runError}</div> : null}
              {resetError ? <div className="text-xs text-red-600">Reset error: {resetError}</div> : null}
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
                  <PrecisionCard key={summary.standard} summary={summary} linearityEnabled={Boolean(activeConfig.linearity.apply)} />
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
                      figure={withColorScaleRange(displayedWorkspace.figures["VPDB(13C)"])}
                      className="aspect-square w-full"
                      {...chartHoverProps("VPDB(13C)")}
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
                      figure={withColorScaleRange(displayedWorkspace.figures["VSMOW(18O)"])}
                      className="aspect-square w-full"
                      {...chartHoverProps("VSMOW(18O)")}
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
                      figure={withColorScaleRange(displayedWorkspace.figures.calibration_3d)}
                      className="min-h-[520px] xl:aspect-square"
                      {...chartHoverProps("calibration_3d")}
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
                      figure={withColorScaleRange(displayedWorkspace.figures.crossplot)}
                      className="min-h-[520px] xl:aspect-square"
                      {...chartHoverProps("crossplot")}
                      onPointClick={(points) => openProcessingSelectionEditor("crossplot", points, false)}
                      onSelection={(points) => openProcessingSelectionEditor("crossplot", points, true)}
                    />
                  </CardContent>
                </Card>
              </div>

              <Card>
                <CardHeader className="gap-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <CardTitle>Linearity Correction</CardTitle>
                    <span className="rounded-full bg-stone-50 px-3 py-1.5 text-sm text-stone-700 ring-1 ring-stone-200">
                      Basis: {selectedLinearityBasisLabel}
                    </span>
                  </div>
                  <CardDescription>
                    Standards-only linearity fits built from the active precision date window and the selected basis used during calibration.
                  </CardDescription>
                </CardHeader>
                <CardContent className="grid gap-6 2xl:grid-cols-2">
                  <PlotlyChart
                    figure={withColorScaleRange(displayedWorkspace.linearity_figures.d13_raw)}
                    className="min-h-[440px]"
                    {...chartHoverProps("linearity|d13_raw")}
                    onPointClick={(points) => openProcessingSelectionEditor("linearity|d13_raw", points, false)}
                    onSelection={(points) => openProcessingSelectionEditor("linearity|d13_raw", points, true)}
                  />
                  <PlotlyChart
                    figure={withColorScaleRange(displayedWorkspace.linearity_figures.d13_corrected)}
                    className="min-h-[440px]"
                    {...chartHoverProps("linearity|d13_corrected")}
                    onPointClick={(points) => openProcessingSelectionEditor("linearity|d13_corrected", points, false)}
                    onSelection={(points) => openProcessingSelectionEditor("linearity|d13_corrected", points, true)}
                  />
                  <PlotlyChart
                    figure={withColorScaleRange(displayedWorkspace.linearity_figures.d18_raw)}
                    className="min-h-[440px]"
                    {...chartHoverProps("linearity|d18_raw")}
                    onPointClick={(points) => openProcessingSelectionEditor("linearity|d18_raw", points, false)}
                    onSelection={(points) => openProcessingSelectionEditor("linearity|d18_raw", points, true)}
                  />
                  <PlotlyChart
                    figure={withColorScaleRange(displayedWorkspace.linearity_figures.d18_corrected)}
                    className="min-h-[440px]"
                    {...chartHoverProps("linearity|d18_corrected")}
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
                              figure={withColorScaleRange(section.d13_figure)}
                              className="min-h-[340px]"
                              {...chartHoverProps(`${section.standard}|d13C`)}
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
                              figure={withColorScaleRange(section.d18_figure)}
                              className="min-h-[340px]"
                              {...chartHoverProps(`${section.standard}|d18O`)}
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
      {shouldShowHoverPreview && hoverPreview && hoverPreviewPosition ? (
        <div
          className="pointer-events-none fixed z-[80] w-[560px] rounded-xl border border-stone-300 bg-white/95 p-3 shadow-2xl backdrop-blur-[1px]"
          style={{ left: `${hoverPreviewPosition.left}px`, top: `${hoverPreviewPosition.top}px` }}
        >
          <div className="mb-2 flex items-center justify-between gap-2 text-xs text-stone-600">
            <span className="font-medium text-stone-800">
              {hoverPreview.target.identifier1 || "Sample"} | {hoverPreview.target.identifier2 || "N/A"}
            </span>
            <span className="rounded-full bg-stone-100 px-2 py-0.5 font-medium uppercase tracking-wide text-stone-700">
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
