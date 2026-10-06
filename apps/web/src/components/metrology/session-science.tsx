"use client";

import { Fragment, useState, type ReactNode } from "react";
import { Download, HelpCircle, Settings } from "lucide-react";
import { useTranslation } from "@/components/layout/language-provider";
import { Tooltip } from "@/components/ui/tooltip";
import { METROLOGY_API, isotopeLabel, isotopes, type ResidualOverride, type Fit, type Isotope, type ResultsSessionDetail, type SessionAnalysis, type SessionExport, type SessionRow } from "@/lib/metrology";
import { Chart } from "./results-station";
import { Empty, Field, Inspect, Panel, Status, type WorkspaceProps } from "./shared";
import type { ChartInteractions } from "./consultation-context";
import { MetrologyChartHeight } from "./consultation-context";
import { ResidualControls } from "./residual-controls";
import { uncertaintyEnvelope } from "@/lib/metrology-envelopes";

const carbon = "#215ec5", oxygen = "#167d87", beforeColor = "#94a3b8";
const color = (iso: Isotope) => iso === "d13c" ? carbon : oxygen;
const readable = (value: number | null | undefined, digits = 3) => value == null ? "—" : value.toFixed(digits);
const precise = (value: number | null | undefined) => value == null ? "—" : Number(value.toPrecision(3)).toString();
const tip = (label: string) => <Tooltip label={label}><button type="button" className="station-help" aria-label={label}><HelpCircle size={13}/></button></Tooltip>;

export function SessionUncertainty({ detail, group, analysis }: { detail: ResultsSessionDetail; group: string; analysis?: SessionAnalysis | null }) {
  const tr = useTranslation();
  const rows = detail.runs.flatMap(run => (run.evaluation?.results ?? []).filter(row => !group || row.role !== "unknown" || detail.groups[row.id] === group).map(row => ({ ...row, run_label: run.label })));
  const [id, setId] = useState(rows.find(row => row.role === "unknown")?.id ?? rows[0]?.id ?? "");
  const selected = rows.find(row => row.id === id) ?? rows[0];
  if (!selected) return <Empty>{tr("Evaluate imported data to view individual uncertainty budgets.")}</Empty>;
  const componentLabels: Record<string, [string, string]> = {
    u_prec: ["Intermediate precision", "Individual-observation SD from the method's reviewed QC population, not SD divided by the square root of n."],
    u_norm: ["Dual-anchor normalization", "Sample-specific propagation of the measured anchor means and assigned-value uncertainties, including the full covariance. Retained when Qtegra has already normalized the result."],
    u_corr: ["Residual correction", "Uncertainty from the validated residual correction. Its shared coefficient affects the sample and the two anchors; the full sensitivity is counted once."],
  };
  return <div className="metro-stack">
    {analysis && <><p className="metro-muted">{tr("Click a sample in either isotope chart to inspect its uncertainty budget below. The selector provides the same selection by keyboard.")}</p><SessionProcessing analysis={analysis} group={group} chartsOnly selectedId={selected.id} onSelect={setId}/></>}
    <Panel><div className="station-budget-toolbar"><Field label={tr("Individual result")}><select value={selected.id} onChange={e => setId(e.target.value)}>{rows.map(row => <option key={row.id} value={row.id}>{row.label} {row.comment} · {row.source_index} · {row.run_label}</option>)}</select></Field><span>{tr("Standard uncertainties in ‰; expanded uncertainty uses the recorded coverage factor.")}</span></div></Panel>
    <div className="metro-grid station-isotope-pair">{isotopes.map(iso => {
      const result = selected.isotopes?.[iso], budget = result?.budget;
      const components = budget?.components ?? [];
      const standard = ["u_prec", "u_norm", "u_corr"].map(key => ({ key, value: result?.[key as "u_prec" | "u_norm" | "u_corr"], label: componentLabels[key][0], help: componentLabels[key][1] }));
      const componentNames: Record<string,string> = {precision:"u_prec",normalization:"u_norm",secondary_correction:"u_corr"};
      const extras = components.filter(c => !["u_prec", "u_norm", "u_corr"].includes(componentNames[c.name]??c.name)).map(c => ({ key: c.name, value: c.u, label: c.name, help: c.rationale }));
      return <Panel key={iso} title={`${isotopeLabel[iso]} · ${tr("Uncertainty budget")}`}>
        <div className="station-result-value" style={{ borderColor: color(iso) }}><b>{readable(result?.value)} <span>± {readable(budget?.expanded_uncertainty)} ‰</span></b><small>VPDB · {tr("Expanded uncertainty")} · k = {readable(budget?.k, 2)}</small></div>
        <table className="station-budget-table"><thead><tr><th>{tr("Contribution")}</th><th>{tr("Symbol")}</th><th className="num">u / ‰</th></tr></thead><tbody>{[...standard, ...extras].map(item => <tr key={item.key}><td>{tr(item.label)} {tip(tr(item.help))}</td><td><code>{item.key}</code></td><td className="num">{readable(item.value, 4)}</td></tr>)}<tr className="station-budget-total"><td>{tr("Combined uncertainty")} {tip(tr("Root sum of the independent component variances. Any additional method components are included in the recorded budget."))}</td><td>u_c</td><td className="num">{readable(budget?.u_combined,4)}</td></tr><tr><td>{tr("Coverage factor")} {tip(tr(detail.method?.config.coverage_rationale || "The method records the selected coverage factor and its rationale. k=2 does not by itself establish an exact coverage probability."))}</td><td>k</td><td className="num">{readable(budget?.k,2)}</td></tr></tbody></table>
        <div className="station-formula" tabIndex={0} title={tr("Each standard uncertainty is squared, independent variances are added, then the square root is multiplied by k.")}>
          <span dangerouslySetInnerHTML={{__html: '<math xmlns="http://www.w3.org/1998/Math/MathML"><msub><mi>u</mi><mi>c</mi></msub><mo>=</mo><msqrt><msubsup><mi>u</mi><mi>prec</mi><mn>2</mn></msubsup><mo>+</mo><msubsup><mi>u</mi><mi>norm</mi><mn>2</mn></msubsup><mo>+</mo><msubsup><mi>u</mi><mi>corr</mi><mn>2</mn></msubsup><mo>+</mo><mo>∑</mo><msubsup><mi>u</mi><mi>j</mi><mn>2</mn></msubsup></msqrt></math>'}}/>{tip(tr("u_j represents additional independent method components. An unavailable budget remains unavailable, never zero."))}
          <span>U = k × u_c</span>{tip(tr("Expanded uncertainty equals the combined standard uncertainty multiplied by the recorded coverage factor."))}
        </div>
        {budget ? <Chart title={tr("Standard uncertainty contributions")} x="" y="u / ‰" height={220} data={[{ type:"bar", name:isotopeLabel[iso], x:components.map(c => tr(componentLabels[componentNames[c.name]??c.name]?.[0] ?? c.name)), y:components.map(c => c.u), marker:{color:color(iso)}, hovertemplate:"%{x}: %{y:.4f} ‰<extra></extra>" }]}/> : <Empty>{tr("No complete uncertainty budget. Review this analysis in Import.")}</Empty>}
        <details><summary>{tr("Normalization and correction formulas")}</summary><div className="station-formula"><span>u_norm² = Jᵀ Σ_anchors J</span>{tip(tr("J contains the derivatives with respect to A1, A2, M1 and M2. Σ contains their uncertainties and covariances."))}<span>z = x − c(p − p₀)</span>{tip(tr("x is the imported delta, c the independently validated correction slope, p the predictor and p₀ its reference value. Already applied corrections are retained."))}<span>y = A₁ + (z − M₁)(A₂ − A₁)/(M₂ − M₁)</span>{tip(tr("A1/A2 are assigned anchor values; M1/M2 are corrected measured anchor means. The equation propagates uncertainty even when Qtegra already applied normalization."))}</div></details>
        <Inspect title={tr("Recorded calculation and component evidence")} value={result}/>
      </Panel>;
    })}</div>
  </div>;
}

export function SessionProcessing({ analysis, group, mapping = {}, interact, chartsOnly = false, tableOnly = false, selectedId, onSelect }: { analysis: SessionAnalysis; group: string; mapping?: Record<string,string>; interact?: ChartInteractions; chartsOnly?: boolean; tableOnly?: boolean; selectedId?: string; onSelect?: (id: string) => void }) {
  const tr = useTranslation();
  const [showImported, setShowImported] = useState(!chartsOnly);
  const rows = analysis.rows.filter(row => !group || row.role !== "unknown" || row.sample_group === group);
  const organized = [...rows].sort((a,b) => (a.identifier1??"").localeCompare(b.identifier1??"", undefined, {numeric:true}) || (a.identifier2??"").localeCompare(b.identifier2??"", undefined, {numeric:true}) || (a.species??"").localeCompare(b.species??"") || a.sequence-b.sequence);
  const custom = (row: SessionRow, iso: Isotope) => onSelect ? [row.id] : mapping[row.id] ? [mapping[row.id], iso === "d13c" ? "d13C" : "d18O", row.label, row.comment] : null;
  return <div className="metro-stack">{!tableOnly&&<><p className="metro-muted">{rows.length} {tr("analyses across")} {analysis.workbooks} {tr("workbooks")}. {tr("Shading and bars show pointwise expanded uncertainty U. Gaps indicate missing budgets or workbook boundaries. Imported points remain visible; excluded points are marked ×.")}</p>
    <label className="metro-check"><input type="checkbox" checked={showImported} onChange={e=>setShowImported(e.target.checked)}/>{tr("Show imported observations")}</label>
    <div className="metro-grid station-isotope-pair">{isotopes.map(iso => <Chart key={iso} title={`${isotopeLabel[iso]} · ${tr("Session results with expanded uncertainty")}`} x={tr("Session acquisition sequence")} y={`${isotopeLabel[iso]} / ‰ VPDB`} height={340} interactions={onSelect ? {onPointClick: points => {const id = (points[0]?.customdata as string[] | undefined)?.[0]; if(id && rows.some(row => row.id === id)) onSelect(id);}} : interact?.(`session-results-${iso}`)} layout={{ margin:{l:55,r:62,t:75,b:45}, legend:{orientation:"h",y:1.02,yanchor:"bottom",x:0,font:{size:10},traceorder:"normal"}, meta:{equalStandardScale:true}, yaxis2:{title:{text:`QC ${isotopeLabel[iso]} / ‰`},overlaying:"y",side:"right",showgrid:false,zeroline:false} }} data={[
      ...Array.from(new Set(["unknown","qc","anchor", ...rows.map(row => row.role ?? row.source_role)])).flatMap(role => {
        const selected = rows.filter(row => (row.role ?? row.source_role) === role);
        return [
          ...uncertaintyEnvelope(selected.map(row => ({x:row.sequence, value:row.excluded ? null : row.isotopes?.[iso]?.value, uncertainty:row.isotopes?.[iso]?.budget?.expanded_uncertainty, segment:row.run_id})), `${tr(role)} · U`, role==="qc"?"rgba(186,129,59,0.18)":iso==="d13c"?"rgba(33,94,197,0.18)":"rgba(22,125,135,0.18)", role==="qc"?"y2":"y").map(trace=>({...trace,showlegend:false})),
          {type:"scatter",mode:"markers",name:`${tr(role)} · ${tr("Imported")}`,visible:showImported,x:selected.map(r=>r.sequence),y:selected.map(r=>r[iso]),customdata:selected.map(r=>custom(r,iso)),text:selected.map(r=>`${r.label} · ${r.comment} · ${r.run_label}`),marker:{color:beforeColor,size:selected.map(r=>r.id===selectedId?13:6),symbol:selected.map(r=>r.excluded?"x":"circle-open")},yaxis:role==="qc"?"y2":"y",hovertemplate:"%{text}<br>%{y:.3f} ‰<extra>%{fullData.name}</extra>"},
          {type:"scatter",mode:"markers",name:`${tr(role)} · ${tr("Final ± U")}`,x:selected.map(r=>r.sequence),y:selected.map(r=>r.isotopes?.[iso]?.value??null),customdata:selected.map(r=>custom(r,iso)),text:selected.map(r=>`${r.label} · ${r.comment} · ${r.run_label}`),marker:{color:role==="qc"?"#ba813b":color(iso),size:selected.map(r=>r.id===selectedId?13:7),symbol:selected.map(r=>r.excluded?"x":"circle")},yaxis:role==="qc"?"y2":"y",error_y:{type:"data",array:selected.map(r=>r.isotopes?.[iso]?.budget?.expanded_uncertainty??null),visible:true,thickness:1,width:2},hovertemplate:"%{text}<br>%{y:.3f} ‰<extra>%{fullData.name}</extra>"},
        ];
      }),
    ]}/>)}</div>
    </>}
    {!chartsOnly&&<div className="metro-table-wrap station-results-table"><table><thead><tr><th>{tr("Identifier 1")}</th><th>{tr("Identifier 2")}</th><th>{tr("Species")}</th><th>{tr("Workbook / group")}</th><th>δ¹³C ± U / ‰</th><th>δ¹⁸O ± U / ‰</th><th>{tr("Review")}</th></tr></thead><tbody>{organized.map(row=><tr key={row.id} className={row.issues?.length?"station-row-problem":""}><td>{row.identifier1||row.label}<small className="station-cell-subtitle">{row.source_index}</small></td><td>{row.identifier2||"—"}</td><td>{row.species||"—"}</td><td>{row.run_label}<small className="station-cell-subtitle">{row.sample_group}</small></td>{isotopes.map(iso=><td className="num" key={iso}>{readable(row.isotopes?.[iso]?.value)} ± {readable(row.isotopes?.[iso]?.budget?.expanded_uncertainty)}</td>)}<td><Status value={row.excluded?"excluded":row.issues?.length?"review_required":row.accepted_issues?.length?"accepted_exception":"within_criteria"}/></td></tr>)}</tbody></table></div>}
  </div>;
}

export function CorrectionValidation({ analysis }: { analysis: SessionAnalysis }) {
  const tr=useTranslation();
  return <Panel title={tr("Correction validation")}><div className="metro-grid station-isotope-pair">{isotopes.map(iso=>{const review=analysis.correction_review[iso];return <div key={iso}><div className="metro-actions"><b>{isotopeLabel[iso]}</b><span className="station-validation-verdict">{tr(review.status.replaceAll("_", " "))}</span></div><div className="station-sd-change"><span><small>{tr("QC SD before")}</small>{readable(review.before.sd,4)} ‰</span><b>→</b><span><small>{tr("QC SD after")}</small>{readable(review.after.sd,4)} ‰</span><span><small>{tr("SD reduction")}</small>{review.sd_reduction_fraction==null?"—":`${(100*review.sd_reduction_fraction).toFixed(1)}%`}</span></div><small>{review.paired_n}/{review.total_qc} {tr("paired QC observations")}. {tr("95% interval for reduction")}: {review.reduction_interval95?.map(v=>`${(100*v).toFixed(1)}%`).join(` ${tr("to")} `)??"—"}.</small>{!!review.reasons.length&&<details><summary>{tr("Validation criteria")} · {review.reasons.length}</summary><ul className="station-issue-list">{review.reasons.map(reason=><li key={reason}>{tr(reason)}</li>)}</ul></details>}</div>;})}</div><p className="metro-muted">{tr("Session QC only; the same aliquots and VPDB scale are compared. A smaller SD supports review but does not activate a correction.")}</p></Panel>;
}

export function SessionQcSequence({ analysis, mapping, interact }: { analysis: SessionAnalysis; mapping: Record<string,string>; interact: ChartInteractions }) {
  const tr=useTranslation();
  return <><div className="metro-grid station-isotope-pair">{isotopes.map(iso=>{const points=analysis.correction_review[iso].points;return <Chart key={iso} title={`${isotopeLabel[iso]} · ${tr("Session QC sequence")}`} x={tr("Session acquisition sequence")} y={`${isotopeLabel[iso]} / ‰ VPDB`} interactions={interact(`session-qc-${iso}`)} data={(["before","after"] as const).map(stage=>({type:"scatter",mode:"lines+markers",name:tr(stage==="before"?"Before correction":"After correction"),x:points.map(p=>p.sequence),y:points.map(p=>p[stage]),customdata:points.map(p=>{const row=analysis.rows.find(r=>r.id===p.id);return mapping[p.id]?[mapping[p.id],iso==="d13c"?"d13C":"d18O",row?.label,row?.comment]:null;}),marker:{size:7,color:stage==="before"?beforeColor:color(iso),symbol:points.map(p=>analysis.outliers.flags.some(f=>f.measurement_id===p.id&&f.isotope===iso)?"x":"circle")},line:{width:1,color:stage==="before"?beforeColor:color(iso)}}))}/>;})}</div></>;
}

const effects = [
  ["intensity_dependence","Residual intensity linearity","Mean I44 / V"],
  ["pressure_dependence","Pressure-adjustment linearity","Result − target intensity / V"],
  ["pressure_residual","Pressure mismatch after intensity detrending","Result − target intensity / V"],
  ["mass_dependence","Mass dependency","Carbonate mass / µg"],
  ["drift","QC drift","Session acquisition sequence"],
  ["memory","QC memory screening","Preceding sample − QC mean / ‰"],
] as const;

export function SessionResiduals({ analysis, materialId, mapping, interact, manualControls, overrides, busy, saveOverride }: {
  analysis:SessionAnalysis; materialId:string; mapping:Record<string,string>; interact:ChartInteractions; manualControls:ReactNode;
  overrides:Record<string,ResidualOverride>; busy:boolean;
  saveOverride:(materialId:string,effect:string,isotope:Isotope,settings:ResidualOverride|null)=>Promise<unknown>;
}) {
  const tr=useTranslation();
  const [active,setActive]=useState<string>(effects[0][0]);
  const [editing,setEditing]=useState<string|null>(null);
  const [hoverPreview,setHoverPreview]=useState<{left:number;top:number}|null>(null);
  const material=analysis.diagnostics_before.materials.find(m=>m.material_id===materialId)??analysis.diagnostics_before.materials[0];
  const final=analysis.diagnostics_after.materials.find(m=>m.material_id===material?.material_id);
  if(!material)return <Empty>{tr("No identified QC or anchor observations for residual diagnostics.")}</Empty>;
  const rowsById=new Map(analysis.rows.map(row=>[row.id,row]));
  const custom=(id:string|undefined,iso:Isotope)=>{const row=id?rowsById.get(id):undefined;return id&&mapping[id]?[mapping[id],iso==="d13c"?"d13C":"d18O",row?.identifier1??row?.label,row?.identifier2??row?.comment]:null;};
  const preview=(key:string,iso:Isotope)=>analysis.residual_previews?.[`${material.material_id}:${key}:${iso}`];
  const status=(fit:Fit|undefined)=>tr((fit?.screening_status??"insufficient_evidence").replaceAll("_"," "));
  const traces=(fit:Fit|undefined,iso:Isotope,stage:string)=>{
    if(!fit?.points?.length)return [];
    const xx=fit.points.map(p=>p.x),span=[Math.min(...xx),Math.max(...xx)];
    return [{type:"scatter",mode:"markers",name:tr(stage),x:xx,y:fit.points.map(p=>p.y),customdata:fit.points.map(p=>custom(p.id,iso)),marker:{size:7,color:stage==="Before correction"?beforeColor:color(iso),symbol:fit.points.map(p=>analysis.outliers.flags.some(f=>f.measurement_id===p.id&&f.isotope===iso)?"x":stage==="Before correction"?"circle-open":stage==="Manual preview"?"diamond":"circle")}},...(fit.slope==null||fit.intercept==null?[]:[{type:"scatter",mode:"lines",name:tr(stage),showlegend:false,x:span,y:span.map(x=>fit.intercept!+fit.slope!*x),line:{width:1.5,dash:stage==="Before correction"?"dot":stage==="Manual preview"?"dash":"solid",color:stage==="Before correction"?beforeColor:color(iso)}}])];
  };
  const [key,label,x]=effects.find(effect=>effect[0]===active)??effects[0];
  return <div className="metro-stack station-residual-workspace">
    {hoverPreview&&<div id="station-effect-tooltip" role="tooltip" className="station-effect-tooltip" style={hoverPreview}><h3>{tr(label)}</h3><MetrologyChartHeight.Provider value={230}><div className="metro-grid station-isotope-pair">{isotopes.map(iso=><Chart key={iso} title={isotopeLabel[iso]} x={tr(x)} y="‰" data={[...traces(material.isotopes[iso][key],iso,"Before correction"),...traces(final?.isotopes[iso][key],iso,"After correction"),...traces(preview(key,iso),iso,"Manual preview")]} layout={{margin:{l:45,r:10,t:60,b:45}}}/>)}</div></MetrologyChartHeight.Provider></div>}
    <p className="metro-muted">{material.label} · {tr("Hover or focus a residual effect to inspect its charts. Open its gear to adjust the calculation.")}</p>
    <div className="metro-table-wrap"><table className="station-residual-table"><thead><tr><th>{tr("Residual effect")}</th><th>δ¹³C · {tr("Before / after")}</th><th>δ¹⁸O · {tr("Before / after")}</th><th>{tr("Adjust")}</th></tr></thead><tbody>{effects.map(([effect,title])=><Fragment key={effect}><tr data-active={active===effect} onMouseEnter={()=>setActive(effect)} onFocus={()=>setActive(effect)}>
      <th scope="row"><button type="button" className="station-effect-link" onMouseEnter={event=>{if(window.innerWidth>=1100)setHoverPreview({left:Math.min(event.clientX+18,window.innerWidth-724),top:Math.max(70,Math.min(event.clientY+18,window.innerHeight-330))});}} onMouseLeave={()=>setHoverPreview(null)} aria-describedby={hoverPreview&&active===effect?"station-effect-tooltip":undefined} aria-pressed={active===effect} aria-controls="station-residual-charts" onClick={()=>setActive(effect)}>{tr(title)}</button></th>
      {isotopes.map(iso=>{const before=material.isotopes[iso][effect],after=final?.isotopes[iso][effect],draft=preview(effect,iso);return <td key={iso}><span>{status(before)} → {status(after)}</span><small className="station-cell-subtitle">{tr("Effect span")}: {readable(before?.effect_span)} → {readable(after?.effect_span)} ‰<br/>{tr("Practical threshold / ‰")}: {readable(before?.practical_threshold)}</small>{draft&&<small className="station-preview-result">{tr("Manual preview")}: {status(draft)} · {readable(draft.effect_span)} ‰</small>}</td>;})}
      <td><button type="button" className="metro-btn station-gear" aria-label={`${tr("Manual linearity control")}: ${tr(title)}`} aria-expanded={editing===effect} aria-controls={`settings-${effect}`} onClick={()=>{setActive(effect);setEditing(editing===effect?null:effect);}}><Settings size={16}/></button></td>
    </tr>{editing===effect&&<tr><td colSpan={4}><div id={`settings-${effect}`}><ResidualControls effect={effect} label={title} materialId={material.material_id} overrides={overrides} busy={busy} save={saveOverride}/>{effect==="intensity_dependence"&&<details className="station-original-linearity"><summary>{tr("Original IRMS linearity algorithms and offsets")}</summary>{manualControls}</details>}</div></td></tr>}</Fragment>)}</tbody></table></div>
    <section id="station-residual-charts" aria-label={tr(label)} className="station-residual-charts"><h3>{tr(label)}</h3><div className="metro-grid station-isotope-pair">{isotopes.map(iso=>{const before=material.isotopes[iso][key],after=final?.isotopes[iso][key],draft=preview(key,iso);return <div key={iso}><Chart title={isotopeLabel[iso]} x={tr(x)} y={key==="pressure_residual"?tr("Isotope residual / ‰"):`${isotopeLabel[iso]} / ‰ VPDB`} height={320} interactions={interact(`session-${key}-${iso}`)} layout={{margin:{l:55,r:16,t:72,b:48},legend:{orientation:"h",x:0,y:1.08,font:{size:10}}}} data={[...traces(before,iso,"Before correction"),...traces(after,iso,"After correction"),...traces(draft,iso,"Manual preview")]}/><p className="metro-muted">{tr("Slope")}: {precise(before?.slope)} → {precise(after?.slope)} · {tr("95% CI")}: {after?.slope_ci95?.map(precise).join(` ${tr("to")} `)??"—"}{draft&&<> · {tr("Preview SD / ‰")}: {readable(draft.after?.sd,4)}</>}</p></div>;})}</div></section>
    <p className="metro-muted">{tr("Before/after fits use paired observations on the same VPDB scale. Detection requires a slope interval excluding zero and an effect above the practical threshold; it does not authorize a correction.")}</p>
    <Chart title={tr("Signal intensity versus pressure adjustment difference")} x="Mean I44 / V" y={tr("Result − target intensity / V")} interactions={interact("session-intensity-pressure")} data={[{type:"scatter",mode:"markers",name:material.label,x:analysis.rows.filter(r=>r.material_id===material.material_id&&!r.excluded).map(r=>r.i44_v),y:analysis.rows.filter(r=>r.material_id===material.material_id&&!r.excluded).map(r=>r.pressure_mismatch_v),customdata:analysis.rows.filter(r=>r.material_id===material.material_id&&!r.excluded).map(r=>custom(r.id,"d13c")),marker:{color:oxygen,size:7}}]}/>
  </div>;
}

export function TraceableExport({ detail, group, ...props }: WorkspaceProps & { detail: ResultsSessionDetail; group: string }) {
  const tr=useTranslation();
  const hasResults=detail.runs.some(run=>run.evaluation?.results.some(row=>row.role==="unknown"&&!row.excluded&&(!group||detail.groups[row.id]===group)));
  const [client,setClient]=useState(detail.client),[series,setSeries]=useState(group||detail.project||detail.name);
  const [identifierSource,setIdentifierSource]=useState("raw_label"),[sampleSource,setSampleSource]=useState("raw_comment");
  return <Panel title={tr("Traceable results export")}><div className="metro-form-grid"><Field label={tr("Client name for export")}><input value={client} maxLength={180} onChange={e=>setClient(e.target.value)}/></Field><Field label={tr("Series identifier for filename")}><input value={series} maxLength={180} onChange={e=>setSeries(e.target.value)}/></Field></div><div className="metro-form-grid">{[["Client worksheet identifier",identifierSource,setIdentifierSource],["Client worksheet sample",sampleSource,setSampleSource]].map(([label,value,setValue])=><Field key={String(label)} label={tr(String(label))}><select value={String(value)} onChange={e=>(setValue as (value:string)=>void)(e.target.value)}><option value="raw_label">{tr("Original sample label")}</option><option value="raw_comment">{tr("Original comment / sample identifier")}</option></select></Field>)}</div><p className="metro-muted">{tr("IRMS naming convention: Results for [series] series - stable C & O isotopes - P2L - [client] - [DDMMYYYY]. Names are sanitized; source records remain unchanged.")}</p><p className="metro-muted">{tr("The package contains a formatted PDF calculation certificate, Excel results, CSV and the complete calculation JSON. Review and blocked results retain their decision labels.")}</p><div className="metro-actions">{[["zip","Results + calculation dossier"],["pdf","PDF calculation certificate"],["xlsx","Results Excel"],["csv","Results CSV"],["json","Calculation JSON"]].map(([format,label])=><button className={`metro-btn ${format==="zip"?"primary":""}`} key={format} disabled={props.busy||!hasResults} onClick={async()=>{const record=await props.act(`/results-sessions/${detail.id}/exports`,{format,group:group||null,client_name:client,series_name:series,identifier_source:identifierSource,sample_source:sampleSource}) as SessionExport|undefined;if(record){const a=document.createElement("a");a.href=`${METROLOGY_API}/session-exports/${record.id}`;a.download=record.filename;a.click();}}}><Download size={14}/>{tr(label)}</button>)}</div><div className="metro-table-wrap"><table><thead><tr><th>{tr("Generated")}</th><th>{tr("Group / results")}</th><th>{tr("Download")}</th></tr></thead><tbody>{detail.exports.map(item=><tr key={item.id}><td>{item.created_at.slice(0,16).replace("T"," ")}</td><td>{item.group??tr("All groups")} · {item.rows}</td><td><a className="station-session-link" href={`${METROLOGY_API}/session-exports/${item.id}`}>{item.filename}</a></td></tr>)}</tbody></table></div></Panel>;
}
