"use client";

import { useEffect, useMemo, useState } from "react";

import { cn } from "@/lib/utils";
import { formatScientificText } from "@/lib/scientific-notation";

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function parseDraft(value: string): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function DualRangeField({
  label,
  value,
  min,
  max,
  step = 0.1,
  precision = 2,
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
  description?: string;
  className?: string;
  onChange: (next: [number, number]) => void;
}) {
  const resolvedMin = Math.min(min, max);
  const resolvedMax = Math.max(min, max);
  const low = clamp(Math.min(value[0], value[1]), resolvedMin, resolvedMax);
  const high = clamp(Math.max(value[0], value[1]), resolvedMin, resolvedMax);
  const [lowDraft, setLowDraft] = useState(low.toFixed(precision));
  const [highDraft, setHighDraft] = useState(high.toFixed(precision));
  const span = resolvedMax - resolvedMin || 1;
  const trackStyle = useMemo(
    () => ({
      left: `${((low - resolvedMin) / span) * 100}%`,
      right: `${100 - ((high - resolvedMin) / span) * 100}%`,
    }),
    [high, low, resolvedMin, span],
  );

  useEffect(() => setLowDraft(low.toFixed(precision)), [low, precision]);
  useEffect(() => setHighDraft(high.toFixed(precision)), [high, precision]);

  const commitLow = () => {
    const parsed = parseDraft(lowDraft);
    if (parsed == null) {
      setLowDraft(low.toFixed(precision));
      return;
    }
    onChange([clamp(parsed, resolvedMin, high), high]);
  };

  const commitHigh = () => {
    const parsed = parseDraft(highDraft);
    if (parsed == null) {
      setHighDraft(high.toFixed(precision));
      return;
    }
    onChange([low, clamp(parsed, low, resolvedMax)]);
  };

  return (
    <fieldset className={cn("range-field", className)}>
      <legend className="range-field__label">{formatScientificText(label)}</legend>
      {description ? <p className="range-field__description">{formatScientificText(description)}</p> : null}
      <div className="range-field__controls">
        <label className="range-field__number">
          <span>Low</span>
          <input
            type="number"
            min={resolvedMin}
            max={high}
            step={step}
            value={lowDraft}
            onChange={(event) => setLowDraft(event.currentTarget.value)}
            onBlur={commitLow}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
            aria-label={`${label} lower limit`}
          />
        </label>

        <div className="dual-range" aria-label={`${label} range`}>
          <div className="dual-range__rail" />
          <div className="dual-range__selection" style={trackStyle} />
          <input
            className="dual-range__input"
            type="range"
            min={resolvedMin}
            max={resolvedMax}
            step={step}
            value={low}
            onInput={(event) => onChange([Math.min(Number(event.currentTarget.value), high), high])}
            aria-label={`${label} lower handle`}
          />
          <input
            className="dual-range__input"
            type="range"
            min={resolvedMin}
            max={resolvedMax}
            step={step}
            value={high}
            onInput={(event) => onChange([low, Math.max(Number(event.currentTarget.value), low)])}
            aria-label={`${label} upper handle`}
          />
        </div>

        <label className="range-field__number">
          <span>High</span>
          <input
            type="number"
            min={low}
            max={resolvedMax}
            step={step}
            value={highDraft}
            onChange={(event) => setHighDraft(event.currentTarget.value)}
            onBlur={commitHigh}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
            aria-label={`${label} upper limit`}
          />
        </label>
      </div>
      <div className="range-field__bounds" aria-hidden="true">
        <span>{resolvedMin.toFixed(precision)}</span>
        <span>{resolvedMax.toFixed(precision)}</span>
      </div>
    </fieldset>
  );
}
