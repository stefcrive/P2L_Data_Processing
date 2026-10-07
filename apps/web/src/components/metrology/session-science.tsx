"use client";

import { memo, Fragment, useState, useRef, useEffect, type ReactNode } from "react";
import { Download, Settings } from "lucide-react";
import { useTranslation } from "@/components/layout/language-provider";
import { METROLOGY_API, isotopeLabel, isotopes, type ResidualOverride, type QcFlagCategory, type Fit, type Isotope, type ResultsSessionDetail, type SessionAnalysis, type SessionExport, type SessionRow } from "@/lib/metrology";
import { Chart } from "./results-station";
import { Empty, Field, Panel, Status, Inspect, type WorkspaceProps } from "./shared";
import type { ChartInteractions } from "./consultation-context";
import { MetrologyChartHeight } from "./consultation-context";
import { ResidualControls } from "./residual-controls";
import { uncertaintyEnvelope } from "@/lib/metrology-envelopes";
import { residualEffectSummary, qcSdEvidence, correctionUncertaintyRange } from "@/lib/residual-summary";

const carbon = "#215ec5", oxygen = "#167d87", beforeColor = "#94a3b8";
const color = (iso: Isotope) => iso === "d13c" ? carbon : oxygen;
const readable = (value: number | null | undefined, digits = 3) => value == null ? "—" : value.toFixed(digits);

export { UncertaintyWorkspace as SessionUncertainty } from "./uncertainty-workspace";

export const SessionProcessing = memo(function SessionProcessing({ analysis, group, mapping = {}, interact, chartsOnly = false, tableOnly = false, selectedId, onSelect }: { analysis: SessionAnalysis; group: string; mapping?: Record<string,string>; interact?: ChartInteractions; chartsOnly?: boolean; tableOnly?: boolean; selectedId?: string; onSelect?: (id: string) => void }) {
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
});

export function CorrectionValidation({ analysis }: { analysis: SessionAnalysis }) {
  const tr=useTranslation();
  return <Panel title={tr("Correction validation")}>
    <div className="metro-table-wrap"><table className="station-validation-table"><thead><tr><th>{tr("Isotope")}</th><th>{tr("Paired QC SD / ‰")}</th><th>{tr("SD reduction")}</th><th>{tr("Correction validation")}</th></tr></thead><tbody>{isotopes.map(iso=>{
      const review=analysis.correction_review[iso];
      return <tr key={iso}><th scope="row">{isotopeLabel[iso]}</th>
        <td><strong>{readable(review.before.sd,4)} → {readable(review.after.sd,4)}</strong><small>{review.paired_n}/{review.total_qc} {tr("paired QC observations")}{review.excluded_outlier_n?` · ${review.excluded_outlier_n} ${tr("outliers excluded")}`:""}</small></td>
        <td><strong>{review.sd_reduction_fraction==null?"—":`${(100*review.sd_reduction_fraction).toFixed(1)}%`} · {tr(qcSdEvidence(review))}</strong><small>{tr("95% interval for reduction")}: {review.reduction_interval95?.map(v=>`${(100*v).toFixed(1)}%`).join(" … ")??"—"}</small></td>
        <td><Status value={review.status}/><small>{tr("Required")}: ≥ {review.criteria.minimum_qc} QC · ≥ {(100*review.criteria.minimum_sd_reduction_fraction).toFixed(0)}% {tr("SD reduction")}</small></td>
      </tr>;
    })}</tbody></table></div>
    {isotopes.map(iso=>{const review=analysis.correction_review[iso];return !!review.reasons.length&&<details key={iso} className="station-validation-findings"><summary>{isotopeLabel[iso]} · {tr("Validation criteria")} · {review.reasons.length}</summary><ul className="station-issue-list">{review.reasons.map(reason=><li key={reason}>{tr(reason)}</li>)}</ul></details>;})}
    <p className="metro-muted">{tr("Session QC only; detected outliers are excluded, and the remaining paired aliquots are compared on the same VPDB scale. A smaller SD supports review but does not activate a correction.")}</p>
  </Panel>;
}

export function SessionQcSequence({ analysis, mapping, interact, target }: { analysis: SessionAnalysis; mapping: Record<string,string>; interact: ChartInteractions; target: Record<Isotope,number|null> }) {
  const tr=useTranslation();
  const rows=analysis.rows.filter(row=>row.role==="qc").sort((a,b)=>a.sequence-b.sequence);
  return <div className="metro-grid station-isotope-pair">{isotopes.map(iso=>{
    const paired=new Map(analysis.correction_review[iso].points.map(p=>[p.id,p.before]));
    const flagged=new Set(analysis.outliers.flags.filter(f=>f.isotope===iso).map(f=>f.measurement_id));
    const custom=(row:SessionRow)=>[mapping[row.id]??row.id,iso==="d13c"?"d13C":"d18O",row.label,row.comment];
    // Keep gaps at workbook boundaries; before values exist only for valid paired comparisons.
    const sequence=rows.flatMap((row,index)=>index&&row.run_id!==rows[index-1].run_id?[null,row]:[row]);
    const outliers=rows.filter(row=>flagged.has(row.id));
    const stats=analysis.qc_statistics[iso].final;
    const xValues=rows.map(row=>row.sequence);
    const span=xValues.length?[Math.min(...xValues),Math.max(...xValues)]:[];
    const reference=(name:string,value:number|null|undefined,lineColor:string,dash:string,showlegend=true,legendgroup?:string)=>value==null||span.length<2?null:{type:"scatter",mode:"lines",name:tr(name),legendgroup,showlegend,x:span,y:[value,value],hovertemplate:`${tr(name)}: %{y:.4f} ‰<extra></extra>`,line:{width:1.5,color:lineColor,dash}};
    const references=[
      reference("Average",stats.mean,"#334155","solid"),
      reference("True value",target[iso],"#7c3aed","dash"),
      reference("Average ±1σ",stats.mean!=null&&stats.sd!=null?stats.mean+stats.sd:null,"#c38835","dot",true,"one-sigma"),
      reference("Average ±1σ",stats.mean!=null&&stats.sd!=null?stats.mean-stats.sd:null,"#c38835","dot",false,"one-sigma"),
    ].filter(Boolean);
    return <Chart key={iso} title={`${isotopeLabel[iso]} · ${tr("Session QC sequence")}`} x={tr("Session acquisition sequence")} y={`${isotopeLabel[iso]} / ‰ VPDB`} interactions={interact(`session-qc-${iso}`)} data={[
      ...(["before","after"] as const).map(stage=>({type:"scatter",mode:"lines+markers",connectgaps:false,name:tr(stage==="before"?"Before correction":"After recorded method"),meta:{correctionStage:stage},x:sequence.map(r=>r?.sequence??null),y:sequence.map(r=>r?(stage==="before"?paired.get(r.id)??null:r.isotopes?.[iso]?.value??null):null),customdata:sequence.map(r=>r?custom(r):null),text:sequence.map(r=>r?`${r.label} · ${r.source_index} · ${r.run_label}`:""),marker:{size:6,color:stage==="before"?beforeColor:color(iso),symbol:stage==="before"?"circle-open":"circle"},line:{width:1,color:stage==="before"?beforeColor:color(iso)},hovertemplate:"%{text}<br>%{y:.4f} ‰<extra>%{fullData.name}</extra>"})),
      {type:"scatter",mode:"markers",name:`${tr("Detected outliers")} · ${isotopeLabel[iso]}`,x:outliers.map(r=>r.sequence),y:outliers.map(r=>r.isotopes?.[iso]?.value??null),customdata:outliers.map(custom),text:outliers.map(r=>`${r.label} · ${r.source_index} · ${r.run_label}`),marker:{size:10,symbol:"x",color:color(iso),line:{width:1}},hovertemplate:"%{text}<br>%{y:.4f} ‰<extra>%{fullData.name}</extra>"},
      ...references,
    ]}/>;
  })}</div>;
}

const qcCategories: [QcFlagCategory,string][] = [["statistical","Statistical outliers"],["range","Validity-range flags"],["manual","Manual exclusions"],["failed","Failed analyses"]];
export function SessionOutlierTable({analysis,review}:{analysis:SessionAnalysis;review:(measurementId:string,runId:string)=>ReactNode}) {
  const tr=useTranslation();
  const [selected,setSelected]=useState<{measurementId:string;runId:string}|null>(null);
  const rows=new Map(analysis.rows.map(row=>[row.id,row]));
  const flags=[...analysis.outliers.flags,...analysis.qc_review_flags??[]];
  return <Panel title={tr("QC outliers and review flags")}>
    <p className="metro-muted">{analysis.outliers.method==="iqr"?"IQR":"Sigma"} × {analysis.outliers.threshold}. {tr("Saved statistical flags are excluded from long-term QC statistics. Other categories retain the recorded evaluation decisions.")}</p>
    {qcCategories.map(([category,label])=>{
      const selectedFlags=flags.filter(flag=>(flag.category??"statistical")===category);
      return <details key={category} open={category==="statistical"} className="station-outlier-category"><summary>{tr(label)} · {selectedFlags.length}</summary>
        {selectedFlags.length?<div className="metro-table-wrap"><table><thead><tr><th>{tr("Analysis")}</th><th>{tr("Identifier 1")}</th><th>{tr("Identifier 2")}</th><th>{tr("Species")}</th><th>{tr("Analytical workbook")}</th><th>{tr("Isotope")}</th><th>{tr("Final ± U")} / ‰</th>{category==="statistical"&&<><th>{tr("Detection interval")} / ‰</th><th>n</th></>}<th>{tr("Review")}</th></tr></thead><tbody>{selectedFlags.map(flag=>{
          const row=rows.get(flag.measurement_id);
          return <tr key={`${flag.measurement_id}:${flag.isotope}`} className="station-row-problem"><td title={row?.source_index}>{row?.source_index?.split("@")[0]??row?.workbook_sequence}</td><td>{row?.identifier1||row?.label}</td><td>{row?.identifier2||"—"}</td><td>{row?.species||"—"}</td><td>{row?.run_label}</td><td>{isotopeLabel[flag.isotope]}</td><td className="num">{readable(flag.value,4)} ± {readable(row?.isotopes?.[flag.isotope]?.budget?.expanded_uncertainty,4)}</td>{category==="statistical"&&<><td className="num">{readable(flag.lower,4)} … {readable(flag.upper,4)}</td><td>{flag.population_n}</td></>}<td><button className="metro-btn" onClick={()=>setSelected({measurementId:flag.measurement_id,runId:flag.run_id})}>{tr("Inspect analysis")}</button></td></tr>;
        })}</tbody></table></div>:<Empty>{tr("No rows in this outlier category.")}</Empty>}
      </details>;
    })}
    {selected&&<div className="metro-stack"><button className="metro-btn" onClick={()=>setSelected(null)}>{tr("Close")}</button>{review(selected.measurementId,selected.runId)}</div>}
  </Panel>;
}

const effects = [
  ["intensity_dependence","Sample intensity vs delta","Initial sample I44 / V"],
  ["sample_reference_dependence","Sample–reference difference vs delta","Sample − reference I44 / V"],
  ["pressure_adjusted_dependence","Pressure-adjusted signal difference vs delta","Pressure-adjusted signal difference / V"],
  ["pressure_dependence","Pressure-adjustment linearity","Result − target intensity / V"],
  ["pressure_residual","Pressure mismatch after intensity detrending","Pressure mismatch residual / V"],
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
  const [showResidual,setShowResidual]=useState(true);
  const [editing,setEditing]=useState<string|null>(null);
  const [hoverPreview,setHoverPreview]=useState<{left:number;top:number}|null>(null);
  const hoverTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const clearHoverTimer=()=>{if(hoverTimer.current)clearTimeout(hoverTimer.current);};
  const closeHover=()=>{clearHoverTimer();hoverTimer.current=setTimeout(()=>setHoverPreview(null),180);};
  useEffect(()=>{
    const dismiss=(event:KeyboardEvent)=>{if(event.key==="Escape"){if(hoverTimer.current)clearTimeout(hoverTimer.current);setHoverPreview(null);}};
    window.addEventListener("keydown",dismiss);
    return ()=>{window.removeEventListener("keydown",dismiss);if(hoverTimer.current)clearTimeout(hoverTimer.current);};
  },[]);

  const material=analysis.diagnostics_before.materials.find(m=>m.material_id===materialId);
  const final=analysis.diagnostics_after.materials.find(m=>m.material_id===material?.material_id);
  if(!material)return <Empty>{tr("No identified QC or anchor observations for residual diagnostics.")}</Empty>;
  const rowsById=new Map(analysis.rows.map(row=>[row.id,row]));
  const custom=(id:string|undefined,iso:Isotope)=>{const row=id?rowsById.get(id):undefined;return id&&mapping[id]?[mapping[id],iso==="d13c"?"d13C":"d18O",row?.identifier1??row?.label,row?.identifier2??row?.comment]:null;};
  const preview=(key:string,iso:Isotope)=>analysis.residual_previews?.[`${material.material_id}:${key}:${iso}`];
  const displayedLinearity=(effect:typeof effects[number][0],iso:Isotope)=>{
    const corrected=analysis.residual_linearity_views?.[`${material.material_id}:${effect}:${iso}`];
    return showResidual&&corrected ? {fit:corrected,stage:"Manual preview"} : {fit:final?.isotopes[iso][effect],stage:"After correction"};
  };
  const effectCell=(fit:Fit|undefined,stage:string)=>{
    const summary=residualEffectSummary(fit);
    const evidence=`${tr("Linear fit")}: y = ${readable(fit?.intercept,5)} + ${readable(fit?.slope,6)} x; n=${fit?.n??0}. ${tr("95% confidence interval for effect span")}: ${summary.interval?.map(v=>readable(v)).join(" … ")??"—"}`;
    return <td title={evidence} className={`station-effect-value ${stage} ${summary.relevant==null?"":summary.relevant?"effect-relevant":"effect-small"}`}>
      <strong>Δ = {readable(summary.effect)} <span>± {readable(summary.uncertainty)} ‰</span></strong>
      <small>b = {readable(fit?.slope,5)} ± {readable(fit?.slope_se,5)} · R² {readable(fit?.r_squared,2)}</small>
    </td>;
  };
  const traces=(fit:Fit|undefined,iso:Isotope,stage:string)=>{
    if(!fit?.points?.length)return [];
    const xx=fit.points.map(p=>p.x),retained=fit.points.filter(p=>!p.excluded_from_fit).map(p=>p.x),span=[Math.min(...retained),Math.max(...retained)];
    return [{type:"scatter",mode:"markers",name:tr(stage==="After correction"?"After recorded method":stage==="Manual preview"?"Residual correction preview":stage),meta:{correctionStage:stage==="Before correction"?"before":stage==="After correction"?"after":"preview"},x:xx,y:fit.points.map(p=>p.y),customdata:fit.points.map(p=>custom(p.id,iso)),marker:{size:7,color:stage==="Before correction"?beforeColor:color(iso),symbol:fit.points.map(p=>analysis.outliers.flags.some(f=>f.measurement_id===p.id&&f.isotope===iso)?"x":stage==="Before correction"?"circle-open":stage==="Manual preview"?"diamond":"circle")}},...(fit.slope==null||fit.intercept==null?[]:[{type:"scatter",mode:"lines",name:tr(stage==="After correction"?"After recorded method":stage==="Manual preview"?"Residual correction preview":stage),meta:{correctionStage:stage==="Before correction"?"before":stage==="After correction"?"after":"preview"},showlegend:false,x:span,y:span.map(x=>fit.intercept!+fit.slope!*x),line:{width:1.5,dash:stage==="Before correction"?"dot":stage==="Manual preview"?"dash":"solid",color:stage==="Before correction"?beforeColor:color(iso)}}])];
  };
  const [key,label,x]=effects.find(effect=>effect[0]===active)??effects[0];
  return <div className="metro-stack station-residual-workspace">
    {hoverPreview&&<div id="station-effect-tooltip" role="dialog" aria-label={tr(label)} className="station-effect-tooltip" style={hoverPreview} onMouseEnter={clearHoverTimer} onMouseLeave={closeHover} onKeyDown={e=>{if(e.key==="Escape")setHoverPreview(null);}}><div className="station-panel-toolbar"><h3>{tr(label)}</h3><button type="button" className="metro-btn" onClick={()=>{clearHoverTimer();setHoverPreview(null);}} aria-label={tr("Close")}>×</button></div><MetrologyChartHeight.Provider value={360}><div className="metro-grid station-isotope-pair">{isotopes.map(iso=><Chart key={iso} title={isotopeLabel[iso]} x={tr(x)} y="‰" data={[...traces(material.isotopes[iso][key],iso,"Before correction"),...traces(final?.isotopes[iso][key],iso,"After correction"),...traces(preview(key,iso),iso,"Manual preview")]} />)}</div></MetrologyChartHeight.Provider></div>}
    <p className="metro-muted station-residual-legend">{material.label} · {tr("Δ: effect across the observed range; b: fitted slope per predictor unit; ±: standard uncertainty.")} {tr("Practical threshold / ‰")}: δ¹³C {readable(material.isotopes.d13c.intensity_dependence.practical_threshold)} / δ¹⁸O {readable(material.isotopes.d18o.intensity_dependence.practical_threshold)}. {tr("Amber: effect above the practical threshold. Green: below. Gray: no estimate.")}</p>
    <div className="metro-table-wrap"><table className="station-residual-table"><thead><tr><th rowSpan={2}>{tr("Residual effect")}</th><th colSpan={2}>δ¹³C · {tr(materialId==="__all__"?"Effect / ‰":"QC effect / ‰")}</th><th colSpan={2}>δ¹⁸O · {tr(materialId==="__all__"?"Effect / ‰":"QC effect / ‰")}</th><th rowSpan={2}>{tr("Adjust")}</th></tr><tr>{isotopes.map(iso=><Fragment key={iso}><th className="before">{tr("Before")}</th><th className="after">{tr("After recorded method")}</th></Fragment>)}</tr></thead><tbody>{effects.map(([effect,title])=><Fragment key={effect}><tr data-active={active===effect}>
      <th scope="row"><button type="button" className="station-effect-link" title={`${tr(title)}; x: ${tr(effects.find(item=>item[0]===effect)![2])}`} onMouseEnter={()=>{clearHoverTimer();setActive(effect);if(window.innerWidth>=1100)hoverTimer.current=setTimeout(()=>setHoverPreview({left:Math.max(16,(window.innerWidth-1060)/2),top:Math.max(60,(window.innerHeight-440)/2)}),600);}} onMouseLeave={closeHover} onFocus={()=>setActive(effect)} onKeyDown={e=>{if(e.key==="Escape")setHoverPreview(null);}} aria-pressed={active===effect} aria-controls={`residual-${effect}`} onClick={()=>{clearHoverTimer();setHoverPreview(null);document.getElementById(`residual-${effect}`)?.scrollIntoView({behavior:"smooth",block:"start"});}}>{tr(title)}</button></th>
      {isotopes.map(iso=><Fragment key={iso}>{effectCell(material.isotopes[iso][effect],"before")}{effectCell(final?.isotopes[iso][effect],"after")}</Fragment>)}
      <td><button type="button" className="metro-btn station-gear" aria-label={`${tr("Manual linearity control")}: ${tr(title)}`} aria-expanded={editing===effect} aria-controls={`settings-${effect}`} onClick={()=>{clearHoverTimer();setHoverPreview(null);setActive(effect);setEditing(editing===effect?null:effect);}}><Settings size={16}/></button></td>
    </tr>{editing===effect&&<tr><td colSpan={6}><div id={`settings-${effect}`}><ResidualControls effect={effect} label={title} materialId={material.material_id} overrides={overrides} busy={busy} save={saveOverride}/>{isotopes.map(iso=>{const draft=preview(effect,iso);return draft&&<p key={iso} className="station-preview-result">{isotopeLabel[iso]} · {tr("Manual preview")}: {readable(draft.effect_span)} ‰ · {tr("Preview SD / ‰")}: {readable(draft.after?.sd,4)}</p>;})}{effect==="intensity_dependence"&&<details className="station-original-linearity"><summary>{tr("Original IRMS linearity algorithms and offsets")}</summary>{manualControls}</details>}</div></td></tr>}</Fragment>)}</tbody></table></div>
    <section className="station-preview-summary" aria-label={tr("Residual correction preview")}>
      <h3>{tr("Residual correction preview")}</h3>
      {isotopes.some(iso=>preview("intensity_dependence",iso)?.model)&&<div className="metro-table-wrap"><table className="station-validation-table"><thead><tr><th>{tr("Isotope")}</th><th>{tr("Correction coefficient")}</th><th>{tr(materialId==="__all__"?"Fitting data SD / ‰":"Fitting QC SD / ‰")}</th><th>{tr("Correction standard uncertainty / ‰")}</th></tr></thead><tbody>{isotopes.map(iso=>{
        const draft=preview("intensity_dependence",iso),range=correctionUncertaintyRange(draft);
        if(!draft?.model)return null;
        const reduction=draft.before?.sd&&draft.after?.sd!=null?100*(1-draft.after.sd/draft.before.sd):null;
        return <tr key={iso}><th scope="row">{isotopeLabel[iso]}</th><td><strong>b = {readable(draft.model.slope,5)} ± {readable(draft.model.u_slope,5)} ‰/V</strong><small>x₀ = {readable(draft.model.x_ref,3)} V{draft.model.degree===2?` · q = ${readable(draft.model.quad,5)} ± ${readable(draft.model.u_quad,5)} ‰/V²`:""}</small></td><td><strong>{readable(draft.before?.sd,4)} → {readable(draft.after?.sd,4)} · {reduction==null?"—":`${reduction.toFixed(1)}%`}</strong><small>{tr(materialId==="__all__"?"Fitted data; independent validation pending":"Fitted QC; independent validation pending")} · n={draft.n}</small></td><td><strong>u_corr = {range?.map(v=>readable(v,4)).join(" … ")??"—"}</strong><small>{tr("Coefficient component; total uncertainty not evaluated")}</small></td></tr>;
      })}</tbody></table></div>}
      <div className="metro-actions">{isotopes.map(iso=><button key={iso} type="button" className="metro-btn" disabled={busy} onClick={()=>void saveOverride(material.material_id,"intensity_dependence",iso,{enabled:true,algorithm:"linear",slope:null,quadratic:null,center:null,offset:0,practical_threshold:iso==="d13c"?.01:.02})}>{isotopeLabel[iso]} · {tr(materialId==="__all__"?"Fit a linear correction preview to selected data":"Fit a linear correction preview to session QC")}</button>)}</div>
      {isotopes.map(iso=>{const draft=preview("intensity_dependence",iso);return draft?.model&&<Inspect key={iso} title={`${isotopeLabel[iso]} · ${tr("Correction model and uncertainty")}`} value={draft}/>;})}
    </section>
    <div id="station-residual-charts" className="metro-stack"><Field label={tr("Linearity chart values")}><select value={showResidual?"preview":"recorded"} onChange={e=>setShowResidual(e.target.value==="preview")}><option value="preview">{tr("After residual correction, when enabled")}</option><option value="recorded">{tr("After recorded method")}</option></select></Field><p className="metro-muted">{tr("The three signal plots use the same intensity-corrected observations. Other predictor trends can remain. Preview values do not replace exported results.")}</p><div className="station-linearity-triptych">{isotopes.flatMap(iso=>effects.slice(0,3).map(([effect,title,axisLabel])=><section key={`${iso}-${effect}`} id={iso==="d13c"?`residual-${effect}`:`residual-${effect}-${iso}`} className="station-residual-charts"><Chart title={`${isotopeLabel[iso]}: ${tr(title)}`} x={tr(axisLabel)} y={`${isotopeLabel[iso]} / ‰ VPDB`} height={330} interactions={interact(`session-${effect}-${iso}`)} data={[...traces(material.isotopes[iso][effect],iso,"Before correction"),...traces(displayedLinearity(effect,iso).fit,iso,displayedLinearity(effect,iso).stage)]}/></section>))}</div>{effects.slice(3).map(([effect,title,axisLabel])=><section key={effect} id={`residual-${effect}`} aria-label={tr(title)} className="station-residual-charts"><h3>{tr(title)}</h3><div className="metro-grid station-isotope-pair">{isotopes.map(iso=>{const before=material.isotopes[iso][effect],after=final?.isotopes[iso][effect],draft=preview(effect,iso);return <Chart key={iso} title={isotopeLabel[iso]} x={tr(axisLabel)} y={effect==="pressure_residual"?tr("Isotope residual / ‰"):`${isotopeLabel[iso]} / ‰ VPDB`} height={340} interactions={interact(`session-${effect}-${iso}`)} data={[...traces(before,iso,"Before correction"),...traces(after,iso,"After correction"),...traces(draft,iso,"Manual preview")]} />;})}</div></section>)}</div>
    <p className="metro-muted">{tr(materialId==="__all__"?"All-data fits use the available observations in each stage. Different sample compositions can influence the pooled slope; this view does not change the session calibration.":"Before/after fits use paired observations on the same VPDB scale. Detection requires a slope interval excluding zero and an effect above the practical threshold; it does not authorize a correction.")}</p>

  </div>;
}

export function TraceableExport({ detail, group, ...props }: WorkspaceProps & { detail: ResultsSessionDetail; group: string }) {
  const tr=useTranslation();
  const hasResults=detail.runs.some(run=>run.evaluation?.results.some(row=>row.role==="unknown"&&!row.excluded&&(!group||detail.groups[row.id]===group)));
  const [includeOutliers,setIncludeOutliers]=useState(false);
  const [client,setClient]=useState(detail.client),[series,setSeries]=useState(group||detail.project||detail.name);
  const [identifierSource,setIdentifierSource]=useState("raw_label"),[sampleSource,setSampleSource]=useState("raw_comment");
  return <Panel title={tr("Traceable results export")}><div className="metro-form-grid"><Field label={tr("Client name for export")}><input value={client} maxLength={180} onChange={e=>setClient(e.target.value)}/></Field><Field label={tr("Series identifier for filename")}><input value={series} maxLength={180} onChange={e=>setSeries(e.target.value)}/></Field></div><div className="metro-form-grid">{[["Client worksheet identifier",identifierSource,setIdentifierSource],["Client worksheet sample",sampleSource,setSampleSource]].map(([label,value,setValue])=><Field key={String(label)} label={tr(String(label))}><select value={String(value)} onChange={e=>(setValue as (value:string)=>void)(e.target.value)}><option value="raw_label">{tr("Original sample label")}</option><option value="raw_comment">{tr("Original comment / sample identifier")}</option></select></Field>)}</div><p className="metro-muted">{tr("IRMS naming convention: Results for [series] series - stable C & O isotopes - P2L - [client] - [DDMMYYYY]. Names are sanitized; source records remain unchanged.")}</p><p className="metro-muted">{tr("The package contains a formatted PDF calculation certificate, Excel results, CSV and the complete calculation JSON. Review and blocked results retain their decision labels.")}</p><label className="metro-check"><input type="checkbox" checked={includeOutliers} onChange={e=>setIncludeOutliers(e.target.checked)}/>{tr("Include excluded results")}</label><div className="metro-actions">{[["zip","Results + calculation dossier"],["pdf","PDF calculation certificate"],["xlsx","Client output Excel","client_output"],["xlsx","Whole results Excel","dataset"],["csv","Results CSV"],["json","Calculation JSON"]].map(([format,label,output_type])=><button className={`metro-btn ${format==="zip"?"primary":""}`} key={output_type??format} disabled={props.busy||(output_type==="dataset"?!detail.runs.some(r=>r.evaluation?.results.length):!hasResults)} onClick={async()=>{const record=await props.act(`/results-sessions/${detail.id}/exports`,{format,output_type:output_type??"combined",include_outliers:includeOutliers,group:group||null,client_name:client,series_name:series,identifier_source:identifierSource,sample_source:sampleSource}) as SessionExport|undefined;if(record){const a=document.createElement("a");a.href=`${METROLOGY_API}/session-exports/${record.id}`;a.download=record.filename;a.click();}}}><Download size={14}/>{tr(label)}</button>)}</div><div className="metro-table-wrap"><table><thead><tr><th>{tr("Generated")}</th><th>{tr("Group / results")}</th><th>{tr("Download")}</th></tr></thead><tbody>{detail.exports.map(item=><tr key={item.id}><td>{item.created_at.slice(0,16).replace("T"," ")}</td><td>{item.group??tr("All groups")} · {item.rows}</td><td><a className="station-session-link" href={`${METROLOGY_API}/session-exports/${item.id}`}>{item.filename}</a></td></tr>)}</tbody></table></div></Panel>;
}
