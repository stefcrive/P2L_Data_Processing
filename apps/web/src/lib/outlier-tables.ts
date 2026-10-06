type Table = {name:string; title?:string; rows:Record<string,unknown>[]};

/** One acquisition per table set; retain every failed criterion on its row. */
export function uniqueOutlierTables<T extends Table>(tables:T[]):T[] {
  const key = (row:Record<string,unknown>) => String(row.__row_label ?? JSON.stringify(row));
  const criteria = new Map<string,Set<string>>();
  for (const table of tables) for (const row of table.rows) {
    const id=key(row), names=criteria.get(id) ?? new Set<string>();
    names.add(table.name); criteria.set(id,names);
  }
  // Failed-sample recovery actions remain attached to their acquisition.
  const ordered=[...tables.filter(t=>t.name==="Failed Sample"),...tables.filter(t=>t.name!=="Failed Sample")];
  const seen=new Set<string>(), result=new Map<T,T>();
  for (const table of ordered) result.set(table,{...table,rows:table.rows.flatMap(row=>{
    const id=key(row); if(seen.has(id))return []; seen.add(id);
    return [{...row,"Outlier criteria":[...criteria.get(id)!].join("; ")}];
  })});
  return tables.map(t=>result.get(t)!);
}
