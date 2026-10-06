"use client";

import { useTranslation } from "@/components/layout/language-provider";
import { FileInput } from "@/components/ui/file-input";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { SessionDetail } from "./results-sessions";
import { QualificationStation } from "./results-station";
import { Methods } from "./methods";
import { METROLOGY_API, human, metroRequest, type Method, type ResultsSessionDetail, type Qualification } from "@/lib/metrology";
import { BusyButton, Empty, Field, Inspect, Num, Panel, Status, type WorkspaceProps } from "./shared";

export function Qualifications(props: WorkspaceProps & {initialQualificationId?:string}) {
  const tr = useTranslation();
  const router = useRouter();
  const [selected, setSelected] = useState(props.initialQualificationId ?? props.state.qualifications.at(-1)?.id ?? "");
  useEffect(()=>{if(props.initialQualificationId)setSelected(props.initialQualificationId);},[props.initialQualificationId]);
  const [methodId, setMethod] = useState(props.state.methods.at(-1)?.id ?? "");
  const [event, setEvent] = useState("");
  const [masses, setMasses] = useState("60, 100, 140");
  const [replicates, setReplicates] = useState<number | null>(5);
  const [error, setError] = useState("");
  const q = props.state.qualifications.find(q => q.id === selected) ?? props.state.qualifications.at(-1);
  const method = props.state.methods.find(m => m.id === methodId);
  const owner=props.state.results_sessions?.find(s=>s.qualification_id===q?.id&&s.context==="qualification");
  const [detail,setDetail]=useState<ResultsSessionDetail|null>(null);
  useEffect(()=>{if(!owner){setDetail(null);return;}const controller=new AbortController();metroRequest<ResultsSessionDetail>(`/results-sessions/${owner.id}`,{signal:controller.signal}).then(setDetail).catch(e=>{if(!controller.signal.aborted)setError(e.message);});return ()=>controller.abort();},[owner?.id,props.state]);
  return <div className="metro-stack">{q&&<div className="station-panel-toolbar"><Field label={tr("Qualification session")}><select value={q.id} onChange={e=>setSelected(e.target.value)}>{[...props.state.qualifications].reverse().map(item=><option key={item.id} value={item.id}>v{props.state.methods.find(m=>m.id===item.method_id)?.version} · {item.id.slice(0,8)} · {tr(item.status)} · {item.approval?.at.slice(0,10)??tr("Under review")}</option>)}</select></Field><Status value={q.status}/></div>}
  <details className="station-create-qualification" open={!q}><summary>{tr("Start a qualification session")}</summary><Panel><form className="metro-form-grid" onSubmit={async e => {
    e.preventDefault(); setError("");
    const levels = masses.split(",").map(v => Number(v.trim()));
    if (levels.some(v => !Number.isFinite(v) || v <= 0) || !replicates) { setError("Enter positive mass levels and a replicate count"); return; }
    let chosenMethod = method;
    if(chosenMethod?.status!=="draft"&&!event&&chosenMethod){const draft=await props.act("/methods",{config:chosenMethod.config}) as Method|undefined;if(!draft)return;chosenMethod=draft;setMethod(draft.id);}
    if(!chosenMethod)return;
    const record = await props.act("/qualifications", { method_id: chosenMethod.id, trigger: event ? "event" : "periodic", intervention_id: event || null, tests: [], carousel: [ ...(method?.config.anchor_ids ?? []), ...(method?.config.qc_id ? [method.config.qc_id] : []) ].flatMap(material_id => levels.map(mass_ug => ({ material_id, mass_ug, replicates }))) }) as Qualification | undefined;
    if (record) {
      setSelected(record.id);
      const session = await props.act("/results-sessions", {name:`${method?.config.name ?? "Qualification"} · ${new Date().toISOString().slice(0,10)}`,client:method?.config.laboratory || "Laboratory",project:"Metrological qualification",context:"qualification",method_id:chosenMethod.id,qualification_id:record.id,input_basis:"instrument_delta",processing_evidence:"Qualification observations before residual correction and dual-point normalization; Qtegra drift correction disabled."}) as {id:string}|undefined;
      if(session)router.push(`/metrology/qualification?qualification=${record.id}`);
    }
  }}>
    <Field label={tr("Method")}><select value={methodId} onChange={e => setMethod(e.target.value)}>{props.state.methods.filter(m => ["draft", "active"].includes(m.status)).map(m => <option value={m.id} key={m.id}>{tr("v")}{m.version} · {tr(m.status)} · {tr(m.config.name)}</option>)}</select></Field>
    <Field label={tr("Trigger")}><select value={event} onChange={e => setEvent(e.target.value)}><option value="">{tr("Periodic qualification")}</option>{props.state.interventions.filter(i => i.status === "open").map(i => <option value={i.id} key={i.id}>{tr("After ")}{tr(human(i.kind))} · {tr(i.created_at.slice(0,10))}</option>)}</select></Field>
    <Field label={tr("Carousel mass levels / µg, comma separated")}><input value={masses} onChange={e => setMasses(e.target.value)} required /></Field><Field label={tr("Replicates per material and mass")}><Num value={replicates} min={1} onChange={setReplicates} required /></Field>
    <div className="wide metro-note">{tr("The plan includes both configured anchors and independent QC at each mass. Acceptance matching uses ±0.5 µg. Instrument checks and residual-effect reviews remain explicit operator decisions.")}</div>
    {tr(error && <p role="alert" className="wide metro-note error">{tr(error)}</p>)}<div className="wide metro-actions"><BusyButton busy={props.busy}>{tr("Create qualification session")}</BusyButton></div>
  </form></Panel></details>
  {error&&<p role="alert">{tr(error)}</p>}
  {q ? owner ? detail?.id===owner.id ? <SessionDetail {...props} key={detail.id} detail={detail} navigate={section=>router.push(`/metrology/${section}`)} openSession={id=>router.push(`/metrology/results?session=${id}`)} qualificationReview={<QualificationReview {...props} q={q}/>}/> : <p role="status">{tr("Opening saved results session…")}</p> : <><Panel title={tr("Session summary")}><p className="metro-muted">{tr("This qualification has no linked results session yet.")}</p><button className="metro-btn" disabled={props.busy} onClick={async()=>{await props.act("/results-sessions",{name:`Qualification ${q.id.slice(0,8)}`,client:"Laboratory",context:"qualification",method_id:q.method_id,qualification_id:q.id,input_basis:"instrument_delta",processing_evidence:"Qualification carousel before residual correction and dual-point normalization."});}}>{tr("Create linked qualification session")}</button></Panel><Methods {...props} methodId={q.method_id} sessionScoped/><QualificationReview {...props} q={q}/></> : <Empty>{tr("Create a qualification session to define its method and import a carousel.")}</Empty>}
  </div>;
}

function QualificationReview({ q, ...props }: WorkspaceProps & { q: Qualification }) {
  const tr = useTranslation();
  const [test, setTest] = useState(q.required_tests[0] ?? "");
  const [testResult, setTestResult] = useState("not_evaluated");
  const [value, setValue] = useState<number | null>(null);
  const [unit, setUnit] = useState("");
  const [criterion, setCriterion] = useState("");
  const [effect, setEffect] = useState("mass_intensity");
  const [effectDecision, setEffectDecision] = useState("investigate");
  const [evidence, setEvidence] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [interpretation, setInterpretation] = useState(props.state.demo ? "DEMO: independent diagnostic evidence reviewed against the configured acceptance criterion; supplementary to the stored training and validation studies." : "");
  const [evaluationId, setEvaluation] = useState(props.state.runs.filter(r => r.qualification_id === q.id).at(-1)?.latest_evaluation_id ?? "");
  const runs = props.state.runs.filter(r => r.qualification_id === q.id);
  const latest = Object.fromEntries(q.tests.map(t => [t.name, t]));
  useEffect(() => { const record = [...q.tests].reverse().find(t => t.name === test); setTestResult(record?.result ?? "not_evaluated"); setValue(record?.value ?? null); setUnit(record?.unit ?? ""); setCriterion(record?.criterion ?? ""); }, [test, q.tests]);
  useEffect(() => { const record = q.effects[effect]; setEffectDecision(record?.decision ?? "investigate"); setEvidence(record?.evidence ?? ""); }, [effect, q.effects]);
  const locked = q.status === "approved";
  const method = props.state.methods.find(m => m.id === q.method_id);
  const hasSession=props.state.results_sessions?.some(s=>s.context==="qualification"&&s.qualification_id===q.id);
  return <>{!hasSession&&<QualificationStation runId={runs.at(-1)?.id} state={props.state}/>}<Panel title={tr(`Qualification · ${q.id.slice(0,8)}`)}><div className="metro-actions" style={{ marginTop: 0 }}><Status value={q.status} /><span className="metro-muted">{tr("Method v")}{method?.version} · {tr(q.trigger)}</span></div><div className="metro-table-wrap" style={{ marginTop: 16 }}><table><thead><tr><th>{tr("Instrument check")}</th><th>{tr("Review")}</th><th>{tr("Recorded criterion")}</th></tr></thead><tbody>{q.required_tests.map(name => <tr key={name}><td>{tr(name)}</td><td><Status value={latest[name]?.result ?? "not_evaluated"} /></td><td>{tr(latest[name]?.criterion || "Record the acceptance criterion and evidence")}</td></tr>)}</tbody></table></div>
    {!locked && <form className="metro-form-grid" style={{ marginTop: 22 }} onSubmit={e => { e.preventDefault(); void props.act(`/qualifications/${q.id}/tests`, { name: test, result: testResult, value, unit, criterion }); }}>
      <Field label={tr("Instrument check")}><input value={test} list="metro-test-names" onChange={e => setTest(e.target.value)} required /><datalist id="metro-test-names">{[...new Set([...q.required_tests, ...props.state.test_catalog])].map(t => <option value={t} key={t} />)}</datalist></Field>
      <Field label={tr("Reviewed result")}><select value={testResult} onChange={e => setTestResult(e.target.value)}><option value="not_evaluated">{tr("Not evaluated")}</option><option value="pass">{tr("Pass")}</option><option value="fail">{tr("Fail")}</option></select></Field>
      <Field label={tr("Measured value, if applicable")}><Num value={value} onChange={setValue} /></Field><Field label={tr("Unit")}><input value={unit} onChange={e => setUnit(e.target.value)} /></Field><Field label={tr("Acceptance criterion and evidence reference")} wide><textarea value={criterion} minLength={3} required onChange={e => setCriterion(e.target.value)} /></Field><div className="wide metro-actions"><BusyButton busy={props.busy}>{tr("Record instrument check")}</BusyButton></div>
    </form>}
  </Panel>
  <div className="metro-grid"><Panel title={tr("Diagnostic files")}><p className="metro-muted">{tr("Attach the original Qtegra screenshots or diagnostic files. Pass/fail comes from the reviewed check.")}</p>{q.assets.map(a => <div className="metro-meter" key={a.id}><a className="metro-btn" href={`${METROLOGY_API}/assets/${a.id}`}>{a.filename}</a><p className="metro-muted">{tr(a.interpretation)}</p></div>)}{!locked && <form className="metro-stack" style={{ marginTop: 18 }} onSubmit={async e => { e.preventDefault(); if (!file) return; const data = new FormData(); data.append("file", file); data.append("actor", props.decision.actor); data.append("reason", props.decision.reason); data.append("interpretation", interpretation); await props.upload(`/qualifications/${q.id}/assets`, data); }}><Field label={tr("Evidence file")}><FileInput required onChange={e => setFile(e.target.files?.[0] ?? null)} /></Field><Field label={tr("Operator interpretation")}><textarea value={interpretation} required onChange={e => setInterpretation(e.target.value)} /></Field><BusyButton busy={props.busy}>{tr("Attach evidence")}</BusyButton></form>}</Panel>
  <Panel title={tr("Carousel plan")}><div className="metro-table-wrap"><table><thead><tr><th>{tr("Material")}</th><th>{tr("Mass / µg")}</th><th>{tr("Replicates")}</th></tr></thead><tbody>{q.carousel.map((s, i) => <tr key={i}><td>{tr(props.state.materials.find(m => m.id === s.material_id)?.name)}</td><td className="num">{s.mass_ug}</td><td className="num">{s.replicates}</td></tr>)}</tbody></table></div><p className="metro-muted" style={{ marginTop: 12 }}>{runs.length}{tr(" linked analytical run")}{tr(runs.length === 1 ? "" : "s")}{tr(". Select this session when importing its Qtegra export.")}</p></Panel></div>
  <Panel title={tr("Review residual effects")}><div className="metro-table-wrap"><table><thead><tr><th>{tr("Effect")}</th><th>{tr("Decision")}</th><th>{tr("Evidence")}</th></tr></thead><tbody>{["mass_intensity", "pressure_adjustment", "drift", "memory", "bias"].map(key => <tr key={key}><td>{tr(human(key))}</td><td><Status value={q.effects[key]?.decision ?? "not_evaluated"} /></td><td>{tr(q.effects[key]?.evidence || "Review the diagnostic analysis and experimental design")}</td></tr>)}</tbody></table></div>
    {!locked && <form className="metro-form-grid" style={{ marginTop: 20 }} onSubmit={e => { e.preventDefault(); void props.act(`/qualifications/${q.id}/effects`, { effect, decision: effectDecision, evidence }); }}>
      <Field label={tr("Effect")}><select value={effect} onChange={e => setEffect(e.target.value)}>{["mass_intensity", "pressure_adjustment", "drift", "memory", "bias"].map(x => <option key={x} value={x}>{tr(human(x))}</option>)}</select></Field>
      <Field label={tr("Scientific decision")}><select value={effectDecision} onChange={e => setEffectDecision(e.target.value)}><option value="investigate">{tr("Investigate before approval")}</option><option value="negligible">{tr("Negligible within validated conditions")}</option><option value="monitor">{tr("Monitor under the defined criteria")}</option><option value="restrict_range">{tr("Restrict the method range")}</option><option value="reject_correction">{tr("Reject a proposed correction")}</option><option value="approve_correction">{tr("Approve the configured linear correction")}</option></select></Field><Field label={tr("Evidence, practical magnitude and reproducibility")} wide><textarea value={evidence} minLength={3} required onChange={e => setEvidence(e.target.value)} /></Field><div className="wide metro-note">{tr("A fitted slope does not activate a correction. Approval requires a configured equation, its uncertainty, a validated range and attached independent validation evidence. Memory corrections require a separately defined measurement model and are not offered here.")}</div><div className="wide metro-actions"><BusyButton busy={props.busy}>{tr("Save effect review")}</BusyButton></div>
    </form>}
  </Panel>
  <Panel title={tr("Metrological approval")}><p className="metro-muted">{tr("Approval checks the current evaluation, independent QC, carousel, uncertainty evidence and all required reviews. The approved method becomes immutable.")}</p>{!locked && <form className="metro-stack" style={{ marginTop: 16 }} onSubmit={e => { e.preventDefault(); void props.act(`/methods/${q.method_id}/approve`, { qualification_id: q.id, evaluation_id: evaluationId }); }}><Field label={tr("Qualification evaluation")}><select value={evaluationId} onChange={e => setEvaluation(e.target.value)} required><option value="">{tr("Select a processed qualification run")}</option>{runs.filter(r => r.latest_evaluation_id).map(r => <option key={r.id} value={r.latest_evaluation_id!}>{r.label} · {tr(r.status)}</option>)}</select></Field><div className="metro-actions"><BusyButton busy={props.busy}>{tr(method?.status === "active" ? "Approve verification" : "Approve and activate method")}</BusyButton></div></form>}<div className="metro-actions"><button className="metro-btn" disabled={props.busy} onClick={() => void props.act("/reports", { kind: "qualification", target_id: q.id })}>{tr("Generate qualification PDF and dossier")}</button></div><Inspect title={tr("Full qualification record")} value={q} /></Panel>
  </>;
}
