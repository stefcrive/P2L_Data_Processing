"use client";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "@/components/layout/language-provider";
import { metroRequest, type Measurement, type RunDetail, type Method } from "@/lib/metrology";
import { useContext } from "react";
import { RawAnalysisInfoTable } from "@/components/diagnostics/raw-analysis-info-table";
import { CycleMolecularRatioChart } from "@/components/diagnostics/cycle-molecular-ratio-chart";
import { api } from "@/lib/api";
import { MetrologyEvidenceBridge } from "./consultation-context";
type Evidence={cycles:Record<string,number|null>[];raw_rows:{sheet_row:number;values:Record<string,unknown>}[];source_units:Record<string,string>;intensity_basis:string;source_sha256:string};
const numeric=(value:unknown)=>typeof value==="number"&&Number.isFinite(value)?value.toFixed(4):"—";
export type EvidenceSection = "all" | "summary" | "details";
export function AnalysisEvidence({row,run,method,section="all"}:{row:Measurement;run:RunDetail;method?:Method;section?:EvidenceSection}){
  const tr=useTranslation();
  const bridge=useContext(MetrologyEvidenceBridge);
  const rowLabel=bridge?.row_mapping[row.id];
  const diagnostics=useQuery({queryKey:["analysis-cycle-diagnostics",bridge?.session_id,rowLabel],queryFn:()=>api.getProcessingCycleDiagnostics(bridge!.session_id,{target:{row_label:rowLabel!,isotope_key:"d13C"}}),enabled:section!=="summary"&&!!bridge&&!!rowLabel,staleTime:Infinity});
  const evidence=useQuery({queryKey:["measurement-evidence",run.id,row.id],queryFn:({signal})=>metroRequest<Evidence>(`/runs/${run.id}/measurements/${row.id}/evidence`,{signal}),enabled:section!=="summary",staleTime:Infinity,gcTime:30*60*1000});
  const checks=[
    {key:"i44_v",label:"Sample I44 / V",value:row.i44_v},
    {key:"pressure_mismatch_v",label:"Pressure adjustment: result − target / V",value:row.pressure_mismatch_v},
    {key:"d13c_imported",label:`δ¹³C · ${tr("Imported")} / ‰`,value:row.d13c},
    {key:"d18o_imported",label:`δ¹⁸O · ${tr("Imported")} / ‰`,value:row.d18o},
    ...(["d13c","d18o"] as const).filter(iso=>row.isotopes?.[iso]?.value!=null).map(iso=>({key:iso,label:`${iso==="d13c"?"δ¹³C":"δ¹⁸O"} · ${tr("Final result")} / ‰ VPDB`,value:row.isotopes?.[iso]?.value})),
    ...(["d13c","d18o"] as const).map(iso=>({key:iso+"_sd",label:`${iso==="d13c"?"δ¹³C":"δ¹⁸O"} SD / ‰`,value:row[`${iso}_sd`]}))
  ];
  const cycles=evidence.data?.cycles??[];
  const hasAnalysisInfo=Object.keys(diagnostics.data?.analysis_info??{}).length>0;
  const conversion=row.isotopes?.d18o?.processing?.carbonate as {material:string;factor:number;preapplied:boolean;before:number;after:number}|undefined;
  return <div className={`station-analysis-evidence metro-stack evidence-${section}`}>
    {section!=="details"&&<>
    <div className="metro-form-grid"><div><small>{tr("Identifier 1")}</small><div>{row.identifier1||row.label}</div></div><div><small>{tr("Identifier 2")}</small><div>{row.identifier2||row.comment||"—"}</div></div><div><small>{tr("Species")}</small><div>{row.species||"—"}</div></div><div><small>{tr("Acquisition")}</small><div>{row.acquired_at?.replace("T"," ").slice(0,23)||"—"} · {tr(row.status||"Unknown")} · {tr(run.acquisition_complete?"Confirmed complete":"Not confirmed")}</div></div></div>
    <div className="metro-table-wrap"><table className="station-validity"><thead><tr><th>{tr("Parameter")}</th><th>{tr("Measured value")}</th><th>{tr("Valid range / criterion")}</th></tr></thead><tbody>{checks.map(check=>{
      const instrumental=check.key.endsWith("_imported")&&run.input_basis!=="already_vpdb",sd=check.key.endsWith("_sd"),bounds=instrumental?undefined:method?.config.ranges[check.key.replace("_imported","")],limit=sd?method?.config.qc.internal_sd[check.key.slice(0,-3) as "d13c"|"d18o"]:undefined;
      const valid=check.value!=null&&(sd?limit!=null&&check.value>=0&&check.value<limit:bounds!=null&&check.value>=bounds.low&&check.value<=bounds.high);
      return <tr key={check.key} className={instrumental||(!sd&&!bounds)?"":valid?"inside":"outside"}><td>{tr(check.label)}</td><td>{numeric(check.value)}</td><td>{instrumental?tr("Instrument scale; evaluate after normalization"):sd?`0 ≤ SD < ${numeric(limit)}`:bounds?`${numeric(bounds.low)} ≤ x ≤ ${numeric(bounds.high)}`:tr("Not set")}</td></tr>;
    })}<tr><td>{tr("Reference I44 / V")}</td><td>{numeric(row.reference_i44_v)}</td><td>—</td></tr><tr><td>{tr("Sample − reference I44 / V")}</td><td>{numeric(row.sample_reference_difference_v)}</td><td>—</td></tr></tbody></table></div>
    {conversion&&<p>δ¹⁸O · {tr(conversion.material)} · f={conversion.factor.toFixed(8)} · {numeric(conversion.before)} → {numeric(conversion.after)} ‰ · {tr(conversion.preapplied?"Correction retained from export":"Carbonate conversion")}</p>}
    </>}
    {section!=="summary"&&<>
    {evidence.isPending&&<p role="status">{tr("Loading original analysis data…")}</p>}{evidence.error&&<p role="alert">{evidence.error.message}</p>}
    <section className="station-cycle-evidence">
      {diagnostics.isFetching&&<p role="status">{tr("Loading original analysis data...")}</p>}
      {diagnostics.error&&<p role="alert">{diagnostics.error.message}</p>}
      {diagnostics.data&&section==="all"&&<CycleMolecularRatioChart rows={diagnostics.data.table}/>}
      {hasAnalysisInfo&&<details><summary>{tr("Original analysis data")}</summary><div style={{height:300}}><RawAnalysisInfoTable info={diagnostics.data?.analysis_info} layout="vertical" showHeading={false}/></div></details>}
      {!bridge&&cycles.length>0&&<div className="metro-table-wrap"><table><thead><tr><th>{tr("Cycle")}</th><th>{tr("Sample I44 / V")}</th><th>{tr("Reference I44 / V")}</th></tr></thead><tbody>{cycles.map((cycle,i)=><tr key={i}><td>{cycle.cycle}</td><td>{numeric(cycle.i44_v)}</td><td>{numeric(cycle.reference_i44_v)}</td></tr>)}</tbody></table></div>}
    </section>
    {evidence.data&&!hasAnalysisInfo&&<details><summary>{tr("Original analysis data")} · {evidence.data.raw_rows.length} {tr("rows")}</summary><p className="metro-muted">{evidence.data.intensity_basis}</p>{evidence.data.raw_rows.map(raw=><div className="metro-table-wrap" key={raw.sheet_row}><table><caption>{tr("Worksheet row")} {raw.sheet_row}</caption><tbody>{Object.entries(raw.values).filter(([,value])=>value!=null&&value!=="").map(([key,value])=><tr key={key}><th>{key}</th><td>{String(value)} {evidence.data?.source_units[key]||""}</td></tr>)}</tbody></table></div>)}</details>}
    </>}
  </div>;
}
