import { plotlyVector } from "./plotly-order";

/** Display-only masking preserves point identity, errors and trace visibility controls. */
export function qcOutlierDisplay(trace: Record<string, unknown>, flags: {row:string;isotope:string;hidden?:boolean;category?:string;reasons?:string[]}[]): Record<string, unknown> {
  if (!Array.isArray(trace.customdata) || !/markers|lines/.test(String(trace.mode ?? ""))) return trace;
  const marker=trace.marker as Record<string,unknown> ?? {};
  const flagsByRow=new Map<string,typeof flags>();
  for(const flag of flags){const row=flagsByRow.get(flag.row)??[];row.push(flag);flagsByRow.set(flag.row,row);}
  const matches=trace.customdata.map(point=>Array.isArray(point)?(flagsByRow.get(String(point[0]))??[]).filter(flag=>point[1]==="cross"||point[1]==="crossplot"||flag.isotope===point[1]):[]);
  const symbolFor=(found:typeof flags)=>{
    if(found.some(f=>!f.category||f.category==="statistical"))return "square";
    if(found.some(f=>f.category==="failed"))return trace.type==="scatter3d"?"cross":"triangle-down";
    if(found.some(f=>f.category==="manual"))return "circle-open";
    const reasons=found.flatMap(f=>f.reasons??[]).join(" ").toLowerCase();
    if(reasons.includes("leak"))return trace.type==="scatter3d"?"cross":"star";
    return "diamond";
  };
  const next:Record<string,unknown>&{marker:Record<string,unknown>}={...trace,marker:{...marker,
    symbol:(trace.meta as {partialSaturation?:boolean})?.partialSaturation ? marker.symbol : matches.map((found,i)=>found.length?symbolFor(found):Array.isArray(marker.symbol)?marker.symbol[i]:marker.symbol??"circle")}};
  const stage=(trace.meta as {correctionStage?:string})?.correctionStage;
  if(stage==="before" || stage==="after") {
    if(stage==="before") {
      next.marker.color="#c4c4c4";
      delete next.marker.coloraxis;
      delete next.marker.colorscale;
      next.marker.showscale=false;
    }
    const symbols=next.marker.symbol as string[];
    next.marker={...next.marker,size:stage==="before"?11:7,opacity:stage==="before"?.9:1,symbol:symbols.map(symbol=>{
      const base=String(symbol).replace(/-open$/, "");
      if(stage==="after")return base;
      return trace.type==="scatter3d" && !["circle","square","diamond"].includes(base) ? "diamond-open" : `${base}-open`;
    }),line:stage==="after"?{color:"rgba(255,255,255,.9)",width:.7}:{width:2}};
  }
  // Nulls break connecting lines, rather than joining across a hidden outlier.
  for(const axis of ["y","z"]){
    const values=plotlyVector(trace[axis]);
    if(values&&values.length===matches.length)Object.assign(next,{[axis]:values.map((value,i)=>matches[i].some(flag=>flag.hidden)?null:value)});
  }
  return {...next,connectgaps:false};
}

/** Diagnostics uses an orange diamond outline in addition to the observation's QC shape. */
export function partiallySaturatedOverlay(trace: Record<string, unknown>, rows: ReadonlySet<string>): Record<string, unknown> | null {
  if (!Array.isArray(trace.customdata) || !String(trace.mode ?? "").includes("markers")) return null;
  const selected = trace.customdata.map(point => Array.isArray(point) && rows.has(String(point[0])));
  if (!selected.some(Boolean)) return null;
  const stage = (trace.meta as {correctionStage?:string})?.correctionStage;
  const overlay: Record<string, unknown> = {
    ...trace, mode: "markers", name: "Partially Saturated Collectors", showlegend: false,
    meta: {partialSaturation: true}, legendgroup: stage ? `correction-${stage}` : trace.legendgroup,
    marker: {color: "#ff7f0e", symbol: "diamond-open", size: stage === "before" ? 17 : 13,
      opacity: 1, line: {width: 2, color: "#ff7f0e"}},
  };
  for (const axis of ["x", "y", "z"]) {
    const values = plotlyVector(trace[axis]);
    if (values) overlay[axis] = values.map((value, index) => selected[index] ? value : null);
  }
  return overlay;
}
