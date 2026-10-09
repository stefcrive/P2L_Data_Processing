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
  Num:()=>React.createElement('input',{type:'number'}),
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
    if(name==='./results-station')return {Chart:({title,data})=>React.createElement('div',{'data-chart':true,'data-traces':JSON.stringify(data)},title)};
    if(name==='./consultation-context')return {MetrologyChartHeight:React.createContext(340)};
    if(name==='./residual-controls')return load('components/metrology/residual-controls.tsx');
    if(name==='./uncertainty-workspace'||name==='@/lib/metrology-envelopes')return {};
    if(name==='@/lib/residual-summary')return load('lib/residual-summary.ts');
    return require(name);
  };
  new Function('require','module','exports',ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText)(localRequire,compiled,compiled.exports);
  return compiled.exports;
}
const {SessionResiduals,SessionOutlierTable} = load('components/metrology/session-science.tsx');
const effectKeys=['intensity_dependence','sample_reference_dependence','pressure_adjusted_dependence','pressure_dependence','pressure_residual','mass_dependence','drift','memory'];
const fit={status:'estimated',n:2,slope:.01,slope_se:.002,intercept:1,effect_span:.1,practical_threshold:.01,r_squared:.7,slope_ci95:[.006,.014],points:[{id:'a',x:0,y:1},{id:'b',x:10,y:1.1}]};
const fits=Object.fromEntries(effectKeys.map(key=>[key,fit]));
const material={material_id:'qc',label:'QC',isotopes:{d13c:fits,d18o:fits}};
const review={status:'not_improved',before:{sd:.059},after:{sd:.064},paired_n:111,total_qc:111,sd_reduction_fraction:-.0847,reduction_interval95:[-.15,-.01],criteria:{minimum_qc:10,minimum_sd_reduction_fraction:.1},reasons:[]};
const analysis={rows:[],diagnostics_before:{materials:[material]},diagnostics_after:{materials:[material]},outliers:{flags:[]},correction_review:{d13c:review,d18o:review}};
const props={analysis,materialId:'qc',mapping:{},interact:()=>({}),overrides:{},saveOverride:async()=>{}};

const decisions={
  d13c:{intensity_dependence:{status:'applied',n:8,before:{sd:.059},after:{sd:.02},sd_reduction_fraction:.66,applied_n:13,model:{slope:.01,u_slope:.002}},sample_reference_dependence:{status:'not_selected',n:8},pressure_adjusted_dependence:{status:'insufficient_evidence',n:2}},
  d18o:{intensity_dependence:{status:'not_improved',n:8,before:{sd:.1},after:{sd:.11},sd_reduction_fraction:-.1},sample_reference_dependence:{status:'disabled',n:0},pressure_adjusted_dependence:{status:'uncertainty_required',n:8}},
};
const correctedFit={...fit,points:[{id:'a',x:0,y:.6},{id:'b',x:10,y:.61}]};
const correctedMaterial={...material,isotopes:{d13c:Object.fromEntries(effectKeys.map(k=>[k,correctedFit])),d18o:fits}};
const context={...analysis,rows:['a','b'].map(id=>({id,isotopes:{d13c:{residual_correction:{}},d18o:{residual_correction:{}}}})),residual_qc_id:'qc',residual_corrections:decisions,diagnostics_after:{materials:[correctedMaterial]}};
const rendered=()=>renderToStaticMarkup(React.createElement(SessionResiduals,{...props,analysis:context}));

test('outlier review separates pressure adjustment and no-signal samples',()=>{
  const flags=[{measurement_id:'p',run_id:'run',isotope:'d13c',category:'pressure_adjustment',value:1},{measurement_id:'n',run_id:'run',isotope:'d13c',category:'no_signal',value:null}];
  const state={...analysis,outliers:{method:'sigma',threshold:3,flags:[flags[1]]},qc_review_flags:[flags[0]]};
  const html=renderToStaticMarkup(React.createElement(SessionOutlierTable,{analysis:state,review:()=>null}));
  assert.ok(html.includes('Amostras com ajuste de pressão ruim'));
  assert.ok(html.includes('Amostras sem sinal'));
  assert.doesNotMatch(html,/<summary>Análises com falha/);
  assert.equal((html.match(/<tbody><tr/g)??[]).length,2);
});

test('residual table colors represent applied decisions and offers only three parameter gears',()=>{
  const html=rendered();
  const table=html.match(/<table class="station-residual-table">[\s\S]*?<\/table>/)[0];
  assert.equal((table.match(/scope="row"/g)??[]).length,8);
  assert.equal((table.match(/station-effect-value/g)??[]).length,16);
  assert.equal((table.match(/effect-small/g)??[]).length,1);
  assert.equal((table.match(/effect-relevant/g)??[]).length,1);
  assert.equal((table.match(/station-gear/g)??[]).length,3);
  for(const label of ['Applied','Not applied: QC SD did not improve','Not applied: another predictor improves QC SD more','Enter coefficient uncertainty'])assert.ok(table.includes(translate(label)));
  assert.match(table,/0.0590/);
  assert.match(table,/0.0200/);
  assert.doesNotMatch(html,/Residual correction preview|Prévia da correção residual|Manual linearity control|Controle manual/);
  assert.doesNotMatch(html,/<form/); // Parameter controls open only through a gear.
});

test('all diagnostic panels use the same corrected observations',()=>{
  const html=rendered();
  assert.equal((html.match(/data-chart="true"/g)??[]).length,16);
  const charts=[...html.matchAll(/data-traces="([^"]*)"/g)].map(m=>JSON.parse(m[1].replaceAll('&quot;','"').replaceAll('&amp;','&')));
  for(const [index,data] of charts.entries()){
    assert.deepEqual(data.find(t=>t.meta?.correctionStage==='after').y,index%2===0?[.6,.61]:[1,1.1]);
    if(index<6)assert.deepEqual(data.find(t=>t.meta?.correctionStage==='before').y,[1,1.1]);
  }
  for(const key of effectKeys)assert.ok(html.includes(`id="residual-${key}"`));
});

test('showing all data retains session QC decisions',()=>{
  const all={...correctedMaterial,material_id:'__all__',label:'All available data'};
  const html=renderToStaticMarkup(React.createElement(SessionResiduals,{...props,materialId:'__all__',analysis:{...context,diagnostics_after:{materials:[correctedMaterial,all]}}}));
  assert.equal((html.match(/effect-small/g)??[]).length,1);
  assert.match(html,/0.0590/);
  assert.doesNotMatch(html,/<select/);
});

test('pressure correction displays its own failed-analysis decision and corrected count',()=>{
  const failed={status:'applied',n:6,before:{sd:.04},after:{sd:.002},sd_reduction_fraction:.95,applied_n:3,unknown_applied_n:2,qc_pool:{status:'admitted',before:{sd:.04},after:{sd:.035},admitted_ids:['recovered'],candidate_ids:['recovered']},model:{slope:2,u_slope:.01}};
  const html=renderToStaticMarkup(React.createElement(SessionResiduals,{...props,analysis:{...context,failed_analysis_corrections:{d13c:failed,d18o:{status:'insufficient_evidence',n:2}}}}));
  assert.ok(html.includes(translate('Failed-analysis correction')));
  assert.ok(html.includes(`${translate('Corrected observations')}: 3`));
  assert.ok(html.includes(`${translate('Pressure-corrected unknowns')}: 2`));
  assert.ok(html.includes(`${translate('Recovered QC admitted')}: 1`));
  assert.ok(html.includes(translate('QC pool SD before / with corrected failures')));
  assert.match(html,/b = 2.00000/);
  assert.ok(html.includes(translate('Insufficient QC for fitting')));
  assert.equal(dictionary['Correct failed analyses'],'Corrige análises com falha');
  assert.equal((html.match(/station-gear/g)??[]).length,3);
});

test('pressure QC admission reports a rejected candidate pool',()=>{
  const failed={status:'applied',n:6,unknown_applied_n:1,qc_pool:{status:'not_improved',before:{sd:.04},after:{sd:.1},admitted_ids:[],candidate_ids:['failed']}};
  const html=renderToStaticMarkup(React.createElement(SessionResiduals,{...props,analysis:{...context,failed_analysis_corrections:{d13c:failed}}}));
  assert.ok(html.includes(translate('Not admitted: pooled QC SD did not improve')));
  assert.ok(html.includes(`${translate('Recovered QC admitted')}: 0`));
});

test('joint pressure correction identifies its training population and remaining intensity trend',()=>{
  const failed={status:'applied',n:40,training_population:'pressure_failed_qc',fit_excluded_ids:['bad-a','bad-b'],
    model:{slope:.02,u_slope:.001,intensity_slope:-.03,u_intensity_slope:.002,intensity_ref:6},
    intensity_before:{slope:-.05},intensity_after:{slope:0}};
  const html=renderToStaticMarkup(React.createElement(SessionResiduals,{...props,analysis:{...context,failed_analysis_corrections:{d13c:failed,d18o:{...failed,training_population:'nonfailed_qc_fallback',model:{slope:.1}}}}}));
  for(const label of ['Pressure-failed QC joint fit','Nonfailed QC fallback','Initial-intensity coefficient','Pressure-failed QC intensity slope before / after'])assert.ok(html.includes(translate(label)));
  assert.ok(html.includes(`${translate('QC excluded from pressure fit')}: 2`));
  assert.match(html,/-0.05000 \/ 0.00000/);
});

test('pressure fit reports minimum signal and rejected cross-validation',()=>{
  const failed={status:'not_improved',n:20,minimum_sample_intensity:3.55,
    validation:{before:{sd:.72},after:{sd:.77},passed:false}};
  const html=renderToStaticMarkup(React.createElement(SessionResiduals,{...props,analysis:{...context,failed_analysis_corrections:{d13c:failed}}}));
  for(const label of ['Minimum sample signal for pressure correction','Leave-one-out QC SD before / after','Not applied: cross-validation did not improve QC SD'])assert.ok(html.includes(translate(label)));
  assert.match(html,/0.7200 \/ 0.7700/);
});

test('gear parameters supply manual coefficient uncertainties and recalculate directly',()=>{
  const {ResidualControls}=load('components/metrology/residual-controls.tsx');
  const override={enabled:true,algorithm:'quadratic',slope:.01,quadratic:.001,offset:0,u_slope:.002,u_quadratic:.0001};
  const html=renderToStaticMarkup(React.createElement(ResidualControls,{effect:'intensity_dependence',materialId:'qc',overrides:{'qc:intensity_dependence:d13c':override},save:async()=>{}}));
  for(const label of ['Slope standard uncertainty','Quadratic coefficient standard uncertainty','Save and recalculate','Restore automatic fit'])assert.ok(html.includes(translate(label)));
  assert.doesNotMatch(html,/preview|prévia/i);
});

test('residual correction translations retain Portuguese accents and units',()=>{
  assert.equal(dictionary['Correction parameters'],'Parâmetros da correção');
  assert.equal(dictionary['QC SD / ‰'],'DP do CQ / ‰');
  for(const key of ['Not applied: QC SD did not improve','Residual linearity correction','Before residual correction','Recorded review findings'])assert.ok(!dictionary[key].includes('?'));
});

test('uncorrected acquisitions never appear in a corrected-results trace',()=>{
  const rows=[context.rows[0],{id:'b',isotopes:{d13c:{},d18o:{}}}];
  const html=renderToStaticMarkup(React.createElement(SessionResiduals,{...props,analysis:{...context,rows}}));
  const data=JSON.parse([...html.matchAll(/data-traces="([^"]*)"/g)][0][1].replaceAll('&quot;','"').replaceAll('&amp;','&'));
  assert.deepEqual(data.find(t=>t.meta?.correctionStage==='before').y,[1]);
  assert.deepEqual(data.find(t=>t.meta?.correctionStage==='after'&&t.meta.correctionApplied).y,[.6]);
  const unchanged=data.find(t=>t.meta?.correctionApplied===false);
  assert.deepEqual(unchanged.y,[.61]);
  assert.equal(unchanged.name,translate('Uncorrected observations'));
});
