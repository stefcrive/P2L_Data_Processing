import type { Normalization, SessionRow, Isotope } from "./metrology";

export type EnvelopePoint = { x: number | string | null; value: number | null | undefined; uncertainty: number | null | undefined; segment?: string };
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

/** Plotly 6 serializes numeric vectors as dtype/base64 objects. */
function vector(value: unknown): unknown[] | null {
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

/** Pointwise intervals, separated at workbook boundaries and missing budgets. */
export function uncertaintyEnvelope(points: EnvelopePoint[], name: string, fillcolor: string, yaxis = "y") {
  const x: (number | string | null)[] = [], y: (number | null)[] = [];
  let segment: EnvelopePoint[] = [];
  let previous: string | undefined;
  const closeSegment = () => {
    if (!segment.length) return;
    // Independent closed polygons avoid Plotly filling across missing observations.
    for (const point of segment) { x.push(point.x); y.push(point.value! - point.uncertainty!); }
    for (const point of [...segment].reverse()) { x.push(point.x); y.push(point.value! + point.uncertainty!); }
    x.push(segment[0].x, null); y.push(segment[0].value! - segment[0].uncertainty!, null);
    segment = [];
  };
  for (const point of points) {
    if (point.segment !== previous) closeSegment();
    previous = point.segment;
    const valid = (finite(point.x) || typeof point.x === "string" && point.x.length > 0) && finite(point.value) && finite(point.uncertainty) && point.uncertainty >= 0;
    if (valid) segment.push(point); else closeSegment();
  }
  closeSegment();
  if (!x.length) return [];
  return [{ type: "scatter", mode: "lines", x, y, yaxis, connectgaps: false, hoverinfo: "skip", line: { width: 0 },
    legendgroup: name, meta: { uncertaintyEnvelope: true }, name, fill: "toself", fillcolor, showlegend: true }];
}

/** Add canonical final results to the reused processing figures; raw traces stay unchanged. */
export function withSessionUncertainty(figure: Record<string, unknown> | undefined, rows: Record<string, SessionRow>, label: string) {
  if (!figure || !Array.isArray(figure.data) || !Object.keys(rows).length) return figure;
  const data = figure.data as Record<string, unknown>[];
  if (data.some(trace => (trace.meta as Record<string, unknown>)?.sessionUncertainty)) return figure;
  const seen = new Set<string>(), overlays: Record<string, unknown>[] = [];
  for (const trace of data) {
    const traceX=vector(trace.x), traceY=vector(trace.y), traceZ=vector(trace.z);
    if (!Array.isArray(trace.customdata) || !traceX || !traceY) continue;
    const custom = trace.customdata as unknown[][];
    const token = custom.find(point => Array.isArray(point) && point[1])?.[1];
    if (!["d13C", "d18O", "cross"].includes(String(token))) continue;
    const iso: Isotope = token === "d18O" ? "d18o" : "d13c";
    const cross = token === "cross", three = trace.type === "scatter3d";
    const indices = custom.flatMap((point, index) => {
      const id = String(point?.[0] ?? ""), row = rows[id], key = `${token}:${id}`;
      if (!row || seen.has(key)) return [];
      seen.add(key); return [index];
    });
    if (!indices.length) continue;
    const selected = indices.map(i => rows[String(custom[i][0])]);
    const usable = (row: SessionRow, isotope: Isotope) => {
      const result = row.isotopes?.[isotope];
      return !row.excluded && result?.budget && finite(result.budget.expanded_uncertainty) && finite(result.value) ? result : undefined;
    };
    const x = indices.map((i, n) => cross ? usable(selected[n], "d18o")?.value ?? null : traceX[i] as number|string);
    const y = selected.map(row => usable(row, iso)?.value ?? null);
    const u = selected.map(row => usable(row, iso)?.budget?.expanded_uncertainty ?? null);
    const shade = iso === "d13c" ? "rgba(33,94,197,0.18)" : "rgba(22,125,135,0.18)";
    const yaxis = String(trace.yaxis ?? "y");
    if (!cross && !three) overlays.push(...uncertaintyEnvelope(selected.map((row, i) => ({ x:x[i], value:y[i], uncertainty:u[i], segment:row.run_id })), label, shade, yaxis).map(t => ({...t, xaxis:trace.xaxis, showlegend:false})));
    overlays.push({ type: three ? "scatter3d" : "scatter", mode:"markers", name:label,
      x, y, ...(three ? {z:indices.map(i => traceZ?.[i]), scene:trace.scene} : {xaxis:trace.xaxis, yaxis}),
      marker:{size:three?4:6,color:iso==="d13c"?"#215ec5":"#167d87"},
      error_y:{type:"data",array:u,visible:true,thickness:1,width:2},
      ...(cross ? {error_x:{type:"data",array:selected.map(row=>usable(row,"d18o")?.budget?.expanded_uncertainty??null),visible:true,thickness:1,width:2}} : {}),
      customdata:indices.map(i=>custom[i]), text:selected.map(row=>`${row.identifier1??row.label} · ${row.identifier2??row.comment} · ${row.species??""}`),
      hovertemplate:"%{text}<br>%{y:.3f} ‰<extra>%{fullData.name}</extra>",
      legendgroup:"session-uncertainty",showlegend:!overlays.some(t=>(t.meta as Record<string,unknown>)?.sessionUncertainty),meta:{sessionUncertainty:true},
    });
  }
  return {...figure,data:[...data,...overlays]};
}

/** Display the frozen anchor model's pointwise k*u_norm; never refit or normalize samples. */
export function normalizationEnvelope(model: Normalization, coverageFactor: number, name: string, fillcolor: string) {
  const [m1, m2] = model.measured;
  const covariance = model.input_covariance;
  if (!finite(m1) || !finite(m2) || m1 === m2 || !finite(coverageFactor) || coverageFactor <= 0
    || covariance.length !== 4 || covariance.some(row => row.length !== 4 || row.some(v => !finite(v)))) return [];
  const low = Math.min(m1, m2), high = Math.max(m1, m2);
  return uncertaintyEnvelope(Array.from({ length: 81 }, (_, index) => {
    const x = low + (high - low) * index / 80;
    const t = (x - m1) / (m2 - m1), b = model.slope;
    const jacobian = [1 - t, t, b * (t - 1), -b * t];
    const variance = jacobian.reduce((sum, ji, i) => sum + jacobian.reduce((inner, jj, j) => inner + ji * covariance[i][j] * jj, 0), 0);
    return { x, value: model.intercept + b * x, uncertainty: variance < -1e-12 ? null : coverageFactor * Math.sqrt(Math.max(0, variance)) };
  }), name, fillcolor);
}

export type CalibrationStages = {before:(number|null)[];after:(number|null)[]};
/** Add paired canonical stages in the figure's existing isotope-axis assignment. */
export function withCalibrationStages(figure:Record<string,unknown>, values:Map<string,CalibrationStages>, labels:{before:string;after:string}) {
  const traces=(Array.isArray(figure.data)?figure.data:[]) as Record<string,unknown>[];
  const layout=(figure.layout??{}) as Record<string,unknown>,scene=layout.scene as Record<string,unknown>|undefined;
  const isotopeAxis=(axis:unknown,fallback:number)=>{
    const title=(axis as {title?:unknown})?.title;
    const label=typeof title==="string"?title:String((title as {text?:string})?.text??"");
    return /18|¹⁸/.test(label)?1:/13|¹³/.test(label)?0:fallback;
  };
  const xi=isotopeAxis(scene?.xaxis??layout.xaxis,1),yi=isotopeAxis(scene?.yaxis??layout.yaxis,0),zi=isotopeAxis(scene?.zaxis,-1);
  const isotopeNames=["δ¹³C","δ¹⁸O"],used=new Set<string>();
  const additions=traces.flatMap(trace=>{
    const custom=vector(trace.customdata) as unknown[][]|null, xs=vector(trace.x),zs=vector(trace.z),texts=vector(trace.text);
    if(!custom||!xs||!String(trace.mode).includes("markers"))return [];
    const indices=custom.map((point,i)=>{const id=String(point[0]);if(!values.has(id)||used.has(id))return -1;used.add(id);return i;}).filter(i=>i>=0);
    if(!indices.length)return [];
    return (["before","after"] as const).map(stage=>({...trace,name:labels[stage],meta:{...(trace.meta as object??{}),correctionStage:stage},legendgroup:stage,showlegend:true,mode:"markers",
      x:indices.map(i=>values.get(String(custom[i][0]))![stage][xi]),y:indices.map(i=>values.get(String(custom[i][0]))![stage][yi]),
      ...(zs?{z:indices.map(i=>zi<0?zs[i]:values.get(String(custom[i][0]))![stage][zi])}:{}),
      customdata:indices.map(i=>custom[i]),text:texts?indices.map(i=>texts[i]):trace.text,
      // Imported error bars belong to the imported values; retain them on that trace.
      error_x:undefined,error_y:undefined,error_z:undefined,
      marker:{size:stage==="before"?8:6,symbol:stage==="before"?"circle-open":"circle",color:stage==="before"?"#94a3b8":"#215ec5"},
      hovertemplate:`${isotopeNames[xi]}: %{x:.4f} ‰<br>${isotopeNames[yi]}: %{y:.4f} ‰<extra>%{fullData.name}</extra>`}));
  });
  return {...figure,data:[...traces,...additions]};
}
