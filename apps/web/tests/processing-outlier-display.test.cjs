const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs'), path = require('node:path'), ts = require('typescript');
function load(name) {
  const mod = {exports:{}};
  const source = fs.readFileSync(path.join(__dirname, `../src/lib/${name}.ts`), 'utf8');
  new Function('module','exports','require', ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(mod,mod.exports,key=>load(key.replace('./','')));
  return mod.exports;
}
const {qcOutlierDisplay, processingOutlierHighlights} = load('qc-outlier-display');
const {alignedIsotopeAxes} = load('plotly-order');

test('visible flags get filled colored symbols and one legend entry per category, preserving observations', () => {
  const trace = {mode:'lines+markers',x:[1,2,3,4],y:[10,20,30,40],customdata:[['a','d13C'],['b','d13C'],['c','d13C'],['d','d13C']],marker:{color:[1,2,3,4]}};
  const flags = [{row:'a',isotope:'d13C',category:'statistical'},{row:'b',isotope:'d13C',category:'manual'},{row:'c',isotope:'d13C',category:'range',hidden:true}];
  const flagged = qcOutlierDisplay(trace, flags);
  const result = processingOutlierHighlights({data:[flagged,{...flagged,meta:{...flagged.meta,sessionUncertainty:true}}]});
  const overlays = result.data.slice(2);
  assert.equal(overlays.length,4);
  assert.equal(overlays.filter(t=>t.showlegend).length,2);
  assert.deepEqual(overlays[0].x,[1]);
  assert.deepEqual(overlays[0].customdata,[['a','d13C']]);
  assert.equal(overlays[0].marker.symbol,'square');
  assert.equal(overlays[0].marker.color,'#dc2626');
  assert.ok(overlays[0].marker.size>8);
  assert.equal(overlays[0].zorder,5);
  assert.equal(overlays[1].name,'Manual exclusions');
  assert.deepEqual(trace.y,[10,20,30,40]);
  assert.deepEqual(flagged.marker.color,[1,2,3,4]);
  assert.ok(!overlays.some(t=>t.name==='Validity-range flags'));
});

test('standard visibility preserves both axis ranges and gives QC a separate symbol', () => {
  const page = fs.readFileSync(path.join(__dirname,'../src/app/(dashboard)/processing/page.tsx'),'utf8');
  const start = page.indexOf('function applyDisplayState('), end = page.indexOf('const normalizeProcessingMarkerOpacityCache', start);
  const code = ts.transpileModule(page.slice(start,end),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
  const apply = new Function('normalizeDisplayState','cloneFigure','alignedIsotopeAxes','STANDARD_MEASURED_TRACE_PREFIX',`${code};return applyDisplayState;`)(s=>s,structuredClone,alignedIsotopeAxes,'Standard measured ');
  const source = {data:[{name:'Raw d13C',mode:'markers',x:[1,2],y:[-2,-1]},{name:'Standard measured d13C - QC',mode:'lines+markers',yaxis:'y2',x:[1,2],y:[-40,-39]}],layout:{yaxis:{title:'d13C'},yaxis2:{title:'Standard d13C'}}};
  const off = apply(source,{overlayStandards:false}), on = apply(source,{overlayStandards:true});
  assert.equal(off.data.length,1);
  assert.equal(on.data.length,2);
  assert.deepEqual(off.layout.meta.standardAxisAlignment,on.layout.meta.standardAxisAlignment);
  assert.equal(off.layout.yaxis2.visible,false);
  assert.equal(on.layout.yaxis2.visible,true);
  assert.equal(on.data[1].marker.symbol,'diamond-open');
  assert.equal(source.data[1].marker,undefined);
});
const {processingChartFlags,processingTraceVisibility} = load('processing-chart-display');
const {withSessionUncertainty} = load('metrology-envelopes');
const {filterStationFigure} = load('station-chart-filters');
const toggles = ['show_statistical_outliers','show_range_outliers','show_manual_outliers','show_saturated_collectors','show_saturated_samples','show_failed_samples'];
const config = enabled => ({pressure_adjustment_as_outlier:false,overlays:Object.fromEntries(toggles.map(key=>[key,enabled]))});

test('corrected pressure warnings are ordinary observations and unrelated outliers remain flagged', () => {
  const corrected={id:'a',role:'unknown',failure_categories:{d13c:'pressure_adjustment'},isotopes:{d13c:{value:3,residual_correction:{effect:'pressure_dependence',adjustment:.2,u:.1},budget:{expanded_uncertainty:.5}}}};
  const flags=[{row:'a',isotope:'d13C',category:'pressure_adjustment'},{row:'a',isotope:'d13C',category:'statistical'}];
  const rows={a:corrected};
  for (const pressure_adjustment_as_outlier of [false,true]) {
    const selected={...config(true),pressure_adjustment_as_outlier};
    const active=processingChartFlags(flags,rows,selected);
    assert.deepEqual(active.map(f=>f.category),['statistical']);
  }
  assert.equal(processingChartFlags([{row:'a',isotope:'d13C',category:'failed'}],rows,config(true)).length,0);
  assert.equal(processingChartFlags([flags[0]],{},config(true)).length,0);
  assert.equal(processingChartFlags([flags[0]],{}, {...config(true),pressure_adjustment_as_outlier:true}).length,1);
  const source={data:[{name:'Failed Samples (Interpolated)',mode:'markers',x:[1],y:[2],customdata:[['a','d13C']],marker:{symbol:'triangle-down',color:'gray'}}]};
  const canonical=withSessionUncertainty(source,rows,'Final',processingChartFlags([flags[0]],rows,config(true)));
  const displayed=processingTraceVisibility(canonical,rows,config(true));
  assert.deepEqual(displayed.data[0].y,[null]);
  const final=displayed.data.find(t=>t.meta?.sessionUncertainty);
  assert.deepEqual(final.y,[3]);
  assert.equal(final.marker.symbol,'circle');
  assert.notEqual(final.marker.color,'#cbd5e1');
});

test('every Mostrar no gráfico checkbox controls both saved traces and live flags', () => {
  const categories=['statistical','range','manual','partial','full','no_signal'];
  const names=['Statistical Outliers','Signal Intensity Range','Manual Outliers','Partially Failed (Recovered Mean)','Failed Samples (Fully Saturated)','Failed Samples (No Values)'];
  const flags=categories.map((category,i)=>({row:String(i),isotope:'d13C',category,hidden:true}));
  const source={data:names.map((name,i)=>({name,mode:'markers',x:[i],y:[i+1],customdata:[[String(i),'d13C']],marker:{color:'#cbd5e1',symbol:'square-open',size:10}}))};
  for(let i=0;i<toggles.length;i++) {
    const selected=config(false); selected.overlays[toggles[i]]=true;
    const active=processingChartFlags(flags,{},selected);
    assert.deepEqual(active.map(f=>!f.hidden),categories.map((_,n)=>n===i));
    const displayed=processingTraceVisibility(filterStationFigure(source,active),{},selected);
    assert.equal(displayed.data.length,1);
    assert.equal(displayed.data[0].name,names[i]);
    assert.equal(displayed.data[0].marker.opacity,1);
    assert.ok([displayed.data[0].marker.symbol].flat().every(symbol=>!symbol.endsWith('-open')));
    assert.notEqual(displayed.data[0].marker.color,'#cbd5e1');
  }
  assert.equal(processingTraceVisibility(source,{},config(false)).data.length,0);
});

test('different range symbols keep their own filled markers and legends',()=>{
  const trace={mode:'markers',x:[1,2,3],y:[1,2,3],customdata:[['a','d13C'],['b','d13C'],['c','d13C']],marker:{color:'#cbd5e1'}};
  const flags=[{row:'a',isotope:'d13C',category:'range',reasons:['Leak Rate Range']},{row:'b',isotope:'d13C',category:'range',reasons:['Signal Intensity Range']},{row:'c',isotope:'d13C',category:'range',reasons:['d13C Range']}];
  const displayed=processingOutlierHighlights(filterStationFigure({data:[trace]},flags));
  const overlays=displayed.data.slice(1);
  assert.deepEqual(overlays.map(t=>t.marker.symbol),['star','diamond','cross']);
  assert.equal(overlays.filter(t=>t.showlegend).length,3);
  assert.ok(overlays.every(t=>t.marker.color==='#dc2626'&&t.marker.opacity===1));
});
const {restoreProcessingOutlierRows} = load('processing-chart-display');

test('checking an initially disabled category restores points omitted from the saved chart, repeatedly',()=>{
 const source={data:[{name:'Raw d13C - BTS',mode:'lines+markers',x:[1],y:[-2],customdata:[['ok','d13C','BTS','1','BTS']]}],layout:{}};
 const rows=[{row_label:'ok',identifier1:'BTS',identifier2:'1',species:'BTS',d13_raw:-2,d18_raw:-4,intensities:{},attributes:{}},
  {row_label:'missing',identifier1:'BTS',identifier2:'2',species:'BTS',d13_raw:9,d18_raw:8,intensities:{},attributes:{}}];
 const chartConfig={...config(false),selected_identifier:'All',identifier1_name_map:{},species_name_map:{},x_axis_option:'By Identifier 2',z_axis:'d 13C/12C  Mean'};
 for(const category of ['statistical','range','manual']) {
  const toggle={statistical:'show_statistical_outliers',range:'show_range_outliers',manual:'show_manual_outliers'}[category];
  for(const enabled of [false,true,false,true]) {
   const selected={...chartConfig,overlays:{...chartConfig.overlays,[toggle]:enabled}};
   const flags=processingChartFlags([{row:'missing',isotope:'d13C',category}],{},selected);
   const restored=restoreProcessingOutlierRows(source,rows,flags,selected,'BTS|BTS|d13C');
   const rendered=processingOutlierHighlights(processingTraceVisibility(filterStationFigure(restored,flags),{},selected));
   const highlights=rendered.data.filter(t=>t.meta?.qcHighlight);
   assert.equal(highlights.length,enabled?1:0,`${category}, enabled=${enabled}`);
   if(enabled) {
    assert.deepEqual(highlights[0].x,[2]);
    assert.deepEqual(highlights[0].y,[9]);
    assert.equal(highlights[0].showlegend,true);
    assert.equal(highlights[0].customdata[0][0],'missing');
    const repeated=restoreProcessingOutlierRows(restored,rows,flags,selected,'BTS|BTS|d13C');
    assert.equal(repeated.data.length,restored.data.length);
   }
  }
 }
 assert.equal(source.data.length,1);
});

test('restored points respect identifier, species, isotope, corrected values and selected scope',()=>{
 const rows=[{row_label:'carbon',identifier1:'BTS',identifier2:'10,5',species:'raw',d13_raw:9,d18_raw:8,intensities:{},attributes:{}},
 {row_label:'oxygen',identifier1:'BTS',identifier2:'12',species:'raw',d13_raw:1,d18_raw:2,intensities:{},attributes:{}},
 {row_label:'other',identifier1:'Elsewhere',identifier2:'13',species:'raw',d13_raw:3,d18_raw:4,intensities:{},attributes:{}}];
 const selected={...config(true),selected_identifier:'All',identifier1_name_map:{},species_name_map:{raw:'Calcite'},x_axis_option:'By Identifier 2',z_axis:'d 13C/12C  Mean'};
 const flags=[{row:'carbon',isotope:'d13C',category:'statistical'},{row:'oxygen',isotope:'d18O',category:'statistical'},{row:'other',isotope:'d13C',category:'statistical'}];
 const values=new Map([['carbon',{d13:7,d18:6}]]);
 const figure={data:[],layout:{}};
 const result=restoreProcessingOutlierRows(figure,rows,flags,selected,'Calcite|BTS|d13C',values);
 assert.deepEqual(result.data[0].x,[10.5]);assert.deepEqual(result.data[0].y,[7]);
 assert.deepEqual(result.data[0].customdata.map(point=>point[0]),['carbon']);
 const cross=restoreProcessingOutlierRows(figure,rows,flags,{...selected,selected_identifier:'BTS'},'processing_3d',values);
 assert.deepEqual(cross.data[0].x,[6,2]);assert.deepEqual(cross.data[0].y,[7,1]);assert.deepEqual(cross.data[0].z,[7,1]);
 assert.equal(restoreProcessingOutlierRows(figure,rows,flags,{...selected,selected_identifier:'Elsewhere'},'Calcite|BTS|d13C').data.length,0);
});
const {finalProcessingFigure} = load('processing-chart-display');
test('processing shows canonical final results and flagged final values without original traces',()=>{
 const rows={a:{id:'a',role:'unknown',isotopes:{d13c:{value:10,budget:{expanded_uncertainty:.1}}}},b:{id:'b',role:'qc',isotopes:{d13c:{value:20,budget:{expanded_uncertainty:.2}}}}};
 const source={data:[{name:'Raw d13C',mode:'markers',x:[1,2],y:[100,200],customdata:[['a','d13C'],['b','d13C']],marker:{color:[1,2]}}],layout:{}};
 const flags=[{row:'b',isotope:'d13C',category:'statistical',hidden:false}];
 const final=finalProcessingFigure(filterStationFigure(withSessionUncertainty(source,rows,'Final',flags),flags),rows);
 assert.ok(final.data.every(t=>!String(t.name).startsWith('Raw')));
 const observations=final.data.filter(t=>String(t.mode).includes('markers'));
 assert.deepEqual(observations.flatMap(t=>t.y).filter(v=>v!=null),[10,20]);
 assert.deepEqual(observations.flatMap(t=>t.customdata).map(p=>p[0]),['a','b','b']);
 assert.equal(source.data[0].y[0],100);
});

test('duplicates use filled brown diamonds at final values and preserve point identity',()=>{
 const page=fs.readFileSync(path.join(__dirname,'../src/app/(dashboard)/processing/page.tsx'),'utf8');
 const start=page.indexOf('function applyDuplicateHighlightsToFigure('),end=page.indexOf('function sortedFinite',start);
 const code=ts.transpileModule(page.slice(start,end),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
 const duplicate=new Function('cloneFigure','coerceVector','customDataRowLabel','customDataIsotope',`${code};return applyDuplicateHighlightsToFigure;`)(structuredClone,load('plotly-order').plotlyVector,p=>String(p[0]),p=>p[1]);
 const source={data:[{name:'Final',mode:'markers',x:[1,2],y:[10,20],customdata:[['a','d13C'],['b','d13C']]}],layout:{}};
 const result=duplicate(source,new Set(['b']));
 const marker=result.data.at(-1);
 assert.deepEqual(marker.x,[2]);assert.deepEqual(marker.y,[20]);assert.deepEqual(marker.customdata,[['b','d13C']]);
 assert.equal(marker.marker.symbol,'diamond');assert.equal(marker.marker.color,'#92400e');assert.equal(marker.marker.opacity,1);
 assert.equal(marker.showlegend,true);assert.equal(source.data.length,1);
 assert.equal(duplicate(source,new Set()),source);
});

test('sample range flags and missing-metadata warnings do not classify QC or anchor standards as outliers',()=>{
 const rows={qc:{id:'qc',role:'qc'},anchor:{id:'anchor',role:'anchor'},sample:{id:'sample',role:'unknown'}};
 const flags=[{row:'qc',isotope:'d18O',category:'range',source:'processing'},
  {row:'anchor',isotope:'d18O',category:'range',source:'processing'},
  {row:'qc',isotope:'d18O',category:'range',metadataOnly:true,excludeFromFit:false},
  {row:'sample',isotope:'d18O',category:'range',source:'processing'},
  {row:'qc',isotope:'d18O',category:'statistical'},
  {row:'anchor',isotope:'d18O',category:'manual'},
  {row:'qc',isotope:'d18O',category:'range',reasons:['Explicit QC signal limit']}];
 const result=processingChartFlags(flags,rows,config(true));
 assert.deepEqual(result.map(f=>[f.row,f.category]),[['sample','range'],['qc','statistical'],['anchor','manual'],['qc','range']]);
 assert.ok(result.every(f=>!f.hidden));
});

test('overlaid standards keep their own symbols without red sample-range overlays',()=>{
 const standard={name:'Standard measured final',yaxis:'y2',mode:'markers',x:[1],y:[-3.4],customdata:[['qc','d18O']],marker:{symbol:'diamond-open',color:'#0369a1'}};
 const flags=[{row:'qc',isotope:'d18O',category:'range',hidden:false}];
 const result=processingOutlierHighlights(filterStationFigure({data:[standard]},flags));
 assert.equal(result.data.length,1);
 assert.equal(result.data[0].marker.color,'#0369a1');
 assert.ok(!result.data.some(t=>t.meta?.qcHighlight));
});

test('standards and sample range outliers remain independent across all display-toggle combinations',()=>{
 const page=fs.readFileSync(path.join(__dirname,'../src/app/(dashboard)/processing/page.tsx'),'utf8');
 const start=page.indexOf('function applyDisplayState('),end=page.indexOf('const normalizeProcessingMarkerOpacityCache',start);
 const code=ts.transpileModule(page.slice(start,end),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
 const display=new Function('normalizeDisplayState','cloneFigure','alignedIsotopeAxes','STANDARD_MEASURED_TRACE_PREFIX',`${code};return applyDisplayState;`)(s=>s,structuredClone,alignedIsotopeAxes,'Standard measured ');
 const rows={sample:{id:'sample',role:'unknown',isotopes:{d18o:{value:-5,budget:{expanded_uncertainty:.1}}}},qc:{id:'qc',role:'qc',isotopes:{d18o:{value:-3.4,budget:{expanded_uncertainty:.2}}}}};
 const source={data:[{name:'Raw d18O',mode:'markers',x:[1,2],y:[-50,-34],customdata:[['sample','d18O'],['qc','d18O']]},
 {name:'Standard measured d18O - QC',mode:'lines+markers',yaxis:'y2',x:[2],y:[-34],customdata:[['qc','d18O']]}],layout:{yaxis:{title:'d18O'},yaxis2:{title:'Standard d18O'}}};
 for(const range of [false,true])for(const standards of [false,true]) {
  const selected={...config(true),overlays:{...config(true).overlays,show_range_outliers:range}};
  const flags=processingChartFlags([{row:'sample',isotope:'d18O',category:'range',source:'processing'},
   {row:'qc',isotope:'d18O',category:'range',excludeFromFit:false,reasons:['Outside method validation interval']}],rows,selected);
  const canonical=withSessionUncertainty(source,rows,'Final',flags,{standardVisibilityIndependent:true});
  const final=finalProcessingFigure(filterStationFigure(canonical,flags,true,true),rows);
  const result=display(processingOutlierHighlights(processingTraceVisibility(final,rows,selected)),{overlayStandards:standards});
  const std=result.data.filter(t=>String(t.name).startsWith('Standard measured '));
  assert.equal(std.length,standards?1:0,`range=${range}; standards=${standards}`);
  if(standards){assert.deepEqual(std[0].y,[-3.4]);assert.equal(std[0].marker.symbol,'diamond-open');assert.equal(std[0].marker.color,'#0369a1');}
  const outliers=result.data.filter(t=>t.meta?.qcHighlight);
  assert.equal(outliers.length,range?1:0);
  if(range){assert.deepEqual(outliers[0].customdata.map(p=>p[0]),['sample']);assert.equal(outliers[0].marker.color,'#dc2626');}
  assert.equal(result.data.some(t=>t.meta?.standardOverlay),standards);
 }
});

test('standards on a separate axis survive deduplication and display final values without inventing a budget',()=>{
 const rows={qc:{id:'qc',role:'qc',isotopes:{d13c:{value:2}}}};
 const source={data:[{mode:'markers',x:[1],y:[10],customdata:[['qc','d13C']]},{name:'Standard measured d13C - QC',mode:'markers',yaxis:'y2',x:[1],y:[10],customdata:[['qc','d13C']]}]};
 const final=finalProcessingFigure(withSessionUncertainty(source,rows,'Final',[],{standardVisibilityIndependent:true}),rows);
 const standard=final.data.find(t=>t.meta?.standardOverlay&&t.meta?.sessionUncertainty);
 assert.ok(standard);assert.deepEqual(standard.y,[2]);assert.deepEqual(standard.error_y.array,[null]);
 assert.equal(standard.name,'Standard measured final');
});
