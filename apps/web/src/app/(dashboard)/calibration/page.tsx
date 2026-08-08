"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ChevronUp, Database, GripVertical, X } from "lucide-react";
import Link from "next/link";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { PlotlyChart, type PlotlyHoverPayload, type PlotlyPoint } from "@/components/charts/lazy-plotly-chart";
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
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DecimalInput } from "@/components/ui/decimal-input";
import { DualRangeField } from "@/components/ui/dual-range-field";
import { MultiSelectDropdown } from "@/components/ui/multi-select-dropdown";
import { PageHeader } from "@/components/ui/page-header";
import { Tooltip } from "@/components/ui/tooltip";
import { api, type JobSnapshot } from "@/lib/api";
import type {
  CalibrationConfig,
  CalibrationOfficialValue,
  CalibrationPrecisionSummary,
  CalibrationWorkspace,
  CycleDiagnosticsPayload,
  EditAction,
  ProcessingLinearityPreviewData,
  SessionSnapshot,
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
type IsotopeKey = "d13C" | "d18O";
const ISOTOPE_KEYS: IsotopeKey[] = ["d13C", "d18O"];
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
type CalibrationPreviewRowState = {
  rowLabel: string;
  identifier1: string;
  identifier2: string;
  species: string;
  line: number | null;
  d13: number | null;
  d18: number | null;
  attributes: Record<string, unknown>;
  intensities: Record<string, number | null>;
};
type CalibrationPreviewColorState = {
  param: string;
  valuesByRow: Map<string, number>;
  tickvals?: number[];
  ticktext?: string[];
};
type CalibrationPreviewMasks = {
  rowsByLabel: Map<string, CalibrationPreviewRowState>;
  selectedRows: Set<string>;
  baseD13: Set<string>;
  baseD18: Set<string>;
  baseCross: Set<string>;
  outlierD13: Set<string>;
  outlierD18: Set<string>;
  outlierCombined: Set<string>;
  color: CalibrationPreviewColorState | null;
};
type LinearityOffsetField = "line_1_offset_d13" | "line_1_offset_d18" | "line_2_offset_d13" | "line_2_offset_d18";
type LinearityOffsetDraftState = Record<LinearityOffsetField, string>;
type LinearityCycleIntensityAggregation = "run_median" | "first_valid_cycle" | "last_valid_cycle";

const PRECISION_PASS_THRESHOLD = 0.07;
const OFFICIAL_VALUE_TYPE_D13 = "VPDB(13C)";
const OFFICIAL_VALUE_TYPE_D18 = "VSMOW(18O)";
const OFFICIAL_VALUES_ORDER_STORAGE_KEY = "irms-official-standard-values-order";
const SELECTION_EDITOR_DEFAULT_OFFSET = 0.1;
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

function formatOfficialValue(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(3)} permil` : "Not set";
}

type OfficialValuesRow = {
  standard: string;
  d13Value: number | null;
  d18Value: number | null;
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
      };
    const value = typeof item.value === "number" && Number.isFinite(item.value) ? item.value : null;
    if (isotopicType === OFFICIAL_VALUE_TYPE_D13) {
      current.d13Value = value;
    } else if (isotopicType === OFFICIAL_VALUE_TYPE_D18) {
      current.d18Value = value;
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

function isSignalIntensityColumnLabel(label: string): boolean {
  const normalized = label.trim().toLowerCase();
  return normalized.includes("int m/z") && normalized.includes("(v)");
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

function normalizeStandardKey(value: unknown): string {
  return String(value ?? "").trim().toUpperCase();
}

function finiteNumber(value: unknown): number | null {
  return toFiniteNumber(value);
}

function lineOffsetForPreview(linearity: CalibrationConfig["linearity"], isotopeKey: IsotopeKey, line: number | null | undefined): number {
  if (line !== 1 && line !== 2) {
    return 0;
  }
  if (isotopeKey === "d13C") {
    return line === 1 ? finiteNumber(linearity.line_1_offset_d13) ?? 0 : finiteNumber(linearity.line_2_offset_d13) ?? 0;
  }
  return line === 1 ? finiteNumber(linearity.line_1_offset_d18) ?? 0 : finiteNumber(linearity.line_2_offset_d18) ?? 0;
}

function linearityPrimaryOffsetScale(intensityCol: string | null | undefined): number {
  return intensityCol === LINEARITY_INTENSITY_SYMMETRIC_MISMATCH44 || intensityCol === LINEARITY_INTENSITY_RELATIVE_MISMATCH44 ? 1 : 10;
}

function linearitySecondaryOffsetScale(intensityCol: string | null | undefined): number {
  if (
    intensityCol === LINEARITY_INTENSITY_SYMMETRIC_MISMATCH44 ||
    intensityCol === LINEARITY_INTENSITY_RELATIVE_MISMATCH44 ||
    intensityCol === LINEARITY_INTENSITY_TWO_TERM44
  ) {
    return 1;
  }
  return 100;
}

function applyManualLinearityOffsetsForPreview(
  fits: Record<string, unknown> | undefined,
  linearity: CalibrationConfig["linearity"],
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
  const configByIsotope: Record<IsotopeKey, { linear: number; quadratic: number }> = {
    d13C: {
      linear: finiteNumber(linearity.manual_d13_per_10v) ?? 0,
      quadratic: finiteNumber(linearity.manual_d13_per_10v2) ?? 0,
    },
    d18O: {
      linear: finiteNumber(linearity.manual_d18_per_10v) ?? 0,
      quadratic: finiteNumber(linearity.manual_d18_per_10v2) ?? 0,
    },
  };
  for (const isotopeKey of ISOTOPE_KEYS) {
    const fit = { ...((adjusted[isotopeKey] as Record<string, unknown> | undefined) ?? {}) };
    const xRef = finiteNumber(fit.x_ref) ?? 0;
    const baseIntercept = finiteNumber(fit.intercept);
    let interceptShift = 0;
    if (String(fit.model ?? "") === "two_term") {
      const slopeOffsetRaw = configByIsotope[isotopeKey].linear;
      const secondaryOffsetRaw = configByIsotope[isotopeKey].quadratic;
      if (Number.isFinite(slopeOffsetRaw) && Math.abs(slopeOffsetRaw) > 1e-15) {
        const slopeOffset = slopeOffsetRaw / linearityPrimaryOffsetScale(LINEARITY_INTENSITY_TWO_TERM44);
        fit.slope = (finiteNumber(fit.slope) ?? 0) + slopeOffset;
        interceptShift += slopeOffset * xRef;
      }
      if (Number.isFinite(secondaryOffsetRaw) && Math.abs(secondaryOffsetRaw) > 1e-15) {
        const secondaryOffset = secondaryOffsetRaw / linearitySecondaryOffsetScale(LINEARITY_INTENSITY_TWO_TERM44);
        fit.quad = (finiteNumber(fit.quad) ?? 0) + secondaryOffset;
        const secondaryRef = finiteNumber(fit.secondary_x_ref);
        if (secondaryRef != null) {
          interceptShift += secondaryOffset * secondaryRef;
        }
      }
      if (baseIntercept != null && Math.abs(interceptShift) > 1e-15) {
        fit.intercept = baseIntercept - interceptShift;
      }
      adjusted[isotopeKey] = fit;
      continue;
    }

    const slopeOffsetRaw = configByIsotope[isotopeKey].linear;
    if (Number.isFinite(slopeOffsetRaw) && Math.abs(slopeOffsetRaw) > 1e-15) {
      const slopeOffset = slopeOffsetRaw / linearityPrimaryOffsetScale(basisCol);
      fit.slope = (finiteNumber(fit.slope) ?? 0) + slopeOffset;
      if (baseIntercept != null) {
        fit.intercept = baseIntercept - slopeOffset * xRef;
      }
    }
    if (linearity.quadratic) {
      const quadOffsetRaw = configByIsotope[isotopeKey].quadratic;
      if (Number.isFinite(quadOffsetRaw) && Math.abs(quadOffsetRaw) > 1e-15) {
        const quadOffset = quadOffsetRaw / linearitySecondaryOffsetScale(basisCol);
        fit.quad = (finiteNumber(fit.quad) ?? 0) + quadOffset;
        const currentIntercept = finiteNumber(fit.intercept);
        if (currentIntercept != null) {
          fit.intercept = currentIntercept - quadOffset * xRef ** 2;
        }
        fit.degree = Math.max(finiteNumber(fit.degree) ?? 1, 2);
      }
    }
    adjusted[isotopeKey] = fit;
  }
  return adjusted;
}

function linearityFitDegree(fit: Record<string, unknown>): number {
  const degree = finiteNumber(fit.degree);
  if (degree != null && degree >= 2) {
    return 2;
  }
  if (fit.quadratic === true) {
    return 2;
  }
  const quad = finiteNumber(fit.quad);
  return quad != null && Math.abs(quad) > 1e-15 ? 2 : 1;
}

function linearityCorrectionDeltaForPreview(
  fit: Record<string, unknown>,
  intensity: number | null,
  secondaryIntensity?: number | null,
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
    const delta = slope * (intensity - xRef) + secondarySlope * (secondaryIntensity - secondaryRef);
    return Number.isFinite(delta) ? delta : null;
  }
  let delta = slope * (intensity - xRef);
  const quad = finiteNumber(fit.quad);
  if (linearityFitDegree(fit) >= 2 && quad != null) {
    delta += quad * (intensity ** 2 - xRef ** 2);
  }
  return Number.isFinite(delta) ? delta : null;
}

function previewValueForRow(
  row: ProcessingLinearityPreviewData["rows"][number] | undefined,
  isotopeKey: IsotopeKey,
  linearity: CalibrationConfig["linearity"],
  previewData: ProcessingLinearityPreviewData,
  effectiveFits: Record<string, unknown>,
  valueSpace: "raw" | "calibrated",
): number | null {
  if (!row) {
    return null;
  }
  const baseRaw = isotopeKey === "d13C" ? finiteNumber(row.d13_raw) : finiteNumber(row.d18_raw);
  if (baseRaw == null) {
    return null;
  }
  const adjustedRaw = baseRaw + lineOffsetForPreview(linearity, isotopeKey, finiteNumber(row.line));
  let rawValue = adjustedRaw;
  const fit = (effectiveFits[isotopeKey] as Record<string, unknown> | undefined) ?? {};
  if (linearity.apply) {
    const intensityCol =
      String(
        (String(fit.model ?? "") === "two_term"
          ? fit.primary_col
          : effectiveFits[isotopeKey === "d13C" ? "d13_intensity_col" : "d18_intensity_col"]) ??
          previewData.intensity_col ??
          linearity.intensity_col ??
          "",
      ).trim();
    const fallbackIntensityCol = String(previewData.intensity_col ?? linearity.intensity_col ?? "").trim();
    const primaryIntensity = finiteNumber(row.intensities[intensityCol]) ?? finiteNumber(row.intensities[fallbackIntensityCol]);
    const secondaryCol = String(fit.secondary_col ?? LINEARITY_INTENSITY_SYMMETRIC_MISMATCH44);
    const secondaryIntensity = finiteNumber(row.intensities[secondaryCol]);
    const delta = linearityCorrectionDeltaForPreview(fit, primaryIntensity, secondaryIntensity);
    if (delta != null) {
      rawValue = adjustedRaw - delta;
    }
  }
  if (valueSpace === "raw") {
    return Number.isFinite(rawValue) ? rawValue : null;
  }
  const coeff = (previewData.coefficients?.[isotopeKey] as Record<string, unknown> | undefined) ?? {};
  const slope = finiteNumber(coeff.slope);
  const intercept = finiteNumber(coeff.intercept);
  if (slope != null && intercept != null) {
    return slope * rawValue + intercept;
  }
  return isotopeKey === "d13C" ? finiteNumber(row.d13_calibrated) : finiteNumber(row.d18_calibrated);
}

function customDataRowLabel(value: unknown): string {
  if (Array.isArray(value)) {
    return String(value[0] ?? "").trim();
  }
  if (value && typeof value === "object") {
    const payload = value as Record<string, unknown>;
    return String(payload.row_label ?? payload.rowLabel ?? payload[0] ?? "").trim();
  }
  return String(value ?? "").trim();
}

function customDataIsotope(value: unknown): IsotopeKey | "cross" | null {
  if (Array.isArray(value)) {
    return normalizeIsotopeKey(value[1]);
  }
  if (value && typeof value === "object") {
    const payload = value as Record<string, unknown>;
    return normalizeIsotopeKey(payload.isotope_key ?? payload.isotopeKey ?? payload[1]);
  }
  return null;
}

function sortedFinite(values: Array<number | null>): number[] {
  return values.filter((value): value is number => value != null && Number.isFinite(value)).sort((a, b) => a - b);
}

function quantile(values: number[], q: number): number | null {
  if (!values.length) {
    return null;
  }
  if (values.length === 1) {
    return values[0];
  }
  const position = (values.length - 1) * q;
  const lowerIndex = Math.floor(position);
  const upperIndex = Math.ceil(position);
  const lower = values[lowerIndex];
  const upper = values[upperIndex];
  if (lower == null || upper == null) {
    return null;
  }
  return lower + (upper - lower) * (position - lowerIndex);
}

function statisticalOutlierRows(valuesByRow: Array<{ rowLabel: string; value: number | null }>, method: string, sigmaLevel: number, iqrMultiplier: number): Set<string> {
  const finiteValues = sortedFinite(valuesByRow.map((item) => item.value));
  const outliers = new Set<string>();
  if (finiteValues.length <= 1) {
    return outliers;
  }
  if (String(method).trim().toUpperCase() === "IQR") {
    const q1 = quantile(finiteValues, 0.25);
    const q3 = quantile(finiteValues, 0.75);
    if (q1 == null || q3 == null) {
      return outliers;
    }
    const iqr = q3 - q1;
    const lower = q1 - iqrMultiplier * iqr;
    const upper = q3 + iqrMultiplier * iqr;
    for (const item of valuesByRow) {
      if (item.value != null && (item.value < lower || item.value > upper)) {
        outliers.add(item.rowLabel);
      }
    }
    return outliers;
  }
  const mean = finiteValues.reduce((sum, value) => sum + value, 0) / finiteValues.length;
  const variance =
    finiteValues.length > 1
      ? finiteValues.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (finiteValues.length - 1)
      : 0;
  const std = Math.sqrt(variance);
  if (!Number.isFinite(mean) || !Number.isFinite(std) || std <= 0) {
    return outliers;
  }
  const lower = mean - sigmaLevel * std;
  const upper = mean + sigmaLevel * std;
  for (const item of valuesByRow) {
    if (item.value != null && (item.value < lower || item.value > upper)) {
      outliers.add(item.rowLabel);
    }
  }
  return outliers;
}

function localDateTime(value: unknown): number | null {
  if (value == null) {
    return null;
  }
  const text = String(value).trim();
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (match) {
    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    const time = new Date(year, month - 1, day).getTime();
    return Number.isFinite(time) ? time : null;
  }
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? parsed : null;
}

function dateOrdinal(value: unknown): number | null {
  const time = localDateTime(value);
  if (time == null) {
    return null;
  }
  return Math.floor(time / 86_400_000) + 719_163;
}

function rowInPrecisionDateRange(row: CalibrationPreviewRowState, config: CalibrationConfig): boolean {
  const range = config.precision_date_range;
  if (!range || !range[0] || !range[1]) {
    return true;
  }
  const rowTime = localDateTime(row.attributes.Date);
  const startTime = localDateTime(range[0]);
  const endTime = localDateTime(range[1]);
  if (rowTime == null || startTime == null || endTime == null) {
    return true;
  }
  return rowTime >= startTime && rowTime < endTime + 86_400_000;
}

function calibrationPreviewAttribute(row: CalibrationPreviewRowState, param: string): unknown {
  const normalized = param.trim();
  if (normalized === "Identifier 1") {
    return row.identifier1;
  }
  if (normalized === "Identifier 2") {
    return row.identifier2;
  }
  if (normalized === "Species") {
    return row.species;
  }
  if (normalized === "Line") {
    return row.line;
  }
  if (normalized === "d 13C/12C  Mean") {
    return row.d13;
  }
  if (normalized === "d 18O/16O  Mean") {
    return row.d18;
  }
  if (normalized === "leak_rate") {
    return row.attributes.leak_rate ?? row.attributes[normalized];
  }
  return row.attributes[normalized] ?? row.intensities[normalized];
}

function calibrationPreviewNumericAttribute(row: CalibrationPreviewRowState, param: string): number | null {
  const value = calibrationPreviewAttribute(row, param);
  if (param.trim() === "Date") {
    return dateOrdinal(value);
  }
  return finiteNumber(value);
}

function buildCalibrationPreviewColorState(rows: CalibrationPreviewRowState[], colorParam: string): CalibrationPreviewColorState | null {
  const param = String(colorParam ?? "").trim();
  if (!param) {
    return null;
  }
  const numericPairs: Array<{ rowLabel: string; value: number }> = [];
  const rawPairs: Array<{ rowLabel: string; value: string }> = [];
  for (const row of rows) {
    const numeric = calibrationPreviewNumericAttribute(row, param);
    if (numeric != null) {
      numericPairs.push({ rowLabel: row.rowLabel, value: numeric });
    }
    const raw = calibrationPreviewAttribute(row, param);
    if (raw != null && String(raw).trim() !== "") {
      rawPairs.push({ rowLabel: row.rowLabel, value: String(raw).trim() });
    }
  }
  if (numericPairs.length) {
    return {
      param,
      valuesByRow: new Map(numericPairs.map((item) => [item.rowLabel, item.value])),
    };
  }
  if (!rawPairs.length) {
    return null;
  }
  const categories = Array.from(new Set(rawPairs.map((item) => item.value))).sort((left, right) => left.localeCompare(right));
  const codes = new Map(categories.map((value, index) => [value, index]));
  return {
    param,
    valuesByRow: new Map(rawPairs.map((item) => [item.rowLabel, codes.get(item.value) ?? 0])),
    tickvals: categories.map((_, index) => index),
    ticktext: categories,
  };
}

function buildCalibrationPreviewMasks(
  previewData: ProcessingLinearityPreviewData | undefined,
  config: CalibrationConfig | null | undefined,
): CalibrationPreviewMasks | null {
  if (!previewData || !config) {
    return null;
  }
  const selectedStandards = new Set(config.selected_standards.map(normalizeStandardKey).filter(Boolean));
  if (!selectedStandards.size) {
    return {
      rowsByLabel: new Map(),
      selectedRows: new Set(),
      baseD13: new Set(),
      baseD18: new Set(),
      baseCross: new Set(),
      outlierD13: new Set(),
      outlierD18: new Set(),
      outlierCombined: new Set(),
      color: null,
    };
  }
  const effectiveFits = applyManualLinearityOffsetsForPreview(previewData.fits, config.linearity);
  const rowsByLabel = new Map<string, CalibrationPreviewRowState>();
  const selectedRows: CalibrationPreviewRowState[] = [];
  for (const row of previewData.rows) {
    const identifier1 = String(row.identifier1 ?? "").trim();
    if (!selectedStandards.has(normalizeStandardKey(identifier1))) {
      continue;
    }
    const state: CalibrationPreviewRowState = {
      rowLabel: String(row.row_label),
      identifier1,
      identifier2: String(row.identifier2 ?? "").trim(),
      species: String(row.species ?? identifier1).trim(),
      line: finiteNumber(row.line),
      d13: previewValueForRow(row, "d13C", config.linearity, previewData, effectiveFits, "raw"),
      d18: previewValueForRow(row, "d18O", config.linearity, previewData, effectiveFits, "raw"),
      attributes: (row.attributes ?? {}) as Record<string, unknown>,
      intensities: row.intensities ?? {},
    };
    if (!rowInPrecisionDateRange(state, config)) {
      continue;
    }
    rowsByLabel.set(state.rowLabel, state);
    selectedRows.push(state);
  }

  const masks: CalibrationPreviewMasks = {
    rowsByLabel,
    selectedRows: new Set(selectedRows.map((row) => row.rowLabel)),
    baseD13: new Set(),
    baseD18: new Set(),
    baseCross: new Set(),
    outlierD13: new Set(),
    outlierD18: new Set(),
    outlierCombined: new Set(),
    color: buildCalibrationPreviewColorState(selectedRows, config.color_param),
  };

  const rowsByStandard = new Map<string, CalibrationPreviewRowState[]>();
  for (const row of selectedRows) {
    const key = normalizeStandardKey(row.identifier1);
    const groupRows = rowsByStandard.get(key) ?? [];
    groupRows.push(row);
    rowsByStandard.set(key, groupRows);
  }
  const sigmaLevel = finiteNumber(config.sigma_level) ?? 3;
  const iqrMultiplier = finiteNumber(config.iqr_multiplier) ?? 1.5;
  for (const groupRows of rowsByStandard.values()) {
    const d13Outliers = statisticalOutlierRows(
      groupRows.map((row) => ({ rowLabel: row.rowLabel, value: row.d13 })),
      config.calibration_type,
      sigmaLevel,
      iqrMultiplier,
    );
    const d18Outliers = statisticalOutlierRows(
      groupRows.map((row) => ({ rowLabel: row.rowLabel, value: row.d18 })),
      config.calibration_type,
      sigmaLevel,
      iqrMultiplier,
    );
    for (const row of groupRows) {
      const d13Outlier = d13Outliers.has(row.rowLabel);
      const d18Outlier = d18Outliers.has(row.rowLabel);
      if (d13Outlier) {
        masks.outlierD13.add(row.rowLabel);
      }
      if (d18Outlier) {
        masks.outlierD18.add(row.rowLabel);
      }
      if (d13Outlier || d18Outlier) {
        masks.outlierCombined.add(row.rowLabel);
      }
    }
  }
  for (const row of selectedRows) {
    const combinedOutlier = masks.outlierCombined.has(row.rowLabel);
    const d13Excluded = config.independent_isotope_outliers ? masks.outlierD13.has(row.rowLabel) : combinedOutlier;
    const d18Excluded = config.independent_isotope_outliers ? masks.outlierD18.has(row.rowLabel) : combinedOutlier;
    if (!d13Excluded) {
      masks.baseD13.add(row.rowLabel);
    }
    if (!d18Excluded) {
      masks.baseD18.add(row.rowLabel);
    }
    if (!combinedOutlier) {
      masks.baseCross.add(row.rowLabel);
    }
  }
  return masks;
}

function filterTraceVector(vector: unknown, keepIndexes: number[], sourceLength: number): unknown {
  const values = coerceVector(vector);
  if (!values || values.length !== sourceLength) {
    return vector;
  }
  return keepIndexes.map((index) => values[index]);
}

function filterTraceNestedVectors(record: Record<string, unknown> | undefined, keepIndexes: number[], sourceLength: number): Record<string, unknown> | undefined {
  if (!record) {
    return record;
  }
  let changed = false;
  const next: Record<string, unknown> = { ...record };
  for (const key of ["color", "size", "symbol", "text", "opacity"]) {
    if (!(key in next)) {
      continue;
    }
    const filtered = filterTraceVector(next[key], keepIndexes, sourceLength);
    if (filtered !== next[key]) {
      next[key] = filtered;
      changed = true;
    }
  }
  return changed ? next : record;
}

function isotopeFromChartKey(chartKey?: string): IsotopeKey | "cross" | null {
  if (!chartKey) {
    return null;
  }
  if (chartKey === "calibration_3d" || chartKey === "crossplot") {
    return "cross";
  }
  if (chartKey === "VPDB(13C)" || chartKey.includes("d13")) {
    return "d13C";
  }
  if (chartKey === "VSMOW(18O)" || chartKey.includes("d18")) {
    return "d18O";
  }
  return null;
}

function rowsForStandard(rowSet: Set<string>, standard: string | null, masks: CalibrationPreviewMasks): Set<string> {
  if (!standard) {
    return rowSet;
  }
  const standardKey = normalizeStandardKey(standard);
  return new Set(Array.from(rowSet).filter((rowLabel) => normalizeStandardKey(masks.rowsByLabel.get(rowLabel)?.identifier1) === standardKey));
}

function calibrationTraceRowSet(
  trace: Record<string, unknown>,
  chartKey: string | undefined,
  masks: CalibrationPreviewMasks,
  config: CalibrationConfig,
): Set<string> {
  const traceName = String(trace.name ?? "");
  const chartIsotope = isotopeFromChartKey(chartKey);
  const sectionMatch = chartKey?.match(/^(.*)\|(d13C|d18O)$/);
  const sectionStandard = sectionMatch?.[1] ?? null;
  if (sectionMatch) {
    const isotope = sectionMatch[2] as IsotopeKey;
    if (traceName.includes("Outliers")) {
      return rowsForStandard(isotope === "d13C" ? masks.outlierD13 : masks.outlierD18, sectionStandard, masks);
    }
    if (traceName.includes("Included")) {
      const excluded = isotope === "d13C" ? masks.outlierD13 : masks.outlierD18;
      return rowsForStandard(new Set(Array.from(masks.selectedRows).filter((rowLabel) => !excluded.has(rowLabel))), sectionStandard, masks);
    }
    return rowsForStandard(masks.selectedRows, sectionStandard, masks);
  }
  const customdata = coerceVector(trace.customdata);
  const firstIsotope = customdata?.length ? customDataIsotope(customdata[0]) : null;
  const isotope = firstIsotope ?? chartIsotope;
  if (traceName.includes("Outliers")) {
    if (isotope === "d13C") {
      return masks.outlierD13;
    }
    if (isotope === "d18O") {
      return masks.outlierD18;
    }
    return masks.outlierCombined;
  }
  if (isotope === "d13C") {
    return masks.baseD13;
  }
  if (isotope === "d18O") {
    return masks.baseD18;
  }
  return masks.baseCross;
}

function patchedCalibrationAxisValue(
  row: CalibrationPreviewRowState | undefined,
  axis: "x" | "y" | "z",
  chartKey: string | undefined,
  config: CalibrationConfig,
): number | null {
  if (!row) {
    return null;
  }
  // Linearity figures use residuals (and, for corrected figures, corrected
  // residuals) supplied by the same backend fit that draws their regression
  // line. Replacing those values with the raw isotope means separates the
  // samples from the fitted line.
  if (chartKey?.startsWith("linearity|")) {
    return null;
  }
  const chartIsotope = isotopeFromChartKey(chartKey);
  if (chartIsotope === "cross") {
    if (axis === "x") {
      return row.d18;
    }
    if (axis === "y") {
      return row.d13;
    }
    return calibrationPreviewNumericAttribute(row, config.z_axis);
  }
  if (axis === "y" && chartIsotope === "d13C") {
    return row.d13;
  }
  if (axis === "y" && chartIsotope === "d18O") {
    return row.d18;
  }
  return null;
}

function patchFilteredVector(
  vector: unknown,
  keepIndexes: number[],
  customdata: unknown[],
  sourceLength: number,
  masks: CalibrationPreviewMasks,
  axis: "x" | "y" | "z",
  chartKey: string | undefined,
  config: CalibrationConfig,
): unknown {
  const filtered = filterTraceVector(vector, keepIndexes, sourceLength);
  const values = coerceVector(filtered);
  if (!values) {
    return filtered;
  }
  let changed = false;
  const nextValues = [...values];
  for (let outputIndex = 0; outputIndex < keepIndexes.length; outputIndex += 1) {
    const sourceIndex = keepIndexes[outputIndex];
    const rowLabel = customDataRowLabel(customdata[sourceIndex]);
    const patched = patchedCalibrationAxisValue(masks.rowsByLabel.get(rowLabel), axis, chartKey, config);
    if (patched == null) {
      continue;
    }
    const current = finiteNumber(nextValues[outputIndex]);
    if (current == null || Math.abs(current - patched) > 1e-12) {
      nextValues[outputIndex] = patched;
      changed = true;
    }
  }
  return changed ? nextValues : filtered;
}

function colorValuesForIndexes(customdata: unknown[], keepIndexes: number[], masks: CalibrationPreviewMasks): number[] | null {
  if (!masks.color?.valuesByRow.size) {
    return null;
  }
  const values: number[] = [];
  for (const index of keepIndexes) {
    const rowLabel = customDataRowLabel(customdata[index]);
    const value = masks.color.valuesByRow.get(rowLabel);
    if (value == null) {
      return null;
    }
    values.push(value);
  }
  return values;
}

function filterCalibrationTraceByRows(
  trace: Record<string, unknown>,
  rowSet: Set<string>,
  masks: CalibrationPreviewMasks,
  config: CalibrationConfig,
  chartKey?: string,
): Record<string, unknown> {
  const customdata = coerceVector(trace.customdata);
  if (!customdata?.length) {
    return trace;
  }
  const keepIndexes: number[] = [];
  for (let index = 0; index < customdata.length; index += 1) {
    const rowLabel = customDataRowLabel(customdata[index]);
    if (rowLabel && rowSet.has(rowLabel)) {
      keepIndexes.push(index);
    }
  }
  const nextTrace: Record<string, unknown> = { ...trace };
  let changed = keepIndexes.length !== customdata.length;
  for (const key of ["customdata", "text", "hovertext", "ids"]) {
    if (key in nextTrace) {
      const filtered = filterTraceVector(nextTrace[key], keepIndexes, customdata.length);
      if (filtered !== nextTrace[key]) {
        nextTrace[key] = filtered;
        changed = true;
      }
    }
  }
  for (const axis of ["x", "y", "z"] as const) {
    if (axis in nextTrace) {
      const patched = patchFilteredVector(nextTrace[axis], keepIndexes, customdata, customdata.length, masks, axis, chartKey, config);
      if (patched !== nextTrace[axis]) {
        nextTrace[axis] = patched;
        changed = true;
      }
    }
  }
  const marker = trace.marker && typeof trace.marker === "object" ? (trace.marker as Record<string, unknown>) : undefined;
  let nextMarker = filterTraceNestedVectors(marker, keepIndexes, customdata.length);
  const previewColors = colorValuesForIndexes(customdata, keepIndexes, masks);
  if (previewColors) {
    nextMarker = {
      ...(nextMarker ?? marker ?? {}),
      color: previewColors,
      coloraxis: "coloraxis",
    };
  }
  if (nextMarker && nextMarker !== marker) {
    nextTrace.marker = nextMarker;
    changed = true;
  }
  const errorY = trace.error_y && typeof trace.error_y === "object" ? (trace.error_y as Record<string, unknown>) : undefined;
  const nextErrorY = filterTraceNestedVectors(errorY, keepIndexes, customdata.length);
  if (nextErrorY && nextErrorY !== errorY) {
    nextTrace.error_y = nextErrorY;
    changed = true;
  }
  return changed ? nextTrace : trace;
}

function colorAxisLayout(color: CalibrationPreviewColorState | null, verticalTitle = false): Record<string, unknown> | null {
  if (!color) {
    return null;
  }
  const values = Array.from(color.valuesByRow.values()).filter((value) => Number.isFinite(value));
  const min = values.length ? Math.min(...values) : undefined;
  const max = values.length ? Math.max(...values) : undefined;
  return {
    colorscale: "Viridis",
    ...(min != null && max != null ? { cmin: min === max ? min - 0.5 : min, cmax: min === max ? max + 0.5 : max } : {}),
    colorbar: {
      title: {
        text: color.param === "Date" ? "Date" : color.param,
        side: verticalTitle ? "right" : "top",
      },
      ...(color.tickvals && color.ticktext ? { tickmode: "array", tickvals: color.tickvals, ticktext: color.ticktext } : {}),
    },
  };
}

function applyCalibrationConfigPreviewToFigure(
  figure: Record<string, unknown> | undefined,
  masks: CalibrationPreviewMasks | null,
  config: CalibrationConfig | null | undefined,
  chartKey?: string,
): Record<string, unknown> | undefined {
  if (!figure || !masks || !config) {
    return figure;
  }
  const cloned = cloneFigure(figure);
  if (!Array.isArray(cloned.data)) {
    return figure;
  }
  let changed = false;
  const nextData = cloned.data.map((trace) => {
    const customdata = coerceVector(trace.customdata);
    if (!customdata?.length) {
      return trace;
    }
    const rowSet = calibrationTraceRowSet(trace, chartKey, masks, config);
    const nextTrace = filterCalibrationTraceByRows(trace, rowSet, masks, config, chartKey);
    changed = changed || nextTrace !== trace;
    return nextTrace;
  });
  const nextColorAxis = colorAxisLayout(masks.color, Boolean(chartKey?.includes("|d")));
  const nextLayout = nextColorAxis
    ? {
        ...cloned.layout,
        coloraxis: {
          ...((cloned.layout.coloraxis as Record<string, unknown> | undefined) ?? {}),
          ...nextColorAxis,
        },
      }
    : cloned.layout;
  changed = changed || nextLayout !== cloned.layout;
  return changed ? { ...cloned, data: nextData, layout: nextLayout } : figure;
}

function collectCalibrationPreviewFigures(
  workspace: CalibrationWorkspace | undefined,
  masks: CalibrationPreviewMasks | null,
  config: CalibrationConfig | null | undefined,
): Array<Record<string, unknown> | undefined> {
  if (!workspace) {
    return [];
  }
  const figures: Array<Record<string, unknown> | undefined> = [
    applyCalibrationConfigPreviewToFigure(workspace.figures["VPDB(13C)"], masks, config, "VPDB(13C)"),
    applyCalibrationConfigPreviewToFigure(workspace.figures["VSMOW(18O)"], masks, config, "VSMOW(18O)"),
    applyCalibrationConfigPreviewToFigure(workspace.figures.calibration_3d, masks, config, "calibration_3d"),
    applyCalibrationConfigPreviewToFigure(workspace.figures.crossplot, masks, config, "crossplot"),
    applyCalibrationConfigPreviewToFigure(workspace.linearity_figures.d13_raw, masks, config, "linearity|d13_raw"),
    applyCalibrationConfigPreviewToFigure(workspace.linearity_figures.d13_corrected, masks, config, "linearity|d13_corrected"),
    applyCalibrationConfigPreviewToFigure(workspace.linearity_figures.d18_raw, masks, config, "linearity|d18_raw"),
    applyCalibrationConfigPreviewToFigure(workspace.linearity_figures.d18_corrected, masks, config, "linearity|d18_corrected"),
  ];
  for (const section of workspace.standard_sections) {
    figures.push(
      applyCalibrationConfigPreviewToFigure(section.d13_figure, masks, config, `${section.standard}|d13C`),
      applyCalibrationConfigPreviewToFigure(section.d18_figure, masks, config, `${section.standard}|d18O`),
    );
  }
  return figures;
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
    <DualRangeField
      label={label}
      value={[low, high]}
      min={resolvedMin}
      max={resolvedMax}
      step={step}
      precision={precision}
      onChange={onChange}
    />
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
    <label className={cn("flex items-center gap-2 py-1.5 text-sm", disabled ? "cursor-not-allowed opacity-60" : "")}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4"
      />
      <span className="font-medium text-stone-800">{label}</span>
      {description ? (
        <Tooltip label={description}>
          <span tabIndex={0} aria-label={`More information about ${label}`} className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-stone-300 text-[10px] font-semibold text-stone-500">
            ?
          </span>
        </Tooltip>
      ) : null}
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

function formatCalibrationColorScaleValue(value: number, colorParam: string | null): string {
  if (String(colorParam ?? "").trim().toLowerCase() === "date") {
    const date = new Date((value - 719163) * 86400000);
    if (!Number.isNaN(date.getTime())) {
      return date.toISOString().slice(0, 10);
    }
  }
  return value.toLocaleString(undefined, { maximumSignificantDigits: 5 });
}

function calibrationColorParameterLabel(colorParam: string | null): string {
  const key = String(colorParam ?? "").trim().toLowerCase().replace(/\s+/g, " ");
  if (key === "date" || key === "date_ordinal") return "Date";
  if (key === "1 cycle int samp 44") return "Initial sample intensity";
  if (key === "1 cycle int ref 44") return "Initial reference gas intensity";
  if (key === "p_no_acid") return "P no Acid";
  if (key === "total_co2") return "total CO2";
  if (key === "p_gases") return "P gasses";
  return colorParam ?? "Color";
}

function calibrationColorScaleTicks(range: [number, number], count = 6): number[] {
  const [start, end] = range;
  if (!Number.isFinite(start) || !Number.isFinite(end) || count < 2) return [];
  return Array.from({ length: count }, (_, index) => start + ((end - start) * index) / (count - 1));
}

function CalibrationColorScaleBar({
  colorParam,
  range,
}: {
  colorParam: string | null;
  range: [number, number];
}) {
  const label = calibrationColorParameterLabel(colorParam);
  const ticks = calibrationColorScaleTicks(range);
  return (
    <div className="mx-auto w-full max-w-xl rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-xs">
      <div className="mb-1 font-semibold text-stone-900">{label}</div>
      <div
        className="h-2 w-full rounded-full border border-stone-300 bg-[linear-gradient(90deg,#440154_0%,#3b528b_25%,#21918c_50%,#5ec962_75%,#fde725_100%)]"
        role="img"
        aria-label={`${label} color scale from ${range[0]} to ${range[1]}`}
      />
      <div className="mt-1 grid grid-cols-6 text-[10px] tabular-nums text-stone-500">
        {ticks.map((tick, index) => (
          <span key={`${tick}-${index}`} className={index === 0 ? "text-left" : index === ticks.length - 1 ? "text-right" : "text-center"}>
            {formatCalibrationColorScaleValue(tick, colorParam)}
          </span>
        ))}
      </div>
    </div>
  );
}

function hideCalibrationEmbeddedColorbars(figure: Record<string, unknown> | undefined): Record<string, unknown> | undefined {
  if (!figure) {
    return figure;
  }
  const cloned = cloneFigure(figure);
  const data = cloned.data.map((trace) => {
    const marker = trace.marker && typeof trace.marker === "object" ? trace.marker as Record<string, unknown> : null;
    return marker ? { ...trace, marker: { ...marker, showscale: false } } : trace;
  });
  const layout = { ...cloned.layout };
  const legend = layout.legend && typeof layout.legend === "object" ? layout.legend as Record<string, unknown> : {};
  const margin = layout.margin && typeof layout.margin === "object" ? layout.margin as Record<string, unknown> : {};
  const existingTopMargin = toFiniteNumber(margin.t) ?? 0;
  layout.legend = {
    ...legend,
    orientation: "h",
    x: 0,
    xanchor: "left",
    y: 1.02,
    yanchor: "bottom",
  };
  layout.margin = { ...margin, r: 16, t: Math.max(existingTopMargin, 96) };
  for (const key of Object.keys(layout)) {
    if (!key.toLowerCase().startsWith("coloraxis")) {
      continue;
    }
    const axis = layout[key];
    if (axis && typeof axis === "object") {
      layout[key] = { ...(axis as Record<string, unknown>), showscale: false };
    }
  }
  return { ...cloned, data, layout };
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
  onPickDeltaValue?: (value: number, stdev?: number | null) => void;
}) {
  const [saturationColorAxis, setSaturationColorAxis] = useState<SaturationColorAxisKey>("mean44");
  const [saturationYAxis, setSaturationYAxis] = useState<SaturationAxisKey>("d13C");
  const cycleMean = diagnostics?.cycle_mean ?? {};
  const validMean = asNumber(cycleMean.valid_mean);
  const validStdDev = asNumber(cycleMean.valid_std_dev);
  const validCycleCount = asNumber(cycleMean.valid_cycles);
  const hasTooFewLinearityCycles = validCycleCount != null && validCycleCount < 4;
  const firstValidCycle = asNumber(cycleMean.selected_value) ?? asNumber(cycleMean.mean);
  const lastValidCycle = asNumber(cycleMean.last_valid_value);
  const referenceGasCorrection = asNumber(cycleMean.saturation_reference_gas_value);
  const firstCycleCorrection = asNumber(cycleMean.saturation_first_cycle_value);
  const saturationCorrection =
    diagnostics?.saturation_correction && typeof diagnostics.saturation_correction === "object"
      ? (diagnostics.saturation_correction as Record<string, unknown>)
      : {};
  const cycleLinearityValue = (key: string) => {
    const payload = saturationCorrection[key];
    return payload && typeof payload === "object" ? asNumber((payload as Record<string, unknown>).value) : null;
  };
  const cycleLinearityStd = (key: string) => {
    const payload = saturationCorrection[key];
    return payload && typeof payload === "object" ? asNumber((payload as Record<string, unknown>).std_dev) : null;
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
                figure={ensureFigureUiRevision(diagnosticsFigure, "calibration:selection-cycle")}
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

function formatMetric(value?: number | null, digits = 3) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "N/A";
  }
  return value.toFixed(digits);
}

function formatMetricWithUnit(value?: number | null, digits = 3) {
  const formatted = formatMetric(value, digits);
  return formatted === "N/A" ? formatted : `${formatted} permil`;
}

function classifyPrecision(value?: number | null) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "neutral" as const;
  }
  return value < PRECISION_PASS_THRESHOLD ? ("pass" as const) : ("fail" as const);
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

function PrecisionMetricPanel({
  label,
  value,
  detail,
}: {
  label: string;
  value?: number | null;
  detail?: string;
}) {
  const styles = toneClasses(classifyPrecision(value));
  return (
    <div className={`rounded-lg border px-4 py-3 ${styles.shell}`}>
      <div className={`text-xs font-medium ${styles.subtle}`}>{label}</div>
      <div className={`mt-1.5 text-2xl font-semibold leading-none tabular-nums ${styles.value}`}>{formatMetricWithUnit(value)}</div>
      {detail ? <div className={`mt-2 text-xs tabular-nums ${styles.subtle}`}>{detail}</div> : null}
    </div>
  );
}

function IsotopeSummaryTile({
  label,
  precision,
  correctedPrecision,
  linearityEnabled,
  totalRows,
  includedRows,
  includedPct,
}: {
  label: string;
  precision?: number | null;
  correctedPrecision?: number | null;
  linearityEnabled: boolean;
  totalRows: number;
  includedRows: number;
  includedPct: number;
}) {
  const outlierCount = Math.max(0, totalRows - includedRows);
  const outlierLabel = outlierCount === 1 ? "outlier" : "outliers";
  return (
    <PrecisionMetricPanel
      label={`${label} precision`}
      value={linearityEnabled ? correctedPrecision : precision}
      detail={`${outlierCount} ${outlierLabel} · ${includedPct.toFixed(1)}% successful`}
    />
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
      <CardHeader className="border-b border-stone-200 bg-stone-50 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-lg tracking-normal">{summary.standard}</CardTitle>
          {linearityEnabled ? (
            <Badge className="border-emerald-200 bg-emerald-50 text-emerald-800">Linearity corrected</Badge>
          ) : null}
        </div>
        <CardDescription>Precision across included measurements. Values below 0.07 permil are shown in green.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 p-4">
        <div className="grid gap-3 md:grid-cols-2">
          <IsotopeSummaryTile
            label="Carbon isotope (d13C)"
            precision={summary.d13_precision}
            correctedPrecision={summary.d13_linearity_corrected_precision}
            linearityEnabled={linearityEnabled}
            totalRows={summary.total_rows}
            includedRows={summary.included_d13}
            includedPct={summary.included_pct_d13}
          />
          <IsotopeSummaryTile
            label="Oxygen isotope (d18O)"
            precision={summary.d18_precision}
            correctedPrecision={summary.d18_linearity_corrected_precision}
            linearityEnabled={linearityEnabled}
            totalRows={summary.total_rows}
            includedRows={summary.included_d18}
            includedPct={summary.included_pct_d18}
          />
        </div>
        {linePrecisionEntries.length ? (
          <div>
            <div className="mb-2 flex items-center justify-between">
              <div className="text-xs font-medium text-stone-600">Precision breakdown</div>
              <div className="text-xs text-stone-500">{linePrecisionEntries.length} lines</div>
            </div>
            <div className="overflow-x-auto rounded-lg border border-stone-200">
              <div className="grid min-w-[460px] grid-cols-[100px_repeat(2,minmax(0,1fr))] bg-stone-100 px-3 py-2 text-xs font-medium text-stone-600">
                <span>Line</span>
                <span>d13C (permil)</span>
                <span>d18O (permil)</span>
              </div>
              {linePrecisionEntries.map(([line, values], index) => {
                const d13Precision = linearityEnabled ? values.d13_linearity_corrected_precision : values.d13_precision;
                const d18Precision = linearityEnabled ? values.d18_linearity_corrected_precision : values.d18_precision;
                const d13Tone = toneClasses(classifyPrecision(d13Precision));
                const d18Tone = toneClasses(classifyPrecision(d18Precision));
                return (
                  <div
                    key={line}
                    className={`grid min-w-[460px] grid-cols-[100px_repeat(2,minmax(0,1fr))] items-center px-3 py-2 text-sm tabular-nums text-stone-700 ${
                      index % 2 ? "bg-stone-50" : "bg-white"
                    }`}
                  >
                    <span className="font-medium text-stone-900">Line {line}</span>
                    <span className={`inline-flex max-w-fit rounded-md border px-2 py-1 text-base font-semibold ${d13Tone.shell} ${d13Tone.value}`}>
                      {formatMetricWithUnit(d13Precision)}
                    </span>
                    <span className={`inline-flex max-w-fit rounded-md border px-2 py-1 text-base font-semibold ${d18Tone.shell} ${d18Tone.value}`}>
                      {formatMetricWithUnit(d18Precision)}
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
  const [calibrationJob, setCalibrationJob] = useState<JobSnapshot<SessionSnapshot> | null>(null);
  const [hasLoadedDraft, setHasLoadedDraft] = useState(false);
  const [selectedTargets, setSelectedTargets] = useState<SelectedTarget[]>([]);
  const [activeTargetIndex, setActiveTargetIndex] = useState(0);
  const [isSelectionEditorOpen, setSelectionEditorOpen] = useState(false);
  const [isOfficialValuesModalOpen, setOfficialValuesModalOpen] = useState(false);
  const [isOfficialValuesEditMode, setOfficialValuesEditMode] = useState(false);
  const [officialValuesDraftRows, setOfficialValuesDraftRows] = useState<Record<string, { d13: string; d18: string }>>({});
  const [officialValuesOrder, setOfficialValuesOrder] = useState<string[]>(() => {
    if (typeof window === "undefined") {
      return [];
    }
    try {
      const stored = JSON.parse(window.localStorage.getItem(OFFICIAL_VALUES_ORDER_STORAGE_KEY) ?? "null");
      return Array.isArray(stored) ? stored.filter((item): item is string => typeof item === "string") : [];
    } catch {
      return [];
    }
  });
  const [draggingOfficialStandard, setDraggingOfficialStandard] = useState<string | null>(null);
  const [newStandardName, setNewStandardName] = useState("");
  const [newStandardD13, setNewStandardD13] = useState("");
  const [newStandardD18, setNewStandardD18] = useState("");
  const [officialValuesError, setOfficialValuesError] = useState<string | null>(null);
  const [singleValue, setSingleValue] = useState(0);
  const [singleStdev, setSingleStdev] = useState<number | null>(null);
  const [singleOffset, setSingleOffset] = useState(SELECTION_EDITOR_DEFAULT_OFFSET);
  const [crossD13Value, setCrossD13Value] = useState(0);
  const [crossD18Value, setCrossD18Value] = useState(0);
  const [multiOffsetD13, setMultiOffsetD13] = useState(0);
  const [multiOffsetD18, setMultiOffsetD18] = useState(0);
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
        closeSelectionEditor();
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
    setSingleStdev(null);
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
              ...current.linearity,
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

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(OFFICIAL_VALUES_ORDER_STORAGE_KEY, JSON.stringify(officialValuesOrder));
    }
  }, [officialValuesOrder]);

  const persistedWorkspace = workspaceQuery.data as CalibrationWorkspace | undefined;
  const activeDraftConfig = config ?? persistedWorkspace?.config ?? null;
  const activeDraftConfigSignature = activeDraftConfig ? JSON.stringify(activeDraftConfig) : "";
  const persistedConfigSignature = persistedWorkspace?.config ? JSON.stringify(persistedWorkspace.config) : "";
  const hasDraftConfigChanges = Boolean(
    sessionId &&
      activeDraftConfig &&
      persistedWorkspace?.config &&
      activeDraftConfigSignature !== persistedConfigSignature,
  );
  const calibrationPreviewWorkspaceQuery = useQuery({
    queryKey: ["calibration-workspace-preview", sessionId, activeDraftConfigSignature],
    queryFn: () => api.previewCalibrationWorkspace(sessionId!, activeDraftConfig!),
    enabled: hasDraftConfigChanges,
    staleTime: 5_000,
  });
  const linearityPreviewDataQuery = useQuery({
    queryKey: ["processing-linearity-preview-data", sessionId],
    queryFn: () => api.getProcessingLinearityPreviewData(sessionId!),
    enabled: Boolean(sessionId),
    staleTime: 60_000,
  });
  const calibrationPreviewMasks = useMemo(
    () => buildCalibrationPreviewMasks(linearityPreviewDataQuery.data, activeDraftConfig),
    [activeDraftConfig, linearityPreviewDataQuery.data],
  );
  const activeColorParam = activeDraftConfig?.color_param ?? persistedWorkspace?.config.color_param ?? null;
  const colorScaleFigures = useMemo<Array<Record<string, unknown> | undefined>>(() => {
    return collectCalibrationPreviewFigures(persistedWorkspace, calibrationPreviewMasks, activeDraftConfig);
  }, [activeDraftConfig, calibrationPreviewMasks, persistedWorkspace]);
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

  const runMutation = useMutation({
    mutationFn: (payload: CalibrationConfig) => api.runCalibration(sessionId!, payload, setCalibrationJob),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration-workspace", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-linearity-preview-data", sessionId] });
    },
    onSettled: () => setCalibrationJob(null),
  });
  const cancelCalibrationJobMutation = useMutation({
    mutationFn: (jobId: string) => api.cancelJob(jobId),
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
      await queryClient.invalidateQueries({ queryKey: ["calibration", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-workspace", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-linearity-preview-data", sessionId] });
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
      }
    },
  });

  const deleteOfficialStandardMutation = useMutation({
    mutationFn: (standard: string) => api.deleteOfficialStandard(standard),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["official-standard-values"] });
      if (sessionId) {
        await queryClient.invalidateQueries({ queryKey: ["calibration-workspace", sessionId] });
      }
    },
  });

  const editMutation = useMutation({
    mutationFn: (payload: EditAction) => api.editProcessing(sessionId!, payload, []),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-workspace", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-species-section", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-linearity-preview-data", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-diagnostics", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-diagnostics-cross-d13", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["processing-diagnostics-cross-d18", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration-workspace", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration", sessionId] });
    },
  });

  function updateConfig<T extends keyof CalibrationConfig>(key: T, value: CalibrationConfig[T]) {
    setConfig((current) => (current ? { ...current, [key]: value } : current));
  }

  function updateLinearity(key: keyof CalibrationConfig["linearity"], value: boolean | number | string | null) {
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

  function updateLinearityCoefficientOffset(
    isotopeKey: "d13C" | "d18O",
    term: LinearityCoefficientTerm,
    value: number,
  ) {
    setConfig((current) => {
      if (!current) {
        return current;
      }
      const next = { ...current, linearity: { ...current.linearity } };
      if (term === "primary" && isotopeKey === "d13C") {
        next.linearity.manual_d13_per_10v = value;
      } else if (term === "primary") {
        next.linearity.manual_d18_per_10v = value;
      } else if (isotopeKey === "d13C") {
        next.linearity.manual_d13_per_10v2 = value;
      } else {
        next.linearity.manual_d18_per_10v2 = value;
      }
      const activeOffsets = [
        Number(next.linearity.manual_d13_per_10v ?? 0),
        Number(next.linearity.manual_d18_per_10v ?? 0),
        ...(next.linearity.quadratic || selectedLinearityIntensityCol === LINEARITY_INTENSITY_TWO_TERM44
          ? [Number(next.linearity.manual_d13_per_10v2 ?? 0), Number(next.linearity.manual_d18_per_10v2 ?? 0)]
          : []),
      ];
      const hasOffset = activeOffsets.some((offset) => Number.isFinite(offset) && Math.abs(offset) > 1e-12);
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

  function closeSelectionEditor() {
    setSelectionEditorOpen(false);
    setSelectedTargets([]);
    setActiveTargetIndex(0);
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

  function setSingleValueFromSuggestion(value: number, stdev: number | null = null) {
    setSingleValue(roundDeltaValue(value));
    setSingleStdev(stdev);
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
      stdev: singleStdev,
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

  function moveOfficialStandard(standard: string, direction: "up" | "down") {
    setOfficialValuesOrder((current) => {
      const currentOrder = allOfficialValueRows.map((row) => row.standard);
      const order = currentOrder.length ? currentOrder : current;
      const index = order.indexOf(standard);
      const nextIndex = direction === "up" ? index - 1 : index + 1;
      if (index < 0 || nextIndex < 0 || nextIndex >= order.length) {
        return order;
      }
      const next = [...order];
      [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
      return next;
    });
  }

  function handleOfficialStandardDragStart(event: ReactDragEvent<HTMLTableRowElement>, standard: string) {
    setDraggingOfficialStandard(standard);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", standard);
  }

  function handleOfficialStandardDrop(event: ReactDragEvent<HTMLTableRowElement>, targetStandard: string) {
    event.preventDefault();
    const draggedStandard = event.dataTransfer.getData("text/plain") || draggingOfficialStandard;
    if (!draggedStandard || draggedStandard === targetStandard) {
      setDraggingOfficialStandard(null);
      return;
    }
    setOfficialValuesOrder((current) => {
      const order = allOfficialValueRows.map((row) => row.standard);
      const fromIndex = order.indexOf(draggedStandard);
      const toIndex = order.indexOf(targetStandard);
      if (fromIndex < 0 || toIndex < 0) {
        return current;
      }
      const next = [...order];
      const [moved] = next.splice(fromIndex, 1);
      next.splice(toIndex, 0, moved);
      return next;
    });
    setDraggingOfficialStandard(null);
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
  const activeConfig = activeDraftConfig;
  const displayedWorkspace = calibrationPreviewWorkspaceQuery.data ?? workspace;

  if (!workspace || !activeConfig || !displayedWorkspace) {
    return null;
  }
  const colorSliderBounds: ColorScaleBounds = colorScaleBounds ?? { min: 0, max: 1 };
  const effectiveColorScaleRange = normalizeColorScaleRange(
    colorScaleRange ?? colorScaleTwoSigmaRange ?? [colorSliderBounds.min, colorSliderBounds.max],
    colorSliderBounds,
  );
  const colorScaleSignature = `${activeConfig.color_param}:${effectiveColorScaleRange[0]}:${effectiveColorScaleRange[1]}`;
  if (colorScaleSignatureRef.current !== colorScaleSignature) {
    colorScaleSignatureRef.current = colorScaleSignature;
    colorScaledFigureCacheRef.current = new WeakMap();
  }
  const hasServerPreviewWorkspace = Boolean(calibrationPreviewWorkspaceQuery.data);
  const withCalibrationPreview = (figure: Record<string, unknown> | undefined, chartKey: string) =>
    hasServerPreviewWorkspace ? figure : applyCalibrationConfigPreviewToFigure(figure, calibrationPreviewMasks, activeConfig, chartKey);
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
  const withLinearityFigure = (figure: Record<string, unknown> | undefined) => {
    const scaled = hideCalibrationEmbeddedColorbars(withColorScaleRange(figure));
    if (!scaled) {
      return scaled;
    }
    const layout = scaled.layout && typeof scaled.layout === "object" ? scaled.layout as Record<string, unknown> : {};
    return {
      ...scaled,
      layout: {
        ...layout,
        title: { text: "" },
        showlegend: true,
        margin: { ...(layout.margin as Record<string, unknown> | undefined), r: 12 },
      },
    };
  };

  const minDate = displayedWorkspace.available_values.min_date ?? undefined;
  const maxDate = displayedWorkspace.available_values.max_date ?? undefined;
  const selectedStandards = activeConfig.selected_standards;
  const selectedStandardOfficialValues = displayedWorkspace.selected_standard_official_values ?? [];
  const selectedStandardsSet = new Set(selectedStandards.map((item) => String(item ?? "").trim().toUpperCase()).filter(Boolean));
  const officialValueRows = buildOfficialValuesRows(officialValuesQuery.data ?? selectedStandardOfficialValues);
  const officialValueRowsByStandard = new Map(officialValueRows.map((row) => [row.standard, row]));
  const storedOfficialValueRows = officialValuesOrder.flatMap((standard) => {
    const row = officialValueRowsByStandard.get(standard);
    return row ? [row] : [];
  });
  const storedOfficialStandards = new Set(storedOfficialValueRows.map((row) => row.standard));
  const newOfficialValueRows = officialValueRows.filter((row) => !storedOfficialStandards.has(row.standard));
  const allOfficialValueRows = [...storedOfficialValueRows, ...newOfficialValueRows];
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
  const selectedLinearityCycleIntensityAggregation = LINEARITY_CYCLE_INTENSITY_AGGREGATION_OPTIONS.some(
    (option) => option.value === activeConfig.linearity.cycle_intensity_aggregation,
  )
    ? (activeConfig.linearity.cycle_intensity_aggregation as LinearityCycleIntensityAggregation)
    : "run_median";
  const selectedLinearityBasisLabel = `${getLinearityIntensityOptionLabel(selectedLinearityIntensityCol)} · ${getLinearityCycleAggregationLabel(
    selectedLinearityCycleIntensityAggregation,
  )}`;
  const isTwoTermLinearityBasis = selectedLinearityIntensityCol === LINEARITY_INTENSITY_TWO_TERM44;
  const showSecondaryCoefficientOffset = Boolean(activeConfig.linearity.quadratic) || isTwoTermLinearityBasis;
  const d13Fit = (displayedWorkspace.linearity_fits?.d13C ?? {}) as Record<string, unknown>;
  const d18Fit = (displayedWorkspace.linearity_fits?.d18O ?? {}) as Record<string, unknown>;
  const d13FitSlope = asNumber(d13Fit.slope);
  const d18FitSlope = asNumber(d18Fit.slope);
  const d13FitQuad = asNumber(d13Fit.quad);
  const d18FitQuad = asNumber(d18Fit.quad);
  const lineIntensityBasis = String(displayedWorkspace.linearity_fits?.intensity_col ?? selectedLinearityIntensityCol ?? "N/A");
  const runError = runMutation.error instanceof Error ? runMutation.error.message : null;
  const resetError = resetCalibrationMutation.error instanceof Error ? resetCalibrationMutation.error.message : null;
  const hasUnsavedPreview = JSON.stringify(activeConfig) !== JSON.stringify(workspace.config);
  const precisionSummaries = displayedWorkspace.precision_summaries;
  const standardPrecisionRows = precisionSummaries.filter((summary) => selectedStandards.includes(summary.standard)).slice(0, 6);
  const coefficientOffsetEnabled = [
    Number(activeConfig.linearity.manual_d13_per_10v ?? 0),
    Number(activeConfig.linearity.manual_d18_per_10v ?? 0),
    ...(activeConfig.linearity.quadratic || isTwoTermLinearityBasis
      ? [Number(activeConfig.linearity.manual_d13_per_10v2 ?? 0), Number(activeConfig.linearity.manual_d18_per_10v2 ?? 0)]
      : []),
  ].some((offset) => Number.isFinite(offset) && Math.abs(offset) > 1e-12);
  const busy = runMutation.isPending || editMutation.isPending || resetCalibrationMutation.isPending;
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
        figure: withColorScaleRange(withCalibrationPreview(displayedWorkspace.figures["VPDB(13C)"], "VPDB(13C)")),
      },
      "VSMOW(18O)": {
        title: "d18O Calibration",
        description: "Source chart for current selection.",
        chartKey: "VSMOW(18O)",
        figure: withColorScaleRange(withCalibrationPreview(displayedWorkspace.figures["VSMOW(18O)"], "VSMOW(18O)")),
      },
      calibration_3d: {
        title: "Calibration 3D Chart",
        description: "Source chart for current selection.",
        chartKey: "calibration_3d",
        figure: withColorScaleRange(withCalibrationPreview(displayedWorkspace.figures.calibration_3d, "calibration_3d")),
      },
      crossplot: {
        title: "Calibration Crossplot",
        description: "Source chart for current selection.",
        chartKey: "crossplot",
        figure: withColorScaleRange(withCalibrationPreview(displayedWorkspace.figures.crossplot, "crossplot")),
      },
      "linearity|d13_raw": {
        title: "Linearity d13C Raw",
        description: "Source chart for current selection.",
        chartKey: "linearity|d13_raw",
        figure: withColorScaleRange(withCalibrationPreview(displayedWorkspace.linearity_figures.d13_raw, "linearity|d13_raw")),
      },
      "linearity|d13_corrected": {
        title: "Linearity d13C Corrected",
        description: "Source chart for current selection.",
        chartKey: "linearity|d13_corrected",
        figure: withColorScaleRange(withCalibrationPreview(displayedWorkspace.linearity_figures.d13_corrected, "linearity|d13_corrected")),
      },
      "linearity|d18_raw": {
        title: "Linearity d18O Raw",
        description: "Source chart for current selection.",
        chartKey: "linearity|d18_raw",
        figure: withColorScaleRange(withCalibrationPreview(displayedWorkspace.linearity_figures.d18_raw, "linearity|d18_raw")),
      },
      "linearity|d18_corrected": {
        title: "Linearity d18O Corrected",
        description: "Source chart for current selection.",
        chartKey: "linearity|d18_corrected",
        figure: withColorScaleRange(withCalibrationPreview(displayedWorkspace.linearity_figures.d18_corrected, "linearity|d18_corrected")),
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
    const standardChartKey = `${standardName}|${standardSuffix}`;
    const figure =
      standardSuffix === "d13C"
        ? withColorScaleRange(withCalibrationPreview(section.d13_figure, standardChartKey))
        : withColorScaleRange(withCalibrationPreview(section.d18_figure, standardChartKey));
    return {
      title: `${standardName} ${standardSuffix} Outlier Trace`,
      description: "Source chart for current selection.",
      chartKey,
      figure: highlightSelectionSourceFigure(figure, activeTarget),
    };
  })();

  return (
    <div className="space-y-5">
      <PageHeader
        title="Calibration"
        description="Configure standards, review precision, and apply calibration."
        compact
        actions={
          <>
            <span className="rounded-md bg-white px-2 py-0.5 ring-1 ring-stone-200">Standards: {selectedStandards.length}</span>
            <span className="rounded-md bg-white px-2 py-0.5 ring-1 ring-stone-200">Method: {activeConfig.calibration_type}</span>
            <span className="rounded-md bg-white px-2 py-0.5 ring-1 ring-stone-200">
              {hasUnsavedPreview ? "Preview active" : "Saved config"}
            </span>
          </>
        }
      />

      {colorScaleBounds ? (
        <CalibrationColorScaleBar colorParam={activeColorParam} range={effectiveColorScaleRange} />
      ) : null}

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
            className="w-full max-w-4xl rounded-lg border border-stone-300 bg-white shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="relative z-20 flex items-center justify-between border-b border-stone-200 bg-white px-4 py-3">
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
                  <X className="h-4 w-4" />
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
                  <div className="w-fit max-w-full overflow-x-auto rounded-lg border border-stone-200">
                    <table className="w-auto min-w-[680px] border-collapse text-sm">
                      <thead className="bg-stone-50 text-left text-xs uppercase tracking-normal text-stone-500">
                        <tr>
                          <th className="w-10 px-2 py-2.5 font-semibold" aria-label="Reorder" />
                          <th className="px-3 py-2.5 font-semibold">Standard</th>
                          <th className="px-3 py-2.5 font-semibold">d13C ({OFFICIAL_VALUE_TYPE_D13})</th>
                          <th className="px-3 py-2.5 font-semibold">d18O ({OFFICIAL_VALUE_TYPE_D18})</th>
                          {isOfficialValuesEditMode ? <th className="px-3 py-2.5 font-semibold">Actions</th> : null}
                        </tr>
                      </thead>
                      <tbody>
                        {allOfficialValueRows.map((row, index) => {
                          const draft = officialValuesDraftRows[row.standard] ?? { d13: "", d18: "" };
                          return (
                          <tr
                            key={`${row.standard}:${index}`}
                            draggable
                            onDragStart={(event) => handleOfficialStandardDragStart(event, row.standard)}
                            onDragOver={(event) => event.preventDefault()}
                            onDrop={(event) => handleOfficialStandardDrop(event, row.standard)}
                            onDragEnd={() => setDraggingOfficialStandard(null)}
                            className={cn(
                              index % 2 ? "bg-stone-50/60" : "bg-white",
                              draggingOfficialStandard === row.standard && "opacity-50",
                            )}
                          >
                            <td className="px-2 py-2.5 text-stone-400">
                              <div className="flex items-center gap-0.5" title="Drag to reorder">
                                <GripVertical className="h-4 w-4 cursor-grab" aria-hidden="true" />
                                {isOfficialValuesEditMode ? (
                                  <span className="flex flex-col">
                                    <button
                                      type="button"
                                      className="rounded p-0.5 text-stone-400 hover:bg-stone-200 hover:text-stone-800 disabled:opacity-30"
                                      onClick={() => moveOfficialStandard(row.standard, "up")}
                                      disabled={index === 0}
                                      aria-label={`Move ${row.standard} up`}
                                    >
                                      <ChevronUp className="h-3 w-3" />
                                    </button>
                                    <button
                                      type="button"
                                      className="rounded p-0.5 text-stone-400 hover:bg-stone-200 hover:text-stone-800 disabled:opacity-30"
                                      onClick={() => moveOfficialStandard(row.standard, "down")}
                                      disabled={index === allOfficialValueRows.length - 1}
                                      aria-label={`Move ${row.standard} down`}
                                    >
                                      <ChevronDown className="h-3 w-3" />
                                    </button>
                                  </span>
                                ) : null}
                              </div>
                            </td>
                            <td className="px-3 py-2.5 font-semibold text-stone-800">
                              <div className="flex items-center gap-2">
                                <span>{row.standard}</span>
                                {selectedStandardsSet.has(row.standard) ? (
                                  <span className="rounded-md bg-stone-900 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-normal text-white">
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
                <div className="space-y-3 rounded-lg border border-stone-200 bg-stone-50/60 p-3">
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
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  closeSelectionEditor();
                }}
              >
                <X className="h-4 w-4" />
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
                  <CardContent className="min-w-0 overflow-hidden">
                    <PlotlyChart
                      figure={selectionSourceChart.figure}
                      className="pointer-events-none h-[360px] w-full"
                      fitContainer
                      deferRenderMs={SELECTION_EDITOR_CHART_DEFER_MS}
                    />
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
                              if (item.canSetSingleValue && item.setValue != null) {
                                setSingleValueFromSuggestion(item.setValue);
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
                          className={`rounded-md px-3 py-1 text-xs ring-1 ring-stone-200 ${
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
                            onChange={(event) => {
                              setSingleValue(Number(event.target.value));
                              setSingleStdev(null);
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
                          <div className="text-xs uppercase tracking-normal text-stone-500">d13C details</div>
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
                          <div className="text-xs uppercase tracking-normal text-stone-500">d18O details</div>
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
                  No active selection.
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}

      <div className="workspace-grid">
        <aside className="control-column">
          <Card>
            <CardHeader>
              <CardTitle>Calibration Controls</CardTitle>
              <CardDescription>Configure standards, visualization, linearity, outlier detection, and precision date range settings.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <MultiSelectDropdown
                label="Selected standards"
                options={displayedWorkspace.available_values.standards}
                selected={activeConfig.selected_standards}
                onChange={(next) => updateConfig("selected_standards", next)}
                placeholder="Select standards"
              />
              <p className="-mt-2 text-xs text-stone-500">
                Choose reference materials from the standards database or any Identifier 1 in this dataset.
              </p>

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
                        {calibrationColorParameterLabel(option)}
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

              <div className="space-y-4 rounded-lg border border-stone-200 bg-white/80 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-sm font-medium text-stone-800">Linearity (shared with processing)</div>
                  <span className="rounded-md bg-stone-100 px-2 py-1 text-xs text-stone-600">Basis: {selectedLinearityBasisLabel}</span>
                </div>
                <CheckboxField
                  checked={activeConfig.linearity.apply}
                  label="Enable linearity correction"
                  description="Uses the same basis, fits, and offsets as Processing."
                  onChange={(checked) => updateLinearity("apply", checked)}
                />
                {!isTwoTermLinearityBasis ? (
                  <CheckboxField
                    checked={Boolean(activeConfig.linearity.quadratic)}
                    label="Use quadratic linearity relationship"
                    description="Fits and applies y = a + b*I + c*I^2 instead of y = a + b*I."
                    onChange={(checked) => updateLinearity("quadratic", checked)}
                  />
                ) : null}
                <label className="text-sm">
                  <span className="mb-1 block text-stone-700">Linearity basis</span>
                  <select
                    value={selectedLinearityIntensityCol}
                    onChange={(event) => updateLinearityIntensityCol(event.target.value)}
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
                    onChange={(event) => updateLinearity("cycle_intensity_aggregation", event.target.value)}
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
                <Tooltip label={getLinearityBasisFormula(selectedLinearityIntensityCol, selectedLinearityCycleIntensityAggregation)} align="start">
                  <span tabIndex={0} className="inline-flex cursor-help text-xs font-medium text-stone-600 underline decoration-dotted underline-offset-4">
                    Basis formula
                  </span>
                </Tooltip>
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
                  <div className="border-t border-stone-200 pt-2 text-sm">
                    <div className="text-xs font-medium text-stone-500">d13C fitted coefficients</div>
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
                  <div className="border-t border-stone-200 pt-2 text-sm">
                    <div className="text-xs font-medium text-stone-500">d18O fitted coefficients</div>
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
                      value={activeConfig.linearity.manual_d13_per_10v ?? 0}
                      onValueChange={(value) => updateLinearityCoefficientOffset("d13C", "primary", value)}
                      className="w-full rounded-lg border border-stone-300 px-3 py-2"
                    />
                  </label>
                  <label className="text-sm">
                    <span className="mb-1 block text-stone-700">
                      {getLinearityCoefficientLabel("d18O", selectedLinearityIntensityCol, "primary", selectedLinearityCycleIntensityAggregation)}
                    </span>
                    <DecimalInput
                      value={activeConfig.linearity.manual_d18_per_10v ?? 0}
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
                        value={activeConfig.linearity.manual_d13_per_10v2 ?? 0}
                        onValueChange={(value) => updateLinearityCoefficientOffset("d13C", "secondary", value)}
                        className="w-full rounded-lg border border-stone-300 px-3 py-2"
                      />
                    </label>
                    <label className="text-sm">
                      <span className="mb-1 block text-stone-700">
                        {getLinearityCoefficientLabel("d18O", selectedLinearityIntensityCol, "secondary", selectedLinearityCycleIntensityAggregation)}
                      </span>
                      <DecimalInput
                        value={activeConfig.linearity.manual_d18_per_10v2 ?? 0}
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
                  <div className="space-y-1 border-t border-stone-200 pt-2">
                    <Tooltip label="Precision after applying the shared linearity correction to each selected standard." align="start">
                      <span tabIndex={0} className="inline-flex cursor-help text-xs font-medium text-stone-600 underline decoration-dotted underline-offset-4">Corrected precision</span>
                    </Tooltip>
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

              <div className="space-y-3 border-t border-stone-200 pt-4">
                <Tooltip label="Set how calibration outliers are identified before precision is calculated." align="start">
                  <span tabIndex={0} className="inline-flex cursor-help text-xs font-medium text-stone-600 underline decoration-dotted underline-offset-4">Outlier settings</span>
                </Tooltip>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-2">
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

                <label className="flex items-center gap-2 py-1.5 text-sm">
                  <input
                    type="checkbox"
                    checked={activeConfig.independent_isotope_outliers}
                    onChange={(event) => updateConfig("independent_isotope_outliers", event.target.checked)}
                    className="h-4 w-4 accent-stone-900"
                  />
                  <span>
                    <span className="text-sm font-medium tracking-normal text-stone-800">Independent isotope outliers</span>
                  </span>
                  <Tooltip label="Keep d13C and d18O outlier filtering independent for each standard row.">
                    <span tabIndex={0} aria-label="More information about independent isotope outliers" className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-stone-300 text-[10px] font-semibold text-stone-500">?</span>
                  </Tooltip>
                </label>
              </div>

              <div className="space-y-3 border-t border-stone-200 pt-4">
                <Tooltip label="Limit the measurement dates included in the precision calculation." align="start">
                  <span tabIndex={0} className="inline-flex cursor-help text-xs font-medium text-stone-600 underline decoration-dotted underline-offset-4">Precision date range</span>
                </Tooltip>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
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

              <div className="flex items-center justify-between border-t border-stone-200 pt-4">
                <Tooltip label="Open the database values used in calibration equations." align="start">
                  <span tabIndex={0} className="cursor-help text-sm font-medium text-stone-800 underline decoration-dotted underline-offset-4">Official standard values</span>
                </Tooltip>
                <Button asChild variant="outline" size="sm">
                  <Link href="/settings">
                    <Database className="h-4 w-4" />
                    Settings
                  </Link>
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
                {calibrationJob?.cancellable ? (
                  <Button
                    variant="outline"
                    onClick={() => cancelCalibrationJobMutation.mutate(calibrationJob.job_id)}
                    disabled={cancelCalibrationJobMutation.isPending}
                  >
                    {cancelCalibrationJobMutation.isPending ? "Cancelling..." : "Cancel run"}
                  </Button>
                ) : null}
              </div>
              {calibrationJob ? (
                <div className="space-y-2" aria-live="polite">
                  <div className="flex justify-between text-xs text-stone-600">
                    <span>{calibrationJob.message || "Running calibration"}</span>
                    <span>{Math.round(calibrationJob.progress)}%</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-stone-200" role="progressbar" aria-valuenow={calibrationJob.progress} aria-valuemin={0} aria-valuemax={100}>
                    <div className="h-full rounded-full bg-stone-900 transition-[width] duration-200" style={{ width: `${calibrationJob.progress}%` }} />
                  </div>
                </div>
              ) : null}
              <div className="text-xs text-stone-500">Select exactly one or two standards to run calibration.</div>
              {runError ? <div className="text-xs text-red-600">Calibration error: {runError}</div> : null}
              {resetError ? <div className="text-xs text-red-600">Reset error: {resetError}</div> : null}
            </CardContent>
          </Card>
        </aside>

        <div className="space-y-6">
          {precisionSummaries.length ? (
            <div className="grid gap-3">
              {precisionSummaries.map((summary) => (
                <PrecisionCard key={summary.standard} summary={summary} linearityEnabled={Boolean(activeConfig.linearity.apply)} />
              ))}
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
              <div className="space-y-3">
                <div className="grid gap-6 2xl:grid-cols-2">
                <Card className="flex min-w-0 flex-col overflow-hidden">
                  <CardHeader>
                    <CardTitle>d13C Calibration</CardTitle>
                  </CardHeader>
                  <CardContent className="min-h-0 min-w-0 flex-1 overflow-hidden p-0">
                    <PlotlyChart
                      figure={hideCalibrationEmbeddedColorbars(withColorScaleRange(withCalibrationPreview(displayedWorkspace.figures["VPDB(13C)"], "VPDB(13C)")))}
                      className="h-[clamp(380px,42vw,620px)] w-full"
                      fitContainer
                      {...chartHoverProps("VPDB(13C)")}
                      onPointClick={(points) => openProcessingSelectionEditor("VPDB(13C)", points, false)}
                      onSelection={(points) => openProcessingSelectionEditor("VPDB(13C)", points, true)}
                    />
                  </CardContent>
                </Card>
                <Card className="flex min-w-0 flex-col overflow-hidden">
                  <CardHeader>
                    <CardTitle>d18O Calibration</CardTitle>
                  </CardHeader>
                  <CardContent className="min-h-0 min-w-0 flex-1 overflow-hidden p-0">
                    <PlotlyChart
                      figure={hideCalibrationEmbeddedColorbars(withColorScaleRange(withCalibrationPreview(displayedWorkspace.figures["VSMOW(18O)"], "VSMOW(18O)")))}
                      className="h-[clamp(380px,42vw,620px)] w-full"
                      fitContainer
                      {...chartHoverProps("VSMOW(18O)")}
                      onPointClick={(points) => openProcessingSelectionEditor("VSMOW(18O)", points, false)}
                      onSelection={(points) => openProcessingSelectionEditor("VSMOW(18O)", points, true)}
                    />
                  </CardContent>
                </Card>
                </div>
              </div>

              <div className="grid gap-6 xl:grid-cols-2">
                <Card className="flex min-w-0 flex-col overflow-hidden">
                  <CardHeader>
                    <CardTitle>Calibration 3D Chart</CardTitle>
                    <CardDescription>Filtered standards in calibration space using the active color and Z-axis parameters.</CardDescription>
                  </CardHeader>
                  <CardContent className="min-h-0 min-w-0 flex-1 overflow-hidden p-0">
                    <PlotlyChart
                      figure={hideCalibrationEmbeddedColorbars(withColorScaleRange(withCalibrationPreview(displayedWorkspace.figures.calibration_3d, "calibration_3d")))}
                      className="h-[clamp(380px,42vw,620px)] w-full"
                      fitContainer
                      {...chartHoverProps("calibration_3d")}
                      onPointClick={(points) => openProcessingSelectionEditor("calibration_3d", points, false)}
                      onSelection={(points) => openProcessingSelectionEditor("calibration_3d", points, true)}
                    />
                  </CardContent>
                </Card>
                <div className="min-w-0">
                <Card className="flex min-w-0 flex-col overflow-hidden">
                  <CardHeader>
                    <CardTitle>Calibration Crossplot</CardTitle>
                    <CardDescription>d13C vs d18O crossplot for the filtered standards set.</CardDescription>
                  </CardHeader>
                  <CardContent className="min-h-0 min-w-0 flex-1 overflow-hidden p-0">
                    <PlotlyChart
                      figure={hideCalibrationEmbeddedColorbars(withColorScaleRange(withCalibrationPreview(displayedWorkspace.figures.crossplot, "crossplot")))}
                      className="h-[clamp(380px,42vw,620px)] w-full"
                      fitContainer
                      {...chartHoverProps("crossplot")}
                      onPointClick={(points) => openProcessingSelectionEditor("crossplot", points, false)}
                      onSelection={(points) => openProcessingSelectionEditor("crossplot", points, true)}
                    />
                  </CardContent>
                </Card>
                </div>
              </div>

              <Card className="overflow-hidden">
                <CardHeader className="gap-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <CardTitle>Linearity Correction</CardTitle>
                    <span className="rounded-md bg-stone-50 px-3 py-1.5 text-sm text-stone-700 ring-1 ring-stone-200">
                      Basis: {selectedLinearityBasisLabel}
                    </span>
                  </div>
                  <CardDescription>
                    Standards-only linearity fits built from the active precision date window and the selected basis used during calibration.
                  </CardDescription>
                </CardHeader>
                <CardContent className="bg-stone-200 p-0">
                  <div className="grid min-w-0 gap-px 2xl:grid-cols-2">
                    <div className="min-w-0 bg-white">
                    <div className="border-b border-stone-200 px-3 py-2 text-xs font-semibold text-stone-700">d13C · Raw</div>
                    <PlotlyChart
                      figure={withLinearityFigure(withCalibrationPreview(displayedWorkspace.linearity_figures.d13_raw, "linearity|d13_raw"))}
                      className="h-[420px] w-full"
                      fitContainer
                      {...chartHoverProps("linearity|d13_raw")}
                      onPointClick={(points) => openProcessingSelectionEditor("linearity|d13_raw", points, false)}
                      onSelection={(points) => openProcessingSelectionEditor("linearity|d13_raw", points, true)}
                    />
                    </div>
                    <div className="min-w-0 bg-white">
                    <div className="border-b border-stone-200 px-3 py-2 text-xs font-semibold text-stone-700">d13C · Linearity corrected</div>
                    <PlotlyChart
                      figure={withLinearityFigure(withCalibrationPreview(displayedWorkspace.linearity_figures.d13_corrected, "linearity|d13_corrected"))}
                      className="h-[420px] w-full"
                      fitContainer
                      {...chartHoverProps("linearity|d13_corrected")}
                      onPointClick={(points) => openProcessingSelectionEditor("linearity|d13_corrected", points, false)}
                      onSelection={(points) => openProcessingSelectionEditor("linearity|d13_corrected", points, true)}
                    />
                    </div>
                    <div className="min-w-0 bg-white">
                    <div className="border-b border-stone-200 px-3 py-2 text-xs font-semibold text-stone-700">d18O · Raw</div>
                    <PlotlyChart
                      figure={withLinearityFigure(withCalibrationPreview(displayedWorkspace.linearity_figures.d18_raw, "linearity|d18_raw"))}
                      className="h-[420px] w-full"
                      fitContainer
                      {...chartHoverProps("linearity|d18_raw")}
                      onPointClick={(points) => openProcessingSelectionEditor("linearity|d18_raw", points, false)}
                      onSelection={(points) => openProcessingSelectionEditor("linearity|d18_raw", points, true)}
                    />
                    </div>
                    <div className="min-w-0 bg-white">
                    <div className="border-b border-stone-200 px-3 py-2 text-xs font-semibold text-stone-700">d18O · Linearity corrected</div>
                    <PlotlyChart
                      figure={withLinearityFigure(withCalibrationPreview(displayedWorkspace.linearity_figures.d18_corrected, "linearity|d18_corrected"))}
                      className="h-[420px] w-full"
                      fitContainer
                      {...chartHoverProps("linearity|d18_corrected")}
                      onPointClick={(points) => openProcessingSelectionEditor("linearity|d18_corrected", points, false)}
                      onSelection={(points) => openProcessingSelectionEditor("linearity|d18_corrected", points, true)}
                    />
                    </div>
                  </div>
                </CardContent>
              </Card>

              <div className="space-y-6">
                {displayedWorkspace.standard_sections.map((section) => (
                  <section
                    key={section.standard}
                    className="rounded-lg border border-stone-200 bg-white shadow-sm"
                    aria-label={`${section.standard} calibration details`}
                  >
                    <h2 className="px-4 py-3 text-base font-semibold text-stone-900">
                      {section.standard}
                    </h2>
                    <div className="space-y-6 p-6 pt-0">
                      <div className="grid gap-6">
                        <Card className="flex flex-col overflow-hidden">
                          <CardHeader>
                            <CardTitle className="text-base">d13C Outlier Trace</CardTitle>
                          </CardHeader>
                          <CardContent className="min-h-0 flex-1 p-0">
                            <PlotlyChart
                              figure={hideCalibrationEmbeddedColorbars(withColorScaleRange(withCalibrationPreview(section.d13_figure, `${section.standard}|d13C`)))}
                              className="h-[460px] w-full"
                              fitContainer
                              {...chartHoverProps(`${section.standard}|d13C`)}
                              onPointClick={(points) => openProcessingSelectionEditor(`${section.standard}|d13C`, points, false)}
                              onSelection={(points) => openProcessingSelectionEditor(`${section.standard}|d13C`, points, true)}
                            />
                          </CardContent>
                        </Card>
                        <Card className="flex flex-col overflow-hidden">
                          <CardHeader>
                            <CardTitle className="text-base">d18O Outlier Trace</CardTitle>
                          </CardHeader>
                          <CardContent className="min-h-0 flex-1 p-0">
                            <PlotlyChart
                              figure={hideCalibrationEmbeddedColorbars(withColorScaleRange(withCalibrationPreview(section.d18_figure, `${section.standard}|d18O`)))}
                              className="h-[460px] w-full"
                              fitContainer
                              {...chartHoverProps(`${section.standard}|d18O`)}
                              onPointClick={(points) => openProcessingSelectionEditor(`${section.standard}|d18O`, points, false)}
                              onSelection={(points) => openProcessingSelectionEditor(`${section.standard}|d18O`, points, true)}
                            />
                          </CardContent>
                        </Card>
                      </div>
                      <div className="grid gap-6 2xl:grid-cols-2">
                        <details className="rounded-lg border border-stone-200 bg-white shadow-sm">
                          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-stone-900">
                            <ChevronRight className="h-4 w-4 shrink-0 text-blue-600" aria-hidden="true" />
                            <span>d13C Outliers ({section.d13_outliers.length})</span>
                          </summary>
                          <div className="px-6 pb-6">
                            <DataTable rows={section.d13_outliers} emptyLabel="No d13C outliers for this standard." />
                          </div>
                        </details>
                        <details className="rounded-lg border border-stone-200 bg-white shadow-sm">
                          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-stone-900">
                            <ChevronRight className="h-4 w-4 shrink-0 text-blue-600" aria-hidden="true" />
                            <span>d18O Outliers ({section.d18_outliers.length})</span>
                          </summary>
                          <div className="px-6 pb-6">
                            <DataTable rows={section.d18_outliers} emptyLabel="No d18O outliers for this standard." />
                          </div>
                        </details>
                      </div>
                    </div>
                  </section>
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
