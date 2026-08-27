import { useContext } from "react";
import { JobStatusCtx } from "../context/jobStatusContext";

export function useJobStatus() {
  return useContext(JobStatusCtx);
}
