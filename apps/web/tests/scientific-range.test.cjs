const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/lib/scientific-range.ts'), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const compiled = { exports: {} };
new Function('require','module','exports',outputText)(require,compiled,compiled.exports);
const {formatRangeValue,parseRangeValue} = compiled.exports;
test('UTC ordinals retain fractional acquisition time without timezone shifts',()=>{
  for(const ordinal of [719163,738830,738840.8125]) {
    assert.equal(parseRangeValue(formatRangeValue(ordinal,3,true),true),ordinal);
  }
  assert.equal(formatRangeValue(719163,3,true),'1970-01-01');
});
test('invalid date and empty numeric edits do not silently become values',()=>{
  for(const input of ['', '2026-02-31','2026-13-01','Oct 6','2026-10-06 27:00']) assert.equal(parseRangeValue(input,true),null);
  assert.equal(parseRangeValue('',false),null);
  assert.equal(parseRangeValue('-15.591',false),-15.591);
  assert.equal(formatRangeValue(-15.591,3,false),'-15.591');
});
