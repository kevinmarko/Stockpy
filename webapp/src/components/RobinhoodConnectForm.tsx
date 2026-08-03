import { useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import { theme } from "../theme";
import { Notice } from "./ui";

type RobinhoodConnectStatus = "idle" | "connecting" | "connected" | "error";

/**
 * RobinhoodConnectForm — the credential-intake form for POST /brokerage/connect,
 * extracted from Onboarding so the onboarding wizard and the Settings brokerage
 * section share ONE implementation (username / password / one-time authenticator
 * code inputs + the connecting -> connected/error state machine) and can't drift.
 *
 * Owns only the transient form fields + connecting/error status. It does NOT own
 * the durable "connected" fact: on a verified connect it calls `onConnected()`
 * and lets the PARENT decide what to render next (Onboarding flips a `connected`
 * flag that unmounts this form and marks its option card "— connected"; Settings
 * re-fetches GET /brokerage/status and swaps in the connected view). Between the
 * success and the parent re-rendering it away, the `connected` branch below shows
 * a brief confirmation rather than the still-filled form.
 *
 * Honesty: credentials go only to the local backend (loopback + read-only
 * verification server-side — see api/pilots_api.py); the submitted password and
 * one-time code are never echoed back, and the fields unmount the moment the
 * parent swaps views. The 6-digit code is used once to verify the login and is
 * never persisted — only username/password are written to .env on success.
 */
export function RobinhoodConnectForm({ onConnected }: { onConnected?: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [status, setStatus] = useState<RobinhoodConnectStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  const connect = async () => {
    setStatus("connecting");
    setError(null);
    try {
      let res = await api.connectBrokerage({
        username,
        password,
        mfa_code: mfaCode,
      });

      if (res.status === "pending" && res.task_id) {
        const taskId = res.task_id;
        while (res.status === "pending") {
          await new Promise((r) => setTimeout(r, 1000));
          res = await api.getConnectBrokerageStatus(taskId);
        }
      }

      if (res.status === "error") {
        throw new ApiError(res.error || "Could not verify Robinhood credentials.", 401);
      }

      setStatus("connected");
      onConnected?.();
    } catch (e) {
      setStatus("error");
      setError(
        e instanceof ApiError
          ? e.message
          : "Could not reach the backend to verify credentials."
      );
    }
  };

  if (status === "connected") {
    return (
      <Notice variant="success" style={{ marginTop: "var(--s-1)" }}>
        <span aria-hidden>✅</span>
        <span>
          Connected — credentials verified with a read-only login and saved to
          your local backend.
        </span>
      </Notice>
    );
  }

  return (
    <div className="card card-pad" style={{ marginBottom: "var(--s-2-5)" }}>
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
      <label className="tile-label" htmlFor="rh-password" style={{ marginTop: "var(--s-2-5)" }}>
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
      <label className="tile-label" htmlFor="rh-mfa-code" style={{ marginTop: "var(--s-2-5)" }}>
        Authenticator app code
      </label>
      <input
        id="rh-mfa-code"
        className="field"
        type="password"
        inputMode="numeric"
        autoComplete="off"
        maxLength={6}
        value={mfaCode}
        onChange={(e) => setMfaCode(e.target.value)}
      />
      <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-1-5)" }}>
        Open your authenticator app and enter the current 6-digit code. It's
        used once to verify the login — nothing beyond that is stored.
      </div>

      {status === "error" && error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
          {error}
        </Notice>
      )}

      <button
        className="btn btn-primary btn-block"
        style={{ marginTop: "var(--s-3)" }}
        disabled={
          status === "connecting" ||
          !username.trim() ||
          !password.trim() ||
          mfaCode.trim().length !== 6
        }
        onClick={connect}
      >
        {status === "connecting" ? "Verifying…" : "Connect"}
      </button>
    </div>
  );
}
