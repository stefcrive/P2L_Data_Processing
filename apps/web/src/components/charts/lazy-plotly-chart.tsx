"use client";

import dynamic from "next/dynamic";

import type { PlotlyChartProps } from "@/components/charts/plotly-chart";

export type { PlotlyChartProps, PlotlyHoverPayload, PlotlyPoint } from "@/components/charts/plotly-chart";

export const PlotlyChart = dynamic<PlotlyChartProps>(
  () => import("@/components/charts/plotly-chart").then((module) => module.PlotlyChart),
  {
    ssr: false,
    loading: () => (
      <div className="flex min-h-[240px] min-w-0 items-center justify-center rounded-lg border border-dashed border-stone-300 p-6 text-sm text-stone-500">
        Preparing chart...
      </div>
    ),
  },
);
