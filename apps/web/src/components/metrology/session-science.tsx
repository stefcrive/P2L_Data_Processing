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
          {type:"scatter",mode:"markers",name:`${tr(role)} · ${tr("Final")}`,x:selected.map(r=>r.sequence),y:selected.map(r=>r.isotopes?.[iso]?.value??null),customdata:selected.map(r=>custom(r,iso)),text:selected.map(r=>`${r.label} · ${r.comment} · ${r.run_label}`),marker:{color:role==="qc"?"#ba813b":color(iso),size:selected.map(r=>r.id===selectedId?13:7),symbol:selected.map(r=>r.excluded?"x":"circle")},yaxis:role==="qc"?"y2":"y",error_y:{type:"data",array:selected.map(r=>r.isotopes?.[iso]?.budget?.expanded_uncertainty??null),visible:true,thickness:1,width:2,color:role==="qc"?"#ba813b":color(iso)},hovertemplate:"%{text}<br>%{y:.3f} ‰<extra>%{fullData.name}</extra>"},
        ];
      }),
    ]}/>)}</div>
    </>}
    {!chartsOnly&&<div className="metro-table-wrap station-results-table"><table><thead><tr><th>{tr("Identifier 1")}</th><th>{tr("Identifier 2")}</th><th>{tr("Species")}</th><th>{tr("Workbook / group")}</th><th>δ¹³C ± U / ‰</th><th>δ¹⁸O ± U / ‰</th><th>{tr("Review")}</th></tr></thead><tbody>{organized.map(row=><tr key={row.id} className={row.issues?.length?"station-row-problem":""}><td>{row.identifier1||row.label}<small className="station-cell-subtitle">{row.source_index}</small></td><td>{row.identifier2||"—"}</td><td>{row.species||"—"}</td><td>{row.run_label}<small className="station-cell-subtitle">{row.sample_group}</small></td>{isotopes.map(iso=><td className="num" key={iso}>{readable(row.isotopes?.[iso]?.value)} ± {readable(row.isotopes?.[iso]?.budget?.expanded_uncertainty)}</td>)}<td><Status value={row.excluded?"excluded":row.issues?.length?"review_required":row.accepted_issues?.length?"accepted_exception":"within_criteria"}/></td></tr>)}</tbody></table></div>}
  </div>;
});

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
      ...(["before","after"] as const).map(stage=>({type:"scatter",mode:"lines+markers",connectgaps:false,name:tr(stage==="before"?"Before residual correction":"Corrected results"),meta:{correctionStage:stage},x:sequence.map(r=>r?.sequence??null),y:sequence.map(r=>r?(stage==="before"?paired.get(r.id)??null:r.isotopes?.[iso]?.value??null):null),customdata:sequence.map(r=>r?custom(r):null),text:sequence.map(r=>r?`${r.label} · ${r.source_index} · ${r.run_label}`:""),marker:{size:6,color:stage==="before"?beforeColor:color(iso),symbol:stage==="before"?"circle-open":"circle"},line:{width:1,color:stage==="before"?beforeColor:color(iso)},hovertemplate:"%{text}<br>%{y:.4f} ‰<extra>%{fullData.name}</extra>"})),
      {type:"scatter",mode:"markers",name:`${tr("Detected outliers")} · ${isotopeLabel[iso]}`,x:outliers.map(r=>r.sequence),y:outliers.map(r=>r.isotopes?.[iso]?.value??null),customdata:outliers.map(custom),text:outliers.map(r=>`${r.label} · ${r.source_index} · ${r.run_label}`),marker:{size:10,symbol:"x",color:color(iso),line:{width:1}},hovertemplate:"%{text}<br>%{y:.4f} ‰<extra>%{fullData.name}</extra>"},
      ...references,
    ]}/>;
  })}</div>;
}

const qcCategories: [QcFlagCategory,string][] = [["statistical","Statistical outliers"],["range","Validity-range flags"],["manual","Manual exclusions"],["pressure_adjustment","Poor pressure adjustment samples"],["no_signal","No-signal samples"]];
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

export function SessionResiduals({analysis,materialId,mapping,interact,overrides,busy,saveOverride}: {
  analysis:SessionAnalysis;materialId:string;mapping:Record<string,string>;interact:ChartInteractions;
  overrides:Record<string,ResidualOverride>;busy:boolean;
  saveOverride:(materialId:string,effect:string,isotope:Isotope,settings:ResidualOverride|null)=>Promise<unknown>;
}) {
  const tr=useTranslation();
  const [editing,setEditing]=useState<string|null>(null);
  const material=analysis.diagnostics_after.materials.find(m=>m.material_id===materialId);
  const before=analysis.diagnostics_before.materials.find(m=>m.material_id===materialId);
  const qcId=analysis.residual_qc_id??materialId;
  const resultRows=new Map(analysis.rows.map(row=>[row.id,row]));
  if(!material)return <Empty>{tr("No identified QC or anchor observations for residual diagnostics.")}</Empty>;
  const decision=(iso:Isotope,effect:string)=>effect==="pressure_dependence"?analysis.failed_analysis_corrections?.[iso]:analysis.residual_corrections?.[iso]?.[effect];
  const statusLabel=(status?:string)=>tr(status==="applied"?"Applied":status==="not_improved"?"Not applied: QC SD did not improve":status==="not_selected"?"Not applied: another predictor improves QC SD more":status==="disabled"?"Disabled":status==="uncertainty_required"?"Enter coefficient uncertainty":status==="insufficient_evidence"?"Insufficient QC for fitting":status==="unavailable_results"?"No eligible results with complete budgets":"Diagnostic only");
  const traces=(fit:Fit|undefined,iso:Isotope,stage:"before"|"after")=>{
    if(!fit?.points?.length)return [];
    const allPoints=fit.points;
    const groups=stage==="before"?[true]:[true,false];
    const pointTraces=groups.flatMap(applied=>{
      const points=allPoints.filter(p=>Boolean(resultRows.get(p.id??"")?.isotopes?.[iso]?.residual_correction)===applied);
      if(!points.length)return [];
      return [{type:"scatter",mode:"markers",name:tr(stage==="before"?"Before residual correction":applied?"Residual correction applied":"Uncorrected observations"),
        meta:{correctionStage:stage,correctionApplied:applied},x:points.map(p=>p.x),y:points.map(p=>p.y),
        customdata:points.map(p=>[mapping[p.id??""]??p.id,iso==="d13c"?"d13C":"d18O"]),
        marker:{size:7,color:stage==="before"?beforeColor:color(iso),symbol:stage==="before"?"circle-open":"circle"}}];
    });
    const retained=allPoints.filter(p=>!p.excluded_from_fit&&Number.isFinite(p.x));
    const bounds=retained.length?[Math.min(...retained.map(p=>p.x)),Math.max(...retained.map(p=>p.x))]:[];
    if(fit.slope==null||fit.intercept==null||bounds.length!==2||bounds[0]===bounds[1])return pointTraces;
    return [...pointTraces,{type:"scatter",mode:"lines",name:tr(stage==="before"?"Before correction fit":"After correction fit"),showlegend:true,hoverinfo:"skip",x:bounds,y:bounds.map(x=>fit.intercept!+fit.slope!*x),line:{color:stage==="before"?beforeColor:color(iso),width:2,dash:stage==="before"?"dash":"solid"}}];
  };
  return <div className="metro-stack station-residual-workspace">
    <p className="metro-muted">{tr("QC determines the correction. The predictor with the largest QC SD reduction is applied once per isotope to all eligible observations, including outside the method range. Its uncertainty is included in results and exports.")}</p>
    <p className="metro-muted">{tr("Green: applied. Amber: no SD improvement. Gray: not applied or diagnostic only.")}</p>
    <p className="metro-muted">{tr("QC SD is compared on the observations used for fitting; this is not independent validation.")}</p>
    <p className="metro-muted">{tr("Pressure-flagged analyses receive one joint pressure-and-intensity correction fitted on pressure-failed QC. Robust residual screening excludes extreme QC from fitting while keeping observations visible. The reference is zero pressure difference and the median initial intensity of retained nonfailed QC. If the joint fit is unavailable, the nonfailed-QC pressure model is used and labeled as a fallback. Ordinary residual corrections never apply to pressure-flagged analyses. Qualification range warnings remain separate. Coefficient covariance and extrapolation are recorded. A flat fitted QC trend is not independent validation; original review flags remain.")}</p>
    <div className="metro-table-wrap"><table className="station-residual-table"><thead><tr><th>{tr("Residual effect")}</th>{isotopes.map(iso=><th key={iso}>{isotopeLabel[iso]} · {tr("QC SD / ‰")}</th>)}<th>{tr("Adjustments")}</th></tr></thead><tbody>{effects.map(([effect,label],index)=><Fragment key={effect}>
      <tr><th scope="row">{tr(label)}{effect==="pressure_dependence"&&analysis.failed_analysis_corrections?.d13c&&<small>{tr("Failed-analysis correction")}</small>}</th>{isotopes.map(iso=>{
        const d=decision(iso,effect),fit=material.isotopes[iso][effect];
        return <td key={iso} className={`station-effect-value ${d?.status==="applied"?"effect-small":d?.status==="not_improved"?"effect-relevant":""}`}>
          {d?.before&&d.after?<strong>{readable(d.before.sd,4)} → {readable(d.after.sd,4)} · {readable((d.sd_reduction_fraction??0)*100,1)}%</strong>:<strong>Δ = {readable(fit?.effect_span)} ‰</strong>}
          <small>{statusLabel(d?.status)}</small>
          <small>{d?.model?`b = ${readable(d.model.slope,5)} ± ${readable(d.model.u_slope,5)}`:`b = ${readable(fit?.slope,5)}`} · n={d?.n??fit?.n??0}{d?.status==="applied"?` · ${tr("Corrected observations")}: ${d.applied_n??0}`:""}</small>
          {effect==="pressure_dependence"&&d&&<small>{tr("Pressure-corrected unknowns")}: {d.unknown_applied_n??0}</small>}
          {effect==="pressure_dependence"&&d?.training_population&&<small>{tr(d.training_population==="pressure_failed_qc"?"Pressure-failed QC joint fit":"Nonfailed QC fallback")}</small>}
          {d?.model?.intensity_slope!=null&&<>
            <small>{tr("Initial-intensity coefficient")}: {readable(d.model.intensity_slope,5)} ± {readable(d.model.u_intensity_slope,5)} · I₀ = {readable(d.model.intensity_ref,3)} V</small>
            <small>{tr("QC excluded from pressure fit")}: {d.fit_excluded_ids?.length??0}</small>
            <small>{tr("Pressure-failed QC intensity slope before / after")}: {readable(d.intensity_before?.slope,5)} / {readable(d.intensity_after?.slope,5)}</small>
          </>}
          {d?.qc_pool&&<><small>{tr("Recovered QC admitted")}: {d.qc_pool.admitted_ids.length} · {tr("QC pool SD before / with corrected failures")}: {readable(d.qc_pool.before.sd,4)} / {readable(d.qc_pool.after.sd,4)}</small>{d.qc_pool.status==="not_improved"&&<small>{tr("Not admitted: pooled QC SD did not improve")}</small>}</>}
          {index<3&&<small>{tr("Remaining slope on retained QC")}: {readable(analysis.diagnostics_after.materials.find(m=>m.material_id===qcId)?.isotopes[iso][effect]?.slope,5)}</small>}
        </td>;
      })}<td>{index<3?<button type="button" className="metro-btn station-gear" aria-label={`${tr("Correction parameters")}: ${tr(label)}`} aria-expanded={editing===effect} aria-controls={`settings-${effect}`} onClick={()=>setEditing(editing===effect?null:effect)}><Settings size={16}/></button>:"—"}</td></tr>
      {editing===effect&&<tr><td colSpan={4}><div id={`settings-${effect}`}><ResidualControls effect={effect} materialId={qcId} overrides={overrides} busy={busy} save={saveOverride}/></div></td></tr>}
    </Fragment>)}</tbody></table></div>
    <div className="metro-grid station-isotope-pair">{effects.slice(0,3).flatMap(([effect,label,x])=>isotopes.map(iso=><section key={`${iso}-${effect}`} id={iso==="d13c"?`residual-${effect}`:`residual-${effect}-${iso}`}><Chart title={`${isotopeLabel[iso]}: ${tr(label)}`} x={tr(x)} y={`${isotopeLabel[iso]} / ‰ VPDB`} height={330} interactions={interact(`session-${effect}-${iso}`)} data={[...traces(before?.isotopes[iso][effect],iso,"before"),...traces(material.isotopes[iso][effect],iso,"after")]}/></section>))}</div>
    {effects.slice(3).map(([effect,label,x])=><section key={effect} id={`residual-${effect}`}><h3>{tr(label)}</h3><div className="metro-grid station-isotope-pair">{isotopes.map(iso=><Chart key={iso} title={isotopeLabel[iso]} x={tr(x)} y="‰" height={300} interactions={interact(`session-${effect}-${iso}`)} data={traces(material.isotopes[iso][effect],iso,"after")}/>)}</div></section>)}
  </div>;
}

export function TraceableExport({ detail, group, ...props }: WorkspaceProps & { detail: ResultsSessionDetail; group: string }) {
  const tr=useTranslation();
  const hasResults=detail.runs.some(run=>run.evaluation?.results.some(row=>row.role==="unknown"&&!row.excluded&&(!group||detail.groups[row.id]===group)));
  const [includeOutliers,setIncludeOutliers]=useState(false);
  const [client,setClient]=useState(detail.client),[series,setSeries]=useState(group||detail.project||detail.name);
  const [identifierSource,setIdentifierSource]=useState("raw_label"),[sampleSource,setSampleSource]=useState("raw_comment");
  return <Panel title={tr("Traceable results export")}><div className="metro-form-grid"><Field label={tr("Client name for export")}><input value={client} maxLength={180} onChange={e=>setClient(e.target.value)}/></Field><Field label={tr("Series identifier for filename")}><input value={series} maxLength={180} onChange={e=>setSeries(e.target.value)}/></Field></div><div className="metro-form-grid">{[["Client worksheet identifier",identifierSource,setIdentifierSource],["Client worksheet sample",sampleSource,setSampleSource]].map(([label,value,setValue])=><Field key={String(label)} label={tr(String(label))}><select value={String(value)} onChange={e=>(setValue as (value:string)=>void)(e.target.value)}><option value="raw_label">{tr("Sample label (with corrections)")}</option><option value="raw_comment">{tr("Sample identifier (with corrections)")}</option></select></Field>)}</div><p className="metro-muted">{tr("IRMS naming convention: Results for [series] series - stable C & O isotopes - P2L - [client] - [DDMMYYYY]. Names are sanitized; source records remain unchanged.")}</p><p className="metro-muted">{tr("The package contains a formatted PDF calculation certificate, Excel results, CSV and the complete calculation JSON. Review and blocked results retain their decision labels.")}</p><label className="metro-check"><input type="checkbox" checked={includeOutliers} onChange={e=>setIncludeOutliers(e.target.checked)}/>{tr("Include excluded results")}</label><div className="metro-actions">{[["zip","Results + calculation dossier"],["pdf","PDF calculation certificate"],["xlsx","Client output Excel","client_output"],["xlsx","Whole results Excel","dataset"],["csv","Results CSV"],["json","Calculation JSON"]].map(([format,label,output_type])=><button className={`metro-btn ${format==="zip"?"primary":""}`} key={output_type??format} disabled={props.busy||(output_type==="dataset"?!detail.runs.some(r=>r.evaluation?.results.length):!hasResults)} onClick={async()=>{const record=await props.act(`/results-sessions/${detail.id}/exports`,{format,output_type:output_type??"combined",include_outliers:includeOutliers,group:group||null,client_name:client,series_name:series,identifier_source:identifierSource,sample_source:sampleSource}) as SessionExport|undefined;if(record){const a=document.createElement("a");a.href=`${METROLOGY_API}/session-exports/${record.id}`;a.download=record.filename;a.click();}}}><Download size={14}/>{tr(label)}</button>)}</div><div className="metro-table-wrap"><table><thead><tr><th>{tr("Generated")}</th><th>{tr("Group / results")}</th><th>{tr("Download")}</th></tr></thead><tbody>{detail.exports.map(item=><tr key={item.id}><td>{item.created_at.slice(0,16).replace("T"," ")}</td><td>{item.group??tr("All groups")} · {item.rows}</td><td><a className="station-session-link" href={`${METROLOGY_API}/session-exports/${item.id}`}>{item.filename}</a></td></tr>)}</tbody></table></div></Panel>;
}
