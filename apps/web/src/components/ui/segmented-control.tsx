import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import { formatScientificText } from "@/lib/scientific-notation";

type SegmentItem = {
  value: string;
  label: ReactNode;
  disabled?: boolean;
};

export function SegmentedControl({
  label,
  items,
  value,
  onChange,
  className,
}: {
  label: string;
  items: SegmentItem[];
  value: string;
  onChange: (value: string) => void;
  className?: string;
}) {
  return (
    <div className={cn("inline-flex rounded-lg border border-stone-300 bg-white p-1 shadow-sm", className)} role="group" aria-label={label}>
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            aria-pressed={active}
            disabled={item.disabled}
            onClick={() => onChange(item.value)}
            className={cn(
              "min-h-8 min-w-20 rounded-md px-3 py-1.5 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50",
              active ? "bg-stone-900 text-white" : "text-stone-700 hover:bg-stone-100",
            )}
          >
            {typeof item.label === "string" ? formatScientificText(item.label) : item.label}
          </button>
        );
      })}
    </div>
  );
}
