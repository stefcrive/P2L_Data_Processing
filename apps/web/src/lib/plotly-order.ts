/** Plotly 6 serializes numeric vectors as dtype/base64 objects. */
export function plotlyVector(value: unknown): unknown[] | null {
  if (Array.isArray(value)) return value;
  if (ArrayBuffer.isView(value)) return Array.from(value as unknown as ArrayLike<number>);
  if (!value || typeof value !== "object") return null;
  const encoded = value as {dtype?:string;bdata?:string};
  if (!encoded.dtype || !encoded.bdata) return null;
  try {
    const binary = atob(encoded.bdata);
    const view = new DataView(Uint8Array.from(binary, c=>c.charCodeAt(0)).buffer);
    const readers: Record<string,[number,(offset:number)=>number]> = {
      f8:[8,i=>view.getFloat64(i,true)], f4:[4,i=>view.getFloat32(i,true)],
      i4:[4,i=>view.getInt32(i,true)], u4:[4,i=>view.getUint32(i,true)],
      i2:[2,i=>view.getInt16(i,true)], u2:[2,i=>view.getUint16(i,true)],
      i1:[1,i=>view.getInt8(i)], u1:[1,i=>view.getUint8(i)],
    };
    const reader=readers[encoded.dtype];
    if (!reader || view.byteLength%reader[0]) return null;
    return Array.from({length:view.byteLength/reader[0]},(_,i)=>reader[1](i*reader[0]));
  } catch { return null; }
}

/** Reorder all point-aligned fields together; never discard observations or error bars. */
export function orderTraceByX(trace: Record<string, unknown>): Record<string, unknown> {
  const x=plotlyVector(trace.x);
  if (!x || trace.z || !String(trace.mode).includes("lines")) return trace;
  const number=(value:unknown)=>{const n=value==null||value===""?NaN:Number(value);return Number.isFinite(n)?n:Infinity;};
  const indices=x.map((_,i)=>i).sort((a,b)=>(number(x[a])-number(x[b]))||a-b);
  if(indices.every((index,i)=>index===i))return trace;
  const reorder=(value:unknown):unknown=> {
    const vector=plotlyVector(value);
    if(vector)return vector.length===x.length?indices.map(i=>vector[i]):value;
    return value;
  };
  const next={...trace};
  for(const key of ["x","y","text","hovertext","customdata","ids","hovertemplate"])next[key]=reorder(trace[key]);
  for(const key of ["marker","error_x","error_y"]){
    const original=trace[key];
    if(original&&typeof original==="object")next[key]=Object.fromEntries(Object.entries(original).map(([name,value])=>[name,["color","size","symbol","text","opacity","array","arrayminus"].includes(name)?reorder(value):value]));
  }
  next.connectgaps=false;
  return next;
}

/** Align sample and standard means without changing values, units or units per pixel. */
export function alignedIsotopeAxes(traces: Record<string, unknown>[], reversed = false) {
  const groups = [new Map<string, number>(), new Map<string, number>()];
  const bounds = [[Infinity, -Infinity], [Infinity, -Infinity]];
  traces.forEach((trace, ti) => {
    if (trace.visible === false || trace.visible === "legendonly" || trace.z) return;
    const axis = trace.yaxis === "y2" ? 1 : trace.yaxis == null || trace.yaxis === "y" ? 0 : -1;
    if (axis < 0) return;
    const y = plotlyVector(trace.y), ids = plotlyVector(trace.customdata);
    const error = trace.error_y as {array?:unknown;arrayminus?:unknown;visible?:boolean} | undefined;
    const plus = error?.visible === false ? null : plotlyVector(error?.array);
    const minus = error?.visible === false ? null : plotlyVector(error?.arrayminus) ?? plus;
    const meta = trace.meta as Record<string, unknown> | undefined;
    const observation = String(trace.mode ?? "").includes("markers") && (!meta?.sessionUncertainty || meta?.finalOnly)
      && !meta?.uncertaintyEnvelope && !/duplicate|duplicat|calibrated|running average/i.test(String(trace.name));
    y?.forEach((value, i) => {
      if (typeof value !== "number" || !Number.isFinite(value)) return;
      bounds[axis][0] = Math.min(bounds[axis][0], value - Math.abs(Number(minus?.[i]) || 0));
      bounds[axis][1] = Math.max(bounds[axis][1], value + Math.abs(Number(plus?.[i]) || 0));
      if (observation) {
        const point = ids?.[i];
        const id = Array.isArray(point) && point[0] != null ? String(point[0]) : `${ti}:${i}`;
        if (!groups[axis].has(id)) groups[axis].set(id, value);
      }
    });
  });
  if (groups.some(g => !g.size)) return null;
  const means = groups.map(g => [...g.values()].reduce((a,b) => a+b,0) / g.size);
  const offset = means[1] - means[0];
  const low = Math.min(bounds[0][0], bounds[1][0] - offset);
  const high = Math.max(bounds[0][1], bounds[1][1] - offset);
  const padding = Math.max((high-low)*.06, .001);
  const primary = reversed ? [high+padding, low-padding] : [low-padding, high+padding];
  return {primary, secondary:primary.map(v => v+offset), offset, means};
}
