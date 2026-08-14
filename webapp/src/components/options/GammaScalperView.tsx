import React, { useState, useEffect } from "react";
import { api } from "../../api/client";
import { useMutation } from "../../hooks/useMutation";
import { theme } from "../../theme";
import type { GammaScalpRequest, GammaScalpResponse } from "../../api/types";

interface GammaScalperViewProps {
  initialSymbol?: string;
  spotPrice?: number;
  onClose?: () => void;
}

export const GammaScalperView: React.FC<GammaScalperViewProps> = ({
  initialSymbol = "SPY",
  spotPrice = 505.20,
  onClose,
}) => {
  const [symbol, setSymbol] = useState(initialSymbol);
  const [spot, setSpot] = useState(spotPrice);
  const [optionType, setOptionType] = useState<"CALL" | "PUT" | "STRADDLE">("CALL");
  const [strike, setStrike] = useState(spotPrice);
  const [contracts, setContracts] = useState(10);
  const [deltaThreshold, setDeltaThreshold] = useState(0.15);
  const [iv, setIv] = useState(0.25);
  const [realizedVol, setRealizedVol] = useState(0.32);
  const [steps, setSteps] = useState(40);
  const [pathPreset, setPathPreset] = useState<"OSCILLATING" | "UP_TREND" | "DOWN_TREND" | "HIGH_VOL">("OSCILLATING");

  const [result, setResult] = useState<GammaScalpResponse | null>(null);

  const simulateMutation = useMutation((req: GammaScalpRequest) =>
    api.simulateGammaScalping(req)
  );

  const generatePricePath = (
    baseSpot: number,
    numSteps: number,
    vol: number,
    preset: "OSCILLATING" | "UP_TREND" | "DOWN_TREND" | "HIGH_VOL"
  ): number[] => {
    const path: number[] = [baseSpot];
    let current = baseSpot;
    const dt = 1 / 252 / 6.5;

    for (let i = 1; i < numSteps; i++) {
      let drift = 0;
      let shockScale = 1.0;

      if (preset === "UP_TREND") {
        drift = 0.002 * current;
      } else if (preset === "DOWN_TREND") {
        drift = -0.002 * current;
      } else if (preset === "HIGH_VOL") {
        shockScale = 2.2;
      }

      const cycle = Math.sin(i * 0.45) * 1.2;
      const noise = (Math.random() - 0.49) * 2;
      const dS = drift + (cycle * 0.6 + noise * 0.8) * shockScale * vol * Math.sqrt(dt) * current * 6;
      current = Math.max(10, current + dS);
      path.push(Number(current.toFixed(2)));
    }
    return path;
  };

  const handleRunSimulation = async () => {
    const generatedPath = generatePricePath(spot, steps, realizedVol, pathPreset);
    const req: GammaScalpRequest = {
      symbol: symbol.toUpperCase(),
      spot_price: spot,
      option_type: optionType,
      strike,
      contracts,
      delta_threshold: deltaThreshold,
      simulation_steps: steps,
      realized_vol: realizedVol,
      iv,
      underlying_price_path: generatedPath,
    };

    const res = await simulateMutation.run(req);
    if (res) {
      setResult(res);
    }
  };

  // Run initial simulation on mount
  useEffect(() => {
    handleRunSimulation();
  }, []);

  return (
    <div
      style={{
        background: theme.surface,
        borderRadius: 8,
        border: `1px solid ${theme.border}`,
        padding: 20,
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
            <span>⚡ Intraday Gamma Scalping &amp; Delta Neutralization Simulator</span>
            {result && (
              <span style={{ fontSize: 13, color: theme.accent, fontWeight: 600 }}>
                {result.symbol} ${spot.toFixed(2)}
              </span>
            )}
          </h2>
        </div>

        {onClose && (
          <button
            onClick={onClose}
            style={{
              padding: "5px 10px",
              background: "transparent",
              border: `1px solid ${theme.border}`,
              color: theme.textSecondary,
              borderRadius: 4,
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            ✕ Close
          </button>
        )}
      </div>

      {/* Simulator Control Parameters Grid */}
      <div
        style={{
          background: theme.base,
          borderRadius: 6,
          border: `1px solid ${theme.border}`,
          padding: 16,
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: 16,
        }}
      >
        {/* Symbol & Option Type */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label style={{ fontSize: 11, color: theme.textSecondary, fontWeight: 600 }}>Underlying Symbol &amp; Spot</label>
          <div style={{ display: "flex", gap: 6 }}>
            <input
              type="text"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              style={{
                width: 70,
                padding: "6px 8px",
                background: theme.surface,
                border: `1px solid ${theme.border}`,
                color: theme.textPrimary,
                borderRadius: 4,
                fontSize: 12,
              }}
            />
            <input
              type="number"
              value={spot}
              onChange={(e) => {
                const s = parseFloat(e.target.value) || 100;
                setSpot(s);
                setStrike(s);
              }}
              style={{
                width: 90,
                padding: "6px 8px",
                background: theme.surface,
                border: `1px solid ${theme.border}`,
                color: theme.textPrimary,
                borderRadius: 4,
                fontSize: 12,
              }}
            />
          </div>
        </div>

        {/* Option Structure */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label style={{ fontSize: 11, color: theme.textSecondary, fontWeight: 600 }}>Option Type</label>
          <div style={{ display: "flex", gap: 4 }}>
            {(["CALL", "PUT", "STRADDLE"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setOptionType(t)}
                style={{
                  flex: 1,
                  padding: "5px 4px",
                  fontSize: 11,
                  borderRadius: 4,
                  background: optionType === t ? theme.accent : theme.surface,
                  color: optionType === t ? "#000" : theme.textPrimary,
                  border: `1px solid ${optionType === t ? theme.accent : theme.border}`,
                  cursor: "pointer",
                  fontWeight: optionType === t ? 600 : 400,
                }}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* Contracts & Delta Threshold Slider */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <label style={{ fontSize: 11, color: theme.textSecondary, fontWeight: 600 }}>Contracts &amp; Delta Threshold</label>
            <span style={{ fontSize: 11, color: theme.accent, fontWeight: 700 }}>
              {contracts} cts | ±{deltaThreshold.toFixed(2)}
            </span>
          </div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              type="number"
              min="1"
              max="100"
              value={contracts}
              onChange={(e) => setContracts(parseInt(e.target.value) || 1)}
              style={{
                width: 55,
                padding: "4px 6px",
                background: theme.surface,
                border: `1px solid ${theme.border}`,
                color: theme.textPrimary,
                borderRadius: 4,
                fontSize: 11,
              }}
            />
            <input
              type="range"
              min="0.05"
              max="0.40"
              step="0.01"
              value={deltaThreshold}
              onChange={(e) => setDeltaThreshold(parseFloat(e.target.value))}
              style={{ accentColor: theme.accent, flex: 1, cursor: "pointer" }}
            />
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: theme.textMuted }}>
            <span>Tight (±0.05)</span>
            <span>Balanced (±0.15)</span>
            <span>Loose (±0.40)</span>
          </div>
        </div>

        {/* Realized Volatility vs Implied Volatility */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <label style={{ fontSize: 11, color: theme.textSecondary, fontWeight: 600 }}>Realized Vol / IV ({steps} steps)</label>
            <span style={{ fontSize: 11, color: realizedVol > iv ? theme.growth : theme.decline, fontWeight: 700 }}>
              {(realizedVol * 100).toFixed(0)}% RV vs {(iv * 100).toFixed(0)}% IV
            </span>
          </div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              type="number"
              min="5"
              max="150"
              step="1"
              value={Number((iv * 100).toFixed(0))}
              onChange={(e) => setIv((parseFloat(e.target.value) || 20) / 100)}
              style={{
                width: 55,
                padding: "4px 6px",
                background: theme.surface,
                border: `1px solid ${theme.border}`,
                color: theme.textPrimary,
                borderRadius: 4,
                fontSize: 11,
              }}
            />
            <input
              type="range"
              min="0.10"
              max="0.80"
              step="0.02"
              value={realizedVol}
              onChange={(e) => setRealizedVol(parseFloat(e.target.value))}
              style={{ accentColor: realizedVol > iv ? theme.growth : theme.decline, flex: 1, cursor: "pointer" }}
            />
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: theme.textMuted }}>
            <span>10% RV</span>
            <span>RV &gt; IV = +Edge</span>
            <span>80% RV</span>
          </div>
        </div>

        {/* Path Dynamics Preset & Steps */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <label style={{ fontSize: 11, color: theme.textSecondary, fontWeight: 600 }}>Path Dynamics &amp; Steps</label>
            <span style={{ fontSize: 11, color: theme.textSecondary }}>{steps}h horizon</span>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <select
              value={pathPreset}
              onChange={(e) => setPathPreset(e.target.value as any)}
              style={{
                flex: 1,
                padding: "5px 8px",
                background: theme.surface,
                border: `1px solid ${theme.border}`,
                color: theme.textPrimary,
                borderRadius: 4,
                fontSize: 12,
              }}
            >
              <option value="OSCILLATING">🌊 Mean-Reverting / Oscillating</option>
              <option value="HIGH_VOL">⚡ High Volatility Whip</option>
              <option value="UP_TREND">📈 Upward Trending Drift</option>
              <option value="DOWN_TREND">📉 Downward Trending Drift</option>
            </select>
            <input
              type="number"
              min="10"
              max="120"
              value={steps}
              onChange={(e) => setSteps(parseInt(e.target.value) || 40)}
              style={{
                width: 50,
                padding: "5px 6px",
                background: theme.surface,
                border: `1px solid ${theme.border}`,
                color: theme.textPrimary,
                borderRadius: 4,
                fontSize: 11,
              }}
            />
          </div>
        </div>

        {/* Simulation Execution Button */}
        <div style={{ display: "flex", alignItems: "flex-end" }}>
          <button
            onClick={handleRunSimulation}
            disabled={simulateMutation.pending}
            style={{
              width: "100%",
              padding: "8px 14px",
              background: theme.accent,
              color: "#000",
              border: "none",
              borderRadius: 4,
              fontWeight: 700,
              fontSize: 12,
              cursor: simulateMutation.pending ? "not-allowed" : "pointer",
            }}
          >
            {simulateMutation.pending ? "Simulating..." : "▶ Run Scalp Simulation"}
          </button>
        </div>
      </div>

      {/* KPI Performance Banner */}
      {result && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
          <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
            <div style={{ fontSize: 11, color: theme.textSecondary }}>Net Gamma Scalp P&amp;L</div>
            <div
              style={{
                fontSize: 20,
                fontWeight: 700,
                marginTop: 4,
                color: result.total_pnl >= 0 ? theme.growth : theme.decline,
              }}
            >
              {result.total_pnl >= 0 ? "+" : ""}${result.total_pnl.toLocaleString("en-US", { minimumFractionDigits: 2 })}
            </div>
            <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>
              {result.total_trades} rebalance hedges executed
            </div>
          </div>

          <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
            <div style={{ fontSize: 11, color: theme.textSecondary }}>Realized Gamma Rent (½Γ·ΔS²)</div>
            <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: theme.growth }}>
              +${result.gamma_rent_total.toLocaleString("en-US", { minimumFractionDigits: 2 })}
            </div>
            <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>
              Captured stock rebalancing gains
            </div>
          </div>

          <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
            <div style={{ fontSize: 11, color: theme.textSecondary }}>Option Theta Decay (Θ·Δt)</div>
            <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: theme.decline }}>
              -${result.theta_burn_total.toLocaleString("en-US", { minimumFractionDigits: 2 })}
            </div>
            <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>
              Time decay cost over horizon
            </div>
          </div>

          <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
            <div style={{ fontSize: 11, color: theme.textSecondary }}>Net Theoretical Edge</div>
            <div
              style={{
                fontSize: 20,
                fontWeight: 700,
                marginTop: 4,
                color: result.net_edge >= 0 ? theme.growth : theme.decline,
              }}
            >
              {result.net_edge >= 0 ? "+" : ""}${result.net_edge.toLocaleString("en-US", { minimumFractionDigits: 2 })}
            </div>
            <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>
              Fees: -${result.transaction_costs.toFixed(2)}
            </div>
          </div>
        </div>
      )}

      {/* Interactive PnL & Path Breakdown SVG Chart */}
      {result && result.pnl_path.length > 0 && (
        <div style={{ background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}`, padding: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>
              📈 Cumulative P&amp;L Attribution: Gamma Rent vs. Theta Burn
            </div>
            <div style={{ display: "flex", gap: 12, fontSize: 11 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 4, color: theme.growth }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", background: theme.growth }} /> Realized Gamma Rent
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 4, color: theme.decline }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", background: theme.decline }} /> Theta Decay
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 4, color: theme.accent }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", background: theme.accent }} /> Net Scalp P&amp;L
              </span>
            </div>
          </div>

          <div style={{ position: "relative", width: "100%", height: 180 }}>
            {(() => {
              const points = result.pnl_path;
              const pnlVals = points.map((p) => p.total_pnl);
              const rentVals = points.map((p) => p.gamma_rent);
              const thetaVals = points.map((p) => -p.theta_decay);
              const allVals = [...pnlVals, ...rentVals, ...thetaVals];

              const minVal = Math.min(...allVals, 0);
              const maxVal = Math.max(...allVals, 100);

              const chartW = 700;
              const chartH = 150;
              const padL = 50;
              const padR = 25;
              const padT = 15;
              const padB = 25;

              const scaleX = (step: number) => padL + (step / (points.length - 1 || 1)) * (chartW - padL - padR);
              const scaleY = (v: number) => padT + (1 - (v - minVal) / (maxVal - minVal || 1)) * (chartH - padT - padB);

              const zeroY = scaleY(0);

              const pnlPathStr = points
                .map((p, idx) => `${idx === 0 ? "M" : "L"} ${scaleX(p.step).toFixed(1)} ${scaleY(p.total_pnl).toFixed(1)}`)
                .join(" ");

              const rentPathStr = points
                .map((p, idx) => `${idx === 0 ? "M" : "L"} ${scaleX(p.step).toFixed(1)} ${scaleY(p.gamma_rent).toFixed(1)}`)
                .join(" ");

              const thetaPathStr = points
                .map((p, idx) => `${idx === 0 ? "M" : "L"} ${scaleX(p.step).toFixed(1)} ${scaleY(-p.theta_decay).toFixed(1)}`)
                .join(" ");

              return (
                <svg viewBox={`0 0 ${chartW} ${chartH}`} width="100%" height="100%" preserveAspectRatio="none">
                  {/* Zero Line */}
                  <line x1={padL} y1={zeroY} x2={chartW - padR} y2={zeroY} stroke={theme.borderStrong} strokeWidth="1.5" strokeDasharray="3 3" />

                  {/* Gamma Rent Curve */}
                  <path d={rentPathStr} fill="none" stroke={theme.growth} strokeWidth="2" strokeLinecap="round" />

                  {/* Theta Decay Curve */}
                  <path d={thetaPathStr} fill="none" stroke={theme.decline} strokeWidth="2" strokeLinecap="round" />

                  {/* Net PnL Curve */}
                  <path d={pnlPathStr} fill="none" stroke={theme.accent} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />

                  {/* Rebalance Trade Markers */}
                  {result.trades.map((t) => {
                    const cx = scaleX(t.step);
                    const cy = scaleY(t.total_pnl);
                    return (
                      <g key={t.step}>
                        <circle cx={cx} cy={cy} r="4" fill={t.side === "BUY" ? "#38bdf8" : "#f43f5e"} stroke="#0f172a" strokeWidth="1.5" />
                      </g>
                    );
                  })}
                </svg>
              );
            })()}
          </div>
        </div>
      )}

      {/* Hedge Trade Ledger Table */}
      {result && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>
            📋 Dynamic Delta Rebalancing Ledger ({result.trades.length} hedge executions)
          </div>

          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, textAlign: "left" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.border}`, color: theme.textSecondary }}>
                  <th style={{ padding: "6px 8px" }}>Time</th>
                  <th style={{ padding: "6px 8px" }}>Spot Price</th>
                  <th style={{ padding: "6px 8px" }}>Pre-Delta</th>
                  <th style={{ padding: "6px 8px" }}>Action</th>
                  <th style={{ padding: "6px 8px" }}>Shares</th>
                  <th style={{ padding: "6px 8px" }}>Cash Flow</th>
                  <th style={{ padding: "6px 8px" }}>Stock Pos</th>
                  <th style={{ padding: "6px 8px" }}>Gamma Rent</th>
                  <th style={{ padding: "6px 8px" }}>Theta Decay</th>
                  <th style={{ padding: "6px 8px" }}>Total P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {result.trades.length === 0 ? (
                  <tr>
                    <td colSpan={10} style={{ padding: 16, textAlign: "center", color: theme.textMuted }}>
                      No threshold delta breaches occurred during this path.
                    </td>
                  </tr>
                ) : (
                  result.trades.map((t) => (
                    <tr key={t.step} style={{ borderBottom: `1px solid ${theme.border}` }}>
                      <td style={{ padding: "6px 8px", color: theme.textMuted }}>{t.timestamp}</td>
                      <td style={{ padding: "6px 8px", fontWeight: 600 }}>${t.spot_price.toFixed(2)}</td>
                      <td style={{ padding: "6px 8px", color: Math.abs(t.pre_delta) >= deltaThreshold ? theme.caution : theme.textPrimary }}>
                        {t.pre_delta > 0 ? `+${t.pre_delta.toFixed(2)}` : t.pre_delta.toFixed(2)}
                      </td>
                      <td style={{ padding: "6px 8px" }}>
                        <span
                          style={{
                            padding: "2px 6px",
                            borderRadius: 4,
                            fontSize: 10,
                            fontWeight: 700,
                            background: t.side === "BUY" ? "rgba(56, 189, 248, 0.2)" : "rgba(244, 63, 94, 0.2)",
                            color: t.side === "BUY" ? "#38bdf8" : "#f43f5e",
                          }}
                        >
                          {t.side}
                        </span>
                      </td>
                      <td style={{ padding: "6px 8px" }}>{t.shares_traded}</td>
                      <td style={{ padding: "6px 8px" }}>${t.cash_flow.toLocaleString()}</td>
                      <td style={{ padding: "6px 8px" }}>{t.stock_position}</td>
                      <td style={{ padding: "6px 8px", color: theme.growth }}>+${t.gamma_rent_cumulative.toFixed(2)}</td>
                      <td style={{ padding: "6px 8px", color: theme.decline }}>-${t.theta_decay_cumulative.toFixed(2)}</td>
                      <td
                        style={{
                          padding: "6px 8px",
                          fontWeight: 700,
                          color: t.total_pnl >= 0 ? theme.growth : theme.decline,
                        }}
                      >
                        {t.total_pnl >= 0 ? "+" : ""}${t.total_pnl.toFixed(2)}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};
