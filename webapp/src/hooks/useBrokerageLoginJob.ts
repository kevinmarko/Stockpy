import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import type { BrokerageLoginJob, BrokerageLoginMode } from "../api/types";
import { usePoll } from "./usePoll";

const POLL_INTERVAL_MS = 2000;

export interface BrokerageLoginCredentials {
  username: string;
  password: string;
}

export interface UseBrokerageLoginJobResult {
  /** `null` until `start()` is called; then the live status object, replaced
   *  wholesale on every poll (never merged/patched -- the server is the only
   *  source of truth for `seconds_remaining`, see the type's own docs). */
  job: BrokerageLoginJob | null;
  /** True only for the brief window between calling `start()` and the
   *  initial 202 response -- distinct from `job?.state === "running"`,
   *  which covers everything after that (including "awaiting_approval"). */
  starting: boolean;
  /** A transport/request-level failure (network error, unexpected 4xx/5xx)
   *  -- NOT the same as a job that started fine and later reached
   *  state: "failed"/"timeout"/"cancelled", which is reported via `job`. */
  error: string | null;
  start: (mode: BrokerageLoginMode, creds?: BrokerageLoginCredentials) => Promise<void>;
  cancel: () => Promise<void>;
  /** Clears `job`/`error` back to the initial idle state (and stops any
   *  active poll) WITHOUT touching the backend -- e.g. so a stale "Refreshed
   *  just now" success notice doesn't linger past an unrelated Disconnect. */
  reset: () => void;
}

/**
 * useBrokerageLoginJob — owns the async device-approval-push Robinhood login
 * job lifecycle: starting a job (POST /brokerage/connect or .../refresh),
 * polling GET /brokerage/login/status/{job_id} every 2s while it's running,
 * and cancelling it. Shared by RobinhoodConnectForm (typed-credential
 * connect) and any other trigger (Settings' "Force fresh login" /.env
 * button, AgenticTrading's "Refresh Data") so every surface polls the SAME
 * way and can't drift.
 *
 * Built on `usePoll` rather than a hand-rolled interval: `activeJobId` is the
 * enable/disable switch usePoll needs, and it flips to `null` the instant a
 * poll (or the initial start) observes a terminal state -- so the interval
 * this hook owns is torn down automatically, not just left to no-op forever.
 *
 * `seconds_remaining` is never counted down client-side between polls -- the
 * value rendered is always exactly what the last poll reported, matching the
 * API contract's honesty requirement (a wedged backend must not be able to
 * show a plausible-looking countdown for a job that isn't really progressing).
 */
export function useBrokerageLoginJob(): UseBrokerageLoginJobResult {
  const [job, setJob] = useState<BrokerageLoginJob | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  // Mirrors useApi.ts's/useMutation.ts's alive-ref pattern -- a poll tick,
  // start(), or cancel() response that arrives after the component
  // unmounted (e.g. the user navigated away mid-login, or a real interval
  // tick lands just after `usePoll`'s own cleanup fired) must not call
  // setState on an unmounted component. Unlike those two hooks this one
  // owns a REAL setInterval (via usePoll) whenever a job is "running", so
  // an in-flight `reloadStatus()` call can genuinely still be awaiting a
  // response at the exact moment of unmount -- this guard is what makes
  // that safe rather than merely unlikely.
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
      const status = await api.getBrokerageLoginStatus(activeJobId);
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
          : "Lost contact with the backend while checking the login status."
      );
      // Stop polling a job we can no longer reach rather than retrying
      // forever against an unreachable backend -- surfaced as an honest
      // error, not a silently-stuck spinner.
      setActiveJobId(null);
    }
  }, [activeJobId]);

  usePoll(reloadStatus, POLL_INTERVAL_MS, activeJobId !== null);

  const start = useCallback(
    async (mode: BrokerageLoginMode, creds?: BrokerageLoginCredentials) => {
      // A new start() always supersedes whatever job (if any) was previously
      // being polled -- stop that poll before this one's 202 comes back.
      setActiveJobId(null);
      setError(null);
      setStarting(true);
      try {
        const initial =
          mode === "connect"
            ? await api.connectBrokerage({
                username: creds?.username ?? "",
                password: creds?.password ?? "",
              })
            : await api.refreshBrokerage();
        if (!alive.current) return;
        setJob(initial);
        setActiveJobId(initial.state === "running" ? initial.job_id : null);
      } catch (e) {
        if (!alive.current) return;
        setError(
          e instanceof ApiError
            ? e.message
            : "Could not reach the backend to start the login."
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
    // Stop polling immediately rather than waiting for the cancel round-trip
    // -- a stray poll landing between the cancel request and its response
    // would otherwise briefly resurrect "running" in the UI.
    setActiveJobId(null);
    try {
      const result = await api.cancelBrokerageLogin(jobId);
      if (!alive.current) return;
      setJob(result);
    } catch (e) {
      if (!alive.current) return;
      setError(
        e instanceof ApiError ? e.message : "Could not cancel the login."
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
