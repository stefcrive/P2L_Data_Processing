"use client";

import { useTranslation } from "@/components/layout/language-provider";
import { useMetrologyConsultation, MetrologyChartAppearance, MetrologyChartHeight } from "@/components/metrology/consultation-context";
import { useLanguage } from "@/components/layout/language-provider";
import dynamic from "next/dynamic";
import { createPortal } from "react-dom";
import { GripHorizontal, HelpCircle, SlidersHorizontal, ChevronDown } from "lucide-react";
import {
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";

import { alignedIsotopeAxes } from "@/lib/plotly-order";
import { cn } from "@/lib/utils";
import { formatPlotlyDisplayText } from "@/lib/scientific-notation";

const Plot = dynamic(
  async () => {
    const [{ default: createPlotlyComponent }, { default: Plotly }] = await Promise.all([
      import("react-plotly.js/factory"),
      import("@/lib/plotly-core"),
    ]);
    return createPlotlyComponent(Plotly);
  },
  { ssr: false },
);

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

export type PlotlyChartProps = {
  figure?: Record<string, unknown>;
  className?: string;
  fitContainer?: boolean;
  collapsibleLegend?: boolean;
  legendCollapsed?: boolean;
  verticallyResizable?: boolean;
  minHeight?: number;
  maxHeight?: number;
  deferRenderMs?: number;
  lazy?: boolean;
  initialHeight?: number;
  legendFontSize?: number;
  uiRevision?: string;
  onPointClick?: (points: PlotlyPoint[]) => void;
  onSelection?: (points: PlotlyPoint[]) => void;
  onPointHover?: (payload: PlotlyHoverPayload) => void;
  onHoverEnd?: () => void;
};

const persistedViewports = new Map<string, Record<string, unknown>>();

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

function panelTitle(value: unknown): string {
  return titleText(value).split(/<br\s*\/?>(?:\s*)/i)[0].replace(/<[^>]*>/g, "").replace(/&amp;/g, "&").replace(/&nbsp;/g, " ");
}

type PlotlyGraphDiv = HTMLDivElement & {
  on?: (name:string,handler:(event:unknown)=>void)=>void;
  removeListener?: (name:string,handler:(event:unknown)=>void)=>void;
  _fullLayout?: {
    meta?: { equalStandardScale?: boolean; standardMeanOffset?: number };
    yaxis?: { dtick?: unknown; range?: unknown };
    yaxis2?: { dtick?: unknown; overlaying?: unknown; range?: unknown; tickmode?: unknown; title?: unknown };
  };
};

type PlotlyRelayoutApi = {
  relayout: (graphDiv: PlotlyGraphDiv, update: Record<string, unknown>) => Promise<unknown>;
};

type PlotlyResizeApi = {
  Plots: {
    resize: (graphDiv: PlotlyGraphDiv) => Promise<unknown> | void;
  };
};

function numericAxisRange(value: unknown): [number, number] | null {
  if (!Array.isArray(value) || value.length !== 2) {
    return null;
  }
  const start = Number(value[0]);
  const end = Number(value[1]);
  return Number.isFinite(start) && Number.isFinite(end) && start !== end ? [start, end] : null;
}

function syncStandardAxisScale(
  graphDiv: PlotlyGraphDiv | undefined,
  setProgrammaticRelayout?: (active: boolean) => void,
) {
  const primaryAxis = graphDiv?._fullLayout?.yaxis;
  const standardAxis = graphDiv?._fullLayout?.yaxis2;
  if (
    !graphDiv ||
    !primaryAxis ||
    !standardAxis ||
    standardAxis.overlaying !== "y" ||
    !(graphDiv._fullLayout?.meta?.equalStandardScale || titleText(standardAxis.title).startsWith("Standard "))
  ) {
    return;
  }

  const primaryTickInterval = Number(primaryAxis.dtick);
  if (!Number.isFinite(primaryTickInterval) || primaryTickInterval <= 0) {
    return;
  }
  const primaryRange = numericAxisRange(primaryAxis.range);
  const standardRange = numericAxisRange(standardAxis.range);
  if (!primaryRange || !standardRange) {
    return;
  }

  const primarySpan = Math.abs(primaryRange[1] - primaryRange[0]);
  const standardCenter = (primaryRange[0] + primaryRange[1]) / 2 + (graphDiv._fullLayout?.meta?.standardMeanOffset ?? ((standardRange[0] + standardRange[1] - primaryRange[0] - primaryRange[1]) / 2));
  const reversed = primaryRange[1] < primaryRange[0];
  const targetRange: [number, number] = reversed
    ? [standardCenter + primarySpan / 2, standardCenter - primarySpan / 2]
    : [standardCenter - primarySpan / 2, standardCenter + primarySpan / 2];
  const rangeAlreadyMatched =
    Math.abs(standardRange[0] - targetRange[0]) < 1e-9 && Math.abs(standardRange[1] - targetRange[1]) < 1e-9;
  if (standardAxis.tickmode === "linear" && Number(standardAxis.dtick) === primaryTickInterval && rangeAlreadyMatched) {
    return;
  }

  setProgrammaticRelayout?.(true);
  void import("@/lib/plotly-core").then(({ default: plotlyModule }) => {
    if (!graphDiv.isConnected) { setProgrammaticRelayout?.(false); return; }
    const Plotly = plotlyModule as unknown as PlotlyRelayoutApi;
    void Plotly
      .relayout(graphDiv, {
        "yaxis2.tickmode": "linear",
        "yaxis2.dtick": primaryTickInterval,
        // Both means occupy the same pixel; isotope units per pixel remain equal.
        "yaxis2.range": targetRange,
      })
      .finally(() => setProgrammaticRelayout?.(false));
  });
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

function compactColorbar(value: unknown): Record<string, unknown> {
  const colorbar = value && typeof value === "object" ? { ...(value as Record<string, unknown>) } : {};
  const existingThickness = typeof colorbar.thickness === "number" ? colorbar.thickness : 10;
  const existingLength = typeof colorbar.len === "number" ? colorbar.len : 0.68;
  const tickfont = colorbar.tickfont && typeof colorbar.tickfont === "object" ? { ...(colorbar.tickfont as Record<string, unknown>) } : {};
  tickfont.size = Math.min(typeof tickfont.size === "number" ? tickfont.size : 9, 9);
  colorbar.tickfont = tickfont;
  colorbar.thickness = Math.min(existingThickness, 10);
  colorbar.len = Math.min(existingLength, 0.68);
  colorbar.xpad = Math.min(typeof colorbar.xpad === "number" ? colorbar.xpad : 3, 3);
  colorbar.ypad = Math.min(typeof colorbar.ypad === "number" ? colorbar.ypad : 2, 2);

  if (typeof colorbar.title === "string") {
    colorbar.title = { text: colorbar.title, font: { size: 10 } };
  } else if (colorbar.title && typeof colorbar.title === "object") {
    const title = { ...(colorbar.title as Record<string, unknown>) };
    const titleFont = title.font && typeof title.font === "object" ? { ...(title.font as Record<string, unknown>) } : {};
    titleFont.size = Math.min(typeof titleFont.size === "number" ? titleFont.size : 10, 10);
    title.font = titleFont;
    colorbar.title = title;
  }
  return colorbar;
}

function compactFigureColorbars(
  data: unknown[],
  layout: Record<string, unknown>,
): { data: unknown[]; hasColorbar: boolean } {
  let hasColorbar = false;
  const compactData = data.map((traceValue) => {
    if (!traceValue || typeof traceValue !== "object") {
      return traceValue;
    }
    const trace = { ...(traceValue as Record<string, unknown>) };
    if (trace.showscale !== false && trace.colorbar && typeof trace.colorbar === "object") {
      trace.colorbar = compactColorbar(trace.colorbar);
      hasColorbar = true;
    }
    if (trace.marker && typeof trace.marker === "object") {
      const marker = { ...(trace.marker as Record<string, unknown>) };
      if (marker.showscale !== false && marker.colorbar && typeof marker.colorbar === "object") {
        marker.colorbar = compactColorbar(marker.colorbar);
        hasColorbar = true;
      }
      trace.marker = marker;
    }
    return trace;
  });

  for (const [key, value] of Object.entries(layout)) {
    if (!/^coloraxis\d*$/.test(key) || !value || typeof value !== "object") {
      continue;
    }
    const colorAxis = { ...(value as Record<string, unknown>) };
    if (colorAxis.showscale !== false) {
      colorAxis.colorbar = compactColorbar(colorAxis.colorbar);
      hasColorbar = true;
    }
    layout[key] = colorAxis;
  }
  return { data: compactData, hasColorbar };
}

function reclaimHiddenLegendMargin(layout: Record<string, unknown>) {
  const legend = layout.legend && typeof layout.legend === "object" ? (layout.legend as Record<string, unknown>) : null;
  if (!legend) {
    return;
  }
  const y = typeof legend.y === "number" ? legend.y : null;
  const yAnchor = typeof legend.yanchor === "string" ? legend.yanchor : "auto";
  const legendIsAbovePlot = y != null && (y > 1 || (y >= 1 && yAnchor === "bottom"));
  const legendIsBelowPlot = y != null && (y < 0 || (y <= 0 && yAnchor === "top"));
  if (!legendIsAbovePlot && !legendIsBelowPlot) {
    return;
  }

  const margin = layout.margin && typeof layout.margin === "object" ? { ...(layout.margin as Record<string, unknown>) } : {};
  if (legendIsAbovePlot) {
    margin.t = Math.min(typeof margin.t === "number" ? margin.t : 100, 64);
  }
  if (legendIsBelowPlot) {
    margin.b = Math.min(typeof margin.b === "number" ? margin.b : 80, 56);
  }
  margin.autoexpand = true;
  layout.margin = margin;
}

function cloneRecord(value: Record<string, unknown>): Record<string, unknown> {
  if (typeof structuredClone === "function") {
    try {
      return structuredClone(value) as Record<string, unknown>;
    } catch {
      // Fall through to JSON cloning for plain Plotly relayout payloads.
    }
  }
  return JSON.parse(JSON.stringify(value)) as Record<string, unknown>;
}

function isViewportRelayoutKey(key: string): boolean {
  return (
    /^xaxis\d*\.(range(\[\d+\])?|autorange)$/.test(key) ||
    /^yaxis\d*\.(range(\[\d+\])?|autorange)$/.test(key) ||
    /^scene\d*\.camera$/.test(key) ||
    /^scene\d*\.[xyz]axis\.(range(\[\d+\])?|autorange)$/.test(key)
  );
}

function setNestedViewportValue(layout: Record<string, unknown>, key: string, value: unknown) {
  if (key.endsWith(".camera")) {
    const [sceneKey] = key.split(".");
    const scene = layout[sceneKey] && typeof layout[sceneKey] === "object" ? { ...(layout[sceneKey] as Record<string, unknown>) } : {};
    scene.camera = value;
    layout[sceneKey] = scene;
    return;
  }

  const rangeMatch = key.match(/^(xaxis\d*|yaxis\d*|scene\d*\.[xyz]axis)\.range\[(\d+)\]$/);
  if (rangeMatch) {
    const [, axisPath, rawIndex] = rangeMatch;
    const index = Number(rawIndex);
    const axis = axisRecordForPath(layout, axisPath);
    const range = Array.isArray(axis.range) ? [...axis.range] : [];
    range[index] = value;
    axis.range = range;
    axis.autorange = false;
    setAxisRecordForPath(layout, axisPath, axis);
    return;
  }

  const rangeArrayMatch = key.match(/^(xaxis\d*|yaxis\d*|scene\d*\.[xyz]axis)\.range$/);
  if (rangeArrayMatch) {
    const axisPath = rangeArrayMatch[1];
    const axis = axisRecordForPath(layout, axisPath);
    axis.range = value;
    axis.autorange = false;
    setAxisRecordForPath(layout, axisPath, axis);
    return;
  }

  const autorangeMatch = key.match(/^(xaxis\d*|yaxis\d*|scene\d*\.[xyz]axis)\.autorange$/);
  if (autorangeMatch) {
    const axisPath = autorangeMatch[1];
    const axis = axisRecordForPath(layout, axisPath);
    const nextAutorange = value === true && axis.autorange === "reversed" ? "reversed" : value;
    axis.autorange = nextAutorange;
    if (nextAutorange === true || nextAutorange === "reversed") {
      delete axis.range;
    }
    setAxisRecordForPath(layout, axisPath, axis);
  }
}

function axisRecordForPath(layout: Record<string, unknown>, axisPath: string): Record<string, unknown> {
  if (!axisPath.includes(".")) {
    return layout[axisPath] && typeof layout[axisPath] === "object" ? { ...(layout[axisPath] as Record<string, unknown>) } : {};
  }
  const [sceneKey, axisKey] = axisPath.split(".");
  const scene = layout[sceneKey] && typeof layout[sceneKey] === "object" ? (layout[sceneKey] as Record<string, unknown>) : {};
  return scene[axisKey] && typeof scene[axisKey] === "object" ? { ...(scene[axisKey] as Record<string, unknown>) } : {};
}

function setAxisRecordForPath(layout: Record<string, unknown>, axisPath: string, axis: Record<string, unknown>) {
  if (!axisPath.includes(".")) {
    layout[axisPath] = axis;
    return;
  }
  const [sceneKey, axisKey] = axisPath.split(".");
  const scene = layout[sceneKey] && typeof layout[sceneKey] === "object" ? { ...(layout[sceneKey] as Record<string, unknown>) } : {};
  scene[axisKey] = axis;
  layout[sceneKey] = scene;
}

function applyPersistedViewport(layout: Record<string, unknown>, viewport: Record<string, unknown> | undefined) {
  if (!viewport) {
    return;
  }
  for (const [key, value] of Object.entries(viewport)) {
    setNestedViewportValue(layout, key, value);
  }
}

function mergeViewportRelayout(current: Record<string, unknown>, update: Record<string, unknown>): Record<string, unknown> {
  const next = { ...current };
  for (const [key, value] of Object.entries(update)) {
    if (!isViewportRelayoutKey(key)) {
      continue;
    }
    next[key] = value;

    const rangePartMatch = key.match(/^(.+)\.range\[(\d+)\]$/);
    if (rangePartMatch) {
      const base = rangePartMatch[1];
      delete next[`${base}.autorange`];
    }

    const autorangeMatch = key.match(/^(.+)\.autorange$/);
    if (autorangeMatch && (value === true || value === "reversed")) {
      const base = autorangeMatch[1];
      delete next[`${base}.range`];
      delete next[`${base}.range[0]`];
      delete next[`${base}.range[1]`];
    }
  }
  return next;
}

export function PlotlyChart({
  figure: sourceFigure,
  className,
  fitContainer = false,
  collapsibleLegend = true,
  legendCollapsed = false,
  verticallyResizable = true,
  minHeight = 280,
  maxHeight = 960,
  deferRenderMs = 0,
  lazy = false,
  initialHeight,
  legendFontSize = 11,
  uiRevision,
  onPointClick,
  onSelection,
  onPointHover,
  onHoverEnd,
}: PlotlyChartProps) {
  const tr = useTranslation();
  const [headerHost,setHeaderHost] = useState<HTMLElement|null>(null);
  const [panelHelp,setPanelHelp] = useState("");
  const [legendHost,setLegendHost] = useState<HTMLElement|null>(null);
  const [isNearViewport,setIsNearViewport] = useState(!lazy);
  const metrologyConsultation = useMetrologyConsultation();
  const appearance = useContext(MetrologyChartAppearance);
  const stationHeight = useContext(MetrologyChartHeight);
  const figure = useMemo(() => sourceFigure && appearance ? appearance(sourceFigure) : sourceFigure, [sourceFigure, appearance]);
  const { language } = useLanguage();
  const plotConfig = useMemo(() => ({responsive:true,displaylogo:false,displayModeBar:"hover" as const,locale:language === "pt" ? "pt-BR" : "en-US"}), [language]);
  const [isDeferredReady, setIsDeferredReady] = useState(deferRenderMs <= 0);
  const [isLegendExpanded, setIsLegendExpanded] = useState(!legendCollapsed);
  useEffect(()=>setIsLegendExpanded(!legendCollapsed),[legendCollapsed]);
  const [chartHeight, setChartHeight] = useState<number | null>(null);
  const [isResizing, setIsResizing] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const plotContainerRef = useRef<HTMLDivElement>(null);
  const graphDivRef = useRef<PlotlyGraphDiv | null>(null);
  const liveInteractions = useRef<Record<string,(event:unknown)=>void>>({});
  const interactionListeners = useMemo(()=>Object.fromEntries(["plotly_hover","plotly_unhover","plotly_click","plotly_selected","plotly_relayout"].map(name=>[name,(event:unknown)=>liveInteractions.current[name]?.(event)])),[]);
  function bindInteractions(graphDiv:PlotlyGraphDiv|undefined) {
    if(!graphDiv?.on)return;
    // react-plotly retains its handler registry after Strict Mode purges the graph.
    // Reattach our own listeners without asking Plotly to redraw the figure.
    for(const [name,handler] of Object.entries(interactionListeners)) {
      graphDiv.removeListener?.(name,handler);
      graphDiv.on(name,handler);
    }
  }
  const initialHeightRef = useRef<number | null>(null);
  const resizeDragRef = useRef<{ pointerId: number; startY: number; startHeight: number } | null>(null);

  const pointerInteractionTokenRef = useRef(0);
  const consumedPointerInteractionTokenRef = useRef(0);
  const isSynchronizingStandardAxisRef = useRef(false);
  const shouldDeferRender = deferRenderMs > 0;
  const normalizedMinHeight = Math.max(200, metrologyConsultation ? Math.min(240,minHeight) : minHeight);
  const normalizedMaxHeight = Math.max(normalizedMinHeight, maxHeight);
  const hasCollapsibleLegend = useMemo(() => {
    if (!collapsibleLegend || !figure) {
      return false;
    }
    const layout = figure.layout && typeof figure.layout === "object" ? (figure.layout as Record<string, unknown>) : {};
    const traces = Array.isArray(figure.data) ? figure.data : [];
    return traces.some((traceValue) => {
      if (!traceValue || typeof traceValue !== "object") {
        return false;
      }
      const trace = traceValue as Record<string, unknown>;
      return trace.showlegend !== false && typeof trace.name === "string" && trace.name.trim().length > 0;
    });
  }, [collapsibleLegend, figure]);
  const isLegendVisible = isLegendExpanded;
  const shouldFillContainer = fitContainer || verticallyResizable || hasCollapsibleLegend;
  useEffect(()=>{
    const card=containerRef.current?.closest("[data-card]");
    const header=card?.querySelector<HTMLElement>("[data-card-header]")??null;
    setHeaderHost(header);
    setLegendHost(header?.querySelector<HTMLElement>('[data-chart-display-menu="external"]') ?? null);
    setPanelHelp([titleText((figure?.layout as Record<string,unknown>)?.title).replace(/<[^>]*>/g," "),header?.querySelector("[data-card-description]")?.textContent,...(Array.isArray(figure?.data)?figure.data.map((t: {name?:string})=>t.name):[])].filter(Boolean).join(" · "));
    if(header) header.classList.add("analytical-panel-header");
    return ()=>header?.classList.remove("analytical-panel-header");
  },[figure, isDeferredReady, isNearViewport]);
  const preparedFigure = useMemo(() => {
    if (!figure || Object.keys(figure).length === 0) {
      return null;
    }
    const figureData = Array.isArray(figure.data) ? figure.data : [];
    const layout = typeof figure.layout === "object" && figure.layout ? { ...(figure.layout as Record<string, unknown>) } : {};
    const compacted = compactFigureColorbars(figureData, layout);
    // Record the axis role before translating any display text (e.g. Standard → Padrão).
    const secondaryAxis = layout.yaxis2 as { title?: unknown } | undefined;
    if (secondaryAxis && titleText(secondaryAxis.title).startsWith("Standard ")) {
      layout.meta = { ...(typeof layout.meta === "object" && layout.meta ? layout.meta : {}), equalStandardScale: true };
    }
    // Presentation only: never modify data, axis ranges, scales or assignments.
    layout.height = stationHeight === 280 ? 340 : stationHeight;
    layout.title = {text:""};
    layout.font = { ...(layout.font as object ?? {}), family: "Segoe UI, sans-serif", size: 11, color: "#475569" };
    layout.paper_bgcolor = "#ffffff";
    layout.plot_bgcolor = "#ffffff";
    layout.margin = { ...(layout.margin as object ?? {}), l: 54, r: secondaryAxis ? 58 : 18, t: isLegendVisible ? 28 : 8, b: 42, pad: 0, autoexpand: true };
    layout.legend = { ...(layout.legend as object ?? {}), orientation:"h", x:0, xanchor:"left", y:1.01, yanchor:"bottom", font:{size:legendFontSize}, itemsizing:"constant", tracegroupgap:0, borderwidth:0, entrywidth:undefined, entrywidthmode:"pixels" };
    for (const key of Object.keys(layout).filter(k => /^[xy]axis\d*$/.test(k))) {
      const axis=layout[key] as Record<string,unknown>;
      layout[key] = { ...axis, gridcolor: "#e8edf1", automargin: true, title: typeof axis.title === "string" ? {text:axis.title,standoff:6} : {...(axis.title as object??{}),standoff:6} };
    }
    for(const key of Object.keys(layout).filter(k=>/^scene\d*$/.test(k))) {
      const scene={...(layout[key] as Record<string,unknown>)};
      if(key==="scene" && !layout.scene2) {
        // A single 3D viewport includes its own tick/title space. Reclaim the
        // old bottom legend reservation without changing data ranges or saved views.
        scene.domain={x:[0,1],y:[0,1]};
        if(!scene.camera) scene.camera={eye:{x:1.65,y:1.65,z:1.65}};
        layout.margin={...(layout.margin as object),l:10,r:10,b:12};
      }
      for(const axisKey of ["xaxis","yaxis","zaxis"]) {
        const axis={...(scene[axisKey] as Record<string,unknown>??{})};
        axis.tickfont={...(axis.tickfont as object??{}),size:10};
        axis.title=typeof axis.title==="string"?{text:axis.title,font:{size:10}}:{...(axis.title as object??{}),font:{size:10}};
        axis.gridcolor="#e8edf1";
        scene[axisKey]=axis;
      }
      layout[key]=scene;
    }
    applyD18AxisInversion(layout);
    if ((layout.meta as {equalStandardScale?:boolean})?.equalStandardScale) {
      const alignment = alignedIsotopeAxes(figureData as Record<string,unknown>[], (layout.yaxis as {autorange?:unknown})?.autorange === "reversed");
      if (alignment) {
        layout.meta = {...(layout.meta as object), standardMeanOffset:alignment.offset};
        layout.yaxis = {...(layout.yaxis as object), range:alignment.primary, autorange:false};
        layout.yaxis2 = {...(layout.yaxis2 as object), range:alignment.secondary, autorange:false};
      }
    }
    const hoverLabel = layout.hoverlabel && typeof layout.hoverlabel === "object" ? { ...(layout.hoverlabel as Record<string, unknown>) } : {};
    hoverLabel.namelength = -1;
    layout.hoverlabel = hoverLabel;
    const hasExplicitHeight = typeof (layout as { height?: unknown }).height === "number";
    delete (layout as { width?: unknown }).width;
    layout.autosize = true;
    if (shouldFillContainer) {
      delete (layout as { height?: unknown }).height;
    }
    if (hasCollapsibleLegend) {
      layout.showlegend = isLegendVisible;
      if (!isLegendVisible) {
        reclaimHiddenLegendMargin(layout);
      }
    }
    if (compacted.hasColorbar) {
      const margin = layout.margin && typeof layout.margin === "object" ? { ...(layout.margin as Record<string, unknown>) } : {};
      margin.r = Math.max(typeof margin.r === "number" ? margin.r : 0, 54);
      margin.autoexpand = true;
      layout.margin = margin;
    }
    if (uiRevision) {
      layout.uirevision = uiRevision;
    } else if (typeof (layout as { uirevision?: unknown }).uirevision === "undefined") {
      layout.uirevision = buildDefaultUiRevision(compacted.data, layout);
    }
    applyPersistedViewport(layout, uiRevision ? persistedViewports.get(uiRevision) : undefined);
    return {
      data: formatPlotlyDisplayText(compacted.data.map(value=>{if(!value || typeof value!=="object")return value; const trace=value as Record<string,unknown>; const name=trace.name; return {...trace,name:typeof name==="string"?name.replace(/^(Duplicat(?:e|ed) samples|Amostras duplicadas)$/i, "Duplicates").replace(/^(Final result ± U|Resultado final ± U)$/, "Final ± U").replace(/^(Before correction|Antes da correção)$/, "Before").replace(/^(After correction|Após a correção)$/, "After").replace(/^(Manual preview|Prévia manual)$/, "Preview"):name};}), text => tr(metrologyConsultation ? text.replace(/VSMOW/g, "VPDB") : text)) as never[],
      layout: formatPlotlyDisplayText(layout, text => tr(metrologyConsultation ? text.replace(/VSMOW/g, "VPDB") : text)) as never,
      useResizeHandler: true,
      fillContainerHeight: shouldFillContainer,
      hasExplicitHeight,
    };
  }, [figure, hasCollapsibleLegend, isLegendVisible, shouldFillContainer, tr, uiRevision, metrologyConsultation, stationHeight, legendFontSize]);

  useEffect(() => {
    if ((!verticallyResizable && !(metrologyConsultation && stationHeight !== 280 && fitContainer)) || chartHeight !== null) {
      return;
    }
    const container = containerRef.current;
    if (!container) {
      return;
    }
    const measuredHeight = Math.round(container.getBoundingClientRect().height);
    const requestedHeight = initialHeight ?? (metrologyConsultation
      ? (stationHeight === 280 ? 340 : stationHeight)
      : 340);
    const startingHeight = Math.min(normalizedMaxHeight, Math.max(normalizedMinHeight, requestedHeight));
    initialHeightRef.current = startingHeight;
    setChartHeight(startingHeight);
  }, [chartHeight, isDeferredReady, normalizedMaxHeight, normalizedMinHeight, preparedFigure, verticallyResizable, fitContainer, metrologyConsultation, stationHeight, initialHeight]);

  useEffect(() => {
    if (!shouldDeferRender) {
      setIsDeferredReady(true);
      return;
    }
    setIsDeferredReady(false);
    const timer = window.setTimeout(() => {
      setIsDeferredReady(true);
    }, deferRenderMs);
    return () => window.clearTimeout(timer);
  }, [deferRenderMs, shouldDeferRender]);

  useEffect(() => {
    const container = containerRef.current;
    const resizeTarget = plotContainerRef.current ?? container;
    if (!container || !resizeTarget || !preparedFigure || !isDeferredReady || typeof ResizeObserver === "undefined") {
      return;
    }

    let resizeFrame: number | null = null;
    let lastWidth = resizeTarget.getBoundingClientRect().width;
    let lastHeight = resizeTarget.getBoundingClientRect().height;

    const observer = new ResizeObserver(([entry]) => {
      if (!entry) {
        return;
      }
      const { width, height } = entry.contentRect;
      if(width<=0||height<=0)return;
      if (Math.abs(width - lastWidth) < 0.5 && Math.abs(height - lastHeight) < 0.5) {
        return;
      }
      lastWidth = width;
      lastHeight = height;

      if (resizeFrame !== null) {
        window.cancelAnimationFrame(resizeFrame);
      }
      resizeFrame = window.requestAnimationFrame(() => {
        resizeFrame = null;
        const graphDiv = graphDivRef.current;
        if (!graphDiv?.isConnected || graphDiv.getBoundingClientRect().width === 0) {
          return;
        }
        void import("@/lib/plotly-core").then(({ default: plotlyModule }) => {
          if (!graphDiv.isConnected) {
            return;
          }
          const Plotly = plotlyModule as unknown as PlotlyResizeApi;
          void Plotly.Plots.resize(graphDiv);
        });
      });
    });

    observer.observe(resizeTarget);
    return () => {
      observer.disconnect();
      if (resizeFrame !== null) {
        window.cancelAnimationFrame(resizeFrame);
      }
    };
  }, [isDeferredReady, isNearViewport, preparedFigure]);

  useEffect(() => {
    if (!hasCollapsibleLegend || !isDeferredReady) {
      return;
    }
    const resizeFrame = window.requestAnimationFrame(() => {
      const graphDiv = graphDivRef.current;
      if (!graphDiv?.isConnected || graphDiv.getBoundingClientRect().width === 0) {
        return;
      }
      void import("@/lib/plotly-core").then(({ default: plotlyModule }) => {
        if (graphDiv.isConnected) {
          const Plotly = plotlyModule as unknown as PlotlyResizeApi;
          void Plotly.Plots.resize(graphDiv);
        }
      });
    });
    return () => window.cancelAnimationFrame(resizeFrame);
  }, [hasCollapsibleLegend, isDeferredReady, isNearViewport, isLegendVisible]);

  useEffect(() => {
    if (isNearViewport || !lazy) return;
    const element = containerRef.current;
    if (!element || typeof IntersectionObserver === "undefined") { setIsNearViewport(true); return; }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setIsNearViewport(true); observer.disconnect(); }
    }, {rootMargin:"240px"});
    observer.observe(element);
    return () => observer.disconnect();
  }, [isNearViewport, lazy]);

  function refreshAfterInitialize(_figure?: unknown, graphDiv?: PlotlyGraphDiv) {
    graphDivRef.current = graphDiv ?? null;
    bindInteractions(graphDiv);
    syncStandardAxisScale(graphDiv, (active) => {
      isSynchronizingStandardAxisRef.current = active;
    });
  }

  function persistViewportUpdate(update: Record<string, unknown> | undefined) {
    if (!uiRevision || !update || isSynchronizingStandardAxisRef.current) {
      return;
    }
    if (Object.keys(update).some(key => key.startsWith("yaxis."))) {
      syncStandardAxisScale(graphDivRef.current ?? undefined, active => {isSynchronizingStandardAxisRef.current = active;});
    }
    const current = persistedViewports.get(uiRevision) ?? {};
    const next = mergeViewportRelayout(current, update);
    if (Object.keys(next).length > 0) {
      persistedViewports.set(uiRevision, cloneRecord(next));
    }
  }

  function registerPointerInteraction() {
    pointerInteractionTokenRef.current += 1;
  }

  function consumePointerInteraction(): boolean {
    const token = pointerInteractionTokenRef.current;
    if (token <= consumedPointerInteractionTokenRef.current) {
      return false;
    }
    consumedPointerInteractionTokenRef.current = token;
    return true;
  }

  function clampHeight(value: number): number {
    return Math.min(normalizedMaxHeight, Math.max(normalizedMinHeight, Math.round(value)));
  }

  function beginVerticalResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!verticallyResizable || event.button !== 0) {
      return;
    }
    const containerHeight = containerRef.current?.getBoundingClientRect().height ?? chartHeight ?? normalizedMinHeight;
    resizeDragRef.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startHeight: containerHeight,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setIsResizing(true);
    event.preventDefault();
  }

  function continueVerticalResize(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = resizeDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) {
      return;
    }
    setChartHeight(clampHeight(drag.startHeight + event.clientY - drag.startY));
  }

  function endVerticalResize(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = resizeDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) {
      return;
    }
    resizeDragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setIsResizing(false);
  }

  function resizeWithKeyboard(event: ReactKeyboardEvent<HTMLDivElement>) {
    const currentHeight = chartHeight ?? containerRef.current?.getBoundingClientRect().height ?? normalizedMinHeight;
    if (event.key === "ArrowUp") {
      setChartHeight(clampHeight(currentHeight - 24));
    } else if (event.key === "ArrowDown") {
      setChartHeight(clampHeight(currentHeight + 24));
    } else if (event.key === "Home" && initialHeightRef.current != null) {
      setChartHeight(initialHeightRef.current);
    } else {
      return;
    }
    event.preventDefault();
  }

  if (!preparedFigure) {
    return <div className="rounded-lg border border-dashed border-stone-300 p-6 text-sm text-stone-500">{tr("No chart data yet.")}</div>;
  }
  if (!isDeferredReady || !isNearViewport) {
    return (
      <div
        className={cn(
          "flex min-w-0 items-center justify-center rounded-lg border border-dashed border-stone-300 p-6 text-sm text-stone-500",
          className,
        )}
        ref={containerRef}
        style={{minHeight:initialHeight ?? 340}}
        aria-busy="true"
      >{tr("Preparing chart...")}</div>
    );
  }
  const shouldUseContainerHeight = preparedFigure.fillContainerHeight;
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
  liveInteractions.current = {
    plotly_hover:event=>hoverHandlers.onHover?.(event as Parameters<NonNullable<typeof hoverHandlers.onHover>>[0]),
    plotly_unhover:()=>onHoverEnd?.(),
    plotly_click:event=>{if(onPointClick&&consumePointerInteraction())onPointClick((event as {points?:PlotlyPoint[]})?.points??[]);},
    plotly_selected:event=>{if(onSelection&&consumePointerInteraction())onSelection((event as {points?:PlotlyPoint[]})?.points??[]);},
    plotly_relayout:event=>persistViewportUpdate(event as Record<string,unknown>),
  };
  const legendControl = <label className="chart-legend-control"><input type="checkbox" checked={!isLegendVisible} onChange={event=>setIsLegendExpanded(!event.target.checked)}/>{tr("Hide legend")}</label>;
  return (
    <div
      ref={containerRef}
      className={cn("analytical-chart flex min-w-0 w-full flex-col overflow-hidden", className)}
      style={chartHeight == null ? undefined : { height: `${chartHeight}px` }}
    >
      {headerHost ? createPortal(<div className="analytical-chart-actions">{panelHelp&&<button type="button" className="analytical-help" title={panelHelp} aria-label={panelHelp}><HelpCircle size={14}/></button>}{!legendHost&&hasCollapsibleLegend&&<details className="chart-display-menu"><summary><SlidersHorizontal size={14}/>{tr("Display")}<ChevronDown size={13}/></summary><div data-chart-display-menu>{legendControl}</div></details>}</div>,headerHost) : <div className="analytical-chart-header"><h3 title={panelHelp}>{tr(panelTitle((figure?.layout as Record<string,unknown>)?.title))}</h3>{hasCollapsibleLegend&&<details className="chart-display-menu"><summary><SlidersHorizontal size={14}/>{tr("Display")}<ChevronDown size={13}/></summary><div data-chart-display-menu>{legendControl}</div></details>}</div>}
      {legendHost&&hasCollapsibleLegend ? createPortal(legendControl,legendHost) : null}
      <div
        ref={plotContainerRef}
        className={cn("relative min-h-0 w-full", shouldUseContainerHeight ? "flex-1" : "")}
        onPointerDownCapture={registerPointerInteraction}
      >
        <Plot
          data={preparedFigure.data}
          layout={preparedFigure.layout}
          config={plotConfig}
          onInitialized={refreshAfterInitialize}
          onUpdate={(_figure: unknown, graphDiv: PlotlyGraphDiv) => {
            graphDivRef.current = graphDiv;
            bindInteractions(graphDiv);
            if (!isSynchronizingStandardAxisRef.current) syncStandardAxisScale(graphDiv, (active) => {
              isSynchronizingStandardAxisRef.current = active;
            })
          }}
          useResizeHandler={preparedFigure.useResizeHandler}
          className={cn("w-full max-w-full", shouldUseContainerHeight ? "h-full" : "")}
          // Plotly resets its inner SVG to 100% while resizing. Keep a concrete
          // height on fixed-size charts so an auto-height parent cannot collapse.
          style={shouldUseContainerHeight ? { width: "100%", height: "100%" } : { width: "100%", height: (preparedFigure.layout as { height?: number }).height ?? normalizedMinHeight }}

        />
      </div>
      {verticallyResizable ? (
        <div
          role="separator"
          aria-label={tr("Resize chart height")}
          aria-orientation="horizontal"
          aria-valuemin={normalizedMinHeight}
          aria-valuemax={normalizedMaxHeight}
          aria-valuenow={chartHeight ?? undefined}
          tabIndex={0}
          title={tr("Drag to resize chart height. Use the up and down arrow keys for precise changes.")}
          className={cn(
            "analytical-resize group flex h-2 shrink-0 touch-none cursor-ns-resize items-center justify-center border-t border-slate-200 bg-slate-50 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400",
            isResizing && "bg-slate-100 text-blue-700",
          )}
          onPointerDown={beginVerticalResize}
          onPointerMove={continueVerticalResize}
          onPointerUp={endVerticalResize}
          onPointerCancel={endVerticalResize}
          onKeyDown={resizeWithKeyboard}
          onDoubleClick={() => {
            if (initialHeightRef.current != null) {
              setChartHeight(initialHeightRef.current);
            }
          }}
        >
          <GripHorizontal className="h-3.5 w-5" aria-hidden="true" />
        </div>
      ) : null}
    </div>
  );
}
