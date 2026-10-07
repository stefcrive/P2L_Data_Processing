const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs'), path = require('node:path'), ts = require('typescript');
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const dictionary = require('../src/lib/i18n/pt-BR.json');
const translate = value => typeof value === 'string' ? dictionary[value] ?? value : value;
const shared = {
  Panel:({title,children})=>React.createElement('section',null,title,children),
  Field:({label,children})=>React.createElement('label',null,label,children),
  Status:({value})=>React.createElement('span',null,value),
  Inspect:()=>null, Empty:({children})=>React.createElement('div',null,children),
};
function load(relative) {
  const filename = path.join(__dirname,'../src',relative);
  const compiled = {exports:{}};
  const localRequire = name => {
    if(name==='@/components/layout/language-provider')return {useTranslation:()=>translate};
    if(name==='@/lib/metrology')return {isotopes:['d13c','d18o'],isotopeLabel:{d13c:'δ¹³C',d18o:'δ¹⁸O'}};
    if(name==='./shared')return shared;
    if(name==='./results-station')return {Chart:({title})=>React.createElement('div',{'data-chart':true},title)};
    if(name==='./consultation-context')return {MetrologyChartHeight:React.createContext(340)};
    if(name==='./residual-controls'||name==='./uncertainty-workspace'||name==='@/lib/metrology-envelopes')return {};
    if(name==='@/lib/residual-summary')return load('lib/residual-summary.ts');
    return require(name);
  };
  new Function('require','module','exports',ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText)(localRequire,compiled,compiled.exports);
  return compiled.exports;
}
const {SessionResiduals,CorrectionValidation} = load('components/metrology/session-science.tsx');
const effectKeys=['intensity_dependence','sample_reference_dependence','pressure_adjusted_dependence','pressure_dependence','pressure_residual','mass_dependence','drift','memory'];
const fit={status:'estimated',n:2,slope:.01,slope_se:.002,intercept:1,effect_span:.1,practical_threshold:.01,r_squared:.7,slope_ci95:[.006,.014],points:[{id:'a',x:0,y:1},{id:'b',x:10,y:1.1}]};
const fits=Object.fromEntries(effectKeys.map(key=>[key,fit]));
const material={material_id:'qc',label:'QC',isotopes:{d13c:fits,d18o:fits}};
const review={status:'not_improved',before:{sd:.059},after:{sd:.064},paired_n:111,total_qc:111,sd_reduction_fraction:-.0847,reduction_interval95:[-.15,-.01],criteria:{minimum_qc:10,minimum_sd_reduction_fraction:.1},reasons:[]};
const analysis={rows:[],diagnostics_before:{materials:[material]},diagnostics_after:{materials:[material]},outliers:{flags:[]},correction_review:{d13c:review,d18o:review}};
const props={analysis,materialId:'qc',mapping:{},interact:()=>({}),overrides:{},saveOverride:async()=>{}};

test('residual table has eight effects, exactly two value lines per cell, and no repeated SD',()=>{
  const html=renderToStaticMarkup(React.createElement(SessionResiduals,props));
  const table=html.match(/<table class="station-residual-table">[\s\S]*?<\/table>/)[0];
  assert.equal((table.match(/scope="row"/g)??[]).length,8);
  const cells=[...table.matchAll(/<td[^>]*class="station-effect-value[^>]*>([\s\S]*?)<\/td>/g)];
  assert.equal(cells.length,32);
  for(const [,cell] of cells){
    assert.equal((cell.match(/<strong/g)??[]).length,1);
    assert.equal((cell.match(/<small/g)??[]).length,1);
    assert.match(cell,/Δ = .*±/);
    assert.match(cell,/b = .*R²/);
    assert.doesNotMatch(cell,/SD:|DP:|n=/);
  }
  assert.doesNotMatch(table,/Limiar de relevância/);
  assert.equal((html.match(/data-chart="true"/g)??[]).length,16);
  assert.doesNotMatch(html,/Signal intensity versus pressure adjustment difference/);
  for(const key of effectKeys)assert.ok(html.includes(`id="residual-${key}"`));
});

test('QC deterioration is visible once per isotope without claiming validation',()=>{
  const html=renderToStaticMarkup(React.createElement(CorrectionValidation,{analysis}));
  assert.equal((html.match(/0.0590 → 0.0640/g)??[]).length,2);
  assert.equal((html.match(/Sem redução do DP/g)??[]).length,2);
  assert.equal((html.match(/not_improved/g)??[]).length,2);
});

test('manual preview keeps coefficient uncertainty unavailable and labels fitting QC',()=>{
  const draft={...fit,before:{sd:.059},after:{sd:.02},model:{slope:.01,u_slope:null,degree:1,x_ref:5}};
  const html=renderToStaticMarkup(React.createElement(SessionResiduals,{...props,analysis:{...analysis,residual_previews:{'qc:intensity_dependence:d13c':draft}}}));
  assert.match(html,/CQ do ajuste; validação independente pendente/);
  assert.match(html,/u_corr = —/);
  assert.match(html,/Componente do coeficiente; incerteza total não avaliada/);
});
