import type { ReactNode } from "react";

import { formatScientificText } from "@/lib/scientific-notation";

export function Tooltip({
  label,
  children,
  contentClassName = "",
  align = "center",
}: {
  label: string;
  children: ReactNode;
  contentClassName?: string;
  align?: "start" | "center" | "end";
}) {
  const alignClass =
    align === "start"
      ? "left-0"
      : align === "end"
        ? "right-0"
        : "left-1/2 -translate-x-1/2";

  return (
    <span className="group relative inline-flex">
      {children}
      <span
        role="tooltip"
        className={`pointer-events-none absolute top-full z-50 mt-2 hidden max-w-[min(24rem,calc(100vw-2rem))] whitespace-normal break-words rounded-md border border-stone-200 bg-stone-950 px-3 py-2 text-left text-xs font-medium leading-5 text-white shadow-lg group-hover:block group-focus-within:block ${alignClass} ${contentClassName}`}
      >
        {formatScientificText(label)}
      </span>
    </span>
  );
}
