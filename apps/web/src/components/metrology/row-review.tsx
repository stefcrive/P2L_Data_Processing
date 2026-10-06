"use client";

import { useState } from "react";
import { useTranslation } from "@/components/layout/language-provider";
import type { Measurement, RunDetail } from "@/lib/metrology";
import { Field, Status, type WorkspaceProps } from "./shared";

export function RowReview({ row, run, ...props }: WorkspaceProps & { row: Measurement; run: RunDetail }) {
  const tr = useTranslation();
  const [reason, setReason] = useState("");
  const [mass, setMass] = useState("");
  const locked = run.status === "released" || props.state.qualifications.find(q => q.id === run.qualification_id)?.status === "approved";
  const eligible = (row.issues ?? []).filter(issue => /internal SD|extrapolates beyond|outside validated isotope range/.test(issue) || issue.endsWith("missing or outside validated range"));
  const calculated = !!row.isotopes?.d13c?.budget && !!row.isotopes?.d18o?.budget;
  const decision = { actor: props.decision.actor, reason };
  async function refresh(path: string, body: unknown) {
    const result = await props.act(path, body);
    if (result) await props.act(`/runs/${run.id}/evaluate`, { ...decision, method_id: run.method_id });
  }
  return <div className="station-row-review">
    <div className="metro-actions"><b>{row.label} · {row.source_index}</b><Status value={row.excluded ? "excluded" : row.issues?.length ? "review_required" : "pass"}/></div>
    {!!row.issues?.length && <ul className="station-issue-list">{row.issues.map(issue => <li key={issue}>{tr(issue)}</li>)}</ul>}
    {!!row.accepted_issues?.length && <p className="station-exception-note">{tr("Accepted exceptions")}: {row.accepted_issues.map(tr).join("; ")}</p>}
    {row.reviews?.map((review, i) => <p className="metro-muted" key={i}>{review.actor} · {review.at.slice(0,16)} · {review.reason}</p>)}
    {!locked && !row.excluded && <form className="metro-stack" onSubmit={event => event.preventDefault()}>
      <Field label={tr("Justification for this analysis")}><textarea required minLength={3} value={reason} onChange={e => setReason(e.target.value)} placeholder={tr("Record the evidence and the reason for your decision")}/></Field>
      <div className="metro-actions">
        <button className="metro-btn" disabled={props.busy || reason.trim().length < 3} onClick={() => void refresh(`/runs/${run.id}/exclusions`, { ...decision, measurement_id: row.id, evidence: reason })}>{tr("Exclude analysis and re-evaluate")}</button>
        {row.role === "unknown" && eligible.length > 0 && <button className="metro-btn primary" disabled={props.busy || !calculated || reason.trim().length < 3 || !run.evaluation} onClick={() => void props.act(`/runs/${run.id}/row-review`, { ...decision, measurement_id: row.id, evaluation_id: run.evaluation!.id, issues: eligible })}>{tr("Accept sample exceptions")}</button>}
      </div>
      {row.role === "unknown" && !calculated && <small>{tr("Complete both isotope values and uncertainty budgets before accepting an exception.")}</small>}
      <div className="metro-actions"><Field label={tr("Verified weighed mass / µg")}><input type="number" min="0.001" step="any" value={mass} onChange={e => setMass(e.target.value)}/></Field><button className="metro-btn" disabled={props.busy || Number(mass) <= 0 || reason.trim().length < 3} onClick={() => void refresh(`/runs/${run.id}/annotations`, { ...decision, masses_ug: { [row.id]: Number(mass) } })}>{tr("Save mass and re-evaluate")}</button></div>
      <small className="metro-muted">{tr("Original data and flags remain in the audit record. Acceptance does not bypass session QC, qualification or missing calculations. A new evaluation requires a new exception review.")}</small>
    </form>}
  </div>;
}
