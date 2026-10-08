"use client";

import { useTranslation } from "@/components/layout/language-provider";
import type { ReactNode } from "react";
import { fmt, human, type Decision, type State } from "@/lib/metrology";

export type WorkspaceProps = {
  state: State; decision: Decision; busy: boolean;
  act: (path: string, body?: unknown, method?: string) => Promise<unknown>;
  upload: (path: string, data: FormData) => Promise<unknown>;
};
export function Panel({ title, children, chartPanel = false }: { title?: string; children: ReactNode; chartPanel?: boolean }) {
  const tr = useTranslation();
  return <section className="metro-panel" data-chart-panel={chartPanel ? "" : undefined}>{tr(title && (chartPanel ? <div data-card-header><h2>{tr(title)}</h2></div> : <h2>{tr(title)}</h2>))}{tr(children)}</section>;
}
export function Field({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  const tr = useTranslation();
  return <label className={`metro-field ${wide ? "wide" : ""}`}><span>{tr(label)}</span>{tr(children)}</label>;
}
export function Num({ value, onChange, min, required = false, unit }: { value: number | null | undefined; onChange: (n: number | null) => void; min?: number; required?: boolean; unit?: string }) {
  const input = <input type="number" step="any" min={min} required={required} value={value ?? ""} onChange={e => onChange(e.target.value === "" ? null : Number(e.target.value))} />;
  return unit ? <span className="station-unit-input">{input}<span>{unit}</span></span> : input;
}
export function Status({ value }: { value: string }) {
  const tr = useTranslation();
  const bad = /blocked|fail|out_of_control/.test(value);
  const good = /^(active|valid|approved|released|pass|no_signal_detected|resolved)$/.test(value);
  return <span className={`metro-status ${bad ? "bad" : good ? "good" : "warn"}`}>{tr(human(value))}</span>;
}
export function Inspect({ title, value }: { title: string; value: unknown }) {
  const tr = useTranslation();
  return <details><summary>{tr(title)}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}
export function Empty({ children }: { children: ReactNode }) {
  const tr = useTranslation(); return <div className="metro-empty"><p className="metro-muted">{tr(children)}</p></div>; }
export function BusyButton({ busy, children, primary = true }: { busy: boolean; children: ReactNode; primary?: boolean }) {
  const tr = useTranslation();
  return <button disabled={busy} className={`metro-btn ${primary ? "primary" : ""}`} type="submit">{tr(busy ? "Saving…" : children)}</button>;
}
export function Scatter({ points, limits, target, mean, sd, xLabel, yLabel, connect = false }: { points: { x: number; y: number; label?: string }[]; limits?: number[] | null; target?: number | null; mean?: number | null; sd?: number | null; xLabel: string; yLabel: string; connect?: boolean }) {
  const tr = useTranslation();
  if (points.length < 2) return <Empty>{tr("At least two observations are needed for this chart.")}</Empty>;
  const oneSigma = mean == null || sd == null ? [] : [mean - sd, mean + sd];
  const xs = points.map(p => p.x), ys = [...points.map(p => p.y), ...(limits ?? []), ...(target == null ? [] : [target]), ...(mean == null ? [] : [mean]), ...oneSigma];
  const xmin = Math.min(...xs), xmax = Math.max(...xs), ymin = Math.min(...ys), ymax = Math.max(...ys);
  const dx = Math.max(xmax - xmin, 1e-6), dy = Math.max(ymax - ymin, .005);
  const x = (n: number) => 60 + 445 * (n - xmin) / dx;
  const y = (n: number) => 191 - 144 * (n - ymin + dy * .15) / (dy * 1.3);
  return <svg className="metro-chart" viewBox="0 0 530 255" role="img" aria-label={tr(`${yLabel} versus ${xLabel}, ${points.length} observations`)}>
    <rect x="59" y="24" width="447" height="177" fill="#f8fbfc" />
    {[0, .5, 1].map(t => <g key={t}><line x1="60" x2="506" y1={y(ymin + t * dy)} y2={y(ymin + t * dy)} stroke="#dce8eb" /><text x="52" y={y(ymin + t * dy) + 3} fontSize="9" textAnchor="end" fill="#597b87">{tr(fmt(ymin + t * dy, 3))}</text></g>)}
    {(limits ?? []).map((v, i) => <line key={i} x1="60" x2="506" y1={y(v)} y2={y(v)} stroke="#b87638" strokeDasharray="4 4" />)}
    {oneSigma.map((value,index)=><line key={`sigma-${index}`} x1="60" x2="506" y1={y(value)} y2={y(value)} stroke="#c38835" strokeDasharray="2 3"><title>{tr("Average ±1σ")}: {fmt(value,4)}</title></line>)}
    {target != null && <line x1="60" x2="506" y1={y(target)} y2={y(target)} stroke="#7c3aed" strokeDasharray="6 3"><title>{tr("True value")}: {fmt(target,4)}</title></line>}
    {mean != null && <line x1="60" x2="506" y1={y(mean)} y2={y(mean)} stroke="#334155" strokeWidth="1.6"><title>{tr("Average")}: {fmt(mean,4)}</title></line>}
    {connect && <polyline points={points.map(p => `${x(p.x)},${y(p.y)}`).join(" ")} fill="none" stroke="#7aa9b3" strokeWidth="1.2" />}
    {points.map((p, i) => <circle key={i} cx={x(p.x)} cy={y(p.y)} r="3.4" fill="#196b7b" stroke="white" strokeWidth=".7"><title>{tr(p.label || `${xLabel}: ${fmt(p.x)}, ${yLabel}: ${fmt(p.y)}`)}</title></circle>)}
    <text x="60" y="216" fontSize="9" fill="#597b87">{tr(fmt(xmin, 1))}</text><text x="505" y="216" textAnchor="end" fontSize="9" fill="#597b87">{tr(fmt(xmax, 1))}</text>
    <text x="283" y="232" textAnchor="middle" fontSize="10" fill="#597b87">{tr(xLabel)}</text><text x="60" y="14" fontSize="10" fill="#597b87">{tr(yLabel)}</text>
    {mean != null && <g><line x1="60" x2="76" y1="247" y2="247" stroke="#334155" strokeWidth="1.6"/><text x="80" y="250" fontSize="8" fill="#597b87">{tr("Average")}</text></g>}
    {target != null && <g><line x1="180" x2="196" y1="247" y2="247" stroke="#7c3aed" strokeDasharray="6 3"/><text x="200" y="250" fontSize="8" fill="#597b87">{tr("True value")}</text></g>}
    {!!oneSigma.length && <g><line x1="330" x2="346" y1="247" y2="247" stroke="#c38835" strokeDasharray="2 3"/><text x="350" y="250" fontSize="8" fill="#597b87">{tr("Average ±1σ")}</text></g>}
  </svg>;
}
