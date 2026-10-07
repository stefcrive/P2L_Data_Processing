const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs'),path=require('node:path'),ts=require('typescript');
function load(name){const compiled={exports:{}};new Function('module','exports','require',ts.transpileModule(fs.readFileSync(path.join(__dirname,`../src/lib/${name}.ts`),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(compiled,compiled.exports,key=>load(key.replace('./','')));return compiled.exports;}
const {rangeFlags,filterStationFigure,qcRangeRows,scopeColorRows,stationMarkerColors,viridisColor}=load('station-chart-filters');

test('isotope ranges stay independent; signal and leak limits apply to both',()=>{
 const rows=[{row_label:'a',signal:5,leak_rate:4,d13_raw:2,d18_raw:1},{row_label:'b',signal:9,leak_rate:12,d13_raw:0,d18_raw:0}];
 assert.deepEqual(rangeFlags(rows,{d13_raw:[-1,1]}).map(f=>[f.row,f.isotope]),[['a','d13C']]);
 for(const limits of [{signal:[0,6]},{leak_rate:[0,10]}])assert.deepEqual(rangeFlags(rows,limits).map(f=>[f.row,f.isotope]),[['b','d13C'],['b','d18O']]);
 assert.equal(rangeFlags(rows,{}).length,0);
});

test('standard curves always exclude flagged QC, even when outlier markers are enabled',()=>{
 const trace={name:'Standard measured d13C - QC',mode:'lines+markers',x:[1,2,3],y:[.1,100,.2],customdata:[['a','d13C'],['b','d13C'],['c','d13C']]};
 const source={data:[trace],layout:{xaxis:{range:[0,100]},yaxis:{range:[0,100]}}};
 const result=filterStationFigure(source,[{row:'b',isotope:'d13C',hidden:false}],true);
 assert.deepEqual(result.data[0].y,[.1,null,.2]);
 assert.equal(result.data[0].connectgaps,false);
 assert.ok(result.layout.yaxis.range[1]<1);
 assert.deepEqual(source.data[0].y,[.1,100,.2]);
});

test('informational review warnings do not remove standards unless explicitly hidden',()=>{
 const source={data:[{name:'Standard measured d13C - QC',mode:'lines+markers',x:[1,2],y:[.1,.2],customdata:[['a','d13C'],['b','d13C']]}]};
 const flag={row:'a',isotope:'d13C',category:'range',excludeFromFit:false,hidden:false};
 assert.deepEqual(filterStationFigure(source,[flag],true).data[0].y,[.1,.2]);
 assert.deepEqual(filterStationFigure(source,[{...flag,hidden:true}],true).data[0].y,[null,.2]);
});

test('hidden outliers release both axes from old bounds without clipping visible errors',()=>{
 const source={data:[{mode:'markers',x:[1,100,3],y:[2,500,4],customdata:[['a','d13C'],['b','d13C'],['c','d13C']],error_y:{array:[.5,10,2]}}],layout:{xaxis:{range:[0,110]},yaxis:{range:[0,600]}}};
 const result=filterStationFigure(source,[{row:'b',isotope:'d13C',hidden:true}]);
 assert.ok(result.layout.xaxis.range[1]<4);
 assert.ok(result.layout.yaxis.range[1]>6&&result.layout.yaxis.range[1]<7);
 assert.ok(result.layout.yaxis.range[0]<1.5);
 assert.notEqual(result.layout.meta.filteredViewport,filterStationFigure(source,[{row:'a',isotope:'d13C',hidden:true}]).layout.meta.filteredViewport);
});

test('QC range controls exclude anchors and samples by bridged identity',()=>{
 const rows=[{row_label:'qc-1',signal:4},{row_label:'qc-2',signal:8},{row_label:'anchor',signal:50},{row_label:'sample',signal:100}];
 const measurements=[{id:'a',role:'qc',material_id:'QC'},{id:'b',role:'qc',material_id:'QC'},{id:'c',role:'anchor',material_id:'anchor'},{id:'d',role:'unknown'}];
 const mapping={a:'qc-1',b:'qc-2',c:'anchor',d:'sample'};
 const selected=qcRangeRows(rows,measurements,mapping,'QC');
 assert.deepEqual(selected.map(r=>r.signal),[4,8]);
 assert.deepEqual(rangeFlags(selected,{signal:[0,6]}).map(f=>f.row),['qc-2','qc-2']);
 assert.deepEqual(qcRangeRows(rows,measurements,mapping,'missing'),[]);
});

test('QC color scale ignores other sample populations and honors a parameter change',()=>{
 const rows=[{row_label:'qc-a',intensity:5,difference:-.4},{row_label:'qc-b',intensity:7,difference:.2},{row_label:'sample',intensity:100,difference:90}];
 const scoped=scopeColorRows(rows,['qc-a','qc-b']);
 assert.deepEqual(scoped.map(r=>r.difference),[-.4,.2]);
 assert.equal(scopeColorRows(rows),rows);
 assert.deepEqual(scopeColorRows(rows,[]),[]);
 const trace={mode:'markers',customdata:[['qc-b','d13C'],['qc-a','d13C']],x:[7,5],y:[1,2],meta:{correctionStage:'after'},marker:{color:'#215ec5'}};
 const colors=key=>new Map(scoped.map(row=>[row.row_label,row[key]]));
 const intensity=stationMarkerColors(trace,colors('intensity'));
 const difference=stationMarkerColors(trace,colors('difference'));
 assert.deepEqual(intensity.marker.color,[7,5]);
 assert.deepEqual(difference.marker.color,[.2,-.4]);
 assert.equal(difference.marker.coloraxis,'coloraxis');
 const displayed=filterStationFigure({data:[difference] },[{row:'qc-a',isotope:'d13C',category:'range',hidden:false}]);
 assert.deepEqual(displayed.data[0].y,[1,2]);
 assert.deepEqual(displayed.data[0].marker.color,[.2,-.4]);
 assert.equal(trace.marker.color,'#215ec5');
});

test('rendered markers vary on the selected scale without depending on a shared Plotly axis',()=>{
 const trace={mode:'markers',meta:{correctionStage:'after'},customdata:[['a','d13C'],['b','d13C'],['c','d13C']],x:[1,2,3],y:[4,5,6],marker:{coloraxis:'coloraxis',cmin:100,cmax:200,color:'#215ec5'}};
 const actual=stationMarkerColors(trace,new Map([['a',1],['b',2],['c',3]]),[1,3]);
 assert.deepEqual(actual.marker.color,['#440154',viridisColor(2,[1,3]),'#fde725']);
 assert.equal(new Set(actual.marker.color).size,3);
 assert.equal(actual.marker.coloraxis,undefined);
 assert.equal(actual.marker.cmin,undefined);
 const changed=stationMarkerColors(trace,new Map([['a',3],['b',2],['c',1]]),[1,3]);
 assert.deepEqual(changed.marker.color,[...actual.marker.color].reverse());
 assert.deepEqual(actual.y,[4,5,6]);
 assert.equal(trace.marker.coloraxis,'coloraxis');
});
