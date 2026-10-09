import type { SessionRow } from "./metrology";
import type { ChartFlag } from "./station-chart-filters";
import type { LinearityPreviewRow, ProcessingConfig } from "./types";
import { parseNumericToken } from "./numeric-token";
import { plotlyVector } from "./plotly-order";

type DisplayConfig = Pick<ProcessingConfig, "overlays" | "pressure_adjustment_as_outlier">;

/** Keep canonical final results and their uncertainty, including visible flagged results. */
export function finalProcessingFigure(figure: Record<string, unknown>, rows: Record<string, SessionRow>): Record<string, unknown> {
  if (!Array.isArray(figure.data) || !Object.keys(rows).length) return figure;
  const finals = (figure.data as Record<string, unknown>[]).filter((trace: Record<string, unknown>) => (trace.meta as {sessionUncertainty?:boolean;uncertaintyEnvelope?:boolean})?.sessionUncertainty || (trace.meta as {uncertaintyEnvelope?:boolean})?.uncertaintyEnvelope);
  const axisKey = (trace: Record<string, unknown>) => trace.type === "scatter3d" ? String(trace.scene ?? "scene") : `${trace.xaxis ?? "x"}:${trace.yaxis ?? "y"}`;
  const present = new Set<string>();
  for (const trace of finals) {
    const y = plotlyVector(trace.y);
    if (Array.isArray(trace.customdata)) trace.customdata.forEach((point, i) => {
      if (Array.isArray(point) && y?.[i] != null) present.add(`${axisKey(trace)}:${point[0]}:${point[1]}`);
    });
  }
  const flagged: Record<string, unknown>[] = [];
  for (const trace of figure.data as Record<string, unknown>[]) {
    const categories = (trace.meta as {qcCategories?:string[]})?.qcCategories;
    if (!categories || !Array.isArray(trace.customdata)) continue;
    const originalY = plotlyVector(trace.y), originalX = plotlyVector(trace.x);
    const indices = trace.customdata.flatMap((point, i) => {
      if (!Array.isArray(point) || categories[i] === "Unflagged observations" || originalY?.[i] == null) return [];
      const key = `${axisKey(trace)}:${point[0]}:${point[1]}`, row = rows[String(point[0])];
      const iso = point[1] === "d18O" ? "d18o" : "d13c";
      if (!row?.isotopes?.[iso] || present.has(key)) return [];
      present.add(key);
      return [i];
    });
    if (!indices.length) continue;
    const custom = indices.map(i => (trace.customdata as unknown[][])[i]);
    const value = (point: unknown[], iso: "d13c" | "d18o") => rows[String(point[0])]?.isotopes?.[iso]?.value ?? null;
    flagged.push({...trace, name: "Final", mode: "markers", showlegend: false,
      customdata: custom, x: custom.map((point, i) => point[1] === "cross" ? value(point, "d18o") : originalX?.[indices[i]]),
      y: custom.map(point => value(point, point[1] === "d18O" ? "d18o" : "d13c")),
      ...(trace.type === "scatter3d" ? {z: indices.map(i => plotlyVector(trace.z)?.[i])} : {}),
      marker: {...(trace.marker as object ?? {}), symbol: indices.map(i => Array.isArray((trace.marker as {symbol?:unknown})?.symbol) ? (trace.marker as {symbol:unknown[]}).symbol[i] : (trace.marker as {symbol?:unknown})?.symbol)},
      error_x: undefined, error_y: undefined, error_z: undefined,
      meta: {...(trace.meta as object ?? {}), qcCategories: indices.map(i => categories[i])},
    });
  }
  return {...figure, data: [...finals.map((trace: Record<string, unknown>) => ({...trace, meta: {...(trace.meta as object ?? {}), finalOnly: true}})).map((trace: Record<string, unknown>) => (trace.meta as {standardOverlay?:boolean})?.standardOverlay && (trace.meta as {sessionUncertainty?:boolean})?.sessionUncertainty ? {...trace, name: "Standard measured final", showlegend: true} : trace), ...flagged]};
}

/** Saved figures can omit disabled categories entirely. Recover enabled points from source rows. */
export function restoreProcessingOutlierRows(
  figure: Record<string, unknown>,
  rows: LinearityPreviewRow[],
  flags: ChartFlag[],
  config: Pick<ProcessingConfig, "selected_identifier" | "identifier1_name_map" | "species_name_map" | "x_axis_option" | "z_axis">,
  chartKey: string | undefined,
  values?: ReadonlyMap<string, {d13: number | null; d18: number | null}>,
): Record<string, unknown> {
  if (!Array.isArray(figure.data) || !chartKey) return figure;
  const parts = chartKey.split("|");
  const identifier = parts.length >= 3 ? parts.at(-2) : undefined;
  const species = parts.length >= 3 ? parts.slice(0, -2).join("|") : undefined;
  const isotope = parts.length >= 3 ? parts.at(-1) : chartKey === "d13_summary" ? "d13C" : chartKey === "d18_summary" ? "d18O" : "cross";
  const three = chartKey === "processing_3d";
  const scopedRows = rows.filter(row => {
    const id = config.identifier1_name_map[row.identifier1 ?? ""] ?? row.identifier1 ?? "";
    const sp = config.species_name_map[row.species ?? row.identifier1 ?? ""] ?? row.species ?? id;
    return (!identifier || id === identifier) && (!species || sp === species)
      && (config.selected_identifier === "All" || config.selected_identifier === id);
  });
  const visible = new Set(flags.filter(flag => !flag.hidden && (isotope === "cross" || flag.isotope === isotope)).map(flag => flag.row));
  const present = new Set<string>();
  for (const trace of figure.data as Record<string, unknown>[]) {
    if (trace.visible === false || trace.visible === "legendonly" || trace.yaxis === "y2" || String(trace.name).startsWith("Standard measured ") || !Array.isArray(trace.customdata)) continue;
    const y = plotlyVector(trace.y), z = plotlyVector(trace.z);
    trace.customdata.forEach((point, i) => {
      if (Array.isArray(point) && point[1] === isotope && y?.[i] != null && (!three || z?.[i] != null)) present.add(String(point[0]));
    });
  }
  const selected = scopedRows.flatMap((row, sequence) => visible.has(row.row_label) && !present.has(row.row_label) ? [{row, sequence}] : []);
  if (!selected.length) return figure;
  const delta = (row: LinearityPreviewRow, iso: "d13" | "d18") => {
    const corrected = values?.get(row.row_label);
    return corrected ? corrected[iso] : row[iso === "d13" ? "d13_raw" : "d18_raw"] ?? null;
  };
  const trace: Record<string, unknown> = {
    type: three ? "scatter3d" : "scatter", mode: "markers", name: "Outlier observations", showlegend: false,
    x: selected.map(({row, sequence}) => isotope === "cross" ? delta(row, "d18") : config.x_axis_option === "By Sequence" ? sequence : parseNumericToken(row.identifier2)),
    y: selected.map(({row}) => delta(row, isotope === "d18O" ? "d18" : "d13")),
    customdata: selected.map(({row}) => [row.row_label, isotope,
      config.identifier1_name_map[row.identifier1 ?? ""] ?? row.identifier1,
      row.identifier2, config.species_name_map[row.species ?? ""] ?? row.species]),
    marker: {color: "#dc2626", size: 12, opacity: 1},
    meta: {recoveredOutliers: true},
  };
  if (three) trace.z = selected.map(({row}) => config.z_axis === "d 13C/12C  Mean" ? delta(row, "d13") : config.z_axis === "d 18O/16O  Mean" ? delta(row, "d18") : row.intensities[config.z_axis] ?? parseNumericToken(row.attributes[config.z_axis]));
  return {...figure, data: [...figure.data, trace]};
}

function pressureCorrected(row: SessionRow | undefined, isotope: string): boolean {
  const corrected = (iso: "d13c" | "d18o") => /pressure/.test(row?.isotopes?.[iso]?.residual_correction?.effect ?? "");
  return isotope === "cross" || isotope === "crossplot" ? corrected("d13c") && corrected("d18o") : corrected(isotope === "d18O" ? "d18o" : "d13c");
}

function pressureFailure(row: SessionRow | undefined, isotope: string): boolean {
  if (isotope === "cross" || isotope === "crossplot") return row?.failure_categories?.d13c === "pressure_adjustment" || row?.failure_categories?.d18o === "pressure_adjustment";
  return row?.failure_categories?.[isotope === "d18O" ? "d18o" : "d13c"] === "pressure_adjustment";
}

/** The processing sidebar owns category visibility, including flags from other workspaces. */
export function processingChartFlags(flags: ChartFlag[], rows: Record<string, SessionRow>, config: DisplayConfig): ChartFlag[] {
  const visible: Record<string, boolean> = {
    statistical: config.overlays.show_statistical_outliers,
    range: config.overlays.show_range_outliers,
    manual: config.overlays.show_manual_outliers,
    pressure_adjustment: config.overlays.show_range_outliers,
    partial: config.overlays.show_saturated_collectors,
    full: config.overlays.show_saturated_samples,
    failed: config.overlays.show_failed_samples,
    no_signal: config.overlays.show_failed_samples,
  };
  return flags.flatMap(flag => {
    // Missing QC metadata is a review warning. Sample processing ranges do not classify standards.
    const role = rows[flag.row]?.role;
    if (flag.category === "range" && (flag.metadataOnly || (flag.source === "processing" || flag.excludeFromFit === false) && (role === "qc" || role === "anchor"))) return [];
    const pressure = flag.category === "pressure_adjustment" || flag.category === "failed" && pressureFailure(rows[flag.row], flag.isotope);
    if (pressure && (!config.pressure_adjustment_as_outlier || pressureCorrected(rows[flag.row], flag.isotope))) return [];
    return [{...flag, hidden: flag.category && flag.category in visible ? !visible[flag.category] : flag.hidden}];
  });
}

/** Apply current visibility to saved traces as well as dynamically generated markers. */
export function processingTraceVisibility(figure: Record<string, unknown>, rows: Record<string, SessionRow>, config: DisplayConfig): Record<string, unknown> {
  if (!Array.isArray(figure.data)) return figure;
  const data = figure.data.flatMap((trace: Record<string, unknown>) => {
    const name = String(trace.name ?? "");
    const pressure = /Poor Pressure Adjustment/i.test(name);
    const failed = /Failed Samples|Failed Sample/i.test(name);
    const enabled = /Statistical Outliers/i.test(name) ? config.overlays.show_statistical_outliers
      : /Manual Outliers/i.test(name) ? config.overlays.show_manual_outliers
      : /Partially Failed|Partially Saturated/i.test(name) ? config.overlays.show_saturated_collectors
      : /Fully Saturated/i.test(name) ? config.overlays.show_saturated_samples
      : pressure ? config.pressure_adjustment_as_outlier && config.overlays.show_range_outliers
      : /Signal Intensity Range|Leak Rate Range|d13C Range|d18O Range/i.test(name) ? config.overlays.show_range_outliers
      : failed ? config.overlays.show_failed_samples : true;
    if (!enabled) return [];
    const flaggedTrace = /Statistical Outliers|Manual Outliers|Partially Failed|Partially Saturated|Fully Saturated|Poor Pressure Adjustment|Signal Intensity Range|Leak Rate Range|d13C Range|d18O Range|Failed Samples|Failed Sample/i.test(name);
    if (flaggedTrace) {
      const marker = trace.marker as Record<string, unknown> ?? {};
      const filled = (symbol: unknown) => typeof symbol === "string" ? symbol.replace(/-open$/, "") : symbol;
      const color = /Manual Outliers/.test(name) ? "#c026d3" : /Partially Failed|Partially Saturated/.test(name) ? "#ea580c" : "#dc2626";
      trace = {...trace, ...(trace.type === "scatter3d" ? {} : {zorder: 4}), marker: {...marker,
        color, coloraxis: undefined, showscale: false, opacity: 1,
        symbol: Array.isArray(marker.symbol) ? marker.symbol.map(filled) : filled(marker.symbol),
        line: {color: "#7f1d1d", width: 1.2}}};
    }
    if ((!pressure && !failed) || !Array.isArray(trace.customdata)) return [trace];
    const corrected = trace.customdata.map(point => {
      if (!Array.isArray(point)) return false;
      const row = rows[String(point[0])], isotope = String(point[1]);
      return pressureCorrected(row, isotope) || pressureFailure(row, isotope) && !config.pressure_adjustment_as_outlier;
    });
    if (!corrected.some(Boolean)) return [trace];
    // Corrected measurements are represented by their ordinary final-result dots.
    const next = {...trace};
    for (const axis of ["y", "z"]) {
      const values = plotlyVector(trace[axis]);
      if (values?.length === corrected.length) next[axis] = values.map((v, i) => corrected[i] ? null : v);
    }
    return [next];
  });
  return {...figure, data};
}
