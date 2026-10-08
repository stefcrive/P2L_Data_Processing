"use client";

import { useState } from "react";
import { useTranslation } from "@/components/layout/language-provider";
import { isotopeLabel, isotopes, type ResidualOverride, type Isotope } from "@/lib/metrology";
import { Field, Num } from "./shared";

export function ResidualControls({effect, materialId, overrides, busy, save}: {
  effect:string; materialId:string; overrides:Record<string,ResidualOverride>; busy:boolean;
  save:(materialId:string,effect:string,isotope:Isotope,settings:ResidualOverride|null)=>Promise<unknown>;
}) {
  return <div className="metro-grid station-isotope-pair station-residual-settings">{isotopes.map(iso=>
    <CorrectionForm key={`${materialId}:${effect}:${iso}:${JSON.stringify(overrides[`${materialId}:${effect}:${iso}`])}`} iso={iso}
      initial={overrides[`${materialId}:${effect}:${iso}`]} busy={busy} save={settings=>save(materialId,effect,iso,settings)}/>
  )}</div>;
}

function CorrectionForm({iso,initial,busy,save}:{iso:Isotope;initial?:ResidualOverride;busy:boolean;save:(settings:ResidualOverride|null)=>Promise<unknown>}) {
  const tr=useTranslation();
  const [settings,setSettings]=useState<ResidualOverride>(initial??{enabled:true,algorithm:"linear",slope:null,quadratic:null,center:null,offset:0,practical_threshold:iso==="d13c"?.01:.02});
  const [saving,setSaving]=useState(false);
  const update=<K extends keyof ResidualOverride>(key:K,value:ResidualOverride[K])=>setSettings(p=>({...p,[key]:value}));
  const submit=async(value:ResidualOverride|null)=>{setSaving(true);try{await save(value);}finally{setSaving(false);}};
  const manual=settings.slope!=null||(settings.algorithm==="quadratic"&&settings.quadratic!=null);
  return <form aria-label={`${isotopeLabel[iso]} ${tr("Correction parameters")}`} onSubmit={e=>{e.preventDefault();void submit(settings);}}>
    <h4>{isotopeLabel[iso]}</h4>
    <label className="metro-check"><input type="checkbox" checked={settings.enabled} onChange={e=>update("enabled",e.target.checked)}/>{tr("Evaluate this correction")}</label>
    <div className="metro-form-grid">
      <Field label={tr("Calculation algorithm")}><select value={settings.algorithm} onChange={e=>update("algorithm",e.target.value as ResidualOverride["algorithm"])}><option value="linear">{tr("Linear")}</option><option value="quadratic">{tr("Quadratic")}</option></select></Field>
      <Field label={tr("Slope override")}><Num value={settings.slope} onChange={v=>update("slope",v)}/></Field>
      {settings.algorithm==="quadratic"&&<Field label={tr("Quadratic coefficient override")}><Num value={settings.quadratic} onChange={v=>update("quadratic",v)}/></Field>}
      <Field label={tr("Reference predictor override")}><Num value={settings.center} onChange={v=>update("center",v)}/></Field>
      <Field label={tr("Offset / ‰")}><Num required value={settings.offset} onChange={v=>update("offset",v??0)}/></Field>
      {manual&&<Field label={tr("Slope standard uncertainty")}><Num required min={0} value={settings.u_slope} onChange={v=>update("u_slope",v)}/></Field>}
      {manual&&settings.algorithm==="quadratic"&&<Field label={tr("Quadratic coefficient standard uncertainty")}><Num required min={0} value={settings.u_quadratic} onChange={v=>update("u_quadratic",v)}/></Field>}
    </div>
    <p className="metro-muted">{tr("Blank coefficients are fitted from QC. Changes are applied only if they reduce QC SD.")}</p>
    {manual&&<p className="metro-muted">{tr("Enter coefficient uncertainties for the uncertainty budget.")}</p>}
    <label className="metro-check"><input type="checkbox" checked={settings.include_statistical_outliers??false} onChange={e=>update("include_statistical_outliers",e.target.checked)}/>{tr("Include statistical outliers in this fit")}</label>
    <div className="metro-actions"><button className="metro-btn primary" disabled={busy||saving}>{tr(saving?"Saving...":"Save and recalculate")}</button><button type="button" className="metro-btn" disabled={busy||saving||!initial} onClick={()=>void submit(null)}>{tr("Restore automatic fit")}</button></div>
  </form>;
}
