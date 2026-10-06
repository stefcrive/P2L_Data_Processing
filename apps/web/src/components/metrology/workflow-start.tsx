"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "@/components/layout/language-provider";
import { ArrowRight, FlaskConical, FolderPlus } from "lucide-react";
import { METROLOGY_API, metroRequest } from "@/lib/metrology";
import { type WorkspaceProps } from "./shared";

type Demo = { as_of: string; highlights: Record<string,string>; files: {filename:string;run_id:string;context:string}[] };

export function WorkflowStart(props: WorkspaceProps & { openRun: (id: string) => void; navigate: (section: string) => void }) {
  const tr = useTranslation();
  const [demo,setDemo]=useState<Demo|null>(null);
  const [demoError,setDemoError]=useState("");
  useEffect(()=>{if(props.state.demo) metroRequest<Demo>("/demo").then(setDemo).catch(e=>setDemoError(e.message));},[props.state.demo]);
  return <section className="metro-start" aria-label={tr("Start a workflow")}>
    <div className="metro-start-title"><span className="metro-eyebrow">{tr("Start here")}</span><h2>{tr("Choose your next analytical workflow")}</h2></div>
    <div className="station-workflow-cards">
      <button onClick={()=>props.navigate("qualification")}><FlaskConical size={22}/><span><b>{tr("Update metrological qualification")}</b><small>{tr("Create an assessment session, define its method and upload the 3-material carousel.")}</small></span><ArrowRight size={17}/></button>
      <button onClick={()=>props.navigate("results?new=1")}><FolderPlus size={22}/><span><b>{tr("Process a fresh analytical batch")}</b><small>{tr("Assign a client and sample group, retain the Qtegra normalization and upload results.")}</small></span><ArrowRight size={17}/></button>
    </div>
    {demo&&<div className="metro-demo-scenarios"><span className="metro-eyebrow">{tr("Explore populated examples · April–October 2026")}</span><div className="metro-actions">{[["review_run","Recent qualification"],["ready_run","Passing routine run"],["blocked_run","Out-of-range sample"],["released_run","Released results"],["historical_failure_run","Historical QC failure"]].map(([key,label])=><button className="metro-btn" key={key} onClick={()=>props.openRun(demo.highlights[key])}>{tr(label)}</button>)}<button className="metro-btn" onClick={()=>props.navigate("history")}>{tr("Six-month QC history")}</button></div><div className="metro-actions">{["review_run","ready_run","blocked_run"].map(key=>{const book=demo.files.find(f=>f.run_id===demo.highlights[key]);return book&&<a key={key} className="metro-muted" href={`${METROLOGY_API}/demo/files/${book.filename}`} download>{tr("Download")} {book.filename}</a>;})}</div></div>}
    {demoError&&<p role="alert">{tr(demoError)}</p>}
  </section>;
}
