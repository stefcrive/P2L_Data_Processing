"use client";

import { useTranslation } from "@/components/layout/language-provider";
import * as React from "react";

import { cn } from "@/lib/utils";
import { formatScientificText } from "@/lib/scientific-notation";

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-lg border border-slate-200 bg-white shadow-sm",
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-col gap-1 border-b border-slate-200 p-4", className)} {...props} />;
}

export function CardTitle({ className, children, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  const tr = useTranslation();
  return <h3 className={cn("font-display text-base font-semibold leading-tight tracking-normal text-slate-950", className)} {...props}>{tr(typeof children === "string" ? formatScientificText(children) : children)}</h3>;
}

export function CardDescription({ className, children, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  const tr = useTranslation();
  return <p className={cn("text-xs leading-relaxed text-slate-500", className)} {...props}>{tr(typeof children === "string" ? formatScientificText(children) : children)}</p>;
}

export function CardContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4", className)} {...props} />;
}
