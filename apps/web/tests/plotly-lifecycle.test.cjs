const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const moduleResult = { exports: {} };
const source = fs.readFileSync(path.join(__dirname, '../src/lib/plotly-lifecycle.ts'), 'utf8');
new Function('module', 'exports', ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText)(moduleResult, moduleResult.exports);

test('late mouse-out after purge is harmless through both Plotly unhover paths', () => {
  // Exercise the installed Plotly implementation, including wrapped -> raw dispatch.
  const unhover = { exports: {} };
  const plotlySource = fs.readFileSync(require.resolve('plotly.js/src/components/dragelement/unhover'), 'utf8');
  new Function('require', 'module', plotlySource)(name => {
    if (name.endsWith('/dom')) return { getGraphDiv: graph => graph };
    if (name.endsWith('/throttle')) return { clear() {} };
    return {};
  }, unhover);
  const dragElement = { unhoverRaw: unhover.exports.raw };
  moduleResult.exports.guardPlotlyUnhover(unhover.exports, dragElement);
  const guarded = unhover.exports.raw;
  moduleResult.exports.guardPlotlyUnhover(unhover.exports, dragElement);
  assert.equal(unhover.exports.raw, guarded);

  let removed = 0;
  const graph = {
    _fullLayout: { _hoverlayer: { selectAll: () => ({ remove: () => removed++ }) } },
    _hoverdata: ['point'],
  };
  unhover.exports.wrapped(graph);
  assert.equal(removed, 3);
  assert.equal(graph._hoverdata, undefined);
  delete graph._fullLayout;
  assert.doesNotThrow(() => unhover.exports.wrapped(graph));
  assert.doesNotThrow(() => dragElement.unhoverRaw(graph));
  assert.equal(removed, 3);
});
