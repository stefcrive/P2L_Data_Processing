const SUPERSCRIPT_DIGITS: Record<string, string> = {
  "0": "⁰",
  "1": "¹",
  "2": "²",
  "3": "³",
  "4": "⁴",
  "5": "⁵",
  "6": "⁶",
  "7": "⁷",
  "8": "⁸",
  "9": "⁹",
};

const SUBSCRIPT_DIGITS: Record<string, string> = {
  "0": "₀",
  "1": "₁",
  "2": "₂",
  "3": "₃",
  "4": "₄",
  "5": "₅",
  "6": "₆",
  "7": "₇",
  "8": "₈",
  "9": "₉",
};

const ISOTOPE_ELEMENTS: Record<string, string> = {
  c: "C",
  h: "H",
  n: "N",
  o: "O",
  s: "S",
};

const MOLECULES = ["H3PO4", "HCO3", "N2O", "NO3", "SO4", "CO3", "CO2", "CH4", "NH3", "H2O", "NO2", "SO2", "O2", "N2"];

function withDigitStyle(value: string, digits: Record<string, string>): string {
  return Array.from(value, (digit) => digits[digit] ?? digit).join("");
}

function formatMolecule(formula: string): string {
  return formula.replace(/\d+/g, (digits) => withDigitStyle(digits, SUBSCRIPT_DIGITS));
}

/**
 * Formats scientific notation only at presentation boundaries. API keys, dataframe
 * columns, and exported data must retain their original machine-readable names.
 */
export function formatScientificText(value: string): string {
  let formatted = value;

  formatted = formatted.replace(
    /(?:\bd|δ)\s*(\d{1,3})([CHNOS])\s*\/\s*(\d{1,3})([CHNOS])(?=$|[^A-Za-z0-9])/gi,
    (_match, firstMass: string, firstElement: string, secondMass: string, secondElement: string) =>
      `δ${withDigitStyle(firstMass, SUPERSCRIPT_DIGITS)}${ISOTOPE_ELEMENTS[firstElement.toLowerCase()] ?? firstElement}/${withDigitStyle(secondMass, SUPERSCRIPT_DIGITS)}${ISOTOPE_ELEMENTS[secondElement.toLowerCase()] ?? secondElement}`,
  );

  formatted = formatted.replace(
    /(?:\bd|δ)\s*(\d{1,3})([CHNOS])(?=$|[^A-Za-z0-9])/gi,
    (_match, mass: string, element: string) =>
      `δ${withDigitStyle(mass, SUPERSCRIPT_DIGITS)}${ISOTOPE_ELEMENTS[element.toLowerCase()] ?? element}`,
  );

  formatted = formatted.replace(
    /\b(1|2|12|13|14|15|16|17|18|32|33|34|36)([CHNOS])(?=$|[^A-Za-z0-9])/g,
    (_match, mass: string, element: string) => `${withDigitStyle(mass, SUPERSCRIPT_DIGITS)}${element}`,
  );

  for (const molecule of MOLECULES) {
    const pattern = new RegExp(`(^|[^A-Za-z])${molecule}(?![A-Za-z0-9])`, "g");
    formatted = formatted.replace(pattern, (_match, prefix: string) => `${prefix}${formatMolecule(molecule)}`);
  }

  return formatted.replace(/\b(?:permil|per\s+mil|per\s+mille)\b/gi, "‰");
}

const PLOTLY_DISPLAY_KEYS = new Set([
  "hovertemplate",
  "hovertext",
  "label",
  "name",
  "text",
  "texttemplate",
  "ticktext",
]);

const PLOTLY_DATA_KEYS = new Set(["customdata", "ids", "meta", "x", "y", "z"]);

function formatPlotlyValue(value: unknown, key = ""): unknown {
  if (PLOTLY_DATA_KEYS.has(key)) {
    return value;
  }
  if (typeof value === "string") {
    return PLOTLY_DISPLAY_KEYS.has(key) ? formatScientificText(value) : value;
  }
  if (Array.isArray(value)) {
    if (!value.some((item) => typeof item === "string" || (item && typeof item === "object"))) {
      return value;
    }
    if (PLOTLY_DISPLAY_KEYS.has(key)) {
      return value.map((item) => (typeof item === "string" ? formatScientificText(item) : formatPlotlyValue(item, key)));
    }
    return value.map((item) => formatPlotlyValue(item));
  }
  if (!value || typeof value !== "object") {
    return value;
  }

  const next: Record<string, unknown> = {};
  for (const [childKey, childValue] of Object.entries(value as Record<string, unknown>)) {
    if (childKey === "title" && typeof childValue === "string") {
      next[childKey] = formatScientificText(childValue);
    } else {
      next[childKey] = formatPlotlyValue(childValue, childKey);
    }
  }
  return next;
}

export function formatPlotlyDisplayText<T>(value: T): T {
  return formatPlotlyValue(value) as T;
}
