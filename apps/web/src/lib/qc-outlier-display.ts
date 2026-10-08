import { plotlyVector } from "./plotly-order";

/** Display-only masking preserves point identity, errors and trace visibility controls. */
export function qcOutlierDisplay(trace: Record<string, unknown>, flags: {row:string;isotope:string;hidden?:boolean;category?:string;reasons?:string[]}[]): Record<string, unknown> {
  if (!Array.isArray(trace.customdata) || !/markers|lines/.test(String(trace.mode ?? ""))) return trace;
  const marker=trace.marker as Record<string,unknown> ?? {};
  const flagsByRow=new Map<string,typeof flags>();
  for(const flag of flags){const row=flagsByRow.get(flag.row)??[];row.push(flag);flagsByRow.set(flag.row,row);}
  const matches=trace.customdata.map(point=>Array.isArray(point)?(flagsByRow.get(String(point[0]))??[]).filter(flag=>point[1]==="cross"||point[1]==="crossplot"||flag.isotope===point[1]):[]);
  const symbolFor=(found:typeof flags)=>{
    if(found.some(f=>f.category==="no_signal"))return "x";
    if(found.some(f=>f.category==="pressure_adjustment"))return trace.type==="scatter3d"?"diamond":"triangle-up";
    if(found.some(f=>f.category==="failed"))return trace.type==="scatter3d"?"cross":"triangle-down";
    if(found.some(f=>f.category==="manual"))return "circle-open";
    if(found.some(f=>!f.category||f.category==="statistical"))return "square";
    const reasons=found.flatMap(f=>f.reasons??[]).join(" ").toLowerCase();
    if(reasons.includes("leak"))return trace.type==="scatter3d"?"cross":"star";
    return "diamond";
  };
  const categoryFor=(found:typeof flags)=>found.some(f=>f.category==="no_signal")?"No-signal samples":found.some(f=>f.category==="pressure_adjustment")?"Poor pressure adjustment samples":found.some(f=>f.category==="failed")?"Failed analyses":found.some(f=>f.category==="manual")?"Manual exclusions":found.some(f=>!f.category||f.category==="statistical")?"Statistical outliers":found.length?"Validity-range flags":"Unflagged observations";
  const next:Record<string,unknown>&{marker:Record<string,unknown>}={...trace,marker:{...marker,
    symbol:(trace.meta as {partialSaturation?:boolean})?.partialSaturation ? marker.symbol : matches.map((found,i)=>found.length?symbolFor(found):Array.isArray(marker.symbol)?marker.symbol[i]:marker.symbol??"circle")}};
  next.meta={...(trace.meta as object??{}),qcCategories:matches.map(categoryFor)};
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

/** Every displayed QC shape gets an accurate stage/category legend entry. */
export function correctionStageLegends(traces:Record<string,unknown>[], tr:(label:string)=>string):Record<string,unknown>[] {
  const legends=new Map<string,Record<string,unknown>>();
  const labeledStages=new Set<string>();
  const data=traces.map(trace=>{
    const meta=trace.meta as {correctionStage?:string;correctionApplied?:boolean;qcCategories?:string[]}|undefined;
    if(!meta?.correctionStage||!Array.isArray(trace.customdata)||!String(trace.mode).includes("markers"))return trace;
    const group=`correction-${meta.correctionStage}${meta.correctionApplied===false?"-unchanged":""}`;
    const marker=trace.marker as Record<string,unknown>??{},ys=plotlyVector(trace.y),zs=plotlyVector(trace.z);
    trace.customdata.forEach((_,i)=>{
      if(ys?.[i]==null||(trace.type==="scatter3d"&&zs?.[i]==null))return;
      const symbol=Array.isArray(marker.symbol)?marker.symbol[i]:marker.symbol??"circle";
      const category=meta.qcCategories?.[i]??"Unflagged observations",key=`${group}:${category}:${symbol}`;
      if(legends.has(key))return;
      const name=labeledStages.has(group)?tr(category):`${trace.name}<br>${tr(category)}`;
      labeledStages.add(group);
      legends.set(key,{type:trace.type??"scatter",mode:"markers",name,legendgroup:group,
        x:[null],y:[null],...(trace.type==="scatter3d"?{z:[null]}:{}),hoverinfo:"skip",showlegend:true,
        marker:{...marker,symbol,color:Array.isArray(marker.color)?marker.color[i]:marker.color,size:7}});
    });
    return {...trace,legendgroup:group,showlegend:false};
  });
  return [...data,...Array.from(legends.values())];
}

/** Diagnostics uses an orange diamond outline in addition to the observation's QC shape. */
export function partiallySaturatedOverlay(trace: Record<string, unknown>, rows: ReadonlySet<string>): Record<string, unknown> | null {
  if (!Array.isArray(trace.customdata) || !String(trace.mode ?? "").includes("markers")) return null;
  const selected = trace.customdata.map(point => Array.isArray(point) && rows.has(String(point[0])));
  if (!selected.some(Boolean)) return null;
  const stage = (trace.meta as {correctionStage?:string})?.correctionStage;
  const overlay: Record<string, unknown> = {
    ...trace, mode: "markers", name: "Partially Saturated Collectors", showlegend: false,
    meta: {partialSaturation: true}, legendgroup: stage ? `correction-${stage}${(trace.meta as {correctionApplied?:boolean})?.correctionApplied===false?"-unchanged":""}` : trace.legendgroup,
    marker: {color: "#ff7f0e", symbol: "diamond-open", size: stage === "before" ? 17 : 13,
      opacity: 1, line: {width: 2, color: "#ff7f0e"}},
  };
  for (const axis of ["x", "y", "z"]) {
    const values = plotlyVector(trace[axis]);
    if (values) overlay[axis] = values.map((value, index) => selected[index] ? value : null);
  }
  return overlay;
}
