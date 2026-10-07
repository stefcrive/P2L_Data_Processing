"use client";

import Link from "next/link";
import { useTranslation } from "@/components/layout/language-provider";
import { fmt, isotopeLabel, isotopes, type Method, type ResultsSessionDetail, type State } from "@/lib/metrology";
import { Chart } from "./results-station";
import { Empty, Panel, Status } from "./shared";
import { normalizationEnvelope } from "@/lib/metrology-envelopes";

export function MethodFacts({ method, compact = false }: { method: Method | null | undefined; compact?: boolean }) {
  const tr = useTranslation();
  if (!method) return <Empty>{tr("No qualified method yet.")}</Empty>;
  const c = method.config;
  return <dl className="station-compact-facts">{[
    ["Method", `${c.name} · v${method.version}`], ["Instrument", c.instrument],
    ["Instrument configuration identifier", c.configuration], ["TuneBook / Qtegra configuration", c.tunebook],
    ["Reaction temperature / °C", c.reaction_temperature_c == null ? "" : `${c.reaction_temperature_c} °C`],
    ["Mass / µg", c.ranges.mass_ug ? `${c.ranges.mass_ug.low}–${c.ranges.mass_ug.high} µg` : ""],
  ].filter((_,i)=>!compact||i>1).map(([label, value]) => <div key={label}><dt>{tr(label)}</dt><dd>{value || tr("Unset")}</dd></div>)}</dl>;
}

export function AnchorPair({ method }: { method: Method | null | undefined }) {
  const tr = useTranslation();
  if (!method?.normalization) return <Empty>{tr("Dual-anchor curves will appear after qualification approval.")}</Empty>;
  return <div className="metro-grid station-anchor-pair">{isotopes.map(iso => {
    const model = method.normalization?.[iso];
    return <section key={iso} className="metro-panel">{model ? <>
      <Chart height={220} legendCollapsed title={`${isotopeLabel[iso]} · ${tr("Dual-point anchors")}`} x={tr("Corrected instrument delta / ‰")} y={`${isotopeLabel[iso]} / ‰ VPDB`} data={[...normalizationEnvelope(model, method.config.coverage_factor, `${tr("Normalization envelope")} · k=${method.config.coverage_factor}`, "rgba(31,95,191,0.16)"), {
        type: "scatter", mode: "lines+markers", name: tr("Anchor means"), x: model.measured, y: model.assigned,
        line: { color: "#1f5fbf", width: 1.5 }, marker: { size: 11, color: "#1f5fbf" },
        error_x: { type: "data", array: [2, 3].map(i => Math.sqrt(model.input_covariance[i][i])), visible: true },
        error_y: { type: "data", array: [0, 1].map(i => Math.sqrt(model.input_covariance[i][i])), visible: true },
      }]} />
      <p className="station-model-equation">y = {fmt(model.intercept, 5)} + {fmt(model.slope, 6)} x <span>{tr("Anchor error bars: standard uncertainty")}</span></p>
      <p className="metro-muted">{tr("Shading: pointwise ±k·u_norm from the full anchor covariance; precision and residual-correction uncertainty are separate.")}</p>
    </> : <Empty>{tr("Normalization unavailable")}</Empty>}</section>;
  })}</div>;
}

export function UncertaintySummary({ method }: { method: Method | null | undefined }) {
  const tr = useTranslation();
  return <div className="metro-table-wrap"><table className="station-numeric-table"><thead><tr><th>{tr("Uncertainty / ‰")}</th><th>δ¹³C</th><th>δ¹⁸O</th></tr></thead><tbody>
    <tr><td>u_prec</td>{isotopes.map(i => <td key={i}>{fmt(method?.config.precision[i], 4)}</td>)}</tr>
    <tr><td>u_norm</td><td colSpan={2}>{tr("Sample-specific anchor covariance")}</td></tr>
    <tr><td>u_corr</td>{isotopes.map(i => <td key={i}>{tr(method?.config.corrections?.[i] ? "Propagated per result" : "No correction model")}</td>)}</tr>
    {[...new Set(isotopes.flatMap(i=>method?.config.additional_components[i]?.map(c=>c.name)??[]))].map(name=><tr key={name}><td>{name}</td>{isotopes.map(i=><td key={i}>{fmt(method?.config.additional_components[i]?.find(c=>c.name===name)?.u ?? 0,4)}</td>)}</tr>)}
    <tr><td>U = k · u_c</td><td colSpan={2}>k = {method?.config.coverage_factor ?? "—"}</td></tr>
  </tbody></table></div>;
}

export function SessionMethodSummary({ detail, state }: { detail: ResultsSessionDetail; state: State }) {
  const tr = useTranslation();
  const recent = state.qualifications.at(-1);
  const recentMethod = state.methods.find(m => m.id === recent?.method_id);
  return <Panel title={tr("Method used by this results session")}><div className="station-toolbar">
    <span>{tr("Qualification of reference")} · <Link className="station-session-link" href={`/metrology/qualification?qualification=${detail.qualification_id}`}>{detail.qualification_id?.slice(0, 8) ?? tr("Pending qualification")}</Link> · {detail.qualification?.approval?.at.slice(0, 10) ?? tr("Under review")}</span>
    <Status value={detail.qualification?.status ?? "not_evaluated"}/>
  </div><MethodFacts method={detail.method}/><p className="metro-muted">{tr(detail.input_basis === "already_vpdb" ? "Qtegra normalization retained; no second normalization." : "Instrument deltas: correction followed by dual-point normalization.")}</p>
    {recent && recent.id !== detail.qualification_id && <div className="station-latest-qualification"><span>{tr("Most recent qualification")} · <Link href={`/metrology/qualification?qualification=${recent.id}`}>{recent.id.slice(0,8)}</Link> · v{recentMethod?.version} · {recent.approval?.at.slice(0,10) ?? tr("Under review")}</span><Status value={recent.status}/><small>{tr("This session keeps its original qualification of reference.")}</small></div>}
  </Panel>;
}

export function SessionQcSummary({ detail }: { detail: ResultsSessionDetail }) {
  const tr = useTranslation();
  // The latest evaluation of every workbook in THIS session; no historical QC or sample-group filter.
  const rows = [...new Map(detail.runs.flatMap(r => r.evaluation?.results ?? []).filter(r => r.role === "qc" && !r.excluded).map(r => [r.id, r])).values()];
  const stats = (values: (number | null | undefined)[]) => {
    const v = values.filter((x): x is number => typeof x === "number" && Number.isFinite(x));
    const mean = v.length ? v.reduce((s,x) => s+x,0)/v.length : 0;
    return { n: v.length, sd: v.length > 1 ? Math.sqrt(v.reduce((s,x) => s+(x-mean)**2,0)/(v.length-1)) : null };
  };
  const populations = [
    { id: "session", label: tr("Global session QC"), rows },
    ...detail.runs.map(run => ({ id: run.id, label: run.label, rows: (run.evaluation?.results ?? []).filter(row => row.role === "qc" && !row.excluded) })),
  ];
  return <Panel title={tr("QC standard deviation")}><div className="metro-table-wrap"><table className="station-numeric-table"><thead><tr><th>{tr("Session / carousel")}</th><th>{tr("Isotope")}</th><th>{tr("Imported SD / ‰")}</th><th>{tr("Final SD / ‰")}</th><th>{tr("SD criterion / ‰")}</th></tr></thead><tbody>{populations.flatMap(population => isotopes.map(i => {
    const raw = stats(population.rows.map(r => r[i])), final = stats(population.rows.map(r => r.isotopes?.[i]?.value));
    const limit = detail.method?.config.qc.external_sd[i];
    const failed = limit != null && final.sd != null && final.sd >= limit;
    return <tr key={`${population.id}-${i}`} className={`${population.id === "session" ? "station-budget-total" : ""} ${failed ? "station-qc-failed" : ""}`} title={failed ? tr("Final QC SD exceeds the method criterion") : undefined}><th scope="row">{population.label}</th><td>{isotopeLabel[i]}</td><td>{fmt(raw.sd,4)} <small>n={raw.n}</small></td><td>{fmt(final.sd,4)} <small>n={final.n}</small></td><td>&lt; {fmt(detail.method?.config.qc.external_sd[i],3)}</td></tr>;
  }))}</tbody></table></div><p className="metro-muted">{tr("All non-excluded QC aliquots in this session. Missing results reduce n; pooled SD does not replace individual workbook acceptance or historical u_prec.")}</p></Panel>;
}
