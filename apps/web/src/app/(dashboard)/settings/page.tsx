"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Database, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { IconButton } from "@/components/ui/icon-button";
import { PageHeader } from "@/components/ui/page-header";
import { api } from "@/lib/api";
import type { CalibrationOfficialValue } from "@/lib/types";
import { useSessionStore } from "@/store/use-session-store";

const D13_TYPE = "VPDB(13C)";
const D18_TYPE = "VSMOW(18O)";

type StandardDraft = {
  standard: string;
  d13: string;
  d18: string;
  source: string;
};

function buildDrafts(values: CalibrationOfficialValue[]): StandardDraft[] {
  const byStandard = new Map<string, StandardDraft>();
  for (const item of values) {
    const standard = String(item.standard ?? "").trim().toUpperCase();
    if (!standard) continue;
    const row = byStandard.get(standard) ?? { standard, d13: "", d18: "", source: item.source ?? "standards database" };
    if (item.isotopic_value_type === D13_TYPE) row.d13 = item.value == null ? "" : String(item.value);
    if (item.isotopic_value_type === D18_TYPE) row.d18 = item.value == null ? "" : String(item.value);
    row.source = item.source ?? row.source;
    byStandard.set(standard, row);
  }
  return Array.from(byStandard.values()).sort((a, b) => a.standard.localeCompare(b.standard));
}

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const sessionId = useSessionStore((state) => state.sessionId);
  const sessionQuery = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => api.getSession(sessionId!),
    enabled: Boolean(sessionId),
  });
  const valuesQuery = useQuery({
    queryKey: ["official-standard-values"],
    queryFn: () => api.listOfficialStandardValues(),
  });
  const [drafts, setDrafts] = useState<StandardDraft[]>([]);
  const [newDraft, setNewDraft] = useState<StandardDraft>({ standard: "", d13: "", d18: "", source: "standards database" });
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (valuesQuery.data) setDrafts(buildDrafts(valuesQuery.data));
  }, [valuesQuery.data]);

  const saveMutation = useMutation({
    mutationFn: async (draft: StandardDraft) => {
      const standard = draft.standard.trim().toUpperCase();
      const d13 = Number(draft.d13);
      const d18 = Number(draft.d18);
      if (!standard || !Number.isFinite(d13) || !Number.isFinite(d18)) {
        throw new Error("Enter a standard name and valid d13C and d18O values.");
      }
      await Promise.all([
        api.upsertOfficialStandardValue({ standard, isotopic_value_type: D13_TYPE, value: d13, source: draft.source.trim() || null }),
        api.upsertOfficialStandardValue({ standard, isotopic_value_type: D18_TYPE, value: d18, source: draft.source.trim() || null }),
      ]);
      return standard;
    },
    onSuccess: async (standard) => {
      setError(null);
      setMessage(`${standard} saved.`);
      setNewDraft({ standard: "", d13: "", d18: "", source: "standards database" });
      await queryClient.invalidateQueries({ queryKey: ["official-standard-values"] });
    },
    onError: (mutationError) => {
      setMessage(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (standard: string) => api.deleteOfficialStandard(standard),
    onSuccess: async (_, standard) => {
      setError(null);
      setMessage(`${standard} removed.`);
      await queryClient.invalidateQueries({ queryKey: ["official-standard-values"] });
    },
    onError: (mutationError) => {
      setMessage(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const autosaveMutation = useMutation({
    mutationFn: (enabled: boolean) => api.updateAutosave(sessionId!, enabled),
    onSuccess: async (session) => {
      setError(null);
      setMessage(`Autosave ${session.autosave.enabled === false ? "disabled" : "enabled"}.`);
      await queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (mutationError) => {
      setMessage(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const autosaveEnabled = sessionQuery.data ? sessionQuery.data.autosave.enabled !== false : false;
  const busy = saveMutation.isPending || deleteMutation.isPending;
  const valueCount = useMemo(() => (valuesQuery.data ?? []).filter((item) => item.value != null).length, [valuesQuery.data]);

  function updateDraft(index: number, field: keyof StandardDraft, value: string) {
    setDrafts((current) => current.map((draft, draftIndex) => (draftIndex === index ? { ...draft, [field]: value } : draft)));
  }

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Workspace configuration"
        title="Settings"
        description="Manage session recovery behavior and the official isotope references used by calibration."
        actions={
          <div className="flex items-center gap-2 font-mono text-[11px] text-slate-500">
            <Database className="h-4 w-4 text-blue-700" aria-hidden="true" />
            {drafts.length} standards · {valueCount} values
          </div>
        }
      />

      <Card>
        <CardHeader className="gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <CardTitle>Session autosave</CardTitle>
            <CardDescription>Keep the active session record, event history, and portable state file current.</CardDescription>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 text-xs font-medium text-slate-700">
              <span className={`h-2.5 w-2.5 rounded-full ${autosaveEnabled ? "bg-emerald-500" : "bg-red-500"}`} aria-hidden="true" />
              {sessionId ? (autosaveEnabled ? "Enabled" : "Disabled") : "No active session"}
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={autosaveEnabled}
              aria-label="Enable session autosave"
              disabled={!sessionId || sessionQuery.isLoading || autosaveMutation.isPending}
              onClick={() => autosaveMutation.mutate(!autosaveEnabled)}
              className={`relative h-6 w-11 rounded-full transition focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-45 ${
                autosaveEnabled ? "bg-blue-700" : "bg-slate-300"
              }`}
            >
              <span
                className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${
                  autosaveEnabled ? "translate-x-5" : "translate-x-0.5"
                }`}
              />
            </button>
          </div>
        </CardHeader>
        {!sessionId ? <CardContent className="pt-0 text-xs text-slate-500">Open or create a session to change autosave.</CardContent> : null}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Official standard values</CardTitle>
          <CardDescription>Values are stored in the application database and used by calibration calculations.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {valuesQuery.isLoading ? <div className="text-sm text-slate-500">Loading standards…</div> : null}
          {valuesQuery.error ? (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              Failed to load standards: {String(valuesQuery.error)}
            </div>
          ) : null}

          {!valuesQuery.isLoading ? (
            <div className="overflow-x-auto rounded-lg border border-slate-200">
              <table className="min-w-[760px] w-full border-collapse text-left text-xs">
                <thead className="bg-slate-50 font-mono text-[10px] uppercase text-slate-500">
                  <tr>
                    <th className="h-8 px-3 font-medium">Standard</th>
                    <th className="h-8 px-3 text-right font-medium">d13C · VPDB</th>
                    <th className="h-8 px-3 text-right font-medium">d18O · VSMOW</th>
                    <th className="h-8 px-3 font-medium">Source</th>
                    <th className="h-8 w-20 px-3 text-right font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 bg-white">
                  {drafts.map((draft, index) => (
                    <tr key={draft.standard} className="hover:bg-slate-50/70">
                      <td className="px-3 py-2 font-mono font-semibold text-slate-900">{draft.standard}</td>
                      <td className="px-3 py-2">
                        <input
                          className="form-control ml-auto max-w-36 text-right font-mono tabular-nums"
                          type="number"
                          step="0.001"
                          value={draft.d13}
                          onChange={(event) => updateDraft(index, "d13", event.target.value)}
                          aria-label={`${draft.standard} d13C value`}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <input
                          className="form-control ml-auto max-w-36 text-right font-mono tabular-nums"
                          type="number"
                          step="0.001"
                          value={draft.d18}
                          onChange={(event) => updateDraft(index, "d18", event.target.value)}
                          aria-label={`${draft.standard} d18O value`}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <input
                          className="form-control"
                          value={draft.source}
                          onChange={(event) => updateDraft(index, "source", event.target.value)}
                          aria-label={`${draft.standard} source`}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex justify-end gap-1">
                          <IconButton label={`Save ${draft.standard}`} onClick={() => saveMutation.mutate(draft)} disabled={busy}>
                            <Save className="h-3.5 w-3.5" />
                          </IconButton>
                          <IconButton label={`Remove ${draft.standard}`} onClick={() => deleteMutation.mutate(draft.standard)} disabled={busy}>
                            <Trash2 className="h-3.5 w-3.5" />
                          </IconButton>
                        </div>
                      </td>
                    </tr>
                  ))}
                  <tr className="bg-blue-50/40">
                    <td className="px-3 py-2">
                      <input
                        className="form-control font-mono uppercase"
                        placeholder="Standard"
                        value={newDraft.standard}
                        onChange={(event) => setNewDraft((current) => ({ ...current, standard: event.target.value.toUpperCase() }))}
                        aria-label="New standard name"
                      />
                    </td>
                    <td className="px-3 py-2">
                      <input
                        className="form-control text-right font-mono"
                        type="number"
                        step="0.001"
                        placeholder="0.000"
                        value={newDraft.d13}
                        onChange={(event) => setNewDraft((current) => ({ ...current, d13: event.target.value }))}
                        aria-label="New standard d13C value"
                      />
                    </td>
                    <td className="px-3 py-2">
                      <input
                        className="form-control text-right font-mono"
                        type="number"
                        step="0.001"
                        placeholder="0.000"
                        value={newDraft.d18}
                        onChange={(event) => setNewDraft((current) => ({ ...current, d18: event.target.value }))}
                        aria-label="New standard d18O value"
                      />
                    </td>
                    <td className="px-3 py-2">
                      <input
                        className="form-control"
                        value={newDraft.source}
                        onChange={(event) => setNewDraft((current) => ({ ...current, source: event.target.value }))}
                        aria-label="New standard source"
                      />
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Button size="sm" onClick={() => saveMutation.mutate(newDraft)} disabled={busy}>
                        <Plus className="h-3.5 w-3.5" />
                        Add
                      </Button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          ) : null}

          {message ? <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800" role="status">{message}</div> : null}
          {error ? <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{error}</div> : null}
          <p className="text-xs text-slate-500">
            Changes affect future calibration previews and runs. Use traceable source names when values come from a certificate or publication.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
