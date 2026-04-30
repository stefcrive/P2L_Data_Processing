"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, RotateCcw, Save, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { IconButton } from "@/components/ui/icon-button";
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
    <header className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-stone-200 bg-white px-4 py-3 shadow-sm">
      <div className="min-w-0">
        <div className="text-xs font-semibold uppercase tracking-normal text-stone-500">Active Session</div>
        <div className="mt-1 break-all text-base font-semibold leading-tight text-stone-900 sm:text-lg">{displayName}</div>
        {displaySessionId ? <div className="mt-2 font-mono text-xs text-stone-500">{displaySessionId}</div> : null}
      </div>
      <div className="flex flex-wrap items-center justify-end gap-2">
        <div className="flex flex-wrap justify-end gap-1.5">
          <Badge>{data?.row_count ?? 0} rows</Badge>
          <Badge>{data?.cycles_row_count ?? 0} cycle rows</Badge>
          <Badge>{(data?.source_files?.length ?? 0).toString()} source files</Badge>
          <Badge>{String(data?.autosave?.event_count ?? 0)} autosave events</Badge>
          <IconButton
            label={saveMutation.isPending ? "Saving session" : "Save session"}
            onClick={() => saveMutation.mutate()}
            disabled={!sessionId || saveMutation.isPending}
          >
            <Save className="h-4 w-4" />
          </IconButton>
          <IconButton
            label={closeMutation.isPending ? "Closing session" : "Close session"}
            onClick={() => closeMutation.mutate()}
            disabled={!sessionId || closeMutation.isPending}
          >
            <X className="h-4 w-4" />
          </IconButton>
          <IconButton
            label={discardMutation.isPending ? "Discarding changes" : confirmDiscard ? "Confirm discard" : "Discard changes"}
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
            <Ban className="h-4 w-4" />
          </IconButton>
          {confirmDiscard ? (
            <IconButton label="Cancel discard" variant="secondary" onClick={() => setConfirmDiscard(false)} disabled={discardMutation.isPending}>
              <RotateCcw className="h-4 w-4" />
            </IconButton>
          ) : null}
        </div>
        {actionError ? <div className="basis-full text-right text-xs text-red-700">{String(actionError)}</div> : null}
      </div>
    </header>
  );
}
