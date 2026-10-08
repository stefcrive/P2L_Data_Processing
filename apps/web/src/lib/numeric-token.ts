/** Parse numeric identifiers using the processing chart conventions. */
export function parseNumericToken(value: unknown): number | null {
  if (value == null) {
    return null;
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  const normalized = String(value)
    .trim()
    .replace(/[\u2212\u2010\u2011\u2012\u2013\u2014]/g, "-");
  if (!normalized) {
    return null;
  }
  const match = normalized.match(/[-+]?[\d.,]+/);
  if (!match) {
    return null;
  }
  let token = match[0].replace(/[\s\u00A0\u2009]/g, "");
  if (token.includes(",") && token.includes(".")) {
    if (token.lastIndexOf(",") > token.lastIndexOf(".")) {
      token = token.replace(/\./g, "").replace(",", ".");
    } else {
      token = token.replace(/,/g, "");
    }
  } else if (token.includes(",")) {
    const parts = token.split(",");
    if (parts.length > 2) {
      token = token.replace(/,/g, "");
    } else {
      // A single separator denotes decimals, including three-digit precision.
      token = token.replace(",", ".");
    }
  } else if (token.split(".").length > 2) {
    token = token.replace(/\./g, "");
  }

  const parsed = Number(token);
  return Number.isFinite(parsed) ? parsed : null;
}
