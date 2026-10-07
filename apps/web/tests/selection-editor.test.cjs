const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs'), path = require('node:path'), ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const dictionary = require('../src/lib/i18n/pt-BR.json');
let analysisInfo = { 'Identifier 1': 'Sample A', Line: 12 };
const queries = [];
let showIntensities = false, toggleSignalMode, renderedSignalFigure;
const bridge = React.createContext({ session_id: 'session', row_mapping: { sample: '12' } });
function load(relative) {
  const filename = path.join(__dirname, '../src', relative);
  const compiled = { exports: {} };
  const localRequire = name => {
    if (relative.endsWith('cycle-molecular-ratio-chart.tsx') && name === 'react') return { ...React,
      useState: () => [showIntensities, update => { showIntensities = update(showIntensities); }],
    };
    if (relative.endsWith('cycle-molecular-ratio-chart.tsx') && name === 'react/jsx-runtime') {
      const runtime = require(name);
      return { ...runtime, jsx: (type, props, key) => {
        if (props.className === 'cycle-signal-toggle') toggleSignalMode = props.onClick;
        return runtime.jsx(type, props, key);
      } };
    }
    if (name === '@/components/charts/lazy-plotly-chart') return { PlotlyChart: props => {
      renderedSignalFigure = props.figure;
      return React.createElement('div', { 'data-chart-height': props.initialHeight });
    } };
    if (name === '@/components/layout/language-provider') return { useTranslation: () => text => dictionary[text] ?? text };
    if (name === './consultation-context') return { MetrologyEvidenceBridge: bridge };
    if (name === '@tanstack/react-query') return { useQuery: options => {
      queries.push(options);
      return { data: options.queryKey[0] === 'analysis-cycle-diagnostics'
        ? { table: [], analysis_info: analysisInfo }
        : { cycles: [], source_units: {}, raw_rows: [{ sheet_row: 12, values: { 'Source-only parameter': 7 } }] } };
    } };
    if (name === '@/lib/metrology' || name === '@/lib/api') return {};
    if (name.endsWith('cycle-molecular-ratio-chart')) return { CycleMolecularRatioChart: () => React.createElement('div', { 'data-ratio-chart': true }) };
    if (name === './saturation-figure-card') return { SATURATION_COLOR_AXIS_OPTIONS: [], SaturationFigureCard: () => null };
    if (name.startsWith('@/')) return load(name.slice(2) + (name.includes('/components/') ? '.tsx' : '.ts'));
    if (name.startsWith('./')) return load(path.relative(path.join(__dirname, '../src'), path.resolve(path.dirname(filename), name + '.tsx')));
    return require(name);
  };
  new Function('require', 'module', 'exports', ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText)(localRequire, compiled, compiled.exports);
  return compiled.exports;
}
const { AnalysisEvidence } = load('components/metrology/analysis-evidence.tsx');
const { SelectionCycleDiagnostics } = load('components/diagnostics/selection-cycle-diagnostics.tsx');
const { CycleMolecularRatioChart } = load('components/diagnostics/cycle-molecular-ratio-chart.tsx');
const props = { row: { id: 'sample', label: 'Sample A' }, run: { id: 'run' } };

test('original data has one disclosure with the diagnostic parameter table', () => {
  analysisInfo = { 'Identifier 1': 'Sample A', Line: 12 };
  const html = renderToStaticMarkup(React.createElement(AnalysisEvidence, props));
  assert.equal((html.match(/<summary>Dados originais da análise/g) ?? []).length, 1);
  assert.match(html, /raw-analysis-info/);
  assert.doesNotMatch(html, /Source-only parameter/);
});

test('missing diagnostic parameters fall back to original worksheet tables', () => {
  analysisInfo = {};
  const html = renderToStaticMarkup(React.createElement(AnalysisEvidence, props));
  assert.equal((html.match(/<summary>Dados originais da análise/g) ?? []).length, 1);
  assert.match(html, /Source-only parameter/);
});

test('split review renders criteria only in the summary and no duplicate cycle chart in the details', () => {
  analysisInfo = { Line: 12 };
  queries.length = 0;
  const summary = renderToStaticMarkup(React.createElement(AnalysisEvidence, { ...props, section: 'summary' }));
  assert.match(summary, /Faixa válida \/ critério/);
  assert.doesNotMatch(summary, /<details>|data-ratio-chart/);
  assert.ok(queries.every(query => query.enabled === false));
  const details = renderToStaticMarkup(React.createElement(AnalysisEvidence, { ...props, section: 'details' }));
  assert.doesNotMatch(details, /station-validity|data-ratio-chart/);
  assert.match(details, /raw-analysis-info/);
});

test('cycle table stays open beside the ratio chart and isotope suggestions remain selectable', () => {
  const payload = { table: [{ Cycle: 1, 'SMP Int m/z 44 (V)': 8 }], cycle_mean: { valid_cycles: 7, valid_mean: -1, valid_std_dev: .01 } };
  const html = renderToStaticMarkup(React.createElement(SelectionCycleDiagnostics, { d13: payload, d18: payload, onPick: () => {}, pickableIsotope: 'd13C' }));
  assert.match(html, /selection-cycle-overview/);
  assert.match(html, /data-ratio-chart/);
  assert.match(html, /cycle-diagnostics-table/);
  assert.doesNotMatch(html, /<details/);
  assert.equal((html.match(/<button/g) ?? []).length, 5);
});

test('the header button switches between molecular ratios and six voltage traces and back', () => {
  const rows = [{ Cycle: 1, 'SMP Int m/z 44 (V)': 8, 'SMP Int m/z 45 (V)': 10 }];
  const render = () => renderToStaticMarkup(React.createElement(CycleMolecularRatioChart, { rows }));
  showIntensities = false;
  let html = render();
  assert.match(html, /<h3>Razões moleculares por ciclo<\/h3>/);
  assert.match(html, /class="cycle-signal-toggle">Intensidades por ciclo<\/button>/);
  assert.equal(renderedSignalFigure.data.length, 4);
  toggleSignalMode();
  html = render();
  assert.match(html, /<h3>Intensidades por ciclo<\/h3>/);
  assert.match(html, /class="cycle-signal-toggle">Razões moleculares por ciclo<\/button>/);
  assert.equal(renderedSignalFigure.data.length, 6);
  assert.deepEqual(renderedSignalFigure.data[0].y, [8]);
  toggleSignalMode();
  render();
  assert.equal(renderedSignalFigure.data.length, 4);
  assert.deepEqual(renderedSignalFigure.data[0].y, [1.25]);
});
