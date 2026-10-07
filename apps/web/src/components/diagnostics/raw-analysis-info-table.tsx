"use client";

import { useTranslation } from "@/components/layout/language-provider";
import { useId } from "react";
import { cn } from "@/lib/utils";
import { formatScientificText } from "@/lib/scientific-notation";

function formatAnalysisInfoValue(value: unknown, label: string): string {
  if (value == null || value === "") {
    return "N/A";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    if (Number.isInteger(value) && label === "Line") {
      return String(value);
    }
    return value.toLocaleString(undefined, { maximumFractionDigits: 6 });
  }
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

export function RawAnalysisInfoTable({
  info,
  layout = "horizontal",
  className,
  showHeading = true,
}: {
  info?: Record<string, unknown>;
  layout?: "horizontal" | "vertical";
  className?: string;
  showHeading?: boolean;
}) {
  const tr = useTranslation();
  const headingId = useId();
  const entries = Object.entries(info ?? {});
  if (!entries.length) {
    return null;
  }

  if (layout === "vertical") {
    return (
      <section
        className={cn("raw-analysis-info flex h-full min-h-0 flex-col overflow-hidden rounded-md border border-slate-200 bg-white", className)}
        aria-labelledby={headingId}
      >
        <div className={cn("flex shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-slate-50 px-2 py-1.5", !showHeading && "sr-only")}>
          <h3 id={headingId} className="text-[11px] font-semibold text-slate-800">{tr("Raw analysis data")}</h3>
          <span className="text-[10px] tabular-nums text-slate-500">{entries.length}</span>
        </div>
        <div className="min-h-0 flex-1 overflow-auto" tabIndex={0} aria-label={tr("Scroll raw analysis parameters")}>
          <table className="w-full table-fixed border-collapse text-left text-[11px] leading-tight">
            <caption className="sr-only">{tr("Raw file parameters for the hovered analysis")}</caption>
            <thead className="sticky top-0 z-10 bg-slate-100">
              <tr className="border-b border-slate-200">
                <th scope="col" className="w-[44%] px-2 py-1 font-semibold text-slate-600">{tr("Parameter")}</th>
                <th scope="col" className="px-2 py-1 font-semibold text-slate-600">{tr("Value")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {entries.map(([label, value]) => {
                const formattedValue = formatAnalysisInfoValue(value, label);
                return (
                  <tr key={label}>
                    <th scope="row" className="truncate px-2 py-0.5 font-medium text-slate-600" title={tr(label)}>
                      {tr(formatScientificText(label))}
                    </th>
                    <td
                      className="truncate px-2 py-0.5 font-mono tabular-nums text-slate-800"
                      title={tr(formattedValue)}
                    >
                      {tr(formattedValue)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    );
  }

  return (
    <section className={cn("mb-2 border-y border-slate-200 py-2", className)} aria-labelledby={headingId}>
      <div className={cn("mb-1.5 flex items-center justify-between gap-3", !showHeading && "sr-only")}>
        <h3 id={headingId} className="text-[11px] font-semibold text-slate-800">{tr("Raw analysis data")}</h3>
        <span className="text-[10px] tabular-nums text-slate-500">{entries.length}{tr(" parameters")}</span>
      </div>
      <div className="overflow-x-auto rounded-md border border-slate-200 bg-white" tabIndex={0} aria-label={tr("Scroll raw analysis parameters horizontally")}>
        <table className="w-max min-w-full border-collapse text-left text-[10px] leading-tight">
          <caption className="sr-only">{tr("Raw file parameters for the hovered analysis")}</caption>
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50">
              {entries.map(([label]) => (
                <th key={label} scope="col" className="max-w-[180px] whitespace-nowrap px-2 py-1 font-medium text-slate-600">
                  {tr(formatScientificText(label))}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              {entries.map(([label, value]) => {
                const formattedValue = formatAnalysisInfoValue(value, label);
                return (
                  <td
                    key={label}
                    className="max-w-[180px] whitespace-nowrap px-2 py-1.5 font-mono tabular-nums text-slate-800"
                    title={tr(formattedValue)}
                  >
                    {tr(formattedValue)}
                  </td>
                );
              })}
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}
