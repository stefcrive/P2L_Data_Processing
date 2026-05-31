import type {
  CalibrationConfig,
  CalibrationOfficialValue,
  CalibrationWorkspace,
  ChartBundle,
  ClientOutputDuplicateCheckResponse,
  CycleDiagnosticsPayload,
  EditAction,
  ExportRequest,
  ImportResult,
  LinearityConfig,
  ProcessingConfig,
  ProcessingWorkspace,
  SessionSnapshot,
} from "@/lib/types";

export type UploadResponse = {
  job_id: string;
  task_id?: string;
  status: string;
  result?: unknown;
};

const API_BASE =
  process.env.NEXT_PUBLIC_IRMS_API_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  "http://localhost:8000";

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

function endpoint(path: string, params?: URLSearchParams): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const query = params?.toString();
  return `${API_BASE}${normalizedPath}${query ? `?${query}` : ""}`;
}

function appendSpeciesSectionParams(params: URLSearchParams, speciesSections?: string[]): URLSearchParams {
  if (speciesSections && speciesSections.length > 0) {
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

  const response = await fetch(endpoint(path, params), {
    ...init,
    headers,
  });
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return response.json() as Promise<T>;
}

function formDataFromFiles(files: File[]): FormData {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  return form;
}

function body(payload: unknown): string {
  return JSON.stringify(payload);
}

export async function uploadIRMSFile(file: File) {
  const form = new FormData();
  form.append("file", file);
  return requestJson<UploadResponse>("/v1/irms/process", { method: "POST", body: form });
}

export async function fetchJobStatus(task_id: string) {
  return requestJson<{ task_id: string; state: string; result?: unknown }>(`/v1/jobs/${encodeURIComponent(task_id)}`);
}

async function exportDataset(sessionId: string, payload: ExportRequest): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(endpoint(`/sessions/${encodeURIComponent(sessionId)}/exports/dataset`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body(payload),
  });
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
  return {
    blob: await response.blob(),
    filename: filenameMatch?.[1] ?? "irms_export.xlsx",
  };
}

export const api = {
  listSessions: () => requestJson<SessionSnapshot[]>("/sessions"),
  getSession: (sessionId: string) => requestJson<SessionSnapshot>(`/sessions/${encodeURIComponent(sessionId)}`),
  importSession: (files: File[]) =>
    requestJson<ImportResult>("/sessions/import", {
      method: "POST",
      body: formDataFromFiles(files),
    }),
  appendSession: (sessionId: string, files: File[]) =>
    requestJson<ImportResult>(`/sessions/${encodeURIComponent(sessionId)}/append`, {
      method: "POST",
      body: formDataFromFiles(files),
    }),
  openSession: (sessionId: string) =>
    requestJson<SessionSnapshot>(`/sessions/${encodeURIComponent(sessionId)}/open`, { method: "POST" }),
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
  runCalibration: (sessionId: string, config: CalibrationConfig) =>
    requestJson<SessionSnapshot>(`/sessions/${encodeURIComponent(sessionId)}/calibration/run`, {
      method: "POST",
      body: body(config),
    }),
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

  getProcessingWorkspace: (sessionId: string, speciesSections?: string[]) =>
    requestJson<ProcessingWorkspace>(
      `/sessions/${encodeURIComponent(sessionId)}/processing/workspace`,
      undefined,
      appendSpeciesSectionParams(new URLSearchParams(), speciesSections),
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
  exportDataset,
};
