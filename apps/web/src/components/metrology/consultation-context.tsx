"use client";
import { createContext, useContext, type ReactNode } from "react";
import type { PlotlyChartProps } from "@/components/charts/plotly-chart";
import type { SessionRow } from "@/lib/metrology";

export const MetrologyConsultation = createContext(false);
export const useMetrologyConsultation = () => useContext(MetrologyConsultation);
export const MetrologyProcessingResults = createContext<Record<string, SessionRow>>({});

export type ChartInteractions = (key: string) => Pick<PlotlyChartProps, "onPointClick" | "onSelection" | "onPointHover" | "onHoverEnd">;
export const MetrologyChartWorkspace = createContext<{
  sequence: (interact: ChartInteractions) => ReactNode;
  plots: (interact: ChartInteractions) => ReactNode;
  controls: ReactNode;
  review: (rowLabels: string[]) => ReactNode;
} | null>(null);
