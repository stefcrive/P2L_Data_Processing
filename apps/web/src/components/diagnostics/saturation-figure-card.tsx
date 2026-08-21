"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, Info } from "lucide-react";

import { PlotlyChart } from "@/components/charts/lazy-plotly-chart";
import { formatScientificText } from "@/lib/scientific-notation";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";

export type SaturationAxisKey = "cycle" | "samp44" | "ref44" | "mean44" | "mismatch" | "d13C" | "d18O";
export type SaturationColorAxisKey = SaturationAxisKey;

export const SATURATION_COLOR_AXIS_OPTIONS: Array<{ value: SaturationAxisKey; label: string }> = [
  { value: "cycle", label: "Cycle" },
  { value: "samp44", label: "Samp44" },
  { value: "ref44", label: "Ref44" },
  { value: "mean44", label: "Mean44" },
  { value: "mismatch", label: "Mismatch" },
  { value: "d13C", label: "δ¹³C cycle signal" },
  { value: "d18O", label: "δ¹⁸O cycle signal" },
];

const SATURATION_AXIS_HELP_TEXT =
  "Cycle: valid cycle number. Samp44: sample m/z 44 intensity. Ref44: reference-gas m/z 44 intensity. Mean44: average of Samp44 and Ref44. Mismatch: symmetric Samp-Ref mismatch. The isotope axes use exported cycle values when available; when the export repeats only a run mean, they use an internal rare/44 sample-to-reference signal proxy centered on that mean.";

type SaturationFigureCardProps = {
  chartKey: string;
  title: string;
  description?: string;
  figure?: Record<string, unknown>;
  colorAxis: SaturationAxisKey;
  yAxis: SaturationAxisKey;
  collapsibleLegend?: boolean;
  legendCollapsed?: boolean;
  verticallyResizable?: boolean;
  deferRenderMs?: number;
};

function deepClone<T>(value: T): T {
  if (typeof structuredClone === "function") {
    return structuredClone(value);
  }
  return JSON.parse(JSON.stringify(value)) as T;
}

function titleText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (value && typeof value === "object") {
    const text = (value as Record<string, unknown>).text;
    return typeof text === "string" ? text : "";
  }
  return "";
}

function setTitle(value: string) {
  return { text: value };
}

function vector(value: unknown): unknown[] | null {
  if (Array.isArray(value)) {
    return value;
  }
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record)
    .filter((key) => /^\d+$/.test(key))
    .map(Number)
    .sort((left, right) => left - right);
  if (!keys.length) {
    return null;
  }
  return keys.map((key) => record[String(key)]);
}

function numericVector(value: unknown): number[] | null {
  const values = vector(value);
  if (!values?.length) {
    return null;
  }
  const parsed = values.map((item) => Number(item));
  return parsed.every((item) => Number.isFinite(item)) ? parsed : null;
}

function axisLabel(axis: SaturationAxisKey): string {
  return SATURATION_COLOR_AXIS_OPTIONS.find((option) => option.value === axis)?.label ?? "Color axis";
}

function setColorbarTitle(marker: Record<string, unknown>, title: string): Record<string, unknown> {
  const colorbar = marker.colorbar && typeof marker.colorbar === "object" ? { ...(marker.colorbar as Record<string, unknown>) } : {};
  colorbar.title = setTitle(title);
  return { ...marker, colorbar };
}

function customDataRows(customdata: unknown): unknown[][] | null {
  const rows = vector(customdata);
  if (!rows?.length) {
    return null;
  }
  return rows.map((row) => (Array.isArray(row) ? row : row == null ? [] : [row]));
}

function customDataNumber(rows: unknown[][] | null, rowIndex: number, itemIndex: number): number {
  const value = rows?.[rowIndex]?.[itemIndex];
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

function valuesForAxis(customdata: unknown, axis: SaturationAxisKey, fallbackLength: number): number[] | null {
  const rows = customDataRows(customdata);
  if (!rows?.length) {
    return null;
  }
  const customDataIndex: Record<SaturationAxisKey, number> = {
    cycle: 0,
    samp44: 1,
    ref44: 2,
    mean44: 3,
    mismatch: 4,
    d13C: 5,
    d18O: 6,
  };
  const values = Array.from({ length: fallbackLength }, (_, index) => customDataNumber(rows, index, customDataIndex[axis]));
  return values.every((value) => Number.isFinite(value)) ? values : null;
}

function mergeNormalCustomData(customdata: unknown, selectedColor: number[]): unknown[] {
  const rows = customDataRows(customdata);
  return selectedColor.map((colorValue, index) => [colorValue, ...(rows?.[index] ?? [])]);
}

function mergeSwappedCustomData(customdata: unknown, originalX: number[]): unknown[] {
  const rows = customDataRows(customdata);
  return originalX.map((xValue, index) => [xValue, ...(rows?.[index] ?? [])]);
}

function sameNumericValues(left: number[] | null, right: number[] | null): boolean {
  if (!left || !right || left.length !== right.length) {
    return false;
  }
  return left.every((value, index) => Math.abs(value - right[index]) < 1e-9);
}

function titleMatchesAxis(title: string, axis: SaturationAxisKey): boolean {
  const normalizedTitle = title.toLowerCase().replace(/[^a-z0-9]/g, "");
  const normalizedAxis = axisLabel(axis).toLowerCase().replace(/[^a-z0-9]/g, "");
  return normalizedTitle === normalizedAxis || normalizedTitle.includes(normalizedAxis);
}

function formatRangeValue(value: number): string {
  if (!Number.isFinite(value)) {
    return "N/A";
  }
  const absValue = Math.abs(value);
  if ((absValue >= 1000 || absValue < 0.01) && absValue !== 0) {
    return value.toExponential(2);
  }
  return Number(value.toPrecision(4)).toString();
}

function normalHoverTemplate(xTitle: string, yTitle: string, colorTitle: string): string {
  return [
    `${xTitle}: %{x:.4g}`,
    `${yTitle}: %{y:.4g}`,
    `${colorTitle}: %{customdata[0]:.4g}`,
    "Cycle: %{customdata[1]:.0f}",
    "Samp44: %{customdata[2]:.4g} V",
    "Ref44: %{customdata[3]:.4g} V",
    "Mean44: %{customdata[4]:.4g} V",
    "Mismatch: %{customdata[5]:.4g}",
    "δ¹³C: %{customdata[6]:.4g}",
    "δ¹⁸O: %{customdata[7]:.4g}",
    "<extra></extra>",
  ].join("<br>");
}

function swappedHoverTemplate(colorTitle: string, originalXTitle: string, yTitle: string): string {
  return [
    `${colorTitle}: %{x:.4g}`,
    `${yTitle}: %{y:.4g}`,
    `${originalXTitle}: %{customdata[0]:.4g}`,
    "Cycle: %{customdata[1]:.0f}",
    "Samp44: %{customdata[2]:.4g} V",
    "Ref44: %{customdata[3]:.4g} V",
    "Mean44: %{customdata[4]:.4g} V",
    "Mismatch: %{customdata[5]:.4g}",
    "δ¹³C: %{customdata[6]:.4g}",
    "δ¹⁸O: %{customdata[7]:.4g}",
    "<extra></extra>",
  ].join("<br>");
}

function prepareFigure(
  figure: Record<string, unknown> | undefined,
  colorAxis: SaturationAxisKey,
  yAxis: SaturationAxisKey,
  swapped: boolean,
): Record<string, unknown> | undefined {
  if (!figure || !Array.isArray(figure.data) || !figure.data.length) {
    return figure;
  }
  const cloned = deepClone(figure);
  const data = Array.isArray(cloned.data) ? (cloned.data as Array<Record<string, unknown>>) : [];
  const validTrace = data[0];
  if (!validTrace || typeof validTrace !== "object") {
    return figure;
  }
  const marker = validTrace.marker && typeof validTrace.marker === "object" ? (validTrace.marker as Record<string, unknown>) : {};
  const originalX = numericVector(validTrace.x);
  const originalY = numericVector(validTrace.y);
  if (!originalX?.length) {
    return figure;
  }
  const layout = cloned.layout && typeof cloned.layout === "object" ? { ...(cloned.layout as Record<string, unknown>) } : {};
  const xaxis = layout.xaxis && typeof layout.xaxis === "object" ? { ...(layout.xaxis as Record<string, unknown>) } : {};
  const yaxis = layout.yaxis && typeof layout.yaxis === "object" ? { ...(layout.yaxis as Record<string, unknown>) } : {};
  const originalXTitle = titleText(xaxis.title) || "Original x axis";
  const originalYTitle = titleText(yaxis.title) || "Value";
  const selectedYTitle = axisLabel(yAxis);
  const selectedColorTitle = axisLabel(colorAxis);
  const selectedColor = valuesForAxis(validTrace.customdata, colorAxis, originalX.length);
  const selectedY = valuesForAxis(validTrace.customdata, yAxis, originalX.length) ?? (titleMatchesAxis(originalYTitle, yAxis) ? originalY : null);
  if (!selectedColor?.length || selectedColor.length !== originalX.length) {
    return figure;
  }
  if (!selectedY?.length || selectedY.length !== originalX.length) {
    return figure;
  }
  const keepModelTraces = sameNumericValues(selectedY, originalY);
  yaxis.title = setTitle(selectedYTitle);
  yaxis.tickformat = ".3f";
  layout.yaxis = yaxis;
  if (!swapped) {
    const recoloredTrace = {
      ...validTrace,
      y: selectedY,
      marker: setColorbarTitle({ ...marker, color: selectedColor, showscale: false }, selectedColorTitle),
      customdata: mergeNormalCustomData(validTrace.customdata, selectedColor),
      hovertemplate: normalHoverTemplate(originalXTitle, selectedYTitle, selectedColorTitle),
    };
    if (!keepModelTraces) {
      layout.annotations = [];
    }
    layout.uirevision = `${String(layout.uirevision ?? "saturation-chart")}:color-${colorAxis}:y-${yAxis}`;
    return {
      ...cloned,
      data: keepModelTraces ? [recoloredTrace, ...data.slice(1)] : [recoloredTrace],
      layout,
    };
  }
  const swappedMarker = setColorbarTitle({ ...marker, color: selectedColor, showscale: false }, selectedColorTitle);
  const swappedTrace = {
    ...validTrace,
    x: selectedColor,
    y: selectedY,
    marker: swappedMarker,
    customdata: mergeSwappedCustomData(validTrace.customdata, originalX),
    hovertemplate: swappedHoverTemplate(selectedColorTitle, originalXTitle, selectedYTitle),
    name: `${validTrace.name ?? "Valid cycles"} (swapped)`,
  };
  xaxis.title = setTitle(selectedColorTitle);
  layout.xaxis = xaxis;
  layout.annotations = [];
  layout.uirevision = `${String(layout.uirevision ?? "saturation-chart")}:axis-swapped-${colorAxis}:y-${yAxis}`;
  return {
    ...cloned,
    data: [swappedTrace],
    layout,
  };
}

function valuesForFigureAxis(figure: Record<string, unknown> | undefined, axis: SaturationAxisKey): number[] {
  if (!figure || !Array.isArray(figure.data) || !figure.data.length) {
    return [];
  }
  const trace = figure.data[0] as Record<string, unknown>;
  const originalX = numericVector(trace.x);
  if (!originalX?.length) {
    return [];
  }
  return valuesForAxis(trace.customdata, axis, originalX.length) ?? [];
}

export function saturationAxisDefaultFromDiagnostics(diagnostics?: { target?: Record<string, unknown> }): SaturationAxisKey {
  const isotopeKey = String(diagnostics?.target?.isotope_key ?? "").trim();
  return isotopeKey === "d18O" ? "d18O" : "d13C";
}

export function SaturationSharedColorbar({
  figures,
  colorAxis,
  orientation = "horizontal",
}: {
  figures: Array<Record<string, unknown> | undefined>;
  colorAxis: SaturationAxisKey;
  orientation?: "horizontal" | "vertical";
}) {
  const range = useMemo(() => {
    const values = figures.flatMap((figure) => valuesForFigureAxis(figure, colorAxis)).filter((value) => Number.isFinite(value));
    if (!values.length) {
      return null;
    }
    return { min: Math.min(...values), max: Math.max(...values) };
  }, [colorAxis, figures]);

  if (orientation === "vertical") {
    return (
      <div className="flex min-h-[260px] flex-col items-center justify-center gap-2 text-xs">
        <div className="text-center text-stone-600">{axisLabel(colorAxis)} scale</div>
        <div className="flex items-center gap-2">
          <div className="flex h-44 w-2 flex-col justify-between text-[10px] tabular-nums text-stone-500">
            <span>{range ? formatRangeValue(range.max) : "N/A"}</span>
            <span>{range ? formatRangeValue(range.min) : "N/A"}</span>
          </div>
          <div className="h-44 w-2 rounded-full border border-stone-300 bg-[linear-gradient(0deg,#440154_0%,#3b528b_25%,#21918c_50%,#5ec962_75%,#fde725_100%)]" />
        </div>
      </div>
    );
  }
  return (
    <div className="w-full max-w-[280px] min-w-[180px] text-xs">
      <div className="mb-1 text-stone-600">{formatScientificText(axisLabel(colorAxis))} scale</div>
      <div className="h-2 rounded-full border border-stone-300 bg-[linear-gradient(90deg,#440154_0%,#3b528b_25%,#21918c_50%,#5ec962_75%,#fde725_100%)]" />
      <div className="mt-0.5 flex justify-between gap-3 text-[10px] tabular-nums text-stone-500">
        <span>{range ? formatRangeValue(range.min) : "N/A"}</span>
        <span>{range ? formatRangeValue(range.max) : "N/A"}</span>
      </div>
    </div>
  );
}

export function SaturationAxisHelpTooltip({ label }: { label: string }) {
  return (
    <span className="mb-1 flex items-center gap-1.5 text-stone-700">
      <span>{formatScientificText(label)}</span>
      <Tooltip label={SATURATION_AXIS_HELP_TEXT} align="start" contentClassName="w-96">
        <span
          tabIndex={0}
          className="inline-flex h-5 w-5 items-center justify-center rounded-md text-stone-500 hover:bg-stone-100 hover:text-stone-700 focus:outline-none focus:ring-2 focus:ring-stone-300"
        >
          <Info className="h-3.5 w-3.5" />
          <span className="sr-only">{formatScientificText(label)} parameter help</span>
        </span>
      </Tooltip>
    </span>
  );
}

export function SaturationFigureCard({
  chartKey,
  title,
  description,
  figure,
  colorAxis,
  yAxis,
  collapsibleLegend = false,
  legendCollapsed = false,
  verticallyResizable = false,
  deferRenderMs = 0,
}: SaturationFigureCardProps) {
  const [swapped, setSwapped] = useState(false);
  const displayedFigure = useMemo(() => prepareFigure(figure, colorAxis, yAxis, swapped), [colorAxis, figure, swapped, yAxis]);
  const swappedMessage = `X is set to ${axisLabel(colorAxis)}; fit and prediction traces are hidden in this view.`;

  return (
    <div className="rounded-lg border border-stone-200 p-2">
      <div className="flex flex-wrap items-start justify-end gap-2 px-1 pb-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setSwapped((current) => !current)}
          title="Swap the valid-cycle dots' x axis with the variable currently used for color."
        >
          {swapped ? "Original axes" : "Swap x/color"}
        </Button>
      </div>
      <PlotlyChart
        key={`${chartKey}:${colorAxis}:${yAxis}:${swapped ? "swapped" : "normal"}`}
        figure={displayedFigure}
        className="h-[320px] w-full"
        fitContainer
        collapsibleLegend={collapsibleLegend}
        legendCollapsed={legendCollapsed}
        verticallyResizable={verticallyResizable}
        deferRenderMs={deferRenderMs}
      />
      <div className="flex min-w-0 items-center gap-2 px-1 pt-2">
        {description ? (
          <Tooltip label={description} align="start" contentClassName="w-96">
            <button type="button" className="truncate text-left text-sm font-medium text-stone-700 underline decoration-stone-300 underline-offset-4">
              {formatScientificText(title)}
            </button>
          </Tooltip>
        ) : (
          <div className="truncate text-sm font-medium text-stone-700">{formatScientificText(title)}</div>
        )}
        {swapped ? (
          <Tooltip label={swappedMessage} align="end" contentClassName="w-80">
            <button type="button" className="inline-flex h-7 w-7 items-center justify-center rounded-md text-amber-700 hover:bg-amber-50">
              <AlertTriangle className="h-4 w-4" />
              <span className="sr-only">{swappedMessage}</span>
            </button>
          </Tooltip>
        ) : null}
      </div>
    </div>
  );
}
