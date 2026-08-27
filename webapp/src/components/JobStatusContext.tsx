import React, { useMemo } from "react";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { usePoll } from "../hooks/usePoll";
import { JobStatusCtx, JobStatusState } from "../context/jobStatusContext";
import type { JobsListResponse } from "../api/types";

export function JobStatusProvider({ children }: { children: React.ReactNode }) {
  const { data, loading, error, reload } = useApi<JobsListResponse>(
    () => api.listJobs(false, 20),
    []
  );

  usePoll(reload, 3000, true);

  const activeJobs = useMemo(() => (data?.jobs || []).filter(j => j.is_running), [data]);

  const value = useMemo<JobStatusState>(() => {
    return {
      jobs: data?.jobs || [],
      activeJobs,
      loading,
      error: error != null ? String(error) : null,
      reload,
      isJobTypeActive: (jobType: string) => activeJobs.some((j) => j.job_type === jobType),
      isCommandActive: (commandName: string) => activeJobs.some((j) => j.command_name === commandName),
    };
  }, [data, activeJobs, loading, error, reload]);

  return (
    <JobStatusCtx.Provider value={value}>
      {children}
    </JobStatusCtx.Provider>
  );
}
