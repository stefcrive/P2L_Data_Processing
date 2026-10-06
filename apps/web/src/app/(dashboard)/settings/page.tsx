"use client";

import { useTranslation } from "@/components/layout/language-provider";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Database, Eye, EyeOff, KeyRound, Plus, Save, ShieldCheck, Trash2 } from "lucide-react";
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
  const tr = useTranslation();
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
  const apiKeyQuery = useQuery({
    queryKey: ["openai-api-key-status"],
    queryFn: () => api.getOpenAIApiKeyStatus(),
  });
  const [drafts, setDrafts] = useState<StandardDraft[]>([]);
  const [newDraft, setNewDraft] = useState<StandardDraft>({ standard: "", d13: "", d18: "", source: "standards database" });
  const [apiKey, setApiKey] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [apiKeyMessage, setApiKeyMessage] = useState<string | null>(null);
  const [apiKeyError, setApiKeyError] = useState<string | null>(null);
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
        throw new Error("Enter a standard name and valid δ¹³C and δ¹⁸O values.");
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

  const apiKeyMutation = useMutation({
    mutationFn: () => api.setOpenAIApiKey(apiKey),
    onSuccess: async () => {
      setApiKey("");
      setShowApiKey(false);
      setApiKeyError(null);
      setApiKeyMessage("OpenAI API key saved for this and future app runs.");
      await queryClient.invalidateQueries({ queryKey: ["openai-api-key-status"] });
    },
    onError: (mutationError) => {
      setApiKeyMessage(null);
      setApiKeyError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const clearApiKeyMutation = useMutation({
    mutationFn: () => api.clearOpenAIApiKey(),
    onSuccess: async (status) => {
      setApiKey("");
      setShowApiKey(false);
      setApiKeyError(null);
      setApiKeyMessage(status.source === "environment"
        ? "Saved user key cleared. A backend environment key remains active."
        : "Saved OpenAI API key cleared.");
      await queryClient.invalidateQueries({ queryKey: ["openai-api-key-status"] });
    },
    onError: (mutationError) => {
      setApiKeyMessage(null);
      setApiKeyError(mutationError instanceof Error ? mutationError.message : String(mutationError));
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
        eyebrow={tr("Workspace configuration")}
        title={tr("Settings")}
        description={tr("Manage session recovery behavior and the official isotope references used by calibration.")}
        actions={
          <div className="flex items-center gap-2 font-mono text-[11px] text-slate-500">
            <Database className="h-4 w-4 text-blue-700" aria-hidden="true" />
            {drafts.length}{tr(" standards · ")}{valueCount}{tr("values")}</div>
        }
      />

      <Card>
        <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2"><KeyRound className="h-4 w-4 text-blue-700" />{tr(" OpenAI connection")}</CardTitle>
            <CardDescription>{tr("Configure the server-side credential used by the scientific results assistant.")}</CardDescription>
          </div>
          <div className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-[10px] font-semibold uppercase ${
            apiKeyQuery.data?.configured
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : "border-amber-200 bg-amber-50 text-amber-900"
          }`}>
            <span className={`h-2 w-2 rounded-full ${apiKeyQuery.data?.configured ? "bg-emerald-500" : "bg-amber-500"}`} aria-hidden="true" />
            {tr(apiKeyQuery.isLoading ? "Checking" : apiKeyQuery.data?.configured ? "Configured" : "Not configured")}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="max-w-3xl">
            <label htmlFor="openai-api-key" className="form-label">{tr("OpenAI API key")}</label>
            <div className="mt-1.5 flex flex-col gap-2 sm:flex-row">
              <div className="relative min-w-0 flex-1">
                <input
                  id="openai-api-key"
                  type={showApiKey ? "text" : "password"}
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder={tr(apiKeyQuery.data?.configured ? "Enter a replacement key" : "sk-…")}
                  autoComplete="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  className="form-control pr-10 font-mono"
                />
                <button
                  type="button"
                  onClick={() => setShowApiKey((value) => !value)}
                  className="absolute inset-y-0 right-0 grid w-10 place-items-center text-slate-400 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400"
                  aria-label={tr(showApiKey ? "Hide API key" : "Show API key")}
                >
                  {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              <Button
                type="button"
                onClick={() => apiKeyMutation.mutate()}
                disabled={apiKey.trim().length < 20 || apiKeyMutation.isPending || clearApiKeyMutation.isPending}
              >
                <Save className="h-3.5 w-3.5" />
                {tr(apiKeyMutation.isPending ? "Saving" : apiKeyQuery.data?.configured ? "Replace key" : "Save key")}
              </Button>
              {apiKeyQuery.data?.source === "application_memory" || apiKeyQuery.data?.source === "user_environment" ? (
                <Button type="button" variant="outline" onClick={() => clearApiKeyMutation.mutate()} disabled={apiKeyMutation.isPending || clearApiKeyMutation.isPending}>
                  <Trash2 className="h-3.5 w-3.5" />{tr("Clear")}</Button>
              ) : null}
            </div>
          </div>

          <div className="flex max-w-3xl items-start gap-2 rounded-md border border-blue-200 bg-blue-50/70 px-3 py-2 text-xs leading-5 text-blue-950">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-blue-700" />
            <span>{tr("On Windows, the key is saved as your user-level OPENAI_API_KEY environment variable so it remains available after restarts. It is never saved in browser storage, application files, chat history, or API responses.")}{tr(apiKeyQuery.data?.source === "environment" ? " The current key comes from the backend process environment." : "")}
            </span>
          </div>
          {apiKeyQuery.error ? <div className="text-xs text-red-700" role="alert">{tr("Could not read OpenAI key status: ")}{tr(String(apiKeyQuery.error))}</div> : null}
          {apiKeyMessage ? <div className="text-xs font-medium text-emerald-700" role="status">{tr(apiKeyMessage)}</div> : null}
          {apiKeyError ? <div className="text-xs text-red-700" role="alert">{tr(apiKeyError)}</div> : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <CardTitle>{tr("Session autosave")}</CardTitle>
            <CardDescription>{tr("Keep the active session record, event history, and portable state file current.")}</CardDescription>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 text-xs font-medium text-slate-700">
              <span className={`h-2.5 w-2.5 rounded-full ${autosaveEnabled ? "bg-emerald-500" : "bg-red-500"}`} aria-hidden="true" />
              {tr(sessionId ? (autosaveEnabled ? "Enabled" : "Disabled") : "No active session")}
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={autosaveEnabled}
              aria-label={tr("Enable session autosave")}
              disabled={!sessionId || sessionQuery.isLoading || autosaveMutation.isPending}
              onClick={() => autosaveMutation.mutate(!autosaveEnabled)}
              className={`relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-45 ${
                autosaveEnabled ? "bg-blue-700" : "bg-slate-300"
              }`}
            >
              <span
                className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${
                  autosaveEnabled ? "translate-x-5" : "translate-x-0"
                }`}
              />
            </button>
          </div>
        </CardHeader>
        {!sessionId ? <CardContent className="pt-0 text-xs text-slate-500">{tr("Open or create a session to change autosave.")}</CardContent> : null}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{tr("Official standard values")}</CardTitle>
          <CardDescription>{tr("Values are stored in the application database and used by calibration calculations.")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {valuesQuery.isLoading ? <div className="text-sm text-slate-500">{tr("Loading standards…")}</div> : null}
          {valuesQuery.error ? (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{tr("Failed to load standards:")}{tr(String(valuesQuery.error))}
            </div>
          ) : null}

          {!valuesQuery.isLoading ? (
            <div className="overflow-x-auto rounded-lg border border-slate-200">
              <table className="min-w-[560px] w-full table-fixed border-collapse text-left text-xs">
                <colgroup>
                  <col className="w-[32%]" />
                  <col className="w-[25%]" />
                  <col className="w-[25%]" />
                  <col className="w-[18%]" />
                </colgroup>
                <thead className="bg-slate-50 font-mono text-[10px] uppercase text-slate-500">
                  <tr>
                    <th className="h-8 px-3 font-medium">{tr("Standard")}</th>
                    <th className="h-8 px-3 text-center font-medium">{tr("δ¹³C · VPDB")}</th>
                    <th className="h-8 px-3 text-center font-medium">{tr("δ¹⁸O · VSMOW")}</th>
                    <th className="h-8 px-3 text-center font-medium">{tr("Actions")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 bg-white">
                  {drafts.map((draft, index) => (
                    <tr key={draft.standard} className="hover:bg-slate-50/70">
                      <td className="px-3 py-1 font-mono font-semibold text-slate-900">{tr(draft.standard)}</td>
                      <td className="px-3 py-1">
                        <input
                          className="form-control mx-auto h-8 max-w-36 text-right font-mono tabular-nums"
                          type="number"
                          step="0.001"
                          value={draft.d13}
                          onChange={(event) => updateDraft(index, "d13", event.target.value)}
                          aria-label={tr(`${draft.standard} delta 13 C value`)}
                        />
                      </td>
                      <td className="px-3 py-1">
                        <input
                          className="form-control mx-auto h-8 max-w-36 text-right font-mono tabular-nums"
                          type="number"
                          step="0.001"
                          value={draft.d18}
                          onChange={(event) => updateDraft(index, "d18", event.target.value)}
                          aria-label={tr(`${draft.standard} delta 18 O value`)}
                        />
                      </td>
                      <td className="px-3 py-1">
                        <div className="flex justify-center gap-1">
                          <IconButton label={tr(`Save ${draft.standard}`)} onClick={() => saveMutation.mutate(draft)} disabled={busy}>
                            <Save className="h-3.5 w-3.5" />
                          </IconButton>
                          <IconButton label={tr(`Remove ${draft.standard}`)} onClick={() => deleteMutation.mutate(draft.standard)} disabled={busy}>
                            <Trash2 className="h-3.5 w-3.5" />
                          </IconButton>
                        </div>
                      </td>
                    </tr>
                  ))}
                  <tr className="bg-blue-50/40">
                    <td className="px-3 py-1">
                      <input
                        className="form-control h-8 font-mono uppercase"
                        placeholder={tr("Standard")}
                        value={newDraft.standard}
                        onChange={(event) => setNewDraft((current) => ({ ...current, standard: event.target.value.toUpperCase() }))}
                        aria-label={tr("New standard name")}
                      />
                    </td>
                    <td className="px-3 py-1">
                      <input
                        className="form-control mx-auto h-8 max-w-36 text-right font-mono"
                        type="number"
                        step="0.001"
                        placeholder={tr("0.000")}
                        value={newDraft.d13}
                        onChange={(event) => setNewDraft((current) => ({ ...current, d13: event.target.value }))}
                        aria-label={tr("New standard delta 13 C value")}
                      />
                    </td>
                    <td className="px-3 py-1">
                      <input
                        className="form-control mx-auto h-8 max-w-36 text-right font-mono"
                        type="number"
                        step="0.001"
                        placeholder={tr("0.000")}
                        value={newDraft.d18}
                        onChange={(event) => setNewDraft((current) => ({ ...current, d18: event.target.value }))}
                        aria-label={tr("New standard delta 18 O value")}
                      />
                    </td>
                    <td className="px-3 py-1 text-center">
                      <Button size="sm" onClick={() => saveMutation.mutate(newDraft)} disabled={busy}>
                        <Plus className="h-3.5 w-3.5" />{tr("Add")}</Button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          ) : null}

          {message ? <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800" role="status">{tr(message)}</div> : null}
          {error ? <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{tr(error)}</div> : null}
          <p className="text-xs text-slate-500">{tr("Changes affect future calibration previews and runs. Use traceable source names when values come from a certificate or publication.")}</p>
        </CardContent>
      </Card>
    </div>
  );
}
