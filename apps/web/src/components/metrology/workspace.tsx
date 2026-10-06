"use client";

import { useTranslation } from "@/components/layout/language-provider";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Activity, Archive, BookOpen, CheckSquare, ClipboardList, FileText, FlaskConical, Gauge, History, RefreshCw, Wrench } from "lucide-react";
import { METROLOGY_API, fmt, human, isotopeLabel, isotopes, metroRequest, type Decision, type State } from "@/lib/metrology";
import { Materials } from "./methods";
import { Qualifications } from "./qualifications";
import { ResultsSessions } from "./results-sessions";
import { MetrologyOverview } from "./overview-dashboard";
import { LongTermCharts } from "./results-station";
import { BusyButton, Empty, Field, Inspect, Panel, Scatter, Status, type WorkspaceProps } from "./shared";

const sections = [
  { id: "overview", title: "Overview", label: "Overview", icon: Gauge, subtitle: "The current method state, qualification evidence and analytical work awaiting review." },
  { id: "qualification", title: "Instrument qualification", label: "Qualification", icon: CheckSquare, subtitle: "Instrument checks, a designed carbonate carousel and documented metrological decisions." },
  { id: "results", title: "Results Station", label: "Results Station", icon: Activity, subtitle: "Client sessions, their applied qualification, diagnostic plots and traceable results." },
  { id: "materials", title: "Reference-material library", label: "Reference materials", icon: FlaskConical, subtitle: "Certificate values, uncertainty interpretation and material lots, with permanent revision history." },
  { id: "history", title: "Historical QA/QC", label: "QC history", icon: History, subtitle: "Individual observations, control signals and reviewed periods for intermediate precision." },
  { id: "interventions", title: "Interventions and verification", label: "Interventions", icon: Wrench, subtitle: "Record instrument changes and identify the checks needed to return to a qualified state." },
  { id: "reports", title: "Reports and certificates", label: "Reports", icon: FileText, subtitle: "Stored PDFs and their calculation snapshots, preserved exactly as generated." },
  { id: "audit", title: "Scientific audit trail", label: "Audit trail", icon: Archive, subtitle: "Who changed or reviewed each record, why, and which data were affected." },
];

export function MetrologyWorkspace({ initialSection = "overview", initialSessionId, initialRunId, createSession, newContext, initialQualificationId }: {initialSection?:string;initialSessionId?:string;initialRunId?:string;createSession?:boolean;newContext?:"routine"|"qualification";initialQualificationId?:string}) {
  const router = useRouter();
  const tr = useTranslation();
  const [state, setState] = useState<State | null>(null);
  const [section, setSection] = useState(initialSection);
  const [savedSession, setSavedSession] = useState(initialSessionId);
  const [resultsVisited, setResultsVisited] = useState(initialSection === "results");
  useEffect(() => { if (initialSection === "results") { setResultsVisited(true); if(initialSessionId) {setSavedSession(initialSessionId);sessionStorage.setItem("metrology-open-session", initialSessionId);} } }, [initialSection, initialSessionId]);
  useEffect(() => { const id=sessionStorage.getItem("metrology-open-session");if(id&&!initialSessionId)setSavedSession(id); }, [initialSessionId]);
  useEffect(() => setSection(initialSection), [initialSection]);
  const [decision, setDecision] = useState<Decision>({ actor: "", reason: "" });
  useEffect(() => { try { const saved=sessionStorage.getItem("metrology-reviewer"); if(saved)setDecision(JSON.parse(saved)); } catch {} }, []);
  const changeDecision = (next:Decision) => {setDecision(next);try {sessionStorage.setItem("metrology-reviewer",JSON.stringify(next));} catch {}};
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const refresh = useCallback(async () => { const next = await metroRequest<State>("/state"); setState(next); if (next.demo) setDecision(p => p.actor ? p : { actor: "Demo scientist", reason: "Review synthetic demonstration evidence and evaluate workflow behavior." }); }, []);
  useEffect(() => { refresh().catch(e => setError(e.message)); }, [refresh]);
  const mutate = useCallback(async (path: string, body: unknown, method: string, isUpload = false): Promise<unknown> => {
    const review = { ...decision, ...(!isUpload && body && typeof body === "object" ? body : {}) };
    if (!review.actor.trim() || review.reason.trim().length < 3) { setError("Enter the operator/reviewer name and a justification above before saving a scientific record."); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await metroRequest<unknown>(path, { method, body: isUpload ? body as FormData : JSON.stringify({ ...decision, ...(body as object ?? {}) }) });
      await refresh();
      setNotice(result && typeof result === "object" && "duplicate" in result && result.duplicate ? "This workbook was already imported. The existing run has been opened." : path.startsWith("/reports") ? "Report saved. Its PDF and calculation snapshot are available in Reports." : "Record saved with its audit entry.");
      return result;
    } catch (e) { setError(e instanceof Error ? e.message : "The operation could not be completed"); }
    finally { setBusy(false); }
  },[decision,refresh]);
  const act = useCallback((path: string, body?: unknown, method = "POST") => mutate(path, body, method),[mutate]);
  const upload = useCallback((path: string, data: FormData) => mutate(path, data, "POST", true),[mutate]);
  const navigate = (id: string) => { setError(""); setNotice(""); router.push(id === "overview" ? "/metrology" : `/metrology/${id === "runs" || id === "methods" ? "results" : id}`); };
  const openSession = (id: string) => router.push(`/metrology/results?session=${id}`);
  const openRun = (id: string) => { const owner=state?.results_sessions?.find(s=>s.run_ids.includes(id)); router.push(`/metrology/results?${owner ? `session=${owner.id}&` : ""}run=${id}`); };
  const current = sections.find(s => s.id === section)!;
  const props: WorkspaceProps | null = state ? { state, decision, busy, act, upload } : null;
  return <div className="metro-shell"><aside className="metro-rail"><Link href="/metrology" className="metro-brand"><FlaskConical size={28} strokeWidth={1.5} /><span>{tr("Metrology Station")}</span></Link><small>{tr("Kiel IV · MAT253 Plus · Dual Inlet")}<br />{tr("Carbonate δ¹³C / δ¹⁸O · VPDB")}</small><nav aria-label={tr("Metrology navigation")}>{sections.map(({ id, label, icon: Icon }) => <Link key={id} href={id === "overview" ? "/metrology" : `/metrology/${id}`} aria-current={id === section ? "page" : undefined}><Icon size={16} strokeWidth={1.6} />{tr(label)}</Link>)}</nav><div className="metro-rail-footer"><p>{tr("Qtegra drift correction")}<br /><b>{tr("Disabled by laboratory policy")}</b></p><Link href="/metrology/results">{tr("Open Results Station ↗")}</Link><small>{tr("Local laboratory workspace")}<br />{tr("Metrology platform v1")}</small></div></aside>
    <main className={`metro-main${section === "results" || section === "qualification" ? " station-compact-header" : ""}`}><header className="metro-top"><div><h1>{tr(current.title)}</h1></div><button className="metro-btn" aria-label={tr("Refresh laboratory state")} disabled={busy} onClick={() => refresh().catch(e => setError(e.message))}><RefreshCw size={14} />{tr("Refresh")}</button></header>
      <div className="station-workspace-context">
      {state?.demo && <div className="station-demo-strip" role="note">{tr(state.operational?"Operational simulation: real raw exports, mock qualification and example certificates":"Demonstration workspace: synthetic data and example certificates")}</div>}
      <details className="station-review-bar"><summary>{tr("Review attribution")} · {decision.actor || tr("Enter reviewer name")}</summary><div className="metro-review-context"><Field label={tr("Operator / reviewer")}><input autoComplete="name" value={decision.actor} onChange={e => changeDecision({ ...decision, actor: e.target.value })} placeholder={tr("Your name")} /></Field><Field label={tr("Justification for the next saved action")}><input value={decision.reason} onChange={e => changeDecision({ ...decision, reason: e.target.value })} placeholder={tr("Evidence reviewed or reason for this change")} /></Field><small>{tr("Recorded with each scientific change. The name is a local attribution, not an authenticated electronic signature.")}</small></div></details>
      </div>
      {tr(error && <div className="metro-note error metro-flash" role="alert" style={{ marginBottom: 18 }}>{tr(error)}<button className="metro-btn" style={{ marginLeft: 12 }} onClick={() => setError("")}>{tr("Dismiss")}</button></div>)}{tr(notice && <div className="metro-note metro-flash" role="status" style={{ marginBottom: 18 }}>{tr(notice)}</div>)}
      {!props ? <Panel><p role="status">{tr(error ? "The backend could not be reached. Start the metrology launcher, then refresh." : "Loading laboratory state…")}</p></Panel> : <>
        {section === "overview" && <MetrologyOverview {...props} navigate={navigate} openRun={openRun} openSession={openSession} />}
        {section === "materials" && <Materials {...props} />}
        {section === "qualification" && <Qualifications {...props} initialQualificationId={initialQualificationId} />}{resultsVisited && <div hidden={section !== "results"}><ResultsSessions {...props} sessionId={initialSessionId ?? savedSession} runId={initialRunId} create={createSession} newContext={newContext} openSession={openSession} navigate={id=>{if(id==="results"){setSavedSession(undefined);sessionStorage.removeItem("metrology-open-session");}navigate(id);}} /></div>}
        {section === "history" && <HistoryPanel {...props} />}{section === "interventions" && <Interventions {...props} />}
        {section === "reports" && <Reports {...props} />}{section === "audit" && <Audit {...props} />}
      </>}
    </main></div>;
}

function HistoryPanel(props: WorkspaceProps) {
  const tr = useTranslation();
  const [groupId, setGroup] = useState(props.state.history.find(h => h.method_id === props.state.active_method?.id)?.key ?? "");
  const [method, setMethod] = useState(props.state.active_method?.id ?? "");
  const [name, setName] = useState(props.state.demo ? "DEMO reviewed recent QC period" : "");
  const [chosen, setChosen] = useState<string[]>(props.state.demo ? props.state.runs.filter(r => r.method_id === props.state.active_method?.id && r.context === "routine" && ["released", "awaiting_review"].includes(r.status)).map(r => r.latest_evaluation_id!).filter(Boolean) : []);
  const group = props.state.history.find(h => h.key === groupId) ?? props.state.history[0];
  return <div className="metro-stack"><LongTermCharts state={props.state} /><Panel title={tr("QC control charts")}>{group ? <><Field label={tr("Homogeneous population")}><select value={group.key} onChange={e => setGroup(e.target.value)}>{props.state.history.map(h => <option key={h.key} value={h.key}>{h.material.name}{tr(" · v")}{h.method_version}{tr(" · lot ")}{tr(h.material.lot || "unset")}{tr(" · period ")}{tr(h.period_key.slice(0,8))}</option>)}</select></Field><div className="metro-grid" style={{ marginTop: 22 }}>{isotopes.map(iso => { const s = group.isotopes[iso]; return <section key={iso}><h3>{tr(isotopeLabel[iso])} · {s.n}{tr(" observations")}</h3><Status value={s.status} /><Scatter points={s.points.map((p,i) => ({ x: i + 1, y: p.value, label: `${p.at}: ${p.value.toFixed(4)}‰` }))} limits={s.limits} target={s.target} mean={s.mean} sd={s.sd} xLabel={tr("QC observation in acquisition order")} yLabel={tr(`${isotopeLabel[iso]} / ‰ VPDB`)} connect /><p className="metro-muted">{tr("Mean ")}{tr(fmt(s.mean))}{tr("‰ · individual-observation SD ")}{tr(fmt(s.sd))}‰ · {group.run_count}{tr(" runs")}</p><Inspect title={tr("Control signals and individual QC observations")} value={s} /></section>; })}</div><div className="metro-note" style={{ marginTop: 18 }}>{tr("Limits use the frozen precision baseline, not the same observations being tested. Signals include a point outside 3 SD, eight points on one side, and six increasing or decreasing points. Failed observations remain visible. A reviewed homogeneous period is required before using historical SD in a method.")}</div></> : <Empty>{tr("No normalized independent QC observations yet. Raw QC diagnostics are available inside imported runs.")}</Empty>}</Panel>
    <Panel title={tr("Review a period for intermediate precision")}><form className="metro-stack" onSubmit={e => { e.preventDefault(); void props.act("/periods", { name, method_id: method, evaluation_ids: chosen }); }}><div className="metro-form-grid"><Field label={tr("Period name")}><input value={name} onChange={e => setName(e.target.value)} required /></Field><Field label={tr("Method version")}><select required value={method} onChange={e => { setMethod(e.target.value); setChosen([]); }}><option value="">{tr("Select method")}</option>{props.state.methods.filter(m => m.status !== "draft").map(m => <option key={m.id} value={m.id}>{tr("v")}{m.version} · {tr(m.config.name)}</option>)}</select></Field></div><p className="metro-muted">{tr("Select current evaluations from at least two acquisition dates within one instrument/intervention state. Failed or out-of-control observations cannot establish a controlled precision period.")}</p>{props.state.runs.filter(r => r.method_id === method && r.context === "routine" && r.latest_evaluation_id).map(r => <label className="metro-check" key={r.id}><input type="checkbox" checked={chosen.includes(r.latest_evaluation_id!)} onChange={e => setChosen(p => e.target.checked ? [...p,r.latest_evaluation_id!] : p.filter(v => v !== r.latest_evaluation_id))} />{r.label} · {tr(r.acquired_date)} · {tr(r.status)}</label>)}<div className="metro-actions"><BusyButton busy={props.busy}>{tr("Approve homogeneous precision period")}</BusyButton></div></form>{props.state.periods.map(p => <Inspect key={p.id} title={p.name} value={p} />)}</Panel>
  </div>;
}

function Interventions(props: WorkspaceProps) {
  const tr = useTranslation();
  const [kind, setKind] = useState("source_opening");
  const [instrument, setInstrument] = useState(props.state.active_method?.config.instrument ?? "Kiel IV + MAT253 Plus + Dual Inlet");
  return <div className="metro-stack"><Panel title={tr("Record an intervention")}><form className="metro-stack" onSubmit={e => { e.preventDefault(); void props.act("/interventions", { kind, instrument }); }}><div className="metro-form-grid"><Field label={tr("Intervention type")}><select value={kind} onChange={e => setKind(e.target.value)}>{Object.keys(props.state.event_tests).map(k => <option key={k} value={k}>{tr(human(k))}</option>)}</select></Field><Field label={tr("Instrument")}><input value={instrument} onChange={e => setInstrument(e.target.value)} required /></Field></div><div className="metro-note"><b>{tr("Proposed verification tests")}</b><p>{tr(props.state.event_tests[kind]?.join(", "))}</p></div><p className="metro-muted">{tr("Recording an intervention blocks routine release for this instrument until its targeted verification is approved. Create the linked event session under Qualification.")}</p><div className="metro-actions"><BusyButton busy={props.busy}>{tr("Record intervention")}</BusyButton></div></form></Panel><Panel title={tr("Intervention register")}>{props.state.interventions.length ? <div className="metro-table-wrap"><table><thead><tr><th>{tr("Date")}</th><th>{tr("Event")}</th><th>{tr("Reason")}</th><th>{tr("Status")}</th></tr></thead><tbody>{props.state.interventions.map(i => <tr key={i.id}><td>{tr(i.created_at.slice(0,10))}</td><td>{tr(human(i.kind))}</td><td>{tr(i.reason)}</td><td><Status value={i.status} /></td></tr>)}</tbody></table></div> : <Empty>{tr("No interventions recorded.")}</Empty>}</Panel></div>;
}

function Reports(props: WorkspaceProps) {
  const tr = useTranslation();
  return <Panel title={tr("Stored reports")}><p className="metro-muted">{tr("Generate qualification dossiers from their session, client certificates from released runs, and historical reports here. Every report includes a downloadable source snapshot.")}</p><div className="metro-actions"><button className="metro-btn" disabled={props.busy} onClick={() => void props.act("/reports", { kind: "history" })}>{tr("Generate historical performance PDF")}</button></div>{props.state.reports.length ? <div className="metro-table-wrap" style={{ marginTop: 20 }}><table><thead><tr><th>{tr("Report")}</th><th>{tr("Generated")}</th><th>{tr("PDF")}</th><th>{tr("Calculation snapshot")}</th></tr></thead><tbody>{[...props.state.reports].reverse().map(r => <tr key={r.id}><td>{tr(human(r.kind))} · {tr(r.id.slice(0,8))}</td><td>{tr(r.created_at.slice(0,19).replace("T", " "))}{tr(" UTC")}</td><td><div className="metro-actions"><a className="metro-btn" href={`${METROLOGY_API}/reports/${r.id}/pdf`}>{tr("Download PDF")}</a><button className="metro-btn" disabled={props.busy} onClick={() => void props.act(`/reports/${r.id}/reissue`)}>{tr("Reissue PDF")}</button></div></td><td><a className="metro-btn" href={`${METROLOGY_API}/reports/${r.id}/json`}>{tr("Download JSON")}</a></td></tr>)}</tbody></table></div> : <Empty>{tr("No reports generated yet.")}</Empty>}</Panel>;
}

function Audit(props: WorkspaceProps) {
  const tr = useTranslation();
  return <Panel title={tr("Recorded scientific actions")}><p className="metro-muted">{tr("Append-only records include previous and resulting values. Each entry is linked to the previous entry by its SHA-256 hash.")}</p><div className="metro-table-wrap" style={{ marginTop: 18 }}><table><thead><tr><th>{tr("Time / UTC")}</th><th>{tr("Action")}</th><th>{tr("Operator")}</th><th>{tr("Justification and changes")}</th></tr></thead><tbody>{props.state.audit.map(e => <tr key={e.id}><td>{tr(e.at.slice(0,19).replace("T", " "))}</td><td>{tr(human(e.action))}</td><td>{tr(e.actor)}</td><td>{tr(e.reason)}<Inspect title={tr("Before / after and audit hash")} value={e} /></td></tr>)}</tbody></table></div></Panel>;
}
