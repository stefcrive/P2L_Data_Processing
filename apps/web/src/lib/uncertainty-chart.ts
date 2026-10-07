import type { SessionRow } from "./metrology";
import { withSessionUncertainty } from "./metrology-envelopes";
import { filterStationFigure, fitVisibleMarkers, type ChartFlag } from "./station-chart-filters";

/** Keep processing outliers out of both imported traces and canonical U overlays. */
export function buildUncertaintyFigure(
  figure: Record<string, unknown>,
  rows: Record<string, SessionRow>,
  label: string,
  flags: ChartFlag[],
) {
  const traces = (figure.data ?? []) as Record<string, unknown>[];
  const isOutlierTrace = (trace: Record<string, unknown>) =>
    /outliers|failed|^(?:signal intensity|leak rate|d13c|d18o) range$/i.test(String(trace.name ?? ""));
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
  return fitVisibleMarkers(withSessionUncertainty(filtered, rows, label, exclusions)!);
}
