"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ChartRangeControls } from "./chart-range-controls";
import { rangeFlags, qcRangeRows, filterStationFigure, type ChartRanges } from "@/lib/station-chart-filters";
import { withCalibrationStages } from "@/lib/metrology-envelopes";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useCallback, useMemo, useRef, useState, type ContextType, type FormEvent, type ReactNode } from "react";
import { ArrowLeft, Building2, FolderOpen, Plus, Search, ShieldCheck, Upload, Download, FlaskConical } from "lucide-react";
import { useTranslation } from "@/components/layout/language-provider";
import { FileInput } from "@/components/ui/file-input";
import { useSessionStore } from "@/store/use-session-store";
import { METROLOGY_API, isotopeLabel, isotopes, metroRequest, type Method, type ResultsSession, type ResultsSessionDetail, type Run, type SessionAnalysis, type ResidualOverride, type QcFlagCategory, type Isotope } from "@/lib/metrology";
import { BusyButton, Empty, Field, Inspect, Panel, Status, type WorkspaceProps } from "./shared";
import { Methods } from "./methods";
import { LongTermCharts } from "./results-station";
import { RunReview } from "./runs";
import { AnchorPair, SessionMethodSummary, SessionQcSummary } from "./qualification-summary";
import { CorrectionValidation, SessionUncertainty, SessionProcessing, SessionQcSequence, SessionOutlierTable, SessionResiduals, TraceableExport } from "./session-science";
import { RowReview } from "./row-review";
import { MetrologyChartWorkspace, MetrologyConsultation, MetrologyProcessingResults, MetrologyChartHeight, MetrologyToolsSession, MetrologyToolActive, MetrologyEvidenceBridge, MetrologySymbolSize, MetrologyStationFilters, MetrologyChartAppearance } from "./consultation-context";

const DiagnosticsTools=dynamic(()=>import("@/app/(dashboard)/diagnostics/page"),{ssr:false,loading:()=> <p>Loading diagnostics…</p>});
const CalibrationTools=dynamic(()=>import("@/app/(dashboard)/calibration/page"),{ssr:false,loading:()=> <p>Loading calibration charts…</p>});
const ProcessingTools=dynamic(()=>import("@/app/(dashboard)/processing/page"),{ssr:false,loading:()=> <p>Loading processing charts…</p>});

type Props=WorkspaceProps & { sessionId?:string; runId?:string; create?:boolean; newContext?:"routine"|"qualification"; openSession:(id:string)=>void; navigate:(section:string)=>void };

export function ResultsSessions(props:Props) {
  const tr=useTranslation();
  const router=useRouter();
  const sessions=props.state.results_sessions??[];
  const selectedId=props.sessionId || sessions.find(s=>s.run_ids.includes(props.runId??""))?.id;
  const [search,setSearch]=useState("");
  const [client,setClient]=useState("");
  const [context,setContext]=useState("");
  const [origin,setOrigin]=useState(props.state.operational?"observed":"all");
  const [creating,setCreating]=useState(props.create??false);
  const [detail,setDetail]=useState<ResultsSessionDetail|null>(null);
  const [error,setError]=useState("");
  useEffect(()=>setCreating(props.create??false),[props.create]);
  useEffect(()=>{if(detail?.context==="qualification"&&detail.id===selectedId&&detail.qualification_id)router.replace(`/metrology/qualification?qualification=${detail.qualification_id}`);},[detail?.id,detail?.context,detail?.qualification_id,selectedId,router]);
  useEffect(()=>{
    setError("");
    if(!selectedId){setDetail(null);return;}
    const controller=new AbortController();
    metroRequest<ResultsSessionDetail>(`/results-sessions/${selectedId}`,{signal:controller.signal}).then(setDetail).catch(e=>{if(!controller.signal.aborted)setError(e.message);});
    return ()=>controller.abort();
  },[selectedId,props.state]);
  if(selectedId) return <div className="metro-stack">{error&&<div className="metro-note error" role="alert">{tr(error)} <button className="metro-btn" onClick={()=>props.navigate("results")}>{tr("All results sessions")}</button></div>}{detail?.id===selectedId?<SessionDetail key={detail.id} {...props} detail={detail} sessionNavigation={<button className="metro-btn" onClick={()=>props.navigate("results")}><ArrowLeft size={14}/>{tr("All results sessions")}</button>}/>:<p role="status">{tr("Opening saved results session…")}</p>}</div>;
  const filtered=sessions.filter(s=>(origin==="all"||(origin==="observed"?s.calibration_verification==="simulation_assumption":s.calibration_verification!=="simulation_assumption"))&&(!client||s.client===client)&&(!context||s.context===context)&&`${s.name} ${s.client} ${s.project} ${s.method_name}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="metro-stack">
    <div className="station-library-heading"><div><span className="metro-eyebrow">{tr("Local results library")}</span><h2>{tr("Clients, batches and their calibration history")}</h2><p className="metro-muted">{tr("Reopen a session to consult its data, qualification, QC, uncertainty and saved exports.")}</p></div><button className="metro-btn primary" onClick={()=>setCreating(!creating)}><Plus size={15}/>{tr(creating?"Close session form":"New results session")}</button></div>
    {creating&&<NewSession {...props} onCreated={props.openSession}/>}
    {props.state.operational&&<div className="metro-actions">{[["observed","Observed series"],["synthetic","Mock workflows"],["all","All sessions"]].map(([id,label])=><button className={`metro-btn ${origin===id?"primary":""}`} key={id} onClick={()=>setOrigin(id)}>{tr(label)}</button>)}</div>}
    <div className="station-search"><Field label={tr("Find a session")}><div className="station-search-input"><Search size={16}/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder={tr("Session, client, project or method")}/></div></Field><Field label={tr("Client")}><select value={client} onChange={e=>setClient(e.target.value)}><option value="">{tr("All clients")}</option>{[...new Set(sessions.map(s=>s.client))].sort().map(c=><option key={c}>{c}</option>)}</select></Field><Field label={tr("Workflow")}><select value={context} onChange={e=>setContext(e.target.value)}><option value="">{tr("All workflows")}</option><option value="routine">{tr("Routine results")}</option><option value="qualification">{tr("Metrological qualification")}</option></select></Field></div>
    <Panel><div className="station-register-caption"><b>{filtered.length} {tr("saved sessions")}</b><span>{tr("Stored locally with source data and calculation history")}</span></div>{filtered.length?<div className="metro-table-wrap"><table className="station-register"><thead><tr><th>{tr("Client / project")}</th><th>{tr("Results session")}</th><th>{tr("Acquired")}</th><th>{tr("Method")}</th><th>{tr("Analyses")}</th><th>{tr("Decision")}</th><th/></tr></thead><tbody>{[...filtered].reverse().map(s=><tr key={s.id}><td><b>{s.client}</b><small>{s.project}</small></td><td><button className="station-session-link" onClick={()=>props.openSession(s.id)}>{s.name}</button><small>{tr(s.context)} · {s.run_ids.length} {tr("workbooks")} · {s.sample_groups.length} {tr("groups")}</small></td><td>{s.acquired_date??s.created_at.slice(0,10)}</td><td>v{props.state.methods.find(m=>m.id===s.method_id)?.version??"—"}</td><td>{s.analysis_count}</td><td><Status value={s.status}/></td><td><button className="metro-btn" onClick={()=>props.openSession(s.id)} aria-label={`${tr("Open session")} ${s.name}`}><FolderOpen size={15}/>{tr("Open")}</button></td></tr>)}</tbody></table></div>:<Empty>{tr("Create a results session, assign its client and method, then upload the Qtegra export.")}</Empty>}</Panel>
  </div>;
}

function NewSession(props:WorkspaceProps & {onCreated:(id:string)=>void;newContext?:"routine"|"qualification"}) {
  const tr=useTranslation();
  const [context,setContext]=useState<"routine"|"qualification">(props.newContext??"routine");
  const [name,setName]=useState("");const [client,setClient]=useState("");const [project,setProject]=useState("");
  const [methodId,setMethod]=useState((props.newContext==="qualification"?props.state.methods.filter(m=>m.status==="draft").at(-1)?.id:null)??props.state.active_method?.id??props.state.methods.at(-1)?.id??"");
  const [basis,setBasis]=useState("already_vpdb");
  const [evidence,setEvidence]=useState("Qtegra dual-point normalization transferred from the selected qualified method. Reference-gas calibration is separate; Qtegra drift correction is disabled. Secondary corrections marked below are already included.");
  const [preapplied,setPreapplied]=useState<string[]>([]);
  const [notes,setNotes]=useState("");
  const method=props.state.methods.find(m=>m.id===methodId);
  async function submit(event:React.FormEvent){
    event.preventDefault(); if(!method)return;
    let selected=method;
    let qid:string|null=null;
    if(context==="qualification"){
      if(selected.status!=="draft"){
        const draft=await props.act("/methods",{config:{...selected.config,name:`${selected.config.name} · qualification`}}) as Method|undefined;
        if(!draft)return;selected=draft;
      }
      qid=props.state.qualifications.find(q=>q.status==="open"&&q.method_id===selected.id)?.id??null;
      if(!qid){const q=await props.act("/qualifications",{method_id:selected.id,trigger:"periodic",carousel:[...selected.config.anchor_ids,...(selected.config.qc_id?[selected.config.qc_id]:[])].flatMap(material_id=>[60,100,140].map(mass_ug=>({material_id,mass_ug,replicates:5})))}) as {id:string}|undefined;if(!q)return;qid=q.id;}
    }
    const result=await props.act("/results-sessions",{name,client,project,context,method_id:selected.id,qualification_id:qid,notes,method_name:selected.config.name,intended_use:selected.config.intended_use,input_basis:context==="qualification"?"instrument_delta":basis,preapplied_corrections:context==="qualification"?[]:preapplied,processing_evidence:context==="qualification"?"Qualification carousel: instrument deltas before residual correction and dual-point normalization.":evidence}) as ResultsSession|undefined;
    if(result)props.onCreated(result.id);
  }
  return <Panel title={tr("Create a results session")}><form className="metro-stack station-session-form" onSubmit={submit}>
    <div className="station-form-section"><div className="station-form-heading"><Building2 size={19}/><div><h3>{tr("Client and analytical batch")}</h3><p>{tr("These labels keep client samples and exports together.")}</p></div></div><div className="metro-form-grid"><Field label={tr("Session name")}><input required value={name} onChange={e=>setName(e.target.value)} placeholder={tr("Carbonates · batch 12 · October 2026")}/></Field><Field label={tr("Client")}><input required list="station-clients" value={client} onChange={e=>setClient(e.target.value)} placeholder={tr("Laboratory, company or research group")}/><datalist id="station-clients">{[...new Set((props.state.results_sessions??[]).map(s=>s.client))].map(c=><option key={c} value={c}/>)}</datalist></Field><Field label={tr("Project / sample series")}><input value={project} onChange={e=>setProject(e.target.value)} placeholder={tr("Project or sample collection")}/></Field><Field label={tr("Workflow")}><select value={context} onChange={e=>{const next=e.target.value as typeof context;setContext(next);setMethod(next==="routine"?props.state.active_method?.id??"":props.state.methods.filter(m=>m.status==="draft").at(-1)?.id??methodId);}}><option value="routine">{tr("Routine sample processing")}</option><option value="qualification">{tr("Periodic metrological qualification")}</option></select></Field></div></div>
    <div className="station-form-section"><div className="station-form-heading"><ShieldCheck size={19}/><div><h3>{tr("Method and Qtegra processing")}</h3><p>{tr("The selected qualification stays linked to this session after later calibrations.")}</p></div></div><div className="metro-form-grid"><Field label={tr("Validated method / qualification template")}><select required value={methodId} onChange={e=>setMethod(e.target.value)}><option value="">{tr("Select a method")}</option>{props.state.methods.filter(m=>context==="routine"?m.status==="active":["active","draft"].includes(m.status)).map(m=><option key={m.id} value={m.id}>{m.config.name} · v{m.version} · {tr(m.status)}</option>)}</select></Field><Field label={tr("Values in the Qtegra export")}><select value={context==="qualification"?"instrument_delta":basis} disabled={context==="qualification"} onChange={e=>setBasis(e.target.value)}><option value="already_vpdb">{tr("Already normalized on VPDB with this method")}</option><option value="instrument_delta">{tr("Instrument deltas before normalization")}</option></select></Field></div>
      {context==="routine"&&<><div className="station-choice-note"><ShieldCheck size={17}/><p>{tr(basis==="already_vpdb" ? "Qtegra normalization is retained. The station propagates its uncertainty and applies only remaining approved residual corrections." : "Instrument deltas will receive the approved residual correction and dual-point normalization.")}</p></div><div className="metro-actions">{isotopes.filter(i=>method?.config.corrections?.[i]).map(i=><label className="metro-check" key={i}><input type="checkbox" checked={preapplied.includes(i)} onChange={e=>setPreapplied(p=>e.target.checked?[...p,i]:p.filter(v=>v!==i))}/>{isotopeLabel[i]} · {tr("Residual correction already applied in Qtegra")}</label>)}</div><Field label={tr("Qtegra processing record / transfer evidence")}><textarea required minLength={3} value={evidence} onChange={e=>setEvidence(e.target.value)}/></Field></>}
      <Field label={tr("Session notes")}><input value={notes} onChange={e=>setNotes(e.target.value)} placeholder={tr("Sample preparation, purpose or review notes")}/></Field></div><div className="metro-actions"><BusyButton busy={props.busy}>{tr("Create session and continue to import")}</BusyButton></div>
  </form></Panel>;
}

export function SessionDetail({detail, qualificationReview, sessionNavigation, ...props}:Props & {detail:ResultsSessionDetail;qualificationReview?:ReactNode;sessionNavigation?:ReactNode}) {
  const tr=useTranslation();
  const qualificationMode = detail.context === "qualification";
  const [tab,setTab]=useState(()=>{try{return sessionStorage.getItem(`metrology-tab:${detail.id}`)||"summary";}catch{return "summary";}});
  useEffect(()=>{sessionStorage.setItem(`metrology-tab:${detail.id}`,tab);},[detail.id,tab]);
  const [visitedTools,setVisitedTools]=useState<string[]>([]);
  const [bridges,setBridges]=useState<Record<string,{session_id:string;row_mapping:Record<string,string>}>>({});
  const bridgeCache=useRef(new Map<string,{session_id:string;row_mapping:Record<string,string>}>());
  const [runId,setRun]=useState(props.runId||[...detail.runs].filter(r=>r.evaluation?.results.some(row=>row.role==="unknown")).sort((a,b)=>(b.acquired_date??b.created_at).localeCompare(a.acquired_date??a.created_at))[0]?.id||detail.run_ids.at(-1)||"");
  const [diagnosticScope,setDiagnosticScope]=useState("all");
  const [chartRanges,setChartRanges]=useState<ChartRanges>({});
  const [group,setGroup]=useState("");
  const [files,setFiles]=useState<File[]>([]);
  const importingRef=useRef(false);
  const [importing,setImporting]=useState(false);
  const [fileInputKey,setFileInputKey]=useState(0);
  const [importIndex,setImportIndex]=useState(0);
  const [batch,setBatch]=useState("Main batch");
  const [groups,setGroups]=useState(detail.groups);
  const [toolId,setTool]=useState("");const [toolsError,setToolsError]=useState("");
  const [toolsAttempt,setToolsAttempt]=useState(0);
  const [analysis,setAnalysis]=useState<SessionAnalysis|null>(null);
  const [analysisError,setAnalysisError]=useState("");
  const [outlierMethod,setOutlierMethod]=useState(detail.outlier_screening?.method??"sigma");
  const [threshold,setThreshold]=useState(detail.outlier_screening?.threshold??3);
  const [outlierVisibility,setOutlierVisibility]=useState<Record<Isotope,boolean>>(()=>{
    try{return {...{d13c:true,d18o:true},...JSON.parse(sessionStorage.getItem(`qc-outlier-visibility:${detail.id}`)??"{}")} as Record<Isotope,boolean>;}catch{return {d13c:true,d18o:true};}
  });
  const toggleOutlierVisibility=(iso:Isotope,visible:boolean)=>setOutlierVisibility(previous=>{
    const next={...previous,[iso]:visible};try{sessionStorage.setItem(`qc-outlier-visibility:${detail.id}`,JSON.stringify(next));}catch{}return next;
  });
  const [outlierTypes,setOutlierTypes]=useState<Record<QcFlagCategory,boolean>>(()=>{
    const defaults={statistical:true,range:true,manual:true,failed:true};
    try{return {...defaults,...JSON.parse(sessionStorage.getItem(`qc-outlier-types:${detail.id}`)??"{}")};}catch{return defaults;}
  });
  const toggleOutlierType=(category:QcFlagCategory,visible:boolean)=>setOutlierTypes(previous=>{
    const next={...previous,[category]:visible};try{sessionStorage.setItem(`qc-outlier-types:${detail.id}`,JSON.stringify(next));}catch{}return next;
  });
  const [includeAllData,setIncludeAllData]=useState(false);
  const [symbolSize,setSymbolSize]=useState(8);
  const [materialId,setMaterialId]=useState(detail.chart_settings?.diagnostic_material_id??detail.method?.config.qc_id??"");
  const [carbonatePreapplied,setCarbonatePreapplied]=useState(false);
  const [carbonateMaterial,setCarbonateMaterial]=useState<"calcite"|"aragonite">(detail.chart_settings?.carbonate_material??"calcite");
  const [mapping,setMapping]=useState<Record<string,string>>({});
  const revisionKey=`${detail.method?.id}:${detail.method?.revision}:`+detail.runs.map(r=>`${r.id}:${r.revision}:${r.latest_evaluation_id}`).join("|");
  useEffect(()=>{setBridges({});setMapping({});setTool("");},[detail.id,revisionKey]);
  const analysisSettingsKey=JSON.stringify([detail.groups,detail.residual_overrides,detail.method?.config,detail.outlier_screening]);
  const rangeDataQuery=useQuery({queryKey:["processing-linearity-preview-data",bridges.all?.session_id],queryFn:()=>api.getProcessingLinearityPreviewData(bridges.all!.session_id),enabled:Boolean(bridges.all?.session_id),staleTime:Infinity});
  const linearityMaterialId=detail.method?.config.qc_id??materialId;
  const rangeRows=useMemo(()=>{
    const rows=rangeDataQuery.data?.rows??[];
    if(tab!=="calibration"||includeAllData)return rows;
    return qcRangeRows(rows,detail.runs.flatMap(run=>run.evaluation?.results??run.measurements),mapping,linearityMaterialId);
  },[rangeDataQuery.data,tab,includeAllData,detail.runs,linearityMaterialId,mapping]);
  const ranges=useMemo(()=>rangeFlags(rangeRows,chartRanges),[rangeRows,chartRanges]);
  const rangeQuery=useMemo(()=>{
    const reverse=Object.fromEntries(Object.entries(mapping).map(([id,label])=>[label,id]));
    const exclusions:{d13c:string[];d18o:string[]}={d13c:[],d18o:[]};
    for(const flag of ranges)if(reverse[flag.row])exclusions[flag.isotope==="d13C"?"d13c":"d18o"].push(reverse[flag.row]);
    return ranges.length?JSON.stringify(exclusions):"";
  },[ranges,mapping]);
  const analysisUrl=`/results-sessions/${detail.id}/${rangeQuery?"analysis-preview":"analysis"}?include_all_data=${includeAllData}`;
  const analysisRequest=useMemo(()=>rangeQuery?{method:"POST",body:rangeQuery}:undefined,[rangeQuery]);
  useEffect(()=>{
    const controller=new AbortController();setAnalysisError("");
    const timer=setTimeout(()=>metroRequest<SessionAnalysis>(analysisUrl,{...analysisRequest,signal:controller.signal}).then(setAnalysis).catch(e=>{if(!controller.signal.aborted)setAnalysisError(e.message);}),rangeQuery?180:0);
    return ()=>{clearTimeout(timer);controller.abort();};
  },[detail.id,revisionKey,analysisSettingsKey,analysisUrl,analysisRequest]);
  const setLegacySession=useSessionStore(s=>s.setSessionId);
  const run=detail.runs.find(r=>r.id===runId)??detail.runs.at(-1);
  useEffect(()=>setGroups(detail.groups),[detail.groups]);
  const options=[...new Set(Object.values(detail.groups))];
  const selectedIds=run?.evaluation?.results.filter(r=>!group||(detail.groups[r.id]??"Main batch")===group).map(r=>r.id);
  const toolsRun=tab==="diagnostics"?diagnosticScope:detail.run_ids.length?"all":undefined;
  const toolsActive=["diagnostics","calibration","processing","import","uncertainty"].includes(tab);
  useEffect(()=>{
    if(!toolsActive||!toolsRun)return;
    const key=`${detail.id}:${revisionKey}:${toolsRun}`;
    const accept=(result:{session_id:string;row_mapping:Record<string,string>})=>{
      bridgeCache.current.set(key,result);setLegacySession(result.session_id);setTool(result.session_id);if(toolsRun==="all")setMapping(result.row_mapping??{});
      setBridges(previous=>({...previous,[toolsRun]:result}));
      if(["diagnostics","calibration","processing"].includes(tab))setVisitedTools(previous=>previous.includes(tab)?previous:[...previous,tab]);
    };
    const cached=bridgeCache.current.get(key);
    if(cached){accept(cached);return;}
    const controller=new AbortController();setToolsError("");
    metroRequest<{session_id:string;row_mapping:Record<string,string>}>(`/results-sessions/${detail.id}/tools/${toolsRun}`,{method:"POST",body:JSON.stringify({actor:props.decision.actor||"Results reader",reason:"Open linked raw data and existing plotting tools for consultation"}),signal:controller.signal}).then(result=>{if(!controller.signal.aborted)accept(result);}).catch(e=>{if(!controller.signal.aborted)setToolsError(e.message);});
    return ()=>controller.abort();
  },[toolsActive,toolsRun,detail.id,revisionKey,setLegacySession,props.decision.actor,toolsAttempt,tab]);
  async function importWorkbooks(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if(!files.length||props.busy||importingRef.current)return;
    importingRef.current=true;
    setImporting(true);
    let completed=0;
    try {
      const saved=await props.act(`/results-sessions/${detail.id}/chart-settings`,{carbonate_material:carbonateMaterial,diagnostic_material_id:materialId||null},"PUT");
      if(!saved)return;
      for(const [index,file] of files.entries()) {
        setImportIndex(index+1);
        const data=new FormData();
        data.append("file",file);
        data.append("metadata",JSON.stringify({...props.decision,results_session_id:detail.id,sample_group:batch,carbonate_material:carbonateMaterial,carbonate_correction_preapplied:carbonatePreapplied}));
        const result=await props.upload("/runs/import",data) as Run|undefined;
        if(!result)break;
        completed++;
        setRun(result.id);
      }
    } finally {
      if(completed) {
        setFiles(files.slice(completed));
        setFileInputKey(key=>key+1);
      }
      importingRef.current=false;
      setImporting(false);
    }
  }
  const saveOverride=useCallback(async(material:string,effect:string,isotope:Isotope,settings:ResidualOverride|null)=>{
    const result=await props.act(`/results-sessions/${detail.id}/residual-overrides`,{material_id:material,effect,isotope,settings},"PUT");
    if(result) setAnalysis(await metroRequest<SessionAnalysis>(analysisUrl,analysisRequest));
    return result;
  },[props.act,detail.id,outlierMethod,threshold,analysisUrl,analysisRequest]);
  const saveScreening=useCallback(async()=>{
    const result=await props.act(`/results-sessions/${detail.id}/outlier-screening`,{method:outlierMethod,threshold},"PUT");
    if(result)setAnalysis(await metroRequest<SessionAnalysis>(analysisUrl,analysisRequest));
  },[props.act,detail.id,outlierMethod,threshold,analysisUrl,analysisRequest]);
  const processingResults=useMemo(()=>Object.fromEntries((analysis?.rows??[]).filter(row=>(!group||row.role!=="unknown"||row.sample_group===group)&&mapping[row.id]).map(row=>[mapping[row.id],row])),[analysis,group,mapping]);
  const qcTarget=useMemo(()=>{
    const material=props.state.materials.find(item=>item.id===detail.method?.config.qc_id);
    return Object.fromEntries(isotopes.map(iso=>[iso,material?.assigned[iso].value??null])) as Record<Isotope,number|null>;
  },[props.state.materials,detail.method?.config.qc_id]);
  const comparison=useMemo(()=>{
    const values=new Map<string,{before:(number|null)[];after:(number|null)[]}>();
    for(const row of analysis?.rows??[])if(mapping[row.id]&&(row.role==="qc"||row.role==="anchor")){
      const before=isotopes.map(iso=>analysis?.diagnostics_before.materials.find(m=>m.material_id===row.material_id)?.isotopes[iso].intensity_dependence.points?.find(p=>p.id===row.id)?.y??null);
      values.set(mapping[row.id],{before,after:isotopes.map(iso=>row.isotopes?.[iso]?.value??null)});
    }
    return (figure:Record<string,unknown>)=>withCalibrationStages(figure,values,{before:tr("Before correction"),after:tr("After recorded method")});
  },[analysis,mapping,tr]);
  const calibrationWorkspace=useMemo<ContextType<typeof MetrologyChartWorkspace>>(()=>analysis?({
          sequence:interact=><SessionQcSequence analysis={analysis} mapping={mapping} interact={interact} target={qcTarget}/>,
          plots:(interact,manualControls)=><><CorrectionValidation analysis={analysis}/><SessionResiduals analysis={analysis} materialId={includeAllData?"__all__":linearityMaterialId} mapping={mapping} interact={interact} manualControls={manualControls} overrides={detail.residual_overrides??{}} busy={props.busy} saveOverride={saveOverride}/></>,
          carbonateMaterial,comparison,
          colorRowLabels:includeAllData?undefined:rangeRows.map(row=>row.row_label),
          colorScaleRowLabels:[...new Set(isotopes.flatMap(iso=>analysis.diagnostics_after.materials.find(m=>m.material_id===(includeAllData?"__all__":linearityMaterialId))?.isotopes[iso].intensity_dependence.points?.filter(p=>!p.excluded_from_fit&&p.id&&mapping[p.id]).map(p=>mapping[p.id!])??[]))],
          materialLabels:[...new Set(analysis.rows.filter(row=>row.material_id===materialId).map(row=>row.identifier1||row.label))],
          outliers:{method:analysis.outliers.method,threshold:analysis.outliers.threshold,rows:[...analysis.outliers.flags,...analysis.qc_review_flags??[]].map(f=>({row:mapping[f.measurement_id],isotope:f.isotope==="d13c"?"d13C":"d18O",category:f.category??"statistical",excludeFromFit:f.category!=="range",reasons:f.reasons,hidden:!f.metadata_only&&(!outlierVisibility[f.isotope]||!outlierTypes[f.category??"statistical"])}))},
          outlierTable:<SessionOutlierTable analysis={analysis} review={(measurementId,runId)=>{const run=detail.runs.find(r=>r.id===runId);const row=run?.evaluation?.results.find(r=>r.id===measurementId);return run&&row?<RowReview {...props} row={row} run={run}/>:null;}}/>,
          controls:<div className="metro-stack">{tab!=="diagnostics"&&<Field label={tr("Symbol size")}><input type="number" min={2} max={24} value={symbolSize} onChange={e=>setSymbolSize(Math.max(2,Math.min(24,Number(e.target.value)||8)))}/></Field>}{tab==="calibration"&&<label className="metro-check"><input type="checkbox" checked={includeAllData} onChange={e=>setIncludeAllData(e.target.checked)}/>{tr("Include all available data in linearity analysis")}</label>}<button type="button" className="metro-btn" onClick={()=>{setIncludeAllData(false);setChartRanges({});for(const category of ["statistical","range","manual","failed"] as const)toggleOutlierType(category,true);for(const iso of isotopes)toggleOutlierVisibility(iso,true);}}>{tr("Show all available QC")}</button><ChartRangeControls rows={rangeRows} value={chartRanges} onChange={setChartRanges}/><Field label={tr("Outlier method")}><select value={outlierMethod} onChange={e=>{setOutlierMethod(e.target.value as "sigma"|"iqr");setThreshold(e.target.value==="iqr"?1.5:3);}}><option value="sigma">Sigma</option><option value="iqr">IQR</option></select></Field><Field label={tr("Screening threshold")}><input type="number" min={.5} max={10} step={.5} value={threshold} onChange={e=>{const value=Number(e.target.value);if(value>=.5&&value<=10)setThreshold(value);}}/></Field>
            <button className="metro-btn" disabled={props.busy||(analysis.outliers.method===outlierMethod&&analysis.outliers.threshold===threshold)} onClick={saveScreening}>{tr("Save screening")}</button>
            <fieldset><legend>{tr("Show outliers in charts")}</legend>{([['statistical','Statistical outliers'],['range','Validity-range flags'],['manual','Manual exclusions'],['failed','Failed analyses']] as const).map(([category,label])=><label key={category} className="metro-check"><input type="checkbox" checked={outlierTypes[category]} onChange={e=>toggleOutlierType(category,e.target.checked)}/>{tr(label)}</label>)}</fieldset>
            <fieldset><legend>{tr("Isotope")}</legend>{isotopes.map(iso=><label key={iso} className="metro-check"><input type="checkbox" checked={outlierVisibility[iso]} onChange={e=>toggleOutlierVisibility(iso,e.target.checked)}/>{isotopeLabel[iso]} · {analysis.outliers.flags.filter(f=>f.isotope===iso).length}</label>)}</fieldset>
            <small className="metro-muted">{tr("Visibility only. Saved flags remain excluded from long-term QC statistics.")}</small>
          </div>,
          review:(labels,section)=><>{[...new Set(labels)].map(label=>{const id=Object.keys(mapping).find(id=>mapping[id]===label);const selectedRun=detail.runs.find(r=>r.measurements.some(m=>m.id===id));const row=selectedRun?.evaluation?.results.find(r=>r.id===id);return row&&selectedRun?<RowReview {...props} key={id} row={row} run={selectedRun} section={section}/>:null;})}</>,
        }):null,[analysis,tab,symbolSize,includeAllData,materialId,mapping,detail.residual_overrides,detail.runs,props.state,props.decision,props.busy,props.act,props.upload,saveOverride,carbonateMaterial,comparison,qcTarget,outlierMethod,threshold,outlierVisibility,outlierTypes,saveScreening,tr,rangeRows,chartRanges,linearityMaterialId]);
  const stationFlags=useMemo(()=>[...(calibrationWorkspace?.outliers?.rows??[]),...ranges,...(analysis?.rows??[]).filter(row=>row.role!=="qc"&&mapping[row.id]).flatMap(row=>{
    const category=row.excluded?"manual":row.issues?.some(issue=>["Qtegra Evaluate is disabled","Qtegra pressure adjustment failed","Qtegra reports an acquisition failure"].includes(issue))?"failed":null;
    return category?isotopes.map(iso=>({row:mapping[row.id],isotope:iso==="d13c"?"d13C":"d18O",category,hidden:!outlierTypes[category]||!outlierVisibility[iso]})):[];
  })],[calibrationWorkspace,ranges,analysis,mapping,outlierTypes,outlierVisibility]);
  const filters=useMemo(()=>{
    const scopedMapping=bridges[diagnosticScope]?.row_mapping??{};
    const rows=(analysis?.rows??[]).filter(row=>!row.excluded&&(diagnosticScope==="all"||row.run_id===diagnosticScope));
    return {controls:calibrationWorkspace?.controls,flags:stationFlags,ranges:chartRanges,supplementaryFigures:[{
      key:"session-intensity-pressure",title:"Signal intensity versus pressure adjustment difference",
      figure:{data:[{type:"scatter",mode:"markers",name:tr("Imported"),x:rows.map(row=>row.i44_v),y:rows.map(row=>row.pressure_mismatch_v),customdata:rows.map(row=>[scopedMapping[row.id]??row.id,"d13C",row.identifier1??row.label,row.identifier2??row.comment]),marker:{color:"#167d87",size:7}}],layout:{title:{text:tr("Signal intensity versus pressure adjustment difference")},xaxis:{title:{text:"Mean I44 / V"}},yaxis:{title:{text:tr("Result − target intensity / V")}}}},
    }]};
  },[calibrationWorkspace,stationFlags,chartRanges,analysis,bridges,diagnosticScope,tr]);
  const diagnosticsAppearance=useCallback((figure:Record<string,unknown>)=>{
    const bridge=bridges[diagnosticScope];
    if(!bridge)return figure;
    const reverse=Object.fromEntries(Object.entries(mapping).map(([id,label])=>[label,id]));
    const flags=stationFlags.flatMap(flag=>{
      const row=bridge.row_mapping[reverse[flag.row]];
      return row?[{...flag,row}]:[];
    });
    return filterStationFigure(figure,flags);
  },[bridges,diagnosticScope,mapping,stationFlags]);
  const tabs=qualificationMode
    ? [["summary","Session summary"],["import","Import"],["calibration","Residual corrections"],["processing","Processing charts"],["method","Qualification settings"],["validation","Review & approval"],["uncertainty","Uncertainty"],["export","Traceable results export"],["sources","Raw source archive"]]
    : [["summary","Session summary"],["import","Import"],["calibration","Residual corrections"],["diagnostics","Diagnostics"],["processing","Processing charts"],["uncertainty","Uncertainty"],["export","Traceable results export"],["sources","Raw source archive"]];
  return <MetrologyEvidenceBridge.Provider value={bridges.all??null}><div className="metro-stack">
    <section className="station-session-cover">{sessionNavigation&&<div className="station-session-navigation">{sessionNavigation}</div>}<div className="station-session-identity"><div className="station-session-heading"><h2>{detail.name}</h2><span className="station-session-client">{detail.client} / {detail.project||tr("Results session")}</span></div>{detail.notes&&<p>{detail.notes}</p>}</div><div className="station-session-method"><small>{tr("Session method")}</small><strong>{detail.method_name}</strong><span>v{detail.method?.version} · {detail.run_ids.length} {tr("workbooks")}</span></div></section>
    <div className="station-lineage"><span><FlaskConical size={15}/>{tr("Qualification")}<Link className="station-session-link" href={`/metrology/qualification?qualification=${detail.qualification_id}`}>{detail.qualification?.approval?.at?.slice(0,10)??tr("Under review")}</Link></span><span><ShieldCheck size={15}/>{tr("Frozen method")}<b>v{detail.method?.version}</b></span><span>{tr("Qtegra input")}<b>{tr(detail.input_basis==="already_vpdb"?"VPDB normalization retained":"Instrument deltas")}</b></span><span>{tr("Results session")}<b>{detail.id.slice(0,8)}</b></span></div>
    <div className="metro-tabs station-tabs" role="tablist" aria-label={tr("Results session workflow")}>{tabs.map(([id,label])=><button key={id} role="tab" aria-selected={tab===id} onClick={()=>setTab(id)}>{tr(label)}</button>)}</div>
    {run&&<div className="station-run-selector">{["import","diagnostics"].includes(tab)&&<Field label={tr("Analytical workbook")}><select value={tab==="diagnostics"?diagnosticScope:run.id} onChange={e=>tab==="diagnostics"?setDiagnosticScope(e.target.value):setRun(e.target.value)}>{tab==="diagnostics"&&<option value="all">{tr("All analytical workbooks")}</option>}{detail.runs.map(r=><option key={r.id} value={r.id}>{r.label} · {tr(r.status)}</option>)}</select></Field>}<Field label={tr("Sample group")}><select value={group} onChange={e=>setGroup(e.target.value)}><option value="">{tr("All sample groups")}</option>{(options.length?options:["Main batch"]).map(g=><option key={g}>{g}</option>)}</select></Field><span className="metro-muted">{["import","diagnostics"].includes(tab)&&!(tab==="diagnostics"&&diagnosticScope==="all")?tr(run.status):`${detail.run_ids.length} ${tr("workbooks")} / ${tr("Whole session")}`}</span></div>}
    {tab==="validation"&&qualificationReview}
    {tab==="summary"&&<><div className="metro-grid station-session-summary-grid"><div className="metro-stack station-method-column"><SessionMethodSummary detail={detail} state={props.state}/>{analysis&&<CorrectionValidation analysis={analysis}/>}</div><SessionQcSummary detail={detail}/></div>{qualificationMode&&<div className="metro-actions"><button className="metro-btn" onClick={()=>setTab("method")}>{tr("Edit qualification method and criteria")}</button></div>}{run?.evaluation?<>

      <AnchorPair method={detail.method?.normalization?detail.method:detail.method&&run.evaluation?.normalization.d13c&&run.evaluation?.normalization.d18o?{...detail.method,normalization:{d13c:run.evaluation.normalization.d13c,d18o:run.evaluation.normalization.d18o}}:detail.method}/>
      {!qualificationMode&&<LongTermCharts state={{...props.state,active_method:detail.method,history:detail.history,runs:props.state.runs.filter(r=>r.method_id===detail.method_id)}} highlightRunIds={detail.run_ids}/>}
    </>:<Empty>{tr("The session is ready. Open Import to upload its Qtegra workbook.")}</Empty>}</>}
    {tab==="import"&&<><Panel title={tr("Add analytical workbooks")}><form className="station-import-form" onSubmit={importWorkbooks}><div className="metro-form-grid"><Field label={tr("Carbonate material")}><select value={carbonateMaterial} disabled={importing||props.busy} onChange={e=>{setCarbonateMaterial(e.target.value as "calcite"|"aragonite");setCarbonatePreapplied(false);}}><option value="calcite">{tr("Calcite")}</option><option value="aragonite">{tr("Aragonite")}</option></select></Field><Field label={tr("Homogeneous material")}><select value={materialId} onChange={e=>setMaterialId(e.target.value)}>{props.state.materials.filter(m=>detail.method?.config.anchor_ids.includes(m.id)||m.id===detail.method?.config.qc_id).map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select></Field></div><div className="metro-actions"><button type="button" className="metro-btn" disabled={props.busy||importing} onClick={()=>void props.act(`/results-sessions/${detail.id}/chart-settings`,{carbonate_material:carbonateMaterial,diagnostic_material_id:materialId||null},"PUT")}>{tr("Save material selection")}</button></div>{carbonateMaterial==="aragonite"&&detail.input_basis==="already_vpdb"&&<label className="metro-check"><input type="checkbox" checked={carbonatePreapplied} disabled={importing||props.busy} onChange={e=>setCarbonatePreapplied(e.target.checked)}/>{tr("Aragonite correction already applied in the export")}</label>}<div className="metro-form-grid"><Field label={tr("Qtegra results Excel")}><FileInput key={fileInputKey} accept=".xlsx,.xls" multiple required={!files.length} disabled={props.busy||importing} onChange={e=>setFiles(Array.from(e.target.files??[]))}/></Field><Field label={tr("Sample group for the selected workbooks")}><input required disabled={props.busy||importing} value={batch} onChange={e=>setBatch(e.target.value)} placeholder={tr("Core A, reference series or client batch")}/></Field></div>{files.length>0&&<ul>{files.map((file,index)=><li key={`${index}-${file.name}`}>{file.name}</li>)}</ul>}{importing&&<p role="status">{tr(`Importing workbook ${importIndex} of ${files.length}`)}</p>}<div className="metro-actions"><BusyButton busy={props.busy||importing}><Upload size={14}/>{tr("Import, evaluate and store QC")}</BusyButton></div></form></Panel><Panel title={tr("Analytical workbooks")}><div className="metro-table-wrap"><table><thead><tr><th>{tr("In session")}</th><th>{tr("Analytical workbook")}</th><th>{tr("Analyses")}</th><th>{tr("Within criteria")}</th><th>{tr("Review required")}</th><th>SD QC δ¹³C</th><th>SD QC δ¹⁸O</th><th/></tr></thead><tbody>{[...detail.runs,...detail.detached_runs??[]].map(workbook=>{
      const results=workbook.evaluation?.results??[],problems=results.filter(r=>r.excluded||r.issues?.some(issue=>!issue.startsWith("mass_ug"))).length;
      const included=detail.run_ids.includes(workbook.id);
      return <tr key={workbook.id}><td><input aria-label={`${tr("In session")}: ${workbook.label}`} type="checkbox" checked={included} disabled={props.busy} onChange={e=>void props.act(`/results-sessions/${detail.id}/workbooks/${workbook.id}?included=${e.target.checked}`,{})}/></td><td>{workbook.label}</td><td>{results.length}</td><td><span className="station-threshold-pass">{results.length-problems}</span></td><td><span className={problems?"station-threshold-review":""}>{problems}</span></td>{isotopes.map(iso=>{const omitted=new Set(analysis?.outliers.flags.filter(f=>f.isotope===iso).map(f=>f.measurement_id));const values=results.filter(r=>r.role==="qc"&&!r.excluded&&!omitted.has(r.id)).map(r=>r.isotopes?.[iso]?.value).filter((v):v is number=>v!=null&&Number.isFinite(v));const mean=values.reduce((a,b)=>a+b,0)/values.length;const sd=values.length>1?Math.sqrt(values.reduce((n,v)=>n+(v-mean)**2,0)/(values.length-1)):null;return <td key={iso} className="num">{sd?.toFixed(4)??"—"}<small className="station-cell-subtitle">n={values.length}</small></td>;})}<td>{included&&<button className="metro-btn" onClick={()=>setRun(workbook.id)}>{tr("Review")}</button>}</td></tr>;
    })}</tbody></table></div></Panel>{run&&<RunReview {...props} key={`${run.id}-${run.latest_evaluation_id}-${run.revision}`} run={run} initialTab="measurements" fixedView/>}</>}
    {tab==="sources"&&<Panel title={tr("Raw source archive")}><p className="metro-muted">{tr("Original acquisition bytes are stored with SHA-256 hashes. Duplicate and alternative exports remain available but do not add repeated QC observations. Processed reports are excluded.")}</p><div className="metro-table-wrap"><table><thead><tr><th>{tr("Original export")}</th><th>{tr("Import decision")}</th><th>SHA-256</th><th/></tr></thead><tbody>{detail.sources?.map(a=><tr key={a.id}><td>{a.relative_path}<small className="station-cell-subtitle">{(a.size/1024).toFixed(1)} KB</small></td><td>{tr(a.disposition)}</td><td><code title={a.sha256}>{a.sha256.slice(0,14)}…</code></td><td><a className="metro-btn" href={`${METROLOGY_API}/session-sources/${a.id}`}><Download size={14}/>{tr("Download")}</a></td></tr>)}</tbody></table></div></Panel>}
    {tab==="method"&&(qualificationMode?<Methods {...props} methodId={detail.method_id} sessionScoped/>:<><SessionMethodSummary detail={detail} state={props.state}/><AnchorPair method={detail.method}/></>)}
    {analysisError&&<p role="alert" className="metro-note error">{tr(analysisError)}</p>}
    {tab==="uncertainty"&&<SessionUncertainty detail={detail} group={group} analysis={analysis}/>}
    {(toolsActive||visitedTools.length>0)&&<MetrologyConsultation.Provider value={true}>
      {toolsActive&&(toolsError?<div role="alert" className="metro-note error">{tr(toolsError)} <button className="metro-btn" onClick={()=>setToolsAttempt(attempt=>attempt+1)}>{tr("Retry plotting workspace")}</button></div>:!run?<Empty>{tr("Import a workbook to open the plotting tools.")}</Empty>:!toolId?<p role="status">{tr("Preparing original workbook plots. Large sessions may take a few minutes.")}</p>:null)}
      {visitedTools.map(toolTab=>{const bridge=bridges[toolTab==="diagnostics"?diagnosticScope:"all"];if(!bridge)return null;return <div className="station-existing-tools" hidden={tab!==toolTab} key={toolTab}><MetrologySymbolSize.Provider value={toolTab==="diagnostics"?null:symbolSize}><MetrologyToolActive.Provider value={tab===toolTab}><MetrologyStationFilters.Provider value={filters}><MetrologyToolsSession.Provider value={bridge.session_id}>
        {toolTab==="diagnostics"?<MetrologyChartAppearance.Provider value={diagnosticsAppearance}><DiagnosticsTools key={bridge.session_id}/></MetrologyChartAppearance.Provider>:toolTab==="calibration"&&analysis?<MetrologyChartWorkspace.Provider value={calibrationWorkspace}><CalibrationTools/></MetrologyChartWorkspace.Provider>:toolTab==="processing"?<MetrologyChartHeight.Provider value={340}><section className="station-processing-main"><h2>{tr("Original IRMS processing tools / imported observations")}</h2><MetrologyProcessingResults.Provider value={processingResults}><ProcessingTools/></MetrologyProcessingResults.Provider></section></MetrologyChartHeight.Provider>:null}
      {toolTab==="processing"&&analysis&&<SessionProcessing analysis={analysis} group={group} mapping={mapping} tableOnly/>}
      </MetrologyToolsSession.Provider></MetrologyStationFilters.Provider></MetrologyToolActive.Provider></MetrologySymbolSize.Provider></div>;})}
    </MetrologyConsultation.Provider>}
    {tab==="export"&&<><TraceableExport {...props} detail={detail} group={group}/>
      <details><summary>{tr("Organize sample groups")}</summary><Panel><form onSubmit={async e=>{e.preventDefault();const changed=Object.fromEntries(Object.entries(groups).filter(([id,value])=>value!==detail.groups[id]));if(Object.keys(changed).length)await props.act(`/results-sessions/${detail.id}/groups`,{groups:changed});}}><div className="metro-table-wrap"><table><thead><tr><th>{tr("Sample")}</th><th>{tr("Sample group")}</th><th>{tr("Decision")}</th></tr></thead><tbody>{detail.runs.flatMap(source=>(source.evaluation?.results??[]).filter(r=>r.role==="unknown").map(r=>({...r,locked:source.status==="released"}))).map(r=><tr key={r.id}><td>{r.label}</td><td><input className="metro-control" aria-label={`${tr("Group for")} ${r.label}`} value={groups[r.id]??"Main batch"} disabled={r.locked} onChange={e=>setGroups(p=>({...p,[r.id]:e.target.value}))}/></td><td><Status value={r.issues?.length?"blocked":r.locked?"released":"review"}/></td></tr>)}</tbody></table></div><div className="metro-actions"><BusyButton busy={props.busy}>{tr("Save sample groups")}</BusyButton></div></form></Panel></details>
<Inspect title={tr("Session provenance")} value={{session_id:detail.id,method_id:detail.method_id,qualification_id:detail.qualification_id,input_basis:detail.input_basis,processing_evidence:detail.processing_evidence}}/></>}
  </div></MetrologyEvidenceBridge.Provider>;
}
