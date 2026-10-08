"use client";

import { useState } from "react";
import { CycleMolecularRatioChart } from "./cycle-molecular-ratio-chart";
import { useTranslation } from "@/components/layout/language-provider";
import { SharedCycleDiagnosticsTable } from "./cycle-diagnostics-table";
import { SaturationFigureCard, SATURATION_COLOR_AXIS_OPTIONS, type SaturationAxisKey } from "./saturation-figure-card";
import type { CycleDiagnosticsPayload } from "@/lib/types";

type Isotope = "d13C" | "d18O";
type PickValue = (isotope: Isotope, value: number, stdev: number | null) => void;
const isotopeLabel = { d13C: "δ¹³C", d18O: "δ¹⁸O" };
const number = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" ? value as Record<string, unknown> : {};
const format = (value: unknown, digits = 3) => number(value)?.toFixed(digits) ?? "N/A";

export function IsotopeCycleDiagnostics({ isotope, diagnostics, loading, displayDelta = 0, onPick }: {
  isotope: Isotope;
  diagnostics?: CycleDiagnosticsPayload;
  loading?: boolean;
  displayDelta?: number;
  onPick?: PickValue;
}) {
  const tr = useTranslation();
  const [colorAxis, setColorAxis] = useState<SaturationAxisKey>("mean44");
  const mean = diagnostics?.cycle_mean ?? {};
  const drift = diagnostics?.intensity_linearity ?? {};
  const correction = record(diagnostics?.saturation_correction);
  const mismatch = record(correction.cycle_relative_mismatch);
  const plateau = record(correction.cycle_plateau);
  const figures = record(correction.figures);
  const figure = figures.cycle_relative_mismatch as Record<string, unknown> | undefined;
  const partial = /partial/i.test(String(diagnostics?.target?.collector_status ?? ""));
  const displayed = (value: unknown) => number(value) == null ? null : number(value)! + (partial ? 0 : displayDelta);
  const validCount = number(mean.valid_cycles) ?? 0;
  const suggestions = [
    { label: "Cycle mean", value: displayed(mean.valid_mean), sd: number(mean.valid_std_dev) },
    { label: "First valid cycle", value: displayed(mean.selected_value ?? mean.mean), sd: null },
    { label: "Last valid cycle", value: displayed(mean.last_valid_value), sd: null },
    { label: "Cycle relative mismatch correction", value: number(mismatch.value), sd: null, blocked: validCount < 4 },
    { label: "Cycle plateau", value: number(plateau.value), sd: number(plateau.std_dev) },
  ];

  return <section className="cycle-isotope-diagnostics" aria-label={tr(`${isotope} cycle diagnostics`)}>
    <header className="cycle-isotope-heading"><h3>{isotopeLabel[isotope]}</h3><span>{validCount} {tr("valid cycles")} · ‰</span></header>
    {loading && !diagnostics ? <p role="status">{tr("Loading cycle diagnostics...")}</p> : diagnostics ? <>
      <table className="cycle-values"><thead><tr><th scope="col">{tr("Parameter")}</th><th scope="col">{tr("Value")} / ‰</th><th scope="col">σ / ‰</th></tr></thead>
        <tbody>{suggestions.map(item => <tr key={item.label}>
          <th scope="row">{tr(item.label)}</th>
          <td>{onPick ? <button type="button" disabled={item.value == null || item.blocked} title={tr(item.blocked ? "not enough cycles for linearity calculation" : "Set value")}
            onClick={() => { if (item.value != null && !item.blocked) onPick(isotope, item.value, item.sd); }}>{format(item.value)}</button> : format(item.value)}</td>
          <td>{item.sd == null ? "—" : format(item.sd)}</td>
        </tr>)}</tbody>
      </table>
      <dl className="cycle-drift-values">
        <div><dt>{tr("Intensity-linearity drift")}</dt><dd>{format(drift.issue_index, 2)} × σ · {tr(String(drift.severity ?? "unavailable"))}</dd></div>
        <div><dt>{tr("Slope")} / 10 V</dt><dd>{format(drift.slope_per_10v)} ‰ · R² {format(drift.r_squared, 2)}</dd></div>
      </dl>
      {mean.reason ? <p className="cycle-diagnostics-note">{tr(String(mean.reason))}</p> : null}
      {record(mean.value_source).is_proxy ? <p className="cycle-diagnostics-note">{tr("Internal signal proxy")}</p> : null}
      {figure ? <>
        <SaturationFigureCard chartKey={`${isotope}:cycle_relative_mismatch`} title={tr("Cycle relative mismatch correction")}
          description={tr("Fits a quadratic curve of isotope value versus (Samp44 - Ref44) / Ref44, then predicts where that curve becomes horizontal.")}
          figure={figure} colorAxis={colorAxis} yAxis={isotope} collapsibleLegend
          toolbarControls={<label className="cycle-color-control">{tr("Chart color axis")}<select value={colorAxis} onChange={event => setColorAxis(event.target.value as SaturationAxisKey)}>
            {SATURATION_COLOR_AXIS_OPTIONS.map(option => <option key={option.value} value={option.value}>{tr(option.label)}</option>)}
          </select></label>} />
      </> : <p className="cycle-diagnostics-note">{tr("not enough cycles for linearity calculation")}</p>}
    </> : <p>{tr("Cycle diagnostics appear here once a point is selected.")}</p>}
  </section>;
}

export function SelectionCycleDiagnostics({ d13, d18, loadingD13, loadingD18, displayDelta, onPick, pickableIsotope }: {
  d13?: CycleDiagnosticsPayload;
  d18?: CycleDiagnosticsPayload;
  loadingD13?: boolean;
  loadingD18?: boolean;
  displayDelta?: (isotope: Isotope) => number;
  onPick?: PickValue;
  pickableIsotope?: Isotope;
}) {
  const tr = useTranslation();
  const shared = d13 ?? d18;
  return <div className="selection-cycle-diagnostics">
    <div className="selection-cycle-overview">
      <section className="selection-cycle-chart" data-chart-panel>
        {shared ? <CycleMolecularRatioChart rows={shared.table} /> : <p role="status">{tr(loadingD13 || loadingD18 ? "Loading cycle diagnostics..." : "Cycle diagnostics appear here once a point is selected.")}</p>}
      </section>
      {shared?.table?.length ? <section className="selection-cycle-table" aria-label={tr("Cycles")}><h3>{tr("Cycles")} · {shared.table.length} {tr("rows")}</h3><SharedCycleDiagnosticsTable rows={shared.table}/></section> : null}
    </div>
    <div className="cycle-isotope-grid">
      <IsotopeCycleDiagnostics isotope="d13C" diagnostics={d13} loading={loadingD13} displayDelta={displayDelta?.("d13C")} onPick={!pickableIsotope || pickableIsotope === "d13C" ? onPick : undefined}/>
      <IsotopeCycleDiagnostics isotope="d18O" diagnostics={d18} loading={loadingD18} displayDelta={displayDelta?.("d18O")} onPick={!pickableIsotope || pickableIsotope === "d18O" ? onPick : undefined}/>
    </div>
  </div>;
}
