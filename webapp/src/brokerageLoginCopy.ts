import type { BrokerageLoginJob, BrokerageLoginPhase } from "./api/types";

/**
 * brokerageLoginCopy.ts — pure display helpers for the async device-approval
 * PUSH Robinhood login job (`useBrokerageLoginJob`), shared by
 * RobinhoodConnectForm.tsx (typed-credential connect) and Settings.tsx's
 * BrokerageSection (the one-click ".env credentials" / "Force fresh login"
 * refresh trigger) so the phase labels, countdown format, and honest
 * failure copy can't drift between the two surfaces.
 */

export const PHASE_LABEL: Record<BrokerageLoginPhase, string> = {
  starting: "Starting…",
  authenticating: "Authenticating…",
  awaiting_approval: "Waiting for approval…",
  verifying: "Verifying…",
  fetching_snapshot: "Fetching your account…",
  done: "Done",
};

/** `seconds` -> "m:ss", clamped to non-negative (a stale/late render must
 *  never show a negative countdown). */
export function formatLoginCountdown(seconds: number): string {
  const clamped = Math.max(0, Math.round(seconds));
  const m = Math.floor(clamped / 60);
  const s = clamped % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * Honest, non-presumptuous copy per terminal state. The backend's login
 * library has no separate code path for a denied push vs. one that was
 * simply never seen — so this NEVER claims the operator denied the login,
 * only that nothing came through / nothing was saved.
 */
export function loginFailureMessage(job: BrokerageLoginJob | null): string {
  if (!job) return "The login did not complete. Nothing was saved.";
  if (job.state === "timeout") {
    return "No approval came through in time. Nothing was saved.";
  }
  if (job.state === "cancelled") {
    return "Login cancelled. Nothing was saved.";
  }
  switch (job.error_code) {
    case "no_credentials":
      return "No Robinhood credentials were available to try.";
    case "challenge_unsupported":
      return "Robinhood asked for a verification step this app doesn't support yet.";
    case "auth_failed":
      return "Robinhood rejected that username or password.";
    case "child_start_failed":
      return "Could not start the login process on the backend.";
    default:
      return "The login did not complete. Nothing was saved.";
  }
}
