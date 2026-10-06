"use client";

import { useTranslation } from "@/components/layout/language-provider";
import * as React from "react";

import { cn } from "@/lib/utils";
import { formatScientificText } from "@/lib/scientific-notation";

export function Badge({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const tr = useTranslation();
  return (
    <div
      className={cn(
        "inline-flex items-center rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 font-mono text-[10px] font-medium tracking-normal text-slate-700",
        className,
      )}
      {...props}
    >
      {tr(typeof children === "string" ? formatScientificText(children) : children)}
    </div>
  );
}
