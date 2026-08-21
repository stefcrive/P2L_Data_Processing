"use client";

import { Bot, ExternalLink, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { ScientificChatPanel } from "@/components/chat/scientific-chat-panel";

function FloatingPanel() {
  const [open, setOpen] = useState(false);
  return (
    <div className="fixed bottom-16 right-3 z-50 sm:right-4">
      {open ? (
        <div className="mb-2 flex h-[min(680px,calc(100vh-6rem))] w-[min(420px,calc(100vw-1.5rem))] flex-col overflow-hidden rounded-xl border border-slate-300 bg-white shadow-2xl">
          <div className="flex items-center justify-between border-b border-slate-200 bg-slate-950 px-3 py-2 text-white">
            <span className="font-mono text-[10px] font-semibold uppercase tracking-wide">Dataset console</span>
            <div className="flex items-center gap-1">
              <Link href="/assistant" className="grid h-7 w-7 place-items-center rounded text-slate-300 hover:bg-white/10 hover:text-white" aria-label="Open full-page assistant"><ExternalLink className="h-3.5 w-3.5" /></Link>
              <button type="button" onClick={() => setOpen(false)} className="grid h-7 w-7 place-items-center rounded text-slate-300 hover:bg-white/10 hover:text-white" aria-label="Close assistant"><X className="h-4 w-4" /></button>
            </div>
          </div>
          <ScientificChatPanel compact />
        </div>
      ) : null}
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="ml-auto flex h-11 items-center gap-2 rounded-lg bg-slate-950 px-3.5 text-sm font-semibold text-white shadow-lg transition hover:bg-blue-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2"
        aria-expanded={open}
        aria-label={open ? "Close scientific assistant" : "Open scientific assistant"}
      >
        <Bot className="h-4 w-4" />
        <span>{open ? "Close" : "Ask data"}</span>
      </button>
    </div>
  );
}

export function ScientificChatAssistant() {
  const pathname = usePathname();
  if (pathname === "/assistant") return null;
  return <FloatingPanel />;
}
