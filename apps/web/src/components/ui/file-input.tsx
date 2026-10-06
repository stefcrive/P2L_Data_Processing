"use client";

import { useState, type InputHTMLAttributes } from "react";
import { useTranslation } from "@/components/layout/language-provider";
import { cn } from "@/lib/utils";

/** A native file input with captions that follow the application's language. */
export function FileInput({ className, onChange, multiple, disabled, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, "type">) {
  const tr = useTranslation();
  const [names, setNames] = useState<string[]>([]);
  return <span className={cn("relative flex min-h-9 min-w-0 items-center gap-3 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm shadow-sm focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-100", disabled && "opacity-50", className)}>
    <span aria-hidden="true" className="shrink-0 rounded bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-800">{tr(multiple ? "Choose files" : "Choose file")}</span>
    <span aria-hidden="true" className="min-w-0 truncate text-xs text-slate-600" title={names.join(", ")}>{names.length ? names.join(", ") : tr("No file chosen")}</span>
    <input {...props} type="file" multiple={multiple} disabled={disabled} aria-label={props["aria-label"] ?? tr(multiple ? "Choose files" : "Choose file")} className="absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed" onChange={event => { setNames(Array.from(event.target.files ?? [], file => file.name)); onChange?.(event); }} />
  </span>;
}
