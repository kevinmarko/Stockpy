import { createContext } from "react";
import type { JobRecord } from "../api/types";

export interface JobStatusState {
  jobs: JobRecord[];
  activeJobs: JobRecord[];
  loading: boolean;
  error: string | null;
  reload: () => void;
  isJobTypeActive: (jobType: string) => boolean;
  isCommandActive: (commandName: string) => boolean;
}

export const DEFAULT_JOB_STATUS: JobStatusState = {
  jobs: [],
  activeJobs: [],
  loading: true,
  error: null,
  reload: () => {},
  isJobTypeActive: () => false,
  isCommandActive: () => false,
};

export const JobStatusCtx = createContext<JobStatusState>(DEFAULT_JOB_STATUS);
