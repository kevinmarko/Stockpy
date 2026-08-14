import React, { useState } from "react";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { useMutation } from "../../hooks/useMutation";
import { theme } from "../../theme";
import type { HarRvForecastResponse, VolMispricingResponse, VolMispricingStrike, OptionsAlertTestResult } from "../../api/types";

interface VolForecastScannerProps {
  initialSymbol?: string;
  onClose?: () => void;
  onSelectStrike?: (strike: number, type: "CALL" | "PUT") => void;
}

export const VolForecastScanner: React.FC<VolForecastScannerProps> = ({
  initialSymbol = "SPY",
  onClose,
  onSelectStrike,
}) => {
  const [symbol, setSymbol] = useState(initialSymbol);
  const [symbolInput, setSymbolInput] = useState(initialSymbol);
  const [selectedExp, setSelectedExp] = useState<string | undefined>(undefined);
  const [filter, setFilter] = useState<"ALL" | "RICH" | "CHEAP">("ALL");
  const [testAlertResult, setTestAlertResult] = useState<OptionsAlertTestResult | null>(null);

  const forecastQuery = useApi<HarRvForecastResponse>(
    () => api.getHarRvForecast(symbol),
    [symbol]
  );
  const mispricingQuery = useApi<VolMispricingResponse>(
    () => api.getVolMispricing(symbol, selectedExp),
    [symbol, selectedExp]
  );

  const alertMutation = useMutation((params: { alert_type: string; symbol: string }) =>
    api.testOptionsAlert(params)
  );

  const handleSymbolSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (symbolInput.trim()) {
      const nextSym = symbolInput.trim().toUpperCase();
      setSymbol(nextSym);
      setSelectedExp(undefined);
    }
  };

  const handleSendTestAlert = async (alertType: "UOA" | "EARNINGS_CRUSH" | "DELTA_HEDGE") => {
    const res = await alertMutation.run({ alert_type: alertType, symbol });
    if (res) {
      setTestAlertResult(res);
    }
  };

  const forecast = forecastQuery.data;
  const mispricing = mispricingQuery.data;

  const strikes: VolMispricingStrike[] = mispricing?.strikes || [];
  const filteredStrikes = strikes.filter((s) => {
    if (filter === "RICH") return s.classification === "RICH";
    if (filter === "CHEAP") return s.classification === "CHEAP";
    return true;
  });

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
            <span>🎯 HAR-RV Volatility Forecaster &amp; Mispricing Scanner</span>
            {mispricing && (
              <span style={{ fontSize: 13, color: theme.accent, fontWeight: 600 }}>
                {mispricing.symbol} ${mispricing.spot_price.toFixed(2)}
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

      {(forecastQuery.loading || mispricingQuery.loading) && !mispricing && (
        <div style={{ padding: 24, textAlign: "center", color: theme.textSecondary }}>
          Fitting Corsi (2009) HAR-RV Model &amp; Scanning Strike Mispricings...
        </div>
      )}

      {(forecastQuery.error || mispricingQuery.error) && (
        <div style={{ padding: 12, background: "rgba(239, 68, 68, 0.15)", color: theme.decline, borderRadius: 6, fontSize: 13 }}>
          Failed to load forecast data: {forecastQuery.error || mispricingQuery.error}
        </div>
      )}

      {/* Expirations Bar */}
      {mispricing && mispricing.expirations && mispricing.expirations.length > 0 && (
        <div style={{ display: "flex", gap: 8, overflowX: "auto", paddingBottom: 4 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: theme.textSecondary, alignSelf: "center", marginRight: 4 }}>
            Expirations:
          </span>
          {mispricing.expirations.map((exp) => {
            const isSelected = (selectedExp || mispricing.expiration) === exp;
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
                {exp}
              </button>
            );
          })}
        </div>
      )}

      {/* Top 4 KPI Metrics */}
      {mispricing && forecast && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
          <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
            <div style={{ fontSize: 11, color: theme.textSecondary }}>HAR-RV Fair IV Blend (30d)</div>
            <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4, color: theme.accent }}>
              {(forecast.fair_iv_blend * 100).toFixed(2)}%
            </div>
            <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>
              GJR-GARCH Adj: {forecast.gjr_garch_vol ? `${(forecast.gjr_garch_vol * 100).toFixed(1)}%` : "—"}
            </div>
          </div>

          <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
            <div style={{ fontSize: 11, color: theme.textSecondary }}>Market ATM IV vs Fair IV</div>
            <div
              style={{
                fontSize: 18,
                fontWeight: 600,
                marginTop: 4,
                color: mispricing.market_atm_iv > forecast.fair_iv_blend ? theme.caution : theme.growth,
              }}
            >
              {(mispricing.market_atm_iv * 100).toFixed(2)}% vs {(mispricing.fair_iv_baseline * 100).toFixed(2)}%
            </div>
            <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>
              Spread: {((mispricing.market_atm_iv - mispricing.fair_iv_baseline) * 100 > 0 ? "+" : "") +
                ((mispricing.market_atm_iv - mispricing.fair_iv_baseline) * 100).toFixed(2)}%
            </div>
          </div>

          <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
            <div style={{ fontSize: 11, color: theme.textSecondary }}>Rich Strikes (Sell Premium)</div>
            <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4, color: theme.decline }}>
              {mispricing.rich_strikes_count} Strikes
            </div>
            <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>
              IV &gt; Fair IV + 1.5σ (Overvalued)
            </div>
          </div>

          <div style={{ padding: 12, background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}` }}>
            <div style={{ fontSize: 11, color: theme.textSecondary }}>Cheap Convexity (Buy Gamma)</div>
            <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4, color: theme.growth }}>
              {mispricing.cheap_strikes_count} Strikes
            </div>
            <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>
              IV &lt; Fair IV - 1.0σ (Undervalued)
            </div>
          </div>
        </div>
      )}

      {/* Corsi (2009) HAR-RV Model Breakdown Card */}
      {forecast && (
        <div style={{ background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}`, padding: 14 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>
              📐 Corsi (2009) Heterogeneous Autoregressive Realized Volatility Decomposition
            </span>
            <span style={{ fontSize: 11, color: theme.textMuted }}>
              R² Fit: {forecast.r_squared ? `${(forecast.r_squared * 100).toFixed(1)}%` : "N/A"}
            </span>
          </div>

          <div style={{ fontSize: 12, color: theme.textSecondary, marginBottom: 10, fontFamily: "monospace" }}>
            RV_(t+1) = β₀ ({forecast.coefficients.beta_0.toFixed(3)}) + β_d·RV^(d) ({forecast.coefficients.beta_d.toFixed(3)}) + β_w·RV^(w) ({forecast.coefficients.beta_w.toFixed(3)}) + β_m·RV^(m) ({forecast.coefficients.beta_m.toFixed(3)})
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 8 }}>
            <div style={{ background: theme.surface2, padding: "6px 10px", borderRadius: 4 }}>
              <div style={{ fontSize: 10, color: theme.textMuted }}>1-Day RV (Daily)</div>
              <div style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>
                {(forecast.rv_daily * 100).toFixed(2)}%
              </div>
            </div>
            <div style={{ background: theme.surface2, padding: "6px 10px", borderRadius: 4 }}>
              <div style={{ fontSize: 10, color: theme.textMuted }}>5-Day RV (Weekly)</div>
              <div style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>
                {(forecast.rv_weekly * 100).toFixed(2)}%
              </div>
            </div>
            <div style={{ background: theme.surface2, padding: "6px 10px", borderRadius: 4 }}>
              <div style={{ fontSize: 10, color: theme.textMuted }}>22-Day RV (Monthly)</div>
              <div style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>
                {(forecast.rv_monthly * 100).toFixed(2)}%
              </div>
            </div>
            <div style={{ background: theme.surface2, padding: "6px 10px", borderRadius: 4 }}>
              <div style={{ fontSize: 10, color: theme.textMuted }}>1d Ahead Forecast</div>
              <div style={{ fontSize: 13, fontWeight: 600, color: theme.accent }}>
                {(forecast.forecast_vol_1d * 100).toFixed(2)}%
              </div>
            </div>
            <div style={{ background: theme.surface2, padding: "6px 10px", borderRadius: 4 }}>
              <div style={{ fontSize: 10, color: theme.textMuted }}>30d Term Forecast</div>
              <div style={{ fontSize: 13, fontWeight: 600, color: theme.accent }}>
                {(forecast.forecast_vol_30d * 100).toFixed(2)}%
              </div>
            </div>
          </div>
        </div>
      )}

      {/* SVG Mispricing Chart: Market IV vs HAR-RV Fair IV */}
      {mispricing && strikes.length > 0 && (
        <div style={{ background: theme.base, borderRadius: 6, border: `1px solid ${theme.border}`, padding: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>
              📈 Implied Volatility Mispricing Spread ({mispricing.expiration})
            </div>
            <div style={{ display: "flex", gap: 12, fontSize: 11 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 4, color: "#38bdf8" }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#38bdf8" }} /> Market IV
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 4, color: "#f59e0b" }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#f59e0b" }} /> HAR-RV Fair IV
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 4, color: theme.decline }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", background: theme.decline }} /> Rich (&gt;+1.5σ)
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 4, color: theme.growth }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", background: theme.growth }} /> Cheap (&lt;-1.0σ)
              </span>
            </div>
          </div>

          <div style={{ position: "relative", width: "100%", height: 180 }}>
            {(() => {
              const strikeVals = strikes.map((s) => s.strike);
              const mktIvs = strikes.map((s) => s.market_iv);
              const fairIvs = strikes.map((s) => s.fair_iv);
              const allIvs = [...mktIvs, ...fairIvs];

              const minK = Math.min(...strikeVals);
              const maxK = Math.max(...strikeVals);
              const minIv = Math.max(0, Math.min(...allIvs) - 0.04);
              const maxIv = Math.max(...allIvs) + 0.04;

              const chartW = 700;
              const chartH = 150;
              const padL = 45;
              const padR = 25;
              const padT = 15;
              const padB = 25;

              const scaleX = (k: number) => padL + ((k - minK) / (maxK - minK || 1)) * (chartW - padL - padR);
              const scaleY = (iv: number) => padT + (1 - (iv - minIv) / (maxIv - minIv || 1)) * (chartH - padT - padB);

              const mktPathStr = strikes
                .map((s, idx) => `${idx === 0 ? "M" : "L"} ${scaleX(s.strike).toFixed(1)} ${scaleY(s.market_iv).toFixed(1)}`)
                .join(" ");

              const fairPathStr = strikes
                .map((s, idx) => `${idx === 0 ? "M" : "L"} ${scaleX(s.strike).toFixed(1)} ${scaleY(s.fair_iv).toFixed(1)}`)
                .join(" ");

              const spotX = scaleX(mispricing.spot_price);

              return (
                <svg viewBox={`0 0 ${chartW} ${chartH}`} width="100%" height="100%" preserveAspectRatio="none">
                  {/* Gridlines */}
                  <line x1={padL} y1={padT} x2={chartW - padR} y2={padT} stroke={theme.chartGrid} strokeWidth="1" />
                  <line
                    x1={padL}
                    y1={scaleY(minIv + (maxIv - minIv) / 2)}
                    x2={chartW - padR}
                    y2={scaleY(minIv + (maxIv - minIv) / 2)}
                    stroke={theme.chartGrid}
                    strokeWidth="1"
                  />
                  <line x1={padL} y1={chartH - padB} x2={chartW - padR} y2={chartH - padB} stroke={theme.border} strokeWidth="1" />

                  {/* Spot line */}
                  {spotX >= padL && spotX <= chartW - padR && (
                    <g>
                      <line x1={spotX} y1={padT} x2={spotX} y2={chartH - padB} stroke={theme.accent} strokeWidth="1.5" strokeDasharray="3 3" />
                      <text x={spotX} y={padT + 8} fill={theme.accent} fontSize="9" textAnchor="middle" fontWeight="bold">
                        Spot ${mispricing.spot_price.toFixed(0)}
                      </text>
                    </g>
                  )}

                  {/* Fair IV Curve */}
                  <path d={fairPathStr} fill="none" stroke="#f59e0b" strokeWidth="2" strokeDasharray="4 3" />

                  {/* Market IV Curve */}
                  <path d={mktPathStr} fill="none" stroke="#38bdf8" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />

                  {/* Strike Points */}
                  {strikes.map((s) => {
                    const cx = scaleX(s.strike);
                    const cyMkt = scaleY(s.market_iv);
                    const isRich = s.classification === "RICH";
                    const isCheap = s.classification === "CHEAP";
                    const dotColor = isRich ? theme.decline : isCheap ? theme.growth : "#38bdf8";

                    return (
                      <g key={s.strike} style={{ cursor: "pointer" }} onClick={() => onSelectStrike && onSelectStrike(s.strike, s.option_type)}>
                        <circle cx={cx} cy={cyMkt} r={isRich || isCheap ? "5" : "3.5"} fill={dotColor} stroke="#0f172a" strokeWidth="1.5" />
                        {/* X-axis label */}
                        <text x={cx} y={chartH - padB + 14} fill={theme.textMuted} fontSize="8.5" textAnchor="middle">
                          ${s.strike}
                        </text>
                      </g>
                    );
                  })}
                </svg>
              );
            })()}
          </div>
        </div>
      )}

      {/* Trade Recommendations Cards */}
      {mispricing && mispricing.trade_recommendations && mispricing.trade_recommendations.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>
            💡 Algorithmic Trade Recommendations from Volatility Mispricing
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 10 }}>
            {mispricing.trade_recommendations.map((rec, i) => (
              <div
                key={i}
                style={{
                  background: theme.base,
                  border: `1px solid ${rec.direction === "SELL_VOL" ? "rgba(239, 68, 68, 0.4)" : "rgba(16, 185, 129, 0.4)"}`,
                  borderRadius: 6,
                  padding: 12,
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontWeight: 600, fontSize: 13, color: theme.textPrimary }}>{rec.strategy}</span>
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      padding: "2px 6px",
                      borderRadius: 4,
                      background: rec.direction === "SELL_VOL" ? "rgba(239, 68, 68, 0.2)" : "rgba(16, 185, 129, 0.2)",
                      color: rec.direction === "SELL_VOL" ? theme.decline : theme.growth,
                    }}
                  >
                    {rec.direction === "SELL_VOL" ? "⚡ SELL VOL" : "📈 BUY VOL"} (+{rec.estimated_edge_pct}%)
                  </span>
                </div>
                <div style={{ fontSize: 11, color: theme.textSecondary }}>{rec.reason}</div>
                <div style={{ fontSize: 11, color: theme.textMuted }}>
                  Strikes: {rec.strikes.map((k) => `$${k}`).join(" / ")}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Strike Table with Filter */}
      {mispricing && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>
              📋 Strike Mispricing Ledger ({filteredStrikes.length} strikes)
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              {(["ALL", "RICH", "CHEAP"] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  style={{
                    padding: "3px 8px",
                    borderRadius: 4,
                    fontSize: 11,
                    background: filter === f ? theme.surface3 : theme.base,
                    color: filter === f ? theme.textPrimary : theme.textMuted,
                    border: `1px solid ${filter === f ? theme.accent : theme.border}`,
                    cursor: "pointer",
                  }}
                >
                  {f === "ALL" ? "All Strikes" : f === "RICH" ? "Rich (Sell)" : "Cheap (Buy)"}
                </button>
              ))}
            </div>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, textAlign: "left" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.border}`, color: theme.textSecondary }}>
                  <th style={{ padding: "6px 8px" }}>Strike</th>
                  <th style={{ padding: "6px 8px" }}>Type</th>
                  <th style={{ padding: "6px 8px" }}>Market IV</th>
                  <th style={{ padding: "6px 8px" }}>Fair IV</th>
                  <th style={{ padding: "6px 8px" }}>Spread</th>
                  <th style={{ padding: "6px 8px" }}>Z-Score</th>
                  <th style={{ padding: "6px 8px" }}>Status</th>
                  <th style={{ padding: "6px 8px" }}>Action</th>
                  <th style={{ padding: "6px 8px" }}>Delta</th>
                  <th style={{ padding: "6px 8px" }}>Bid / Ask</th>
                </tr>
              </thead>
              <tbody>
                {filteredStrikes.map((s) => {
                  const isRich = s.classification === "RICH";
                  const isCheap = s.classification === "CHEAP";
                  return (
                    <tr
                      key={`${s.strike}-${s.option_type}`}
                      style={{
                        borderBottom: `1px solid ${theme.border}`,
                        background: isRich
                          ? "rgba(239, 68, 68, 0.05)"
                          : isCheap
                          ? "rgba(16, 185, 129, 0.05)"
                          : "transparent",
                      }}
                    >
                      <td style={{ padding: "6px 8px", fontWeight: 600 }}>${s.strike}</td>
                      <td style={{ padding: "6px 8px", color: s.option_type === "CALL" ? "#38bdf8" : "#f43f5e" }}>
                        {s.option_type}
                      </td>
                      <td style={{ padding: "6px 8px" }}>{(s.market_iv * 100).toFixed(1)}%</td>
                      <td style={{ padding: "6px 8px", color: "#f59e0b" }}>{(s.fair_iv * 100).toFixed(1)}%</td>
                      <td
                        style={{
                          padding: "6px 8px",
                          fontWeight: 600,
                          color: s.iv_spread > 0 ? theme.decline : s.iv_spread < 0 ? theme.growth : theme.textPrimary,
                        }}
                      >
                        {s.iv_spread > 0 ? "+" : ""}
                        {(s.iv_spread * 100).toFixed(1)}%
                      </td>
                      <td style={{ padding: "6px 8px" }}>{s.spread_zscore > 0 ? `+${s.spread_zscore}` : s.spread_zscore}σ</td>
                      <td style={{ padding: "6px 8px" }}>
                        <span
                          style={{
                            padding: "2px 6px",
                            borderRadius: 4,
                            fontSize: 10,
                            fontWeight: 700,
                            background: isRich
                              ? "rgba(239, 68, 68, 0.2)"
                              : isCheap
                              ? "rgba(16, 185, 129, 0.2)"
                              : "rgba(148, 163, 184, 0.2)",
                            color: isRich ? theme.decline : isCheap ? theme.growth : theme.textMuted,
                          }}
                        >
                          {s.classification}
                        </span>
                      </td>
                      <td style={{ padding: "6px 8px", fontSize: 11 }}>{s.suggested_action}</td>
                      <td style={{ padding: "6px 8px", color: theme.textMuted }}>{s.delta ? s.delta.toFixed(2) : "—"}</td>
                      <td style={{ padding: "6px 8px", color: theme.textMuted }}>
                        ${s.bid?.toFixed(2)} / ${s.ask?.toFixed(2)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Real-time Alerts Dispatcher Test Bar */}
      <div
        style={{
          borderTop: `1px solid ${theme.border}`,
          paddingTop: 12,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 8,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 12, color: theme.textSecondary }}>🔔 Dispatch Multi-Channel Test Alert:</span>
          <button
            onClick={() => handleSendTestAlert("UOA")}
            disabled={alertMutation.pending}
            style={{
              padding: "4px 8px",
              background: theme.base,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 4,
              fontSize: 11,
              cursor: "pointer",
            }}
          >
            🌊 UOA Whale Sweep
          </button>
          <button
            onClick={() => handleSendTestAlert("EARNINGS_CRUSH")}
            disabled={alertMutation.pending}
            style={{
              padding: "4px 8px",
              background: theme.base,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 4,
              fontSize: 11,
              cursor: "pointer",
            }}
          >
            ⚡ Earnings Crush
          </button>
          <button
            onClick={() => handleSendTestAlert("DELTA_HEDGE")}
            disabled={alertMutation.pending}
            style={{
              padding: "4px 8px",
              background: theme.base,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 4,
              fontSize: 11,
              cursor: "pointer",
            }}
          >
            🛡 Delta Hedge Limit
          </button>
        </div>

        {testAlertResult && (
          <div style={{ fontSize: 11, color: theme.growth }}>
            ✓ Dispatched to {testAlertResult.channels.join(", ")}
          </div>
        )}
      </div>
    </div>
  );
};
