import { useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import { theme } from "../theme";

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
      await api.connectBrokerage({
        username,
        password,
        mfa_code: mfaCode,
      });
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
      <p style={{ color: theme.growth, fontSize: 13, marginTop: 4 }}>
        ✅ Connected — credentials verified with a read-only login and saved to
        your local backend.
      </p>
    );
  }

  return (
    <div className="card card-pad" style={{ marginBottom: 10 }}>
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
      <label className="tile-label" htmlFor="rh-password" style={{ marginTop: 10 }}>
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
      <label className="tile-label" htmlFor="rh-mfa-code" style={{ marginTop: 10 }}>
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
      <div style={{ color: theme.textMuted, fontSize: 12, marginTop: 6 }}>
        Open your authenticator app and enter the current 6-digit code. It's
        used once to verify the login — nothing beyond that is stored.
      </div>

      {status === "error" && error && (
        <p style={{ color: theme.decline, fontSize: 13, marginTop: 10 }}>{error}</p>
      )}

      <button
        className="btn btn-primary btn-block"
        style={{ marginTop: 12 }}
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
