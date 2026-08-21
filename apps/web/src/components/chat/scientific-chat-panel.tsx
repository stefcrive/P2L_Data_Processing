"use client";

import {
  ArrowUp,
  Ban,
  Beaker,
  Bot,
  Database,
  Eraser,
  FileSpreadsheet,
  LoaderCircle,
  LockKeyhole,
  Paperclip,
  Sparkles,
  X,
} from "lucide-react";
import { ChangeEvent, FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";

import { AssistantMarkdown } from "@/components/chat/assistant-markdown";
import { STARTER_PROMPTS } from "@/components/chat/chat-model";
import { SourceEvidenceDisclosure } from "@/components/chat/source-evidence-disclosure";
import { useScientificChat } from "@/components/chat/use-scientific-chat";
import { Button } from "@/components/ui/button";
import { formatScientificText } from "@/lib/scientific-notation";
import { cn } from "@/lib/utils";
import { useSessionStore } from "@/store/use-session-store";

export function ScientificChatPanel({ compact = false }: { compact?: boolean }) {
  const sessionId = useSessionStore((state) => state.sessionId);
  const {
    messages,
    attachments,
    isLoading,
    error,
    send,
    clear,
    cancel,
    addAttachments,
    removeAttachment,
  } = useScientificChat(sessionId);
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [messages, isLoading]);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (!draft.trim()) return;
    const message = draft;
    setDraft("");
    void send(message);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  const onFilesSelected = (event: ChangeEvent<HTMLInputElement>) => {
    addAttachments(Array.from(event.target.files || []));
    event.target.value = "";
  };

  return (
    <section className={cn("flex min-h-0 flex-1 flex-col bg-white", !compact && "rounded-lg border border-slate-200 shadow-sm")} aria-label="Scientific results assistant">
      <div className={cn("flex items-center gap-3 border-b border-slate-200", compact ? "px-3 py-2.5" : "px-4 py-3")}>
        <div className="relative grid h-9 w-9 shrink-0 place-items-center rounded-md bg-slate-950 text-white shadow-sm">
          <Beaker className="h-4 w-4" aria-hidden="true" />
          <span className="absolute -bottom-1 -right-1 h-2.5 w-2.5 rounded-full border-2 border-white bg-emerald-500" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="truncate font-display text-sm font-semibold text-slate-950">Scientific results assistant</h2>
          <p className="truncate font-mono text-[9px] uppercase tracking-wide text-slate-500">
            {sessionId ? `session ${sessionId.slice(0, 12)}` : "all platform sessions"}
          </p>
        </div>
        <span className="hidden items-center gap-1 rounded border border-emerald-200 bg-emerald-50 px-2 py-1 font-mono text-[9px] font-semibold uppercase text-emerald-800 sm:inline-flex">
          <LockKeyhole className="h-3 w-3" /> Read only
        </span>
        {messages.length ? (
          <button type="button" onClick={clear} className="grid h-8 w-8 place-items-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400" aria-label="Clear conversation">
            <Eraser className="h-4 w-4" />
          </button>
        ) : null}
      </div>

      <div ref={scrollRef} className={cn("min-h-0 flex-1 overflow-y-auto", compact ? "p-3" : "p-4 sm:p-6")}>
        {messages.length === 0 ? (
          <div className={cn("mx-auto flex h-full max-w-3xl flex-col justify-center", compact ? "gap-3 py-3" : "gap-6 py-8")}>
            <div className="max-w-xl">
              <div className="mb-3 flex items-center gap-2 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-blue-700">
                <span className="h-px w-8 bg-blue-300" /> Instrument notebook
              </div>
              <h3 className={cn("font-display font-semibold leading-tight text-slate-950", compact ? "text-lg" : "text-2xl sm:text-3xl")}>
                Ask the dataset. Inspect the evidence.
              </h3>
              <p className="mt-2 max-w-lg text-sm leading-6 text-slate-600">
                Query platform results or attach Excel workbooks to inspect and compare against the active session.
              </p>
            </div>
            <div className={cn("grid gap-2", compact ? "grid-cols-1" : "md:grid-cols-3")}>
              {STARTER_PROMPTS.map((prompt, index) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => void send(prompt)}
                  className="group rounded-md border border-slate-200 bg-slate-50/70 p-3 text-left text-xs leading-5 text-slate-700 transition hover:border-blue-300 hover:bg-blue-50 hover:text-blue-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
                >
                  <span className="mb-2 flex items-center justify-between font-mono text-[9px] font-semibold uppercase text-slate-400 group-hover:text-blue-700">
                    Query {index + 1}
                    <ArrowUp className="h-3 w-3 rotate-45 text-blue-600 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
                  </span>
                  {formatScientificText(prompt)}
                </button>
              ))}
            </div>
            {!sessionId ? (
              <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                <Database className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                No session is selected. The assistant can list sessions and platform state; select a session to ask directly about its data.
              </div>
            ) : null}
          </div>
        ) : (
          <div className="mx-auto max-w-4xl space-y-5">
            {messages.map((message) => message.role === "user" ? (
              <div key={message.id} className="ml-auto max-w-[88%] rounded-lg rounded-br-sm bg-blue-700 px-3.5 py-2.5 text-sm leading-6 text-white shadow-sm">
                {formatScientificText(message.content)}
              </div>
            ) : (
              <article key={message.id} className="relative pl-5">
                <span className="absolute bottom-0 left-0 top-0 w-px bg-gradient-to-b from-blue-500 via-blue-200 to-transparent" aria-hidden="true" />
                <span className="absolute left-[-3px] top-2 h-[7px] w-[7px] rounded-full bg-blue-600 ring-4 ring-blue-50" aria-hidden="true" />
                <div className="rounded-lg border border-slate-200 bg-white p-3.5 shadow-sm">
                  <div className="mb-2 flex items-center gap-2 font-mono text-[9px] font-semibold uppercase tracking-wide text-slate-400">
                    <Bot className="h-3.5 w-3.5 text-slate-700" /> Grounded analysis
                    {message.response?.processing_environment.mode ? <span>· {message.response.processing_environment.mode}</span> : null}
                  </div>
                  <AssistantMarkdown>{message.content}</AssistantMarkdown>
                  {message.response ? <SourceEvidenceDisclosure response={message.response} /> : null}
                </div>
              </article>
            ))}
            {isLoading ? (
              <div className="flex items-center gap-2 pl-5 text-xs text-slate-500" role="status">
                <LoaderCircle className="h-4 w-4 animate-spin text-blue-600" /> Reading platform evidence…
              </div>
            ) : null}
          </div>
        )}
      </div>

      <form onSubmit={submit} className={cn("border-t border-slate-200 bg-slate-50/75", compact ? "p-2.5" : "p-3 sm:p-4")}>
        {error ? <div className="mb-2 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800" role="alert"><Ban className="mt-0.5 h-3.5 w-3.5 shrink-0" />{error}</div> : null}
        <div className="mx-auto max-w-4xl rounded-lg border border-slate-300 bg-white p-2 shadow-sm focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-100">
          {attachments.length ? (
            <div className="mb-2 flex flex-wrap gap-1.5 border-b border-slate-100 pb-2">
              {attachments.map((file, index) => (
                <span key={`${file.name}-${file.size}-${file.lastModified}`} className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 text-[10px] text-emerald-900">
                  <FileSpreadsheet className="h-3 w-3 shrink-0" aria-hidden="true" />
                  <span className="max-w-48 truncate">{file.name}</span>
                  <span className="font-mono text-emerald-700">{file.size < 1024 * 1024 ? `${Math.ceil(file.size / 1024)} KB` : `${(file.size / (1024 * 1024)).toFixed(1)} MB`}</span>
                  <button type="button" onClick={() => removeAttachment(index)} disabled={isLoading} className="rounded p-0.5 hover:bg-emerald-100" aria-label={`Remove ${file.name}`}>
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
              <span className="self-center text-[9px] text-slate-400">Sent with each question until removed</span>
            </div>
          ) : null}
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value.slice(0, 4_000))}
            onKeyDown={onKeyDown}
            rows={compact ? 2 : 3}
            placeholder={sessionId ? "Ask about this session, or attach Excel data to compare…" : "Ask about sessions, or attach an Excel workbook…"}
            className="w-full resize-none border-0 bg-transparent px-1 py-1 text-sm leading-5 text-slate-900 outline-none placeholder:text-slate-400"
            disabled={isLoading}
          />
          <div className="flex items-center justify-between gap-2 border-t border-slate-100 pt-2">
            <div className="flex min-w-0 items-center gap-2">
              <input ref={fileInputRef} type="file" accept=".xls,.xlsx" multiple onChange={onFilesSelected} className="sr-only" />
              <button type="button" onClick={() => fileInputRef.current?.click()} disabled={isLoading || attachments.length >= 5} className="inline-flex h-7 shrink-0 items-center gap-1 rounded px-1.5 text-[10px] font-medium text-slate-500 hover:bg-slate-100 hover:text-blue-700 disabled:opacity-50" aria-label="Attach Excel workbooks">
                <Paperclip className="h-3.5 w-3.5" /> Excel
              </button>
              <span className="hidden truncate font-mono text-[9px] text-slate-400 sm:inline">Enter to send · Shift+Enter for line break</span>
            </div>
            {isLoading ? (
              <Button type="button" size="sm" variant="outline" onClick={cancel}><Ban className="h-3.5 w-3.5" /> Cancel</Button>
            ) : (
              <Button type="submit" size="sm" disabled={!draft.trim()}><Sparkles className="h-3.5 w-3.5" /> Ask dataset</Button>
            )}
          </div>
        </div>
      </form>
    </section>
  );
}
