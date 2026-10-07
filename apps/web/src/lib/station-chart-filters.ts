import { plotlyVector } from "./plotly-order";
import { qcOutlierDisplay } from "./qc-outlier-display";
import type { LinearityPreviewRow } from "./types";

export type RangeKey = "signal" | "leak_rate" | "d13_raw" | "d18_raw";
export type ChartRanges = Partial<Record<RangeKey, [number, number]>>;
export type ChartFlag = {row:string; isotope:string; hidden?:boolean; excludeFromFit?:boolean; category?:string; reasons?:string[]};

export function rangeFlags(rows: LinearityPreviewRow[], ranges: ChartRanges): ChartFlag[] {
  return rows.flatMap(row => {
    const outside = (key: RangeKey) => {
      const limits = ranges[key], value = row[key];
      return limits && typeof value === "number" && Number.isFinite(value) && (value < limits[0] || value > limits[1]);
    };
    const common = outside("signal") || outside("leak_rate");
    return (["d13C", "d18O"] as const).flatMap(isotope => common || outside(isotope === "d13C" ? "d13_raw" : "d18_raw")
      ? [{row:row.row_label, isotope, category:"range", hidden:true}] : []);
  });
}

/** Reframe filtered figures around visible measurements, including their error bars. */
export function fitVisibleMarkers(figure: Record<string, unknown>): Record<string, unknown> {
  const traces = (figure.data ?? []) as Record<string, unknown>[];
  const layout = {...(figure.layout as Record<string, unknown> ?? {})};
  const extents = new Map<string, number[]>();
  const identity: string[] = [];
  for (const trace of traces) {
    if (trace.visible === false || trace.visible === "legendonly" || !String(trace.mode).includes("markers")) continue;
    const vectors = ["x", "y", ...(trace.type === "scatter3d" ? ["z"] : [])].map(axis => [axis, plotlyVector(trace[axis])] as const);
    const length = vectors[0][1]?.length ?? 0;
    for (let i=0; i<length; i++) {
      if (vectors.some(([, values]) => values?.[i] == null)) continue;
      identity.push(`${trace.name}:${i}:${vectors.map(([,values])=>values?.[i]).join(",")}`);
      for (const [axis, values] of vectors) {
        const value = values?.[i];
        if (typeof value !== "number" || !Number.isFinite(value)) continue;
        const ref = String(trace[`${axis}axis`] ?? axis);
        const key = trace.type === "scatter3d" ? `${trace.scene ?? "scene"}.${axis}axis` : `${axis}axis${ref.slice(1)}`;
        const error = trace[`error_${axis}`] as {visible?:boolean;array?:unknown;arrayminus?:unknown;symmetric?:boolean} | undefined;
        const plus = error?.visible === false ? 0 : Number(plotlyVector(error?.array)?.[i] ?? 0);
        const minus = error?.symmetric === false ? Number(plotlyVector(error.arrayminus)?.[i] ?? 0) : plus;
        const bounds = extents.get(key) ?? [Infinity, -Infinity];
        bounds[0] = Math.min(bounds[0], value - (Number.isFinite(minus) ? minus : 0));
        bounds[1] = Math.max(bounds[1], value + (Number.isFinite(plus) ? plus : 0));
        extents.set(key, bounds);
      }
    }
  }
  for (const [key, [low, high]] of extents) {
    const parts = key.split(".");
    const parent = parts.length === 2 ? {...(layout[parts[0]] as object ?? {})} as Record<string,unknown> : layout;
    const name = parts.at(-1)!;
    const axis = {...(parent[name] as Record<string,unknown> ?? {})};
    if (axis.type === "log") { delete axis.range; axis.autorange = true; }
    else {
      const pad = Math.max((high-low)*.07, Math.abs(high)*.001, .001);
      axis.range = axis.autorange === "reversed" ? [high+pad,low-pad] : [low-pad,high+pad];
      axis.autorange = false;
    }
    parent[name] = axis;
    if (parts.length === 2) layout[parts[0]] = parent;
  }
  layout.meta = {...(layout.meta as object ?? {}), filteredViewport: identity.join("|")};
  return {...figure, layout};
}

export function filterStationFigure(figure: Record<string, unknown>, flags: ChartFlag[], excludeStandardCurve = false) {
  if (!Array.isArray(figure.data) || !flags.length) return figure;
  let filtered = false;
  const data = figure.data.map((trace: Record<string,unknown>) => {
    const standard = excludeStandardCurve && String(trace.name).startsWith("Standard measured ");
    const effective = standard ? flags.map(flag => ({...flag, hidden:flag.excludeFromFit !== false || flag.hidden})) : flags;
    const next = qcOutlierDisplay(trace, effective);
    if (next !== trace && effective.some(flag => flag.hidden)) filtered = true;
    return next;
  });
  const result = {...figure, data};
  return filtered ? fitVisibleMarkers(result) : result;
}

/** Match bridge identities, since sample labels need not be unique across workbooks. */
export function qcRangeRows(rows: LinearityPreviewRow[], measurements: {id:string;role?:string;material_id?:string|null}[], mapping: Record<string,string>, materialId:string) {
  const labels=new Set(measurements.filter(row=>row.role==="qc"&&row.material_id===materialId).map(row=>mapping[row.id]));
  return rows.filter(row=>labels.has(row.row_label));
}

/** QC-only charts use the same observation population for the color scale and ranges. */
export function scopeColorRows<T extends {row_label:string}>(rows:T[], labels?:readonly string[]):T[] {
  if(labels===undefined)return rows;
  const selected=new Set(labels);
  return rows.filter(row=>selected.has(row.row_label));
}

/** Match colors by bridge identity rather than by a trace's point order. */
export function stationMarkerColors(trace:Record<string,unknown>, valuesByRow?:ReadonlyMap<string,number>, range?:readonly [number,number]):Record<string,unknown> {
  if(!valuesByRow||!Array.isArray(trace.customdata)||!String(trace.mode).includes("markers"))return trace;
  const values=trace.customdata.map(point=>Array.isArray(point)?valuesByRow.get(String(point[0]))??null:null);
  if(!values.some(value=>value!=null))return trace;
  const marker={...(trace.marker as Record<string,unknown>??{})};
  if(range){
    delete marker.coloraxis;
    delete marker.cmin;
    delete marker.cmax;
    delete marker.colorscale;
    return {...trace,marker:{...marker,color:values.map(value=>value==null?"#94a3b8":viridisColor(value,range)),showscale:false}};
  }
  return {...trace,marker:{...marker,color:values,coloraxis:"coloraxis"}};
}

/** Explicit CSS colors keep every stage on the calibration control's Viridis scale. */
export function viridisColor(value:number, range:readonly [number,number]):string {
  const stops=[[68,1,84],[72,40,120],[62,74,137],[49,104,142],[38,130,142],[31,158,137],[53,183,121],[110,206,88],[181,222,43],[253,231,37]];
  const span=range[1]-range[0];
  const fraction=span>0?Math.max(0,Math.min(1,(value-range[0])/span)):.5;
  const position=fraction*(stops.length-1), index=Math.min(stops.length-2,Math.floor(position)), t=position-index;
  return "#"+stops[index].map((v,i)=>Math.round(v+(stops[index+1][i]-v)*t).toString(16).padStart(2,"0")).join("");
}
