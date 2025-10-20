"use client";

import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { uploadIRMSFile } from "@/lib/api";
import { JobStatusViewer } from "@/components/job-status";
import { IRMSResultsSummary } from "@/components/irms-results";

export function IRMSUpload() {
  const [file, setFile] = useState<File | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [inlineSummary, setInlineSummary] = useState<any | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState<boolean>(false);

  const onUpload = async () => {
    if (!file) return;
    setError(null);
    try {
      setUploading(true);
      const res = await uploadIRMSFile(file);
      if (res.result) {
        // Inline (synchronous) processing path
        const summary = res.result.summary ?? res.result;
        setInlineSummary(summary);
        setTaskId(null);
      } else if (res.task_id) {
        setInlineSummary(null);
        setTaskId(res.task_id);
      } else {
        throw new Error("Unexpected upload response");
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Input
          type="file"
          accept=".csv,.txt,.xlsx,.xls"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
        <Button onClick={onUpload} disabled={!file || uploading}>
          {uploading ? "Uploading…" : "Upload"}
        </Button>
      </div>
      {file && (
        <p className="text-xs text-muted-foreground">
          Selected: {file.name} ({Math.round(file.size / 1024)} KB)
        </p>
      )}
      {error && <p className="text-sm text-red-500">{error}</p>}
      {taskId && <JobStatusViewer taskId={taskId} />}
      {inlineSummary && (
        <div className="mt-4">
          <IRMSResultsSummary summary={inlineSummary} />
        </div>
      )}
    </div>
  );
}
