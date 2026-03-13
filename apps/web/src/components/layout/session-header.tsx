"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useSessionStore } from "@/store/use-session-store";

export function SessionHeader() {
  const sessionId = useSessionStore((state) => state.sessionId);
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
  return (
    <header className="flex flex-wrap items-center justify-between gap-4 rounded-3xl border border-stone-200 bg-white/95 px-7 py-6 shadow-sm">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.22em] text-stone-500">Active Session</div>
        <div className="mt-2 break-all text-[2.05rem] font-semibold leading-none text-stone-900">{sessionId ?? "No session loaded"}</div>
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
        </div>
        {saveMutation.error ? <div className="text-xs text-red-700">{String(saveMutation.error)}</div> : null}
      </div>
    </header>
  );
}
