"use client";
import { usePathname, useSearchParams } from "next/navigation";
import { MetrologyWorkspace } from "./workspace";

// The layout owns the workspace so changing sections does not destroy open data.
export function MetrologyWorkspaceRoute() {
  const path = usePathname(), query = useSearchParams();
  const section=path.split("/")[2] || "overview";
  if(!["overview","results","qualification","history","materials","interventions","reports","audit"].includes(section))return null;
  return <MetrologyWorkspace initialSection={section} initialSessionId={query.get("session") ?? undefined} initialRunId={query.get("run") ?? undefined} initialQualificationId={query.get("qualification") ?? undefined} createSession={query.get("new") === "1"} newContext={query.get("workflow") === "qualification" ? "qualification" : "routine"}/>;
}
