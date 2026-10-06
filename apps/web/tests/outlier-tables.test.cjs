const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs'),path=require('node:path'),ts=require('typescript');
const compiled={exports:{}};
new Function('module','exports',ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/lib/outlier-tables.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2017}}).outputText)(compiled,compiled.exports);
const {uniqueOutlierTables}=compiled.exports;
test('overlapping criteria produce one row, retaining failed-sample recovery and all reasons',()=>{
 const a={__row_label:'a','Identifier 1':'same'},b={__row_label:'b','Identifier 1':'same'};
 const tables=[{name:'Statistical',rows:[a,b]},{name:'Leak Rate',rows:[a]},{name:'Failed Sample',rows:[a]}];
 const result=uniqueOutlierTables(tables);
 assert.equal(result.flatMap(t=>t.rows).length,2);
 assert.deepEqual(result[0].rows.map(r=>r.__row_label),['b']);
 assert.equal(result[2].rows[0]['Outlier criteria'],'Statistical; Leak Rate; Failed Sample');
 assert.equal(tables[0].rows.length,2);
});
