import { useEffect, useState } from "react";
import { useBrokerageLoginJob } from "../hooks/useBrokerageLoginJob";
import { PHASE_LABEL, formatLoginCountdown, loginFailureMessage } from "../brokerageLoginCopy";
import { theme } from "../theme";
import { Notice } from "./ui";

/**
 * RobinhoodConnectForm — the credential-intake form driving the async
 * device-approval PUSH login (POST /brokerage/connect, polled via GET
 * /brokerage/login/status/{job_id} through `useBrokerageLoginJob`) —
 * extracted from Onboarding so the onboarding wizard, the Settings brokerage
 * section, and the Agentic Trading auth modal share ONE implementation and
 * can't drift.
 *
 * There is no 6-digit code anymore: the operator approves the login by
 * tapping a push notification in the Robinhood mobile app, so this form only
 * ever collects username/password, then hands off to the hook to start the
 * job and poll it to completion.
 *
 * Owns only the transient form fields + the login job's live state. It does
 * NOT own the durable "connected" fact: on a successful job it calls
 * `onConnected()` and lets the PARENT decide what to render next (Onboarding
 * flips a `connected` flag that unmounts this form and marks its option card
 * "— connected"; Settings re-fetches GET /brokerage/status and swaps in the
 * connected view; AgenticTrading closes its modal and reloads).
 *
 * Honesty: credentials go only to the local backend (loopback + read-only
 * verification server-side — see api/pilots_api.py); the submitted password
 * is never echoed back, and the fields unmount the moment the parent swaps
 * views. A timeout/failure is never presented as "you denied the login" —
 * see `loginFailureMessage` in ../brokerageLoginCopy.ts.
 */
export function RobinhoodConnectForm({
  onConnected,
  title,
  subtitle,
}: {
  onConnected?: () => void;
  /** Optional copy overrides so the SAME form reads correctly whether it's
   *  rendered inline (Settings/Onboarding) or inside a modal with its own
   *  framing (AgenticTrading's auth modal). */
  title?: string;
  subtitle?: string;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const { job, starting, error, start, cancel } = useBrokerageLoginJob();

  const isRunning = starting || job?.state === "running";
  const isTerminalFailure =
    job != null && (job.state === "failed" || job.state === "timeout" || job.state === "cancelled");

  // Fire onConnected exactly once per job that actually succeeds -- keyed on
  // job_id + state so a retried job (a fresh job_id) can fire it again too.
  useEffect(() => {
    if (job?.state === "succeeded") {
      onConnected?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.job_id, job?.state]);

  if (job?.state === "succeeded") {
    return (
      <Notice variant="success" style={{ marginTop: "var(--s-1)" }}>
        <span aria-hidden>✅</span>
        <span>
          Connected — approved in the Robinhood app and saved to your local
          backend.
        </span>
      </Notice>
    );
  }

  const submit = () => {
    void start("connect", { username, password });
  };

  return (
    <div className="card card-pad" style={{ marginBottom: "var(--s-2-5)" }}>
      {title && (
        <h3 style={{ margin: "0 0 var(--s-1)", fontSize: "var(--t-title)" }}>{title}</h3>
      )}
      {subtitle && (
        <p
          style={{
            color: theme.textSecondary,
            fontSize: "var(--t-body)",
            marginTop: 0,
            marginBottom: "var(--s-3)",
          }}
        >
          {subtitle}
        </p>
      )}

      {isRunning ? (
        <div data-testid="brokerage-login-running">
          <p style={{ fontSize: "var(--t-input)", fontWeight: 600, marginTop: 0 }}>
            <span aria-hidden>📱</span> Open the Robinhood app and approve this login
          </p>
          {job ? (
            <>
              <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)" }}>
                {PHASE_LABEL[job.phase]} — {formatLoginCountdown(job.seconds_remaining)} remaining
              </p>
              <button className="btn btn-neutral" onClick={() => void cancel()}>
                Cancel
              </button>
            </>
          ) : (
            <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)" }}>
              Starting…
            </p>
          )}
        </div>
      ) : (
        <>
          <label className="tile-label" htmlFor="rh-username">
            Robinhood email
          </label>
          <input
            id="rh-username"
            className="field"
            type="email"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <label
            className="tile-label"
            htmlFor="rh-password"
            style={{ marginTop: "var(--s-2-5)" }}
          >
            Password
          </label>
          <input
            id="rh-password"
            className="field"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <div
            style={{
              color: theme.textMuted,
              fontSize: "var(--t-caption)",
              marginTop: "var(--s-1-5)",
            }}
          >
            No code to type — approve the login with a tap in your Robinhood
            mobile app when prompted.
          </div>

          {error && (
            <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
              {error}
            </Notice>
          )}
          {!error && isTerminalFailure && (
            <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
              {loginFailureMessage(job)}
            </Notice>
          )}

          <button
            className="btn btn-primary btn-block"
            style={{ marginTop: "var(--s-3)" }}
            disabled={!username.trim() || !password.trim()}
            onClick={submit}
          >
            {isTerminalFailure || error ? "Try again" : "Connect"}
          </button>
        </>
      )}
    </div>
  );
}
