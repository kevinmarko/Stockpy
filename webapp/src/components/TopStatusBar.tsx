import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Modal } from "./Modal";
import { DensityToggle } from "./DensityToggle";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { usePoll } from "../hooks/usePoll";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { useAutoRefresh } from "./AutoRefreshContext";
import { computeMarketSession } from "../marketSession";
import { useExecutionMode } from "./ExecutionModeContext";
import type { ObservabilitySummary } from "../api/types";

const AUTOMATION_POLL_MS = 30_000;
// Regime is a slow-moving macro read (recomputed once per pipeline cycle,
// not per request) sourced from the heavier observability summary -- polled
// far less often than the kill-switch/heartbeat status so this always-on bar
// doesn't pay for portfolio-risk/equity-curve computation every 30s on every
// screen.
const REGIME_POLL_MS = 300_000;

export function TopStatusBar() {
  const [marketSession, setMarketSession] = useState(() => computeMarketSession(new Date()));
  const [showKillSwitchModal, setShowKillSwitchModal] = useState(false);
  const [reason, setReason] = useState("");

  const { safetyTelemetryEnabled, autoRefreshIntervalMs } = useAutoRefresh();

  const { data: automationData, reload: reloadAutomation, error: automationError } = useExecutionMode();
  // Kill-switch/heartbeat telemetry -- deliberately outside the
  // market-session/visibility/category auto-refresh gates (a plain usePoll,
  // not useAutoPoll). A stale kill-switch reading is a safety issue, not a
  // battery optimization, so it gets its own independent toggle
  // (safetyTelemetryEnabled) rather than folding under the data auto-refresh
  // master switch.
  usePoll(reloadAutomation, AUTOMATION_POLL_MS, safetyTelemetryEnabled);

  const regime = useApi<ObservabilitySummary>(() => api.getObservabilitySummary("1M", 30), []);
  // Heavy composite read -- the Math.max is a FLOOR, not an override: this
  // must never poll faster than its own cadence just because the operator
  // picked a short global auto-refresh interval.
  useAutoPoll(regime.reload, "observability", {
    hasError: regime.error != null,
    customIntervalMs: Math.max(REGIME_POLL_MS, autoRefreshIntervalMs),
  });

  useEffect(() => {
    const interval = setInterval(() => setMarketSession(computeMarketSession(new Date())), 60_000);
    return () => clearInterval(interval);
  }, []);

  const pauseMutation = useMutation((r: string) => api.pauseAutomation(r));
  const resumeMutation = useMutation((r: string) => api.resumeAutomation(r));

  const killSwitchActive = automationData?.kill_switch.active ?? null;
  const advisoryOnly = automationData?.advisory_only ?? true;
  const daemonAlive = automationData?.daemon.alive ?? null;
  const snapshotAgeSeconds = automationData?.pipeline.snapshot_age_seconds ?? null;
  const resumeBlocked = killSwitchActive === false ? false : !advisoryOnly;
  const busy = pauseMutation.pending || resumeMutation.pending;

  const marketRegime = regime.data?.regime.market_regime ?? null;

  const openConfirm = () => {
    setReason("");
    setShowKillSwitchModal(true);
  };

  const confirmToggle = async () => {
    if (!reason.trim()) return;
    const result = killSwitchActive
      ? await resumeMutation.run(reason)
      : await pauseMutation.run(reason);
    if (!result) return; // mutation error -- Notice below stays visible via the toast fallback
    setShowKillSwitchModal(false);
    reloadAutomation();
    if (killSwitchActive) {
      toast.success(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Kill Switch RESET (resumed)</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
            Recommendations resume on the next scheduled or manual run.
          </span>
        </div>
      );
    } else {
      toast.error(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Kill Switch TRIPPED (paused)</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
            New recommendations stop until resumed. The schedule keeps running.
          </span>
        </div>
      );
    }
  };

  const heartbeatLabel = daemonAlive == null ? "Unknown" : daemonAlive ? "Live" : "Offline";
  const heartbeatIcon = daemonAlive == null ? "⚪" : daemonAlive ? "🟢" : "🔴";
  const heartbeatColor = daemonAlive == null ? "var(--text-muted)" : daemonAlive ? "var(--growth)" : "var(--decline)";
  const snapshotAgeLabel =
    snapshotAgeSeconds == null
      ? "no snapshot yet"
      : snapshotAgeSeconds < 120
      ? `${Math.round(snapshotAgeSeconds)}s ago`
      : `${Math.round(snapshotAgeSeconds / 60)}m ago`;

  return (
    <>
      <header
        style={{
          height: "40px",
          minHeight: "40px",
          background: "var(--surface)",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0 var(--s-4)",
          fontSize: "var(--t-caption)",
          color: "var(--text-secondary)",
          gap: "var(--s-3)",
          overflowX: "auto",
          whiteSpace: "nowrap",
          scrollbarWidth: "none",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-4)" }}>
          {/* Heartbeat — real daemon liveness + snapshot age, GET /automation/status */}
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)" }} title={automationError ?? undefined}>
            <span style={{ color: heartbeatColor }}>{heartbeatIcon}</span>
            <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{heartbeatLabel}</span>
            <span style={{ color: "var(--text-muted)", fontSize: "var(--t-micro)" }}>({snapshotAgeLabel})</span>
            {!safetyTelemetryEnabled && (
              <span
                role="img"
                aria-label="Safety telemetry auto-refresh is off"
                title="Safety telemetry auto-refresh is off in Settings → Data Auto-Refresh. This row shows the state as of the last page load."
                data-testid="safety-telemetry-off-indicator"
                style={{ color: "var(--caution)", fontSize: "var(--t-micro)" }}
              >
                ⚠
              </span>
            )}
          </div>

          {/* Macro Regime — read-only; there is no operator override for the
              computed regime, only for the gate that acts on it (Observability
              tab). Showing an editable dropdown here would be fake control. */}
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)" }}>
            <span style={{ color: "var(--text-muted)" }}>Regime:</span>
            <span
              style={{
                fontWeight: 600,
                color: marketRegime == null ? "var(--text-muted)" : "var(--text-primary)",
              }}
            >
              {marketRegime ?? "—"}
            </span>
          </div>

          {/* Kill Switch Indicator — real state, GET /automation/status */}
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)" }}>
            <span style={{ color: "var(--text-muted)" }}>Kill Switch:</span>
            {killSwitchActive == null ? (
              <span style={{ color: "var(--text-muted)" }}>—</span>
            ) : (
              <span
                style={{
                  padding: "2px 6px",
                  borderRadius: "var(--r-xs)",
                  fontSize: "var(--t-micro)",
                  fontWeight: 700,
                  background: killSwitchActive ? "rgba(239, 68, 68, 0.2)" : "rgba(16, 185, 129, 0.15)",
                  color: killSwitchActive ? "var(--decline)" : "var(--growth)",
                  border: `1px solid ${killSwitchActive ? "rgba(239, 68, 68, 0.4)" : "rgba(16, 185, 129, 0.3)"}`,
                }}
              >
                {killSwitchActive ? "TRIPPED (PAUSED)" : "ACTIVE (RUNNING)"}
              </span>
            )}
          </div>

          {/* Market Session — pure client-side clock, real ET time */}
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)" }}>
            <span style={{ color: "var(--text-muted)" }}>Session:</span>
            <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{marketSession}</span>
          </div>
        </div>

        {/* Quick Actions */}
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <DensityToggle />
          <button
            onClick={openConfirm}
            disabled={killSwitchActive == null || (killSwitchActive === true && resumeBlocked)}
            title={killSwitchActive === true && resumeBlocked ? "Resume must be done at the console while live trading is enabled." : undefined}
            style={{
              background: killSwitchActive ? "rgba(16, 185, 129, 0.15)" : "var(--surface-2)",
              color: killSwitchActive ? "var(--growth)" : "var(--decline)",
              border: `1px solid ${killSwitchActive ? "var(--growth)" : "var(--decline)"}`,
              borderRadius: "var(--r-xs)",
              padding: "3px 8px",
              fontSize: "var(--t-micro)",
              fontWeight: 600,
              cursor: killSwitchActive == null ? "default" : "pointer",
              opacity: killSwitchActive == null ? 0.5 : 1,
            }}
          >
            {killSwitchActive ? "🛡️ Reset Kill Switch" : "🚨 Trip Kill Switch"}
          </button>
        </div>
      </header>

      {showKillSwitchModal && (
        <Modal
          ariaLabel={killSwitchActive ? "Reset Global Kill Switch?" : "Trip Global Kill Switch?"}
          onClose={() => setShowKillSwitchModal(false)}
        >
          <div style={{ padding: "var(--s-4)", width: "min(90vw, 420px)" }}>
            <h3 style={{ margin: "0 0 var(--s-2)", color: killSwitchActive ? "var(--growth)" : "var(--decline)" }}>
              {killSwitchActive ? "Reset Global Kill Switch?" : "Trip Global Kill Switch?"}
            </h3>
            <p style={{ fontSize: "var(--t-body)", color: "var(--text-secondary)", lineHeight: 1.5 }}>
              {killSwitchActive
                ? "Recommendations resume on the next scheduled or manual run."
                : "New recommendations stop until resumed. The schedule keeps running -- cycles still run, they just produce no recommendations."}
            </p>
            <label style={{ display: "block", marginTop: "var(--s-3)" }}>
              <span style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>Reason (required)</span>
              <input
                className="input"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                style={{ width: "100%", marginTop: "var(--s-1)" }}
              />
            </label>
            {(pauseMutation.error || resumeMutation.error) && (
              <p style={{ color: "var(--decline)", fontSize: "var(--t-caption)", marginTop: "var(--s-2)" }}>
                {pauseMutation.error ?? resumeMutation.error}
              </p>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--s-2)", marginTop: "var(--s-4)" }}>
              <button className="btn" onClick={() => setShowKillSwitchModal(false)}>Cancel</button>
              <button
                className="btn"
                onClick={confirmToggle}
                disabled={!reason.trim() || busy}
                style={{
                  background: killSwitchActive ? "var(--growth)" : "var(--decline)",
                  color: "#fff",
                  fontWeight: 600,
                }}
              >
                {busy ? "Working…" : killSwitchActive ? "Reset Kill Switch" : "Trip Kill Switch"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
