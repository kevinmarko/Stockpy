import React, { useState, useEffect, useMemo } from "react";
import { api } from "../../api/client";
import { theme, alpha } from "../../theme";
import type { CopulaPairsResponse, CopulaSeriesPoint } from "../../api/types";

interface CopulaSpreadViewProps {
  initialPair?: string;
  onClose?: () => void;
}

export const CopulaSpreadView: React.FC<CopulaSpreadViewProps> = ({
  initialPair = "SPY/QQQ",
  onClose,
}) => {
  const [selectedPair, setSelectedPair] = useState<string>(initialPair);
  const [customPairInput, setCustomPairInput] = useState<string>("");
  const [showCustomInput, setShowCustomInput] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<CopulaPairsResponse | null>(null);
  const [hoveredPoint, setHoveredPoint] = useState<CopulaSeriesPoint | null>(null);

  const presetPairs = [
    { label: "SPY / QQQ", value: "SPY/QQQ", desc: "Index Heavy (Clayton Crash Risk)" },
    { label: "NVDA / AMD", value: "NVDA/AMD", desc: "Semi Momentum (Gumbel Upside)" },
    { label: "GOOGL / META", value: "GOOGL/META", desc: "Ad Giants (Frank Symmetric)" },
    { label: "MSFT / AAPL", value: "MSFT/AAPL", desc: "Big Tech Mean Reversion" },
    { label: "JPM / BAC", value: "JPM/BAC", desc: "Money Center Banks" },
  ];

  const fetchAnalysis = async (pair: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getCopulaPairsAnalysis(pair);
      setData(res);
      setHoveredPoint(res.historical_series[res.historical_series.length - 1] || null);
    } catch (err: any) {
      setError(err?.message || `Failed to fetch Copula pairs analysis for ${pair}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAnalysis(selectedPair);
  }, [selectedPair]);

  const handleSelectPreset = (p: string) => {
    setSelectedPair(p);
    setShowCustomInput(false);
  };

  const handleCustomSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (customPairInput.trim()) {
      const normalized = customPairInput.trim().toUpperCase();
      setSelectedPair(normalized);
    }
  };

  // SVG Chart bounds and helpers
  const series = data?.historical_series || [];

  const { minBeta, maxBeta, minZ, maxZ } = useMemo(() => {
    if (!series.length) return { minBeta: 0.5, maxBeta: 1.5, minZ: -3, maxZ: 3 };
    const betas = series.map((s) => s.kalman_beta);
    const zScores = series.map((s) => s.spread_z_score);
    return {
      minBeta: Math.min(...betas) - 0.05,
      maxBeta: Math.max(...betas) + 0.05,
      minZ: Math.min(-2.5, Math.min(...zScores) - 0.5),
      maxZ: Math.max(2.5, Math.max(...zScores) + 0.5),
    };
  }, [series]);

  // Chart dimensions
  const svgWidth = 640;
  const svgHeight = 180;
  const padding = { top: 20, right: 30, bottom: 25, left: 45 };
  const chartW = svgWidth - padding.left - padding.right;
  const chartH = svgHeight - padding.top - padding.bottom;

  const getBetaY = (beta: number) => {
    const range = maxBeta - minBeta || 0.1;
    return padding.top + chartH - ((beta - minBeta) / range) * chartH;
  };

  const getZY = (z: number) => {
    const range = maxZ - minZ || 1;
    return padding.top + chartH - ((z - minZ) / range) * chartH;
  };

  const getX = (index: number) => {
    const total = series.length || 1;
    return padding.left + (index / (total - 1 || 1)) * chartW;
  };

  const betaPath = useMemo(() => {
    if (!series.length) return "";
    return series
      .map((pt, i) => `${i === 0 ? "M" : "L"} ${getX(i).toFixed(1)} ${getBetaY(pt.kalman_beta).toFixed(1)}`)
      .join(" ");
  }, [series, minBeta, maxBeta]);

  const zPath = useMemo(() => {
    if (!series.length) return "";
    return series
      .map((pt, i) => `${i === 0 ? "M" : "L"} ${getX(i).toFixed(1)} ${getZY(pt.spread_z_score).toFixed(1)}`)
      .join(" ");
  }, [series, minZ, maxZ]);

  const action = data?.signal_action || "HOLD";
  const actionColor =
    action === "LONG_SPREAD"
      ? theme.growth
      : action === "SHORT_SPREAD"
      ? theme.caution
      : action === "EXIT"
      ? theme.decline
      : theme.textSecondary;

  const familyDesc = useMemo(() => {
    if (!data) return { title: "Copula", formula: "", note: "" };
    switch (data.copula_family) {
      case "Clayton":
        return {
          title: "Clayton Copula (Lower Tail Crisis Dependence)",
          formula: "λ_L = 2^(-1/θ),  λ_U = 0",
          note: "Models asymmetric downside co-crash probability during liquidity panics and market stress.",
        };
      case "Gumbel":
        return {
          title: "Gumbel Copula (Upper Tail Momentum Dependence)",
          formula: "λ_U = 2 - 2^(1/θ),  λ_L = 0",
          note: "Models extreme upside momentum co-movement during speculative bull breakouts.",
        };
      case "Frank":
        return {
          title: "Frank Copula (Symmetric Dependence)",
          formula: "λ_L = 0,  λ_U = 0",
          note: "Symmetric association across both tails, ideal for balanced mean-reverting equity pairs.",
        };
      default:
        return {
          title: `${data.copula_family} Copula`,
          formula: "Joint Distribution F(x, y) = C(u, v)",
          note: "Multivariate non-linear joint dependency structure.",
        };
    }
  }, [data]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 16,
        color: theme.textPrimary,
      }}
    >
      {/* Header & Pair Selector */}
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
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: "1.25rem", fontWeight: 700 }}>
              🔗 Copula Statistical Arbitrage & Dynamic Kalman Beta
            </span>
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
              Phase 21
            </span>
            {data && (
              <span
                style={{
                  fontSize: "0.75rem",
                  padding: "2px 8px",
                  borderRadius: 10,
                  background: `${actionColor}25`,
                  color: actionColor,
                  fontWeight: 700,
                  border: `1px solid ${actionColor}60`,
                }}
              >
                {data.signal_action.replace("_", " ")}
              </span>
            )}
          </div>
          <div style={{ fontSize: "0.85rem", color: theme.textSecondary, marginTop: 4 }}>
            Non-linear joint tail risk modeling (Bedford & Cooke 2002), dynamic state-space Kalman filter hedge ratio (β_t), and Ornstein-Uhlenbeck spread mean reversion.
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            onClick={() => fetchAnalysis(selectedPair)}
            disabled={loading}
            style={{
              padding: "6px 12px",
              background: theme.surface2,
              border: `1px solid ${theme.border}`,
              color: theme.textPrimary,
              borderRadius: 8,
              fontSize: "0.85rem",
              cursor: loading ? "not-allowed" : "pointer",
            }}
          >
            {loading ? "Computing..." : "↻ Refresh"}
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

      {/* Preset Pair Selectors */}
      <div
        style={{
          display: "flex",
          gap: 8,
          flexWrap: "wrap",
          alignItems: "center",
          padding: "12px 16px",
          background: theme.surface,
          borderRadius: 12,
          border: `1px solid ${theme.border}`,
        }}
      >
        <span style={{ fontSize: "0.85rem", color: theme.textSecondary, marginRight: 4 }}>
          Select Pair:
        </span>
        {presetPairs.map((p) => {
          const isSelected = selectedPair === p.value && !showCustomInput;
          return (
            <button
              key={p.value}
              onClick={() => handleSelectPreset(p.value)}
              style={{
                padding: "6px 12px",
                borderRadius: 8,
                fontSize: "0.85rem",
                fontWeight: isSelected ? 600 : 400,
                background: isSelected ? theme.accent : theme.surface2,
                color: isSelected ? "#000" : theme.textPrimary,
                border: `1px solid ${isSelected ? theme.accent : theme.border}`,
                cursor: "pointer",
                transition: "all 0.15s ease",
              }}
              title={p.desc}
            >
              {p.label}
            </button>
          );
        })}
        <button
          onClick={() => setShowCustomInput(!showCustomInput)}
          style={{
            padding: "6px 12px",
            borderRadius: 8,
            fontSize: "0.85rem",
            background: showCustomInput ? theme.surface3 : "transparent",
            color: theme.textSecondary,
            border: `1px dashed ${theme.borderStrong}`,
            cursor: "pointer",
          }}
        >
          ✏️ Custom Pair...
        </button>

        {showCustomInput && (
          <form onSubmit={handleCustomSubmit} style={{ display: "flex", gap: 6, alignItems: "center", marginLeft: 6 }}>
            <input
              type="text"
              placeholder="e.g. MSFT/GOOGL"
              value={customPairInput}
              onChange={(e) => setCustomPairInput(e.target.value)}
              style={{
                padding: "6px 10px",
                background: theme.base,
                border: `1px solid ${theme.borderStrong}`,
                borderRadius: 6,
                color: theme.textPrimary,
                fontSize: "0.85rem",
                width: 130,
              }}
            />
            <button
              type="submit"
              style={{
                padding: "6px 12px",
                background: theme.accent,
                color: "#000",
                fontWeight: 600,
                border: "none",
                borderRadius: 6,
                fontSize: "0.8rem",
                cursor: "pointer",
              }}
            >
              Load
            </button>
          </form>
        )}
      </div>

      {error && (
        <div
          style={{
            padding: "12px 16px",
            background: alpha(theme.decline, "15"),
            border: `1px solid ${alpha(theme.decline, "40")}`,
            borderRadius: 8,
            color: theme.decline,
            fontSize: "0.85rem",
          }}
        >
          {error}
        </div>
      )}

      {/* KPI & Mathematical Metrics Grid */}
      {data && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: 12,
          }}
        >
          {/* Dynamic Kalman Beta */}
          <div
            style={{
              padding: "14px",
              background: theme.surface,
              borderRadius: 10,
              border: `1px solid ${theme.border}`,
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            <div style={{ fontSize: "0.75rem", color: theme.textSecondary, textTransform: "uppercase" }}>
              Kalman Dynamic Beta (β_t)
            </div>
            <div style={{ fontSize: "1.5rem", fontWeight: 700, color: theme.accent }}>
              {data.kalman_beta.toFixed(3)}
            </div>
            <div style={{ fontSize: "0.75rem", color: theme.textMuted }}>
              Intercept α_t: {data.kalman_alpha.toFixed(2)} | Q: 10⁻⁵, R: 10⁻³
            </div>
          </div>

          {/* Spread Z-Score */}
          <div
            style={{
              padding: "14px",
              background: theme.surface,
              borderRadius: 10,
              border: `1px solid ${theme.border}`,
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            <div style={{ fontSize: "0.75rem", color: theme.textSecondary, textTransform: "uppercase" }}>
              Spread Z-Score
            </div>
            <div
              style={{
                fontSize: "1.5rem",
                fontWeight: 700,
                color: Math.abs(data.spread_z_score) >= 2.0 ? theme.caution : theme.growth,
              }}
            >
              {data.spread_z_score > 0 ? "+" : ""}
              {data.spread_z_score.toFixed(2)}σ
            </div>
            <div style={{ fontSize: "0.75rem", color: theme.textMuted }}>
              Current Spread: ${data.current_spread.toFixed(2)} (Entry: |Z| &gt; 2.0)
            </div>
          </div>

          {/* OU Half Life */}
          <div
            style={{
              padding: "14px",
              background: theme.surface,
              borderRadius: 10,
              border: `1px solid ${theme.border}`,
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            <div style={{ fontSize: "0.75rem", color: theme.textSecondary, textTransform: "uppercase" }}>
              OU Mean Reversion Half-Life
            </div>
            <div style={{ fontSize: "1.5rem", fontWeight: 700, color: theme.textPrimary }}>
              {data.ou_half_life_days.toFixed(1)} <span style={{ fontSize: "0.9rem" }}>days</span>
            </div>
            <div style={{ fontSize: "0.75rem", color: data.ou_half_life_days <= 60 ? theme.growth : theme.decline }}>
              {data.ou_half_life_days <= 60 ? "✓ Stat-Arb Filter Passed (5-60d)" : "⚠ Half-life exceeds 60d cap"}
            </div>
          </div>

          {/* Copula Tail Risk */}
          <div
            style={{
              padding: "14px",
              background: theme.surface,
              borderRadius: 10,
              border: `1px solid ${theme.border}`,
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            <div style={{ fontSize: "0.75rem", color: theme.textSecondary, textTransform: "uppercase" }}>
              Fitted Copula Family
            </div>
            <div style={{ fontSize: "1.25rem", fontWeight: 700, color: theme.textPrimary }}>
              {data.copula_family}
            </div>
            <div style={{ fontSize: "0.75rem", color: theme.textMuted }}>
              θ = {data.tail_dependence.theta.toFixed(2)} | Kendall's τ = {data.tail_dependence.kendall_tau.toFixed(2)}
            </div>
          </div>
        </div>
      )}

      {/* Tail Dependence Gauges */}
      {data && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 12,
            background: theme.surface,
            padding: "16px",
            borderRadius: 12,
            border: `1px solid ${theme.border}`,
          }}
        >
          {/* Lower Tail Dependence λ_L */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
              <span style={{ fontSize: "0.85rem", fontWeight: 600, color: theme.textPrimary }}>
                📉 Lower Tail Crisis Dependence (λ_L)
              </span>
              <span style={{ fontSize: "0.85rem", fontWeight: 700, color: theme.caution }}>
                {(data.tail_dependence.lower_tail_dependence * 100).toFixed(1)}%
              </span>
            </div>
            <div style={{ height: 8, background: theme.surface2, borderRadius: 4, overflow: "hidden" }}>
              <div
                style={{
                  height: "100%",
                  width: `${Math.min(100, data.tail_dependence.lower_tail_dependence * 100)}%`,
                  background:
                    data.tail_dependence.lower_tail_dependence > 0.3 ? theme.decline : theme.caution,
                  borderRadius: 4,
                  transition: "width 0.3s ease",
                }}
              />
            </div>
            <div style={{ fontSize: "0.75rem", color: theme.textMuted, marginTop: 4 }}>
              Asymmetric downside co-crash probability during market drawdowns.
            </div>
          </div>

          {/* Upper Tail Dependence λ_U */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
              <span style={{ fontSize: "0.85rem", fontWeight: 600, color: theme.textPrimary }}>
                📈 Upper Tail Momentum Dependence (λ_U)
              </span>
              <span style={{ fontSize: "0.85rem", fontWeight: 700, color: theme.growth }}>
                {(data.tail_dependence.upper_tail_dependence * 100).toFixed(1)}%
              </span>
            </div>
            <div style={{ height: 8, background: theme.surface2, borderRadius: 4, overflow: "hidden" }}>
              <div
                style={{
                  height: "100%",
                  width: `${Math.min(100, data.tail_dependence.upper_tail_dependence * 100)}%`,
                  background: theme.growth,
                  borderRadius: 4,
                  transition: "width 0.3s ease",
                }}
              />
            </div>
            <div style={{ fontSize: "0.75rem", color: theme.textMuted, marginTop: 4 }}>
              Joint breakout probability in strong market rally regimes.
            </div>
          </div>
        </div>
      )}

      {/* Interactive Charts Area */}
      {data && series.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr",
            gap: 16,
          }}
        >
          {/* Chart 1: Dynamic Kalman Beta Curve */}
          <div
            style={{
              padding: "16px",
              background: theme.surface,
              borderRadius: 12,
              border: `1px solid ${theme.border}`,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <div>
                <span style={{ fontWeight: 600, fontSize: "0.95rem" }}>
                  📈 Dynamic Kalman Hedge Ratio Time Series (β_t)
                </span>
                <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
                  State-space model: y_t = α_t + β_t x_t + ε_t (Time-varying optimal delta hedge)
                </div>
              </div>
              {hoveredPoint && (
                <div style={{ fontSize: "0.8rem", color: theme.accent, fontWeight: 600 }}>
                  {hoveredPoint.date}: β = {hoveredPoint.kalman_beta.toFixed(3)}
                </div>
              )}
            </div>

            <div style={{ width: "100%", overflowX: "auto" }}>
              <svg
                viewBox={`0 0 ${svgWidth} ${svgHeight}`}
                style={{ width: "100%", height: "auto", display: "block" }}
              >
                {/* Horizontal Grid lines */}
                {[minBeta, (minBeta + maxBeta) / 2, maxBeta].map((val, idx) => (
                  <g key={idx}>
                    <line
                      x1={padding.left}
                      y1={getBetaY(val)}
                      x2={svgWidth - padding.right}
                      y2={getBetaY(val)}
                      stroke={theme.border}
                      strokeDasharray="3 3"
                    />
                    <text
                      x={padding.left - 6}
                      y={getBetaY(val) + 4}
                      fill={theme.textMuted}
                      fontSize="10"
                      textAnchor="end"
                    >
                      {val.toFixed(2)}
                    </text>
                  </g>
                ))}

                {/* Beta Line */}
                <path d={betaPath} fill="none" stroke={theme.accent} strokeWidth="2.5" />

                {/* Data points & hover listener */}
                {series.map((pt, i) => (
                  <circle
                    key={i}
                    cx={getX(i)}
                    cy={getBetaY(pt.kalman_beta)}
                    r={hoveredPoint?.date === pt.date ? 4 : 2}
                    fill={hoveredPoint?.date === pt.date ? "#fff" : theme.accent}
                    style={{ cursor: "pointer" }}
                    onMouseEnter={() => setHoveredPoint(pt)}
                  />
                ))}
              </svg>
            </div>
          </div>

          {/* Chart 2: Rolling Spread Z-Score & ±2σ Entry Bands */}
          <div
            style={{
              padding: "16px",
              background: theme.surface,
              borderRadius: 12,
              border: `1px solid ${theme.border}`,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <div>
                <span style={{ fontWeight: 600, fontSize: "0.95rem" }}>
                  📊 Rolling Spread Z-Score & Mean Reversion Trigger Bands
                </span>
                <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
                  Entry Triggers: |Z| &gt; 2.0σ (Statistical Arbitrage Dislocation) | Exit: Z = 0.0σ Mean Cross
                </div>
              </div>
              {hoveredPoint && (
                <div
                  style={{
                    fontSize: "0.8rem",
                    color: Math.abs(hoveredPoint.spread_z_score) >= 2.0 ? theme.caution : theme.growth,
                    fontWeight: 600,
                  }}
                >
                  {hoveredPoint.date}: Z = {hoveredPoint.spread_z_score > 0 ? "+" : ""}
                  {hoveredPoint.spread_z_score.toFixed(2)}σ
                </div>
              )}
            </div>

            <div style={{ width: "100%", overflowX: "auto" }}>
              <svg
                viewBox={`0 0 ${svgWidth} ${svgHeight}`}
                style={{ width: "100%", height: "auto", display: "block" }}
              >
                {/* Upper +2.0σ Band */}
                <line
                  x1={padding.left}
                  y1={getZY(2.0)}
                  x2={svgWidth - padding.right}
                  y2={getZY(2.0)}
                  stroke={theme.decline}
                  strokeDasharray="4 4"
                  strokeWidth="1.5"
                />
                <text
                  x={svgWidth - padding.right + 4}
                  y={getZY(2.0) + 3}
                  fill={theme.decline}
                  fontSize="9"
                  fontWeight="600"
                >
                  +2.0σ (Short)
                </text>

                {/* Mean 0.0σ Line */}
                <line
                  x1={padding.left}
                  y1={getZY(0.0)}
                  x2={svgWidth - padding.right}
                  y2={getZY(0.0)}
                  stroke={theme.textMuted}
                  strokeWidth="1"
                />
                <text
                  x={svgWidth - padding.right + 4}
                  y={getZY(0.0) + 3}
                  fill={theme.textMuted}
                  fontSize="9"
                >
                  0.0σ (Exit)
                </text>

                {/* Lower -2.0σ Band */}
                <line
                  x1={padding.left}
                  y1={getZY(-2.0)}
                  x2={svgWidth - padding.right}
                  y2={getZY(-2.0)}
                  stroke={theme.growth}
                  strokeDasharray="4 4"
                  strokeWidth="1.5"
                />
                <text
                  x={svgWidth - padding.right + 4}
                  y={getZY(-2.0) + 3}
                  fill={theme.growth}
                  fontSize="9"
                  fontWeight="600"
                >
                  -2.0σ (Long)
                </text>

                {/* Spread Z Line */}
                <path d={zPath} fill="none" stroke="#f59e0b" strokeWidth="2.5" />

                {/* Points */}
                {series.map((pt, i) => {
                  const isDislocated = Math.abs(pt.spread_z_score) >= 2.0;
                  return (
                    <circle
                      key={i}
                      cx={getX(i)}
                      cy={getZY(pt.spread_z_score)}
                      r={hoveredPoint?.date === pt.date ? 4.5 : isDislocated ? 3 : 2}
                      fill={
                        isDislocated
                          ? pt.spread_z_score > 0
                            ? theme.decline
                            : theme.growth
                          : "#f59e0b"
                      }
                      style={{ cursor: "pointer" }}
                      onMouseEnter={() => setHoveredPoint(pt)}
                    />
                  );
                })}
              </svg>
            </div>
          </div>
        </div>
      )}

      {/* Actionable Strategy Directive Card */}
      {data && (
        <div
          style={{
            padding: "16px",
            background: `${actionColor}12`,
            borderRadius: 12,
            border: `1px solid ${actionColor}40`,
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: "1rem", fontWeight: 700, color: actionColor }}>
              🎯 Signal Directive: {data.signal_action.replace("_", " ")}
            </span>
          </div>

          <div style={{ fontSize: "0.85rem", color: theme.textPrimary, lineHeight: 1.5 }}>
            {action === "SHORT_SPREAD" ? (
              <span>
                Spread is overextended at <strong>+{data.spread_z_score.toFixed(2)}σ</strong>. Action: <strong>SELL 1.0 {data.asset_y}</strong> and <strong>BUY {data.kalman_beta.toFixed(3)} {data.asset_x}</strong> to capture mean-reversion compression.
              </span>
            ) : action === "LONG_SPREAD" ? (
              <span>
                Spread is compressed at <strong>{data.spread_z_score.toFixed(2)}σ</strong>. Action: <strong>BUY 1.0 {data.asset_y}</strong> and <strong>SELL {data.kalman_beta.toFixed(3)} {data.asset_x}</strong> to capture mean-reversion expansion.
              </span>
            ) : action === "EXIT" ? (
              <span>
                Spread has crossed the equilibrium mean (Z ≈ 0.0σ). Close out outstanding long/short legs to realize statistical arbitrage alpha.
              </span>
            ) : (
              <span>
                Spread Z-Score ({data.spread_z_score.toFixed(2)}σ) sits within the statistical noise band (|Z| &lt; 2.0σ). No new basket entry recommended.
              </span>
            )}
          </div>

          <div style={{ fontSize: "0.75rem", color: theme.textMuted, marginTop: 4 }}>
            {familyDesc.title} — {familyDesc.formula}. {familyDesc.note}
          </div>
        </div>
      )}
    </div>
  );
};
