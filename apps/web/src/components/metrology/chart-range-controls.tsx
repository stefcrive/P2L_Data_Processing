"use client";
import { DualRangeField } from "@/components/ui/dual-range-field";
import { useTranslation } from "@/components/layout/language-provider";
import type { ChartRanges, RangeKey } from "@/lib/station-chart-filters";
import type { LinearityPreviewRow } from "@/lib/types";

const fields: [RangeKey, string, number][] = [["signal","Signal range",.1],["leak_rate","Leak range",1],["d13_raw","δ¹³C range",.001],["d18_raw","δ¹⁸O range",.001]];

export function ChartRangeControls({rows, value, onChange}: {rows:LinearityPreviewRow[];value:ChartRanges;onChange:(value:ChartRanges)=>void}) {
  const tr = useTranslation();
  return <div className="station-range-controls space-y-3"><h4 className="form-section-title">{tr("Range filters")}</h4>
    {fields.map(([key,label,step]) => {
      const numbers = rows.map(row=>row[key]).filter((v):v is number=>typeof v === "number" && Number.isFinite(v));
      if (!numbers.length) return <p key={key} className="metro-muted">{tr(label)}: {tr("N/A")}</p>;
      const low = Math.min(...numbers), high = Math.max(...numbers);
      const min = Math.floor(low/step)*step, max = Math.max(min+step,Math.ceil(high/step)*step);
      return <DualRangeField key={key} label={label} value={value[key]??[min,max]} min={min} max={max} step={step} precision={step===.001?3:step===1?1:2} onChange={next=>onChange({...value,[key]:next})}/>;
    })}
    <button type="button" className="metro-btn" disabled={!Object.keys(value).length} onClick={()=>onChange({})}>{tr("Reset filters")}</button>
  </div>;
}
