"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Database, RotateCcw, Save, X } from "lucide-react";
import { useEffect, useState } from "react";

import { IconButton } from "@/components/ui/icon-button";
import { Tooltip } from "@/components/ui/tooltip";
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

  useEffect(() => setConfirmDiscard(false), [sessionId]);

  const actionError = saveMutation.error ?? closeMutation.error ?? discardMutation.error;
  const displayName = data ? resolveSessionName(data) : sessionId ?? "No session loaded";
  const displaySessionId = data?.session_id ?? sessionId;
  const sessionSummary = sessionId
    ? `${data?.row_count ?? 0} rows · ${data?.cycles_row_count ?? 0} cycles · ${data?.source_files?.length ?? 0} files`
    : "Import workbooks to begin";

  return (
    <div className="border-t border-slate-200/80 bg-slate-50/85">
      <div className="mx-auto flex min-h-10 max-w-[1680px] items-center gap-2 px-3 sm:px-4 lg:px-6">
        <span className={`h-2 w-2 shrink-0 rounded-full ${sessionId ? "bg-emerald-500" : "bg-slate-300"}`} aria-hidden="true" />
        <div className="min-w-0 flex-1 sm:flex sm:items-baseline sm:gap-2">
          <span className="block truncate text-xs font-semibold text-slate-800">{displayName}</span>
          <span className="hidden truncate font-mono text-[10px] text-slate-500 sm:block">{sessionSummary}</span>
        </div>
        {displaySessionId ? (
          <Tooltip label={`Session ID: ${displaySessionId}`} align="end">
            <button type="button" className="hidden h-7 items-center gap-1 rounded-md px-2 font-mono text-[10px] text-slate-500 hover:bg-slate-200/70 sm:inline-flex">
              <Database className="h-3.5 w-3.5" aria-hidden="true" />
              ID
            </button>
          </Tooltip>
        ) : null}
        <div className="flex items-center gap-1">
          <IconButton
            label={saveMutation.isPending ? "Saving session" : "Save session"}
            onClick={() => saveMutation.mutate()}
            disabled={!sessionId || saveMutation.isPending}
          >
            <Save className="h-3.5 w-3.5" />
          </IconButton>
          <IconButton
            label={closeMutation.isPending ? "Closing session" : "Close session"}
            onClick={() => closeMutation.mutate()}
            disabled={!sessionId || closeMutation.isPending}
          >
            <X className="h-3.5 w-3.5" />
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
            <Ban className="h-3.5 w-3.5" />
          </IconButton>
          {confirmDiscard ? (
            <IconButton label="Cancel discard" variant="secondary" onClick={() => setConfirmDiscard(false)} disabled={discardMutation.isPending}>
              <RotateCcw className="h-3.5 w-3.5" />
            </IconButton>
          ) : null}
        </div>
        {actionError ? <span className="sr-only" role="alert">{String(actionError)}</span> : null}
      </div>
    </div>
  );
}
