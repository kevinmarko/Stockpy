import React, { useState, useEffect } from "react";
import { api } from "../../api/client";
import { theme } from "../../theme";
import type { LobQueueSimulationResponse } from "../../api/types";

interface LobDepthViewProps {
  initialSymbol?: string;
  spotPrice?: number;
  onClose?: () => void;
}

export const LobDepthView: React.FC<LobDepthViewProps> = ({
  initialSymbol = "SPY",
  spotPrice: _spotPrice,
  onClose,
}) => {
  const [symbol, setSymbol] = useState<string>(initialSymbol);
  const [priceLevel, setPriceLevel] = useState<number>(3.15);
  const [orderSize, setOrderSize] = useState<number>(5);
  const [depthAhead, setDepthAhead] = useState<number>(28);

  const [simResult, setSimResult] = useState<LobQueueSimulationResponse | null>(null);
  const [simulating, setSimulating] = useState<boolean>(false);
  const [simError, setSimError] = useState<string | null>(null);

  const activeSymbols = ["SPY", "QQQ", "TSLA", "NVDA", "AAPL", "MSFT"];

  const handleSimulate = async () => {
    setSimulating(true);
    setSimError(null);
    try {
      const res = await api.simulateLobQueue({
        symbol,
        price_level: priceLevel,
        order_size: orderSize,
        depth_ahead: depthAhead,
      });
      setSimResult(res);
    } catch (err: any) {
      setSimError(err?.message || "Failed to simulate LOB Queue");
    } finally {
      setSimulating(false);
    }
  };

  // Run initial simulation on mount or symbol change
  useEffect(() => {
    handleSimulate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  const fillPct = simResult ? Math.round(simResult.fill_probability * 100) : 0;
  const adverseMovePct = simResult ? Math.round(simResult.prob_adverse_move_before_fill * 100) : 0;

  const queueBarTotal = depthAhead + orderSize;
  const depthAheadPct = queueBarTotal > 0 ? (depthAhead / queueBarTotal) * 100 : 0;

  const percentileRows: { label: string; value: number | null }[] = simResult
    ? [
        { label: "P10", value: simResult.queue_progression_percentiles.p10 },
        { label: "P25", value: simResult.queue_progression_percentiles.p25 },
        { label: "P50", value: simResult.queue_progression_percentiles.p50 },
        { label: "P75", value: simResult.queue_progression_percentiles.p75 },
        { label: "P90", value: simResult.queue_progression_percentiles.p90 },
        { label: "P95", value: simResult.queue_progression_percentiles.p95 },
      ]
    : [];

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 16,
        color: theme.textPrimary,
      }}
    >
      {/* Header & Controls */}
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
            <span style={{ fontSize: "1.3rem", fontWeight: 700 }}>
              🪜 Level-3 Limit Order Book (LOB) Depth & Queue Position Simulator
            </span>
            <span
              style={{
                fontSize: "0.75rem",
                padding: "2px 8px",
                borderRadius: 10,
                background: `${theme.growth}25`,
                color: theme.growth,
                fontWeight: 600,
              }}
            >
              Phase 21
            </span>
          </div>
          <div
            style={{
              fontSize: "0.85rem",
              color: theme.textSecondary,
              marginTop: 4,
            }}
          >
            Cont-Stoikov-Talreja (2010) Markovian queue-fill simulator — resting queue fill probability, expected wait time, and time-to-fill percentile distribution.
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            onClick={handleSimulate}
            disabled={simulating}
            style={{
              padding: "6px 12px",
              background: theme.surface2,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 8,
              fontSize: "0.85rem",
              cursor: simulating ? "not-allowed" : "pointer",
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
        <span
          style={{
            fontSize: "0.85rem",
            color: theme.textSecondary,
            fontWeight: 600,
            whiteSpace: "nowrap",
          }}
        >
          Active Tickers:
        </span>
        {activeSymbols.map((sym) => {
          const isSelected = symbol.toUpperCase() === sym.toUpperCase();
          return (
            <button
              key={sym}
              onClick={() => setSymbol(sym)}
              style={{
                padding: "6px 14px",
                borderRadius: 20,
                border: `1px solid ${isSelected ? theme.accent : theme.border}`,
                background: isSelected ? theme.accent : theme.surface,
                color: isSelected ? "#000" : theme.textPrimary,
                fontWeight: 600,
                fontSize: "0.85rem",
                cursor: "pointer",
                transition: "all 0.15s ease",
              }}
            >
              {sym}
            </button>
          );
        })}
      </div>

      {/* Interactive Order Parameter Controls */}
      <div
        style={{
          background: theme.surface,
          borderRadius: 12,
          border: `1px solid ${theme.border}`,
          padding: 16,
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 12,
          alignItems: "flex-end",
        }}
      >
        <div>
          <label style={{ display: "block", fontSize: "0.75rem", color: theme.textSecondary, marginBottom: 4 }}>
            Price Level ($)
          </label>
          <input
            type="number"
            value={priceLevel}
            onChange={(e) => setPriceLevel(Number(e.target.value))}
            step={0.05}
            style={{
              width: "100%",
              padding: "7px 10px",
              background: theme.base,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 6,
              fontSize: "0.85rem",
              fontWeight: 600,
            }}
          />
        </div>

        <div>
          <label style={{ display: "block", fontSize: "0.75rem", color: theme.textSecondary, marginBottom: 4 }}>
            Order Size (contracts)
          </label>
          <input
            type="number"
            value={orderSize}
            onChange={(e) => setOrderSize(Math.max(1, Number(e.target.value)))}
            min={1}
            style={{
              width: "100%",
              padding: "7px 10px",
              background: theme.base,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 6,
              fontSize: "0.85rem",
              fontWeight: 600,
            }}
          />
        </div>

        <div>
          <label style={{ display: "block", fontSize: "0.75rem", color: theme.textSecondary, marginBottom: 4 }}>
            Depth Ahead (contracts resting ahead)
          </label>
          <input
            type="number"
            value={depthAhead}
            onChange={(e) => setDepthAhead(Math.max(0, Number(e.target.value)))}
            min={0}
            style={{
              width: "100%",
              padding: "7px 10px",
              background: theme.base,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 6,
              fontSize: "0.85rem",
              fontWeight: 600,
            }}
          />
        </div>

        <button
          onClick={handleSimulate}
          disabled={simulating}
          style={{
            padding: "8px 16px",
            background: theme.accent,
            color: "#000",
            border: "none",
            borderRadius: 6,
            fontWeight: 700,
            fontSize: "0.85rem",
            cursor: simulating ? "not-allowed" : "pointer",
            height: 35,
          }}
        >
          {simulating ? "Simulating..." : "⚡ Simulate Fill"}
        </button>
      </div>

      {simError && (
        <div style={{ padding: 12, background: `${theme.decline}20`, color: theme.decline, borderRadius: 8 }}>
          {simError}
        </div>
      )}

      {simResult && (
        <>
          {/* Fill Probability & Wait Time KPIs */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: 12,
            }}
          >
            {/* KPI 1: Fill Probability */}
            <div
              style={{
                background: theme.surface,
                borderRadius: 10,
                padding: "14px 16px",
                border: `1px solid ${theme.border}`,
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}
            >
              <span style={{ fontSize: "0.75rem", color: theme.textSecondary, fontWeight: 600 }}>
                Fill Probability (within {simResult.time_horizon_sec}s)
              </span>
              <div style={{ fontSize: "1.6rem", fontWeight: 800, color: fillPct >= 70 ? theme.growth : theme.caution }}>
                {fillPct}%
              </div>
              <div style={{ height: 6, width: "100%", background: theme.surface2, borderRadius: 3, overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${fillPct}%`, background: fillPct >= 70 ? theme.growth : theme.caution }} />
              </div>
            </div>

            {/* KPI 2: Expected / Median Wait Time */}
            <div
              style={{
                background: theme.surface,
                borderRadius: 10,
                padding: "14px 16px",
                border: `1px solid ${theme.border}`,
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              <span style={{ fontSize: "0.75rem", color: theme.textSecondary, fontWeight: 600 }}>
                Expected Wait Time
              </span>
              <div style={{ fontSize: "1.6rem", fontWeight: 800, color: theme.growth }}>
                {simResult.expected_wait_time_sec != null ? `~${simResult.expected_wait_time_sec.toFixed(1)}s` : "—"}
              </div>
              <span style={{ fontSize: "0.75rem", color: theme.textMuted }}>
                Median: {simResult.median_fill_time_sec != null ? `${simResult.median_fill_time_sec.toFixed(1)}s` : "—"}
              </span>
            </div>

            {/* KPI 3: Queue Depletion Velocity & Adverse Move Risk */}
            <div
              style={{
                background: theme.surface,
                borderRadius: 10,
                padding: "14px 16px",
                border: `1px solid ${theme.border}`,
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              <span style={{ fontSize: "0.75rem", color: theme.textSecondary, fontWeight: 600 }}>
                Queue Depletion Velocity
              </span>
              <div style={{ fontSize: "1.4rem", fontWeight: 800, color: theme.textPrimary }}>
                {simResult.queue_depletion_velocity.toFixed(3)} contracts/s
              </div>
              <span style={{ fontSize: "0.75rem", color: theme.textMuted }}>
                P(Adverse Move Before Fill): {adverseMovePct}%
              </span>
            </div>

            {/* KPI 4: Expected Fill Ratio */}
            <div
              style={{
                background: theme.surface,
                borderRadius: 10,
                padding: "14px 16px",
                border: `1px solid ${theme.border}`,
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              <span style={{ fontSize: "0.75rem", color: theme.textSecondary, fontWeight: 600 }}>
                Expected Fill Ratio
              </span>
              <div style={{ fontSize: "1.4rem", fontWeight: 800, color: theme.textPrimary }}>
                {(simResult.expected_fill_ratio * 100).toFixed(1)}%
              </div>
              <span style={{ fontSize: "0.75rem", color: theme.textMuted }}>
                Unconditional Fill Time: {simResult.unconditional_fill_time_sec.toFixed(1)}s
              </span>
            </div>
          </div>

          {/* Reason Alert (only present when the simulation degraded / fell back) */}
          {simResult.reason && (
            <div
              style={{
                padding: "10px 14px",
                background: theme.surface2,
                borderRadius: 8,
                border: `1px solid ${theme.border}`,
                fontSize: "0.85rem",
                color: theme.textSecondary,
              }}
            >
              ℹ️ {simResult.reason}
            </div>
          )}

          {/* Queue Position Visual + Time-to-Fill Percentile Distribution */}
          <div
            style={{
              background: theme.surface,
              borderRadius: 12,
              border: `1px solid ${theme.border}`,
              padding: 20,
              display: "flex",
              flexDirection: "column",
              gap: 16,
            }}
          >
            <div>
              <span style={{ fontSize: "1.05rem", fontWeight: 700 }}>
                {symbol} @ ${priceLevel.toFixed(2)} — Queue Position
              </span>
              <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
                {simResult.depth_ahead} contracts resting ahead vs. your {simResult.order_size}-contract order.
              </div>
            </div>

            {/* Queue Position Bar */}
            <div
              style={{
                display: "flex",
                width: "100%",
                height: 32,
                borderRadius: 8,
                overflow: "hidden",
                border: `1px solid ${theme.borderStrong}`,
              }}
            >
              <div
                style={{
                  width: `${depthAheadPct}%`,
                  background: theme.surface3,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: "0.75rem",
                  color: theme.textSecondary,
                  fontWeight: 600,
                }}
              >
                {depthAhead > 0 ? `${depthAhead} ahead` : ""}
              </div>
              <div
                style={{
                  width: `${100 - depthAheadPct}%`,
                  background: theme.accent,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: "0.75rem",
                  color: "#000",
                  fontWeight: 700,
                }}
              >
                ★ You ({orderSize})
              </div>
            </div>

            {/* Time-to-Fill Percentile Table */}
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem", textAlign: "left" }}>
                <thead>
                  <tr style={{ background: theme.surface2, borderBottom: `1px solid ${theme.border}`, color: theme.textSecondary }}>
                    {percentileRows.map((row) => (
                      <th key={row.label} style={{ padding: "8px 12px", textAlign: "right" }}>
                        {row.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    {percentileRows.map((row) => (
                      <td key={row.label} style={{ padding: "8px 12px", textAlign: "right", fontWeight: 700 }}>
                        {row.value != null ? `${row.value.toFixed(1)}s` : "—"}
                      </td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>

            {/* Microstructure Math Card */}
            <div
              style={{
                fontSize: "0.75rem",
                color: theme.textMuted,
                background: theme.surface2,
                padding: "10px 14px",
                borderRadius: 6,
                lineHeight: 1.4,
              }}
            >
              📐 <b>Queue Dynamics:</b> Cont-Stoikov-Talreja (2010) Markovian queue-fill model — Poisson limit-order arrival (λ), exponential cancellation (μ), and market-order consumption (θ) govern how quickly the {depthAhead}-contract queue ahead of a resting order at ${priceLevel.toFixed(2)} depletes within the {simResult.time_horizon_sec}s horizon.
            </div>
          </div>
        </>
      )}
    </div>
  );
};
