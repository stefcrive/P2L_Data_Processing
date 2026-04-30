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

export type PlotlyHoverPayload = {
  points: PlotlyPoint[];
  clientX: number;
  clientY: number;
};

type PlotlyChartProps = {
  figure?: Record<string, unknown>;
  className?: string;
  fitContainer?: boolean;
  onPointClick?: (points: PlotlyPoint[]) => void;
  onSelection?: (points: PlotlyPoint[]) => void;
  onPointHover?: (payload: PlotlyHoverPayload) => void;
  onHoverEnd?: () => void;
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

function collectAxisTitleTokens(layout: Record<string, unknown>): string[] {
  const tokens: string[] = [];
  const sortedEntries = Object.entries(layout).sort(([a], [b]) => a.localeCompare(b));
  for (const [key, value] of sortedEntries) {
    if (!value || typeof value !== "object") {
      continue;
    }
    if (/^[xy]axis\d*$/.test(key)) {
      const axis = value as Record<string, unknown>;
      const label = titleText(axis.title);
      if (label) {
        tokens.push(`${key}:${label}`);
      }
      continue;
    }
    if (!/^scene\d*$/.test(key)) {
      continue;
    }
    const scene = value as Record<string, unknown>;
    for (const axisKey of ["xaxis", "yaxis", "zaxis"]) {
      const axisValue = scene[axisKey];
      if (!axisValue || typeof axisValue !== "object") {
        continue;
      }
      const axis = axisValue as Record<string, unknown>;
      const label = titleText(axis.title);
      if (label) {
        tokens.push(`${key}.${axisKey}:${label}`);
      }
    }
  }
  return tokens;
}

function buildDefaultUiRevision(data: unknown, layout: Record<string, unknown>): string {
  const traceTokens = Array.isArray(data)
    ? data.map((trace, index) => {
        if (trace && typeof trace === "object") {
          const traceType = (trace as { type?: unknown }).type;
          if (typeof traceType === "string" && traceType) {
            return traceType;
          }
        }
        return `trace${index}`;
      })
    : ["no-data"];
  const axisTokens = collectAxisTitleTokens(layout);
  const plotTitle = titleText(layout.title);
  return ["persist-ui", plotTitle, ...axisTokens, ...traceTokens].join("|");
}

export function PlotlyChart({ figure, className, fitContainer = false, onPointClick, onSelection, onPointHover, onHoverEnd }: PlotlyChartProps) {
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
    const hasExplicitWidth = typeof (layout as { width?: unknown }).width === "number";
    const hasExplicitHeight = typeof (layout as { height?: unknown }).height === "number";
    if (fitContainer) {
      delete (layout as { width?: unknown }).width;
      delete (layout as { height?: unknown }).height;
      layout.autosize = true;
    } else if (!hasExplicitWidth && !hasExplicitHeight && typeof (layout as { autosize?: unknown }).autosize !== "boolean") {
      layout.autosize = true;
    }
    if (typeof (layout as { uirevision?: unknown }).uirevision === "undefined") {
      layout.uirevision = buildDefaultUiRevision(safeFigure.data, layout);
    }
    return {
      data: (safeFigure.data as never[]) ?? [],
      layout: layout as never,
      useResizeHandler: fitContainer || !hasExplicitHeight,
    };
  }, [figure, fitContainer]);

  if (!preparedFigure) {
    return <div className="rounded-lg border border-dashed border-stone-300 p-6 text-sm text-stone-500">No chart data yet.</div>;
  }
  const shouldUseContainerHeight = preparedFigure.useResizeHandler;
  const hoverHandlers =
    onPointHover || onHoverEnd
      ? {
          onHover: (
            event:
              | {
                  points?: PlotlyPoint[];
                  event?: { clientX?: number; clientY?: number; x?: number; y?: number };
                }
              | undefined,
          ) => {
            if (!onPointHover) {
              return;
            }
            const points = event?.points ?? [];
            if (!points.length) {
              return;
            }
            const clientX = event?.event?.clientX ?? event?.event?.x;
            const clientY = event?.event?.clientY ?? event?.event?.y;
            if (typeof clientX !== "number" || typeof clientY !== "number") {
              return;
            }
            onPointHover({ points, clientX, clientY });
          },
          onUnhover: () => onHoverEnd?.(),
        }
      : {};
  return (
    <div className={cn("min-w-0 w-full", className)}>
      <Plot
        data={preparedFigure.data}
        layout={preparedFigure.layout}
        config={{ responsive: true }}
        useResizeHandler={preparedFigure.useResizeHandler}
        className={cn("w-full max-w-full", shouldUseContainerHeight ? "h-full" : "")}
        style={shouldUseContainerHeight ? { width: "100%", height: "100%" } : { width: "100%" }}
        onClick={(event: { points?: PlotlyPoint[] }) => onPointClick?.(event.points ?? [])}
        onSelected={(event: { points?: PlotlyPoint[] } | undefined) => onSelection?.(event?.points ?? [])}
        {...hoverHandlers}
      />
    </div>
  );
}
