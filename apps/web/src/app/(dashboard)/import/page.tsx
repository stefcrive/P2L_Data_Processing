"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { describeSession, resolveSessionName } from "@/lib/session-label";
import type { SessionSnapshot } from "@/lib/types";
import { useSessionStore } from "@/store/use-session-store";

const WORKBOOK_EXTENSION_REGEX = /\.(xls|xlsx)$/i;
const SESSION_RECORD_METADATA_REGEX = /(^|\/)session record\/[^/]+\/metadata\.json$/i;

type FileWithRelativePath = File & { webkitRelativePath?: string };

function normalizeWorkbookName(value: string): string {
  const normalized = value.replace(/\\/g, "/");
  const basename = normalized.split("/").pop() ?? normalized;
  return basename.trim().toLowerCase();
}

function normalizeRelativePath(file: File): string {
  const candidate = (file as FileWithRelativePath).webkitRelativePath;
  return (candidate && candidate.trim().length > 0 ? candidate : file.name).replace(/\\/g, "/");
}

function stringValue(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function numberValue(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function booleanValue(value: unknown): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "string") {
    return value.toLowerCase() === "true";
  }
  return Boolean(value);
}

function formatTimestamp(value: unknown): string {
  const text = stringValue(value);
  if (!text) {
    return "n/a";
  }
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? text : parsed.toLocaleString();
}

async function detectSessionIdFromSessionRecord(folderFiles: File[]): Promise<string | null> {
  const candidates = folderFiles
    .map((file) => ({ file, relativePath: normalizeRelativePath(file).toLowerCase() }))
    .filter(({ relativePath }) => SESSION_RECORD_METADATA_REGEX.test(relativePath))
    .sort((a, b) => b.file.lastModified - a.file.lastModified);

  for (const candidate of candidates) {
    try {
      const payload = JSON.parse(await candidate.file.text()) as { session_id?: unknown };
      const sessionId = stringValue(payload?.session_id);
      if (sessionId) {
        return sessionId;
      }
    } catch {
      continue;
    }
  }
  return null;
}

function sourceWorkbookNames(session: SessionSnapshot): string[] {
  const names: string[] = [];
  for (const item of session.source_files) {
    const raw = item?.name;
    if (typeof raw !== "string") {
      continue;
    }
    const normalized = normalizeWorkbookName(raw);
    if (normalized) {
      names.push(normalized);
    }
  }
  return Array.from(new Set(names));
}

function sourceFileDisplayName(sourceFile: Record<string, unknown>, index: number): string {
  const directName = stringValue(sourceFile.name) ?? stringValue(sourceFile.raw_name);
  if (directName) {
    return directName;
  }
  const fallbackPath = stringValue(sourceFile.path) ?? stringValue(sourceFile.file_path) ?? stringValue(sourceFile.filepath);
  if (fallbackPath) {
    return fallbackPath;
  }
  return `source_file_${index + 1}`;
}

function detectMatchingSessionId(folderFiles: File[], sessions: SessionSnapshot[]): string | null {
  const folderWorkbookNames = Array.from(
    new Set(folderFiles.map((file) => normalizeWorkbookName(file.name)).filter((name) => name && WORKBOOK_EXTENSION_REGEX.test(name))),
  );
  if (folderWorkbookNames.length === 0) {
    return null;
  }
  const folderNameSet = new Set(folderWorkbookNames);
  const candidates = sessions
    .map((session) => {
      const names = sourceWorkbookNames(session);
      if (names.length === 0 || names.some((name) => !folderNameSet.has(name))) {
        return null;
      }
      const sessionNameSet = new Set(names);
      const exactMatch = sessionNameSet.size === folderNameSet.size && [...sessionNameSet].every((name) => folderNameSet.has(name));
      return { session, sourceCount: names.length, exactMatch };
    })
    .filter((candidate): candidate is { session: SessionSnapshot; sourceCount: number; exactMatch: boolean } => Boolean(candidate));

  if (candidates.length === 0) {
    return null;
  }

  candidates.sort((a, b) => {
    if (a.exactMatch !== b.exactMatch) {
      return a.exactMatch ? -1 : 1;
    }
    if (a.sourceCount !== b.sourceCount) {
      return b.sourceCount - a.sourceCount;
    }
    const updatedA = a.session.updated_at ? new Date(a.session.updated_at).getTime() : 0;
    const updatedB = b.session.updated_at ? new Date(b.session.updated_at).getTime() : 0;
    return updatedB - updatedA;
  });

  return candidates[0]?.session.session_id ?? null;
}

export default function ImportPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [browseInfo, setBrowseInfo] = useState<string | null>(null);
  const [browseError, setBrowseError] = useState<string | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);
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
  const recentSessions = useMemo(() => (sessionsQuery.data ?? []).slice(0, 5), [sessionsQuery.data]);
  const autosaveSaveDir = stringValue(autosave.save_dir);
  const autosaveEventCount = numberValue(autosave.event_count, 0);
  const autosaveResumed = booleanValue(autosave.resumed);
  const autosaveEntries = [
    { label: "Session event log", path: stringValue(autosave.log_path) },
    { label: "Session snapshot", path: stringValue(autosave.snapshot_path) },
    { label: "Session metadata", path: stringValue(autosave.meta_path) },
  ];
  const sessionSourceFiles = useMemo(
    () =>
      (activeSession?.source_files ?? []).map((item, index) => {
        const name = sourceFileDisplayName(item, index);
        return {
          key: `${name}-${index}`,
          name,
        };
      }),
    [activeSession?.source_files],
  );

  useEffect(() => {
    const input = folderInputRef.current;
    if (!input) {
      return;
    }
    input.setAttribute("webkitdirectory", "");
    input.setAttribute("directory", "");
  }, []);

  const importMutation = useMutation({
    mutationFn: () => api.importSession(files),
    onSuccess: async (result) => {
      setSessionId(result.session.session_id);
      await queryClient.invalidateQueries({ queryKey: ["session", result.session.session_id] });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
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
    mutationFn: (targetSessionId: string) => api.openSession(targetSessionId),
    onSuccess: async (result) => {
      setSessionId(result.session_id);
      setBrowseError(null);
      await queryClient.invalidateQueries({ queryKey: ["session", result.session_id] });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  const handleBrowseFolderClick = () => {
    setBrowseError(null);
    setBrowseInfo(null);
    folderInputRef.current?.click();
  };

  const handleFolderSelected = async (event: ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files ?? []);
    event.currentTarget.value = "";
    setBrowseError(null);
    setBrowseInfo(null);
    if (selected.length === 0) {
      return;
    }

    const sessionIdFromRecord = await detectSessionIdFromSessionRecord(selected);
    if (sessionIdFromRecord) {
      setBrowseInfo("Found session record in selected folder. Opening...");
      openMutation.mutate(sessionIdFromRecord);
      return;
    }

    const workbookFiles = selected.filter((file) => WORKBOOK_EXTENSION_REGEX.test(file.name));
    if (workbookFiles.length === 0) {
      setBrowseError("No Session record was found and the selected folder does not contain .xls or .xlsx files.");
      return;
    }

    const listResult = await sessionsQuery.refetch();
    const sessions = listResult.data ?? sessionsQuery.data ?? [];
    const matchedSessionId = detectMatchingSessionId(workbookFiles, sessions);
    if (!matchedSessionId) {
      setBrowseError("No saved session matches this folder. Ensure it contains a Session record subfolder or matching workbook files.");
      return;
    }

    const matchedSession = sessions.find((session) => session.session_id === matchedSessionId);
    const matchedName = matchedSession ? resolveSessionName(matchedSession) : matchedSessionId;
    setBrowseInfo(`Matched session "${matchedName}". Opening...`);
    openMutation.mutate(matchedSessionId);
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[1.1fr,0.9fr]">
      <Card>
        <CardHeader>
          <CardTitle>Import Workbooks</CardTitle>
          <CardDescription>Start a new session, add files to an open session, or reopen one of your recent sessions.</CardDescription>
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
          <div className="space-y-2 rounded-xl border border-stone-200 p-4">
            <div className="form-section-title">Current Session Files</div>
            {!sessionId ? (
              <div className="rounded-lg border border-stone-200 bg-stone-50 p-3 text-xs text-stone-600">No open session.</div>
            ) : sessionSourceFiles.length > 0 ? (
              <div className="max-h-40 space-y-1 overflow-y-auto rounded-lg border border-stone-200 bg-stone-50 p-2">
                {sessionSourceFiles.map((sourceFile) => (
                  <div key={sourceFile.key} className="truncate font-mono text-xs text-stone-700">
                    {sourceFile.name}
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-lg border border-stone-200 bg-stone-50 p-3 text-xs text-stone-600">
                Session has no loaded source files.
              </div>
            )}
          </div>
          {(importMutation.error || appendMutation.error) && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {String(importMutation.error ?? appendMutation.error)}
            </div>
          )}

          <div className="space-y-3 rounded-xl border border-stone-200 p-4">
            <div className="form-section-title">Recent Sessions</div>
            <div className="flex flex-wrap gap-3">
              <Button variant="outline" onClick={handleBrowseFolderClick} disabled={openMutation.isPending}>
                Browse Folder...
              </Button>
              <Button variant="secondary" onClick={() => sessionsQuery.refetch()} disabled={sessionsQuery.isFetching}>
                {sessionsQuery.isFetching ? "Refreshing..." : "Refresh List"}
              </Button>
            </div>
            <input ref={folderInputRef} type="file" multiple onChange={handleFolderSelected} className="hidden" />
            {browseInfo ? <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-700">{browseInfo}</div> : null}
            {browseError ? <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{browseError}</div> : null}
            {recentSessions.length > 0 ? (
              <div className="space-y-2">
                {recentSessions.map((session) => {
                  const isActive = session.session_id === sessionId;
                  const isOpening = openMutation.isPending && openMutation.variables === session.session_id;
                  return (
                    <div key={session.session_id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-stone-200 px-3 py-2">
                      <div className="text-sm text-stone-700">{describeSession(session)}</div>
                      <Button
                        variant={isActive ? "secondary" : "outline"}
                        size="sm"
                        onClick={() => openMutation.mutate(session.session_id)}
                        disabled={openMutation.isPending}
                      >
                        {isOpening ? "Opening..." : isActive ? "Open Again" : "Open"}
                      </Button>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="rounded-lg border border-stone-200 bg-stone-50 p-3 text-sm text-stone-600">No saved sessions yet.</div>
            )}
            {openMutation.error ? (
              <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{String(openMutation.error)}</div>
            ) : null}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Autosave Session</CardTitle>
          <CardDescription>Autosave stores event logs, snapshots, and metadata in a Session record folder for this workbook set.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-sm text-stone-600">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-stone-200 bg-stone-50 p-3">
              <div className="text-xs font-semibold uppercase tracking-[0.16em] text-stone-500">Active Session</div>
              <div className="mt-1 break-all font-mono text-xs text-stone-800">{sessionId ?? "none"}</div>
            </div>
            <div className="rounded-lg border border-stone-200 bg-stone-50 p-3">
              <div className="text-xs font-semibold uppercase tracking-[0.16em] text-stone-500">Autosave Status</div>
              <div className="mt-2 flex flex-wrap gap-2">
                <Badge>{autosaveEventCount} events</Badge>
                <Badge>{autosaveResumed ? "Resumed" : "New session"}</Badge>
              </div>
            </div>
          </div>
          <div className="rounded-lg border border-stone-200 bg-white p-3">
            <div className="text-xs font-semibold uppercase tracking-[0.16em] text-stone-500">Session Record Folder</div>
            <div className="mt-1 break-all font-mono text-xs text-stone-800">{autosaveSaveDir ?? "n/a"}</div>
          </div>
          <div className="space-y-2">
            {autosaveEntries.map((entry) => (
              <div key={entry.label} className="rounded-lg border border-stone-200 px-3 py-2">
                <div className="text-xs font-medium text-stone-600">{entry.label}</div>
                <div className="mt-1 break-all font-mono text-xs text-stone-800">{entry.path ?? "n/a"}</div>
              </div>
            ))}
          </div>
          <div className="grid gap-2 text-xs sm:grid-cols-2">
            <div className="rounded-lg border border-stone-200 bg-stone-50 px-3 py-2">
              Last autosave action: <span className="font-mono text-[11px] text-stone-800">{String(autosave.last_action ?? "n/a")}</span>
            </div>
            <div className="rounded-lg border border-stone-200 bg-stone-50 px-3 py-2">
              Last autosave timestamp: <span className="font-mono text-[11px] text-stone-800">{formatTimestamp(autosave.last_saved_at)}</span>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
