"use client";

import { useTranslation } from "@/components/layout/language-provider";
import { useEffect, useMemo, useState, type KeyboardEvent } from "react";

import { formatRangeValue, parseRangeValue } from "@/lib/scientific-range";
import { cn } from "@/lib/utils";
import { formatScientificText } from "@/lib/scientific-notation";

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function DualRangeField({
  label,
  value,
  min,
  max,
  step = 0.1,
  precision = 2,
  date = false,
  compact = false,
  description,
  className,
  onChange,
}: {
  label: string;
  value: [number, number];
  min: number;
  max: number;
  step?: number;
  precision?: number;
  date?: boolean;
  compact?: boolean;
  description?: string;
  className?: string;
  onChange: (next: [number, number]) => void;
}) {
  const tr = useTranslation();
  const format = (v:number) => formatRangeValue(v, precision, date);
  const parse = (v:string) => parseRangeValue(v, date);
  const resolvedMin = Math.min(min, max);
  const resolvedMax = Math.max(min, max);
  const low = clamp(Math.min(value[0], value[1]), resolvedMin, resolvedMax);
  const high = clamp(Math.max(value[0], value[1]), resolvedMin, resolvedMax);
  const [lowDraft, setLowDraft] = useState(format(low));
  const [highDraft, setHighDraft] = useState(format(high));
  const span = resolvedMax - resolvedMin || 1;
  const trackStyle = useMemo(
    () => ({
      left: `${((low - resolvedMin) / span) * 100}%`,
      right: `${100 - ((high - resolvedMin) / span) * 100}%`,
    }),
    [high, low, resolvedMin, span],
  );

  useEffect(() => setLowDraft(format(low)), [low, precision, date]);
  useEffect(() => setHighDraft(format(high)), [high, precision, date]);

  const commitLow = () => {
    if(lowDraft === format(low)) return;
    const parsed = parse(lowDraft);
    if (parsed == null) {
      setLowDraft(format(low));
      return;
    }
    const next=clamp(parsed, resolvedMin, high);
    setLowDraft(format(next));
    onChange([next, high]);
  };

  const commitHigh = () => {
    if(highDraft === format(high)) return;
    const parsed = parse(highDraft);
    if (parsed == null) {
      setHighDraft(format(high));
      return;
    }
    const next=clamp(parsed, low, resolvedMax);
    setHighDraft(format(next));
    onChange([low, next]);
  };

  const handleKey = (event:KeyboardEvent<HTMLInputElement>, lower:boolean) => {
    const current=lower?low:high;
    const delta=event.key==="ArrowRight"||event.key==="ArrowUp"?step:event.key==="ArrowLeft"||event.key==="ArrowDown"?-step:event.key==="PageUp"?step*10:event.key==="PageDown"?-step*10:null;
    const next=event.key==="Home"?resolvedMin:event.key==="End"?resolvedMax:delta==null?null:current+delta;
    if(next==null)return;
    event.preventDefault();
    if(lower)onChange([clamp(next,resolvedMin,high),high]);
    else onChange([low,clamp(next,low,resolvedMax)]);
  };
  // A native stepped range rounds controlled values that are off its step grid.
  // Keep exact numeric/date edits; retain the configured increments for the keyboard.
  const dragValue=(raw:number)=>raw<=resolvedMin?resolvedMin:raw>=resolvedMax?resolvedMax:clamp(resolvedMin+Math.round((raw-resolvedMin)/step)*step,resolvedMin,resolvedMax);

  return (
    <fieldset className={cn("range-field", date && "range-field--date", compact && "range-field--compact", compact && (date||Math.max(lowDraft.length,highDraft.length)>8) && "range-field--wide-values", className)}>
      <legend className="range-field__label">{tr(formatScientificText(label))}</legend>
      {description ? <p className="range-field__description">{tr(formatScientificText(description))}</p> : null}
      <div className="range-field__controls">
        <label className="range-field__number">
          <span>{tr(compact?"Min":"Low")}</span>
          <input
            type={date ? "text" : "number"}
            min={resolvedMin}
            max={high}
            step={step}
            value={lowDraft}
            title={lowDraft}
            placeholder={date?"YYYY-MM-DD":undefined}
            onChange={(event) => setLowDraft(event.currentTarget.value)}
            onBlur={commitLow}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
            aria-label={tr(`${label} lower limit`)}
          />
        </label>

        <div className="dual-range" aria-label={tr(`${label} range`)}>
          <div className="dual-range__rail" />
          <div className="dual-range__selection" style={trackStyle} />
          <input
            className="dual-range__input"
            type="range"
            min={resolvedMin}
            max={resolvedMax}
            step="any"
            onKeyDown={event=>handleKey(event,true)}
            value={low}
            aria-valuetext={format(low)}
            onInput={(event) => onChange([Math.min(dragValue(Number(event.currentTarget.value)), high), high])}
            aria-label={tr(`${label} lower handle`)}
          />
          <input
            className="dual-range__input"
            type="range"
            min={resolvedMin}
            max={resolvedMax}
            step="any"
            onKeyDown={event=>handleKey(event,false)}
            value={high}
            aria-valuetext={format(high)}
            onInput={(event) => onChange([low, Math.max(dragValue(Number(event.currentTarget.value)), low)])}
            aria-label={tr(`${label} upper handle`)}
          />
        </div>

        <label className="range-field__number">
          <span>{tr(compact?"Max":"High")}</span>
          <input
            type={date ? "text" : "number"}
            min={low}
            max={resolvedMax}
            step={step}
            value={highDraft}
            title={highDraft}
            placeholder={date?"YYYY-MM-DD":undefined}
            onChange={(event) => setHighDraft(event.currentTarget.value)}
            onBlur={commitHigh}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
            aria-label={tr(`${label} upper limit`)}
          />
        </label>
      </div>
    </fieldset>
  );
}
