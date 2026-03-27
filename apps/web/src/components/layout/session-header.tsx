"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { resolveSessionName } from "@/lib/session-label";
import { useSessionStore } from "@/store/use-session-store";

export function SessionHeader() {
  const sessionId = useSessionStore((state) => state.sessionId);
  const setSessionId = useSessionStore((state) => state.setSessionId);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => api.getSession(sessionId!),
    enabled: Boolean(sessionId),
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
    onSuccess: async (result) => {
      queryClient.removeQueries({ queryKey: ["session", result.session_id] });
      setConfirmDiscard(false);
      setSessionId(null);
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
  const discardMutation = useMutation({
    mutationFn: () => api.discardSession(sessionId!),
    onSuccess: async (result) => {
      queryClient.removeQueries({ queryKey: ["session", result.session_id] });
      setConfirmDiscard(false);
      setSessionId(null);
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  useEffect(() => {
    setConfirmDiscard(false);
  }, [sessionId]);

  const actionError = saveMutation.error ?? closeMutation.error ?? discardMutation.error;
  const displayName = data ? resolveSessionName(data) : sessionId ?? "No session loaded";
  const displaySessionId = data?.session_id ?? sessionId;

  return (
    <header className="flex flex-wrap items-center justify-between gap-4 rounded-3xl border border-stone-200 bg-white/95 px-7 py-6 shadow-sm">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.22em] text-stone-500">Active Session</div>
        <div className="mt-2 break-all text-[2.05rem] font-semibold leading-none text-stone-900">{displayName}</div>
        {displaySessionId ? <div className="mt-2 font-mono text-xs text-stone-500">{displaySessionId}</div> : null}
      </div>
      <div className="flex flex-col items-end gap-2">
        <div className="flex flex-wrap justify-end gap-2">
          <Badge>{data?.row_count ?? 0} rows</Badge>
          <Badge>{data?.cycles_row_count ?? 0} cycle rows</Badge>
          <Badge>{(data?.source_files?.length ?? 0).toString()} source files</Badge>
          <Badge>{String(data?.autosave?.event_count ?? 0)} autosave events</Badge>
          <Button
            variant="outline"
            size="sm"
            onClick={() => saveMutation.mutate()}
            disabled={!sessionId || saveMutation.isPending}
          >
            {saveMutation.isPending ? "Saving..." : "Save Session"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => closeMutation.mutate()}
            disabled={!sessionId || closeMutation.isPending}
          >
            {closeMutation.isPending ? "Closing..." : "Close Session"}
          </Button>
          <Button
            variant={confirmDiscard ? "default" : "outline"}
            size="sm"
            onClick={() => {
              if (!confirmDiscard) {
                setConfirmDiscard(true);
                return;
              }
              discardMutation.mutate();
            }}
            disabled={!sessionId || discardMutation.isPending}
          >
            {discardMutation.isPending ? "Discarding..." : confirmDiscard ? "Confirm Discard" : "Discard Changes"}
          </Button>
          {confirmDiscard ? (
            <Button size="sm" variant="secondary" onClick={() => setConfirmDiscard(false)} disabled={discardMutation.isPending}>
              Cancel
            </Button>
          ) : null}
        </div>
        {actionError ? <div className="text-xs text-red-700">{String(actionError)}</div> : null}
      </div>
    </header>
  );
}
