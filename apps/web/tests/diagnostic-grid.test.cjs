const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs'), path = require('node:path'), ts = require('typescript');
function load(name) {
  const compiled = { exports: {} };
  new Function('module', 'exports', 'require', ts.transpileModule(fs.readFileSync(path.join(__dirname, `../src/lib/${name}.ts`), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText)(compiled, compiled.exports, key => load(key.replace('./', '')));
  return compiled.exports;
}
const { groupDiagnosticGridItems, matchDiagnosticMarkerStyles, withCycleSignalIntensity } = load('diagnostic-grid');
const { filterStationFigure } = load('station-chart-filters');
const reference = { data: [{ type: 'scatter', mode: 'markers',
  customdata: [['sample', 'Sample A', 'A1', 'Coral', '5'], ['standard', 'SHP2L', 'Q1', 'Standard', '10']],
  marker: { color: [5, 10], colorscale: 'Viridis', symbol: ['circle', 'circle-open'], showscale: true },
  hovertemplate: 'Identifier 1: %{customdata[1]}<br>Identifier 2: %{customdata[2]}<br>Row: %{customdata[0]}<br>X: %{x}<br>Y: %{y}<extra></extra>',
}] };

test('pressure chart cycle selection changes only I44 by row identity and omits unavailable cycles', () => {
  const source = { data: [{ type: 'scatter', mode: 'markers', x: [99, 88, 77], y: [.1, .2, .3],
    customdata: [['standard', 'd13C'], ['sample', 'd13C'], ['missing', 'd13C']],
    marker: { color: '#167d87' } }], layout: { xaxis: { title: 'Mean I44 / V', showgrid: true }, yaxis: { title: 'Pressure difference / V' } } };
  const before = structuredClone(source);
  const summaries = { sample: { first_valid: 14, last_valid: 8, average: 34 / 3 }, standard: { first_valid: 10, last_valid: 6, average: 8 } };
  for (const mode of ['first_valid', 'last_valid', 'average']) {
    const result = withCycleSignalIntensity(source, summaries, mode, `I44 / V · ${mode}`);
    assert.deepEqual(result.data[0].x, [summaries.standard[mode], summaries.sample[mode], null]);
    assert.deepEqual(result.data[0].y, source.data[0].y);
    assert.deepEqual(result.data[0].customdata, source.data[0].customdata);
    assert.deepEqual(result.layout.yaxis, source.layout.yaxis);
    assert.equal(result.layout.xaxis.showgrid, true);
    assert.equal(result.layout.xaxis.title.text, `I44 / V · ${mode}`);
    const styled = matchDiagnosticMarkerStyles(result, reference);
    assert.deepEqual(styled.data[0].marker.symbol, ['circle-open', 'circle', 'circle']);
    assert.deepEqual(styled.data[0].x, result.data[0].x);
  }
  assert.deepEqual(source, before);
  assert.deepEqual(withCycleSignalIntensity(source, undefined, 'average', '').data[0].x, [null, null, null]);
});

test('appended multivariate charts join the existing section without duplicate headings', () => {
  const items = [
    { group: 'Multivariate Overview', key: 'pca' }, { group: 'd13C', key: 'carbon' },
    { group: 'Multivariate Overview', key: 'calibration_3d' },
    { group: 'Multivariate Overview', key: 'session-intensity-pressure' },
  ];
  const groups = groupDiagnosticGridItems(items);
  assert.deepEqual(groups.map(group => group.name), ['Multivariate Overview', 'd13C']);
  assert.deepEqual(groups[0].items.map(item => item.key), ['pca', 'calibration_3d', 'session-intensity-pressure']);
});

test('3D colors and standard symbols match diagnostics by identity and preserve scientific axes', () => {
  const source = { data: [{ type: 'scatter3d', mode: 'markers', name: 'Old species styling',
    x: [-5, -6], y: [-1, -2], z: [8, 9],
    customdata: [['standard', 'cross', 'SHP2L', 'Q1'], ['sample', 'cross', 'Sample A', 'A1']],
    marker: { color: [99, 88], symbol: 'diamond', colorbar: { title: 'Wrong parameter' } },
  }], layout: { title: 'Calibration 3D Chart (Z-axis: 1  Cycle Int  Samp  44)', scene: { zaxis: { title: 'Sample intensity' } } } };
  const before = structuredClone(source), referenceBefore = structuredClone(reference);
  const result = matchDiagnosticMarkerStyles(source, reference);
  assert.deepEqual(result.data[0].marker.color, [10, 5]);
  assert.deepEqual(result.data[0].marker.symbol, ['circle-open', 'circle']);
  assert.equal(result.data[0].marker.colorscale, 'Viridis');
  assert.equal(result.data[0].marker.colorbar, undefined);
  for (const axis of ['x', 'y', 'z']) assert.deepEqual(result.data[0][axis], source.data[0][axis]);
  assert.deepEqual(result.layout.scene, source.layout.scene);
  assert.equal(result.layout.title, source.layout.title);
  assert.match(result.data[0].hovertemplate, /Identifier 1: %\{customdata\[2\]\}/);
  assert.match(result.data[0].hovertemplate, /Z: %\{z\}/);
  assert.deepEqual(source, before);
  assert.deepEqual(reference, referenceBefore);
});

test('pressure chart follows diagnostic filters and retains identifiers for selection and QC flags', () => {
  const source = { data: [{ type: 'scatter', mode: 'markers', x: [1, 2, 3], y: [.1, .2, .3],
    customdata: [['sample', 'd13C'], ['outside', 'd13C'], ['standard', 'd13C']], marker: { color: '#167d87' } }] };
  const result = matchDiagnosticMarkerStyles(source, reference);
  assert.deepEqual(result.data[0].x, [1, null, 3]);
  assert.deepEqual(result.data[0].y, [.1, null, .3]);
  assert.deepEqual(result.data[0].customdata[0], ['sample', 'cross', 'Sample A', 'A1', 'Coral', '5']);
  const flagged = filterStationFigure(result, [{ row: 'sample', isotope: 'd18O', category: 'range' }]);
  assert.equal(flagged.data[0].marker.symbol[0], 'diamond');
});

test('partial saturation keeps the same orange diamond overlay on the supplemental plots', () => {
  const withPartial = { data: [...reference.data, { mode: 'markers', name: 'Partially Saturated Collectors', customdata: [reference.data[0].customdata[0]], marker: { color: '#ff7f0e' } }] };
  const source = { data: [{ type: 'scatter3d', mode: 'markers', x: [1, 2], y: [3, 4], z: [5, 6], customdata: [['sample'], ['standard']] }] };
  const result = matchDiagnosticMarkerStyles(source, withPartial);
  assert.equal(result.data.length, 2);
  assert.equal(result.data[1].marker.symbol, 'diamond-open');
  assert.equal(result.data[1].marker.color, '#ff7f0e');
  assert.deepEqual(result.data[1].z, [5, null]);
});

test('encoded Plotly color vectors follow observation order', () => {
  const encoded = { ...reference.data[0].marker, color: { dtype: 'f8', bdata: Buffer.from(new Float64Array([5, 10]).buffer).toString('base64') } };
  const result = matchDiagnosticMarkerStyles({ data: [{ mode: 'markers', x: [1, 2], y: [3, 4], customdata: [['standard'], ['sample']] }] }, { data: [{ ...reference.data[0], marker: encoded }] });
  assert.deepEqual(result.data[0].marker.color, [10, 5]);
});
