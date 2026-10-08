const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const compiled = { exports: {} };
new Function('module', 'exports', ts.transpileModule(
  fs.readFileSync(path.join(__dirname, '../src/lib/numeric-token.ts'), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS } },
).outputText)(compiled, compiled.exports);

test('three-digit fractional values preserve their decimal separator', () => {
  for (const [token, expected] of [
    ['4,802 ?', 4.802], ['4,479', 4.479], ['4.802', 4.802],
    ['-4,802', -4.802], ['66.537-66.557', 66.537], [19.987, 19.987],
    ['1.234,56', 1234.56], ['1,234.56', 1234.56],
  ]) assert.equal(compiled.exports.parseNumericToken(token), expected, String(token));
});
