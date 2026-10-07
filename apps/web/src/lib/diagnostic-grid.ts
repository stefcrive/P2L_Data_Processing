import { plotlyVector } from "./plotly-order";
import { partiallySaturatedOverlay } from "./qc-outlier-display";

export type CycleSignalMode = "first_valid" | "last_valid" | "average";

/** Change only the pressure chart's sample signal, retaining point identities and pressure values. */
export function withCycleSignalIntensity(
  figure: Record<string, unknown>, summaries: unknown, mode: CycleSignalMode, axisTitle: string,
) {
  const byRow = summaries && typeof summaries === "object" ? summaries as Record<string, Record<string, unknown>> : {};
  const layout = figure.layout as Record<string, unknown> | undefined;
  return { ...figure, data: Array.isArray(figure.data) ? figure.data.map((trace: Record<string, unknown>) => {
    if (!Array.isArray(trace.customdata) || !String(trace.mode).includes("markers")) return trace;
    return { ...trace, x: trace.customdata.map(info => {
      const value = Array.isArray(info) ? byRow[String(info[0])]?.[mode] : null;
      return typeof value === "number" && Number.isFinite(value) ? value : null;
    }) };
  }) : figure.data, layout: { ...layout, xaxis: { ...(layout?.xaxis as object ?? {}), title: { text: axisTitle } } } };
}

export function groupDiagnosticGridItems<T extends { group: string }>(items: T[]) {
  const groups: Array<{ name: string; items: T[] }> = [];
  for (const item of items) {
    let group = groups.find(group => group.name === item.group);
    if (!group) {
      group = { name: item.group, items: [] };
      groups.push(group);
    }
    group.items.push(item);
  }
  return groups;
}

/** Supplemental plots inherit observation colors and symbols by row, rather than trace order. */
export function matchDiagnosticMarkerStyles(figure: Record<string, unknown>, reference?: Record<string, unknown>) {
  if (!Array.isArray(figure.data) || !Array.isArray(reference?.data)) return figure;
  const observations = new Map<string, { color: unknown; symbol: unknown; info: unknown[] }>();
  const partialRows = new Set<string>();
  let style: Record<string, unknown> = {};
  let hovertemplate = "";
  for (const trace of reference.data as Record<string, unknown>[]) {
    if (!String(trace.mode).includes("markers") || !Array.isArray(trace.customdata)) continue;
    if (trace.name === "Partially Saturated Collectors") {
      for (const info of trace.customdata) if (Array.isArray(info)) partialRows.add(String(info[0]));
      continue;
    }
    const marker = trace.marker as Record<string, unknown> | undefined;
    if (!marker) continue;
    const colors = plotlyVector(marker.color), symbols = plotlyVector(marker.symbol);
    if (!observations.size) {
      style = marker;
      hovertemplate = String(trace.hovertemplate ?? "").replace(/customdata\[(\d+)\]/g,
        (_, index: string) => `customdata[${Number(index) ? Number(index) + 1 : 0}]`);
    }
    trace.customdata.forEach((info: unknown, index: number) => {
      if (!Array.isArray(info) || observations.has(String(info[0]))) return;
      observations.set(String(info[0]), { color: colors ? colors[index] ?? null : marker.color,
        symbol: symbols?.[index] ?? marker.symbol ?? "circle", info });
    });
  }
  if (!observations.size) return figure;
  const data = figure.data.flatMap((trace: Record<string, unknown>) => {
    if (!String(trace.mode).includes("markers") || !Array.isArray(trace.customdata)) return [trace];
    const matched = trace.customdata.map(info => Array.isArray(info) ? observations.get(String(info[0])) : undefined);
    const marker: Record<string, unknown> = { ...style, color: matched.map(point => point?.color ?? null),
      symbol: matched.map(point => point?.symbol ?? "circle"), showscale: false };
    delete marker.colorbar;
    delete marker.coloraxis;
    const next: Record<string, unknown> = { ...trace, marker, showlegend: false,
      customdata: trace.customdata.map((info, index) => matched[index]
        ? [matched[index]!.info[0], "cross", ...matched[index]!.info.slice(1)] : info),
      hovertemplate: hovertemplate ? hovertemplate.replace("<extra>", trace.type === "scatter3d" ? "<br>Z: %{z}<extra>" : "<extra>") : trace.hovertemplate,
    };
    // Keep point identities aligned while hiding observations outside the diagnostics filters.
    for (const axis of ["x", "y", "z"]) {
      const values = plotlyVector(trace[axis]);
      if (values) next[axis] = values.map((value, index) => matched[index] ? value : null);
    }
    const overlay = partiallySaturatedOverlay(next, partialRows);
    return overlay ? [next, overlay] : [next];
  });
  return { ...figure, data, layout: { ...(figure.layout as object ?? {}), showlegend: false } };
}
