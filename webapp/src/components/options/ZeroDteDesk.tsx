import React, { useState } from "react";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { useMutation } from "../../hooks/useMutation";
import { theme, alpha } from "../../theme";
import type {
  ZeroDteSignal,
  ZeroDteSignalResponse,
  ZeroDteExecutionResult,
} from "../../api/types";

interface ZeroDteDeskProps {
  initialSymbol?: string;
  onTradeExecuted?: (result: ZeroDteExecutionResult) => void;
  onSelectTicker?: (symbol: string) => void;
  onClose?: () => void;
}

export const ZeroDteDesk: React.FC<ZeroDteDeskProps> = ({
  initialSymbol,
  onTradeExecuted,
  onSelectTicker,
  onClose,
}) => {
  const [selectedSymbol, setSelectedSymbol] = useState<string>(initialSymbol || "SPY");
  const [contractsCount, setContractsCount] = useState<number>(5);
  const [isExecuting, setIsExecuting] = useState(false);
  const [statusMessage, setStatusMessage] = useState<{ text: string; type: "success" | "error" } | null>(null);
  // zero_dte_engine is an UNGATEABLE_DATA_GAP (see CLAUDE.md's "Options desk
  // ML/safety gates and findings" bullet) -- the backend blocks every request by
  // default and only proceeds when override_deployability_gate: true is set
  // explicitly. Tracks which symbol's first (unblocked) attempt just came back
  // blocked, so it can offer a distinct, deliberate override action.
  const [blockedSymbol, setBlockedSymbol] = useState<string | null>(null);

  const query = useApi<ZeroDteSignalResponse>(
    () => api.getZeroDteSignals(selectedSymbol),
    [selectedSymbol]
  );

  const executeMutation = useMutation((signal: ZeroDteSignal, override: boolean) => {
    const contract = signal.recommended_contract;
    return api.executeZeroDteTrade(
      {
        symbol: signal.symbol,
        option_type: contract?.option_type || (signal.momentum_direction === "BEARISH_BREAKDOWN" ? "PUT" : "CALL"),
        strike: contract?.strike || signal.spot_price,
        contracts: contractsCount,
        entry_price: contract?.mid || 2.0,
        profit_target_pct: 0.75,
        stop_loss_pct: 0.30,
        hard_exit_time: "15:45 ET",
      },
      override
    );
  });

  const signals: ZeroDteSignal[] = query.data?.signals || [];

  const activeSignal =
    signals.find((s) => s.symbol.toUpperCase() === selectedSymbol.toUpperCase()) ||
    signals[0];

  const handleExecuteTrade = async (signal: ZeroDteSignal, override = false) => {
    setIsExecuting(true);
    setStatusMessage(null);
    try {
      const res = await executeMutation.run(signal, override);
      if (res && res.ok) {
        setBlockedSymbol(null);
        setStatusMessage({
          text: res.message || `Successfully executed ${res.contracts}x ${res.symbol} ${res.strike} ${res.option_type} @ $${res.fill_price.toFixed(2)}.`,
          type: "success",
        });
        if (onTradeExecuted) {
          onTradeExecuted(res);
        }
      } else if (res && res.blocked) {
        setBlockedSymbol(signal.symbol);
        setStatusMessage({ text: res.message, type: "error" });
      } else {
        setBlockedSymbol(null);
        setStatusMessage({
          text: executeMutation.error || `Failed to execute 0DTE breakout trade on ${signal.symbol}.`,
          type: "error",
        });
      }
    } finally {
      setIsExecuting(false);
    }
  };

  const handleConfirmOverride = (signal: ZeroDteSignal) => {
    const reason = statusMessage?.text || "This strategy is blocked by a deployability gate.";
    const confirmed = window.confirm(
      `⚠️ Deployability gate override\n\n${reason}\n\nThis places a PAPER (simulated) trade on ${signal.symbol} only -- no real capital is at risk. Override and execute anyway?`
    );
    if (confirmed) {
      handleExecuteTrade(signal, true);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, color: theme.textPrimary }}>
      {/* Header & Desk Controls */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: 12,
          padding: "16px",
          background: theme.surface,
          borderRadius: 12,
          border: `1px solid ${theme.border}`,
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: "1.3rem", fontWeight: 700 }}>⚡ 0DTE Intraday Momentum & Breakout Desk</span>
            <span
              style={{
                fontSize: "0.75rem",
                padding: "2px 8px",
                borderRadius: 10,
                background: alpha(theme.accent, "25"),
                color: theme.accent,
                fontWeight: 600,
              }}
            >
              Phase 16
            </span>
          </div>
          <div style={{ fontSize: "0.85rem", color: theme.textSecondary, marginTop: 4 }}>
            15-Minute Opening Range Breakout (ORB) gated by TTM Volatility Squeeze with +75% Profit Target / -30% Stop Loss & 15:45 ET Hard Exit.
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button
            onClick={() => query.reload()}
            style={{
              padding: "6px 12px",
              background: theme.surface2,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 8,
              fontSize: "0.85rem",
              cursor: "pointer",
            }}
          >
            ↻ Refresh
          </button>
          {onClose && (
            <button
              onClick={onClose}
              style={{
                padding: "6px 12px",
                background: theme.surface2,
                border: `1px solid ${theme.border}`,
                color: theme.textSecondary,
                borderRadius: 8,
                fontSize: "0.85rem",
                cursor: "pointer",
              }}
            >
              ✕ Close
            </button>
          )}
        </div>
      </div>

      {/* Status Message */}
      {statusMessage && (
        <div
          style={{
            padding: "12px 16px",
            borderRadius: 8,
            background: statusMessage.type === "success" ? alpha(theme.growth, "20") : alpha(theme.decline, "20"),
            border: `1px solid ${statusMessage.type === "success" ? theme.growth : theme.decline}`,
            color: statusMessage.type === "success" ? theme.growth : theme.decline,
            fontSize: "0.9rem",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>{statusMessage.text}</span>
          <button
            onClick={() => {
              setStatusMessage(null);
              // Dismissing the disclosed reason retires the override affordance too --
              // re-clicking "Trade" re-surfaces the honest reason before offering to
              // override again, so the override is never silent.
              setBlockedSymbol(null);
            }}
            style={{ background: "transparent", border: "none", color: "inherit", cursor: "pointer", fontWeight: 700 }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Symbol Pill Selector */}
      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          padding: "10px 16px",
          background: theme.surface2,
          borderRadius: 8,
          border: `1px solid ${theme.border}`,
          overflowX: "auto",
        }}
      >
        <span style={{ fontSize: "0.85rem", color: theme.textSecondary, fontWeight: 600, whiteSpace: "nowrap" }}>
          Active Symbols:
        </span>
        {signals.map((sig) => {
          const isSelected = activeSignal?.symbol === sig.symbol;
          const isBull = sig.momentum_direction === "BULLISH_BREAKOUT";
          const isBear = sig.momentum_direction === "BEARISH_BREAKDOWN";
          return (
            <button
              key={sig.symbol}
              onClick={() => {
                setSelectedSymbol(sig.symbol);
                if (onSelectTicker) onSelectTicker(sig.symbol);
              }}
              style={{
                padding: "6px 14px",
                borderRadius: 20,
                border: `1px solid ${isSelected ? theme.accent : theme.border}`,
                background: isSelected ? theme.accent : theme.surface,
                color: isSelected ? "#000" : theme.textPrimary,
                fontWeight: 600,
                fontSize: "0.85rem",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 6,
                whiteSpace: "nowrap",
              }}
            >
              <span>{sig.symbol}</span>
              <span
                style={{
                  fontSize: "0.7rem",
                  padding: "1px 6px",
                  borderRadius: 6,
                  background: isSelected
                    ? "rgba(0,0,0,0.2)"
                    : isBull
                    ? alpha(theme.growth, "25")
                    : isBear
                    ? alpha(theme.decline, "25")
                    : theme.surface3,
                  color: isSelected
                    ? "#000"
                    : isBull
                    ? theme.growth
                    : isBear
                    ? theme.decline
                    : theme.textSecondary,
                  fontWeight: 700,
                }}
              >
                {isBull ? "CALL" : isBear ? "PUT" : "WAIT"}
              </span>
            </button>
          );
        })}
      </div>

      {query.loading && !query.data && (
        <div style={{ padding: 40, textAlign: "center", color: theme.textSecondary }}>
          Scanning 15-minute Opening Range Breakouts & TTM Squeeze states...
        </div>
      )}

      {activeSignal && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16 }}>
          {/* Panel 1: 15-Min Opening Range Breakout (ORB) Box */}
          <div
            style={{
              background: theme.surface,
              borderRadius: 12,
              border: `1px solid ${theme.border}`,
              padding: 16,
              display: "flex",
              flexDirection: "column",
              gap: 12,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontSize: "1.1rem", fontWeight: 700 }}>{activeSignal.symbol}</span>
                <span style={{ fontSize: "0.85rem", color: theme.textSecondary }}>15-Min ORB Levels</span>
              </div>
              <span style={{ fontSize: "1.2rem", fontWeight: 700, color: theme.accent }}>
                ${activeSignal.spot_price.toFixed(2)}
              </span>
            </div>

            {/* ORB Box Visual Container */}
            <div
              style={{
                background: theme.surface2,
                borderRadius: 8,
                border: `1px solid ${theme.borderStrong}`,
                padding: 16,
                display: "flex",
                flexDirection: "column",
                gap: 12,
                position: "relative",
              }}
            >
              {/* ORB High Level */}
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  paddingBottom: 8,
                  borderBottom: `2px dashed ${theme.growth}`,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: "0.8rem", color: theme.growth, fontWeight: 700 }}>HIGH₁₅</span>
                  <span style={{ fontSize: "0.75rem", color: theme.textSecondary }}>(Breakout Trigger)</span>
                </div>
                <span style={{ fontSize: "1.1rem", fontWeight: 700, color: theme.growth }}>
                  {activeSignal.opening_range_high != null ? `$${activeSignal.opening_range_high.toFixed(2)}` : "—"}
                </span>
              </div>

              {/* Range Stats & Current Position */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "4px 0" }}>
                <div>
                  <span style={{ fontSize: "0.75rem", color: theme.textSecondary }}>Range Width: </span>
                  <span style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                    {activeSignal.opening_range_high != null && activeSignal.opening_range_low != null
                      ? `$${(activeSignal.opening_range_high - activeSignal.opening_range_low).toFixed(2)} (${
                          activeSignal.opening_range_width_pct != null
                            ? (activeSignal.opening_range_width_pct * 100).toFixed(2)
                            : "—"
                        }%)`
                      : "—"}
                  </span>
                </div>
                <div>
                  <span
                    style={{
                      fontSize: "0.8rem",
                      fontWeight: 700,
                      color:
                        activeSignal.opening_range_high == null || activeSignal.opening_range_low == null
                          ? theme.textSecondary
                          : activeSignal.spot_price >= activeSignal.opening_range_high
                          ? theme.growth
                          : activeSignal.spot_price <= activeSignal.opening_range_low
                          ? theme.decline
                          : theme.textSecondary,
                    }}
                  >
                    {activeSignal.opening_range_high == null || activeSignal.opening_range_low == null
                      ? "No 15m opening-range data available"
                      : activeSignal.spot_price >= activeSignal.opening_range_high
                      ? "▲ ABOVE RANGE (Bullish Thrust)"
                      : activeSignal.spot_price <= activeSignal.opening_range_low
                      ? "▼ BELOW RANGE (Bearish Thrust)"
                      : "■ INSIDE 15M RANGE"}
                  </span>
                </div>
              </div>

              {/* ORB Low Level */}
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  paddingTop: 8,
                  borderTop: `2px dashed ${theme.decline}`,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: "0.8rem", color: theme.decline, fontWeight: 700 }}>LOW₁₅</span>
                  <span style={{ fontSize: "0.75rem", color: theme.textSecondary }}>(Breakdown Trigger)</span>
                </div>
                <span style={{ fontSize: "1.1rem", fontWeight: 700, color: theme.decline }}>
                  {activeSignal.opening_range_low != null ? `$${activeSignal.opening_range_low.toFixed(2)}` : "—"}
                </span>
              </div>
            </div>

            {/* Trigger Reason */}
            {activeSignal.trigger_reason && (
              <div style={{ fontSize: "0.8rem", color: theme.textSecondary, background: theme.surface2, padding: "8px 12px", borderRadius: 6 }}>
                🎯 <b>Setup:</b> {activeSignal.trigger_reason}
              </div>
            )}
          </div>

          {/* Panel 2: TTM Squeeze & Momentum State */}
          <div
            style={{
              background: theme.surface,
              borderRadius: 12,
              border: `1px solid ${theme.border}`,
              padding: 16,
              display: "flex",
              flexDirection: "column",
              gap: 12,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: "0.9rem", fontWeight: 700, color: theme.textSecondary }}>
                VOLATILITY SQUEEZE & MOMENTUM GATE
              </span>
              <span style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
                Timestamp: {activeSignal.timestamp}
              </span>
            </div>

            {/* Squeeze Indicator Light Box */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 14,
                padding: "12px 16px",
                background: theme.surface2,
                borderRadius: 8,
                border: `1px solid ${theme.border}`,
              }}
            >
              {/* Indicator Light */}
              <div
                style={{
                  width: 20,
                  height: 20,
                  borderRadius: "50%",
                  background: activeSignal.ttm_squeeze_active ? theme.decline : theme.growth,
                  boxShadow: activeSignal.ttm_squeeze_active
                    ? `0 0 12px ${theme.decline}`
                    : `0 0 12px ${theme.growth}`,
                  flexShrink: 0,
                }}
              />
              <div style={{ display: "flex", flexDirection: "column" }}>
                <span style={{ fontWeight: 700, fontSize: "0.95rem" }}>
                  {activeSignal.ttm_squeeze_active
                    ? "TTM Squeeze Active (Red Light — Compression)"
                    : "TTM Squeeze Released (Green Light — Fired / Expansion)"}
                </span>
                <span style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
                  {activeSignal.ttm_squeeze_active
                    ? `Bollinger Bands inside Keltner Channel${
                        activeSignal.ttm_squeeze_bars != null ? ` for ${activeSignal.ttm_squeeze_bars} bars` : ""
                      }. Energy building.`
                    : `Bollinger Bands expanding outside Keltner Channel. Momentum release in progress.`}
                </span>
              </div>
            </div>

            {/* Momentum Strength & Relative Volume Grid */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 8 }}>
              <div style={{ background: theme.surface2, padding: 10, borderRadius: 6 }}>
                <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>Momentum Direction</div>
                <div
                  style={{
                    fontSize: "0.95rem",
                    fontWeight: 700,
                    color:
                      activeSignal.momentum_direction === "BULLISH_BREAKOUT"
                        ? theme.growth
                        : activeSignal.momentum_direction === "BEARISH_BREAKDOWN"
                        ? theme.decline
                        : theme.textSecondary,
                  }}
                >
                  {activeSignal.momentum_direction.replace("_", " ")}
                </div>
              </div>
              <div style={{ background: theme.surface2, padding: 10, borderRadius: 6 }}>
                <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>15M Relative Volume</div>
                <div
                  style={{
                    fontSize: "0.95rem",
                    fontWeight: 700,
                    color:
                      activeSignal.relative_volume_15m != null && activeSignal.relative_volume_15m >= 1.5
                        ? theme.growth
                        : theme.textPrimary,
                  }}
                >
                  {activeSignal.relative_volume_15m != null ? `${activeSignal.relative_volume_15m.toFixed(2)}x Vol Thrust` : "—"}
                </div>
              </div>
            </div>

            {/* Risk Management Guardrails Card */}
            <div
              style={{
                background: theme.surface2,
                borderRadius: 8,
                padding: "10px 12px",
                display: "flex",
                flexDirection: "column",
                gap: 6,
                fontSize: "0.8rem",
              }}
            >
              <div style={{ fontWeight: 700, color: theme.textSecondary }}>0DTE FAST RISK CONTROLS:</div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>🎯 Profit Target: <b style={{ color: theme.growth }}>+75% Gain</b></span>
                <span>🛑 Stop Loss: <b style={{ color: theme.decline }}>-30% Loss</b></span>
                <span>⏰ Hard Exit: <b style={{ color: theme.accent }}>15:45 ET Auto-Close</b></span>
              </div>
            </div>
          </div>

          {/* Panel 3: Recommended 0DTE Contract & 1-Click Execution */}
          {activeSignal.recommended_contract && (
            <div
              style={{
                gridColumn: "1 / -1",
                background: theme.surface,
                borderRadius: 12,
                border: `1px solid ${theme.border}`,
                padding: 16,
                display: "flex",
                flexDirection: "column",
                gap: 12,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "1rem", fontWeight: 700 }}>
                  Recommended 0DTE Contract Execution
                </span>
                <span
                  style={{
                    fontSize: "0.75rem",
                    padding: "3px 8px",
                    borderRadius: 6,
                    background: alpha(theme.accent, "20"),
                    color: theme.accent,
                    fontWeight: 700,
                  }}
                >
                  Exp: {activeSignal.recommended_contract.expiration} (0 DTE)
                </span>
              </div>

              {/* Contract Stats Grid */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
                  gap: 8,
                }}
              >
                <div style={{ background: theme.surface2, padding: 10, borderRadius: 6 }}>
                  <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>Contract</div>
                  <div style={{ fontSize: "1rem", fontWeight: 700, color: theme.accent }}>
                    {activeSignal.symbol} ${activeSignal.recommended_contract.strike.toFixed(1)} {activeSignal.recommended_contract.option_type}
                  </div>
                </div>
                <div style={{ background: theme.surface2, padding: 10, borderRadius: 6 }}>
                  <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>Mid / Bid-Ask</div>
                  <div style={{ fontSize: "1rem", fontWeight: 600 }}>
                    ${activeSignal.recommended_contract.mid.toFixed(2)}{" "}
                    <span style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
                      (${activeSignal.recommended_contract.bid.toFixed(2)} - ${activeSignal.recommended_contract.ask.toFixed(2)})
                    </span>
                  </div>
                </div>
                <div style={{ background: theme.surface2, padding: 10, borderRadius: 6 }}>
                  <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>Delta (Δ) / Gamma (Γ)</div>
                  <div style={{ fontSize: "1rem", fontWeight: 600 }}>
                    {activeSignal.recommended_contract.delta.toFixed(2)} Δ / {activeSignal.recommended_contract.gamma != null ? `${activeSignal.recommended_contract.gamma.toFixed(3)} Γ` : "— Γ"}
                  </div>
                </div>
                <div style={{ background: theme.surface2, padding: 10, borderRadius: 6 }}>
                  <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>+75% Profit Target</div>
                  <div style={{ fontSize: "1rem", fontWeight: 700, color: theme.growth }}>
                    ${activeSignal.recommended_contract.target_price.toFixed(2)}
                  </div>
                </div>
                <div style={{ background: theme.surface2, padding: 10, borderRadius: 6 }}>
                  <div style={{ fontSize: "0.7rem", color: theme.textSecondary }}>-30% Stop Loss</div>
                  <div style={{ fontSize: "1rem", fontWeight: 700, color: theme.decline }}>
                    ${activeSignal.recommended_contract.stop_loss_price.toFixed(2)}
                  </div>
                </div>
              </div>

              {/* Order Submission Bar */}
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  flexWrap: "wrap",
                  gap: 12,
                  paddingTop: 8,
                  borderTop: `1px solid ${theme.border}`,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <span style={{ fontSize: "0.85rem", color: theme.textSecondary }}>Position Sizing:</span>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    {[1, 5, 10, 20].map((c) => (
                      <button
                        key={c}
                        onClick={() => setContractsCount(c)}
                        style={{
                          padding: "4px 10px",
                          borderRadius: 6,
                          border: `1px solid ${contractsCount === c ? theme.accent : theme.border}`,
                          background: contractsCount === c ? theme.accent : theme.surface2,
                          color: contractsCount === c ? "#000" : theme.textPrimary,
                          fontSize: "0.8rem",
                          fontWeight: 600,
                          cursor: "pointer",
                        }}
                      >
                        {c}x
                      </button>
                    ))}
                  </div>
                  <span style={{ fontSize: "0.85rem", color: theme.textSecondary }}>
                    Total Premium: <b>${(contractsCount * activeSignal.recommended_contract.mid * 100).toFixed(2)}</b>
                  </span>
                </div>

                {blockedSymbol === activeSignal.symbol ? (
                  <button
                    onClick={() => handleConfirmOverride(activeSignal)}
                    disabled={isExecuting}
                    title="This strategy has an unmeasurable deployability gap. Overriding places a paper (simulated) trade only."
                    style={{
                      background: theme.caution,
                      color: "#000",
                      border: "none",
                      borderRadius: 8,
                      padding: "10px 24px",
                      fontWeight: 700,
                      fontSize: "0.95rem",
                      cursor: isExecuting ? "not-allowed" : "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      opacity: isExecuting ? 0.7 : 1,
                      transition: "all 0.15s ease",
                    }}
                  >
                    {isExecuting ? "⚡ Executing Trade..." : "⚠️ Override & Execute"}
                  </button>
                ) : (
                  <button
                    onClick={() => handleExecuteTrade(activeSignal)}
                    disabled={isExecuting}
                    style={{
                      background:
                        activeSignal.suggested_action === "BUY_CALL"
                          ? theme.growth
                          : activeSignal.suggested_action === "BUY_PUT"
                          ? theme.decline
                          : theme.accent,
                      color: "#000",
                      border: "none",
                      borderRadius: 8,
                      padding: "10px 24px",
                      fontWeight: 700,
                      fontSize: "0.95rem",
                      cursor: isExecuting ? "not-allowed" : "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      opacity: isExecuting ? 0.7 : 1,
                      transition: "all 0.15s ease",
                    }}
                  >
                    {isExecuting
                      ? "⚡ Executing Trade..."
                      : `⚡ Trade 0DTE Breakout (${contractsCount}x ${activeSignal.symbol} ${activeSignal.recommended_contract.option_type})`}
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
