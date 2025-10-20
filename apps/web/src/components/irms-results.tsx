"use client";

import { useMemo } from "react";

type Summary = {
  rows: number;
  cols: number;
  columns: string[];
  date_range: { min: string | null; max: string | null };
  isotopes: {
    d13c_range?: { min: number; max: number } | null;
    d18o_range?: { min: number; max: number } | null;
  };
  sample_counts: Array<{
    identifier: string;
    unique_samples: number;
    total_measurements: number;
    measurements_pct: number;
  }>;
  standards: Array<{
    standard: string;
    count: number;
    d13c_mean?: number | null;
    d13c_std?: number | null;
    d18o_mean?: number | null;
    d18o_std?: number | null;
  }>;
};

export function IRMSResultsSummary({ summary }: { summary: Summary }) {
  const hasStandards = (summary.standards?.length || 0) > 0;
  const hasSamples = (summary.sample_counts?.length || 0) > 0;

  const dateLabel = useMemo(() => {
    const { min, max } = summary.date_range || {};
    if (!min && !max) return "Unknown";
    if (min && max && min !== max) return `${min} → ${max}`;
    return min || max || "Unknown";
  }, [summary.date_range]);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Rows" value={summary.rows} />
        <Stat label="Columns" value={summary.cols} />
        <Stat label="Date Range" value={dateLabel} />
        <Stat label="Detected Standards" value={summary.standards?.length || 0} />
      </div>

      <section>
        <h3 className="mb-2 text-sm font-semibold">Isotope Ranges</h3>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <RangeCard title="δ13C (Mean)" range={summary.isotopes?.d13c_range || null} />
          <RangeCard title="δ18O (Mean)" range={summary.isotopes?.d18o_range || null} />
        </div>
      </section>

      {hasSamples && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">Sample Counts</h3>
          <div className="overflow-auto rounded border">
            <table className="w-full text-sm">
              <thead className="bg-muted">
                <tr>
                  <Th>Identifier</Th>
                  <Th className="text-right">Unique Samples</Th>
                  <Th className="text-right">Total Measurements</Th>
                  <Th className="text-right">% of Measurements</Th>
                </tr>
              </thead>
              <tbody>
                {summary.sample_counts.map((row) => (
                  <tr key={row.identifier} className="border-t">
                    <Td>{row.identifier}</Td>
                    <Td className="text-right">{row.unique_samples}</Td>
                    <Td className="text-right">{row.total_measurements}</Td>
                    <Td className="text-right">{row.measurements_pct.toFixed(1)}%</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {hasStandards && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">Standards Summary</h3>
          <div className="overflow-auto rounded border">
            <table className="w-full text-sm">
              <thead className="bg-muted">
                <tr>
                  <Th>Standard</Th>
                  <Th className="text-right">Count</Th>
                  <Th className="text-right">δ13C mean</Th>
                  <Th className="text-right">δ13C std</Th>
                  <Th className="text-right">δ18O mean</Th>
                  <Th className="text-right">δ18O std</Th>
                </tr>
              </thead>
              <tbody>
                {summary.standards.map((s) => (
                  <tr key={s.standard} className="border-t">
                    <Td>{s.standard}</Td>
                    <Td className="text-right">{s.count}</Td>
                    <Td className="text-right">{fmtNum(s.d13c_mean)}</Td>
                    <Td className="text-right">{fmtNum(s.d13c_std)}</Td>
                    <Td className="text-right">{fmtNum(s.d18o_mean)}</Td>
                    <Td className="text-right">{fmtNum(s.d18o_std)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function fmtNum(v?: number | null) {
  if (v === undefined || v === null || Number.isNaN(v)) return "—";
  return v.toFixed(3);
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded border p-3">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-lg font-medium">{value}</div>
    </div>
  );
}

function RangeCard({ title, range }: { title: string; range: { min: number; max: number } | null }) {
  return (
    <div className="rounded border p-3">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{title}</div>
      {range ? (
        <div className="text-sm">{range.min.toFixed(3)} → {range.max.toFixed(3)}</div>
      ) : (
        <div className="text-sm text-muted-foreground">N/A</div>
      )}
    </div>
  );
}

function Th({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <th className={`px-2 py-1 text-left font-medium ${className}`}>{children}</th>;
}

function Td({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-2 py-1 ${className}`}>{children}</td>;
}

