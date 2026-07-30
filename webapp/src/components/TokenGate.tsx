import { useState } from "react";
import { setStoredToken } from "../auth/apiToken";

/**
 * Shown instead of the app on a non-loopback origin (LAN/Tailscale) with no
 * token in sessionStorage yet. api/client.ts's build-time VITE_API_TOKEN
 * fallback only applies on loopback -- everywhere else, the operator enters
 * the token once here; it's stored in sessionStorage (never bundled, never
 * persisted past the tab closing) and the page reloads so every module that
 * resolves the token at import time (api/client.ts's TOKEN constant) picks
 * up the real value.
 */
export function TokenGate() {
  const [value, setValue] = useState("");

  const submit = () => {
    if (!value.trim()) return;
    setStoredToken(value.trim());
    window.location.reload();
  };

  return (
    <div className="app app-standalone" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "100vh" }}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        style={{ maxWidth: 360, width: "100%", padding: 24 }}
      >
        <h1 className="screen-title" style={{ marginBottom: 8 }}>API token required</h1>
        <p className="screen-sub" style={{ marginBottom: 16 }}>
          This device isn't localhost, so the token baked into the app build
          doesn't apply here. Enter the backend's <code>STATE_API_TOKEN</code>{" "}
          to continue.
        </p>
        <input
          type="password"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="API token"
          style={{
            width: "100%",
            padding: "10px 12px",
            borderRadius: 8,
            border: "1px solid var(--border, #333)",
            marginBottom: 12,
            fontSize: "var(--t-body, 15px)",
          }}
        />
        <button type="submit" disabled={!value.trim()} style={{ width: "100%" }}>
          Continue
        </button>
      </form>
    </div>
  );
}
