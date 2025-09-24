"use client";

import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { uploadIRMSFile } from "@/lib/api";
import { JobStatusViewer } from "@/components/job-status";

export function IRMSUpload() {
  const [file, setFile] = useState<File | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onUpload = async () => {
    if (!file) return;
    setError(null);
    try {
      const res = await uploadIRMSFile(file);
      setTaskId(res.task_id);
    } catch (e: any) {
      setError(e.message);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Input type="file" accept=".csv,.txt,.xlsx" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <Button onClick={onUpload} disabled={!file}>
          Upload
        </Button>
      </div>
      {error && <p className="text-sm text-red-500">{error}</p>}
      {taskId && <JobStatusViewer taskId={taskId} />}
    </div>
  );
}
