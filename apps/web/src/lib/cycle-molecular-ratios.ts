type CycleRow = Record<string, unknown>;

function positive(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

/** Collector signal ratios. Missing or nonpositive signals leave a gap in the curve. */
export function cycleMolecularRatioFigure(rows: CycleRow[]): Record<string, unknown> {
  const data = ([45, 46] as const).flatMap((mass, index) => (["SMP", "REF"] as const).map(role => ({
    type: "scatter",
    mode: "lines+markers",
    name: `${role} · ${mass}/44`,
    x: rows.map(row => row.Cycle),
    y: rows.map(row => {
      const signal = (mz: number) => positive(row[`${role} Int m/z ${mz} (V)`] ?? (role === "REF" ? row[`STD Int m/z ${mz} (V)`] : undefined));
      const numerator = signal(mass), denominator = signal(44);
      if (numerator == null || denominator == null) return null;
      const ratio = numerator / denominator;
      return Number.isFinite(ratio) ? ratio : null;
    }),
    xaxis: index ? "x2" : "x",
    yaxis: index ? "y2" : "y",
    connectgaps: false,
    line: { color: mass === 45 ? "#167d87" : "#215ec5", width: 2, dash: role === "REF" ? "dash" : "solid" },
    marker: { size: 6, symbol: role === "REF" ? "circle-open" : "circle" },
    hovertemplate: "%{x}: %{y:.6f}<extra>%{fullData.name}</extra>",
  })));
  return { data, layout: {
    title: "Molecular ratios per cycle",
    margin: { l: 65, r: 15, t: 40, b: 40 },
    xaxis: { anchor: "y", dtick: 1, showticklabels: false },
    yaxis: { title: "m/z 45/44", domain: [0.57, 1], tickformat: ".4f", automargin: true },
    xaxis2: { title: "Cycle", anchor: "y2", matches: "x", dtick: 1 },
    yaxis2: { title: "m/z 46/44", domain: [0, 0.43], tickformat: ".4f", automargin: true },
    showlegend: true,
    hovermode: "x unified",
  } };
}

export function cycleIntensityFigure(rows: CycleRow[]): Record<string, unknown> {
  const colors = { 44: "#E67E22", 45: "#1E7D2B", 46: "#D4A017" };
  return {
    data: ([44, 45, 46] as const).flatMap(mass => (["SMP", "REF"] as const).map(role => ({
      type: "scatter",
      mode: "lines+markers",
      name: `${mass.toFixed(2)} m/z ${role}`,
      x: rows.map(row => row.Cycle),
      y: rows.map(row => {
        const value = row[`${role} Int m/z ${mass} (V)`] ?? (role === "REF" ? row[`STD Int m/z ${mass} (V)`] : undefined);
        return typeof value === "number" && Number.isFinite(value) ? value : null;
      }),
      connectgaps: false,
      line: { color: colors[mass], width: 2, dash: role === "REF" ? "dash" : "solid" },
      marker: { size: 6 },
      hovertemplate: "%{x}: %{y:.3f} V<extra>%{fullData.name}</extra>",
    }))),
    layout: {
      title: "Intensities per cycle",
      xaxis: { title: "Cycle", dtick: 1 },
      yaxis: { title: "Intensity (V)", automargin: true },
      margin: { l: 65, r: 15, t: 40, b: 40 },
      showlegend: true,
      hovermode: "x unified",
    },
  };
}
