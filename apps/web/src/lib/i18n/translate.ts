import portuguese from "./pt-BR.json";
import { formatScientificText } from "@/lib/scientific-notation";

export type Language = "en" | "pt";
const messages: Record<string, string> = Object.fromEntries(Object.entries(portuguese).flatMap(([key, value]) => [
  [key, value], [formatScientificText(key), value],
]));
const patterns = Object.entries(messages)
  .filter(([key]) => /\{\d+\}/.test(key))
  .sort(([a], [b]) => b.replace(/\{\d+\}/g, "").length - a.replace(/\{\d+\}/g, "").length)
  .map(([key, value]) => {
    const slots: string[] = [];
    const escaped = key.split(/(\{\d+\})/g).map(part => {
      if (/^\{\d+\}$/.test(part)) { slots.push(part); return "([\\s\\S]*?)"; }
      return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }).join("");
    return { regex: new RegExp(`^${escaped}$`), value, slots };
  });

/** Translate presentation text only. Source records and API values stay intact. */
export function translate<T>(value: T, language: Language): T {
  if (language === "en" || typeof value !== "string" || !value) return value;
  const key = value.replace(/\s+/g, " ").trim();
  const direct = messages[value] ?? messages[key];
  if (direct !== undefined) {
    // Keep spaces in inline JSX, where React uses them to separate adjacent values.
    const leading = /^\s+/.exec(value)?.[0] ?? "";
    const trailing = /\s+$/.exec(value)?.[0] ?? "";
    return (messages[value] !== undefined ? direct : leading + direct + trailing) as T;
  }
  for (const pattern of patterns) {
    const match = pattern.regex.exec(value) ?? pattern.regex.exec(key);
    if (match) {
      const captures = Object.fromEntries(pattern.slots.map((slot, index) => [slot, translate(match[index + 1], language)]));
      return pattern.value.replace(/\{\d+\}/g, slot => captures[slot] ?? slot) as T;
    }
  }
  return value;
}
