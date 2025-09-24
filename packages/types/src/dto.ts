import { z } from "zod";

export const JobStatus = z.object({
  task_id: z.string(),
  state: z.string(),
  result: z.any().optional(),
});

export type JobStatus = z.infer<typeof JobStatus>;

