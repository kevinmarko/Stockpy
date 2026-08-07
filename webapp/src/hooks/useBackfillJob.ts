import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { ApiError, ForecastBackfillConflictError } from "../api/types";
import type { ForecastBackfillJob } from "../api/types";
import { usePoll } from "./usePoll";

const POLL_INTERVAL_MS = 2000;

export interface UseBackfillJobResult {
  job: ForecastBackfillJob | null;
  starting: boolean;
  /** A transport/request-level failure (network error, unexpected 4xx/5xx)
   *  -- NOT the same as a job that started fine and later reached
   *  state: "failed"/"timeout"/"cancelled", which is reported via `job`. */
  error: string | null;
  /** Set for a brief window after `start()` discovers (via a 409) that a
   *  backfill run was ALREADY in progress on the backend -- the caller is
   *  now polling that existing job's real status instead of hitting a dead
   *  end (see api/pilots_api.py's `run_forecast_backfill_endpoint`
   *  docstring for why the 409 body carries the existing job's id at all).
   *  Cleared automatically the moment the first real status poll for that
   *  job lands (`job` becomes non-null), or by a fresh `start()`/`reset()`. */
  notice: string | null;
  start: (params?: { tickers?: string[]; start_date?: string; end_date?: string; use_fmp?: boolean; strategy_ids?: string[]; theta_c?: number }) => Promise<void>;
  cancel: () => Promise<void>;
  reset: () => void;
}

export function useBackfillJob(): UseBackfillJobResult {
  const [job, setJob] = useState<ForecastBackfillJob | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // Kept in sync with `activeJobId` state SYNCHRONOUSLY (every setActiveJobId
  // call below has a matching activeJobIdRef.current assignment right next to
  // it) so `reloadStatus` can always check the CURRENT job id rather than the
  // one it closed over when the poll tick fired. Without this: an interval
  // tick fetches status for jobA while jobA is still active; before that
  // fetch resolves, start() is called again and supersedes activeJobId to
  // jobB; the late jobA response would otherwise still run unconditionally,
  // stomping jobB's fresh state (and, if jobA's stale response happened to be
  // terminal, incorrectly killing polling for jobB too).
  const activeJobIdRef = useRef<string | null>(null);

  const reloadStatus = useCallback(async () => {
    const requestedJobId = activeJobIdRef.current;
    if (!requestedJobId) return;
    try {
      const status = await api.getForecastBackfillJobStatus(requestedJobId);
      if (!alive.current) return;
      // A newer start()/cancel() superseded this job while the request was
      // in flight -- discard the stale response instead of applying it.
      if (activeJobIdRef.current !== requestedJobId) return;
      setJob(status);
      setNotice(null);
      if (status.state !== "running") {
        activeJobIdRef.current = null;
        setActiveJobId(null);
      }
    } catch (e) {
      if (!alive.current) return;
      if (activeJobIdRef.current !== requestedJobId) return;
      setError(
        e instanceof ApiError
          ? e.message
          : "Lost contact with the backend while checking the backfill status."
      );
      activeJobIdRef.current = null;
      setActiveJobId(null);
    }
  }, []);

  usePoll(reloadStatus, POLL_INTERVAL_MS, activeJobId !== null);

  const start = useCallback(
    async (params?: { tickers?: string[]; start_date?: string; end_date?: string; use_fmp?: boolean; strategy_ids?: string[]; theta_c?: number }) => {
      activeJobIdRef.current = null;
      setActiveJobId(null);
      setError(null);
      setNotice(null);
      setStarting(true);
      try {
        const initial = await api.runForecastBackfill(params);
        if (!alive.current) return;
        setJob(initial);
        const newActiveJobId = initial.state === "running" ? initial.job_id : null;
        activeJobIdRef.current = newActiveJobId;
        setActiveJobId(newActiveJobId);
      } catch (e) {
        if (!alive.current) return;
        if (e instanceof ForecastBackfillConflictError && e.existingJobId) {
          // A run was already in progress -- start tracking IT instead of
          // surfacing a dead-end error. This is exactly what the backend's
          // 409 body carrying the existing job's id is FOR (see
          // api/pilots_api.py's run_forecast_backfill_endpoint docstring).
          setNotice("A backfill run is already in progress — now tracking it.");
          activeJobIdRef.current = e.existingJobId;
          setActiveJobId(e.existingJobId);
        } else {
          setError(
            e instanceof ApiError
              ? e.message
              : "Could not reach the backend to start the backfill."
          );
        }
      } finally {
        if (alive.current) setStarting(false);
      }
    },
    []
  );

  const cancel = useCallback(async () => {
    const jobId = activeJobIdRef.current ?? job?.job_id ?? null;
    if (!jobId) return;
    activeJobIdRef.current = null;
    setActiveJobId(null);
    try {
      const result = await api.cancelForecastBackfillJob(jobId);
      if (!alive.current) return;
      setJob(result);
      setNotice(null);
    } catch (e) {
      if (!alive.current) return;
      setError(
        e instanceof ApiError ? e.message : "Could not cancel the backfill."
      );
    }
  }, [job]);

  const reset = useCallback(() => {
    activeJobIdRef.current = null;
    setActiveJobId(null);
    setJob(null);
    setError(null);
    setNotice(null);
  }, []);

  return { job, starting, error, notice, start, cancel, reset };
}
