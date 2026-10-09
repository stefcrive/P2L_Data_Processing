import { plotlyVector } from "./plotly-order";

/** Draw filled flag symbols above the observation colors with matching legend entries. */
export function processingOutlierHighlights(figure: Record<string, unknown>): Record<string, unknown> {
  if (!Array.isArray(figure.data)) return figure;
  const overlays: Record<string, unknown>[] = [];
  const labeled = new Set<string>();
  for (const trace of figure.data as Record<string, unknown>[]) {
    if (trace.yaxis === "y2" || String(trace.name ?? "").startsWith("Standard measured ")) continue;
    const categories = (trace.meta as {qcCategories?: string[]})?.qcCategories;
    if (!categories || !String(trace.mode).includes("markers")) continue;
    const x = plotlyVector(trace.x), y = plotlyVector(trace.y), z = plotlyVector(trace.z);
    if (!x || !y) continue;
    const marker = trace.marker as Record<string, unknown> ?? {};
    const symbols = categories.map((_, i) => String(Array.isArray(marker.symbol) ? marker.symbol[i] : marker.symbol ?? "square").replace(/-open$/, ""));
    const groups = new Map<string, {category: string; symbol: string; indices: number[]}>();
    categories.forEach((category, i) => {
      const symbol = symbols[i], key = `${category}:${symbol}`;
      const group = groups.get(key) ?? {category, symbol, indices: []};
      if (x[i] != null && y[i] != null && (trace.type !== "scatter3d" || z?.[i] != null)) group.indices.push(i);
      groups.set(key, group);
    });
    for (const {category, symbol, indices} of groups.values()) {
      if (category === "Unflagged observations") continue;
      if (!indices.length) continue;
      const key = `${category}:${symbol}`;
      const color = category === "Manual exclusions" ? "#c026d3" : category === "Partially Saturated Collectors" ? "#ea580c" : "#dc2626";
      overlays.push({type: trace.type ?? "scatter", mode: "markers", name: category,
        x: indices.map(i => x[i]), y: indices.map(i => y[i]),
        ...(trace.type === "scatter3d" ? {z: indices.map(i => z?.[i]), scene: trace.scene} : {xaxis: trace.xaxis, yaxis: trace.yaxis, zorder: 5}),
        customdata: Array.isArray(trace.customdata) ? indices.map(i => (trace.customdata as unknown[])[i]) : undefined,
        marker: {symbol, color, size: 12, opacity: 1, line: {color: "#7f1d1d", width: 1.2}},
        legendgroup: `processing-flag-${category}`, showlegend: !labeled.has(key),
        meta: {qcHighlight: true}, hovertemplate: "%{y:.3f}<extra>%{fullData.name}</extra>"});
      labeled.add(key);
    }
  }
  return {...figure, data: [...figure.data, ...overlays]};
}

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
    if(found.some(f=>f.category==="full"))return trace.type==="scatter3d"?"cross":"triangle-down";
    if(found.some(f=>f.category==="partial"))return "diamond";
    if(found.some(f=>f.category==="manual"))return "circle-open";
    if(found.some(f=>!f.category||f.category==="statistical"))return "square";
    const reasons=found.flatMap(f=>f.reasons??[]).join(" ").toLowerCase();
    if(reasons.includes("leak"))return trace.type==="scatter3d"?"cross":"star";
    if(reasons.includes("d13c range"))return "cross";
    if(reasons.includes("d18o range"))return "x";
    return "diamond";
  };
  const categoryFor=(found:typeof flags)=>found.some(f=>f.category==="no_signal")?"No-signal samples":found.some(f=>f.category==="pressure_adjustment")?"Poor pressure adjustment samples":found.some(f=>f.category==="full")?"Failed Samples (Fully Saturated)":found.some(f=>f.category==="partial")?"Partially Saturated Collectors":found.some(f=>f.category==="failed")?"Failed analyses":found.some(f=>f.category==="manual")?"Manual exclusions":found.some(f=>!f.category||f.category==="statistical")?"Statistical outliers":found.length?"Validity-range flags":"Unflagged observations";
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
