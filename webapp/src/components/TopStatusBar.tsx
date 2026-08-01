import { useState, useEffect } from "react";
import { useToast } from "./ToastContext";
import { Modal } from "./Modal";

export function TopStatusBar() {
  const { addToast } = useToast();
  const [heartbeatSeconds, setHeartbeatSeconds] = useState(0);
  const [isLive] = useState(true);
  const [macroRegime, setMacroRegime] = useState<"RISK-ON" | "DEFENSIVE" | "UNKNOWN">("RISK-ON");
  const [killSwitchActive, setKillSwitchActive] = useState(true);
  const [marketSession, setMarketSession] = useState<"RTH (Open)" | "PRE/POST" | "CLOSED">("RTH (Open)");
  const [showKillSwitchModal, setShowKillSwitchModal] = useState(false);
  const [showPauseModal, setShowPauseModal] = useState(false);
  const [isPipelinePaused, setIsPipelinePaused] = useState(false);

  // Heartbeat counter
  useEffect(() => {
    const timer = setInterval(() => {
      setHeartbeatSeconds((prev) => prev + 1);
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  // Compute market session dynamically from ET time
  useEffect(() => {
    const checkMarket = () => {
      const now = new Date();
      // Rough ET calculation for display
      const hours = now.getHours();
      if (hours >= 9 && hours < 16) {
        setMarketSession("RTH (Open)");
      } else if ((hours >= 4 && hours < 9) || (hours >= 16 && hours < 20)) {
        setMarketSession("PRE/POST");
      } else {
        setMarketSession("CLOSED");
      }
    };
    checkMarket();
    const interval = setInterval(checkMarket, 60000);
    return () => clearInterval(interval);
  }, []);

  const handleToggleKillSwitch = () => {
    const newState = !killSwitchActive;
    setKillSwitchActive(newState);
    setShowKillSwitchModal(false);
    addToast({
      type: newState ? "success" : "error",
      title: newState ? "Kill Switch ENGAGED (Safe)" : "Kill Switch TRIPPED (Halted)",
      description: newState ? "Advisory quarantine active." : "Global order pipeline halted.",
    });
  };

  const handleTogglePausePipeline = () => {
    const newState = !isPipelinePaused;
    setIsPipelinePaused(newState);
    setShowPauseModal(false);
    addToast({
      type: newState ? "warning" : "info",
      title: newState ? "Pipeline Paused" : "Pipeline Resumed",
      description: newState ? "Execution queue paused by operator." : "Normal pipeline cycle resumed.",
    });
  };

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
          {/* Heartbeat */}
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)" }}>
            <span style={{ color: isLive ? "var(--growth)" : "var(--decline)" }}>
              {isLive ? "🟢" : "🔴"}
            </span>
            <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
              {isLive ? "Live" : "Stale"}
            </span>
            <span style={{ color: "var(--text-muted)", fontSize: "var(--t-micro)" }}>
              ({heartbeatSeconds}s ago)
            </span>
          </div>

          {/* Macro Regime */}
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)" }}>
            <span style={{ color: "var(--text-muted)" }}>Regime:</span>
            <select
              value={macroRegime}
              onChange={(e) => {
                const val = e.target.value as any;
                setMacroRegime(val);
                addToast({
                  type: "info",
                  title: `Macro Regime: ${val}`,
                  description: "Operator override set.",
                });
              }}
              style={{
                background: "var(--surface-2)",
                color:
                  macroRegime === "RISK-ON"
                    ? "var(--growth)"
                    : macroRegime === "DEFENSIVE"
                    ? "var(--caution)"
                    : "var(--text-muted)",
                border: "1px solid var(--border)",
                borderRadius: "var(--r-xs)",
                fontSize: "var(--t-micro)",
                fontWeight: 600,
                padding: "2px 6px",
                cursor: "pointer",
              }}
            >
              <option value="RISK-ON">RISK-ON</option>
              <option value="DEFENSIVE">DEFENSIVE</option>
              <option value="UNKNOWN">UNKNOWN</option>
            </select>
          </div>

          {/* Kill Switch Indicator */}
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)" }}>
            <span style={{ color: "var(--text-muted)" }}>Kill Switch:</span>
            <span
              style={{
                padding: "2px 6px",
                borderRadius: "var(--r-xs)",
                fontSize: "var(--t-micro)",
                fontWeight: 700,
                background: killSwitchActive ? "rgba(16, 185, 129, 0.15)" : "rgba(239, 68, 68, 0.2)",
                color: killSwitchActive ? "var(--growth)" : "var(--decline)",
                border: `1px solid ${killSwitchActive ? "rgba(16, 185, 129, 0.3)" : "rgba(239, 68, 68, 0.4)"}`,
              }}
            >
              {killSwitchActive ? "ACTIVE (SAFE)" : "TRIPPED (HALTED)"}
            </span>
          </div>

          {/* Market Session */}
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)" }}>
            <span style={{ color: "var(--text-muted)" }}>Session:</span>
            <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{marketSession}</span>
          </div>
        </div>

        {/* Quick Action Toggles */}
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <button
            onClick={() => setShowPauseModal(true)}
            style={{
              background: isPipelinePaused ? "rgba(245, 158, 11, 0.2)" : "var(--surface-2)",
              color: isPipelinePaused ? "var(--caution)" : "var(--text-primary)",
              border: `1px solid ${isPipelinePaused ? "var(--caution)" : "var(--border)"}`,
              borderRadius: "var(--r-xs)",
              padding: "3px 8px",
              fontSize: "var(--t-micro)",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            {isPipelinePaused ? "▶ Resume Pipeline" : "⏸ Pause Pipeline"}
          </button>

          <button
            onClick={() => setShowKillSwitchModal(true)}
            style={{
              background: killSwitchActive ? "var(--surface-2)" : "rgba(239, 68, 68, 0.2)",
              color: killSwitchActive ? "var(--decline)" : "var(--growth)",
              border: `1px solid ${killSwitchActive ? "var(--decline)" : "var(--growth)"}`,
              borderRadius: "var(--r-xs)",
              padding: "3px 8px",
              fontSize: "var(--t-micro)",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            {killSwitchActive ? "🚨 Trip Kill Switch" : "🛡️ Reset Kill Switch"}
          </button>
        </div>
      </header>

      {/* Decision Guardrail Modals */}
      {showKillSwitchModal && (
        <Modal ariaLabel={killSwitchActive ? "Trip Global Kill Switch?" : "Reset Global Kill Switch?"} onClose={() => setShowKillSwitchModal(false)}>
          <div style={{ padding: "var(--s-4)", width: "min(90vw, 420px)" }}>
            <h3 style={{ margin: "0 0 var(--s-2)", color: killSwitchActive ? "var(--decline)" : "var(--growth)" }}>
              {killSwitchActive ? "Trip Global Kill Switch?" : "Reset Global Kill Switch?"}
            </h3>
            <p style={{ fontSize: "var(--t-body)", color: "var(--text-secondary)", lineHeight: 1.5 }}>
              {killSwitchActive
                ? "Tripping the kill switch will immediately halt all pending and automated order queues across all strategies."
                : "Resetting the kill switch will re-enable advisory signal routing and order validation."}
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--s-2)", marginTop: "var(--s-4)" }}>
              <button className="btn" onClick={() => setShowKillSwitchModal(false)}>Cancel</button>
              <button
                className="btn"
                onClick={handleToggleKillSwitch}
                style={{
                  background: killSwitchActive ? "var(--decline)" : "var(--growth)",
                  color: "#fff",
                  fontWeight: 600,
                }}
              >
                {killSwitchActive ? "Trip Kill Switch" : "Reset Kill Switch"}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {showPauseModal && (
        <Modal ariaLabel={isPipelinePaused ? "Resume Pipeline Cycles?" : "Pause Pipeline Cycles?"} onClose={() => setShowPauseModal(false)}>
          <div style={{ padding: "var(--s-4)", width: "min(90vw, 420px)" }}>
            <h3 style={{ margin: "0 0 var(--s-2)" }}>
              {isPipelinePaused ? "Resume Pipeline Cycles?" : "Pause Pipeline Cycles?"}
            </h3>
            <p style={{ fontSize: "var(--t-body)", color: "var(--text-secondary)", lineHeight: 1.5 }}>
              {isPipelinePaused
                ? "Resuming will restore automated cycle scheduling and market data polling."
                : "Pausing will temporarily halt upcoming execution refresh loops until resumed."}
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--s-2)", marginTop: "var(--s-4)" }}>
              <button className="btn" onClick={() => setShowPauseModal(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleTogglePausePipeline}>
                {isPipelinePaused ? "Resume Pipeline" : "Pause Pipeline"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
