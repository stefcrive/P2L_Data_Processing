"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { SessionSnapshot } from "@/lib/types";
import { useSessionStore } from "@/store/use-session-store";

function describeSession(session: SessionSnapshot) {
  const updated = session.updated_at ? new Date(session.updated_at).toLocaleString() : "unknown update";
  return `${session.session_id} | ${session.row_count} rows | ${session.source_files.length} files | ${updated}`;
}

export default function ImportPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [selectedOpenSessionId, setSelectedOpenSessionId] = useState<string>("");
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const queryClient = useQueryClient();
  const setSessionId = useSessionStore((state) => state.setSessionId);
  const sessionId = useSessionStore((state) => state.sessionId);

  const sessionQuery = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => api.getSession(sessionId!),
    enabled: Boolean(sessionId),
  });

  const sessionsQuery = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api.listSessions(),
  });

  const activeSession = sessionQuery.data;
  const autosave = activeSession?.autosave ?? {};
  const hasOpenSelection = useMemo(() => Boolean(selectedOpenSessionId.trim()), [selectedOpenSessionId]);

  const importMutation = useMutation({
    mutationFn: () => api.importSession(files),
    onSuccess: async (result) => {
      setSessionId(result.session.session_id);
      await queryClient.invalidateQueries({ queryKey: ["session", result.session.session_id] });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      setSelectedOpenSessionId(result.session.session_id);
      setFiles([]);
    },
  });

  const appendMutation = useMutation({
    mutationFn: () => api.appendSession(sessionId!, files),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["session", result.session.session_id] });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      setFiles([]);
    },
  });

  const openMutation = useMutation({
    mutationFn: () => api.openSession(selectedOpenSessionId),
    onSuccess: async (result) => {
      setSessionId(result.session_id);
      await queryClient.invalidateQueries({ queryKey: ["session", result.session_id] });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  const saveMutation = useMutation({
    mutationFn: () => api.saveSession(sessionId!),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["session", result.session_id] });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  const closeMutation = useMutation({
    mutationFn: () => api.closeSession(sessionId!),
    onSuccess: async () => {
      setSessionId(null);
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  const discardMutation = useMutation({
    mutationFn: () => api.discardSession(sessionId!),
    onSuccess: async (result) => {
      queryClient.removeQueries({ queryKey: ["session", result.session_id] });
      setSessionId(null);
      setConfirmDiscard(false);
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  return (
    <div className="grid gap-6 lg:grid-cols-[1.1fr,0.9fr]">
      <Card>
        <CardHeader>
          <CardTitle>Import Workbooks</CardTitle>
          <CardDescription>
            Start a new session, add files to an open session, or manage session lifecycle controls.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <input
            type="file"
            multiple
            accept=".xls,.xlsx"
            onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
            className="form-control block w-full text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-stone-900 file:px-3 file:py-1.5 file:text-sm file:font-semibold file:text-white hover:file:bg-stone-800"
          />
          <div className="flex flex-wrap gap-3">
            <Button onClick={() => importMutation.mutate()} disabled={files.length === 0 || importMutation.isPending}>
              {importMutation.isPending ? "Importing..." : "Create Session (Open)"}
            </Button>
            <Button
              variant="outline"
              onClick={() => appendMutation.mutate()}
              disabled={!sessionId || files.length === 0 || appendMutation.isPending}
            >
              {appendMutation.isPending ? "Adding..." : "Add Selected Files to Session"}
            </Button>
          </div>
          {files.length > 0 ? (
            <div className="rounded-lg border border-stone-200 bg-stone-50 p-3 text-xs text-stone-700">
              {files.length} file(s) selected: {files.map((file) => file.name).join(", ")}
            </div>
          ) : null}
          {(importMutation.error || appendMutation.error) && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {String(importMutation.error ?? appendMutation.error)}
            </div>
          )}

          <div className="space-y-3 rounded-xl border border-stone-200 p-4">
            <div className="form-section-title">Open Existing Session</div>
            <div className="flex flex-wrap gap-3">
              <select
                className="form-control min-w-[340px] flex-1 text-sm"
                value={selectedOpenSessionId}
                onChange={(event) => setSelectedOpenSessionId(event.target.value)}
              >
                <option value="">Select a saved session...</option>
                {(sessionsQuery.data ?? []).map((session) => (
                  <option key={session.session_id} value={session.session_id}>
                    {describeSession(session)}
                  </option>
                ))}
              </select>
              <Button
                variant="outline"
                onClick={() => openMutation.mutate()}
                disabled={!hasOpenSelection || openMutation.isPending}
              >
                {openMutation.isPending ? "Opening..." : "Open Session"}
              </Button>
              <Button variant="secondary" onClick={() => sessionsQuery.refetch()} disabled={sessionsQuery.isFetching}>
                {sessionsQuery.isFetching ? "Refreshing..." : "Refresh List"}
              </Button>
            </div>
            {openMutation.error ? (
              <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{String(openMutation.error)}</div>
            ) : null}
          </div>

          <div className="space-y-3 rounded-xl border border-stone-200 p-4">
            <div className="form-section-title">Session Actions</div>
            <div className="flex flex-wrap gap-3">
              <Button variant="outline" onClick={() => saveMutation.mutate()} disabled={!sessionId || saveMutation.isPending}>
                {saveMutation.isPending ? "Saving..." : "Save Session Now"}
              </Button>
              <Button variant="outline" onClick={() => closeMutation.mutate()} disabled={!sessionId || closeMutation.isPending}>
                {closeMutation.isPending ? "Closing..." : "Close Session"}
              </Button>
              <Button
                variant={confirmDiscard ? "default" : "outline"}
                onClick={() => {
                  if (!confirmDiscard) {
                    setConfirmDiscard(true);
                    return;
                  }
                  discardMutation.mutate();
                }}
                disabled={!sessionId || discardMutation.isPending}
              >
                {discardMutation.isPending ? "Discarding..." : confirmDiscard ? "Confirm Discard All Changes" : "Discard All Changes"}
              </Button>
              {confirmDiscard ? (
                <Button variant="secondary" onClick={() => setConfirmDiscard(false)} disabled={discardMutation.isPending}>
                  Cancel
                </Button>
              ) : null}
            </div>
            {(saveMutation.error || closeMutation.error || discardMutation.error) ? (
              <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                {String(saveMutation.error ?? closeMutation.error ?? discardMutation.error)}
              </div>
            ) : null}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Autosave Session</CardTitle>
          <CardDescription>Server-managed autosave writes event logs, dataframe snapshots, and metadata on every persisted action.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-stone-600">
          <p>
            Active session: <span className="font-mono text-xs">{sessionId ?? "none"}</span>
          </p>
          <p>Autosave events written: {String(autosave.event_count ?? 0)}</p>
          <p>
            Session event log: <span className="font-mono text-xs">{String(autosave.log_path ?? "n/a")}</span>
          </p>
          <p>
            Session snapshot: <span className="font-mono text-xs">{String(autosave.snapshot_path ?? "n/a")}</span>
          </p>
          <p>
            Session metadata: <span className="font-mono text-xs">{String(autosave.meta_path ?? "n/a")}</span>
          </p>
          <p>
            Last autosave action: <span className="font-mono text-xs">{String(autosave.last_action ?? "n/a")}</span>
          </p>
          <p>
            Last autosave timestamp: <span className="font-mono text-xs">{String(autosave.last_saved_at ?? "n/a")}</span>
          </p>
          <p>
            Resumed session: <span className="font-mono text-xs">{String(autosave.resumed ?? false)}</span>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
