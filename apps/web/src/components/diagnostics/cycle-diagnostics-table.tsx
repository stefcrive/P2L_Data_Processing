"use client";

import { cn } from "@/lib/utils";
import { formatScientificText } from "@/lib/scientific-notation";

function asBoolean(value: unknown): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    return value !== 0;
  }
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    return normalized === "true" || normalized === "1" || normalized === "yes";
  }
  return false;
}

function formatDeltaValue(value?: number | null): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "N/A";
  }
  return value.toFixed(3);
}

function isDeltaColumnLabel(label: string): boolean {
  const normalized = label.trim().toLowerCase();
  return normalized.includes("d13") || normalized.includes("d18");
}

function isSignalIntensityColumnLabel(label: string): boolean {
  const normalized = label.trim().toLowerCase();
  return normalized.includes("int m/z") && normalized.includes("(v)");
}

export function SharedCycleDiagnosticsTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  if (!rows.length) {
    return <div className="rounded-lg border border-dashed border-stone-300 p-4 text-sm text-stone-500">No cycle rows returned for this point.</div>;
  }

  const statusRows: Array<Record<string, unknown>> = rows.map((row) => {
    const excludedD13 = asBoolean(row["Excluded d13C"]);
    const excludedD18 = asBoolean(row["Excluded d18O"]);
    const excludedSaturation = asBoolean(row["Excluded (Saturation)"]);
    const excludedSampleGasEscape = asBoolean(row["Excluded (Sample Gas Escape)"]);
    const excludedAny = excludedSaturation || excludedSampleGasEscape || excludedD13 || excludedD18;
    const firstValidCycle = asBoolean(row["First Valid Cycle"]);
    const lastValidCycle = asBoolean(row["Last Valid Cycle"]);
    return {
      ...row,
      "Cycle status": excludedSampleGasEscape ? "Sample gas escape" : excludedSaturation ? "Saturated" : excludedAny ? "Excluded" : "Successful",
      "First Valid Cycle": firstValidCycle,
      "Last Valid Cycle": lastValidCycle,
    };
  });

  const preferredColumns = [
    "Cycle",
    "Cycle status",
    "First Valid Cycle",
    "Last Valid Cycle",
    "SMP Int m/z 44 (V)",
    "REF Int m/z 44 (V)",
    "SMP Int m/z 45 (V)",
    "REF Int m/z 45 (V)",
    "SMP Int m/z 46 (V)",
    "REF Int m/z 46 (V)",
    "d13C",
    "d18O",
    "Excluded d13C",
    "Excluded d18O",
    "Excluded (Saturation)",
    "Excluded (Sample Gas Escape)",
  ];
  const discoveredColumns = Object.keys(statusRows[0] ?? {});
  const columns = [
    ...preferredColumns.filter((column) => discoveredColumns.includes(column)),
    ...discoveredColumns.filter((column) => !preferredColumns.includes(column)),
  ];

  function formatCell(value: unknown, column: string): string {
    if (value == null || value === "") {
      return "None";
    }
    if (column === "Cycle status") {
      return String(value);
    }
    if (typeof value === "boolean") {
      return value ? "Yes" : "No";
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      if (isDeltaColumnLabel(column)) {
        return formatDeltaValue(value);
      }
      if (isSignalIntensityColumnLabel(column)) {
        return value.toFixed(2);
      }
      if (Number.isInteger(value)) {
        return String(value);
      }
      return value.toFixed(6);
    }
    return String(value);
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-md bg-sky-100 px-2 py-1 text-sky-800">First valid cycle</span>
        <span className="rounded-md bg-amber-100 px-2 py-1 text-amber-800">Last valid cycle</span>
        <span className="rounded-md bg-emerald-100 px-2 py-1 text-emerald-800">Successful cycle</span>
        <span className="rounded-md bg-rose-100 px-2 py-1 text-rose-800">Saturated cycle</span>
        <span className="rounded-md bg-orange-100 px-2 py-1 text-orange-800">Sample gas escape</span>
      </div>
      <div className="max-h-[560px] overflow-auto rounded-lg border border-stone-200">
        <table className="min-w-full divide-y divide-stone-200 text-left text-sm">
          <thead className="bg-stone-50">
            <tr>
              {columns.map((column) => (
                <th key={column} className="px-3 py-2 font-medium text-stone-700">
                  {formatScientificText(column)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-stone-100">
            {statusRows.slice(0, 25).map((row, rowIndex) => {
              const saturated = String(row["Cycle status"]) === "Saturated";
              const sampleGasEscape = String(row["Cycle status"]) === "Sample gas escape";
              const firstValidCycle = asBoolean(row["First Valid Cycle"]);
              const lastValidCycle = asBoolean(row["Last Valid Cycle"]);
              return (
                <tr
                  key={rowIndex}
                  className={cn(
                    firstValidCycle && lastValidCycle
                      ? "bg-teal-100/85"
                      : firstValidCycle
                        ? "bg-sky-100/85"
                        : lastValidCycle
                          ? "bg-amber-100/80"
                          : sampleGasEscape
                            ? "bg-orange-50/85"
                          : saturated
                            ? "bg-rose-50/80"
                            : "bg-emerald-50/70",
                  )}
                >
                  {columns.map((column) => {
                    const cellValue = row[column];
                    const flaggedColumn = column.startsWith("Excluded");
                    const flaggedValue = flaggedColumn ? asBoolean(cellValue) : false;
                    const validCycleColumn = column === "First Valid Cycle" || column === "Last Valid Cycle";
                    const validCycleColumnValue = validCycleColumn ? asBoolean(cellValue) : false;
                    return (
                      <td
                        key={column}
                        className={cn(
                          "px-3 py-2",
                          validCycleColumn
                            ? validCycleColumnValue
                              ? "font-semibold text-stone-900"
                              : "font-medium text-stone-500"
                            : "",
                          flaggedColumn
                            ? flaggedValue
                              ? "font-medium text-rose-700"
                              : "font-medium text-emerald-700"
                            : "text-stone-700",
                        )}
                      >
                        {formatCell(cellValue, column)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length > 25 ? <div className="border-t border-stone-200 px-3 py-2 text-xs text-stone-500">Showing first 25 of {rows.length} rows.</div> : null}
      </div>
    </div>
  );
}
