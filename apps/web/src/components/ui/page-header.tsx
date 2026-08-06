import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  compact = false,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
  compact?: boolean;
}) {
  return (
    <section
      className={cn(
        "flex flex-col border-b border-slate-200 sm:flex-row sm:items-end sm:justify-between",
        compact ? "gap-2 pb-2.5" : "gap-3 pb-4",
      )}
    >
      <div className="min-w-0">
        {eyebrow ? <div className="font-mono text-[10px] font-medium uppercase text-blue-700">{eyebrow}</div> : null}
        <h1 className={cn("font-display font-semibold leading-tight text-slate-950", eyebrow ? "mt-1" : "", compact ? "text-xl" : "text-2xl")}>
          {title}
        </h1>
        <p className={cn("mt-1 max-w-3xl text-slate-600", compact ? "text-xs leading-snug" : "text-sm leading-relaxed")}>{description}</p>
      </div>
      {actions ? <div className={cn("flex shrink-0 flex-wrap items-center", compact ? "gap-1.5 text-xs" : "gap-2")}>{actions}</div> : null}
    </section>
  );
}
