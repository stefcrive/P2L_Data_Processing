"use client";

import { useMemo, useState } from "react";
import { PlotlyChart } from "@/components/charts/lazy-plotly-chart";
import { cycleIntensityFigure, cycleMolecularRatioFigure } from "@/lib/cycle-molecular-ratios";
import { useTranslation } from "@/components/layout/language-provider";

export function CycleMolecularRatioChart({ rows }: { rows: Array<Record<string, unknown>> }) {
  const tr = useTranslation();
  const [showIntensities, setShowIntensities] = useState(false);
  const figure = useMemo(() => showIntensities ? cycleIntensityFigure(rows) : cycleMolecularRatioFigure(rows), [rows, showIntensities]);
  return <div className="cycle-signal-chart" data-chart-panel>
    <header className="cycle-signal-heading" data-card-header>
      <div className="cycle-signal-heading-controls">
        <h3>{tr(showIntensities ? "Intensities per cycle" : "Molecular ratios per cycle")}</h3>
        <button type="button" className="cycle-signal-toggle" onClick={() => setShowIntensities(current => !current)}>
          {tr(showIntensities ? "Molecular ratios per cycle" : "Intensities per cycle")}
        </button>
      </div>
    </header>
    <PlotlyChart figure={figure} initialHeight={480} minHeight={380} maxHeight={800} />
    <p className="cycle-diagnostics-note">{tr(showIntensities ? "Sample and reference gas collector intensities / V." : "Collector signal ratios: I45/I44 and I46/I44.")}</p>
  </div>;
}
