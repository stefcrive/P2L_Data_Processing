/** Python ordinal days; fractional days retain the acquisition time in UTC. */
export function formatRangeValue(value: number, precision: number, date: boolean): string {
  if (!date) return value.toFixed(precision);
  return new Date(Math.round((value - 719163) * 86400000)).toISOString()
    .replace("T", " ").replace(/ 00:00:00\.000Z$/, "").replace(/Z$/, "");
}

export function parseRangeValue(text: string, date: boolean): number | null {
  const value = text.trim();
  if (!value) return null;
  if (!date) return Number.isFinite(Number(value)) ? Number(value) : null;
  if (!/^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?)?$/.test(value)) return null;
  const millis = Date.parse(value.length === 10 ? value + "T00:00:00Z" : value.replace(" ", "T") + "Z");
  if (!Number.isFinite(millis) || new Date(millis).toISOString().slice(0,10) !== value.slice(0,10)) return null;
  return millis / 86400000 + 719163;
}
