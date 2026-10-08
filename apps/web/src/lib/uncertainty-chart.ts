import type { SessionRow } from "./metrology";
import { withSessionUncertainty } from "./metrology-envelopes";
import { filterStationFigure, fitVisibleMarkers, type ChartFlag } from "./station-chart-filters";
import { orderTraceByX, plotlyVector } from "./plotly-order";
import { parseNumericToken } from "./numeric-token";

export type UncertaintyXAxis = "By Identifier 2" | "By Sequence";

function resultAxis(figure: Record<string, unknown>, rows: Record<string, SessionRow>, mode: UncertaintyXAxis) {
  const data = ((figure.data ?? []) as Record<string, unknown>[]).map(trace => {
    const custom = plotlyVector(trace.customdata), x = plotlyVector(trace.x);
    if (!custom || !x || custom.length !== x.length || trace.z) return trace;
    const nextX = custom.map((point, index) => {
      if (!Array.isArray(point) || !["d13C", "d18O"].includes(String(point[1]))) return x[index];
      const row = rows[String(point[0])];
      if (mode === "By Sequence") return row?.sequence ?? index;
      return parseNumericToken(row?.identifier2 ?? point[3]) ?? x[index];
    });
    return orderTraceByX({...trace, x: nextX});
  });
  const layout = {...(figure.layout as Record<string, unknown> ?? {})};
  const axis = {...(layout.xaxis as Record<string, unknown> ?? {}), title: mode === "By Sequence" ? "Sample Number" : "Identifier 2"};
  // Recompute the viewport for the selected coordinate system.
  delete (axis as Record<string, unknown>).range;
  layout.xaxis = {...axis, autorange: true};
  return {...figure, data, layout};
}

/** Keep processing outliers out of both imported traces and canonical U overlays. */
export function buildUncertaintyFigure(
  figure: Record<string, unknown>,
  rows: Record<string, SessionRow>,
  label: string,
  flags: ChartFlag[],
  xAxis?: UncertaintyXAxis,
) {
  const traces = (figure.data ?? []) as Record<string, unknown>[];
  const isOutlierTrace = (trace: Record<string, unknown>) =>
    /outliers|failed|poor pressure adjustment|^(?:signal intensity|leak rate|d13c|d18o) range$/i.test(String(trace.name ?? ""));
  const exclusions = [...flags, ...traces.filter(isOutlierTrace).flatMap(trace =>
    Array.isArray(trace.customdata) ? trace.customdata.flatMap(point =>
      Array.isArray(point) ? [{ row: String(point[0]), isotope: String(point[1]), hidden: true }] : [],
    ) : [],
  )];
  const layout = (figure.layout ?? {}) as Record<string, unknown>;
  const retained = {
    ...figure,
    data: traces.filter(trace => !isOutlierTrace(trace)),
    layout: {
      ...layout,
      ...(Array.isArray(layout.annotations) ? {
        annotations: layout.annotations.filter(annotation => !/are shown as range outliers/i.test(String(annotation.text))),
      } : {}),
    },
  };
  const filtered = filterStationFigure(retained, exclusions, true);
  const positioned = xAxis ? resultAxis(filtered, rows, xAxis) : filtered;
  return fitVisibleMarkers(withSessionUncertainty(positioned, rows, label, exclusions)!);
}
