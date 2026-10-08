const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs'),path=require('node:path'),ts=require('typescript');
function load(name){const compiled={exports:{}};new Function('module','exports','require',ts.transpileModule(fs.readFileSync(path.join(__dirname,`../src/lib/${name}.ts`),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(compiled,compiled.exports,key=>load(key.replace('./','')));return compiled.exports;}
const {buildUncertaintyFigure}=load('uncertainty-chart');
const customdata=[['a','d13C'],['b','d13C'],['c','d13C']];
const rows=Object.fromEntries(['a','b','c'].map((key,i)=>[key,{id:key,run_id:'r',role:'unknown',isotopes:{d13c:{value:i===1?99:i+1,budget:{expanded_uncertainty:.2}}}}]));

test('X selector repositions imported results and uncertainty overlays without detaching point data',()=>{
 const mapped=Object.fromEntries(Object.entries(rows).map(([key,row],i)=>[key,{...row,sequence:i+10,identifier2:['30','10','20'][i]}]));
 const source={data:[{name:'Raw d13C',mode:'lines+markers',x:[0,1,2],y:[1,99,3],customdata,
   text:['A','B','C'],error_y:{array:[.1,.2,.3]},marker:{color:[1,2,3],colorscale:'Viridis',cmin:1,cmax:3,showscale:true}}],layout:{xaxis:{title:'Old axis',range:[0,2]}}};
 const identifier=buildUncertaintyFigure(source,mapped,'Final',[],'By Identifier 2');
 assert.deepEqual(identifier.data[0].x,[10,20,30]);
 assert.deepEqual(identifier.data[0].y,[99,3,1]);
 assert.deepEqual(identifier.data[0].customdata,[customdata[1],customdata[2],customdata[0]]);
 assert.deepEqual(identifier.data[0].text,['B','C','A']);
 assert.deepEqual(identifier.data[0].error_y.array,[.2,.3,.1]);
 assert.equal(identifier.data[0].marker.color,'#cbd5e1');
 assert.equal(identifier.data[0].marker.showscale,false);
 assert.deepEqual(source.data[0].marker.color,[1,2,3]);
 assert.deepEqual(identifier.data.at(-1).marker.color,[2,3,1]);
 assert.equal(identifier.data.at(-1).marker.colorscale,'Viridis');
 assert.equal(identifier.data.at(-1).marker.cmin,1);
 assert.equal(identifier.data.at(-1).marker.cmax,3);
 assert.equal(identifier.data.at(-1).marker.showscale,true);
 assert.equal(identifier.data.at(-1).marker.symbol,'circle');
 assert.ok(identifier.data.at(-1).zorder>identifier.data[0].zorder);
 assert.deepEqual(identifier.data.at(-1).x,[10,20,30]);
 assert.deepEqual(identifier.data.at(-1).y,[99,3,1]);
 assert.equal(identifier.layout.xaxis.title,'Identifier 2');
 const envelope=identifier.data.find(trace=>trace.fill==='toself');
 assert.deepEqual(envelope.x,[10,20,30,30,20,10,10,null]);
 const sequence=buildUncertaintyFigure(source,mapped,'Final',[],'By Sequence');
 assert.deepEqual(sequence.data[0].x,[10,11,12]);
 assert.deepEqual(sequence.data.at(-1).x,[10,11,12]);
 assert.equal(sequence.layout.xaxis.title,'Sample Number');
 assert.deepEqual(source.data[0].x,[0,1,2]);
 assert.deepEqual(source.layout.xaxis.range,[0,2]);
});

test('identifier mode follows processing numeric parsing and falls back for missing identifiers',()=>{
 const mapped={...rows,a:{...rows.a,identifier2:'Depth 2,5'},b:{...rows.b,identifier2:''},c:{...rows.c,identifier2:'Depth 1.234,5'}};
 const source={data:[{name:'Raw d13C',mode:'lines',x:[0,7,2],y:[1,99,3],customdata}]};
 const result=buildUncertaintyFigure(source,mapped,'Final',[],'By Identifier 2');
 assert.deepEqual(result.data[0].x,[2.5,7,1234.5]);
});

test('processing outliers disappear from imported lines, legends, final results and U envelopes',()=>{
 const source={data:[
  {name:'Raw d13C',mode:'lines+markers',x:[1,2,3],y:[1,99,3],customdata},
  {name:'Calibrated d13C',mode:'lines',x:[1,2,3],y:[1,99,3],customdata},
  {name:'Statistical Outliers',mode:'markers',x:[2],y:[99],customdata:[customdata[1]]},
 ],layout:{yaxis:{range:[0,100]}}};
 const result=buildUncertaintyFigure(source,rows,'Final ± U',[]);
 assert.ok(!result.data.some(trace=>trace.name==='Statistical Outliers'));
 assert.deepEqual(result.data[0].y,[1,null,3]);
 assert.deepEqual(result.data[1].y,[1,null,3]);
 assert.deepEqual(result.data.at(-1).y,[1,null,3]);
 assert.ok(result.data.filter(trace=>trace.fill==='toself').every(trace=>trace.y.every(y=>y===null||y<4)));
 assert.ok(result.layout.yaxis.range[1]>3.2&&result.layout.yaxis.range[1]<4);
 assert.deepEqual(source.data[0].y,[1,99,3]);assert.equal(source.data.length,3);
});

test('saved exclusions remain isotope-specific and include final error bars in the viewport',()=>{
 const figure=iso=>({data:[{name:`Raw ${iso}`,mode:'markers',x:[1,2,3],y:[1,99,3],customdata:customdata.map(point=>[point[0],iso])}]});
 const flags=[{row:'b',isotope:'d13C',hidden:true}];
 const carbon=buildUncertaintyFigure(figure('d13C'),rows,'Final',flags);
 assert.deepEqual(carbon.data.at(-1).y,[1,null,3]);
 const oxygen=buildUncertaintyFigure(figure('d18O'),rows,'Final',flags);
 assert.deepEqual(oxygen.data[0].y,[1,99,3]);
});

test('range and failed overlays are hidden even when the processing figure forces them visible',()=>{
 const source={data:['Signal Intensity Range','Leak Rate Range','d13C Range','d18O Range','Manual Outliers','Poor Pressure Adjustment','Partially Failed (Recovered Mean)','Failed Samples (No Values)'].map(name=>({name,mode:'markers',x:[2],y:[99],customdata:[customdata[1]]})),layout:{annotations:[{text:'All measurements are outside the active processing ranges and are shown as range outliers.'},{text:'Workbook boundary'}]}};
 const result=buildUncertaintyFigure(source,rows,'Final',[]);
 assert.deepEqual(result.data,[]);
 assert.deepEqual(result.layout.annotations,[{text:'Workbook boundary'}]);
});
