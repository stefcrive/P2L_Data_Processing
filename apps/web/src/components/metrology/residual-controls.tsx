"use client";

import { useState } from "react";
import { useTranslation } from "@/components/layout/language-provider";
import { isotopeLabel, isotopes, type ResidualOverride, type Isotope } from "@/lib/metrology";
import { Field, Num } from "./shared";

export function ResidualControls({effect, label, materialId, overrides, busy, save}: {
  effect:string; label:string; materialId:string; overrides:Record<string,ResidualOverride>; busy:boolean;
  save:(materialId:string,effect:string,isotope:Isotope,settings:ResidualOverride|null)=>Promise<unknown>;
}) {
  const tr=useTranslation();
  return <div className="station-manual-controls"><h3>{tr("Manual linearity control")} · {tr(label)}</h3>
    <p className="metro-muted">{tr("Each effect is previewed independently from recorded final values. Blank coefficients use the original IRMS fit. Saved previews do not change released results or their uncertainty.")}</p>
    <div className="metro-grid station-isotope-pair">{isotopes.map(iso=><OverrideForm key={`${materialId}:${effect}:${iso}:${JSON.stringify(overrides[`${materialId}:${effect}:${iso}`])}`} iso={iso} initial={overrides[`${materialId}:${effect}:${iso}`]} busy={busy} save={settings=>save(materialId,effect,iso,settings)}/>)}</div>
  </div>;
}

function OverrideForm({iso, initial, busy, save}:{iso:Isotope;initial?:ResidualOverride;busy:boolean;save:(settings:ResidualOverride|null)=>Promise<unknown>}) {
  const tr=useTranslation();
  const [settings,setSettings]=useState<ResidualOverride>(initial??{enabled:true,algorithm:"linear",slope:null,quadratic:null,center:null,offset:0,practical_threshold:iso==="d13c"?.01:.02});
  const [saving,setSaving]=useState(false);
  const update=<K extends keyof ResidualOverride>(key:K,value:ResidualOverride[K])=>setSettings(p=>({...p,[key]:value}));
  const submit=async(value:ResidualOverride|null)=>{setSaving(true);try{await save(value);}finally{setSaving(false);}};
  return <form onSubmit={e=>{e.preventDefault();void submit(settings);}}><h4>{isotopeLabel[iso]}</h4>
    <label className="metro-check"><input type="checkbox" checked={settings.enabled} onChange={e=>update("enabled",e.target.checked)}/>{tr("Enable correction preview")}</label>
    <div className="metro-form-grid">
      <Field label={tr("Calculation algorithm")}><select value={settings.algorithm} onChange={e=>update("algorithm",e.target.value as ResidualOverride["algorithm"])}><option value="linear">{tr("Linear")}</option><option value="quadratic">{tr("Quadratic")}</option></select></Field>
      <Field label={tr("Slope override")}><Num value={settings.slope} onChange={v=>update("slope",v)}/></Field>
      {settings.algorithm==="quadratic"&&<Field label={tr("Quadratic coefficient override")}><Num value={settings.quadratic} onChange={v=>update("quadratic",v)}/></Field>}
      <Field label={tr("Reference predictor override")}><Num value={settings.center} onChange={v=>update("center",v)}/></Field>
      <Field label={tr("Offset / ‰")}><Num required value={settings.offset} onChange={v=>update("offset",v??0)}/></Field>
      <Field label={tr("Practical threshold / ‰")}><Num required min={.000001} value={settings.practical_threshold} onChange={v=>update("practical_threshold",v??.01)}/></Field>
    </div>
    <p className="station-model-equation">y′ = y − b(x − x₀){settings.algorithm==="quadratic"?" − c(x² − x₀²)":""} + Δ</p>
    <div className="metro-actions"><button className="metro-btn primary" disabled={busy||saving}>{tr(saving?"Saving...":"Save and calculate preview")}</button><button type="button" className="metro-btn" disabled={busy||saving||!initial} onClick={()=>void submit(null)}>{tr("Restore recorded calculation")}</button></div>
  </form>;
}
