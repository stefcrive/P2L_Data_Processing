import { notFound } from "next/navigation";

export default async function MetrologySection({params,searchParams}:{params:Promise<{section:string}>;searchParams:Promise<Record<string,string|undefined>>}) {
  const {section}=await params;
  if(!["results","qualification","history","materials","interventions","reports","audit"].includes(section))notFound();
  return null;
}
