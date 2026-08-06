"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Database, FileJson, FolderOpen, Plus, RefreshCw, Table2, Upload, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { api, type UploadProgress } from "@/lib/api";
import { describeSession, resolveSessionName } from "@/lib/session-label";
import type { JsonRecord, SessionArtifactKind, SessionArtifactPayload, SessionSnapshot } from "@/lib/types";
import { useSessionStore } from "@/store/use-session-store";

const WORKBOOK_EXTENSION_REGEX = /\.(xls|xlsx)$/i;
const SESSION_STATE_FILE_REGEX = /(^|\/)irms_session_state\.json$/i;
const SESSION_RECORD_METADATA_REGEX = /(^|\/)session record\/[^/]+\/metadata\.json$/i;

type FileWithRelativePath = File & { webkitRelativePath?: string };

const SESSION_ARTIFACTS: Array<{ kind: SessionArtifactKind; label: string; description: string }> = [
  { kind: "events", label: "Event log", description: "Session activity and saved actions" },
  { kind: "snapshot", label: "Data snapshot", description: "Current processed rows" },
  { kind: "cycles", label: "Cycle snapshot", description: "Current cycle-level rows" },
  { kind: "metadata", label: "Metadata", description: "Session configuration and counts" },
  { kind: "state", label: "State file", description: "Portable recovery state" },
];

function humanizeKey(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function HumanReadableValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value == null) {
    return <span className="text-slate-400">None</span>;
  }
  if (typeof value === "boolean") {
    return <Badge>{value ? "Yes" : "No"}</Badge>;
  }
  if (typeof value === "string" || typeof value === "number") {
    return <span className="break-words text-slate-800">{String(value)}</span>;
  }
  if (Array.isArray(value)) {
    if (!value.length) {
      return <span className="text-slate-400">Empty list</span>;
    }
    return (
      <div className="space-y-1.5">
        {value.map((item, index) => (
          <div key={index} className="rounded-md border border-slate-200 bg-slate-50/70 px-3 py-2">
            <HumanReadableValue value={item} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  }
  if (typeof value === "object") {
    return (
      <dl className={depth === 0 ? "divide-y divide-slate-100" : "space-y-2"}>
        {Object.entries(value as Record<string, unknown>).map(([key, item]) => (
          <div
            key={key}
            className={depth === 0 ? "grid gap-1 py-2.5 sm:grid-cols-[180px_minmax(0,1fr)] sm:gap-4" : "grid gap-1 sm:grid-cols-[140px_minmax(0,1fr)]"}
          >
            <dt className="text-xs font-semibold text-slate-500">{humanizeKey(key)}</dt>
            <dd className="min-w-0 text-sm">
              <HumanReadableValue value={item} depth={depth + 1} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return <span className="text-slate-700">{String(value)}</span>;
}

function ArtifactViewer({ artifact }: { artifact: SessionArtifactPayload }) {
  if (artifact.format === "table") {
    return (
      <div className="overflow-auto rounded-lg border border-slate-200">
        <table className="min-w-max border-collapse text-left text-xs">
          <thead className="sticky top-0 bg-slate-50 text-slate-600">
            <tr>
              {(artifact.columns ?? []).map((column) => (
                <th key={column} className="border-b border-slate-200 px-3 py-2 font-semibold">
                  {humanizeKey(column)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {(artifact.rows ?? []).map((row, index) => (
              <tr key={index}>
                {(artifact.columns ?? []).map((column) => (
                  <td key={column} className="max-w-72 px-3 py-2 align-top text-slate-700">
                    <HumanReadableValue value={row[column]} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (artifact.format === "events") {
    const items = artifact.items ?? [];
    return (
      <ol className="space-y-2">
        {[...items].reverse().map((item: JsonRecord, index) => (
          <li key={`${String(item.timestamp ?? "")}-${index}`} className="rounded-lg border border-slate-200 bg-white px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-semibold text-slate-900">{humanizeKey(String(item.action ?? "Event"))}</span>
              <time className="font-mono text-[10px] text-slate-500">{formatTimestamp(item.timestamp)}</time>
            </div>
            {item.payload && typeof item.payload === "object" && Object.keys(item.payload as JsonRecord).length ? (
              <div className="mt-2 border-t border-slate-100 pt-2">
                <HumanReadableValue value={item.payload} />
              </div>
            ) : null}
          </li>
        ))}
      </ol>
    );
  }
  return <HumanReadableValue value={artifact.data} />;
}

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

function detectSessionStateFile(folderFiles: File[]): File | null {
  const candidates = folderFiles
    .map((file) => ({ file, relativePath: normalizeRelativePath(file).toLowerCase() }))
    .filter(({ relativePath }) => SESSION_STATE_FILE_REGEX.test(relativePath))
    .sort((a, b) => b.file.lastModified - a.file.lastModified);
  return candidates[0]?.file ?? null;
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
    const normalized = fallbackPath.replace(/\\/g, "/");
    return normalized.split("/").pop() ?? normalized;
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
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [browseInfo, setBrowseInfo] = useState<string | null>(null);
  const [browseError, setBrowseError] = useState<string | null>(null);
  const [activeArtifactKind, setActiveArtifactKind] = useState<SessionArtifactKind | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const sessionFileInputRef = useRef<HTMLInputElement | null>(null);
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
  const artifactQuery = useQuery({
    queryKey: ["session-artifact", sessionId, activeArtifactKind],
    queryFn: () => api.getSessionArtifact(sessionId!, activeArtifactKind!),
    enabled: Boolean(sessionId && activeArtifactKind),
  });
  const cancelJobMutation = useMutation({
    mutationFn: (jobId: string) => api.cancelJob(jobId),
  });

  const activeSession = sessionQuery.data;
  const autosave = activeSession?.autosave ?? {};
  const recentSessions = useMemo(() => (sessionsQuery.data ?? []).slice(0, 5), [sessionsQuery.data]);
  const autosaveEventCount = numberValue(autosave.event_count, 0);
  const autosaveResumed = booleanValue(autosave.resumed);
  const autosaveEnabled = typeof autosave.enabled === "undefined" ? Boolean(activeSession) : booleanValue(autosave.enabled);
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

  useEffect(() => {
    setActiveArtifactKind(null);
  }, [sessionId]);

  const importMutation = useMutation({
    mutationFn: () => api.importSession(files, setUploadProgress),
    onMutate: () => setUploadProgress({ loaded: 0, total: null, percent: 0, phase: "uploading" }),
    onSuccess: async (result) => {
      setSessionId(result.session.session_id);
      await queryClient.invalidateQueries({ queryKey: ["session", result.session.session_id] });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      setFiles([]);
    },
    onSettled: () => setUploadProgress(null),
  });

  const appendMutation = useMutation({
    mutationFn: () => api.appendSession(sessionId!, files, setUploadProgress),
    onMutate: () => setUploadProgress({ loaded: 0, total: null, percent: 0, phase: "uploading" }),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["session", result.session.session_id] });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      setFiles([]);
    },
    onSettled: () => setUploadProgress(null),
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

  const openSessionFileMutation = useMutation({
    mutationFn: (file: File) => api.openSessionFile(file),
    onSuccess: async (result) => {
      setSessionId(result.session_id);
      setBrowseError(null);
      await queryClient.invalidateQueries({ queryKey: ["session", result.session_id] });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  const openSessionFolderMutation = useMutation({
    mutationFn: (folderFiles: File[]) => api.openSessionFolder(folderFiles),
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

  const handleOpenSessionFileClick = () => {
    setBrowseError(null);
    setBrowseInfo(null);
    sessionFileInputRef.current?.click();
  };

  const handleSessionFileSelected = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files ?? []);
    event.currentTarget.value = "";
    setBrowseError(null);
    setBrowseInfo(null);
    const file = selected[0];
    if (!file) {
      return;
    }
    setBrowseInfo("Opening session file...");
    openSessionFileMutation.mutate(file);
  };

  const handleFolderSelected = async (event: ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files ?? []);
    event.currentTarget.value = "";
    setBrowseError(null);
    setBrowseInfo(null);
    if (selected.length === 0) {
      return;
    }

    const sessionStateFile = detectSessionStateFile(selected);
    if (sessionStateFile) {
      setBrowseInfo("Found session state file in selected folder. Opening...");
      openSessionFolderMutation.mutate(selected);
      return;
    }

    const sessionIdFromRecord = await detectSessionIdFromSessionRecord(selected);
    if (sessionIdFromRecord) {
      setBrowseInfo("Found session record in selected folder. Opening...");
      openSessionFolderMutation.mutate(selected);
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
    <div className="space-y-5">
      <PageHeader
        eyebrow="Session workspace"
        title="Import"
        description="Create a session from workbooks or reopen an existing analysis."
        actions={<span className="rounded-md border border-slate-200 bg-white px-2.5 py-1 font-mono text-[10px] text-slate-600">{recentSessions.length} recent sessions</span>}
      />

      <Card>
        <CardHeader>
          <CardTitle>Import workbooks</CardTitle>
          <CardDescription>Start a new session, add files to an open session, or reopen a saved session state file.</CardDescription>
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
              <Upload className="h-4 w-4" />
              {importMutation.isPending ? "Importing..." : "Create session"}
            </Button>
            <Button
              variant="outline"
              onClick={() => appendMutation.mutate()}
              disabled={!sessionId || files.length === 0 || appendMutation.isPending}
            >
              <Plus className="h-4 w-4" />
              {appendMutation.isPending ? "Adding..." : "Add files"}
            </Button>
          </div>
          {uploadProgress ? (
            <div className="space-y-2" aria-live="polite">
              <div className="flex items-center justify-between text-xs text-stone-600">
                <span>
                  {uploadProgress.phase === "uploading"
                    ? "Uploading workbooks"
                    : uploadProgress.message || "Processing workbooks on the server"}
                </span>
                <span>{uploadProgress.percent == null ? "Working..." : `${uploadProgress.percent}%`}</span>
              </div>
              <div
                className="h-2 overflow-hidden rounded-full bg-stone-200"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={uploadProgress.percent ?? undefined}
              >
                <div
                  className={`h-full rounded-full bg-stone-900 transition-[width] duration-200 ${uploadProgress.percent == null ? "w-1/3 animate-pulse" : ""}`}
                  style={uploadProgress.percent == null ? undefined : { width: `${uploadProgress.percent}%` }}
                />
              </div>
              {uploadProgress.jobId && uploadProgress.cancellable ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => cancelJobMutation.mutate(uploadProgress.jobId!)}
                  disabled={cancelJobMutation.isPending}
                >
                  {cancelJobMutation.isPending ? "Cancelling..." : "Cancel operation"}
                </Button>
              ) : null}
            </div>
          ) : null}
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
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
          <CardTitle>Session record</CardTitle>
            <CardDescription>Review recovery artifacts and the workbooks attached to the active session.</CardDescription>
          </div>
          <div className="flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs font-medium text-slate-700">
            <span
              className={`h-2.5 w-2.5 rounded-full ${autosaveEnabled ? "bg-emerald-500 shadow-[0_0_0_3px_rgba(16,185,129,0.14)]" : "bg-red-500 shadow-[0_0_0_3px_rgba(239,68,68,0.12)]"}`}
              aria-hidden="true"
            />
            Autosave {autosaveEnabled ? "on" : "off"}
          </div>
        </CardHeader>
        <CardContent className="space-y-4 text-sm text-stone-600">
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 pb-4">
            <Badge>{autosaveEventCount} events</Badge>
            <Badge>{autosaveResumed ? "Resumed" : "New session"}</Badge>
            <span className="font-mono text-[10px] text-slate-500">{sessionId ?? "No active session"}</span>
            <span className="ml-auto text-xs text-slate-500">Last saved {formatTimestamp(autosave.last_saved_at)}</span>
          </div>

          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Record views</div>
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
              {SESSION_ARTIFACTS.map((artifact) => {
                const Icon =
                  artifact.kind === "events"
                    ? Activity
                    : artifact.kind === "metadata"
                      ? Database
                      : artifact.kind === "state"
                        ? FileJson
                        : Table2;
                return (
                  <button
                    key={artifact.kind}
                    type="button"
                    disabled={!sessionId}
                    onClick={() => setActiveArtifactKind(artifact.kind)}
                    className="group flex min-w-0 items-start gap-3 rounded-lg border border-slate-200 bg-white px-3 py-3 text-left transition hover:border-blue-300 hover:bg-blue-50/50 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <span className="rounded-md bg-slate-100 p-1.5 text-slate-600 group-hover:bg-blue-100 group-hover:text-blue-700">
                      <Icon className="h-4 w-4" />
                    </span>
                    <span className="min-w-0">
                      <span className="block text-xs font-semibold text-slate-900">{artifact.label}</span>
                      <span className="mt-0.5 block text-[11px] leading-snug text-slate-500">{artifact.description}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="border-t border-slate-200 pt-4">
            <div className="mb-2 flex items-center justify-between gap-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Current session files</div>
              <span className="font-mono text-[10px] text-slate-500">{sessionSourceFiles.length} files</span>
            </div>
            {!sessionId ? (
              <div className="rounded-lg border border-dashed border-slate-300 p-3 text-xs text-slate-500">No open session.</div>
            ) : sessionSourceFiles.length > 0 ? (
              <div className="grid gap-1.5 sm:grid-cols-2 xl:grid-cols-3">
                {sessionSourceFiles.map((sourceFile) => (
                  <div key={sourceFile.key} className="truncate rounded-md border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-700" title={sourceFile.name}>
                    {sourceFile.name}
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-lg border border-dashed border-slate-300 p-3 text-xs text-slate-500">
                Session has no loaded source files.
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <CardTitle>Recent sessions</CardTitle>
            <CardDescription>Open a known session or recover one from a session folder or state file.</CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={handleBrowseFolderClick}
              disabled={openMutation.isPending || openSessionFileMutation.isPending || openSessionFolderMutation.isPending}
            >
              <FolderOpen className="h-4 w-4" />
              Browse folder
            </Button>
            <Button
              variant="outline"
              onClick={handleOpenSessionFileClick}
              disabled={openSessionFileMutation.isPending || openSessionFolderMutation.isPending}
            >
              <FileJson className="h-4 w-4" />
              Open state file
            </Button>
            <Button variant="secondary" onClick={() => sessionsQuery.refetch()} disabled={sessionsQuery.isFetching}>
              <RefreshCw className="h-4 w-4" />
              {sessionsQuery.isFetching ? "Refreshing..." : "Refresh"}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <input ref={folderInputRef} type="file" multiple onChange={handleFolderSelected} className="hidden" />
          <input ref={sessionFileInputRef} type="file" accept=".json" onChange={handleSessionFileSelected} className="hidden" />
          {browseInfo ? <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-700">{browseInfo}</div> : null}
          {browseError ? <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{browseError}</div> : null}
          {recentSessions.length > 0 ? (
            <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
              {recentSessions.map((session) => {
                const isActive = session.session_id === sessionId;
                const isOpening = openMutation.isPending && openMutation.variables === session.session_id;
                return (
                  <div key={session.session_id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-2.5">
                    <div className="text-sm text-stone-700">{describeSession(session)}</div>
                    <Button
                      variant={isActive ? "secondary" : "outline"}
                      size="sm"
                      onClick={() => openMutation.mutate(session.session_id)}
                      disabled={openMutation.isPending}
                    >
                      {isOpening ? "Opening..." : isActive ? "Open again" : "Open"}
                    </Button>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-stone-300 p-3 text-sm text-stone-500">No saved sessions yet.</div>
          )}
          {openMutation.error || openSessionFileMutation.error || openSessionFolderMutation.error ? (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {String(openMutation.error ?? openSessionFileMutation.error ?? openSessionFolderMutation.error)}
            </div>
          ) : null}
        </CardContent>
      </Card>

      {activeArtifactKind ? (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-slate-950/45 p-3 pt-6 sm:p-6" onClick={() => setActiveArtifactKind(null)}>
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Session record viewer"
            className="flex max-h-[calc(100vh-3rem)] w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-slate-300 bg-white shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-4 py-3">
              <div>
                <div className="text-base font-semibold text-slate-950">
                  {artifactQuery.data?.label ?? SESSION_ARTIFACTS.find((item) => item.kind === activeArtifactKind)?.label}
                </div>
                <div className="mt-0.5 text-xs text-slate-500">
                  {artifactQuery.data?.row_count != null ? `${artifactQuery.data.row_count.toLocaleString()} records` : "Session record"}
                  {artifactQuery.data?.truncated ? " · showing a preview" : ""}
                </div>
              </div>
              <Button variant="outline" size="sm" onClick={() => setActiveArtifactKind(null)}>
                <X className="h-4 w-4" />
                Close
              </Button>
            </div>
            <div className="min-h-0 overflow-auto bg-slate-50/60 p-4">
              {artifactQuery.isLoading ? <div className="text-sm text-slate-500">Loading session record…</div> : null}
              {artifactQuery.error ? (
                <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                  Unable to load this record: {String(artifactQuery.error)}
                </div>
              ) : null}
              {artifactQuery.data ? <ArtifactViewer artifact={artifactQuery.data} /> : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
