import React, { useState, useEffect, useMemo } from "react";
import { api } from "../../api/client";
import { theme } from "../../theme";
import type { MarketMakerSimResponse, MarketMakerStepPoint } from "../../api/types";

interface MarketMakerAgentViewProps {
  initialSymbol?: string;
  spotPrice?: number;
  onClose?: () => void;
}

export const MarketMakerAgentView: React.FC<MarketMakerAgentViewProps> = ({
  initialSymbol = "SPY",
  spotPrice = 546.50,
  onClose,
}) => {
  const [symbol, setSymbol] = useState<string>(initialSymbol);
  const [gamma, setGamma] = useState<number>(0.1);
  const [kappa, setKappa] = useState<number>(1.5);
  const [sigma, setSigma] = useState<number>(0.20);
  const [maxInv, setMaxInv] = useState<number>(10);
  const [stepsCount, setStepsCount] = useState<number>(100);

  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [simResult, setSimResult] = useState<MarketMakerSimResponse | null>(null);
  const [hoveredStep, setHoveredStep] = useState<MarketMakerStepPoint | null>(null);

  const activeSymbols = ["SPY", "QQQ", "NVDA", "AAPL", "TSLA", "IWM"];

  const handleSimulate = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.simulateMarketMakerAgent({
        symbol,
        spot_price: spotPrice,
        risk_aversion_gamma: gamma,
        order_flow_intensity_kappa: kappa,
        volatility_sigma: sigma,
        max_inventory: maxInv,
        time_steps: stepsCount,
      });
      setSimResult(res);
      setHoveredStep(res.steps[res.steps.length - 1] || null);
    } catch (err: any) {
      setError(err?.message || "Failed to simulate Market Maker agent");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    handleSimulate();
  }, [symbol]);

  const steps = simResult?.steps || [];

  // Chart min/max scaling
  const { minPrice, maxPrice, minPnl, maxPnl } = useMemo(() => {
    if (!steps.length) return { minPrice: 500, maxPrice: 600, minPnl: -50, maxPnl: 100 };
    const allPrices = steps.flatMap((s) => [s.mid_price, s.reservation_price, s.bid_price, s.ask_price]);
    const allPnls = steps.map((s) => s.pnl);
    return {
      minPrice: Math.min(...allPrices) - 0.5,
      maxPrice: Math.max(...allPrices) + 0.5,
      minPnl: Math.min(0, Math.min(...allPnls) - 10),
      maxPnl: Math.max(50, Math.max(...allPnls) + 10),
    };
  }, [steps]);

  // SVG dimensions
  const svgWidth = 640;
  const svgHeight = 190;
  const padding = { top: 20, right: 25, bottom: 25, left: 55 };
  const chartW = svgWidth - padding.left - padding.right;
  const chartH = svgHeight - padding.top - padding.bottom;

  const getPriceY = (price: number) => {
    const range = maxPrice - minPrice || 1;
    return padding.top + chartH - ((price - minPrice) / range) * chartH;
  };

  const getPnlY = (pnl: number) => {
    const range = maxPnl - minPnl || 10;
    return padding.top + chartH - ((pnl - minPnl) / range) * chartH;
  };

  const getX = (index: number) => {
    const total = steps.length || 1;
    return padding.left + (index / (total - 1 || 1)) * chartW;
  };

  // Paths
  const midPath = useMemo(() => {
    if (!steps.length) return "";
    return steps.map((s, i) => `${i === 0 ? "M" : "L"} ${getX(i).toFixed(1)} ${getPriceY(s.mid_price).toFixed(1)}`).join(" ");
  }, [steps, minPrice, maxPrice]);

  const resPath = useMemo(() => {
    if (!steps.length) return "";
    return steps.map((s, i) => `${i === 0 ? "M" : "L"} ${getX(i).toFixed(1)} ${getPriceY(s.reservation_price).toFixed(1)}`).join(" ");
  }, [steps, minPrice, maxPrice]);

  const bidPath = useMemo(() => {
    if (!steps.length) return "";
    return steps.map((s, i) => `${i === 0 ? "M" : "L"} ${getX(i).toFixed(1)} ${getPriceY(s.bid_price).toFixed(1)}`).join(" ");
  }, [steps, minPrice, maxPrice]);

  const askPath = useMemo(() => {
    if (!steps.length) return "";
    return steps.map((s, i) => `${i === 0 ? "M" : "L"} ${getX(i).toFixed(1)} ${getPriceY(s.ask_price).toFixed(1)}`).join(" ");
  }, [steps, minPrice, maxPrice]);

  const pnlPath = useMemo(() => {
    if (!steps.length) return "";
    return steps.map((s, i) => `${i === 0 ? "M" : "L"} ${getX(i).toFixed(1)} ${getPnlY(s.pnl).toFixed(1)}`).join(" ");
  }, [steps, minPnl, maxPnl]);

  const currentInventory = hoveredStep ? hoveredStep.inventory : (simResult?.final_inventory ?? 0);
  const inventoryPct = Math.max(-100, Math.min(100, (currentInventory / maxInv) * 100));

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
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: "1.25rem", fontWeight: 700 }}>
              🤖 Avellaneda-Stoikov High-Frequency Market Maker Agent
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
              Phase 22
            </span>
          </div>
          <div style={{ fontSize: "0.85rem", color: theme.textSecondary, marginTop: 4 }}>
            High-frequency dynamic inventory quoting: Reservation Price R(s,q,t) = s - qγσ²(T-t) under Poisson execution intensities λ(d) = A e^(-κd).
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            onClick={handleSimulate}
            disabled={loading}
            style={{
              padding: "6px 14px",
              background: theme.growth,
              color: "#000",
              border: "none",
              borderRadius: 8,
              fontSize: "0.85rem",
              fontWeight: 600,
              cursor: loading ? "not-allowed" : "pointer",
            }}
          >
            {loading ? "Simulating..." : "▶ Run MM Agent Sim"}
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

      {/* Ticker & Parameter Tuning Controls */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 12,
          padding: "16px",
          background: theme.surface,
          borderRadius: 12,
          border: `1px solid ${theme.border}`,
        }}
      >
        {/* Symbol Selectors */}
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: "0.85rem", color: theme.textSecondary, marginRight: 4 }}>
            Symbol:
          </span>
          {activeSymbols.map((sym) => {
            const isSelected = symbol === sym;
            return (
              <button
                key={sym}
                onClick={() => setSymbol(sym)}
                style={{
                  padding: "4px 10px",
                  borderRadius: 6,
                  fontSize: "0.85rem",
                  fontWeight: isSelected ? 600 : 400,
                  background: isSelected ? theme.accent : theme.surface2,
                  color: isSelected ? "#000" : theme.textPrimary,
                  border: `1px solid ${isSelected ? theme.accent : theme.border}`,
                  cursor: "pointer",
                }}
              >
                {sym}
              </button>
            );
          })}
        </div>

        {/* Sliders Grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
            gap: 16,
            marginTop: 6,
          }}
        >
          {/* Risk Aversion gamma */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", marginBottom: 4 }}>
              <span style={{ color: theme.textSecondary }}>Risk Aversion (γ):</span>
              <span style={{ fontWeight: 600, color: theme.accent }}>{gamma.toFixed(2)}</span>
            </div>
            <input
              type="range"
              min="0.01"
              max="1.0"
              step="0.01"
              value={gamma}
              onChange={(e) => setGamma(parseFloat(e.target.value))}
              style={{ width: "100%", accentColor: theme.accent }}
            />
          </div>

          {/* Order Flow Intensity kappa */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", marginBottom: 4 }}>
              <span style={{ color: theme.textSecondary }}>Flow Intensity (κ):</span>
              <span style={{ fontWeight: 600, color: theme.growth }}>{kappa.toFixed(1)}</span>
            </div>
            <input
              type="range"
              min="0.5"
              max="5.0"
              step="0.1"
              value={kappa}
              onChange={(e) => setKappa(parseFloat(e.target.value))}
              style={{ width: "100%", accentColor: theme.growth }}
            />
          </div>

          {/* Volatility sigma */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", marginBottom: 4 }}>
              <span style={{ color: theme.textSecondary }}>Asset Volatility (σ):</span>
              <span style={{ fontWeight: 600, color: theme.caution }}>{(sigma * 100).toFixed(0)}%</span>
            </div>
            <input
              type="range"
              min="0.05"
              max="0.80"
              step="0.05"
              value={sigma}
              onChange={(e) => setSigma(parseFloat(e.target.value))}
              style={{ width: "100%", accentColor: theme.caution }}
            />
          </div>

          {/* Max Inventory limit */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", marginBottom: 4 }}>
              <span style={{ color: theme.textSecondary }}>Max Inventory (Q_max):</span>
              <span style={{ fontWeight: 600, color: theme.textPrimary }}>±{maxInv}</span>
            </div>
            <input
              type="range"
              min="2"
              max="25"
              step="1"
              value={maxInv}
              onChange={(e) => setMaxInv(parseInt(e.target.value))}
              style={{ width: "100%" }}
            />
          </div>

          {/* Simulation Steps */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", marginBottom: 4 }}>
              <span style={{ color: theme.textSecondary }}>Simulation Steps:</span>
              <span style={{ fontWeight: 600, color: theme.textPrimary }}>{stepsCount} steps</span>
            </div>
            <input
              type="range"
              min="50"
              max="200"
              step="25"
              value={stepsCount}
              onChange={(e) => setStepsCount(parseInt(e.target.value))}
              style={{ width: "100%" }}
            />
          </div>
        </div>
      </div>

      {error && (
        <div
          style={{
            padding: "12px 16px",
            background: `${theme.decline}15`,
            border: `1px solid ${theme.decline}40`,
            borderRadius: 8,
            color: theme.decline,
            fontSize: "0.85rem",
          }}
        >
          {error}
        </div>
      )}

      {/* KPI Performance Summary Tiles */}
      {simResult && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            gap: 12,
          }}
        >
          {/* Cumulative PnL */}
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
              Total Agent PnL
            </div>
            <div
              style={{
                fontSize: "1.45rem",
                fontWeight: 700,
                color: simResult.final_pnl >= 0 ? theme.growth : theme.decline,
              }}
            >
              {simResult.final_pnl >= 0 ? "+" : ""}${simResult.final_pnl.toFixed(2)}
            </div>
            <div style={{ fontSize: "0.75rem", color: theme.textMuted }}>
              Max Drawdown: ${simResult.max_drawdown.toFixed(2)}
            </div>
          </div>

          {/* Sharpe Ratio */}
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
              Annualized Sharpe
            </div>
            <div style={{ fontSize: "1.45rem", fontWeight: 700, color: theme.accent }}>
              {simResult.sharpe_ratio.toFixed(2)}
            </div>
            <div style={{ fontSize: "0.75rem", color: theme.textMuted }}>
              High-Frequency Inventory Risk-Adjusted
            </div>
          </div>

          {/* Fill Rate & Trades */}
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
              Poisson Fill Rate
            </div>
            <div style={{ fontSize: "1.45rem", fontWeight: 700, color: theme.textPrimary }}>
              {simResult.fill_rate.toFixed(1)}%
            </div>
            <div style={{ fontSize: "0.75rem", color: theme.textMuted }}>
              Total Executions: {simResult.total_trades} orders
            </div>
          </div>

          {/* Average Quoted Spread */}
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
              Average Quoted Spread
            </div>
            <div style={{ fontSize: "1.45rem", fontWeight: 700, color: theme.textPrimary }}>
              ${simResult.avg_spread.toFixed(3)}
            </div>
            <div style={{ fontSize: "0.75rem", color: theme.textMuted }}>
              Capture δ^a + δ^b per round-trip
            </div>
          </div>
        </div>
      )}

      {/* Dynamic Inventory Gauge */}
      {simResult && (
        <div
          style={{
            padding: "16px",
            background: theme.surface,
            borderRadius: 12,
            border: `1px solid ${theme.border}`,
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: "0.9rem", fontWeight: 600 }}>
              📦 Dynamic Inventory Exposure Gauge (q_t ∈ [-{maxInv}, +{maxInv}])
            </span>
            <span
              style={{
                fontSize: "0.85rem",
                fontWeight: 700,
                color:
                  Math.abs(currentInventory) > maxInv * 0.8
                    ? theme.decline
                    : Math.abs(currentInventory) > 0
                    ? theme.caution
                    : theme.growth,
              }}
            >
              {currentInventory > 0 ? `+${currentInventory} Long` : currentInventory < 0 ? `${currentInventory} Short` : "0 Delta Neutral"}
            </span>
          </div>

          {/* Bi-directional horizontal inventory meter */}
          <div style={{ position: "relative", height: 14, background: theme.surface2, borderRadius: 7, overflow: "hidden" }}>
            {/* Center zero line */}
            <div
              style={{
                position: "absolute",
                left: "50%",
                top: 0,
                bottom: 0,
                width: 2,
                background: theme.borderStrong,
                zIndex: 2,
              }}
            />
            {/* Active bar */}
            {inventoryPct >= 0 ? (
              <div
                style={{
                  position: "absolute",
                  left: "50%",
                  top: 0,
                  bottom: 0,
                  width: `${inventoryPct / 2}%`,
                  background: theme.growth,
                  borderRadius: "0 4px 4px 0",
                  transition: "width 0.2s ease",
                }}
              />
            ) : (
              <div
                style={{
                  position: "absolute",
                  right: "50%",
                  top: 0,
                  bottom: 0,
                  width: `${Math.abs(inventoryPct) / 2}%`,
                  background: theme.decline,
                  borderRadius: "4px 0 0 4px",
                  transition: "width 0.2s ease",
                }}
              />
            )}
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", color: theme.textMuted }}>
            <span>-{maxInv} Max Short</span>
            <span>0 Neutral</span>
            <span>+{maxInv} Max Long</span>
          </div>
        </div>
      )}

      {/* Interactive Charts Area */}
      {simResult && steps.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr",
            gap: 16,
          }}
        >
          {/* Chart 1: Bid-Ask Quoting Ladder vs Mid Price & Reservation Price */}
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
                  🪜 Real-Time Quoting Ladder: Mid, Reservation Price & Optimal Spreads
                </span>
                <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
                  Reservation Price R(s,q,t) skews quotes to attract counterparty flow when inventory builds.
                </div>
              </div>
              {hoveredStep && (
                <div style={{ fontSize: "0.8rem", color: theme.accent, fontWeight: 600 }}>
                  Step #{hoveredStep.step}: Mid ${hoveredStep.mid_price.toFixed(2)} | Bid ${hoveredStep.bid_price.toFixed(2)} | Ask ${hoveredStep.ask_price.toFixed(2)}
                </div>
              )}
            </div>

            <div style={{ width: "100%", overflowX: "auto" }}>
              <svg
                viewBox={`0 0 ${svgWidth} ${svgHeight}`}
                style={{ width: "100%", height: "auto", display: "block" }}
              >
                {/* Horizontal Grid lines */}
                {[minPrice, (minPrice + maxPrice) / 2, maxPrice].map((val, idx) => (
                  <g key={idx}>
                    <line
                      x1={padding.left}
                      y1={getPriceY(val)}
                      x2={svgWidth - padding.right}
                      y2={getPriceY(val)}
                      stroke={theme.border}
                      strokeDasharray="3 3"
                    />
                    <text
                      x={padding.left - 6}
                      y={getPriceY(val) + 4}
                      fill={theme.textMuted}
                      fontSize="10"
                      textAnchor="end"
                    >
                      ${val.toFixed(1)}
                    </text>
                  </g>
                ))}

                {/* Quoted Ask Price line (Red/Top) */}
                <path d={askPath} fill="none" stroke={theme.decline} strokeWidth="1.5" strokeDasharray="2 2" />

                {/* Quoted Bid Price line (Green/Bottom) */}
                <path d={bidPath} fill="none" stroke={theme.growth} strokeWidth="1.5" strokeDasharray="2 2" />

                {/* Mid Price (White/Primary) */}
                <path d={midPath} fill="none" stroke={theme.textPrimary} strokeWidth="2" />

                {/* Reservation Price (Accent/Cyan) */}
                <path d={resPath} fill="none" stroke={theme.accent} strokeWidth="1.5" />

                {/* Trade Execution Markers */}
                {steps.map((s, i) => {
                  if (!s.trade_event) return null;
                  const isBuy = s.trade_event === "BUY";
                  return (
                    <circle
                      key={i}
                      cx={getX(i)}
                      cy={getPriceY(isBuy ? s.bid_price : s.ask_price)}
                      r={hoveredStep?.step === s.step ? 5 : 3.5}
                      fill={isBuy ? theme.growth : theme.decline}
                      stroke="#fff"
                      strokeWidth="1"
                      style={{ cursor: "pointer" }}
                      onMouseEnter={() => setHoveredStep(s)}
                    />
                  );
                })}
              </svg>
            </div>

            {/* Legend */}
            <div style={{ display: "flex", gap: 16, fontSize: "0.75rem", color: theme.textSecondary, marginTop: 8, justifyContent: "center" }}>
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ width: 12, height: 2, background: theme.textPrimary }} /> Mid Price
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ width: 12, height: 2, background: theme.accent }} /> Reservation Price R(s,q,t)
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ width: 12, height: 2, background: theme.growth }} /> Optimal Bid
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ width: 12, height: 2, background: theme.decline }} /> Optimal Ask
              </span>
            </div>
          </div>

          {/* Chart 2: Cumulative Mark-to-Market PnL Curve */}
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
                  💰 Cumulative Mark-to-Market PnL Trajectory
                </span>
                <div style={{ fontSize: "0.75rem", color: theme.textSecondary }}>
                  Total PnL = Cash + Inventory × Mid (Spread capture - adverse selection)
                </div>
              </div>
              {hoveredStep && (
                <div
                  style={{
                    fontSize: "0.8rem",
                    fontWeight: 600,
                    color: hoveredStep.pnl >= 0 ? theme.growth : theme.decline,
                  }}
                >
                  Step #{hoveredStep.step}: {hoveredStep.pnl >= 0 ? "+" : ""}${hoveredStep.pnl.toFixed(2)}
                </div>
              )}
            </div>

            <div style={{ width: "100%", overflowX: "auto" }}>
              <svg
                viewBox={`0 0 ${svgWidth} ${svgHeight}`}
                style={{ width: "100%", height: "auto", display: "block" }}
              >
                {/* Zero PnL Reference Line */}
                <line
                  x1={padding.left}
                  y1={getPnlY(0)}
                  x2={svgWidth - padding.right}
                  y2={getPnlY(0)}
                  stroke={theme.borderStrong}
                  strokeWidth="1.5"
                />

                {/* PnL Path */}
                <path d={pnlPath} fill="none" stroke={theme.growth} strokeWidth="2.5" />

                {/* Step hover points */}
                {steps.map((s, i) => (
                  <circle
                    key={i}
                    cx={getX(i)}
                    cy={getPnlY(s.pnl)}
                    r={hoveredStep?.step === s.step ? 4 : 1.5}
                    fill={hoveredStep?.step === s.step ? "#fff" : theme.growth}
                    style={{ cursor: "pointer" }}
                    onMouseEnter={() => setHoveredStep(s)}
                  />
                ))}
              </svg>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
