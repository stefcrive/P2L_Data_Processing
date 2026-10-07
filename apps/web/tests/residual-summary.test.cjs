const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs'), path = require('node:path'), ts = require('typescript');
const compiled = {exports:{}};
new Function('module','exports',ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/lib/residual-summary.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(compiled,compiled.exports);
const {residualEffectSummary,qcSdEvidence,correctionUncertaintyRange} = compiled.exports;

test('effect uncertainty and interval use only the retained predictor range',()=>{
  const fit={effect_span:-.08,practical_threshold:.01,slope_se:.002,slope_ci95:[-.014,-.002],points:[{x:2,y:1},{x:12,y:2},{x:1000,y:3,excluded_from_fit:true}]};
  const summary=residualEffectSummary(fit);
  assert.equal(summary.uncertainty,.02);
  assert.deepEqual(summary.interval,[-.14,-.02]);
  assert.equal(summary.relevant,true);
  assert.equal(residualEffectSummary({...fit,slope_se:null}).uncertainty,null);
  assert.equal(residualEffectSummary().effect,null);
});

test('SD evidence requires enough pairs, practical improvement, and a positive paired interval',()=>{
  const review={paired_n:20,sd_reduction_fraction:.15,reduction_interval95:[.02,.28],criteria:{minimum_qc:10,minimum_sd_reduction_fraction:.1}};
  assert.equal(qcSdEvidence(review),'SD reduction demonstrated');
  assert.equal(qcSdEvidence({...review,sd_reduction_fraction:-.05}),'No SD reduction');
  assert.equal(qcSdEvidence({...review,sd_reduction_fraction:.05}),'SD reduction below criterion');
  assert.equal(qcSdEvidence({...review,paired_n:5}),'SD reduction below criterion');
  assert.equal(qcSdEvidence({...review,reduction_interval95:[-.01,.3]}),'SD reduction inconclusive');
  assert.equal(qcSdEvidence({...review,reduction_interval95:null}),'SD reduction inconclusive');
  assert.equal(qcSdEvidence({...review,sd_reduction_fraction:null}),'Comparison unavailable');
});

test('preview uncertainty range keeps missing manual uncertainty unavailable',()=>{
  assert.deepEqual(correctionUncertaintyRange({points:[{u_correction:.003},{u_correction:.02},{u_correction:99,excluded_from_fit:true}]}),[.003,.02]);
  assert.equal(correctionUncertaintyRange({points:[{u_correction:null}]}),null);
  assert.equal(correctionUncertaintyRange({points:[{u_correction:.01},{}]}),null);
  assert.equal(correctionUncertaintyRange(),null);
});
