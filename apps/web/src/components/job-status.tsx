"use client";

import { useEffect, useState } from "react";
import { fetchJobStatus } from "@/src/lib/api";

export function JobStatusViewer({ taskId }: { taskId: string }) {
  const [state, setState] = useState<string>("PENDING");
  const [result, setResult] = useState<any>(null);

  useEffect(() => {
    let active = true;
    const tick = async () => {
      const res = await fetchJobStatus(taskId);
      if (!active) return;
      setState(res.state);
      if (res.state === "SUCCESS" && res.result) {
        setResult(res.result);
      } else if (["FAILURE", "REVOKED"].includes(res.state)) {
        setResult({ error: true });
      } else {
        setTimeout(tick, 1000);
      }
    };
    tick();
    return () => {
      active = false;
    };
  }, [taskId]);

  return (
    <div className="text-sm">
      <div>Task: {taskId}</div>
      <div>Status: {state}</div>
      {result && (
        <pre className="mt-2 rounded bg-muted p-2 text-xs overflow-auto max-h-64">
          {JSON.stringify(result, null, 2)}
        </pre>
      )}
    </div>
  );
}

