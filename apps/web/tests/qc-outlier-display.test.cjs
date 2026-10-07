const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs'),path=require('node:path'),ts=require('typescript');
function load(name){const compiled={exports:{}};new Function('module','exports','require',ts.transpileModule(fs.readFileSync(path.join(__dirname,`../src/lib/${name}.ts`),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText)(compiled,compiled.exports,key=>load(key.replace('./','')));return compiled.exports;}
const {qcOutlierDisplay,partiallySaturatedOverlay}=load('qc-outlier-display');
const encode=values=>({dtype:'f8',bdata:Buffer.from(new Float64Array(values).buffer).toString('base64')});
test('isotope filters break lines without deleting observations or uncertainties',()=>{
 const trace={mode:'lines+markers',x:[1,2,3],y:encode([.1,.9,.2]),customdata:[['a','d13C'],['b','d13C'],['c','d13C']],marker:{symbol:'circle-open',color:[1,2,3]},error_y:{array:[.01,.02,.03]},visible:'legendonly'};
 const flags=[{row:'b',isotope:'d13C',hidden:true},{row:'c',isotope:'d18O',hidden:true}];
 const result=qcOutlierDisplay(trace,flags);
 assert.deepEqual(result.y,[.1,null,.2]);assert.equal(result.connectgaps,false);
 assert.deepEqual(result.marker.symbol,['circle-open','square','circle-open']);assert.equal(result.error_y,trace.error_y);assert.equal(result.customdata,trace.customdata);assert.equal(result.visible,'legendonly');
 assert.deepEqual(qcOutlierDisplay(trace,flags.map(f=>({...f,hidden:false}))).y,[.1,.9,.2]);assert.equal(trace.y.dtype,'f8');
});
test('crossplots and 3D hide points matching either disabled isotope',()=>{
 const trace={mode:'markers',x:[1,2],y:[3,4],z:encode([5,6]),customdata:[['a','cross'],['b','cross']],marker:{symbol:['diamond','circle']}};
 const result=qcOutlierDisplay(trace,[{row:'a',isotope:'d18O',hidden:true}]);
 assert.deepEqual(result.y,[null,4]);assert.deepEqual(result.z,[null,6]);assert.deepEqual(result.x,[1,2]);
});
test('fit lines and unlinked diagnostics are preserved',()=>{
 for(const trace of [{mode:'lines',x:[1,2],y:[3,4]},{mode:'markers',x:[1],y:[2]}])assert.equal(qcOutlierDisplay(trace,[]),trace);
});
test('linked calibrated lines break at hidden outliers too',()=>{
 const trace={mode:'lines',x:[1,2,3],y:[1,99,2],customdata:[['a','d13C'],['b','d13C'],['c','d13C']]};
 const result=qcOutlierDisplay(trace,[{row:'b',isotope:'d13C',hidden:true}]);
 assert.deepEqual(result.y,[1,null,2]);assert.equal(result.connectgaps,false);
 assert.deepEqual(trace.y,[1,99,2]);
});
test('recorded categories have distinct symbols and overlapping hidden categories stay hidden',()=>{
 const trace={mode:'markers',x:[1,2,3],y:[4,5,6],customdata:[['a','d13C'],['b','d13C'],['c','d13C']]};
 const flags=[{row:'a',isotope:'d13C',category:'range'},{row:'b',isotope:'d13C',category:'manual'},{row:'c',isotope:'d13C',category:'failed'}];
 assert.deepEqual(qcOutlierDisplay(trace,flags).marker.symbol,['diamond','circle-open','triangle-down']);
 const overlapping=[...flags,{row:'a',isotope:'d13C',category:'statistical',hidden:true}];
 assert.deepEqual(qcOutlierDisplay(trace,overlapping).y,[null,5,6]);
});

test('paired stages use gray before symbols and selected after colors',()=>{
 const trace={mode:'markers',x:[1],y:[2],customdata:[['a','d13C']],marker:{color:[5],symbol:'circle'}};
 const flags=[{row:'a',isotope:'d13C',category:'statistical'}];
 const before=qcOutlierDisplay({...trace,meta:{correctionStage:'before'}},flags);
 const after=qcOutlierDisplay({...trace,meta:{correctionStage:'after'}},flags);
 assert.deepEqual(before.marker.symbol,['square-open']);assert.deepEqual(after.marker.symbol,['square']);
 assert.ok(before.marker.line.width>=1.5);
 assert.ok(before.marker.size>after.marker.size);assert.equal(before.marker.color,'#c4c4c4');assert.deepEqual(after.marker.color,[5]);
});

test('outlier shapes preserve colors selected by calibration controls',()=>{
 const trace={mode:'markers',x:[1,2,3],y:[4,5,6],customdata:[['a','d13C'],['b','d13C'],['c','d13C']],marker:{color:[10,20,30],coloraxis:'coloraxis',symbol:'circle'}};
 const result=qcOutlierDisplay(trace,[{row:'a',isotope:'d13C',category:'statistical'},{row:'b',isotope:'d13C',category:'range'}]);
 assert.deepEqual(result.marker.color,[10,20,30]);assert.equal(result.marker.coloraxis,'coloraxis');
 assert.deepEqual(result.marker.symbol,['square','diamond','circle']);
});

test('before and after retain QC categories with distinct stage colors',()=>{
 const trace={mode:'markers',x:[1,2,3],y:[4,5,6],customdata:[['a','d13C'],['b','d13C'],['c','d13C']],marker:{color:[10,20,30],symbol:'circle'}};
 const flags=[{row:'a',isotope:'d13C',category:'statistical'},{row:'b',isotope:'d13C',category:'range'}];
 const before=qcOutlierDisplay({...trace,meta:{correctionStage:'before'}},flags);
 const after=qcOutlierDisplay({...trace,meta:{correctionStage:'after'}},flags);
 assert.deepEqual(before.marker.symbol,['square-open','diamond-open','circle-open']);
 assert.deepEqual(after.marker.symbol,['square','diamond','circle']);
 assert.equal(before.marker.color,'#c4c4c4');assert.deepEqual(after.marker.color,[10,20,30]);
});
test('partial saturation outline preserves overlapping outliers and hidden-point gaps',()=>{
 const trace={mode:'markers',x:[1,2,3],y:[4,5,6],customdata:[['a','d13C'],['b','d13C'],['c','d13C']],meta:{correctionStage:'before'}};
 const styled=qcOutlierDisplay(trace,[{row:'a',isotope:'d13C',category:'statistical'},{row:'b',isotope:'d13C',hidden:true}]);
 const overlay=partiallySaturatedOverlay(styled,new Set(['a','b']));
 assert.deepEqual(styled.marker.symbol,['square-open','square-open','circle-open']);
 assert.deepEqual(overlay.y,[4,null,null]);
 assert.equal(overlay.marker.color,'#ff7f0e');assert.equal(overlay.marker.symbol,'diamond-open');
 assert.equal(overlay.legendgroup,'correction-before');assert.equal(overlay.showlegend,false);
 assert.equal(qcOutlierDisplay(overlay,[{row:'a',isotope:'d13C',category:'range'}]).marker.symbol,'diamond-open');
 assert.equal(partiallySaturatedOverlay(trace,new Set()),null);
});

test('before markers detach from the shared color axis without hiding QC points',()=>{
 const trace={mode:'markers',meta:{correctionStage:'before'},x:[1,2],y:[3,4],customdata:[['a','d13C'],['b','d13C']],marker:{color:[1,2],coloraxis:'coloraxis',colorscale:'Viridis'}};
 const result=qcOutlierDisplay(trace,[]);
 assert.equal(result.marker.color,'#c4c4c4');
 assert.equal(result.marker.coloraxis,undefined);
 assert.deepEqual(result.y,[3,4]);
 assert.equal(trace.marker.coloraxis,'coloraxis');
});
