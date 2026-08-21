"use client";

import { CheckCircle2, ChevronDown, CircleAlert, Copy, Database } from "lucide-react";
import { useState } from "react";

import type { ScientificChatResponse } from "@/components/chat/chat-model";
import { formatScientificText } from "@/lib/scientific-notation";

function sourceLabel(result: Record<string, unknown>): string | null {
  const source = result.source;
  if (!source || typeof source !== "object") return null;
  const value = source as Record<string, unknown>;
  return formatScientificText([value.label, value.session_id, value.record_version].filter(Boolean).join(" · "));
}

export function SourceEvidenceDisclosure({ response }: { response: ScientificChatResponse }) {
  const [open, setOpen] = useState(false);
  if (!response.tool_activity.length) return null;
  const completed = response.tool_activity.filter((item) => item.status === "completed").length;
  return (
    <div className="mt-4 overflow-hidden rounded-md border border-slate-200 bg-slate-50/70">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-semibold text-slate-700 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400"
      >
        <Database className="h-3.5 w-3.5 text-blue-700" aria-hidden="true" />
        <span className="flex-1">Sources and processing evidence</span>
        <span className="font-mono text-[10px] font-normal text-slate-500">{completed}/{response.tool_activity.length} complete</span>
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden="true" />
      </button>
      {open ? (
        <div className="space-y-2 border-t border-slate-200 p-2">
          {response.tool_activity.map((activity, index) => {
            const source = sourceLabel(activity.result);
            return (
              <details key={`${activity.tool}-${activity.at}-${index}`} className="group rounded-md border border-slate-200 bg-white">
                <summary className="flex cursor-pointer list-none items-start gap-2 px-2.5 py-2 text-xs">
                  {activity.status === "completed"
                    ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />
                    : <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-600" />}
                  <span className="min-w-0 flex-1">
                    <span className="block font-mono text-[10px] font-semibold uppercase text-slate-600">{activity.tool.replaceAll("_", " ")}</span>
                    <span className="mt-0.5 block leading-5 text-slate-600">{formatScientificText(activity.summary)}</span>
                    {source ? <span className="mt-1 block truncate font-mono text-[9px] text-blue-700">{source}</span> : null}
                  </span>
                </summary>
                <div className="space-y-2 border-t border-slate-100 p-2">
                  <div className="flex justify-end">
                    <button
                      type="button"
                      onClick={() => navigator.clipboard.writeText(JSON.stringify(activity.result, null, 2))}
                      className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-[10px] font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"
                    >
                      <Copy className="h-3 w-3" /> Copy evidence
                    </button>
                  </div>
                  <div>
                    <div className="mb-1 font-mono text-[9px] uppercase text-slate-400">Inputs</div>
                    <pre className="max-h-40 overflow-auto rounded bg-slate-950 p-2 font-mono text-[10px] leading-4 text-slate-100">{JSON.stringify(activity.arguments, null, 2)}</pre>
                  </div>
                  <div>
                    <div className="mb-1 font-mono text-[9px] uppercase text-slate-400">Bounded result</div>
                    <pre className="max-h-72 overflow-auto rounded bg-slate-950 p-2 font-mono text-[10px] leading-4 text-slate-100">{JSON.stringify(activity.result, null, 2)}</pre>
                  </div>
                </div>
              </details>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
