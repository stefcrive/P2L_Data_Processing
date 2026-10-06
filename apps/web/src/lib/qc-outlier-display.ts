import { plotlyVector } from "./plotly-order";

/** Display-only masking preserves point identity, errors and trace visibility controls. */
export function qcOutlierDisplay(trace: Record<string, unknown>, flags: {row:string;isotope:string;hidden?:boolean;category?:string;reasons?:string[]}[]): Record<string, unknown> {
  if (!Array.isArray(trace.customdata) || !String(trace.mode ?? "").includes("markers")) return trace;
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
    symbol:matches.map((found,i)=>found.length?symbolFor(found):Array.isArray(marker.symbol)?marker.symbol[i]:marker.symbol??"circle")}};
  const stage=(trace.meta as {correctionStage?:string})?.correctionStage;
  if(stage==="before" || stage==="after") {
    const symbols=next.marker.symbol as string[];
    next.marker={...next.marker,size:stage==="before"?9:6.5,opacity:stage==="before"?.72:.94,symbol:symbols.map(symbol=>{
      const base=String(symbol).replace(/-open$/, "");
      if(stage==="after")return base;
      return trace.type==="scatter3d"?"diamond-open":"x-thin";
    }),line:stage==="after"?{color:"rgba(255,255,255,.9)",width:.7}:undefined};
  }
  // Nulls break connecting lines, rather than joining across a hidden outlier.
  for(const axis of ["y","z"]){
    const values=plotlyVector(trace[axis]);
    if(values&&values.length===matches.length)Object.assign(next,{[axis]:values.map((value,i)=>matches[i].some(flag=>flag.hidden)?null:value)});
  }
  return {...next,connectgaps:false};
}
