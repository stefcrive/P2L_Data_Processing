"use client";

import { useMemo } from "react";

type MultiSelectDropdownProps = {
  label: string;
  options: string[];
  selected: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  maxHeightClassName?: string;
};

export function MultiSelectDropdown({
  label,
  options,
  selected,
  onChange,
  placeholder = "Select values",
  maxHeightClassName = "max-h-56",
}: MultiSelectDropdownProps) {
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const selectedCount = selected.length;
  const summaryLabel = selectedCount ? `${selectedCount} selected` : placeholder;

  function toggleOption(option: string, checked: boolean) {
    const next = checked ? [...selected, option] : selected.filter((item) => item !== option);
    onChange(next);
  }

  return (
    <div className="form-field">
      <span className="form-label">{label}</span>
      <details className="group relative">
        <summary className="form-control list-none cursor-pointer">
          <div className="flex items-center justify-between gap-3">
            <span className="truncate">{summaryLabel}</span>
            <span className="text-xs font-medium text-stone-500 group-open:hidden">Open</span>
            <span className="hidden text-xs font-medium text-stone-500 group-open:inline">Close</span>
          </div>
        </summary>
        <div className="absolute z-20 mt-2 w-full rounded-xl border border-stone-200 bg-white p-3 shadow-lg">
          <div className="mb-2 flex items-center justify-between gap-2 border-b border-stone-200 pb-2 text-xs">
            <button type="button" className="text-stone-700 hover:text-stone-900" onClick={() => onChange(options)}>
              Select all
            </button>
            <button type="button" className="text-stone-700 hover:text-stone-900" onClick={() => onChange([])}>
              Clear
            </button>
          </div>
          <div className={`space-y-1.5 overflow-y-auto pr-1 ${maxHeightClassName}`}>
            {options.length ? (
              options.map((option) => (
                <label key={option} className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-stone-100">
                  <input
                    type="checkbox"
                    checked={selectedSet.has(option)}
                    onChange={(event) => toggleOption(option, event.target.checked)}
                    className="mt-0.5 h-4 w-4 accent-stone-900"
                  />
                  <span className="truncate text-sm text-stone-700">{option}</span>
                </label>
              ))
            ) : (
              <div className="px-1 py-2 text-xs text-stone-500">No options available.</div>
            )}
          </div>
        </div>
      </details>
    </div>
  );
}
