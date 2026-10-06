import type { LinearityPreviewValues } from "@/lib/linearity-preview";

type Figure = Record<string, unknown> & {
  data?: Array<Record<string, unknown>>;
};

export type DiagnosticStatisticsItem = {
  key: string;
  title: string;
  figure: Record<string, unknown>;
};

const CONTRIBUTION_TITLE = "Parameter Contributions to Variability";
const EXPLAINED_VARIANCE_TITLE = "Explained Variance by Component";
const CORRELATION_TITLE = "Spearman Correlation Matrix";

function vector(value: unknown): unknown[] | null {
  if (Array.isArray(value)) {
    return value;
  }
  if (ArrayBuffer.isView(value)) {
    return Array.from(value as unknown as ArrayLike<unknown>);
  }
  return null;
}

function finiteNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function firstTrace(figure: Figure | undefined): Record<string, unknown> | null {
  return Array.isArray(figure?.data) && figure.data.length > 0 ? figure.data[0] : null;
}

function identity(size: number): number[][] {
  return Array.from({ length: size }, (_, row) =>
    Array.from({ length: size }, (_, column) => (row === column ? 1 : 0)),
  );
}

/** Jacobi decomposition for the small symmetric covariance matrices used here. */
function symmetricEigen(matrix: number[][]): { values: number[]; vectors: number[][] } {
  const size = matrix.length;
  const values = matrix.map((row) => row.slice());
  const vectors = identity(size);
  const maxIterations = Math.max(1, size * size * 64);

  for (let iteration = 0; iteration < maxIterations; iteration += 1) {
    let p = 0;
    let q = 0;
    let largest = 0;
    for (let row = 0; row < size; row += 1) {
      for (let column = row + 1; column < size; column += 1) {
        const magnitude = Math.abs(values[row][column]);
        if (magnitude > largest) {
          largest = magnitude;
          p = row;
          q = column;
        }
      }
    }
    if (largest <= 1e-12) {
      break;
    }

    const app = values[p][p];
    const aqq = values[q][q];
    const apq = values[p][q];
    const angle = 0.5 * Math.atan2(2 * apq, app - aqq);
    const cosine = Math.cos(angle);
    const sine = Math.sin(angle);

    for (let index = 0; index < size; index += 1) {
      if (index === p || index === q) {
        continue;
      }
      const aip = values[index][p];
      const aiq = values[index][q];
      values[index][p] = cosine * aip + sine * aiq;
      values[p][index] = values[index][p];
      values[index][q] = -sine * aip + cosine * aiq;
      values[q][index] = values[index][q];
    }
    values[p][p] = cosine ** 2 * app + 2 * sine * cosine * apq + sine ** 2 * aqq;
    values[q][q] = sine ** 2 * app - 2 * sine * cosine * apq + cosine ** 2 * aqq;
    values[p][q] = 0;
    values[q][p] = 0;

    for (let row = 0; row < size; row += 1) {
      const vip = vectors[row][p];
      const viq = vectors[row][q];
      vectors[row][p] = cosine * vip + sine * viq;
      vectors[row][q] = -sine * vip + cosine * viq;
    }
  }

  const order = Array.from({ length: size }, (_, index) => index).sort(
    (left, right) => values[right][right] - values[left][left],
  );
  return {
    values: order.map((index) => Math.max(0, values[index][index])),
    vectors: Array.from({ length: size }, (_, feature) => order.map((index) => vectors[feature][index])),
  };
}

function principalComponents(rows: Array<Array<number | null>>): {
  ratios: number[];
  vectors: number[][];
  completeRowCount: number;
} | null {
  const complete = rows.filter((row): row is number[] => row.every((value) => value != null && Number.isFinite(value)));
  if (complete.length < 2 || complete[0].length === 0) {
    return null;
  }

  const featureCount = complete[0].length;
  const means = Array.from({ length: featureCount }, (_, feature) =>
    complete.reduce((sum, row) => sum + row[feature], 0) / complete.length,
  );
  const variances = means.map((mean, feature) =>
    complete.reduce((sum, row) => sum + (row[feature] - mean) ** 2, 0) / complete.length,
  );
  if (variances.every((variance) => variance <= 1e-24)) {
    return null;
  }
  const scaled = complete.map((row) =>
    row.map((value, feature) => {
      const deviation = Math.sqrt(variances[feature]);
      return deviation <= 1e-12 ? 0 : (value - means[feature]) / deviation;
    }),
  );
  const covariance = Array.from({ length: featureCount }, (_, left) =>
    Array.from({ length: featureCount }, (_, right) =>
      scaled.reduce((sum, row) => sum + row[left] * row[right], 0) / (complete.length - 1),
    ),
  );
  const decomposition = symmetricEigen(covariance);
  const totalVariance = decomposition.values.reduce((sum, value) => sum + value, 0);
  if (totalVariance <= 1e-15) {
    return null;
  }
  const componentCount = Math.min(complete.length, featureCount);
  return {
    ratios: decomposition.values.slice(0, componentCount).map((value) => value / totalVariance),
    vectors: decomposition.vectors.map((row) => row.slice(0, componentCount)),
    completeRowCount: complete.length,
  };
}

function averageRanks(values: number[]): number[] {
  const ordered = values.map((value, index) => ({ value, index })).sort((left, right) => left.value - right.value);
  const ranks = Array<number>(values.length);
  let start = 0;
  while (start < ordered.length) {
    let end = start + 1;
    while (end < ordered.length && ordered[end].value === ordered[start].value) {
      end += 1;
    }
    const average = (start + 1 + end) / 2;
    for (let index = start; index < end; index += 1) {
      ranks[ordered[index].index] = average;
    }
    start = end;
  }
  return ranks;
}

function pearson(left: number[], right: number[]): number | null {
  const leftMean = left.reduce((sum, value) => sum + value, 0) / left.length;
  const rightMean = right.reduce((sum, value) => sum + value, 0) / right.length;
  let numerator = 0;
  let leftSquares = 0;
  let rightSquares = 0;
  for (let index = 0; index < left.length; index += 1) {
    const leftDelta = left[index] - leftMean;
    const rightDelta = right[index] - rightMean;
    numerator += leftDelta * rightDelta;
    leftSquares += leftDelta ** 2;
    rightSquares += rightDelta ** 2;
  }
  const denominator = Math.sqrt(leftSquares * rightSquares);
  if (denominator <= 1e-15) {
    return null;
  }
  const result = numerator / denominator;
  return Number.isFinite(result) ? Math.max(-1, Math.min(1, result)) : null;
}

function spearman(rows: Array<Array<number | null>>, featureCount: number): {
  correlations: Array<Array<number | null>>;
  pairCounts: number[][];
} {
  const correlations = Array.from({ length: featureCount }, () => Array<number | null>(featureCount).fill(null));
  const pairCounts = Array.from({ length: featureCount }, () => Array<number>(featureCount).fill(0));
  for (let left = 0; left < featureCount; left += 1) {
    for (let right = left; right < featureCount; right += 1) {
      const leftValues: number[] = [];
      const rightValues: number[] = [];
      for (const row of rows) {
        const leftValue = row[left];
        const rightValue = row[right];
        if (leftValue != null && rightValue != null && Number.isFinite(leftValue) && Number.isFinite(rightValue)) {
          leftValues.push(leftValue);
          rightValues.push(rightValue);
        }
      }
      pairCounts[left][right] = leftValues.length;
      pairCounts[right][left] = leftValues.length;
      const correlation = leftValues.length >= 3
        ? pearson(averageRanks(leftValues), averageRanks(rightValues))
        : null;
      correlations[left][right] = correlation;
      correlations[right][left] = correlation;
    }
  }
  return { correlations, pairCounts };
}

function replaceFirstTrace(figure: Figure, trace: Record<string, unknown>): Record<string, unknown> {
  return { ...figure, data: [trace, ...(figure.data?.slice(1) ?? [])] };
}

/**
 * Rebuild the three multivariate diagnostic cards from the row values embedded in
 * the correlation heatmap. This keeps coefficient editing immediate while using
 * the same PCA and rank-correlation definitions as the API.
 */
export function buildDiagnosticStatisticsPreview(
  items: DiagnosticStatisticsItem[],
  previewValues: LinearityPreviewValues,
): Map<string, Record<string, unknown>> {
  const updated = new Map<string, Record<string, unknown>>();
  if (previewValues.size === 0) {
    return updated;
  }

  const correlationItem = items.find((item) => item.title === CORRELATION_TITLE);
  const heatmapTrace = firstTrace(correlationItem?.figure as Figure | undefined);
  const traceMeta = heatmapTrace?.meta && typeof heatmapTrace.meta === "object"
    ? (heatmapTrace.meta as Record<string, unknown>)
    : null;
  const previewMeta = traceMeta?.correlationScatterPreview && typeof traceMeta.correlationScatterPreview === "object"
    ? (traceMeta.correlationScatterPreview as Record<string, unknown>)
    : null;
  const featureLabels = (vector(previewMeta?.featureLabels) ?? []).map(String);
  const rowLabels = (vector(previewMeta?.rowLabels) ?? []).map(String);
  const sourceRows = vector(previewMeta?.values) ?? [];
  const d13Index = featureLabels.indexOf("d13C");
  const d18Index = featureLabels.indexOf("d18O");
  if (!correlationItem || !heatmapTrace || !previewMeta || d13Index < 0 || d18Index < 0 || !sourceRows.length) {
    return updated;
  }

  let valuesChanged = false;
  const rows = sourceRows.map((sourceRow, rowIndex) => {
    const parsed = (vector(sourceRow) ?? []).map(finiteNumber);
    const corrected = previewValues.get(rowLabels[rowIndex]);
    const d13 = corrected?.d13C.raw;
    const d18 = corrected?.d18O.raw;
    if (d13 != null && (parsed[d13Index] == null || Math.abs(parsed[d13Index] - d13) > 1e-12)) {
      parsed[d13Index] = d13;
      valuesChanged = true;
    }
    if (d18 != null && (parsed[d18Index] == null || Math.abs(parsed[d18Index] - d18) > 1e-12)) {
      parsed[d18Index] = d18;
      valuesChanged = true;
    }
    return parsed;
  });
  if (!valuesChanged) {
    return updated;
  }

  const featureCount = featureLabels.length;
  const correlation = spearman(rows, featureCount);
  const nextPreviewMeta = { ...previewMeta, values: rows };
  const nextHeatmap = {
    ...heatmapTrace,
    z: correlation.correlations,
    text: correlation.correlations.map((row) => row.map((value) => (value == null ? "" : value.toFixed(2)))),
    customdata: correlation.pairCounts,
    meta: { ...traceMeta, correlationScatterPreview: nextPreviewMeta },
  };
  updated.set(correlationItem.key, replaceFirstTrace(correlationItem.figure as Figure, nextHeatmap));

  const pca = principalComponents(rows);
  if (!pca) {
    return updated;
  }

  const contributionItem = items.find((item) => item.title === CONTRIBUTION_TITLE);
  const contributionTrace = firstTrace(contributionItem?.figure as Figure | undefined);
  const capturedRatio = pca.ratios.slice(0, 2).reduce((sum, value) => sum + value, 0);
  const axisLabels = (vector(previewMeta.axisLabels) ?? featureLabels).map(String);
  if (contributionItem && contributionTrace && capturedRatio > 0) {
    const contributions = Array.from({ length: featureCount }, (_, feature) => {
      const byComponent = pca.ratios.slice(0, 2).map(
        (ratio, component) => ((pca.vectors[feature]?.[component] ?? 0) ** 2 * ratio * 100) / capturedRatio,
      );
      while (byComponent.length < 2) {
        byComponent.push(0);
      }
      return {
        feature,
        total: byComponent.reduce((sum, value) => sum + value, 0),
        pc1: byComponent[0],
        pc2: byComponent[1],
      };
    }).sort((left, right) => left.total - right.total || left.feature - right.feature);
    const capturedPercent = capturedRatio * 100;
    const nextTrace = {
      ...contributionTrace,
      x: contributions.map((entry) => entry.total),
      y: contributions.map((entry) => axisLabels[entry.feature] ?? featureLabels[entry.feature]),
      text: contributions.map((entry) => `${entry.total.toFixed(1)}%`),
      customdata: contributions.map((entry) => [entry.pc1, entry.pc2, capturedPercent]),
    };
    updated.set(contributionItem.key, replaceFirstTrace(contributionItem.figure as Figure, nextTrace));
  }

  const explainedItem = items.find((item) => item.title === EXPLAINED_VARIANCE_TITLE);
  const explainedTrace = firstTrace(explainedItem?.figure as Figure | undefined);
  if (explainedItem && explainedTrace) {
    let cumulative = 0;
    const explained = pca.ratios.map((ratio, index) => {
      const percent = ratio * 100;
      cumulative += percent;
      return { label: `PC${index + 1}`, percent, cumulative };
    });
    const nextTrace = {
      ...explainedTrace,
      x: explained.map((entry) => entry.label),
      y: explained.map((entry) => entry.percent),
      text: explained.map((entry) => `${entry.percent.toFixed(1)}%`),
      customdata: explained.map((entry) => [entry.cumulative, pca.completeRowCount]),
    };
    updated.set(explainedItem.key, replaceFirstTrace(explainedItem.figure as Figure, nextTrace));
  }

  return updated;
}
