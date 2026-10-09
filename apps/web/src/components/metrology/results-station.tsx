"use client";

import { useEffect, useState } from "react";
import { PlotlyChart, type PlotlyChartProps } from "@/components/charts/plotly-chart";
import { useTranslation } from "@/components/layout/language-provider";
import { fmt, isotopeLabel, isotopes, metroRequest, type Fit, type HistoryGroup, type Isotope, type RunDetail, type State } from "@/lib/metrology";
import { Empty, Field, Inspect, Panel, Status } from "./shared";
import { normalizationEnvelope } from "@/lib/metrology-envelopes";

const colors = ["#1f5fbf", "#6478ba", "#c38835", "#c04d65"];
const axis = (text: string) => ({ title: { text }, automargin: true, gridcolor: "#e8edf1", zerolinecolor: "#e8edf1", zerolinewidth: 1 });
export function Chart({ data, title, x, y, shapes = [], date = false, height = 340, annotations = [], layout = {}, interactions = {}, legendCollapsed = false }: { data: unknown[]; title: string; x: string; y: string; shapes?: unknown[]; date?: boolean; height?: number; annotations?: unknown[]; layout?: Record<string, unknown>; interactions?: Pick<PlotlyChartProps,"onPointClick"|"onSelection"|"onPointHover"|"onHoverEnd">; legendCollapsed?: boolean }) {
  const tr = useTranslation();
  const hasPoints = data.some(trace => {
    const t = trace as { x?: unknown[]; y?: unknown[] };
    return t.y?.some((v, i) => typeof v === "number" && Number.isFinite(v) && t.x?.[i] != null && (!date || Number.isFinite(Date.parse(String(t.x[i])))));
  });
  if (!hasPoints) return <div className="station-empty-chart"><h3>{title}</h3><Empty>{tr("No acquisitions available for this chart. Import data or select another population.")}</Empty></div>;
  return <div className="metro-plot"><PlotlyChart {...interactions} minHeight={200} maxHeight={960} verticallyResizable collapsibleLegend legendCollapsed={legendCollapsed} figure={{ data, layout: {
    title: { text: title, font: { size: 13 }, x: .02, xanchor: "left" }, height, margin: { l: 54, r: 14, t: 49, b: 43 },
    paper_bgcolor: "transparent", plot_bgcolor: "transparent", font: { family: "var(--font-sans), Segoe UI, sans-serif", size: 12, color: "#475569" },
    xaxis: { ...axis(x), ...(date ? { type: "date" } : {}) }, yaxis: axis(y), shapes, annotations,
    legend: { orientation: "h", y: 1.08, x: 0, font: { size: 10 } }, hovermode: "closest", ...layout,
  } }} /></div>;
}
const line = (y: number, color = "#a7b6bf", dash = "dot") => ({ type: "line", xref: "paper", x0: 0, x1: 1, y0: y, y1: y, line: { color, dash, width: 1 } });
export function QcControlChart({ group, iso }: { group: HistoryGroup; iso: Isotope }) {
  const tr = useTranslation();
  const s = group.isotopes[iso];
  const omitted = new Set(s.outlier_ids ?? []), flagged = new Set(s.flags.map(f=>f.id));
  const points = s.points.filter(p=>!omitted.has(p.id));
  const x = [points[0]?.at, points.at(-1)?.at];
  const reference = (name: string, value: number | null, color: string, dash: string, showlegend = true) => value == null ? [] : [{type:"scatter", mode:"lines", name:tr(name), legendgroup:name, showlegend, x, y:[value,value], line:{color,dash,width:1.5}}];
  return <Chart title={`${isotopeLabel[iso]} · ${group.material.name}`} x={tr("Acquisition date")} y={`${isotopeLabel[iso]} / ‰ VPDB`} date data={[
    {type:"scatter",mode:"lines+markers",name:group.material.name,x:points.map(p=>p.at),y:points.map(p=>p.value),marker:{size:8,color:points.map(p=>flagged.has(p.id)||!p.qc_passed?"#bd4356":"#16827b")},line:{color:"#16827b",width:1}},
    ...reference("True value",s.target,"#64748b","dash"), ...reference("Average",s.mean,"#16827b","solid"),
    ...reference("Average ±1σ",s.mean!=null&&s.sd!=null?s.mean+s.sd:null,"#16827b","dot"),
    ...reference("Average ±1σ",s.mean!=null&&s.sd!=null?s.mean-s.sd:null,"#16827b","dot",false),
    ...(s.limits??[]).flatMap((v,i)=>reference("±3 SD limits",v,"#bd4356","dash",i===0)),
  ]}/>;
}
function fitTraces(fit: Fit | undefined, name: string, color: string, offset = 0) {
  if (!fit?.points?.length) return [];
  const xx = fit.points.map(p => p.x), yy = fit.points.map(p => p.y - offset);
  const span = [Math.min(...xx), Math.max(...xx)];
  return [{ type: "scatter", mode: "markers", name, x: xx, y: yy, marker: { color, size: 7, opacity: .8 } },
    ...(fit.slope == null || fit.intercept == null ? [] : [{ type: "scatter", mode: "lines", name: `${name} fit`, showlegend: false,
      x: span, y: span.map(x => fit.intercept! + fit.slope! * x - offset), line: { color, width: 2 } }])];
}

export function QualificationStation({ runId, state }: { runId?: string; state: State }) {
  const tr = useTranslation();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    metroRequest<RunDetail>(`/runs/${runId}`, { signal: controller.signal }).then(setRun).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [runId, state]);
  if (error) return <p role="alert">{tr(error)}</p>;
  return run && run.id === runId ? <Panel title="Carousel Results Station"><ResultsStation run={run} state={state} /></Panel> : null;
}

export function ResultsStation({ run, state, fixedView, includedIds }: { run: RunDetail; state: State; fixedView?: string; includedIds?: string[] }) {
  const tr = useTranslation();
  const numberText = (value: number | null | undefined, digits = 3) => tr(fmt(value, digits));
  const [iso, setIso] = useState<Isotope>("d13c");
  const [view, setView] = useState(fixedView ?? (run.context === "qualification" ? "residuals" : "results"));
  const evaluation = run.evaluation;
  const materials = (evaluation?.diagnostics_comparable ?? evaluation?.diagnostics)?.materials ?? [];
  const [materialId, setMaterial] = useState(materials.find(m => m.label === "SHP2L")?.material_id ?? materials[0]?.material_id ?? "");
  const material = materials.find(m => m.material_id === materialId) ?? materials[0];
  const before = material?.isotopes[iso];
  const after = evaluation?.diagnostics_after?.materials.find(m => m.material_id === material?.material_id)?.isotopes[iso];
  const rows = evaluation?.results.filter(r => !r.excluded && r.isotopes?.[iso] && (!includedIds || r.role !== "unknown" || includedIds.includes(r.id))) ?? [];
  const [sampleId, setSample] = useState(rows.find(r => r.role === "unknown")?.id ?? rows[0]?.id ?? "");
  const selected = rows.find(r => r.id === sampleId) ?? rows[0];
  const result = selected?.isotopes?.[iso];
  const assigned = state.materials.find(m => m.id === material?.material_id)?.assigned[iso].value ?? 0;
  const method = state.methods.find(m => m.id === run.method_id);
  const effect = method?.config.corrections?.[iso];
  const model = evaluation?.normalization[iso];
  if (!evaluation) return <Empty>{tr("Evaluate this import to populate the Results Station.")}</Empty>;
  const diagnosticPlots: { title: string; x: string; key: "mass_dependence" | "intensity_dependence" | "pressure_residual" | "drift" | "memory" }[] = [
    { title: "Mass dependency", x: "Carbonate mass / µg", key: "mass_dependence" },
    { title: "Residual intensity linearity", x: "Mean I44 / V", key: "intensity_dependence" },
    { title: "Pressure mismatch after intensity detrending", x: "Result − target intensity / V", key: "pressure_residual" },
    { title: "QC drift", x: "Analysis sequence", key: "drift" },
    { title: "QC memory screening", x: "Preceding sample − QC mean / ‰", key: "memory" },
  ];
  return <div className="metro-stack">
    {!fixedView && <div className="metro-station-heading"><h3>{tr(run.context === "qualification" ? "From carousel to validated method" : "From imported value to release decision")}</h3><Status value={evaluation.ready ? "pass" : "blocked"} /></div>}
    {!fixedView && <div className="metro-tabs" role="tablist" aria-label={tr("Results Station views")}>{["residuals", "carousel", "normalization", "results", "uncertainty"].map(tab => <button key={tab} role="tab" aria-selected={view === tab} onClick={() => setView(tab)}>{tr(tab[0].toUpperCase()+tab.slice(1))}</button>)}</div>}
    <div className="metro-form-grid"><Field label={tr("Isotope")}><select value={iso} onChange={e => setIso(e.target.value as Isotope)}>{isotopes.map(i => <option key={i} value={i}>{isotopeLabel[i]}</option>)}</select></Field>{view === "residuals" && <Field label={tr("Homogeneous material")}><select value={material?.material_id ?? ""} onChange={e => setMaterial(e.target.value)}>{materials.map(m => <option key={m.material_id} value={m.material_id}>{m.label} · n={m.n}</option>)}</select></Field>}</div>
    {view === "residuals" && <CorrectionVerification run={run}/>}
    {view === "residuals" && before && <>
      <div className="metro-note">{tr("Gray: before residual correction on the same VPDB scale. Blue: after correction. The normalization slope is held fixed for this comparison. A trend alone does not authorize a correction.")}</div>
      <div className="metro-table-wrap"><table><thead><tr><th>{tr("Residual effect")}</th><th>{tr("Screening")}</th><th>{tr("Effect across observed range / ‰")}</th><th>{tr("Practical threshold / ‰")}</th><th>{tr("After correction")}</th></tr></thead><tbody>{diagnosticPlots.map(p=><tr key={p.key}><td>{tr(p.title)}</td><td><Status value={before[p.key].screening_status??"insufficient_evidence"}/></td><td>{numberText(before[p.key].effect_span,4)}</td><td>{numberText(before[p.key].practical_threshold,3)}</td><td><Status value={after?.[p.key].screening_status??"insufficient_evidence"}/></td></tr>)}</tbody></table></div>
      <p className="metro-muted">{tr("Detected means the slope interval excludes zero and the effect span exceeds the configured threshold. Reproducibility and independent validation still require review; these are multiple exploratory tests.")}</p>
      {evaluation.correction_review?.[iso]&&<Chart title={tr("Paired QC before and after residual correction")} x={tr("Analysis sequence")} y={`${isotopeLabel[iso]} / ‰ VPDB`} data={(["before","after"] as const).map((stage,i)=>({type:"scatter",mode:"lines+markers",name:tr(i?"After correction":"Before correction"),x:evaluation.correction_review![iso].points.map(p=>p.sequence),y:evaluation.correction_review![iso].points.map(p=>p[stage]),line:{color:i?"#1f5fbf":"#94a3b8"},marker:{size:8}}))}/>}
      {effect && <p className="metro-muted">{tr("Applied residual model")}: {effect.name} · c = {numberText(effect.slope,6)} ± {numberText(effect.u_slope,6)} ‰/{effect.predictor === "i44_v" ? "V" : effect.predictor}. {tr("Qtegra instrumental linearity is a separately documented stage.")}</p>}
      <div className="metro-grid">{diagnosticPlots.map(({title,x,key}) => <section key={key}>
        <Chart title={tr(title)} x={tr(x)} y={key === "pressure_residual" ? "Isotope residual / ‰" : `${isotopeLabel[iso]} − assigned / ‰`} data={[
          ...fitTraces(before[key],tr("Before correction"),"#94a3b8",key === "pressure_residual" ? 0 : assigned),
          ...fitTraces(after?.[key],tr("Final"),"#1f5fbf",key === "pressure_residual" ? 0 : assigned),
        ]} shapes={[line(0)]} />
        <p className="metro-muted">{before[key].status === "estimated" ? `${tr("Before-correction slope")}: ${numberText(before[key].slope,6)}; ${tr("final slope")}: ${numberText(after?.[key].slope,6)}. ${tr("Final 95% CI")}: [${after?.[key].slope_ci95?.map(v => numberText(v,6)).join(", ") ?? tr("unavailable")}].` : tr("Insufficient independent points or predictor range for this fit.")}</p>
      </section>)}<section><Chart title={tr("Independent QC bias after processing")} x={tr("Analysis sequence")} y="QC − assigned / ‰" data={[{type:"scatter",mode:"markers+lines",name:"SHP2L residual",x:rows.filter(r=>r.role==="qc").map(r=>r.sequence),y:rows.filter(r=>r.role==="qc").map(r=>r.isotopes![iso]!.residual_to_assigned),marker:{color:"#1f5fbf"}}]} shapes={[line(0,"#1f5fbf"), ...[1,-1].map(sign=>line(sign*(method?.config.qc.bias[iso] ?? 0),"#c38835"))]} /><p className="metro-muted">{tr("Mean bias")}: {numberText(evaluation.qc.isotopes[iso].bias)}‰. {tr("Dashed lines show the run-mean bias criterion; individual QC points are shown for diagnosis.")}</p></section></div>
      <Chart title={tr("Signal intensity versus pressure adjustment difference")} x="Mean I44 / V" y={tr("Result − target intensity / V")} data={[{type:"scatter",mode:"markers",name:material?.label,x:evaluation.results.filter(r=>r.material_id===material?.material_id&&!r.excluded).map(r=>r.i44_v),y:evaluation.results.filter(r=>r.material_id===material?.material_id&&!r.excluded).map(r=>r.pressure_mismatch_v),marker:{color:"#c38835",size:8}}]}/>
      <p className="metro-muted">{tr("Intensity–pressure correlation")}: {numberText(material?.intensity_pressure_correlation)}. {tr("Missing pressure-adjustment values remain absent; sample/reference intensity differences are a separate diagnostic.")}</p>
      <Inspect title={tr("Regression evidence and covariance after processing")} value={evaluation.diagnostics_after} />
    </>}
    {view === "carousel" && <>
      <p className="metro-muted">{tr("Each tile is an analysis in acquisition order. Color identifies the material; mass and replicate position remain visible.")}</p>
      <div className="metro-carousel">{rows.map(r => <div key={r.id} style={{borderTopColor:colors[Math.max(0,materials.findIndex(m=>m.material_id===r.material_id))%colors.length]}} title={`${r.label}: ${numberText(r.isotopes?.[iso]?.value)}‰`}><small>#{r.sequence}</small><b>{r.label}</b><span>{numberText(r.mass_ug,0)} µg</span></div>)}</div>
      <div className="metro-grid"><Chart title={tr("Final material response by mass")} x="Mass / µg" y={`${isotopeLabel[iso]} − assigned / ‰`} data={materials.map((m,i)=>({type:"scatter",mode:"markers",name:m.label,x:rows.filter(r=>r.material_id===m.material_id).map(r=>r.mass_ug),y:rows.filter(r=>r.material_id===m.material_id).map(r=>r.isotopes![iso]!.residual_to_assigned),marker:{color:colors[i%colors.length],size:9}}))} shapes={[line(0)]}/>
      <Chart title={tr("Within-mass repeatability after processing")} x="Mass / µg" y="Aliquot SD / ‰" data={(evaluation.diagnostics_after?.materials ?? []).map((m,i)=>({type:"scatter",mode:"markers+lines",name:m.label,x:m.isotopes[iso].repeatability_by_mass?.map(s=>s.mass_ug),y:m.isotopes[iso].repeatability_by_mass?.map(s=>s.sd),marker:{color:colors[i%colors.length]}}))} shapes={[line(method?.config.qc.external_sd[iso] ?? 0,"#c38835")]}/></div>
      <div className="metro-table-wrap"><table><thead><tr><th>{tr("Material")}</th><th>{tr("Mass / µg")}</th><th>n</th><th>{tr("Mean / ‰")}</th><th>SD / ‰</th><th>SE / ‰</th></tr></thead><tbody>{evaluation.diagnostics_after?.materials.flatMap(m=>m.isotopes[iso].repeatability_by_mass?.map(s=><tr key={`${m.material_id}-${s.mass_ug}`}><td>{m.label}</td><td>{s.mass_ug}</td><td>{s.n}</td><td>{numberText(s.mean)}</td><td>{numberText(s.sd)}</td><td>{numberText(s.se_mean)}</td></tr>))}</tbody></table></div>
    </>}
    {view === "normalization" && model && <>
      <div className="metro-grid"><Chart title={tr("Dual-point normalization after residual correction")} x="Corrected instrument delta / ‰" y={`${isotopeLabel[iso]} / ‰ VPDB`} data={[
        ...normalizationEnvelope(model, method?.config.coverage_factor ?? NaN, tr("Normalization envelope"), "rgba(31,95,191,0.16)"),
        {type:"scatter",mode:"lines+markers",name:tr("Anchor means"),x:model.measured,y:model.assigned,line:{color:colors[0]},marker:{size:11},error_x:{type:"data",array:[2,3].map(i=>Math.sqrt(model.input_covariance[i][i])),visible:true},error_y:{type:"data",array:[0,1].map(i=>Math.sqrt(model.input_covariance[i][i])),visible:true}},
        {type:"scatter",mode:"markers",name:tr("Samples / independent QC"),x:rows.filter(r=>r.role!=="anchor").map(r=>(r.isotopes![iso]!.value-model.intercept)/model.slope),y:rows.filter(r=>r.role!=="anchor").map(r=>r.isotopes![iso]!.value),text:rows.filter(r=>r.role!=="anchor").map(r=>r.label),marker:{size:7,color:colors[2]}},
      ]}/><Chart title={tr("Sample-specific normalization uncertainty")} x={`${isotopeLabel[iso]} / ‰ VPDB`} y="u_norm / ‰" data={[{type:"scatter",mode:"markers",name:"u_norm",x:rows.map(r=>r.isotopes![iso]!.value),y:rows.map(r=>r.isotopes![iso]!.u_norm),text:rows.map(r=>r.label),marker:{size:8,color:colors[0]}}]}/></div>
      <p className="metro-muted">{tr("Anchor error bars show standard uncertainties of their corrected measured means and assigned values. The independent QC does not set the fitted line. u_norm varies with position between the anchors and includes the complete anchor covariance.")}</p>
      <div className="metro-metrics"><div><small>{tr("Normalization slope")}</small><strong>{numberText(model.slope,6)}</strong></div><div><small>{tr("Intercept / ‰")}</small><strong>{numberText(model.intercept,6)}</strong></div></div>
      <Inspect title={tr("Frozen anchor model and covariance")} value={model}/>
    </>}
    {view === "results" && <>
      <Chart title={tr("Original exported observations")} x={tr("Analysis sequence")} y={`${isotopeLabel[iso]} / ‰ VPDB`} data={[{type:"scatter",mode:"markers",name:tr("Imported"),x:evaluation.results.filter(r=>!r.excluded&&(!includedIds||r.role!=="unknown"||includedIds.includes(r.id))).map(r=>r.sequence),y:evaluation.results.filter(r=>!r.excluded&&(!includedIds||r.role!=="unknown"||includedIds.includes(r.id))).map(r=>r[iso]),text:evaluation.results.filter(r=>!r.excluded&&(!includedIds||r.role!=="unknown"||includedIds.includes(r.id))).map(r=>`${r.label} · ${r.comment}`),marker:{color:"#94a3b8",size:7}}]}/>
      <Chart title={tr("Final results with expanded uncertainty")} x={tr("Analysis sequence")} y={`${isotopeLabel[iso]} / ‰ VPDB`} data={["unknown","qc","anchor"].map((role,i)=>{const subset=rows.filter(r=>r.role===role);return {type:"scatter",mode:"markers",name:tr(role),x:subset.map(r=>r.sequence),y:subset.map(r=>r.isotopes![iso]!.value),text:subset.map(r=>`${r.label}<br>${r.issues?.join("; ") || "Within criteria"}`),marker:{color:subset.map(r=>r.issues?.length?"#bd4356":colors[i]),size:8},error_y:{type:"data",array:subset.map(r=>r.isotopes![iso]!.budget?.expanded_uncertainty),visible:true}};})}/>
      <Chart title={tr("Change from imported to final result")} x={tr("Analysis sequence")} y="Final − imported / ‰" data={[{type:"scatter",mode:"markers",name:tr("Processing change"),x:rows.map(r=>r.sequence),y:rows.map(r=>r.isotopes![iso]!.value-r[iso]!),text:rows.map(r=>r.label),marker:{color:"#1f5fbf",size:8}}]} shapes={[line(0)]}/>
      <div className="metro-note">{tr("Values already normalized with the documented frozen model are retained. Uncertainty is propagated from that model without applying normalization a second time.")}</div>
      {[...new Set([...evaluation.blockers,...run.release_blockers])].length > 0 && <details className="metro-note warn"><summary>{tr("Review blockers")} · {[...new Set([...evaluation.blockers,...run.release_blockers])].length}</summary><ul>{[...new Set([...evaluation.blockers,...run.release_blockers])].map(b=><li key={b}>{tr(b)}</li>)}</ul></details>}
    </>}
    {view === "uncertainty" && result && <>
      <Field label={tr("Individual result")}><select value={selected.id} onChange={e=>setSample(e.target.value)}>{rows.map(r=><option key={r.id} value={r.id}>{r.source_index} · {r.label}</option>)}</select></Field>
      <div className="metro-metrics">{[["u_prec",result.u_prec],["u_norm",result.u_norm],["u_corr",result.u_corr],["u_combined",result.budget?.u_combined],[`U (k=${result.budget?.k ?? "?"})`,result.budget?.expanded_uncertainty]].map(([label,value])=><div key={String(label)}><small>{label}</small><strong>{numberText(value as number,5)}<small> ‰</small></strong></div>)}</div>
      <div className="metro-equations" aria-label={tr("Measurement and uncertainty equations")} dangerouslySetInnerHTML={{__html: '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block"><mi>z</mi><mo>=</mo><mi>x</mi><mo>−</mo><mi>c</mi><mo>(</mo><mi>p</mi><mo>−</mo><msub><mi>p</mi><mn>0</mn></msub><mo>)</mo><mo>;</mo><mspace width="1em"/><mi>y</mi><mo>=</mo><msub><mi>A</mi><mn>1</mn></msub><mo>+</mo><mfrac><mrow><mi>z</mi><mo>−</mo><msub><mi>M</mi><mn>1</mn></msub></mrow><mrow><msub><mi>M</mi><mn>2</mn></msub><mo>−</mo><msub><mi>M</mi><mn>1</mn></msub></mrow></mfrac><mo>(</mo><msub><mi>A</mi><mn>2</mn></msub><mo>−</mo><msub><mi>A</mi><mn>1</mn></msub><mo>)</mo></math><math xmlns="http://www.w3.org/1998/Math/MathML" display="block"><msubsup><mi>u</mi><mi>norm</mi><mn>2</mn></msubsup><mo>=</mo><msup><mi>J</mi><mi>T</mi></msup><msub><mi>Σ</mi><mi>anchors</mi></msub><mi>J</mi><mo>;</mo><mspace width="1em"/><msub><mi>u</mi><mi>c</mi></msub><mo>=</mo><msqrt><msubsup><mi>u</mi><mi>prec</mi><mn>2</mn></msubsup><mo>+</mo><msubsup><mi>u</mi><mi>norm</mi><mn>2</mn></msubsup><mo>+</mo><msubsup><mi>u</mi><mi>corr</mi><mn>2</mn></msubsup><mo>+</mo><munderover><mo>∑</mo><mi>j</mi><mrow/></munderover><msubsup><mi>u</mi><mi>j</mi><mn>2</mn></msubsup></msqrt><mo>;</mo><mspace width="1em"/><mi>U</mi><mo>=</mo><mi>k</mi><msub><mi>u</mi><mi>c</mi></msub></math>'}} />
      <p className="metro-muted">{tr("A₁/A₂ are assigned anchor values; M₁/M₂ are corrected anchor means. J contains the sample-specific sensitivities to those four inputs. Their covariance includes certificate uncertainty and uncertainty of measured means. The shared correction coefficient is propagated through sample and anchors together. u_prec is historical individual QC SD, never SD/√n.")}</p>
      <Chart title={tr("Independent standard uncertainty components")} x={tr("Component")} y="Standard uncertainty / ‰" data={[{type:"bar",x:result.budget?.components.map(c=>c.name),y:result.budget?.components.map(c=>c.u),marker:{color:colors},name:"u"}]}/>
      <Inspect title={tr("Exact uncertainty budget, sensitivities and processing provenance")} value={result}/>
    </>}
  </div>;
}

export function CorrectionVerification({run}:{run:RunDetail}) {
  const tr=useTranslation();
  const review=run.evaluation?.correction_review;
  if(!review)return null;
  return <section className="station-correction-review"><div className="station-correction-heading"><div><span className="metro-eyebrow">{tr("Independent verification")}</span><h3>{tr("Does the correction improve QC?")}</h3></div><small>{tr("Fixed coefficients · paired aliquots · VPDB")}</small></div>
    <div className="station-correction-grid">{isotopes.map(iso=>{const r=review[iso];return <section key={iso}><div className="station-correction-label"><b>{isotopeLabel[iso]}</b><Status value={r.status}/></div><div className="station-sd-comparison"><div><small>{tr("Before / SD")}</small><strong>{fmt(r.before.sd,4)}</strong></div><span aria-hidden>→</span><div><small>{tr("After / SD")}</small><strong>{fmt(r.after.sd,4)}<small> ‰</small></strong></div></div><p><b>{r.sd_reduction_fraction==null?tr("Comparison unavailable"):`${fmt(100*Math.abs(r.sd_reduction_fraction),1)}% ${tr(r.sd_reduction_fraction<0?"SD increase":"SD reduction")}`}</b> · {r.paired_n}/{r.total_qc} {tr("paired QC")}</p><p className="metro-muted">{tr("95% paired interval")}: {r.reduction_interval95?.map(v=>`${fmt(100*v,1)}%`).join(" / ")??tr("Insufficient evidence")}.</p><p className="metro-muted">{tr("Required")}: ≥ {r.criteria.minimum_qc} QC · ≥ {fmt(100*r.criteria.minimum_sd_reduction_fraction,0)}% {tr("SD reduction")}.</p>{r.reasons.length>0?<details><summary>{tr("Validation findings")} · {r.reasons.length}</summary><ul>{r.reasons.map(reason=><li key={reason}>{tr(reason)}</li>)}</ul></details>:<p>{tr("Numerical criteria satisfied; independent evidence and qualification approval still require review.")}</p>}</section>;})}</div>
    <p className="metro-muted">{tr("SD reduction alone never activates a correction. Bias, range, individual QC acceptance and independently characterized coefficients are checked together. Bootstrap intervals assume independent aliquots; drift and memory need separate review.")}</p>
  </section>;
}

export function LongTermCharts({ state, openRun, highlightRunIds = [] }: { state: State; openRun?: (id: string) => void; highlightRunIds?: string[] }) {
  const tr = useTranslation();
  const numberText = (value: number | null | undefined, digits = 3) => tr(fmt(value, digits));
  const [iso,setIso] = useState<Isotope>("d13c");
  const origins=[...new Set(state.history.map(h=>h.data_origin??"observed"))];
  const [origin,setOrigin]=useState(origins.includes("synthetic")?"synthetic":"observed");
  const selectedOrigin=origins.includes(origin)?origin:origins[0];
  const history=state.history.filter(h=>(h.data_origin??"observed")===selectedOrigin);
  const count=history.reduce((n,h)=>n+(h.isotopes[iso].total_n ?? h.isotopes[iso].points.length),0);
  const active=history.find(h=>h.method_id===state.active_method?.id);
  const qcHistoryTraces=history.flatMap((h,i)=>{
    const s=h.isotopes[iso],flags=new Set(s.flags.map(f=>f.id));
    const omitted=new Set(s.outlier_ids??[]);
    const points=s.points.filter(p=>!omitted.has(p.id)&&Number.isFinite(p.value)&&Number.isFinite(Date.parse(p.at)));
    if(!points.length)return [];
    const x=[points[0].at,points.at(-1)!.at],target=s.target,residualTarget=target??0;
    const mean=s.mean==null||target==null?null:s.mean-target;
    const reference=(name:string,value:number|null,dash:string,width=1.4,showlegend=true,legendgroup?:string)=>value==null?[]:[{type:"scatter",mode:"lines",name:`${h.population_label??`v${h.method_version}`} · ${tr(name)}`,legendgroup,showlegend,x,y:[value,value],line:{color:colors[i%colors.length],dash,width}}];
    return [
      {type:"scatter",mode:"markers",name:h.population_label??`v${h.method_version}`,x:points.map(p=>p.at),y:points.map(p=>p.value-residualTarget),text:points.map(p=>`${p.at}<br>Method v${h.method_version}${!p.qc_passed?" · failed run QC":""}`),marker:{color:points.map(p=>flags.has(p.id)||!p.qc_passed?"#bd4356":colors[i%colors.length]),size:points.map(p=>highlightRunIds.includes(p.run_id)?13:9)}},
      ...reference("True value",target==null?null:0,"dash",1.6),
      ...reference("Average",mean,"solid",2),
      ...reference("Average ±1σ",mean!=null&&s.sd!=null?mean+s.sd:null,"dot",1.2,true,`sigma${i}`),
      ...reference("Average ±1σ",mean!=null&&s.sd!=null?mean-s.sd:null,"dot",1.2,false,`sigma${i}`),
      ...(s.limits?s.limits.flatMap((limit,j)=>reference("±3 SD limits",limit-residualTarget,"dot",1,j===0,`limits${i}`)):[]),
    ];
  });
  return <Panel><div className="station-panel-toolbar"><h2>{tr("Long-term independent QC")}</h2>
    <Field label={tr("Evidence population")}><select value={selectedOrigin??"observed"} onChange={e=>setOrigin(e.target.value)}>{origins.length ? origins.map(o=><option key={o} value={o}>{tr(o==="synthetic"?"Mock QC history":"Observed QC from raw exports")}</option>) : <option value="observed">{tr("No QC acquisitions")}</option>}</select></Field>
    <Field label={tr("QC isotope")}><select value={iso} onChange={e=>setIso(e.target.value as Isotope)}>{isotopes.map(i=><option key={i} value={i}>{isotopeLabel[i]}</option>)}</select></Field>
    {openRun && history.filter(h=>h.isotopes[iso].flags.length).slice(0,1).map(h=>{const p=h.isotopes[iso].points.find(p=>h.isotopes[iso].flags.some(f=>f.id===p.id));return p&&<button className="metro-btn" key={h.key} onClick={()=>openRun(p.run_id)}>{tr("Inspect control signal")}</button>;})}
    </div><div className="station-qc-layout"><div>
    <Chart title={`${isotopeLabel[iso]} · ${[...new Set(history.map(h=>h.material.name))].join(", ") || "CQ"}`} x={tr("Acquisition date")} y="QC − assigned / ‰" date legendCollapsed data={qcHistoryTraces}/>
    </div><aside className="station-qc-statistics"><dl>
      <div><dt>{tr("Individual QC observations")}</dt><dd>{count}</dd></div>
      <div><dt>{tr("Outliers omitted from SD")}</dt><dd>{history.reduce((n,h)=>n+(h.isotopes[iso].outlier_ids?.length??0),0)}</dd></div><div><dt>{tr("Analytical acquisitions")}</dt><dd>{new Set(history.flatMap(h=>h.isotopes[iso].points.map(p=>p.run_id))).size}</dd></div>
      <div><dt>{tr("Method populations")}</dt><dd>{history.length}</dd></div>
      <div><dt>{tr("Active frozen u_prec")}</dt><dd>{numberText(state.active_method?.config.precision[iso],4)} ‰</dd></div>
      <div><dt>{tr("Current population SD without outliers")}</dt><dd>{numberText(active?.isotopes[iso].sd,4)} ‰</dd></div>
    </dl><p className="metro-muted">{tr(selectedOrigin==="synthetic"?"Mock QC history":"Observed QC from raw exports")} · {tr("Session outliers are excluded from the chart and SD. Retained control-limit violations remain visible.")}</p></aside></div>
    <details className="station-help"><summary>{tr("QC interpretation and evidence")}</summary><p className="metro-muted">{tr(active?.value_basis??"Corrected and normalized results")}. {tr("Observed and mock QC are kept in separate populations.")}</p><p className="metro-muted">{tr("Each method retains its own frozen ±3 SD limits. Qualification carousels appear as separate populations. Red points mark failed run QC or control signals; no failed data are silently removed.")}</p><p className="metro-muted">{tr("Historical SD is adopted only through a reviewed period and a new method version.")}</p>
    {openRun && <div className="metro-actions">{history.filter(h=>h.isotopes[iso].flags.length).map(h=>{const p=h.isotopes[iso].points.find(p=>h.isotopes[iso].flags.some(f=>f.id===p.id));return p&&<button className="metro-btn" key={h.key} onClick={()=>openRun(p.run_id)}>{tr("Inspect control signal")} · {h.population_label??`v${h.method_version}`}</button>;})}</div>}</details>
  </Panel>;
}
