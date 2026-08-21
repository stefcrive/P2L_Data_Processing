"use client";

import { Database, FlaskConical, ShieldCheck } from "lucide-react";

import { ScientificChatPanel } from "@/components/chat/scientific-chat-panel";

export function ScientificChatPage() {
  return (
    <div className="mx-auto flex w-full max-w-[1500px] flex-col gap-4">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
        <div>
          <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-blue-700">Evidence workspace</p>
          <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl">Scientific results assistant</h1>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-600">Explore the loaded IRMS dataset in plain language. Every current-platform claim is backed by an inspectable tool result.</p>
        </div>
        <div className="flex flex-wrap gap-2 text-[10px] font-medium text-slate-600">
          <span className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1.5"><Database className="h-3 w-3 text-blue-700" /> Raw + processed</span>
          <span className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1.5"><FlaskConical className="h-3 w-3 text-blue-700" /> Diagnostics</span>
          <span className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1.5"><ShieldCheck className="h-3 w-3 text-emerald-700" /> Read only</span>
        </div>
      </div>
      <div className="flex min-h-[calc(100vh-var(--app-header-height)-9rem)] flex-col">
        <ScientificChatPanel />
      </div>
    </div>
  );
}
