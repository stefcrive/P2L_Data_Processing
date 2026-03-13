"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useEffect, useState } from "react";

import { PlotlyChart } from "@/components/charts/plotly-chart";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { MultiSelectDropdown } from "@/components/ui/multi-select-dropdown";
import { api } from "@/lib/api";
import type { CalibrationConfig, CalibrationPrecisionSummary, CalibrationWorkspace } from "@/lib/types";
import { useSessionStore } from "@/store/use-session-store";

const PRECISION_PASS_THRESHOLD = 0.07;
const INCLUSION_PASS_THRESHOLD = 80;

function formatMetric(value?: number | null, digits = 3) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "N/A";
  }
  return value.toFixed(digits);
}

function formatMetricWithUnit(value?: number | null, digits = 3) {
  const formatted = formatMetric(value, digits);
  return formatted === "N/A" ? formatted : `${formatted} ‰`;
}

function classifyPrecision(value?: number | null) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "neutral" as const;
  }
  return value < PRECISION_PASS_THRESHOLD ? ("pass" as const) : ("fail" as const);
}

function classifyInclusion(pct?: number | null) {
  if (typeof pct !== "number" || Number.isNaN(pct)) {
    return "neutral" as const;
  }
  return pct > INCLUSION_PASS_THRESHOLD ? ("pass" as const) : ("fail" as const);
}

function toneClasses(tone: "pass" | "fail" | "neutral") {
  if (tone === "pass") {
    return {
      shell: "border-emerald-200 bg-emerald-50/90",
      value: "text-emerald-800",
      subtle: "text-emerald-700",
    };
  }
  if (tone === "fail") {
    return {
      shell: "border-rose-200 bg-rose-50/90",
      value: "text-rose-800",
      subtle: "text-rose-700",
    };
  }
  return {
    shell: "border-stone-200 bg-white",
    value: "text-stone-900",
    subtle: "text-stone-600",
  };
}

function DataTable({ rows, emptyLabel }: { rows: Array<Record<string, unknown>>; emptyLabel: string }) {
  if (!rows.length) {
    return <div className="rounded-lg border border-dashed border-stone-300 p-4 text-sm text-stone-500">{emptyLabel}</div>;
  }
  const columns = Object.keys(rows[0] ?? {});
  return (
    <div className="overflow-x-auto rounded-lg border border-stone-200">
      <table className="min-w-full divide-y divide-stone-200 text-left text-sm">
        <thead className="bg-stone-50">
          <tr>
            {columns.map((column) => (
              <th key={column} className="px-3 py-2 font-medium text-stone-700">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-stone-100 bg-white">
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} className="px-3 py-2 text-stone-600">
                  {String(row[column] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SummaryChip({ label, value, hint, tone = "neutral" }: { label: string; value: string; hint?: string; tone?: "pass" | "fail" | "neutral" }) {
  const styles = toneClasses(tone);
  return (
    <div className={`min-w-[140px] rounded-xl border px-3 py-2 text-left shadow-sm ${styles.shell}`}>
      <div className="text-[11px] uppercase tracking-[0.14em] text-stone-500">{label}</div>
      <div className={`mt-1 text-base font-semibold tabular-nums ${styles.value}`}>{value}</div>
      {hint ? <div className={`mt-0.5 text-xs ${styles.subtle}`}>{hint}</div> : null}
    </div>
  );
}

function PrecisionMetricPanel({ label, value }: { label: string; value?: number | null }) {
  const styles = toneClasses(classifyPrecision(value));
  return (
    <div className={`rounded-xl border px-3 py-3 ${styles.shell}`}>
      <div className="text-[11px] uppercase tracking-[0.14em] text-stone-500">{label}</div>
      <div className={`mt-2 text-2xl font-semibold leading-none tabular-nums ${styles.value}`}>{formatMetricWithUnit(value)}</div>
    </div>
  );
}

function IsotopeSummaryTile({
  label,
  precision,
  average,
  correctedPrecision,
}: {
  label: string;
  precision?: number | null;
  average?: number | null;
  correctedPrecision?: number | null;
}) {
  return (
    <div className="rounded-xl border border-stone-200 bg-stone-50/80 p-4">
      <div className="text-xs uppercase tracking-[0.14em] text-stone-500">{label} precision</div>
      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <PrecisionMetricPanel label="Normal" value={precision} />
        <PrecisionMetricPanel label="Linearity corrected" value={correctedPrecision} />
      </div>
      <div className="mt-4 space-y-2 text-sm">
        <div className="flex items-center justify-between border-t border-stone-200 pt-2 text-stone-600">
          <span>{label} average</span>
          <span className="font-medium tabular-nums text-stone-900">{formatMetricWithUnit(average)}</span>
        </div>
      </div>
    </div>
  );
}

function PrecisionCard({ summary }: { summary: CalibrationPrecisionSummary }) {
  const linePrecisionEntries = Object.entries(summary.line_precisions).sort(([left], [right]) => {
    const leftNumber = Number(left);
    const rightNumber = Number(right);
    if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
      return leftNumber - rightNumber;
    }
    return left.localeCompare(right);
  });

  return (
    <Card className="w-full overflow-hidden border-stone-300">
      <CardHeader className="gap-4 border-b border-stone-200 bg-gradient-to-r from-stone-50 via-white to-stone-50 pb-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-1">
            <CardTitle className="text-2xl tracking-tight">{summary.standard}</CardTitle>
            <CardDescription>
              Calibration quality snapshot across included standards and isotope precision metrics. Green marks precision &lt; 0.07 ‰ and inclusion &gt; 80%.
            </CardDescription>
          </div>
          <div className="grid gap-2 sm:grid-cols-3">
            <SummaryChip label="Measurements" value={String(summary.total_rows)} />
            <SummaryChip
              label="δ13C included"
              value={`${summary.included_d13}/${summary.total_rows}`}
              hint={`${summary.included_pct_d13.toFixed(1)}% retained`}
              tone={classifyInclusion(summary.included_pct_d13)}
            />
            <SummaryChip
              label="δ18O included"
              value={`${summary.included_d18}/${summary.total_rows}`}
              hint={`${summary.included_pct_d18.toFixed(1)}% retained`}
              tone={classifyInclusion(summary.included_pct_d18)}
            />
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5 p-6">
        <div className="grid gap-4 md:grid-cols-2">
          <IsotopeSummaryTile
            label="δ13C (‰)"
            precision={summary.d13_precision}
            average={summary.d13_average}
            correctedPrecision={summary.d13_linearity_corrected_precision}
          />
          <IsotopeSummaryTile
            label="δ18O (‰)"
            precision={summary.d18_precision}
            average={summary.d18_average}
            correctedPrecision={summary.d18_linearity_corrected_precision}
          />
        </div>
        {linePrecisionEntries.length ? (
          <div className="rounded-xl border border-stone-200 p-4">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-xs uppercase tracking-[0.14em] text-stone-500">Line precision breakdown</div>
              <div className="text-xs text-stone-500">{linePrecisionEntries.length} lines</div>
            </div>
            <div className="overflow-hidden rounded-lg border border-stone-200">
              <div className="grid grid-cols-[100px_minmax(0,1fr)_minmax(0,1fr)] bg-stone-100 px-3 py-2 text-xs font-medium uppercase tracking-wide text-stone-600">
                <span>Line</span>
                <span>δ13C (‰)</span>
                <span>δ18O (‰)</span>
              </div>
              {linePrecisionEntries.map(([line, values], index) => {
                const d13Tone = toneClasses(classifyPrecision(values.d13_precision));
                const d18Tone = toneClasses(classifyPrecision(values.d18_precision));
                return (
                  <div
                    key={line}
                    className={`grid grid-cols-[100px_minmax(0,1fr)_minmax(0,1fr)] px-3 py-2 text-sm tabular-nums text-stone-700 ${
                      index % 2 ? "bg-stone-50" : "bg-white"
                    }`}
                  >
                    <span className="font-medium text-stone-900">Line {line}</span>
                    <span className={`inline-flex max-w-fit rounded-md border px-2 py-1 text-base font-semibold ${d13Tone.shell} ${d13Tone.value}`}>
                      {formatMetricWithUnit(values.d13_precision)}
                    </span>
                    <span className={`inline-flex max-w-fit rounded-md border px-2 py-1 text-base font-semibold ${d18Tone.shell} ${d18Tone.value}`}>
                      {formatMetricWithUnit(values.d18_precision)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export default function CalibrationPage() {
  const sessionId = useSessionStore((state) => state.sessionId);
  const queryClient = useQueryClient();
  const [config, setConfig] = useState<CalibrationConfig | null>(null);
  const [hasLoadedDraft, setHasLoadedDraft] = useState(false);
  const draftStorageKey = sessionId ? `calibration-config:${sessionId}` : null;

  const workspaceQuery = useQuery({
    queryKey: ["calibration-workspace", sessionId],
    queryFn: () => api.getCalibrationWorkspace(sessionId!),
    enabled: Boolean(sessionId),
  });

  useEffect(() => {
    setConfig(null);
    setHasLoadedDraft(false);
  }, [sessionId]);

  useEffect(() => {
    if (!draftStorageKey || typeof window === "undefined") {
      return;
    }
    const raw = window.sessionStorage.getItem(draftStorageKey);
    if (!raw) {
      setHasLoadedDraft(true);
      return;
    }
    try {
      setConfig(JSON.parse(raw) as CalibrationConfig);
    } catch {
      window.sessionStorage.removeItem(draftStorageKey);
    } finally {
      setHasLoadedDraft(true);
    }
  }, [draftStorageKey]);

  useEffect(() => {
    if (!workspaceQuery.data || !hasLoadedDraft) {
      return;
    }
    setConfig((current) => current ?? workspaceQuery.data.config);
  }, [hasLoadedDraft, workspaceQuery.data]);

  useEffect(() => {
    if (!draftStorageKey || typeof window === "undefined" || !config) {
      return;
    }
    window.sessionStorage.setItem(draftStorageKey, JSON.stringify(config));
  }, [config, draftStorageKey]);

  const deferredConfig = useDeferredValue(config);

  const previewQuery = useQuery({
    queryKey: ["calibration-workspace-preview", sessionId, deferredConfig],
    queryFn: () => api.previewCalibrationWorkspace(sessionId!, deferredConfig!),
    enabled: Boolean(sessionId && deferredConfig),
  });

  const runMutation = useMutation({
    mutationFn: (payload: CalibrationConfig) => api.runCalibration(sessionId!, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration-workspace", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration-workspace-preview", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["calibration", sessionId] });
    },
  });

  function updateConfig<T extends keyof CalibrationConfig>(key: T, value: CalibrationConfig[T]) {
    setConfig((current) => (current ? { ...current, [key]: value } : current));
  }

  function updateLinearity(key: keyof CalibrationConfig["linearity"], value: boolean | number) {
    setConfig((current) =>
      current
        ? {
            ...current,
            linearity: {
              ...current.linearity,
              [key]: value,
            },
          }
        : current,
    );
  }

  if (!sessionId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>No Active Session</CardTitle>
          <CardDescription>Import data first to unlock calibration.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (workspaceQuery.isLoading && !workspaceQuery.data) {
    return <div className="text-sm text-stone-500">Loading calibration workspace...</div>;
  }

  if (workspaceQuery.error) {
    return <div className="text-sm text-red-600">Failed to load calibration workspace.</div>;
  }

  const workspace = workspaceQuery.data as CalibrationWorkspace | undefined;
  const activeConfig = config ?? workspace?.config ?? null;
  const displayedWorkspace = (previewQuery.data as CalibrationWorkspace | undefined) ?? workspace;

  if (!workspace || !activeConfig || !displayedWorkspace) {
    return null;
  }

  const minDate = displayedWorkspace.available_values.min_date ?? undefined;
  const maxDate = displayedWorkspace.available_values.max_date ?? undefined;
  const selectedStandards = activeConfig.selected_standards;
  const lineIntensityBasis = String(displayedWorkspace.linearity_fits?.intensity_col ?? activeConfig.z_axis ?? "N/A");
  const previewError = previewQuery.error instanceof Error ? previewQuery.error.message : null;
  const runError = runMutation.error instanceof Error ? runMutation.error.message : null;
  const hasUnsavedPreview = JSON.stringify(activeConfig) !== JSON.stringify(workspace.config);
  const precisionSummaries = displayedWorkspace.precision_summaries;
  const linePrecisionCount = precisionSummaries.reduce((count, summary) => count + Object.keys(summary.line_precisions).length, 0);

  return (
    <div className="space-y-6">
      <Card className="border-stone-300 bg-stone-50">
        <CardHeader className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <CardTitle>Calibration Workspace</CardTitle>
            <CardDescription>
              Streamlit-parity calibration controls, standard filtering, precision summaries, linearity diagnostics, and calibration charts.
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2 text-sm text-stone-600">
            <span className="rounded-full bg-white px-3 py-1 ring-1 ring-stone-200">Standards: {selectedStandards.length}</span>
            <span className="rounded-full bg-white px-3 py-1 ring-1 ring-stone-200">Method: {activeConfig.calibration_type}</span>
            <span className="rounded-full bg-white px-3 py-1 ring-1 ring-stone-200">Linearity basis: {lineIntensityBasis}</span>
            <span className="rounded-full bg-white px-3 py-1 ring-1 ring-stone-200">
              {previewQuery.isFetching ? "Refreshing preview..." : hasUnsavedPreview ? "Preview mode" : "Saved config"}
            </span>
          </div>
        </CardHeader>
      </Card>

      <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <aside className="space-y-6 xl:sticky xl:top-6 xl:self-start">
          <Card>
            <CardHeader>
              <CardTitle>Calibration Controls</CardTitle>
              <CardDescription>Configure standards, outlier detection, visualization, date range, and linearity settings.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <MultiSelectDropdown
                label="Selected standards"
                options={displayedWorkspace.available_values.standards}
                selected={activeConfig.selected_standards}
                onChange={(next) => updateConfig("selected_standards", next)}
                placeholder="Select standards"
              />

              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-2">
                <label className="text-sm">
                  <span className="mb-1 block text-stone-700">Sigma level</span>
                  <input
                    type="number"
                    min="0.1"
                    max="5"
                    step="0.1"
                    value={activeConfig.sigma_level}
                    onChange={(event) => updateConfig("sigma_level", Number(event.target.value))}
                    className="w-full rounded-lg border border-stone-300 px-3 py-2"
                  />
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-stone-700">IQR multiplier</span>
                  <input
                    type="number"
                    min="1"
                    max="10"
                    step="0.1"
                    value={activeConfig.iqr_multiplier}
                    onChange={(event) => updateConfig("iqr_multiplier", Number(event.target.value))}
                    className="w-full rounded-lg border border-stone-300 px-3 py-2"
                  />
                </label>
              </div>

              <label className="text-sm">
                <span className="mb-1 block text-stone-700">Outlier method</span>
                <select
                  value={activeConfig.calibration_type}
                  onChange={(event) => updateConfig("calibration_type", event.target.value as CalibrationConfig["calibration_type"])}
                  className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                >
                  <option value="Z-Score">Z-Score</option>
                  <option value="IQR">IQR</option>
                </select>
              </label>

              <label className="flex items-start gap-3 rounded-lg border border-stone-200 p-3">
                <input
                  type="checkbox"
                  checked={activeConfig.independent_isotope_outliers}
                  onChange={(event) => updateConfig("independent_isotope_outliers", event.target.checked)}
                  className="mt-1 h-4 w-4"
                />
                <span>
                  <span className="block text-sm font-medium text-stone-800">Independent isotope outliers</span>
                  <span className="block text-xs text-stone-500">d13C and d18O outlier filtering stays independent per standard row.</span>
                </span>
              </label>

              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
                <label className="text-sm">
                  <span className="mb-1 block text-stone-700">Color parameter</span>
                  <select
                    value={activeConfig.color_param}
                    onChange={(event) => updateConfig("color_param", event.target.value)}
                    className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                  >
                    {displayedWorkspace.available_values.color_params.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-stone-700">3D Z axis</span>
                  <select
                    value={activeConfig.z_axis}
                    onChange={(event) => updateConfig("z_axis", event.target.value)}
                    className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2"
                  >
                    {displayedWorkspace.available_values.z_axis_options.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="space-y-3 rounded-lg border border-stone-200 p-4">
                <div className="text-sm font-medium text-stone-800">Precision date range</div>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
                  <label className="text-sm">
                    <span className="mb-1 block text-stone-700">Start date</span>
                    <input
                      type="date"
                      min={minDate}
                      max={maxDate}
                      value={activeConfig.precision_date_range?.[0] ?? ""}
                      onChange={(event) =>
                        updateConfig("precision_date_range", [event.target.value || null, activeConfig.precision_date_range?.[1] ?? null])
                      }
                      className="w-full rounded-lg border border-stone-300 px-3 py-2"
                    />
                  </label>
                  <label className="text-sm">
                    <span className="mb-1 block text-stone-700">End date</span>
                    <input
                      type="date"
                      min={minDate}
                      max={maxDate}
                      value={activeConfig.precision_date_range?.[1] ?? ""}
                      onChange={(event) =>
                        updateConfig("precision_date_range", [activeConfig.precision_date_range?.[0] ?? null, event.target.value || null])
                      }
                      className="w-full rounded-lg border border-stone-300 px-3 py-2"
                    />
                  </label>
                </div>
              </div>

              <div className="space-y-3 rounded-lg border border-stone-200 p-4">
                <div className="text-sm font-medium text-stone-800">Linearity</div>
                <label className="flex items-start gap-3">
                  <input type="checkbox" checked={activeConfig.linearity.apply} onChange={(event) => updateLinearity("apply", event.target.checked)} className="mt-1 h-4 w-4" />
                  <span>
                    <span className="block text-sm font-medium text-stone-800">Apply linearity correction on calibration run</span>
                    <span className="block text-xs text-stone-500">Uses the currently selected standards and intensity basis.</span>
                  </span>
                </label>
                <label className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={activeConfig.linearity.use_diff_intensity}
                    onChange={(event) => updateLinearity("use_diff_intensity", event.target.checked)}
                    className="mt-1 h-4 w-4"
                  />
                  <span>
                    <span className="block text-sm font-medium text-stone-800">Use Samp-Ref intensity difference</span>
                    <span className="block text-xs text-stone-500">Switches the linearity basis from sample intensity to cycle-1 sample-reference difference.</span>
                  </span>
                </label>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button
                  onClick={() => runMutation.mutate(activeConfig)}
                  disabled={runMutation.isPending || selectedStandards.length < 1 || selectedStandards.length > 2}
                >
                  {runMutation.isPending ? "Running..." : "Calibrate results"}
                </Button>
                <Button variant="outline" onClick={() => setConfig(workspace.config)} disabled={runMutation.isPending}>
                  Restore saved
                </Button>
              </div>
              <div className="text-xs text-stone-500">Select exactly one or two standards to run calibration.</div>
              {previewError ? <div className="text-xs text-red-600">Preview error: {previewError}</div> : null}
              {runError ? <div className="text-xs text-red-600">Calibration error: {runError}</div> : null}
            </CardContent>
          </Card>
        </aside>

        <div className="space-y-6">
          {precisionSummaries.length ? (
            <div className="space-y-4">
              <Card className="border-stone-200 bg-gradient-to-r from-stone-50 via-white to-stone-50">
                <CardHeader className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                  <div>
                    <CardTitle>Calibration Summary</CardTitle>
                    <CardDescription>Included standards, isotope precision, and per-line precision for the current preview configuration.</CardDescription>
                  </div>
                  <div className="flex flex-wrap gap-2 text-sm text-stone-600">
                    <span className="rounded-full bg-white px-3 py-1 ring-1 ring-stone-200">Standards summarized: {precisionSummaries.length}</span>
                    <span className="rounded-full bg-white px-3 py-1 ring-1 ring-stone-200">Line entries: {linePrecisionCount}</span>
                  </div>
                </CardHeader>
              </Card>
              <div className="grid gap-4">
                {precisionSummaries.map((summary) => (
                  <PrecisionCard key={summary.standard} summary={summary} />
                ))}
              </div>
            </div>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>No Standards Selected</CardTitle>
                <CardDescription>Select one or two standards to populate calibration summaries and charts.</CardDescription>
              </CardHeader>
            </Card>
          )}

          {selectedStandards.length ? (
            <>
              <div className="grid gap-6 2xl:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle>d13C Calibration</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <PlotlyChart figure={displayedWorkspace.figures["VPDB(13C)"]} className="min-h-[360px]" />
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader>
                    <CardTitle>d18O Calibration</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <PlotlyChart figure={displayedWorkspace.figures["VSMOW(18O)"]} className="min-h-[360px]" />
                  </CardContent>
                </Card>
              </div>

              <Card>
                <CardHeader>
                  <CardTitle>Calibration 3D Chart</CardTitle>
                  <CardDescription>Filtered standards in calibration space using the active color and Z-axis parameters.</CardDescription>
                </CardHeader>
                <CardContent>
                  <PlotlyChart figure={displayedWorkspace.figures.calibration_3d} className="min-h-[420px]" />
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Linearity Correction</CardTitle>
                  <CardDescription>Standards-only linearity fits built from the active precision date window and intensity basis.</CardDescription>
                </CardHeader>
                <CardContent className="grid gap-6 2xl:grid-cols-2">
                  <PlotlyChart figure={displayedWorkspace.linearity_figures.d13_raw} className="min-h-[440px]" />
                  <PlotlyChart figure={displayedWorkspace.linearity_figures.d13_corrected} className="min-h-[440px]" />
                  <PlotlyChart figure={displayedWorkspace.linearity_figures.d18_raw} className="min-h-[440px]" />
                  <PlotlyChart figure={displayedWorkspace.linearity_figures.d18_corrected} className="min-h-[440px]" />
                </CardContent>
              </Card>

              <div className="space-y-6">
                {displayedWorkspace.standard_sections.map((section) => (
                  <details key={section.standard} className="rounded-xl border border-stone-200 bg-white shadow-sm" open>
                    <summary className="cursor-pointer px-6 py-4 text-lg font-semibold text-stone-900">{section.standard}</summary>
                    <div className="space-y-6 p-6 pt-0">
                      <div className="grid gap-6">
                        <Card>
                          <CardHeader>
                            <CardTitle className="text-base">d13C Outlier Trace</CardTitle>
                          </CardHeader>
                          <CardContent>
                            <PlotlyChart figure={section.d13_figure} className="min-h-[340px]" />
                          </CardContent>
                        </Card>
                        <Card>
                          <CardHeader>
                            <CardTitle className="text-base">d18O Outlier Trace</CardTitle>
                          </CardHeader>
                          <CardContent>
                            <PlotlyChart figure={section.d18_figure} className="min-h-[340px]" />
                          </CardContent>
                        </Card>
                      </div>
                      <div className="grid gap-6 2xl:grid-cols-2">
                        <details className="rounded-xl border border-stone-200 bg-white shadow-sm">
                          <summary className="cursor-pointer px-6 py-4 text-base font-semibold text-stone-900">
                            d13C Outliers ({section.d13_outliers.length})
                          </summary>
                          <div className="px-6 pb-6">
                            <DataTable rows={section.d13_outliers} emptyLabel="No d13C outliers for this standard." />
                          </div>
                        </details>
                        <details className="rounded-xl border border-stone-200 bg-white shadow-sm">
                          <summary className="cursor-pointer px-6 py-4 text-base font-semibold text-stone-900">
                            d18O Outliers ({section.d18_outliers.length})
                          </summary>
                          <div className="px-6 pb-6">
                            <DataTable rows={section.d18_outliers} emptyLabel="No d18O outliers for this standard." />
                          </div>
                        </details>
                      </div>
                    </div>
                  </details>
                ))}
              </div>
            </>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>Calibration Preview</CardTitle>
                <CardDescription>Select one or more standards to build the calibration figures, precision summaries, and linearity diagnostics.</CardDescription>
              </CardHeader>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
