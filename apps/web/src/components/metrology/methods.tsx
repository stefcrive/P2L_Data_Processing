"use client";

import { useTranslation } from "@/components/layout/language-provider";
import { useState } from "react";
import { MethodIdentity } from "./method-identity";
import { METROLOGY_API, isotopeLabel, isotopes, type LinearCorrection, type Material, type Method, type MethodConfig } from "@/lib/metrology";
import { BusyButton, Field, Inspect, Num, Panel, Status, type WorkspaceProps } from "./shared";

export function Methods(props: WorkspaceProps & {methodId?: string; sessionScoped?: boolean}) {
  const tr = useTranslation();
  const [selected, setSelected] = useState(props.methodId ?? props.state.methods.at(-1)?.id ?? "");
  const method = props.state.methods.find(m => m.id === selected) ?? props.state.methods.at(-1)!;
  return <div className="metro-stack">{!props.sessionScoped && <Panel><Field label={tr("Method version")}><select value={method.id} onChange={e => setSelected(e.target.value)}>{props.state.methods.map(m => <option value={m.id} key={m.id}>{tr(m.config.name)}{tr(" · v")}{m.version} · {tr(m.status)}</option>)}</select></Field></Panel>}<MethodEditor key={`${method.id}-${method.revision}`} {...props} method={method} /></div>;
}

function MethodEditor({ method, ...props }: WorkspaceProps & { method: Method; sessionScoped?: boolean }) {
  const tr = useTranslation();
  const [c, setC] = useState<MethodConfig>(() => structuredClone(method.config));
  const [advanced, setAdvanced] = useState(JSON.stringify({ additional_components: c.additional_components, normalization_covariance: c.normalization_covariance }, null, 2));
  const [error, setError] = useState("");
  const [massPreset, setMassPreset] = useState("60,140");
  const [precisionPreset, setPrecisionPreset] = useState("routine");
  const [ranges, setRanges] = useState<Record<string, string[]>>(() => Object.fromEntries(Object.entries(c.ranges).map(([key, v]) => [key, v ? [String(v.low), String(v.high)] : ["", ""]])));
  const patch = <K extends keyof MethodConfig>(key: K, value: MethodConfig[K]) => setC(p => ({ ...p, [key]: value }));
  const locked = method.status !== "draft";
  const materialOptions = props.state.materials.filter(m => !props.state.materials.some(n => n.revision_of === m.id) || c.anchor_ids.includes(m.id) || c.qc_id === m.id);
  async function save(newVersion = false) {
    setError("");
    try {
      const parsedRanges = Object.fromEntries(Object.entries(ranges).map(([key, [low, high]]) => {
        if ((low === "") !== (high === "")) throw new Error(`Enter both ends of the ${key} range`);
        return [key, low === "" ? null : { low: Number(low), high: Number(high) }];
      }));
      const payload = { ...c, ranges: parsedRanges, ...JSON.parse(advanced) };
      await props.act(newVersion ? "/methods" : `/methods/${method.id}`, { config: payload }, newVersion ? "POST" : "PUT");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save method"); }
  }
  return <form className="metro-stack station-method-form station-readable-method" onSubmit={e => { e.preventDefault(); void save(); }}>
    {!locked && <div className="station-panel-toolbar"><span className="metro-muted">{tr("Qualification method draft; approval freezes these parameters.")}</span><BusyButton busy={props.busy}>{tr("Save method draft")}</BusyButton></div>}
    <details><summary>{tr("Method identity and acquisition")}</summary>
    <Panel title={tr("Method definition")}><div className="metro-actions" style={{ marginTop: 0, marginBottom: 18 }}><Status value={method.status} /><span className="metro-muted">{tr("Version ")}{method.version}{tr(", revision ")}{method.revision}</span>{locked && !props.sessionScoped && <button type="button" className="metro-btn" disabled={props.busy} onClick={() => void save(true)}>{tr("Create next draft from this version")}</button>}{!props.sessionScoped && method.status === "validated" && <button type="button" className="metro-btn" disabled={props.busy} onClick={() => void props.act(`/methods/${method.id}/activate`)}>{tr("Activate validated method")}</button>}{!props.sessionScoped && ["active", "validated", "superseded"].includes(method.status) && <button type="button" className="metro-btn danger" disabled={props.busy} onClick={() => void props.act(`/methods/${method.id}/retire`)}>{tr("Retire method")}</button>}</div>
      <MethodIdentity config={c} patch={patch} locked={locked} />
      <fieldset disabled={locked} className="metro-form-grid station-anchor-fields">
        {[0, 1].map(i => <Field label={tr(`Carbonate anchor ${i + 1}`)} key={i}><select value={c.anchor_ids[i] ?? ""} onChange={e => { const ids = [...c.anchor_ids]; ids[i] = e.target.value; patch("anchor_ids", ids); }}><option value="">{tr("Select material lot")}</option>{materialOptions.map(m => <option key={m.id} value={m.id}>{m.name} · {tr(m.lot || "lot unset")} · {tr(m.verified ? "verified" : "unverified")}</option>)}</select></Field>)}
        <Field label={tr("Independent QC")}><select value={c.qc_id ?? ""} onChange={e => patch("qc_id", e.target.value || null)}><option value="">{tr("Select QC lot")}</option>{materialOptions.map(m => <option key={m.id} value={m.id}>{m.name} · {tr(m.lot || "lot unset")}</option>)}</select></Field>
        <Field label={tr("Periodic review interval / days")}><Num min={1} value={c.qualification_interval_days} onChange={n => patch("qualification_interval_days", n)} /></Field>
      </fieldset>
    </Panel>
    </details>
    <Panel title={tr("Validated ranges and QC criteria")}><fieldset disabled={locked} className="method-criteria-grid">
      {!locked && <div className="station-preset"><div className="station-panel-toolbar"><Field label={tr("Planned mass range")}><select value={massPreset} onChange={e=>setMassPreset(e.target.value)}><option value="40,80">40–80 µg</option><option value="60,140">60–140 µg</option><option value="100,200">100–200 µg</option></select></Field><Field label={tr("Precision requirement preset")}><select value={precisionPreset} onChange={e=>setPrecisionPreset(e.target.value)}><option value="routine">{tr("Routine laboratory criteria")}</option><option value="high">{tr("Higher precision · proposed limits")}</option><option value="screening">{tr("Screening · proposed limits")}</option></select></Field><button type="button" className="metro-btn" onClick={()=>{setRanges(p=>({...p,mass_ug:massPreset.split(",")}));const limits=precisionPreset==="high"?[.04,.06,.025,.035]:precisionPreset==="screening"?[.10,.15,.06,.08]:[.07,.10,.04,.05];patch("qc",{...c.qc,external_sd:{d13c:limits[0],d18o:limits[1]},internal_sd:{d13c:limits[2],d18o:limits[3]}});}}>{tr("Apply preset to draft")}</button></div><p className="metro-muted">{tr("Presets set proposed mass and SD limits only. Bias, intensity range and all validation evidence remain subject to qualification approval.")}</p></div>}
      <section className="method-section method-ranges" aria-labelledby="method-ranges-heading">
        <header className="method-section-heading"><h3 id="method-ranges-heading">{tr("Measurement ranges")}</h3><p>{tr("Set the lower and upper bounds covered by qualification.")}</p></header>
        <div className="method-range-heading" aria-hidden="true"><span>{tr("Parameter")}</span><span>{tr("Minimum")}</span><span>{tr("Maximum")}</span></div>
        {Object.entries(ranges).map(([key, value]) => {
          const [label, unit] = ({ mass_ug: ["Mass", "µg"], i44_v: ["Sample I44", "V"], d13c: ["δ¹³C", "‰ VPDB"], d18o: ["δ¹⁸O", "‰ VPDB"], pressure_mismatch_v: ["Pressure-adjustment mismatch", "V"] } as Record<string, string[]>)[key] ?? [key, ""];
          return <div className="method-range-row" key={key} role="group" aria-label={`${tr(label)} / ${unit}`}>
            <div className="method-parameter"><strong>{tr(label)}</strong><span>{unit}</span></div>
            {[0, 1].map(index => <label className="metro-field" key={index}><span className="method-cell-label">{tr(index === 0 ? "Minimum" : "Maximum")}</span><input aria-label={`${tr(label)} / ${unit}: ${tr(index === 0 ? "Minimum" : "Maximum")}`} type="number" step="any" value={value[index]} onChange={e => setRanges(p => ({ ...p, [key]: index === 0 ? [e.target.value, p[key][1]] : [p[key][0], e.target.value] }))} /></label>)}
          </div>;
        })}
      </section>
      <section className="method-section method-qc" aria-labelledby="method-qc-heading">
        <header className="method-section-heading"><h3 id="method-qc-heading">{tr("Precision and bias limits")}</h3><p>{tr("Compare the acceptance limits for both isotopes. All values are in ‰.")}</p></header>
        <div className="method-qc-heading" aria-hidden="true"><span>{tr("Acceptance criterion")}</span>{isotopes.map(iso => <strong key={iso}>{isotopeLabel[iso]} <small>‰</small></strong>)}</div>
        {(["internal_sd", "external_sd", "bias"] as const).map(key => {
          const label = ({ internal_sd: "Individual internal SD", external_sd: "Run-level QC SD", bias: "Absolute QC mean bias" })[key];
          return <div className="method-qc-row" key={key} role="group" aria-label={tr(label)}><div className="method-parameter"><strong>{tr(label)}</strong><span>{tr(key === "bias" ? "Separate bias acceptance criterion" : "SD must be below this limit")}</span></div>{isotopes.map(iso => <label className="metro-field" key={iso}><span className="method-cell-label">{isotopeLabel[iso]}<span className="sr-only"> · {tr(label)} / ‰</span></span><Num unit="‰" min={0} value={c.qc[key][iso]} onChange={n => patch("qc", { ...c.qc, [key]: { ...c.qc[key], [iso]: n } })} /></label>)}</div>;
        })}
        <p className="method-help">{tr("A required value left blank blocks qualification and release.")}</p>
      </section>
      <section className="method-section method-sequence" aria-labelledby="method-sequence-heading">
        <header className="method-section-heading"><h3 id="method-sequence-heading">{tr("QC placement in the sequence")}</h3></header>
        <div className="method-rule-grid"><Field label={tr("Maximum unknowns between QCs, including edges")}><Num min={1} value={c.qc.max_unknowns_between_qc} onChange={n => patch("qc", { ...c.qc, max_unknowns_between_qc: n ?? 4 })} /></Field><Field label={tr("Minimum independent QC aliquots per run")}><Num min={2} value={c.qc.minimum_qc} onChange={n => patch("qc", { ...c.qc, minimum_qc: n ?? 3 })} /></Field></div>
      </section>
    </fieldset></Panel>
    <details><summary>{tr("Secondary correction models")}</summary>
    <Panel title={tr("Secondary correction models")}><fieldset disabled={locked} className="metro-stack"><p className="metro-muted">{tr("An optional centered linear correction is applied before anchoring. Supply coefficients estimated independently of this qualification carousel and the routine QC. Approval requires supporting files and an explicit effect review. Choose one predictor per isotope; fitting a diagnostic slope never enables it.")}</p>{isotopes.map(iso => <CorrectionEditor key={iso} title={tr(isotopeLabel[iso])} value={c.corrections?.[iso] ?? null} onChange={value => patch("corrections", { d13c: c.corrections?.d13c ?? null, d18o: c.corrections?.d18o ?? null, [iso]: value })} assets={props.state.qualifications.filter(q => q.method_id === method.id).flatMap(q => q.assets)} />)}</fieldset></Panel>
    </details>
    <Panel title={tr("Correction verification criteria")}><fieldset disabled={locked} className="metro-form-grid method-correction-grid">
      <Field label={tr("Minimum paired independent QC aliquots")}><Num min={3} value={c.correction_validation?.minimum_qc??6} onChange={n=>patch("correction_validation",{minimum_sd_reduction_fraction:.05,practical_effect:{d13c:.01,d18o:.02},...c.correction_validation,minimum_qc:n??6})}/></Field>
      <Field label={tr("Minimum QC SD reduction / %")}><Num min={0} unit="%" value={100*(c.correction_validation?.minimum_sd_reduction_fraction??.05)} onChange={n=>patch("correction_validation",{minimum_qc:6,practical_effect:{d13c:.01,d18o:.02},...c.correction_validation,minimum_sd_reduction_fraction:(n??5)/100})}/></Field>
      {isotopes.map(iso=><Field key={iso} label={`${isotopeLabel[iso]} · ${tr("Practical residual effect / ‰")}`}><Num min={0} unit="‰" value={c.correction_validation?.practical_effect[iso]??(iso==="d13c"?.01:.02)} onChange={n=>patch("correction_validation",{minimum_qc:6,minimum_sd_reduction_fraction:.05,...c.correction_validation,practical_effect:{d13c:.01,d18o:.02,...c.correction_validation?.practical_effect,[iso]:n??.01}})}/></Field>)}
      <p className="metro-muted wide">{tr("These are configurable screening criteria. A positive lower paired-bootstrap bound, acceptable final QC, separate training/validation evidence and scientific approval are also required. Demonstration thresholds are illustrative.")}</p>
    </fieldset></Panel>
    <details><summary>{tr("Uncertainty model")}</summary>
    <Panel title={tr("Uncertainty model")}><fieldset disabled={locked} className="metro-form-grid">
      <Field label={tr("Precision evidence source")}><select value={c.precision_source} onChange={e => patch("precision_source", e.target.value as MethodConfig["precision_source"])}><option value="validation">{tr("Validated precision study")}</option><option value="historical_qc">{tr("Reviewed historical QC period")}</option></select></Field>
      <Field label={tr("Reviewed historical period")}><select value={c.precision_period ?? ""} onChange={e => { const period = props.state.periods.find(p => p.id === e.target.value); setC(p => ({ ...p, precision_period: e.target.value || null, ...(period ? { precision: { d13c: period.statistics.d13c.sd, d18o: period.statistics.d18o.sd } } : {}) })); }}><option value="">{tr("Not selected")}</option>{props.state.periods.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
      {isotopes.map(iso => <Field key={iso} label={tr(`${isotopeLabel[iso]} precision u / ‰, individual-observation SD`)}><Num min={0} value={c.precision[iso]} onChange={n => patch("precision", { ...c.precision, [iso]: n })} /></Field>)}
      <Field label={tr("Precision evidence / study reference")} wide><textarea value={c.precision_evidence} onChange={e => patch("precision_evidence", e.target.value)} /></Field>
      <Field label={tr("Independence and double-counting review")} wide><textarea value={c.independence_rationale} onChange={e => patch("independence_rationale", e.target.value)} placeholder={tr("Explain which variability the precision estimate already includes and why the remaining components are independent.")} /></Field>
      <Field label={tr("Coverage factor k")}><Num min={0} value={c.coverage_factor} onChange={n => patch("coverage_factor", n ?? 2)} /></Field>
      <Field label={tr("Coverage interpretation and rationale")}><textarea value={c.coverage_rationale} onChange={e => patch("coverage_rationale", e.target.value)} /></Field>
      <Field label={tr("Qtegra instrumental linearity")}><select value={c.qtegra_linearity} onChange={e => patch("qtegra_linearity", e.target.value as MethodConfig["qtegra_linearity"])}><option value="recorded">{tr("Recorded from export")}</option><option value="enabled">{tr("Enabled in Qtegra")}</option><option value="disabled">{tr("Disabled in Qtegra")}</option></select></Field>
      <div className="metro-note">{tr("Qtegra drift correction is disabled. Normalization already propagates assigned-value uncertainty. Instrumental linearity is not added again as an independent budget term.")}</div>
      <details className="wide"><summary>{tr("Advanced uncertainty components and anchor covariance")}</summary><Field label={tr("Model JSON")}><textarea rows={13} value={advanced} onChange={e => setAdvanced(e.target.value)} /></Field><p className="metro-muted">{tr("Covariance order: assigned anchor 1, assigned anchor 2, measured anchor mean 1, measured anchor mean 2. Each extra component needs a standard uncertainty, covered sources and rationale. Overlapping sources are rejected.")}</p></details>
      <Field label={tr("Required qualification tests, one per line")} wide><textarea value={c.required_tests.join("\n")} onChange={e => patch("required_tests", e.target.value.split("\n"))} /></Field>
    </fieldset><Inspect title={tr("Stored method and normalization")} value={method} /></Panel>
    </details>
    {error && <p role="alert" className="metro-note error">{tr(error)}</p>}
    {!locked && <div className="metro-actions method-save-actions"><BusyButton busy={props.busy}>{tr("Save method draft")}</BusyButton></div>}
  </form>;
}

function CorrectionEditor({ title, value, onChange, assets }: { title: string; value: LinearCorrection | null; onChange: (v: LinearCorrection | null) => void; assets: { id: string; filename: string }[] }) {
  const tr = useTranslation();
  const patch = (update: Partial<LinearCorrection>) => value && onChange({ ...value, ...update });
  return <section className="metro-stack"><h3>{tr(title)}</h3><Field label={tr("Application correction")}><select value={value?.predictor ?? "none"} onChange={e => onChange(e.target.value === "none" ? null : { name: "", slope: 0, u_slope: 0, center: 0, domain: { low: 0, high: 1 }, training_evidence: "", validation_evidence: "", independence_rationale: "", predictor_uncertainty_rationale: "", evidence_asset_ids: [], ...value, predictor: e.target.value as LinearCorrection["predictor"] })}><option value="none">{tr("None")}</option><option value="mass_ug">{tr("Mass / µg")}</option><option value="i44_v">{tr("Sample I44 / V")}</option><option value="pressure_mismatch_v">{tr("Pressure mismatch / V")}</option><option value="sequence">{tr("Sequence position, application drift model")}</option></select></Field>{value && <><div className="metro-note">{tr("z = x − c × (p − p₀). The same coefficient acts on both anchors and the sample. Its shared uncertainty is propagated once. Qtegra drift correction remains disabled.")}</div><div className="metro-form-grid"><Field label={tr("Model name")}><input required value={value.name} onChange={e => patch({ name: e.target.value })} /></Field><Field label={tr("Slope c / ‰ per predictor unit")}><Num value={value.slope} onChange={n => patch({ slope: n ?? 0 })} /></Field><Field label={tr("Standard uncertainty of slope u(c)")}><Num min={0} value={value.u_slope} onChange={n => patch({ u_slope: n ?? 0 })} /></Field><Field label={tr("Center p₀")}><Num value={value.center} onChange={n => patch({ center: n ?? 0 })} /></Field><Field label={tr("Validated predictor minimum")}><Num value={value.domain.low} onChange={n => patch({ domain: { ...value.domain, low: n ?? 0 } })} /></Field><Field label={tr("Validated predictor maximum")}><Num value={value.domain.high} onChange={n => patch({ domain: { ...value.domain, high: n ?? 0 } })} /></Field>{([['training_evidence', 'Training study and reproducibility'], ['validation_evidence', 'Independent validation and practical effect'], ['independence_rationale', 'Coefficient independence from anchors and precision'], ['predictor_uncertainty_rationale', 'Evidence that predictor uncertainty is negligible']] as const).map(([key, label]) => <Field key={key} label={tr(label)}><textarea required minLength={3} value={value[key]} onChange={e => patch({ [key]: e.target.value })} /></Field>)}</div><p className="metro-muted">{tr("Attach supporting files in Qualification, then select them here.")}</p>{assets.map(a => <label className="metro-check" key={a.id}><input type="checkbox" checked={value.evidence_asset_ids.includes(a.id)} onChange={e => patch({ evidence_asset_ids: e.target.checked ? [...value.evidence_asset_ids, a.id] : value.evidence_asset_ids.filter(id => id !== a.id) })} />{a.filename}</label>)}</>}</section>;
}

export function Materials(props: WorkspaceProps) {
  const tr = useTranslation();
  const latest = props.state.materials.filter(m => !props.state.materials.some(n => n.revision_of === m.id));
  const [id, setId] = useState(latest[0]?.id ?? "");
  const material = latest.find(m => m.id === id) ?? latest[0];
  return <div className="metro-stack station-readable-method"><Panel title={tr("Reference-material library")}>
    <p className="metro-muted">{tr("Issuer reference values and original PDFs. Confirm your material lot and uncertainty interpretation before qualification. Saving preserves the previous revision.")}</p>
    <Field label={tr("Material name")}><select value={material?.id ?? ""} onChange={e=>setId(e.target.value)}>{latest.map(m=><option key={m.id} value={m.id}>{m.name} · {tr(m.availability || "Laboratory material")} · {tr(m.lot || "lot unset")}</option>)}</select></Field>
  </Panel>{material && <MaterialEditor key={material.id} material={material} {...props} />}</div>;
}

function MaterialEditor({ material, ...props }: WorkspaceProps & { material: Material }) {
  const tr = useTranslation();
  const [m, setM] = useState(() => structuredClone(material));
  const patch = <K extends keyof Material>(key: K, value: Material[K]) => setM(p => ({ ...p, [key]: value }));
  return <form className="metro-stack station-method-form station-readable-method" onSubmit={e => { e.preventDefault(); const { id, created_at, ...record } = m; void props.act("/materials", { material: { ...record, revision_of: id } }); }}>
    <Panel title={m.name}><div className="station-panel-toolbar"><span>{tr(m.availability || "Laboratory material")} · {m.catalog_version}</span>{m.source_url && <a className="metro-btn" target="_blank" rel="noreferrer" href={m.source_url}>{tr("Issuer source")}</a>}{m.documents?.map(d=><a className="metro-btn" key={d.sha256} href={`${METROLOGY_API}/materials/${material.id}/documents/${d.sha256}`}>{tr("Download issuer PDF")} · {d.filename}</a>)}</div>
    <div className="metro-form-grid"><Field label={tr("Material name")}><input value={m.name} onChange={e => patch("name", e.target.value)} required /></Field><Field label={tr("Lot / batch")}><input value={m.lot} onChange={e => patch("lot", e.target.value)} placeholder={tr("Enter actual lot")} /></Field><Field label={tr("Supplier / producer")}><input value={m.supplier} onChange={e => patch("supplier", e.target.value)} /></Field><Field label={tr("Exact sample-label aliases, comma separated")}><input value={m.aliases.join(", ")} onChange={e => patch("aliases", e.target.value.split(",").map(v => v.trim()))} /></Field><Field label={tr("Certificate / reference document")} wide><input value={m.certificate} onChange={e => patch("certificate", e.target.value)} /></Field><Field label={tr("Certificate issue date")}><input type="date" value={m.issue_date} onChange={e => patch("issue_date", e.target.value)} /></Field><Field label={tr("Valid until, if specified")}><input type="date" value={m.valid_until} onChange={e => patch("valid_until", e.target.value)} /></Field></div>
    </Panel><Panel title={tr("Assigned values and uncertainty")}><div className="method-criteria-grid">{isotopes.map(iso => { const a = m.assigned[iso]; const update = (values: Partial<typeof a>) => patch("assigned", { ...m.assigned, [iso]: { ...a, ...values } }); return <section key={iso} className="method-section metro-stack"><header className="method-section-heading"><h3>{isotopeLabel[iso]}</h3><p>{tr(a.classification || "recommended")}</p></header><Field label={tr("Value classification")}><select value={a.classification || "recommended"} onChange={e=>update({classification:e.target.value as typeof a.classification})}>{(["certified","recommended","information","defined","unassigned"] as const).map(v=><option key={v} value={v}>{tr(v)}</option>)}</select></Field><Field label={tr("Assigned value / ‰")}><Num value={a.value} onChange={value => update({ value })} /></Field><Field label={tr("Quoted uncertainty / ‰")}><Num min={0} value={a.uncertainty} onChange={uncertainty => update({ uncertainty })} /></Field><Field label={tr("Uncertainty type")}><select value={a.uncertainty_type} onChange={e => update({ uncertainty_type: e.target.value as typeof a.uncertainty_type })}><option value="unset">{tr("Awaiting interpretation")}</option><option value="standard">{tr("Standard uncertainty u")}</option><option value="expanded">{tr("Expanded uncertainty U")}</option></select></Field><Field label={tr("Coverage factor k, if expanded")}><Num min={0} value={a.k} onChange={k => update({ k })} /></Field><Field label={tr("Coverage probability, if stated")}><Num min={0} value={a.coverage_probability} onChange={coverage_probability => update({ coverage_probability })} /></Field><Field label={tr("Reference scale")}><input value={a.scale} onChange={e => update({ scale: e.target.value })} /></Field><Field label={tr("Uncertainty interpretation")}><textarea value={a.notes || ""} onChange={e=>update({notes:e.target.value})}/></Field></section>; })}</div></Panel><Panel title={tr("Traceability notes")}>
    <Field label={tr("Traceability notes")}><textarea value={m.traceability} onChange={e => patch("traceability", e.target.value)} /></Field><label className="metro-check"><input type="checkbox" checked={m.verified} onChange={e => patch("verified", e.target.checked)} />{tr("Certificate, lot, assigned values and uncertainty interpretation verified")}</label><div className="metro-actions"><BusyButton busy={props.busy}>{tr("Save reference-material revision")}</BusyButton></div></Panel>
  </form>;
}
