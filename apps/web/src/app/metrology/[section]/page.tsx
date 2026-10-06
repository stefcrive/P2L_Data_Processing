import { notFound } from "next/navigation";
import { MetrologyWorkspace } from "@/components/metrology/workspace";

export default async function MetrologySection({params,searchParams}:{params:Promise<{section:string}>;searchParams:Promise<Record<string,string|undefined>>}) {
  const {section}=await params;
  if(!["results","qualification","history","materials","interventions","reports","audit"].includes(section))notFound();
  const query=await searchParams;
  return <MetrologyWorkspace initialSection={section} initialSessionId={query.session} initialRunId={query.run} initialQualificationId={query.qualification} createSession={query.new==="1"} newContext={query.workflow==="qualification"?"qualification":"routine"}/>;
}
