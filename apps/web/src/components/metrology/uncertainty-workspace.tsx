"use client";

import { useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PlotlyChart } from "@/components/charts/plotly-chart";
import { useTranslation } from "@/components/layout/language-provider";
import { api } from "@/lib/api";
import { isotopeLabel, isotopes, type Isotope, type ResultsSessionDetail, type SessionAnalysis } from "@/lib/metrology";
import { buildUncertaintyFigure } from "@/lib/uncertainty-chart";
import { MetrologyEvidenceBridge } from "./consultation-context";
import { Empty, Field, Inspect, Panel } from "./shared";

const labels:Record<string,string>={precision:"Intermediate precision",normalization:"Dual-anchor normalization",secondary_correction:"Residual correction"};
const symbols:Record<string,string>={precision:"u_prec",normalization:"u_norm",secondary_correction:"u_corr"};
const number=(n:number|null|undefined)=>n==null?"—":n.toFixed(4);
type Component={name:string;u:number;rationale?:string};

function BudgetTable({components,iso}:{components:Component[];iso:Isotope}) {
  const tr=useTranslation();
  const variance=components.reduce((sum,c)=>sum+c.u*c.u,0);
  return <table className="station-budget-table"><thead><tr><th>{tr("Contribution")}</th><th>{tr("Symbol")}</th><th>u / ‰</th><th>{tr("Impact on variance")}</th></tr></thead><tbody>{components.map(c=>{
    const impact=variance>0?100*c.u*c.u/variance:0;
    return <tr key={c.name}><td title={c.rationale}>{tr(labels[c.name]??c.name)}</td><td><code>{symbols[c.name]??c.name}</code></td><td className="num">{number(c.u)}</td><td><div className="station-budget-impact"><span style={{width:`${impact}%`,background:iso==="d13c"?"#215ec5":"#167d87"}}/><b>{impact.toFixed(1)}%</b></div></td></tr>;
  })}</tbody></table>;
}

export function UncertaintyWorkspace({detail,group,analysis}:{detail:ResultsSessionDetail;group:string;analysis?:SessionAnalysis|null}) {
  const tr=useTranslation(),bridge=useContext(MetrologyEvidenceBridge);
  const [selectedId,setSelectedId]=useState("");
  const [popup,setPopup]=useState<{x:number;y:number;pinned:boolean}|null>(null);
  const popover=useRef<HTMLDivElement>(null);
  const hoverPoint=useRef<{x:number;y:number}|null>(null);
  const closeTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const clearCloseTimer=()=>{if(closeTimer.current)clearTimeout(closeTimer.current);};
  const closeHover=()=>{clearCloseTimer();closeTimer.current=setTimeout(()=>setPopup(current=>current?.pinned?current:null),200);};
  const workspace=useQuery({queryKey:["uncertainty-processing-workspace",bridge?.session_id],queryFn:({signal})=>api.getProcessingWorkspace(bridge!.session_id,undefined,signal),enabled:!!bridge,staleTime:Infinity});
  const rows=useMemo(()=>(analysis?.rows??[]).filter(r=>!group||r.role!=="unknown"||r.sample_group===group),[analysis,group]);
  const selected=rows.find(r=>r.id===selectedId)??rows.find(r=>r.role==="unknown")??rows[0];
  const byLabel=useMemo(()=>Object.fromEntries(rows.filter(r=>bridge?.row_mapping[r.id]).map(r=>[bridge!.row_mapping[r.id],r])),[rows,bridge]);
  const flags=useMemo(()=>[
    ...[...analysis?.outliers.flags??[],...analysis?.qc_review_flags??[]].flatMap(f=>bridge?.row_mapping[f.measurement_id]?[{row:bridge.row_mapping[f.measurement_id],isotope:f.isotope==="d13c"?"d13C":"d18O",hidden:true}]:[]),
    ...(analysis?.rows??[]).filter(r=>r.excluded||(!rows.includes(r))).flatMap(r=>bridge?.row_mapping[r.id]?["d13C","d18O"].map(isotope=>({row:bridge.row_mapping[r.id],isotope,hidden:true})):[])
  ],[analysis,bridge,rows]);
  const figures=useMemo(()=>isotopes.map((_,i)=>{
    const source=workspace.data?.overview_figures[i?"d18_summary":"d13_summary"];
    return source&&buildUncertaintyFigure(source,byLabel,tr("Final ± U"),flags);
  }),[workspace.data,byLabel,tr,flags]);
  useLayoutEffect(()=>{
    const element=popover.current;
    if(!element)return;
    if(!popup){element.hidePopover();return;}
    element.showPopover();
    const position=()=>{
      const {width,height}=element.getBoundingClientRect(),gap=12;
      const left=popup.x+gap+width<=window.innerWidth-gap?popup.x+gap:popup.x-gap-width;
      element.style.left=`${Math.max(gap,Math.min(left,window.innerWidth-width-gap))}px`;
      element.style.top=`${Math.max(gap,Math.min(popup.y-24,window.innerHeight-height-gap))}px`;
    };
    position();
    const observer=new ResizeObserver(position);observer.observe(element);
    window.addEventListener("resize",position);
    return ()=>{observer.disconnect();window.removeEventListener("resize",position);};
  },[popup,selected?.id]);
  useEffect(()=>{
    if(!popup)return;
    const dismiss=(event:KeyboardEvent)=>{if(event.key==="Escape")setPopup(null);};
    const outside=(event:Event)=>{if(event.target instanceof Node&&!popover.current?.contains(event.target))setPopup(null);};
    document.addEventListener("keydown",dismiss);
    document.addEventListener("pointerdown",outside);
    window.addEventListener("scroll",outside,true);
    return ()=>{document.removeEventListener("keydown",dismiss);document.removeEventListener("pointerdown",outside);window.removeEventListener("scroll",outside,true);};
  },[popup]);
  useEffect(()=>{setPopup(null);},[group,bridge?.session_id]);
  useEffect(()=>()=>{if(closeTimer.current)clearTimeout(closeTimer.current);},[]);
  const inspect=(custom:unknown,anchor:{x:number;y:number},pinned:boolean)=>{
    const label=Array.isArray(custom)?String(custom[0]):"";
    if(byLabel[label]&&(!popup?.pinned||pinned)){
      clearCloseTimer();setSelectedId(byLabel[label].id);setPopup({...anchor,pinned});
    }
  };
  if(analysis&&!rows.length)return <Empty>{tr("Evaluate imported data to view individual uncertainty budgets.")}</Empty>;
  return <div className="metro-stack">
    {!!Object.keys(analysis?.residual_previews??{}).length&&<p className="metro-muted">{tr("These budgets belong to the recorded method results. Residual preview coefficient uncertainties are shown in Residual corrections and are not added to these budgets.")}</p>}
    <div className="metro-stack station-uncertainty-charts" onPointerDownCapture={event=>{hoverPoint.current={x:event.clientX,y:event.clientY};}}>{isotopes.map((iso,i)=>{
      const figure=figures[i];
      return <Panel key={iso} title={`${tr("Summary")} ${isotopeLabel[iso]}`}>{figure?<PlotlyChart figure={figure} minHeight={320}
        onPointHover={({points,clientX,clientY})=>{hoverPoint.current={x:clientX,y:clientY};inspect(points[0]?.customdata,hoverPoint.current,false);}}
        onHoverEnd={closeHover}
        onPointClick={points=>{if(hoverPoint.current)inspect(points[0]?.customdata,hoverPoint.current,true);}}
      />:<p role="status">{workspace.error?.message??tr("Preparing original workbook plots. Large sessions may take a few minutes.")}</p>}</Panel>;
    })}</div>
    <div className="station-budget-toolbar"><Field label={tr("Individual result")}><select value={selected?.id??""} onChange={e=>setSelectedId(e.target.value)}>{rows.map(r=><option key={r.id} value={r.id}>{r.label} {r.comment} · {r.run_label}</option>)}</select></Field><button className="metro-btn" disabled={!selected} aria-haspopup="dialog" aria-expanded={!!popup} aria-controls="station-budget-popover" onClick={e=>{const rect=e.currentTarget.getBoundingClientRect();clearCloseTimer();setPopup({x:rect.left,y:rect.bottom,pinned:true});}}>{tr("Uncertainty budget")}</button><small>{tr("Click a sample to inspect its uncertainty budget.")}</small></div>
    <Panel title={tr("General uncertainty budget")}><p className="metro-muted">{tr("Component impact across retained sample results. Standard uncertainties are root mean square values; percentages use the mean component variances.")}</p><div className="metro-grid station-isotope-pair">{isotopes.map(iso=>{
      const population=rows.filter(r=>r.role==="unknown"&&!r.excluded&&r.isotopes?.[iso]?.budget);
      const names=[...new Set(population.flatMap(r=>r.isotopes![iso]!.budget!.components.map(c=>c.name)))];
      const components=names.map(name=>({name,u:Math.sqrt(population.reduce((sum,r)=>sum+(r.isotopes![iso]!.budget!.components.find(c=>c.name===name)?.u??0)**2,0)/population.length)}));
      return <section key={iso}><h3>{isotopeLabel[iso]} · n={population.length}</h3>{population.length?<BudgetTable components={components} iso={iso}/>:<Empty>{tr("No complete uncertainty budget. Review this analysis in Import.")}</Empty>}</section>;
    })}</div></Panel>
    <div ref={popover} id="station-budget-popover" popover="manual" role="dialog" className="station-budget-popover" aria-labelledby="station-budget-title" onMouseEnter={clearCloseTimer} onMouseLeave={closeHover} onFocus={()=>{clearCloseTimer();setPopup(current=>current?{...current,pinned:true}:null);}}><div className="station-panel-toolbar"><h2 id="station-budget-title">{selected?.label} {selected?.comment} · {tr("Uncertainty budget")}</h2><button className="metro-btn" onClick={()=>setPopup(null)}>{tr("Close")}</button></div><div className="metro-stack">{isotopes.map(iso=>{
      const result=selected?.isotopes?.[iso],budget=result?.budget;
      return <section key={iso} className="station-budget-isotope"><h3>{isotopeLabel[iso]}</h3>{budget?<><div className="station-result-value"><b>{number(result?.value)} ± {number(budget.expanded_uncertainty)} ‰</b><small>VPDB · k = {budget.k}</small></div><div className="metro-table-wrap"><BudgetTable components={budget.components} iso={iso}/></div><p>u_c = {number(budget.u_combined)} ‰ · U = k × u_c</p><Inspect title={tr("Recorded calculation and component evidence")} value={result}/></>:<Empty>{tr("No complete uncertainty budget. Review this analysis in Import.")}</Empty>}</section>;
    })}</div></div>
  </div>;
}
