const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const filename = path.join(__dirname, '../src/lib/cycle-molecular-ratios.ts');
const compiled = { exports: {} };
new Function('module', 'exports', ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText)(compiled, compiled.exports);
const { cycleIntensityFigure, cycleMolecularRatioFigure } = compiled.exports;

test('ratio panels share a Y interval and span while containing both sample and reference data', () => {
  const figure = cycleMolecularRatioFigure([1, 2, 3].map(Cycle => ({ Cycle,
    'SMP Int m/z 44 (V)': 1, 'REF Int m/z 44 (V)': 1,
    'SMP Int m/z 45 (V)': 1.086 + Cycle * .00001, 'REF Int m/z 45 (V)': 1.066,
    'SMP Int m/z 46 (V)': 1.427 + Cycle * .00001, 'REF Int m/z 46 (V)': 1.4265,
  })));
  const { yaxis, yaxis2 } = figure.layout;
  assert.equal(yaxis.dtick, yaxis2.dtick);
  assert.ok(Math.abs((yaxis.range[1] - yaxis.range[0]) - (yaxis2.range[1] - yaxis2.range[0])) < 1e-12);
  for (const trace of figure.data) {
    const axis = trace.yaxis === 'y' ? yaxis : yaxis2;
    assert.ok(trace.y.every(value => value >= axis.range[0] && value <= axis.range[1]));
  }
});

test('empty and constant ratio series retain finite, nonzero Y spacing', () => {
  for (const rows of [[], [{ Cycle: 1, 'SMP Int m/z 44 (V)': 1, 'SMP Int m/z 45 (V)': 1.08 }]]) {
    const { layout } = cycleMolecularRatioFigure(rows);
    for (const axis of [layout.yaxis, layout.yaxis2]) {
      assert.ok(Number.isFinite(axis.dtick) && axis.dtick > 0);
      assert.ok(axis.range.every(Number.isFinite));
      assert.ok(axis.range[1] > axis.range[0]);
    }
  }
});

test('sample and reference ratios use their own m/z 44 signal and preserve cycle positions', () => {
  const rows = [
    { Cycle: 1, 'SMP Int m/z 44 (V)': 8, 'SMP Int m/z 45 (V)': 10, 'SMP Int m/z 46 (V)': 12,
      'REF Int m/z 44 (V)': 10, 'REF Int m/z 45 (V)': 11, 'REF Int m/z 46 (V)': 15 },
    { Cycle: 3, 'SMP Int m/z 44 (V)': 4, 'SMP Int m/z 45 (V)': 5, 'SMP Int m/z 46 (V)': 6,
      'REF Int m/z 44 (V)': 5, 'REF Int m/z 45 (V)': 5.5, 'REF Int m/z 46 (V)': 7.5 },
  ];
  const before = structuredClone(rows);
  const figure = cycleMolecularRatioFigure(rows);
  assert.deepEqual(figure.data.map(trace => trace.y), [[1.25, 1.25], [1.1, 1.1], [1.5, 1.5], [1.5, 1.5]]);
  assert.deepEqual(figure.data.map(trace => trace.x), Array(4).fill([1, 3]));
  assert.deepEqual(rows, before);
});

test('absent, nonpositive and nonfinite collector values leave gaps instead of fake ratios', () => {
  const rows = [null, 0, -1, NaN, Infinity, '2'].map((signal, index) => ({
    Cycle: index + 1, 'SMP Int m/z 44 (V)': signal, 'SMP Int m/z 45 (V)': 10,
    'REF Int m/z 44 (V)': 10, 'REF Int m/z 45 (V)': signal,
  }));
  for (const trace of cycleMolecularRatioFigure(rows).data) {
    assert.deepEqual(trace.y, Array(6).fill(null));
    assert.equal(trace.connectgaps, false);
  }
});

test('legacy STD collector names supply reference gas ratios', () => {
  const figure = cycleMolecularRatioFigure([{ Cycle: 2, 'STD Int m/z 44 (V)': 8,
    'STD Int m/z 45 (V)': 10, 'STD Int m/z 46 (V)': 12 }]);
  assert.deepEqual(figure.data[1].y, [1.25]);
  assert.deepEqual(figure.data[3].y, [1.5]);
  assert.deepEqual(figure.data[0].y, [null]);
});

test('intensity mode restores all six raw voltage series without ratio normalization', () => {
  const rows = [{ Cycle: 1, 'SMP Int m/z 44 (V)': 8, 'SMP Int m/z 45 (V)': 10, 'SMP Int m/z 46 (V)': 12,
    'REF Int m/z 44 (V)': 10, 'REF Int m/z 45 (V)': 11, 'REF Int m/z 46 (V)': 15 },
    { Cycle: 2, 'REF Int m/z 44 (V)': 9, 'REF Int m/z 45 (V)': 10, 'REF Int m/z 46 (V)': 14 }];
  const before = structuredClone(rows);
  const figure = cycleIntensityFigure(rows);
  assert.deepEqual(figure.data.map(trace => trace.y), [[8, null], [10, 9], [10, null], [11, 10], [12, null], [15, 14]]);
  assert.deepEqual(figure.data.map(trace => trace.name), ['44.00 m/z SMP', '44.00 m/z REF', '45.00 m/z SMP', '45.00 m/z REF', '46.00 m/z SMP', '46.00 m/z REF']);
  assert.equal(figure.layout.yaxis.title, 'Intensity (V)');
  assert.deepEqual(rows, before);
});
