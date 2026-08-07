import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import type { ForecastBackfillJob } from "../api/types";
import { usePoll } from "./usePoll";

const POLL_INTERVAL_MS = 2000;

export interface UseBackfillJobResult {
  job: ForecastBackfillJob | null;
  starting: boolean;
  error: string | null;
  start: (params?: { tickers?: string[]; start_date?: string; end_date?: string; use_fmp?: boolean; strategy_ids?: string[]; theta_c?: number }) => Promise<void>;
  cancel: () => Promise<void>;
  reset: () => void;
}

export function useBackfillJob(): UseBackfillJobResult {
  const [job, setJob] = useState<ForecastBackfillJob | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const reloadStatus = useCallback(async () => {
    if (!activeJobId) return;
    try {
      const status = await api.getForecastBackfillJobStatus(activeJobId);
      if (!alive.current) return;
      setJob(status);
      if (status.state !== "running") {
        setActiveJobId(null);
      }
    } catch (e) {
      if (!alive.current) return;
      setError(
        e instanceof ApiError
          ? e.message
          : "Lost contact with the backend while checking the backfill status."
      );
      setActiveJobId(null);
    }
  }, [activeJobId]);

  usePoll(reloadStatus, POLL_INTERVAL_MS, activeJobId !== null);

  const start = useCallback(
    async (params?: { tickers?: string[]; start_date?: string; end_date?: string; use_fmp?: boolean; strategy_ids?: string[]; theta_c?: number }) => {
      setActiveJobId(null);
      setError(null);
      setStarting(true);
      try {
        const initial = await api.runForecastBackfill(params);
        if (!alive.current) return;
        setJob(initial);
        setActiveJobId(initial.state === "running" ? initial.job_id : null);
      } catch (e) {
        if (!alive.current) return;
        setError(
          e instanceof ApiError
            ? e.message
            : "Could not reach the backend to start the backfill."
        );
      } finally {
        if (alive.current) setStarting(false);
      }
    },
    []
  );

  const cancel = useCallback(async () => {
    const jobId = activeJobId ?? job?.job_id ?? null;
    if (!jobId) return;
    setActiveJobId(null);
    try {
      const result = await api.cancelForecastBackfillJob(jobId);
      if (!alive.current) return;
      setJob(result);
    } catch (e) {
      if (!alive.current) return;
      setError(
        e instanceof ApiError ? e.message : "Could not cancel the backfill."
      );
    }
  }, [activeJobId, job]);

  const reset = useCallback(() => {
    setActiveJobId(null);
    setJob(null);
    setError(null);
  }, []);

  return { job, starting, error, start, cancel, reset };
}
