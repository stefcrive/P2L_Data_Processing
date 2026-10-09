const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs'),path=require('node:path'),ts=require('typescript');
const compiled={exports:{}};
const source=fs.readFileSync(path.join(__dirname,'../src/lib/plotly-standard-viewport.ts'),'utf8');
new Function('module','exports',ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(compiled,compiled.exports);
const {standardAxisRelayout}=compiled.exports;
const layout=()=>({meta:{equalStandardScale:true,standardMeanOffset:10},yaxis:{range:[0,10],dtick:1},yaxis2:{range:[10,20],dtick:1,tickmode:'linear',overlaying:'y',visible:true}});
function apply(state,update){for(const [key,value]of Object.entries(update)){const [axis,property]=key.split('.');if(axis in state)state[axis][property]=value;}return state;}

test('zoom on the standards axis moves the sample axis without snapping back',()=>{
 const state=layout();state.yaxis2.range=[12,14];
 const update=standardAxisRelayout(state,{'yaxis2.range[0]':12,'yaxis2.range[1]':14});
 assert.deepEqual(update['yaxis.range'],[2,4]);
 assert.equal(update['yaxis2.range'],undefined);
 apply(state,update);
 assert.deepEqual(standardAxisRelayout(state,update),{});
 assert.deepEqual(state.yaxis2.range,[12,14]);
});

test('sample-axis zoom, reversed oxygen axes, reset and pan maintain the standard offset',()=>{
 for(const primary of [[2,4],[4,2],[-2,8],[0,10]]){
  const state=layout();state.yaxis.range=primary;
  const update=standardAxisRelayout(state,{'yaxis.range':primary});
  const target=primary.map(v=>v+10);
  assert.deepEqual(update['yaxis2.range']??state.yaxis2.range,target);
  apply(state,update);
  assert.deepEqual(standardAxisRelayout(state,update),{});
 }
 const state=layout();state.yaxis.range=[-5,15];
 assert.deepEqual(standardAxisRelayout(state,{'yaxis.autorange':true})['yaxis2.range'],[5,25]);
});

test('hidden standards and horizontal-only zoom do not change vertical ranges',()=>{
 const hidden=layout();hidden.yaxis2.visible=false;hidden.yaxis2.range=[12,14];
 assert.deepEqual(standardAxisRelayout(hidden,{'yaxis2.range':[12,14]}),{});
 assert.deepEqual(standardAxisRelayout(layout(),{'xaxis.range':[1,2]}),{});
});

test('filtered charts save both axes under their actual revision and restore zoom after redraw',()=>{
 const chart=fs.readFileSync(path.join(__dirname,'../src/components/charts/plotly-chart.tsx'),'utf8');
 const helpers=chart.slice(chart.indexOf('function isViewportRelayoutKey('),chart.indexOf('export function PlotlyChart('));
 const js=ts.transpileModule(helpers,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
 const {mergeViewportRelayout,applyPersistedViewport}=new Function(`${js};return {mergeViewportRelayout,applyPersistedViewport};`)();
 const start=chart.indexOf('  function persistViewportUpdate('),end=chart.indexOf('  function registerPointerInteraction',start);
 const code=ts.transpileModule(chart.slice(start,end),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
 const saved=new Map(),state=layout();state.yaxis2.range=[12,14];
 const prepared={layout:{uirevision:'processing:BTS:d13C:filtered:population'}};
 const persist=new Function('preparedFigure','uiRevision','standardAxisRelayout','graphDivRef','persistedViewports','mergeViewportRelayout','cloneRecord','syncStandardAxisScale',`${code};return persistViewportUpdate;`)(prepared,'processing:BTS:d13C',standardAxisRelayout,{current:{_fullLayout:state}},saved,mergeViewportRelayout,structuredClone,()=>{});
 persist({'yaxis2.range[0]':12,'yaxis2.range[1]':14,'xaxis.range':[20,30]});
 assert.equal(saved.has('processing:BTS:d13C'),false);
 const redraw=layout();applyPersistedViewport(redraw,saved.get(prepared.layout.uirevision));
 assert.deepEqual(redraw.yaxis.range,[2,4]);assert.deepEqual(redraw.yaxis2.range,[12,14]);assert.deepEqual(redraw.xaxis.range,[20,30]);
 assert.deepEqual(standardAxisRelayout(redraw),{});
 // Alternate axes and payload formats, as Plotly does after programmatic synchronization.
 const first=saved.get(prepared.layout.uirevision);
 const second=mergeViewportRelayout(first,{'yaxis2.range':[14,16],'yaxis.range':[4,6]});
 const third=mergeViewportRelayout(second,{'yaxis2.range[0]':15,'yaxis2.range[1]':17,'yaxis.range':[5,7]});
 const after=layout();applyPersistedViewport(after,third);
 assert.deepEqual(after.yaxis2.range,[15,17]);assert.deepEqual(after.yaxis.range,[5,7]);
});
