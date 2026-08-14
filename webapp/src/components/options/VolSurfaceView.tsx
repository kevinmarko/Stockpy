import React, { useState } from "react";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { theme } from "../../theme";
import type { VolSurfaceResponse } from "../../api/types";

interface VolSurfaceViewProps {
  initialSymbol?: string;
  onClose?: () => void;
}

export const VolSurfaceView: React.FC<VolSurfaceViewProps> = ({ initialSymbol = "SPY", onClose }) => {
  const [symbol, setSymbol] = useState(initialSymbol);
  const [symbolInput, setSymbolInput] = useState(initialSymbol);
  const [selectedExp, setSelectedExp] = useState<string | undefined>(undefined);

  const query = useApi(() => api.getVolSurface(symbol, selectedExp), [symbol, selectedExp]);
  const volData: VolSurfaceResponse | null = query.data;

  const handleSymbolSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (symbolInput.trim()) {
      setSymbol(symbolInput.trim().toUpperCase());
      setSelectedExp(undefined);
    }
  };

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
      {/* Header with symbol input and expiries */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
            <span>🌊 Volatility Surface &amp; Skew Analytics</span>
            {volData && (
              <span style={{ fontSize: 13, color: theme.accent, fontWeight: 600 }}>
                {volData.symbol} ${volData.spot_price.toFixed(2)}
              </span>
            )}
          </h2>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <form onSubmit={handleSymbolSubmit} style={{ display: "flex", gap: 6 }}>
            <input
              type="text"
              value={symbolInput}
              onChange={(e) => setSymbolInput(e.target.value)}
              placeholder="Ticker..."
              style={{
                width: 80,
                padding: "5px 8px",
                background: theme.base,
                border: `1px solid ${theme.border}`,
                color: theme.textPrimary,
                borderRadius: 4,
                fontSize: 12,
                textTransform: "uppercase",
              }}
            />
            <button
              type="submit"
              style={{
                padding: "5px 10px",
                background: theme.surface2,
                border: `1px solid ${theme.border}`,
                color: theme.textPrimary,
                borderRadius: 4,
                fontSize: 12,
                cursor: "pointer",
              }}
            >
              Go
            </button>
          </form>

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
      </div>

      {query.loading && !volData && (
        <div style={{ padding: 24, textAlign: "center", color: theme.textSecondary }}>
          Interpolating Volatility Surface &amp; Term Structure...
        </div>
      )}

      {query.error && (
        <div style={{ padding: 12, background: "rgba(239, 68, 68, 0.15)", color: theme.decline, borderRadius: 6, fontSize: 13 }}>
          Failed to load vol surface: {query.error}
        </div>
      )}

      {volData && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Expiration Selector Tabs */}
          <div style={{ display: "flex", gap: 8, overflowX: "auto", paddingBottom: 4 }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: theme.textSecondary, alignSelf: "center", marginRight: 4 }}>
              Expirations:
            </span>
            {volData.expirations.map((exp) => {
              const isSelected = (volData.selected_expiration || volData.expirations[0]) === exp;
              const termPt = volData.term_structure.find((t) => t.expiration === exp);
              return (
                <button
                  key={exp}
                  onClick={() => setSelectedExp(exp)}
                  style={{
                    padding: "4px 10px",
                    background: isSelected ? theme.accent : theme.base,
                    color: isSelected ? "#000" : theme.textPrimary,
                    border: `1px solid ${isSelected ? theme.accent : theme.border}`,
                    borderRadius: 4,
                    fontSize: 11,
                    fontWeight: isSelected ? 600 : 400,
                    cursor: "pointer",
                    whiteSpace: "nowrap",
                  }}
                >
                  {exp} {termPt ? `(${termPt.dte}d - ${(termPt.atm_iv * 100).toFixed(1)}%)` : ""}
                </button>
              );
            })}
          </div>

          {/* 3 Metric Analytics Cards: 25Δ Skew, VRP Spread, ATM Vol */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
            <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
              <div style={{ fontSize: 11, color: theme.textSecondary }}>25-Delta Put-Call Skew (IV₂₅ₚ - IV₂₅꜀)</div>
              <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4, color: volData.skew.skew_25delta >= 0 ? theme.caution : theme.growth }}>
                {volData.skew.skew_25delta > 0 ? "+" : ""}{(volData.skew.skew_25delta * 100).toFixed(2)}%
              </div>
              <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>
                Put 25Δ: {(volData.skew.put_25delta_iv * 100).toFixed(1)}% | Call 25Δ: {(volData.skew.call_25delta_iv * 100).toFixed(1)}%
              </div>
            </div>

            <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
              <div style={{ fontSize: 11, color: theme.textSecondary }}>Volatility Risk Premium (VRP = IV - RV₃₀)</div>
              <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4, color: (volData.skew.vrp_spread ?? 0) >= 0.02 ? theme.growth : theme.decline }}>
                {volData.skew.vrp_spread != null ? `${volData.skew.vrp_spread > 0 ? "+" : ""}${(volData.skew.vrp_spread * 100).toFixed(2)}%` : "—"}
              </div>
              <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>
                {volData.skew.vrp_spread != null && volData.skew.vrp_spread >= 0.02
                  ? "✓ Premium selling edge active (IV > RV)"
                  : "⚠ Thin/negative edge"}
              </div>
            </div>

            <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
              <div style={{ fontSize: 11, color: theme.textSecondary }}>ATM Implied Volatility</div>
              <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4, color: theme.accent }}>
                {(volData.skew.atm_iv * 100).toFixed(2)}%
              </div>
              <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>
                Realized Vol (30d): {volData.skew.realized_vol_30d ? `${(volData.skew.realized_vol_30d * 100).toFixed(1)}%` : "—"}
              </div>
            </div>
          </div>

          {/* Visual Smile Curve (SVG Chart) */}
          <div style={{ background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}`, padding: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>
                📈 Implied Volatility Smile ({volData.selected_expiration || volData.expirations[0]})
              </div>
              <div style={{ fontSize: 11, color: theme.textMuted }}>
                Spot: ${volData.spot_price.toFixed(2)} | As of {new Date(volData.as_of).toLocaleTimeString()}
              </div>
            </div>

            {/* SVG IV Smile curve */}
            {volData.smile_points.length > 0 && (
              <div style={{ position: "relative", width: "100%", height: 160 }}>
                {(() => {
                  const points = volData.smile_points;
                  const strikes = points.map((p) => p.strike);
                  const ivs = points.map((p) => p.iv);
                  const minK = Math.min(...strikes);
                  const maxK = Math.max(...strikes);
                  const minIv = Math.max(0, Math.min(...ivs) - 0.03);
                  const maxIv = Math.max(...ivs) + 0.03;

                  const chartW = 600;
                  const chartH = 130;
                  const padL = 40;
                  const padR = 20;
                  const padT = 10;
                  const padB = 20;

                  const scaleX = (k: number) => padL + ((k - minK) / (maxK - minK || 1)) * (chartW - padL - padR);
                  const scaleY = (iv: number) => padT + (1 - (iv - minIv) / (maxIv - minIv || 1)) * (chartH - padT - padB);

                  const pathStr = points
                    .map((p, idx) => `${idx === 0 ? "M" : "L"} ${scaleX(p.strike).toFixed(1)} ${scaleY(p.iv).toFixed(1)}`)
                    .join(" ");

                  const spotX = scaleX(volData.spot_price);

                  return (
                    <svg viewBox={`0 0 ${chartW} ${chartH}`} width="100%" height="100%" preserveAspectRatio="none">
                      {/* Gridlines */}
                      <line x1={padL} y1={padT} x2={chartW - padR} y2={padT} stroke={theme.chartGrid} strokeWidth="1" />
                      <line x1={padL} y1={scaleY(minIv + (maxIv - minIv) / 2)} x2={chartW - padR} y2={scaleY(minIv + (maxIv - minIv) / 2)} stroke={theme.chartGrid} strokeWidth="1" />
                      <line x1={padL} y1={chartH - padB} x2={chartW - padR} y2={chartH - padB} stroke={theme.border} strokeWidth="1" />

                      {/* Spot Line */}
                      {spotX >= padL && spotX <= chartW - padR && (
                        <g>
                          <line x1={spotX} y1={padT} x2={spotX} y2={chartH - padB} stroke={theme.accent} strokeWidth="1.5" strokeDasharray="3 3" />
                          <text x={spotX} y={padT + 8} fill={theme.accent} fontSize="9" textAnchor="middle" fontWeight="bold">
                            Spot ${volData.spot_price.toFixed(0)}
                          </text>
                        </g>
                      )}

                      {/* Smile Line */}
                      <path d={pathStr} fill="none" stroke="#38bdf8" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />

                      {/* Strike Points */}
                      {points.map((p) => {
                        const cx = scaleX(p.strike);
                        const cy = scaleY(p.iv);
                        const isAtm = Math.abs(p.strike - volData.spot_price) < 5;
                        return (
                          <g key={p.strike}>
                            <circle cx={cx} cy={cy} r={isAtm ? 5 : 3.5} fill={isAtm ? theme.growth : "#38bdf8"} stroke="#0b0e11" strokeWidth="1.5" />
                            <text x={cx} y={chartH - 4} fill={theme.textSecondary} fontSize="8" textAnchor="middle">
                              ${p.strike}
                            </text>
                          </g>
                        );
                      })}
                    </svg>
                  );
                })()}
              </div>
            )}
          </div>

          {/* Term Structure & Volatility Cone */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            {/* Term Structure Table */}
            <div style={{ background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}`, padding: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: theme.textPrimary, marginBottom: 8 }}>
                ⏳ Term Structure (Contango / Backwardation)
              </div>
              <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${theme.border}`, color: theme.textSecondary }}>
                    <th style={{ textAlign: "left", padding: "4px 6px" }}>Expiry</th>
                    <th style={{ textAlign: "right", padding: "4px 6px" }}>DTE</th>
                    <th style={{ textAlign: "right", padding: "4px 6px" }}>ATM IV</th>
                    <th style={{ textAlign: "right", padding: "4px 6px" }}>vs 30d RV</th>
                  </tr>
                </thead>
                <tbody>
                  {volData.term_structure.map((t) => {
                    const rvDiff = (t.atm_iv - (t.historical_realized_vol_30d ?? 0.165)) * 100;
                    return (
                      <tr key={t.expiration} style={{ borderBottom: `1px solid ${theme.border}` }}>
                        <td style={{ padding: "4px 6px", fontWeight: 500 }}>{t.expiration}</td>
                        <td style={{ textAlign: "right", padding: "4px 6px", color: theme.textSecondary }}>{t.dte}d</td>
                        <td style={{ textAlign: "right", padding: "4px 6px", fontWeight: 600, color: theme.accent }}>
                          {(t.atm_iv * 100).toFixed(1)}%
                        </td>
                        <td
                          style={{
                            textAlign: "right",
                            padding: "4px 6px",
                            color: rvDiff >= 0 ? theme.growth : theme.decline,
                            fontWeight: 500,
                          }}
                        >
                          {rvDiff >= 0 ? "+" : ""}{rvDiff.toFixed(1)}%
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Realized Vol Cone */}
            <div style={{ background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}`, padding: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: theme.textPrimary, marginBottom: 8 }}>
                🎯 Realized Volatility Cone vs ATM IV
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
                {[
                  { label: "10-Day Realized Vol", val: volData.skew.realized_vol_10d },
                  { label: "20-Day Realized Vol", val: volData.skew.realized_vol_20d },
                  { label: "30-Day Realized Vol", val: volData.skew.realized_vol_30d },
                  { label: "60-Day Realized Vol", val: volData.skew.realized_vol_60d },
                ].map((item, idx) => (
                  <div key={idx} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11 }}>
                    <span style={{ color: theme.textSecondary }}>{item.label}</span>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontWeight: 600 }}>{item.val ? `${(item.val * 100).toFixed(1)}%` : "—"}</span>
                      {item.val && (
                        <span
                          style={{
                            fontSize: 10,
                            padding: "1px 5px",
                            borderRadius: 3,
                            background: volData.skew.atm_iv > item.val ? "rgba(16, 185, 129, 0.15)" : "rgba(239, 68, 68, 0.15)",
                            color: volData.skew.atm_iv > item.val ? theme.growth : theme.decline,
                          }}
                        >
                          IV spread +{((volData.skew.atm_iv - item.val) * 100).toFixed(1)}%
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
