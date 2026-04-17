"use client";

import dynamic from "next/dynamic";
import { useMemo } from "react";

import { cn } from "@/lib/utils";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

export type PlotlyPoint = {
  x?: number | string | null;
  y?: number | string | null;
  z?: number | string | null;
  customdata?: unknown;
  curveNumber?: number;
  pointNumber?: number;
};

type PlotlyChartProps = {
  figure?: Record<string, unknown>;
  className?: string;
  onPointClick?: (points: PlotlyPoint[]) => void;
  onSelection?: (points: PlotlyPoint[]) => void;
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
    const text = (value as { text?: unknown }).text;
    if (typeof text === "string") {
      return text;
    }
  }
  return "";
}

function isD18Label(label: string): boolean {
  const normalized = label.toLowerCase();
  return normalized.includes("d18o") || normalized.includes("18o");
}

function applyD18AxisInversion(layout: Record<string, unknown>) {
  for (const [axisKey, axisValue] of Object.entries(layout)) {
    if (!/^yaxis\d*$/.test(axisKey)) {
      continue;
    }
    if (!axisValue || typeof axisValue !== "object") {
      continue;
    }
    const axis = { ...(axisValue as Record<string, unknown>) };
    const label = titleText(axis.title);
    if (!isD18Label(label)) {
      continue;
    }
    axis.autorange = "reversed";
    layout[axisKey] = axis;
  }
  for (const [sceneKey, sceneValue] of Object.entries(layout)) {
    if (!/^scene\d*$/.test(sceneKey)) {
      continue;
    }
    if (!sceneValue || typeof sceneValue !== "object") {
      continue;
    }
    const scene = { ...(sceneValue as Record<string, unknown>) };
    if (!scene.yaxis || typeof scene.yaxis !== "object") {
      continue;
    }
    const yAxis = { ...(scene.yaxis as Record<string, unknown>) };
    const label = titleText(yAxis.title);
    if (!isD18Label(label)) {
      continue;
    }
    yAxis.autorange = "reversed";
    scene.yaxis = yAxis;
    layout[sceneKey] = scene;
  }
}

export function PlotlyChart({ figure, className, onPointClick, onSelection }: PlotlyChartProps) {
  const preparedFigure = useMemo(() => {
    if (!figure || Object.keys(figure).length === 0) {
      return null;
    }
    const safeFigure = deepClone(figure);
    const layout = typeof safeFigure.layout === "object" && safeFigure.layout ? { ...(safeFigure.layout as Record<string, unknown>) } : {};
    applyD18AxisInversion(layout);
    const hoverLabel = layout.hoverlabel && typeof layout.hoverlabel === "object" ? { ...(layout.hoverlabel as Record<string, unknown>) } : {};
    hoverLabel.namelength = -1;
    layout.hoverlabel = hoverLabel;
    layout.autosize = true;
    delete (layout as { width?: unknown }).width;
    delete (layout as { height?: unknown }).height;
    return {
      data: (safeFigure.data as never[]) ?? [],
      layout: layout as never,
    };
  }, [figure]);

  if (!preparedFigure) {
    return <div className="rounded-lg border border-dashed border-stone-300 p-6 text-sm text-stone-500">No chart data yet.</div>;
  }
  return (
    <div className={cn("min-w-0 w-full overflow-hidden", className)}>
      <Plot
        data={preparedFigure.data}
        layout={preparedFigure.layout}
        config={{ responsive: true }}
        useResizeHandler
        className="h-full w-full max-w-full"
        style={{ width: "100%", height: "100%" }}
        onClick={(event: { points?: PlotlyPoint[] }) => onPointClick?.(event.points ?? [])}
        onSelected={(event: { points?: PlotlyPoint[] } | undefined) => onSelection?.(event?.points ?? [])}
      />
    </div>
  );
}
