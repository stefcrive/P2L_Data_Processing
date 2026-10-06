const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// Exercise the actual TS modules without adding a test runner dependency.
function loadModule(relative) {
  const filename = path.resolve(__dirname, '../src', relative);
  const source = fs.readFileSync(filename, 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true } });
  const module = { exports: {} };
  const localRequire = name => name.startsWith('@/') ? loadModule(name.slice(2) + '.ts') : require(path.resolve(path.dirname(filename), name));
  new Function('require', 'module', 'exports', outputText)(localRequire, module, module.exports);
  return module.exports;
}
const { translate } = loadModule('lib/i18n/translate.ts');
const { formatPlotlyDisplayText } = loadModule('lib/scientific-notation.ts');

test('English is unchanged; Portuguese covers navigation and metrology messages', () => {
  assert.equal(translate('Calibration', 'en'), 'Calibration');
  assert.equal(translate('Calibration', 'pt'), 'Calibração');
  assert.equal(translate('Instrument qualification', 'pt'), 'Qualificação do instrumento');
  assert.equal(translate('Record saved with its audit entry.', 'pt'), 'Registro salvo com sua entrada de auditoria.');
});
test('dynamic scientific captions retain values and symbols', () => {
  assert.equal(translate('3 rows · 12 cycles · 2 files', 'pt'), '3 linhas · 12 ciclos · 2 arquivos');
  assert.equal(translate('Method v1', 'pt'), 'Método v1');
  assert.equal(translate('δ¹³C / ‰ VPDB', 'pt'), 'δ¹³C / ‰ VPDB');
  assert.equal(translate('δ¹⁸O / ‰', 'pt'), 'δ¹⁸O / ‰');
  assert.equal(translate('δ¹³C precision u / ‰, individual-observation SD', 'pt'), 'Precisão u de δ¹³C / ‰, desvio padrão de observações individuais');
  assert.equal(translate('Individual internal SD limit / ‰', 'pt'), 'Limite de Desvio padrão interno individual / ‰');
});
test('inline spacing survives translation and missing strings fall back safely', () => {
  assert.equal(translate(' rows ', 'pt'), ' linhas ');
  assert.equal(translate('NBS19', 'pt'), 'NBS19');
  assert.equal(translate('workbook.xlsx', 'pt'), 'workbook.xlsx');
  assert.equal(translate('d13c', 'pt'), 'd13c');
  assert.equal(translate(null, 'pt'), null);
  assert.equal(translate(0.07, 'pt'), 0.07);
});
test('chart localization leaves numerical vectors, IDs and source metadata intact', () => {
  const source = { data: [{ name: 'Sample intensity', x: [1, 2], y: [0.07, 0.1], ids: ['Sample', 'Unknown'], customdata: [['Sample', 'Species']], meta: { label: 'Original' } }], layout: { title: 'Analytical runs', xaxis: { title: { text: 'Mass / µg' } } } };
  const before = structuredClone(source);
  const localized = formatPlotlyDisplayText(source, value => translate(value, 'pt'));
  assert.equal(localized.data[0].name, 'Intensidade da amostra');
  assert.equal(localized.layout.title, 'Sequências analíticas');
  assert.equal(localized.layout.xaxis.title.text, 'Massa / µg');
  assert.deepEqual(localized.data[0].x, before.data[0].x);
  assert.deepEqual(localized.data[0].y, before.data[0].y);
  assert.deepEqual(localized.data[0].customdata, before.data[0].customdata);
  assert.deepEqual(localized.data[0].ids, before.data[0].ids);
  assert.deepEqual(localized.data[0].meta, before.data[0].meta);
  assert.deepEqual(source, before);
});
test('all translated templates retain their interpolation slots', () => {
  const dictionary = require('../src/lib/i18n/pt-BR.json');
  for (const [key, value] of Object.entries(dictionary)) {
    assert.equal(typeof value, 'string', key);
    assert.deepEqual((value.match(/\{\d+\}/g) ?? []).sort(), (key.match(/\{\d+\}/g) ?? []).sort(), key);
  }
});
