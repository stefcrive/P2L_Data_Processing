import type {
  CalibrationConfig,
  CalibrationOfficialValue,
  CalibrationWorkspace,
  ChartBundle,
  ClientOutputDuplicateCheckResponse,
  ClientOutputPreviewResponse,
  CycleDiagnosticsPayload,
  EditAction,
  ExportRequest,
  ImportNamingWorkspace,
  ImportParsingConfig,
  ImportPreviewResponse,
  ImportResult,
  LinearityConfig,
  ProcessingConfig,
  ProcessingLinearityPreviewData,
  ProcessingWorkspace,
  SessionArtifactKind,
  SessionArtifactPayload,
  SessionSnapshot,
  SpeciesSection,
} from "@/lib/types";

export type UploadProgress = {
  loaded: number;
  total: number | null;
  percent: number | null;
  phase: "uploading" | "processing";
  message?: string;
  jobId?: string;
  cancellable?: boolean;
};

export type JobState = "queued" | "running" | "succeeded" | "failed" | "cancel_requested" | "cancelled";

export type JobSnapshot<TResult = Record<string, unknown>> = {
  job_id: string;
  kind: string;
  state: JobState;
  progress: number;
  phase: string;
  message: string;
  session_id: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  result: TResult | null;
  error: string | null;
  revision: number;
  cancellable: boolean;
};

const API_BASE =
  (
    process.env.NEXT_PUBLIC_IRMS_API_URL ||
    process.env.NEXT_PUBLIC_API_BASE_URL ||
    "/api/irms"
  ).replace(/\/+$/, "");

type DiagnosticsParams = {
  color_param?: string;
  z_axis?: string | null;
  identifier_filter?: string[];
  d13_range?: [number, number] | null;
  d18_range?: [number, number] | null;
};

type LinearityOptions = {
  summaryOnly?: boolean;
};

type FileWithRelativePath = File & { webkitRelativePath?: string };

function endpoint(path: string, params?: URLSearchParams): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const query = params?.toString();
  return `${API_BASE}${normalizedPath}${query ? `?${query}` : ""}`;
}

function appendSpeciesSectionParams(params: URLSearchParams, speciesSections?: string[]): URLSearchParams {
  if (speciesSections !== undefined) {
    params.set("include_all_species_sections", "false");
    for (const section of speciesSections) {
      params.append("species_section", section);
    }
  }
  return params;
}

async function parseError(response: Response): Promise<string> {
  const fallback = `${response.status} ${response.statusText}`.trim();
  try {
    const payload = await response.json();
    if (typeof payload?.detail === "string") {
      return payload.detail;
    }
    if (payload?.detail?.errors && Array.isArray(payload.detail.errors)) {
      return payload.detail.errors.join("; ");
    }
    return JSON.stringify(payload);
  } catch {
    return fallback;
  }
}

async function requestJson<T>(path: string, init?: RequestInit, params?: URLSearchParams): Promise<T> {
  const headers = new Headers(init?.headers);
  const hasBody = init?.body !== undefined && init.body !== null;
  if (hasBody && !(init?.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const url = endpoint(path, params);
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    const detail = error instanceof Error && error.message ? ` (${error.message})` : "";
    throw new Error(`Could not reach the IRMS API through ${url}.${detail}`);
  }
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return response.json() as Promise<T>;
}

function formDataFromFiles(files: File[], includeRelativePaths = false): FormData {
  const form = new FormData();
  for (const file of files) {
    const relativePath = (file as FileWithRelativePath).webkitRelativePath;
    const filename = includeRelativePaths && relativePath && relativePath.trim().length > 0 ? relativePath : file.name;
    form.append("files", file, filename);
  }
  return form;
}

function formDataFromFilesAndParsingConfig(
  files: File[],
  parsingConfig?: ImportParsingConfig | null,
): FormData {
  const form = formDataFromFiles(files);
  if (parsingConfig) {
    form.append("parsing_config", JSON.stringify(parsingConfig));
  }
  return form;
}

function rejectEmptyFiles(files: File[]): void {
  const emptyFiles = files.filter((file) => file.size === 0).map((file) => file.name);
  if (emptyFiles.length > 0) {
    throw new Error(
      `The following workbook file is empty (0 bytes): ${emptyFiles.join(", ")}. Download or save it locally, then select it again.`,
    );
  }
}

function errorFromPayload(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== "object") return fallback;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const errors = (detail as { errors?: unknown }).errors;
    if (Array.isArray(errors)) {
      const messages = errors.filter((error): error is string => typeof error === "string" && error.length > 0);
      if (messages.length > 0) return messages.join("; ");
    }
  }
  return fallback;
}

function requestFormJsonWithProgress<T>(
  path: string,
  form: FormData,
  onProgress?: (progress: UploadProgress) => void,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", endpoint(path));
    request.responseType = "json";
    request.upload.addEventListener("progress", (event) => {
      const total = event.lengthComputable ? event.total : null;
      onProgress?.({
        loaded: event.loaded,
        total,
        percent: total && total > 0 ? Math.min(100, Math.round((event.loaded / total) * 100)) : null,
        phase: "uploading",
      });
    });
    request.upload.addEventListener("load", () => {
      onProgress?.({ loaded: 0, total: null, percent: null, phase: "processing" });
    });
    request.addEventListener("load", () => {
      const payload = request.response ?? (() => {
        try {
          return JSON.parse(request.responseText);
        } catch {
          return null;
        }
      })();
      if (request.status >= 200 && request.status < 300) {
        resolve(payload as T);
        return;
      }
      const detail = errorFromPayload(payload, `${request.status} ${request.statusText}`.trim());
      reject(new Error(detail || "IRMS API request failed"));
    });
    request.addEventListener("error", () => reject(new Error(`Could not reach the IRMS API through ${endpoint(path)}.`)));
    request.addEventListener("abort", () => reject(new DOMException("Upload aborted", "AbortError")));
    request.send(form);
  });
}

function waitForJob<TResult>(
  initial: JobSnapshot<TResult>,
  onProgress?: (snapshot: JobSnapshot<TResult>) => void,
): Promise<TResult> {
  return new Promise<TResult>((resolve, reject) => {
    let settled = false;
    let polling = false;
    let source: EventSource | null = null;

    const finish = (snapshot: JobSnapshot<TResult>) => {
      if (settled) return;
      onProgress?.(snapshot);
      if (snapshot.state === "succeeded") {
        settled = true;
        source?.close();
        if (snapshot.result) {
          resolve(snapshot.result);
        } else {
          reject(new Error("Background job completed without a result"));
        }
      } else if (snapshot.state === "failed" || snapshot.state === "cancelled") {
        settled = true;
        source?.close();
        reject(new Error(snapshot.error || (snapshot.state === "cancelled" ? "Operation cancelled" : "Background job failed")));
      }
    };

    const parseEvent = (event: Event) => {
      if (!(event instanceof MessageEvent) || typeof event.data !== "string") return;
      try {
        finish(JSON.parse(event.data) as JobSnapshot<TResult>);
      } catch {
        // A malformed event will be superseded by the next revision or polling fallback.
      }
    };

    const startPolling = () => {
      if (polling || settled) return;
      polling = true;
      source?.close();
      const poll = async () => {
        while (!settled) {
          try {
            const snapshot = await requestJson<JobSnapshot<TResult>>(`/jobs/${encodeURIComponent(initial.job_id)}`);
            finish(snapshot);
          } catch (error) {
            if (!settled) {
              settled = true;
              reject(error);
            }
            return;
          }
          if (!settled) {
            await new Promise((resume) => window.setTimeout(resume, 500));
          }
        }
      };
      void poll();
    };

    onProgress?.(initial);
    if (initial.state === "succeeded" || initial.state === "failed" || initial.state === "cancelled") {
      finish(initial);
      return;
    }
    if (typeof EventSource === "undefined") {
      startPolling();
      return;
    }
    source = new EventSource(endpoint(`/jobs/${encodeURIComponent(initial.job_id)}/events`));
    source.addEventListener("progress", parseEvent);
    source.addEventListener("complete", parseEvent);
    source.addEventListener("cancelled", parseEvent);
    source.addEventListener("error", (event) => {
      if (event instanceof MessageEvent && event.data) {
        parseEvent(event);
        return;
      }
      startPolling();
    });
  });
}

async function submitJsonJob<TResult>(
  path: string,
  payload: unknown,
  onProgress?: (snapshot: JobSnapshot<TResult>) => void,
): Promise<TResult> {
  const job = await requestJson<JobSnapshot<TResult>>(path, { method: "POST", body: body(payload) });
  return waitForJob(job, onProgress);
}

function body(payload: unknown): string {
  return JSON.stringify(payload);
}

async function exportDataset(
  sessionId: string,
  payload: ExportRequest,
  onProgress?: (snapshot: JobSnapshot<{ filename: string; download_url: string }>) => void,
): Promise<{ blob: Blob; filename: string }> {
  const job = await requestJson<JobSnapshot<{ filename: string; download_url: string }>>(
    `/sessions/${encodeURIComponent(sessionId)}/exports/dataset/jobs`,
    {
      method: "POST",
      body: body(payload),
    },
  );
  const result = await waitForJob(job, onProgress);
  const response = await fetch(endpoint(result.download_url || `/jobs/${encodeURIComponent(job.job_id)}/download`));
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
  return {
    blob: await response.blob(),
    filename: filenameMatch?.[1] ?? result.filename ?? "irms_export.xlsx",
  };
}

export const api = {
  listSessions: () => requestJson<SessionSnapshot[]>("/sessions"),
  getSession: (sessionId: string) => requestJson<SessionSnapshot>(`/sessions/${encodeURIComponent(sessionId)}`),
  getSessionArtifact: (sessionId: string, kind: SessionArtifactKind) =>
    requestJson<SessionArtifactPayload>(
      `/sessions/${encodeURIComponent(sessionId)}/artifacts/${encodeURIComponent(kind)}`,
    ),
  updateAutosave: (sessionId: string, enabled: boolean) =>
    requestJson<SessionSnapshot>(`/sessions/${encodeURIComponent(sessionId)}/autosave`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
      headers: { "Content-Type": "application/json" },
    }),
  getJob: (jobId: string) => requestJson<JobSnapshot>(`/jobs/${encodeURIComponent(jobId)}`),
  cancelJob: (jobId: string) => requestJson<JobSnapshot>(`/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" }),
  previewImport: (files: File[]) => {
    rejectEmptyFiles(files);
    return requestJson<ImportPreviewResponse>("/sessions/import/preview", {
      method: "POST",
      body: formDataFromFiles(files),
    });
  },
  importSession: async (
    files: File[],
    parsingConfig?: ImportParsingConfig | null,
    onProgress?: (progress: UploadProgress) => void,
  ) => {
    rejectEmptyFiles(files);
    const job = await requestFormJsonWithProgress<JobSnapshot<ImportResult>>(
      "/sessions/import/jobs",
      formDataFromFilesAndParsingConfig(files, parsingConfig),
      onProgress,
    );
    return waitForJob(job, (snapshot) =>
      onProgress?.({
        loaded: 0,
        total: null,
        percent: Math.round(snapshot.progress),
        phase: "processing",
        message: snapshot.message,
        jobId: snapshot.job_id,
        cancellable: snapshot.cancellable,
      }),
    );
  },
  openSessionFile: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestJson<SessionSnapshot>("/sessions/open-file", {
      method: "POST",
      body: form,
    });
  },
  openSessionFolder: (files: File[]) =>
    requestJson<SessionSnapshot>("/sessions/open-folder", {
      method: "POST",
      body: formDataFromFiles(files, true),
    }),
  appendSession: async (
    sessionId: string,
    files: File[],
    parsingConfig?: ImportParsingConfig | null,
    onProgress?: (progress: UploadProgress) => void,
  ) => {
    rejectEmptyFiles(files);
    const job = await requestFormJsonWithProgress<JobSnapshot<ImportResult>>(
      `/sessions/${encodeURIComponent(sessionId)}/append/jobs`,
      formDataFromFilesAndParsingConfig(files, parsingConfig),
      onProgress,
    );
    return waitForJob(job, (snapshot) =>
      onProgress?.({
        loaded: 0,
        total: null,
        percent: Math.round(snapshot.progress),
        phase: "processing",
        message: snapshot.message,
        jobId: snapshot.job_id,
        cancellable: snapshot.cancellable,
      }),
    );
  },
  openSession: (sessionId: string) =>
    requestJson<SessionSnapshot>(`/sessions/${encodeURIComponent(sessionId)}/open`, { method: "POST" }),
  excludeSessionFile: (sessionId: string, fileIndex: number) =>
    requestJson<ImportResult>(
      `/sessions/${encodeURIComponent(sessionId)}/exclude-file?file_index=${fileIndex}`,
      { method: "POST" },
    ),
  getImportNaming: (sessionId: string) =>
    requestJson<ImportNamingWorkspace>(`/sessions/${encodeURIComponent(sessionId)}/import/naming`),
  setImportNaming: (
    sessionId: string,
    update: Pick<ImportNamingWorkspace, "species_name_map" | "identifier1_name_map">,
  ) =>
    requestJson<ImportNamingWorkspace>(`/sessions/${encodeURIComponent(sessionId)}/import/naming`, {
      method: "POST",
      body: body(update),
    }),
  saveSession: (sessionId: string) =>
    requestJson<SessionSnapshot>(`/sessions/${encodeURIComponent(sessionId)}/save`, { method: "POST" }),
  closeSession: (sessionId: string) =>
    requestJson<SessionSnapshot>(`/sessions/${encodeURIComponent(sessionId)}/close`, { method: "POST" }),
  discardSession: (sessionId: string) =>
    requestJson<{ session_id: string; deleted: boolean }>(`/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),

  getDiagnostics: (sessionId: string, options: DiagnosticsParams = {}) => {
    const params = new URLSearchParams();
    if (options.color_param) params.set("color_param", options.color_param);
    if (options.z_axis) params.set("z_axis", options.z_axis);
    for (const identifier of options.identifier_filter ?? []) {
      params.append("identifier_filter", identifier);
    }
    if (options.d13_range) {
      params.set("d13_min", String(options.d13_range[0]));
      params.set("d13_max", String(options.d13_range[1]));
    }
    if (options.d18_range) {
      params.set("d18_min", String(options.d18_range[0]));
      params.set("d18_max", String(options.d18_range[1]));
    }
    return requestJson<ChartBundle>(`/sessions/${encodeURIComponent(sessionId)}/diagnostics`, undefined, params);
  },

  getCalibrationWorkspace: (sessionId: string) =>
    requestJson<CalibrationWorkspace>(`/sessions/${encodeURIComponent(sessionId)}/calibration/workspace`),
  previewCalibrationWorkspace: (sessionId: string, config: CalibrationConfig) =>
    requestJson<CalibrationWorkspace>(`/sessions/${encodeURIComponent(sessionId)}/calibration/workspace`, {
      method: "POST",
      body: body(config),
    }),
  setCalibrationLinearity: (
    sessionId: string,
    linearity: LinearityConfig,
    selectedStandards?: string[],
    options: LinearityOptions = {},
  ) => {
    const params = new URLSearchParams();
    if (options.summaryOnly) {
      params.set("summary_only", "true");
    }
    return requestJson<CalibrationWorkspace>(
      `/sessions/${encodeURIComponent(sessionId)}/calibration/linearity`,
      {
        method: "POST",
        body: body({ linearity, selected_standards: selectedStandards }),
      },
      params,
    );
  },
  runCalibration: (
    sessionId: string,
    config: CalibrationConfig,
    onProgress?: (snapshot: JobSnapshot<SessionSnapshot>) => void,
  ) =>
    submitJsonJob<SessionSnapshot>(`/sessions/${encodeURIComponent(sessionId)}/calibration/run/jobs`, config, onProgress),
  resetCalibration: (sessionId: string) =>
    requestJson<SessionSnapshot>(`/sessions/${encodeURIComponent(sessionId)}/calibration/reset`, { method: "POST" }),
  listOfficialStandardValues: () => requestJson<CalibrationOfficialValue[]>("/standards/official-values"),
  upsertOfficialStandardValue: (payload: { standard: string; isotopic_value_type: string; value: number; source?: string | null }) =>
    requestJson<CalibrationOfficialValue>("/standards/official-values", {
      method: "POST",
      body: body(payload),
    }),
  deleteOfficialStandard: (standard: string) =>
    requestJson<{ standard: string; deleted_rows: number }>(`/standards/official-values/${encodeURIComponent(standard)}`, {
      method: "DELETE",
    }),

  getProcessingWorkspace: (sessionId: string, speciesSections?: string[], signal?: AbortSignal) =>
    requestJson<ProcessingWorkspace>(
      `/sessions/${encodeURIComponent(sessionId)}/processing/workspace`,
      { signal },
      appendSpeciesSectionParams(new URLSearchParams(), speciesSections),
    ),
  getProcessingSpeciesSection: (sessionId: string, species: string, signal?: AbortSignal) => {
    const params = new URLSearchParams({ species });
    return requestJson<SpeciesSection>(
      `/sessions/${encodeURIComponent(sessionId)}/processing/species-section`,
      { signal },
      params,
    );
  },
  getProcessingLinearityPreviewData: (sessionId: string) =>
    requestJson<ProcessingLinearityPreviewData>(
      `/sessions/${encodeURIComponent(sessionId)}/processing/linearity-preview-data`,
    ),
  setProcessingConfig: (sessionId: string, config: ProcessingConfig, speciesSections?: string[]) =>
    requestJson<ProcessingWorkspace>(
      `/sessions/${encodeURIComponent(sessionId)}/processing/config`,
      {
        method: "POST",
        body: body(config),
      },
      appendSpeciesSectionParams(new URLSearchParams(), speciesSections),
    ),
  editProcessing: (sessionId: string, payload: EditAction, speciesSections?: string[]) =>
    requestJson<ProcessingWorkspace>(
      `/sessions/${encodeURIComponent(sessionId)}/processing/edit`,
      {
        method: "POST",
        body: body(payload),
      },
      appendSpeciesSectionParams(new URLSearchParams(), speciesSections),
    ),
  setProcessingConfigJob: (
    sessionId: string,
    config: ProcessingConfig,
    onProgress?: (snapshot: JobSnapshot<ProcessingWorkspace>) => void,
  ) =>
    submitJsonJob<ProcessingWorkspace>(
      `/sessions/${encodeURIComponent(sessionId)}/processing/config/jobs`,
      config,
      onProgress,
    ),
  editProcessingBatch: (sessionId: string, edits: EditAction[], speciesSections?: string[]) =>
    requestJson<ProcessingWorkspace>(
      `/sessions/${encodeURIComponent(sessionId)}/processing/edits`,
      {
        method: "POST",
        body: body({ edits }),
      },
      appendSpeciesSectionParams(new URLSearchParams(), speciesSections),
    ),
  editProcessingBatchJob: (
    sessionId: string,
    edits: EditAction[],
    onProgress?: (snapshot: JobSnapshot<ProcessingWorkspace>) => void,
  ) =>
    submitJsonJob<ProcessingWorkspace>(
      `/sessions/${encodeURIComponent(sessionId)}/processing/edits/jobs`,
      { edits },
      onProgress,
    ),
  removeProcessingCalibration: (sessionId: string, speciesSections?: string[]) =>
    requestJson<ProcessingWorkspace>(
      `/sessions/${encodeURIComponent(sessionId)}/processing/calibration/remove`,
      { method: "POST" },
      appendSpeciesSectionParams(new URLSearchParams(), speciesSections),
    ),
  getProcessingCycleDiagnostics: (sessionId: string, payload: { target: { row_label: string; isotope_key: "d13C" | "d18O" } }) =>
    requestJson<CycleDiagnosticsPayload>(`/sessions/${encodeURIComponent(sessionId)}/processing/cycle-diagnostics`, {
      method: "POST",
      body: body(payload),
    }),
  checkClientOutputDuplicates: (sessionId: string, payload: ExportRequest) =>
    requestJson<ClientOutputDuplicateCheckResponse>(
      `/sessions/${encodeURIComponent(sessionId)}/exports/client-output/duplicates`,
      {
        method: "POST",
        body: body(payload),
      },
    ),
  previewClientOutput: (sessionId: string, payload: ExportRequest, signal?: AbortSignal) =>
    requestJson<ClientOutputPreviewResponse>(
      `/sessions/${encodeURIComponent(sessionId)}/exports/client-output/preview`,
      {
        method: "POST",
        body: body(payload),
        signal,
      },
    ),
  exportDataset,
};
