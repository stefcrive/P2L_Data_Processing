export type ChatRole = "user" | "assistant";

export type ToolActivity = {
  tool: string;
  status: "completed" | "error";
  summary: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
  at: string;
};

export type ScientificChatResponse = {
  message: string;
  model: string;
  tools_used: string[];
  usage: Record<string, unknown>;
  generated_at: string;
  read_only: boolean;
  processing_environment: { mode?: string; session_id?: string | null };
  tool_activity: ToolActivity[];
  reasoning_summary?: string | null;
};

export type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
  createdAt: string;
  response?: ScientificChatResponse;
};

export const SCIENTIFIC_CHAT_STORAGE_KEY = "irms.scientific-assistant.messages.v1";
export const SCIENTIFIC_CHAT_SYNC_EVENT = "irms-scientific-chat-sync";
export const MAX_CHAT_MESSAGES = 60;

export const STARTER_PROMPTS = [
  "Summarize this session, its source files, processing state, and main result ranges.",
  "Which measurements have QC, saturation, failure, or outlier flags?",
  "Compare the attached Excel workbook with this session by sample ID and report mismatches.",
];

export function makeMessage(role: ChatRole, content: string, response?: ScientificChatResponse): ChatMessage {
  return {
    id: typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`,
    role,
    content,
    createdAt: new Date().toISOString(),
    response,
  };
}

export function readStoredMessages(): ChatMessage[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(SCIENTIFIC_CHAT_STORAGE_KEY) || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => item && (item.role === "user" || item.role === "assistant") && typeof item.content === "string")
      .slice(-MAX_CHAT_MESSAGES);
  } catch {
    return [];
  }
}

export function storeMessages(messages: ChatMessage[]): void {
  if (typeof window === "undefined") return;
  const bounded = messages.slice(-MAX_CHAT_MESSAGES);
  try {
    window.localStorage.setItem(SCIENTIFIC_CHAT_STORAGE_KEY, JSON.stringify(bounded));
  } catch {
    const compact = bounded.map(({ response, ...message }) => ({
      ...message,
      response: response ? { ...response, tool_activity: [] } : undefined,
    }));
    try {
      window.localStorage.setItem(SCIENTIFIC_CHAT_STORAGE_KEY, JSON.stringify(compact));
    } catch {
      // Keep the live conversation usable when browser storage is unavailable or full.
    }
  }
  window.dispatchEvent(new CustomEvent(SCIENTIFIC_CHAT_SYNC_EVENT, { detail: bounded }));
}
