type Axis = {range?: unknown; dtick?: unknown; tickmode?: unknown; overlaying?: unknown; visible?: unknown};
export type StandardAxisLayout = {yaxis?: Axis; yaxis2?: Axis; meta?: {equalStandardScale?: boolean; standardMeanOffset?: number}};

function range(value: unknown): [number, number] | null {
  return Array.isArray(value) && value.length === 2 && value.every(v => typeof v === "number" && Number.isFinite(v)) && value[0] !== value[1] ? [value[0], value[1]] : null;
}

function eventRange(update: Record<string, unknown>, axis: string, current: unknown): [number, number] | null {
  return range(update[`${axis}.range`]) ?? range([update[`${axis}.range[0]`], update[`${axis}.range[1]`]]) ?? range(current);
}

/** Either overlaid axis may receive a zoom gesture. Preserve that gesture on both axes. */
export function standardAxisRelayout(layout: StandardAxisLayout, update: Record<string, unknown> = {}): Record<string, unknown> {
  const primary = layout.yaxis, secondary = layout.yaxis2;
  if (!primary || !secondary || secondary.overlaying !== "y" || secondary.visible === false || !layout.meta?.equalStandardScale) return {};
  const primaryRange = eventRange(update, "yaxis", primary.range), secondaryRange = eventRange(update, "yaxis2", secondary.range);
  if (!primaryRange || !secondaryRange) return {};
  const offset = layout.meta.standardMeanOffset ?? (secondaryRange[0] + secondaryRange[1] - primaryRange[0] - primaryRange[1]) / 2;
  const changed = (axis: string) => Object.keys(update).some(key => new RegExp(`^${axis}\\.(range(?:\\[\\d\\])?|autorange)$`).test(key));
  const secondaryGesture = changed("yaxis2") && !changed("yaxis");
  const targetPrimary = secondaryGesture ? secondaryRange.map(v => v - offset) : primaryRange;
  const targetSecondary = targetPrimary.map(v => v + offset);
  const result: Record<string, unknown> = {};
  for (const [axis, original, target] of [["yaxis", primary.range, targetPrimary], ["yaxis2", secondary.range, targetSecondary]] as const) {
    const existing = range(original);
    if (!existing || existing.some((v, i) => Math.abs(v - target[i]) > 1e-9)) {
      result[`${axis}.range`] = target;
      result[`${axis}.autorange`] = false;
    }
  }
  const tick = Number(primary.dtick);
  if (Number.isFinite(tick) && tick > 0 && (secondary.tickmode !== "linear" || Number(secondary.dtick) !== tick)) {
    result["yaxis2.tickmode"] = "linear";
    result["yaxis2.dtick"] = tick;
  }
  return result;
}
