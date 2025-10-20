import { IRMSUpload } from "@/components/irms-upload";

export default function AnalysesPage() {
  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold">Analyses</h1>
        <p className="text-sm text-muted-foreground">
          Upload IRMS output files to process, summarize, and review results.
        </p>
      </div>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">IRMS Results Processing</h2>
        <IRMSUpload />
      </section>
    </div>
  );
}
