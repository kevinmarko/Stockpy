import React, { useState, useEffect, useRef } from "react";
import { api } from "../../api/client";
import { DiffusionStressRequest, DiffusionStressResponse } from "../../api/types";
import { theme, alpha } from "../../theme";

interface Props {
  symbol: string;
  spotPrice?: number;
}

type RegimeType = "vol_shock" | "credit_freeze" | "stagflation" | "liquidity_squeeze" | "unconditional";

interface RegimeOption {
  id: RegimeType;
  name: string;
  badge: string;
  description: string;
  color: string;
}

const REGIME_OPTIONS: RegimeOption[] = [
  {
    id: "vol_shock",
    name: "Vol Shock (VIX > 40)",
    badge: "HIGH VOL",
    description: "Tail vol spike & extreme return dispersion",
    color: theme.decline,
  },
  {
    id: "credit_freeze",
    name: "Credit Freeze (High OAS)",
    badge: "CREDIT CRISIS",
    description: "Widening credit spreads & persistent drawdown",
    color: "#f97316",
  },
  {
    id: "stagflation",
    name: "Stagflation",
    badge: "MACRO STRESS",
    description:
      "Persistent negative drift + elevated rate vol. Labeled from real FRED " +
      "data -- market-implied inflation expectations (10-Year Breakeven, " +
      "T10YIE) elevated above their own trailing range, combined with a " +
      "rising unemployment trend -- rather than a single hand-picked dated " +
      "historical window like Credit Freeze/Liquidity Squeeze below. Caveat: " +
      "this is a disclosed heuristic, not a rigorously validated regime " +
      "detector, so treat any training examples it labels as approximate.",
    color: theme.caution,
  },
  {
    id: "liquidity_squeeze",
    name: "Liquidity Squeeze",
    badge: "GAP DOWN",
    description: "Orderbook evaporation & sharp downward jumps",
    color: "#a855f7",
  },
  {
    id: "unconditional",
    name: "Unconditional",
    badge: "BASELINE",
    description: "Standard historical return diffusion distribution",
    color: theme.accent,
  },
];

export const GenerativeDiffusionStressView: React.FC<Props> = ({ symbol, spotPrice }) => {
  const [data, setData] = useState<DiffusionStressResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [spotPriceInput, setSpotPriceInput] = useState(spotPrice ?? 100);
  const [regime, setRegime] = useState<RegimeType>("vol_shock");
  const [guidanceScale, setGuidanceScale] = useState<number>(2.0);
  const [drift, setDrift] = useState<number>(0);
  const [volatility, setVolatility] = useState<number>(0.2);
  const [horizonDays, setHorizonDays] = useState<number>(30);

  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // Update spot price input if prop changes
  useEffect(() => {
    if (spotPrice !== undefined) {
      setSpotPriceInput(spotPrice);
    }
  }, [spotPrice]);

  const runSimulation = async () => {
    setLoading(true);
    setError(null);
    try {
      const req: DiffusionStressRequest = {
        symbol,
        spot_price: spotPriceInput,
        volatility,
        drift,
        num_paths: 1000,
        horizon: horizonDays,
        regime,
        guidance_scale: guidanceScale,
      };
      const res = await api.runDiffusionStressTest(req);
      if (isMountedRef.current) {
        setData(res);
      }
    } catch (e: unknown) {
      if (isMountedRef.current) {
        const msg = e instanceof Error ? e.message : "Failed to run diffusion stress simulation";
        setError(msg);
      }
    } finally {
      if (isMountedRef.current) {
        setLoading(false);
      }
    }
  };

  // Derived metrics from simulated paths
  const paths = data?.paths ?? [];
  const numPaths = paths.length;
  const numSteps = paths[0]?.length ?? 0;

  // Calculate percentile bands across time steps for the fan chart
  const pathStats = React.useMemo(() => {
    if (numPaths === 0 || numSteps === 0) return null;

    const minPerStep: number[] = [];
    const maxPerStep: number[] = [];
    const medianPerStep: number[] = [];
    const q10PerStep: number[] = [];
    const q90PerStep: number[] = [];

    for (let t = 0; t < numSteps; t++) {
      const stepValues = paths.map((p) => p[t] ?? spotPriceInput).sort((a, b) => a - b);
      minPerStep.push(stepValues[0]);
      maxPerStep.push(stepValues[stepValues.length - 1]);

      const midIdx = Math.floor(stepValues.length / 2);
      medianPerStep.push(
        stepValues.length % 2 === 0
          ? (stepValues[midIdx - 1] + stepValues[midIdx]) / 2
          : stepValues[midIdx]
      );

      const q10Idx = Math.floor(stepValues.length * 0.1);
      const q90Idx = Math.floor(stepValues.length * 0.9);
      q10PerStep.push(stepValues[q10Idx] ?? stepValues[0]);
      q90PerStep.push(stepValues[q90Idx] ?? stepValues[stepValues.length - 1]);
    }

    const allPrices = paths.flat();
    const globalMin = Math.min(...allPrices);
    const globalMax = Math.max(...allPrices);

    return {
      minPerStep,
      maxPerStep,
      medianPerStep,
      q10PerStep,
      q90PerStep,
      globalMin,
      globalMax,
    };
  }, [paths, numPaths, numSteps, spotPriceInput]);

  // Terminal prices & crash probabilities
  const terminalPrices = data ? data.paths.map((p) => p[p.length - 1]) : [];
  const crashThresholds = [-0.05, -0.1, -0.2, -0.3];
  const crashProbabilities = data
    ? crashThresholds.map((pct) => {
        const threshold = data.paths[0]?.[0] ?? spotPriceInput;
        const count = terminalPrices.filter((v) => v <= threshold * (1 + pct)).length;
        return { pct, prob: terminalPrices.length > 0 ? count / terminalPrices.length : 0 };
      })
    : [];

  // SVG Chart Dimensions
  const svgWidth = 560;
  const svgHeight = 220;
  const padLeft = 55;
  const padRight = 30;
  const padTop = 20;
  const padBottom = 30;
  const chartW = svgWidth - padLeft - padRight;
  const chartH = svgHeight - padTop - padBottom;

  const yMin = pathStats ? Math.max(0.01, pathStats.globalMin * 0.95) : spotPriceInput * 0.7;
  const yMax = pathStats ? Math.max(pathStats.globalMax * 1.05, spotPriceInput * 1.1) : spotPriceInput * 1.3;

  const getX = (stepIdx: number) => padLeft + (stepIdx / Math.max(1, numSteps - 1)) * chartW;
  const getY = (price: number) => {
    const clamped = Math.max(yMin, Math.min(yMax, price));
    return padTop + chartH - ((clamped - yMin) / Math.max(0.001, yMax - yMin)) * chartH;
  };

  // Build polygon path for 10th-90th confidence band
  let confidenceBandPath = "";
  let medianPathString = "";
  if (pathStats && numSteps > 1) {
    const topPoints = pathStats.q90PerStep.map((val, idx) => `${getX(idx).toFixed(1)},${getY(val).toFixed(1)}`);
    const bottomPoints = pathStats.q10PerStep
      .slice()
      .reverse()
      .map((val, i) => {
        const origIdx = numSteps - 1 - i;
        return `${getX(origIdx).toFixed(1)},${getY(val).toFixed(1)}`;
      });
    confidenceBandPath = `M ${topPoints.join(" L ")} L ${bottomPoints.join(" L ")} Z`;
    medianPathString = `M ${pathStats.medianPerStep.map((val, idx) => `${getX(idx).toFixed(1)},${getY(val).toFixed(1)}`).join(" L ")}`;
  }

  // Active regime option metadata
  const currentRegimeMeta = REGIME_OPTIONS.find((r) => r.id === regime) ?? REGIME_OPTIONS[0];

  return (
    <div className="card card-pad">
      {/* Header */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "var(--s-3)",
          marginBottom: "var(--s-4)",
          paddingBottom: "var(--s-3)",
          borderBottom: `1px solid ${theme.border}`,
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
            <span style={{ fontSize: "1.2rem" }} role="img" aria-label="tornado">🌪️</span>
            <h3 style={{ fontSize: "1.05rem", fontWeight: 700, color: theme.textPrimary, letterSpacing: "0.01em", margin: 0 }}>
              Generative Diffusion Stress Test: {symbol}
            </h3>
          </div>
          <p style={{ fontSize: "0.75rem", color: theme.textMuted, marginTop: 2, marginBottom: 0 }}>
            Score-based stochastic reverse SDE diffusion with Classifier-Free Guidance (CFG)
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <span
            style={{
              padding: "4px 10px",
              borderRadius: "var(--r-xs)",
              fontSize: "0.7rem",
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              backgroundColor: alpha(currentRegimeMeta.color, "20"),
              color: currentRegimeMeta.color,
              border: `1px solid ${alpha(currentRegimeMeta.color, "50")}`,
            }}
          >
            {currentRegimeMeta.badge}
          </span>
          {data?.trained_windows && (
            <span className="chip">{data.trained_windows} Windows Fitted</span>
          )}
        </div>
      </div>

      {/* Regime Conditioning Selector */}
      <div style={{ marginBottom: "var(--s-4)" }}>
        <label
          style={{
            display: "block",
            fontSize: "0.7rem",
            fontWeight: 600,
            color: theme.textSecondary,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            marginBottom: "var(--s-2)",
          }}
        >
          Macro Stress Regime Conditioning
        </label>
        <div
          style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "var(--s-2)" }}
          role="radiogroup"
          aria-label="Stress Regime"
        >
          {REGIME_OPTIONS.map((opt) => {
            const isSelected = regime === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                role="radio"
                aria-checked={isSelected}
                onClick={() => setRegime(opt.id)}
                style={{
                  padding: "var(--s-2-5)",
                  borderRadius: "var(--r-md)",
                  textAlign: "left",
                  border: `1px solid ${isSelected ? theme.accent : theme.border}`,
                  background: isSelected ? theme.surface3 : theme.base,
                  cursor: "pointer",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ fontSize: "0.75rem", fontWeight: 600, color: isSelected ? theme.textPrimary : theme.textSecondary }}>
                    {opt.name}
                  </span>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", backgroundColor: opt.color, flexShrink: 0 }} />
                </div>
                <p
                  style={{
                    fontSize: "0.68rem",
                    color: theme.textMuted,
                    lineHeight: 1.3,
                    margin: 0,
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                  }}
                >
                  {opt.description}
                </p>
              </button>
            );
          })}
        </div>
      </div>

      {/* Simulation Controls & Parameters */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: "var(--s-3)",
          marginBottom: "var(--s-5)",
          padding: "var(--s-3-5)",
          background: theme.base,
          borderRadius: "var(--r-md)",
          border: `1px solid ${theme.border}`,
        }}
      >
        <div>
          <label htmlFor="diff-spot-price" style={{ display: "block", fontSize: "0.7rem", color: theme.textMuted, marginBottom: "var(--s-1)" }}>
            Spot Price ($)
          </label>
          <input
            id="diff-spot-price"
            type="number"
            step="0.01"
            className="input"
            value={spotPriceInput}
            onChange={(e) => setSpotPriceInput(parseFloat(e.target.value) || 0)}
          />
        </div>

        <div>
          <label htmlFor="diff-volatility" style={{ display: "block", fontSize: "0.7rem", color: theme.textMuted, marginBottom: "var(--s-1)" }}>
            Volatility (Ann.)
          </label>
          <input
            id="diff-volatility"
            type="number"
            step="0.01"
            className="input"
            value={volatility}
            onChange={(e) => setVolatility(parseFloat(e.target.value) || 0)}
          />
        </div>

        <div>
          <label htmlFor="diff-drift" style={{ display: "block", fontSize: "0.7rem", color: theme.textMuted, marginBottom: "var(--s-1)" }}>
            Drift (Ann.)
          </label>
          <input
            id="diff-drift"
            type="number"
            step="0.01"
            className="input"
            value={drift}
            onChange={(e) => setDrift(parseFloat(e.target.value) || 0)}
          />
        </div>

        <div>
          <label htmlFor="diff-horizon" style={{ display: "block", fontSize: "0.7rem", color: theme.textMuted, marginBottom: "var(--s-1)" }}>
            Horizon (Days)
          </label>
          <input
            id="diff-horizon"
            type="number"
            step="1"
            min="5"
            max="35"
            title="The calibration fix is only verified well-calibrated up to a 30-35 day horizon; the backend now rejects requests outside this range."
            className="input"
            value={horizonDays}
            onChange={(e) => setHorizonDays(parseInt(e.target.value, 10) || 30)}
          />
        </div>

        <div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--s-1)" }}>
            <label htmlFor="diff-guidance-scale" style={{ fontSize: "0.7rem", color: theme.textMuted }}>
              Guidance (s)
            </label>
            <span style={{ fontSize: "0.7rem", fontFamily: "monospace", fontWeight: 600, color: theme.accent }}>
              {guidanceScale.toFixed(1)}x
            </span>
          </div>
          <input
            id="diff-guidance-scale"
            type="range"
            min="0.0"
            max="5.0"
            step="0.1"
            aria-label="Classifier-Free Guidance Scale"
            style={{ width: "100%", marginTop: "var(--s-2)", accentColor: theme.accent, cursor: "pointer" }}
            value={guidanceScale}
            onChange={(e) => setGuidanceScale(parseFloat(e.target.value))}
          />
        </div>

        <div style={{ display: "flex", alignItems: "flex-end" }}>
          <button
            onClick={runSimulation}
            disabled={loading}
            style={{
              width: "100%",
              background: theme.decline,
              color: "#fff",
              fontWeight: 600,
              padding: "8px 12px",
              borderRadius: "var(--r-sm)",
              border: "none",
              boxShadow: "var(--shadow-card)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "var(--s-1-5)",
              fontSize: "var(--t-body)",
              cursor: loading ? "not-allowed" : "pointer",
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? (
              <>
                {/* .icon-spin (index.css) replaces the dead Tailwind `animate-spin`
                    -- this Tailwind-free webapp generated no CSS for it, so the
                    "Running..." state never actually spun. */}
                <svg className="icon-spin" style={{ height: 16, width: 16, color: "#fff" }} fill="none" viewBox="0 0 24 24">
                  <circle style={{ opacity: 0.25 }} cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path style={{ opacity: 0.75 }} fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                <span>Running...</span>
              </>
            ) : (
              <span>Run Stress Test</span>
            )}
          </button>
        </div>
      </div>

      {error && (
        <div
          style={{
            padding: "var(--s-3)",
            background: alpha(theme.decline, "15"),
            border: `1px solid ${alpha(theme.decline, "40")}`,
            color: theme.decline,
            borderRadius: "var(--r-md)",
            fontSize: "var(--t-body)",
            marginBottom: "var(--s-4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "var(--s-2)",
          }}
        >
          <span>{error}</span>
          <button
            onClick={runSimulation}
            style={{ fontSize: "0.7rem", textDecoration: "underline", color: theme.decline, background: "none", border: "none", cursor: "pointer", padding: 0 }}
          >
            Retry
          </button>
        </div>
      )}

      {data && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
          {/* Dynamic Risk Gauge KPI Cards */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "var(--s-3)" }}>
            <div style={{ background: theme.base, padding: "var(--s-3-5)", borderRadius: "var(--r-md)", border: `1px solid ${alpha(theme.decline, "25")}` }}>
              <div style={{ fontSize: "0.68rem", textTransform: "uppercase", letterSpacing: "0.03em", color: theme.textMuted, marginBottom: 2 }}>Value at Risk (95%)</div>
              <div style={{ fontSize: "1.1rem", fontWeight: 700, color: theme.decline }}>
                ${data.VaR_95.toFixed(2)}
              </div>
              <div style={{ fontSize: "0.68rem", color: theme.textMuted, marginTop: 2 }}>
                -{((data.VaR_95 / spotPriceInput) * 100).toFixed(1)}% Max Loss @ 95%
              </div>
            </div>

            <div style={{ background: theme.base, padding: "var(--s-3-5)", borderRadius: "var(--r-md)", border: `1px solid ${alpha(theme.decline, "25")}` }}>
              <div style={{ fontSize: "0.68rem", textTransform: "uppercase", letterSpacing: "0.03em", color: theme.textMuted, marginBottom: 2 }}>Conditional VaR (95%)</div>
              <div style={{ fontSize: "1.1rem", fontWeight: 700, color: theme.decline }}>
                ${data.CVaR_95.toFixed(2)}
              </div>
              <div style={{ fontSize: "0.68rem", color: theme.textMuted, marginTop: 2 }}>
                -{((data.CVaR_95 / spotPriceInput) * 100).toFixed(1)}% Expected Shortfall
              </div>
            </div>

            <div style={{ background: alpha(theme.decline, "08"), padding: "var(--s-3-5)", borderRadius: "var(--r-md)", border: `1px solid ${alpha(theme.decline, "35")}` }}>
              <div style={{ fontSize: "0.68rem", textTransform: "uppercase", letterSpacing: "0.03em", color: theme.textMuted, marginBottom: 2 }}>Value at Risk (99%)</div>
              <div style={{ fontSize: "1.1rem", fontWeight: 700, color: theme.decline }}>
                ${(data.VaR_99 ?? data.VaR_95 * 1.3).toFixed(2)}
              </div>
              <div style={{ fontSize: "0.68rem", color: theme.textMuted, marginTop: 2 }}>
                Extreme Tail Cutoff
              </div>
            </div>

            <div style={{ background: alpha(theme.decline, "08"), padding: "var(--s-3-5)", borderRadius: "var(--r-md)", border: `1px solid ${alpha(theme.decline, "35")}` }}>
              <div style={{ fontSize: "0.68rem", textTransform: "uppercase", letterSpacing: "0.03em", color: theme.textMuted, marginBottom: 2 }}>Conditional VaR (99%)</div>
              <div style={{ fontSize: "1.1rem", fontWeight: 700, color: theme.decline }}>
                ${(data.CVaR_99 ?? data.CVaR_95 * 1.4).toFixed(2)}
              </div>
              <div style={{ fontSize: "0.68rem", color: theme.textMuted, marginTop: 2 }}>
                Severe Crisis Shortfall
              </div>
            </div>
          </div>

          {/* SVG Multi-path Simulation Fan Chart */}
          <div style={{ background: theme.base, padding: "var(--s-4)", borderRadius: "var(--r-md)", border: `1px solid ${theme.border}` }}>
            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: "var(--s-2)", marginBottom: "var(--s-2)" }}>
              <div>
                <h4 style={{ fontSize: "0.85rem", fontWeight: 600, color: theme.textPrimary, margin: 0 }}>
                  Guided SDE Simulation Cloud ({numPaths} Paths)
                </h4>
                <p style={{ fontSize: "0.75rem", color: theme.textMuted, margin: 0, marginTop: 2 }}>
                  Reverse Ornstein-Uhlenbeck trajectory with {guidanceScale.toFixed(1)}x {currentRegimeMeta.name} steering
                </p>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "var(--s-3)", fontSize: "0.7rem", color: theme.textMuted }}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 2, background: alpha(theme.decline, "20"), border: `1px solid ${alpha(theme.decline, "60")}` }} />
                  10th-90th Cone
                </span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <span style={{ width: 12, height: 2, background: theme.accent, display: "inline-block" }} />
                  Median Path
                </span>
              </div>
            </div>

            <div style={{ width: "100%", overflowX: "auto" }}>
              <svg
                viewBox={`0 0 ${svgWidth} ${svgHeight}`}
                style={{ width: "100%", height: "auto", maxHeight: 256, userSelect: "none" }}
                role="img"
                aria-label="Simulation Paths Chart"
              >
                {/* Horizontal Gridlines and Y-Labels */}
                {[0, 0.25, 0.5, 0.75, 1.0].map((ratio) => {
                  const price = yMin + ratio * (yMax - yMin);
                  const y = getY(price);
                  return (
                    <g key={ratio}>
                      <line
                        x1={padLeft}
                        y1={y}
                        x2={svgWidth - padRight}
                        y2={y}
                        stroke={theme.chartGrid}
                        strokeDasharray="3 3"
                      />
                      <text
                        x={padLeft - 8}
                        y={y + 4}
                        fill={theme.textMuted}
                        fontSize="10"
                        textAnchor="end"
                        fontFamily="monospace"
                      >
                        ${price.toFixed(1)}
                      </text>
                    </g>
                  );
                })}

                {/* Spot Price Baseline */}
                <line
                  x1={padLeft}
                  y1={getY(spotPriceInput)}
                  x2={svgWidth - padRight}
                  y2={getY(spotPriceInput)}
                  stroke={theme.textSecondary}
                  strokeDasharray="4 4"
                  strokeWidth="1.2"
                />
                <text
                  x={svgWidth - padRight + 4}
                  y={getY(spotPriceInput) + 3}
                  fill={theme.textSecondary}
                  fontSize="9"
                  fontFamily="monospace"
                >
                  Spot
                </text>

                {/* Confidence Interval Shaded Area */}
                {confidenceBandPath && (
                  <path
                    d={confidenceBandPath}
                    fill="rgba(239, 68, 68, 0.15)"
                    stroke="rgba(239, 68, 68, 0.4)"
                    strokeWidth="0.8"
                  />
                )}

                {/* Individual Simulated Spaghetti Paths (sampled subset) */}
                {paths.slice(0, 35).map((path, pIdx) => {
                  const pts = path.map((val, sIdx) => `${getX(sIdx).toFixed(1)},${getY(val).toFixed(1)}`);
                  return (
                    <path
                      key={pIdx}
                      d={`M ${pts.join(" L ")}`}
                      fill="none"
                      stroke={currentRegimeMeta.color}
                      strokeWidth="0.75"
                      strokeOpacity="0.18"
                    />
                  );
                })}

                {/* Median Trajectory Path */}
                {medianPathString && (
                  <path
                    d={medianPathString}
                    fill="none"
                    stroke={theme.accent}
                    strokeWidth="2.2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                )}

                {/* X-Axis Day Labels */}
                {[0, Math.floor(horizonDays / 2), horizonDays].map((day, idx) => {
                  const stepIdx = idx === 0 ? 0 : idx === 1 ? Math.floor(numSteps / 2) : numSteps - 1;
                  const x = getX(stepIdx);
                  return (
                    <text
                      key={day}
                      x={x}
                      y={svgHeight - 10}
                      fill={theme.textMuted}
                      fontSize="10"
                      textAnchor={idx === 0 ? "start" : idx === 2 ? "end" : "middle"}
                    >
                      Day {day}
                    </text>
                  );
                })}
              </svg>
            </div>
          </div>

          {/* Bottom Breakdown: Crash Probabilities & Terminal Distribution */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "var(--s-4)" }}>
            {/* Crash Probabilities */}
            <div style={{ background: theme.base, padding: "var(--s-4)", borderRadius: "var(--r-md)", border: `1px solid ${theme.border}` }}>
              <h4 style={{ fontSize: "0.85rem", fontWeight: 600, color: theme.textSecondary, marginBottom: "var(--s-3)", marginTop: 0, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span>Crash Probabilities</span>
                <span style={{ fontSize: "0.7rem", color: theme.textMuted, fontWeight: 400 }}>Empirical Path Frequencies</span>
              </h4>
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2-5)" }}>
                {crashProbabilities.map(({ pct, prob }) => {
                  const pctLabel = Math.abs(pct * 100).toFixed(0);
                  const probPct = (prob * 100).toFixed(1);
                  const barColor =
                    Math.abs(pct) >= 0.2
                      ? theme.decline
                      : Math.abs(pct) >= 0.1
                      ? theme.caution
                      : "#38bdf8";

                  return (
                    <div key={pct}>
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.72rem", color: theme.textSecondary, marginBottom: 4 }}>
                        <span style={{ fontWeight: 600 }}>≥ {pctLabel}% Drawdown</span>
                        <span style={{ fontFamily: "monospace", fontWeight: 700, color: barColor }}>
                          {probPct}%
                        </span>
                      </div>
                      <div style={{ width: "100%", height: 8, background: theme.surface2, borderRadius: "var(--r-pill)", overflow: "hidden", border: `1px solid ${theme.border}` }}>
                        <div
                          style={{
                            height: "100%",
                            borderRadius: "var(--r-pill)",
                            transition: "width 0.4s ease",
                            width: `${Math.min(100, Math.max(0, prob * 100))}%`,
                            backgroundColor: barColor,
                          }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Terminal Price Distribution */}
            <div style={{ background: theme.base, padding: "var(--s-4)", borderRadius: "var(--r-md)", border: `1px solid ${theme.border}` }}>
              <h4 style={{ fontSize: "0.85rem", fontWeight: 600, color: theme.textSecondary, marginBottom: "var(--s-3)", marginTop: 0, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span>Terminal Price Distribution</span>
                <span style={{ fontSize: "0.7rem", color: theme.textMuted, fontWeight: 400 }}>Monte Carlo Density</span>
              </h4>
              <div style={{ height: 128, display: "flex", flexDirection: "column", justifyContent: "flex-end" }}>
                <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", padding: "0 4px", height: "100%", gap: 4 }}>
                  {Array.from({ length: 20 }).map((_, i) => {
                    const min = Math.min(...terminalPrices, spotPriceInput * 0.5);
                    const max = Math.max(...terminalPrices, spotPriceInput * 1.5);
                    const binWidth = (max - min) / 20 || 1;
                    const binStart = min + i * binWidth;
                    const count = terminalPrices.filter((v) => v >= binStart && v < binStart + binWidth).length;
                    const h = Math.min(100, Math.max(0, (count / Math.max(1, terminalPrices.length)) * 400));
                    const isLossBin = binStart + binWidth < spotPriceInput;

                    return (
                      <div
                        key={i}
                        style={{
                          width: "100%",
                          borderTopLeftRadius: 3,
                          borderTopRightRadius: 3,
                          transition: "height 0.3s ease",
                          height: `${Math.max(4, h)}%`,
                          backgroundColor: isLossBin ? "rgba(239, 68, 68, 0.6)" : "rgba(56, 189, 248, 0.6)",
                          borderTop: `1px solid ${isLossBin ? theme.decline : theme.accent}`,
                        }}
                        title={`Bin ${i + 1} ($${binStart.toFixed(1)} - $${(binStart + binWidth).toFixed(1)}): ${count} paths`}
                      />
                    );
                  })}
                </div>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: "0.62rem",
                    color: theme.textMuted,
                    fontFamily: "monospace",
                    marginTop: 6,
                    paddingTop: 4,
                    borderTop: `1px solid ${theme.border}`,
                  }}
                >
                  <span>${(Math.min(...terminalPrices, spotPriceInput * 0.5) || 0).toFixed(0)}</span>
                  <span style={{ color: theme.textSecondary }}>Spot ${spotPriceInput.toFixed(0)}</span>
                  <span>${(Math.max(...terminalPrices, spotPriceInput * 1.5) || 0).toFixed(0)}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
