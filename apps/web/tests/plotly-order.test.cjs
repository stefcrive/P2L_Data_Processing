const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs'),ts=require('typescript');
const source=fs.readFileSync(require('node:path').join(__dirname,'../src/lib/plotly-order.ts'),'utf8');
const compiled={exports:{}};
new Function('module','exports',ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2017}}).outputText)(compiled,compiled.exports);
const {orderTraceByX}=compiled.exports;
const {alignedIsotopeAxes}=compiled.exports;
test('secondary axis overlaps dataset means without clipping extremes or uncertainty',()=>{
 const traces=[{mode:'lines+markers',y:[1,2,9],customdata:[['a'],['b'],['c']]},{mode:'markers',yaxis:'y2',y:[-8,-6],customdata:[['s1'],['s2']],error_y:{array:[20,1]}}];
 const axes=alignedIsotopeAxes(traces);
 assert.deepEqual(axes.means,[4,-7]);assert.equal(axes.offset,-11);
 assert.ok(axes.secondary[0]<-28);assert.ok(axes.primary[1]>9);
 const pixel=(value,range)=>(value-range[0])/(range[1]-range[0]);
 assert.ok(Math.abs(pixel(4,axes.primary)-pixel(-7,axes.secondary))<1e-12);
 assert.deepEqual(alignedIsotopeAxes(traces,true).primary,[...axes.primary].reverse());
 assert.equal(alignedIsotopeAxes(traces.slice(0,1)),null);
});
test('mean alignment ignores duplicate and final overlays but keeps their full extents',()=>{
 const axes=alignedIsotopeAxes([{mode:'markers',y:[1,3],customdata:[['a'],['b']]},{mode:'markers',name:'Duplicate Samples',y:[99],customdata:[['a']]},{mode:'markers',y:[100],meta:{sessionUncertainty:true}},{mode:'markers',yaxis:'y2',y:[-1,-3]}]);
 assert.deepEqual(axes.means,[2,-2]);assert.ok(axes.primary[1]>100);
});
test('Identifier 2 lines sort points and their uncertainties together, preserving duplicates and outliers',()=>{
 const trace={mode:'lines+markers',x:[9,2,2,1],y:[900,20,21,10],customdata:[['d'],['b'],['c'],['a']],marker:{color:[4,2,3,1],symbol:['x','circle','diamond','circle']},error_y:{array:[.4,.2,.3,.1],visible:true}};
 const result=orderTraceByX(trace);
 assert.deepEqual(result.x,[1,2,2,9]);assert.deepEqual(result.y,[10,20,21,900]);assert.deepEqual(result.customdata,[['a'],['b'],['c'],['d']]);assert.deepEqual(result.error_y.array,[.1,.2,.3,.4]);assert.deepEqual(result.marker.color,[1,2,3,4]);assert.deepEqual(trace.x,[9,2,2,1]);assert.equal(result.connectgaps,false);
});
test('3D trajectories and marker-only clouds retain their original order',()=>{
 for(const trace of [{x:[2,1],y:[1,2],z:[4,3],mode:'lines'},{x:[2,1],mode:'markers'}])assert.equal(orderTraceByX(trace),trace);
});

test('binary delta and uncertainty arrays follow the sorted identifiers',()=>{
 const encode=values=>({dtype:'f8',bdata:Buffer.from(new Float64Array(values).buffer).toString('base64')});
 const result=orderTraceByX({mode:'lines',x:[3,1,2],y:encode([30,10,20]),error_y:{array:encode([.3,.1,.2])}});
 assert.deepEqual(result.y,[10,20,30]);assert.deepEqual(result.error_y.array,[.1,.2,.3]);
});

test('color-scale stops are settings, not point-aligned vectors',()=>{
 const stops=[[0,'blue'],[1,'red']];
 const result=orderTraceByX({mode:'lines',x:[2,1],y:[20,10],marker:{color:[2,1],colorscale:stops}});
 assert.equal(result.marker.colorscale,stops);assert.deepEqual(result.marker.color,[1,2]);
});
