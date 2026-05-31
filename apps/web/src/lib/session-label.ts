import type { SessionSnapshot } from "@/lib/types";

function stringValue(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function sourceName(sourceFile: Record<string, unknown>): string | null {
  return (
    stringValue(sourceFile.name) ??
    stringValue(sourceFile.raw_name) ??
    stringValue(sourceFile.path) ??
    stringValue(sourceFile.file_path) ??
    stringValue(sourceFile.filepath)
  );
}

function formatTimestamp(value: string | null | undefined): string | null {
  const text = stringValue(value);
  if (!text) {
    return null;
  }
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? text : parsed.toLocaleString();
}

export function resolveSessionName(session: Pick<SessionSnapshot, "session_id" | "session_name" | "source_files">): string {
  const explicitName = stringValue(session.session_name);
  if (explicitName) {
    return explicitName;
  }

  const names = session.source_files.map(sourceName).filter((name): name is string => Boolean(name));
  if (names.length === 1) {
    return names[0];
  }
  if (names.length > 1) {
    return `${names[0]} + ${names.length - 1}`;
  }
  return session.session_id;
}

export function describeSession(session: SessionSnapshot): string {
  const parts = [resolveSessionName(session)];
  parts.push(`${session.row_count ?? 0} rows`);
  if ((session.cycles_row_count ?? 0) > 0) {
    parts.push(`${session.cycles_row_count} cycle rows`);
  }
  const updated = formatTimestamp(session.updated_at);
  if (updated) {
    parts.push(`updated ${updated}`);
  }
  return parts.join(" - ");
}
