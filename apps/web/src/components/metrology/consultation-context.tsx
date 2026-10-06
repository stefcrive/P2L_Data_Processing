"use client";
import { createContext, useContext, type ReactNode } from "react";
import type { PlotlyChartProps } from "@/components/charts/plotly-chart";
import type { SessionRow } from "@/lib/metrology";

export const MetrologyConsultation = createContext(false);
export const useMetrologyConsultation = () => useContext(MetrologyConsultation);
export const MetrologyProcessingResults = createContext<Record<string, SessionRow>>({});
export const MetrologyChartAppearance = createContext<((figure: Record<string, unknown>) => Record<string, unknown>) | null>(null);
export const MetrologyChartHeight = createContext(280);

export type ChartInteractions = (key: string) => Pick<PlotlyChartProps, "onPointClick" | "onSelection" | "onPointHover" | "onHoverEnd">;
export const MetrologyChartWorkspace = createContext<{
  sequence: (interact: ChartInteractions) => ReactNode;
  plots: (interact: ChartInteractions, manualControls: ReactNode) => ReactNode;
  controls: ReactNode;
  carbonateMaterial?: string;
  materialLabels?: string[];
  outliers?: {method:string;threshold:number;rows:{row:string;isotope:string}[]};
  review: (rowLabels: string[]) => ReactNode;
} | null>(null);
