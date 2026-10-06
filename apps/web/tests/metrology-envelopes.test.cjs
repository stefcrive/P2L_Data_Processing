const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/lib/metrology-envelopes.ts'), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
const compiled = { exports: {} };
new Function('require', 'module', 'exports', outputText)(require, compiled, compiled.exports);
const { uncertaintyEnvelope, normalizationEnvelope, withSessionUncertainty } = compiled.exports;

test('binary Plotly vectors receive canonical uncertainty and excluded rows break envelopes', () => {
  const x = new Float64Array([1,2,3]);
  const rows = Object.fromEntries(['a','b','c'].map((id,i)=>[id,{run_id:'r',excluded:i===1,isotopes:{d13c:{value:i,budget:{expanded_uncertainty:.1}}}}]));
  const updated = withSessionUncertainty({data:[{type:'scatter',x:{dtype:'f8',bdata:Buffer.from(x.buffer).toString('base64')},y:new Float64Array([10,11,12]),customdata:[['a','d13C'],['b','d13C'],['c','d13C']]}]},rows,'Final');
  assert.deepEqual(updated.data.at(-1).x,[1,2,3]);
  assert.deepEqual(updated.data.at(-1).y,[0,null,2]);
  assert.equal(updated.data[1].x.filter(v=>v===null).length,2);
});

test('pointwise intervals retain missing budgets and workbook boundaries', () => {
  const traces = uncertaintyEnvelope([
    { x:1,value:2,uncertainty:.1,segment:'a' },
    { x:2,value:3,uncertainty:null,segment:'a' },
    { x:3,value:4,uncertainty:.2,segment:'b' },
  ], 'U', 'blue');
  assert.deepEqual(traces[0].y, [1.9,2.1,1.9,null,3.8,4.2,3.8,null]);
  assert.deepEqual(traces[0].x, [1,1,1,null,3,3,3,null]);
  assert.equal(traces[0].fill, 'toself');
  assert.equal(traces[0].connectgaps, false);
  assert.deepEqual(uncertaintyEnvelope([{x:1,value:2,uncertainty:null}], 'U', 'blue'), []);
});

test('normalization envelope propagates anchor means, assigned values and covariance', () => {
  const model = { measured:[-10,0], assigned:[-10,0], slope:1, intercept:0,
    input_covariance:[[.04,.01,0,0],[.01,.09,0,0],[0,0,.01,0],[0,0,0,.01]] };
  const [band] = normalizationEnvelope(model, 2, 'norm', 'blue');
  const upperAt = x => Math.max(...band.y.filter((y,i)=>band.x[i]===x));
  assert.ok(Math.abs(upperAt(-10) - (-10 + 2*Math.sqrt(.05))) < 1e-12);
  assert.ok(Math.abs(upperAt(-5) - (-5 + 2*Math.sqrt(.0425))) < 1e-12);
  assert.ok(Math.abs(upperAt(0) - 2*Math.sqrt(.10)) < 1e-12);
  assert.deepEqual(normalizationEnvelope({...model, measured:[1,1]},2,'norm','blue'), []);
});

test('reused processing figures show canonical results and U without changing imported traces', () => {
  const budget = {expanded_uncertainty:.12};
  const rows = {'4':{run_id:'a', label:'sample',comment:'5',isotopes:{d13c:{value:-2,budget},d18o:{value:-4,budget}}}};
  const raw = {type:'scatter',x:[3],y:[99],customdata:[['4','d13C']]};
  const figure = {data:[raw]};
  const updated = withSessionUncertainty(figure,rows,'Final');
  assert.deepEqual(raw.y,[99]); assert.equal(figure.data.length,1);
  const final = updated.data.at(-1);
  assert.deepEqual(final.y,[-2]); assert.deepEqual(final.error_y.array,[.12]);
  assert.deepEqual(withSessionUncertainty(updated,rows,'Final'),updated);
  const cross = withSessionUncertainty({data:[{...raw,type:'scatter3d',z:[6],customdata:[['4','cross']]}]},rows,'Final').data.at(-1);
  assert.deepEqual(cross.x,[-4]); assert.deepEqual(cross.y,[-2]); assert.deepEqual(cross.z,[6]);
  assert.deepEqual(cross.error_x.array,[.12]); assert.deepEqual(cross.error_y.array,[.12]);
});
