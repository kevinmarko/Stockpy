import React, { useState, useEffect, useRef } from "react";
import { api } from "../../api/client";
import { DiffusionStressRequest, DiffusionStressResponse } from "../../api/types";
import { theme } from "../../theme";

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
    <div className="bg-gray-800 p-5 rounded-lg shadow-lg border border-gray-700 font-sans text-gray-200">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4 pb-3 border-b border-gray-700/80">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xl" role="img" aria-label="tornado">🌪️</span>
            <h3 className="text-lg font-bold text-white tracking-wide">
              Generative Diffusion Stress Test: {symbol}
            </h3>
          </div>
          <p className="text-xs text-gray-400 mt-0.5">
            Score-based stochastic reverse SDE diffusion with Classifier-Free Guidance (CFG)
          </p>
        </div>

        <div className="flex items-center gap-2">
          <span
            className="px-2.5 py-1 rounded text-xs font-semibold uppercase tracking-wider"
            style={{
              backgroundColor: `${currentRegimeMeta.color}22`,
              color: currentRegimeMeta.color,
              border: `1px solid ${currentRegimeMeta.color}44`,
            }}
          >
            {currentRegimeMeta.badge}
          </span>
          {data?.trained_windows && (
            <span className="px-2 py-0.5 rounded text-xs bg-gray-700/80 text-gray-300 border border-gray-600">
              {data.trained_windows} Windows Fitted
            </span>
          )}
        </div>
      </div>

      {/* Regime Conditioning Selector */}
      <div className="mb-4">
        <label className="block text-xs font-medium text-gray-300 uppercase tracking-wider mb-2">
          Macro Stress Regime Conditioning
        </label>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-2" role="radiogroup" aria-label="Stress Regime">
          {REGIME_OPTIONS.map((opt) => {
            const isSelected = regime === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                role="radio"
                aria-checked={isSelected}
                onClick={() => setRegime(opt.id)}
                className={`p-2.5 rounded-lg text-left transition-all border ${
                  isSelected
                    ? "bg-gray-700/90 border-accent shadow-sm"
                    : "bg-gray-900/60 border-gray-700/80 hover:bg-gray-700/50 hover:border-gray-600"
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className={`text-xs font-semibold ${isSelected ? "text-white" : "text-gray-300"}`}>
                    {opt.name}
                  </span>
                  <span
                    className="w-2 h-2 rounded-full"
                    style={{ backgroundColor: opt.color }}
                  />
                </div>
                <p className="text-[11px] text-gray-400 line-clamp-2 leading-tight">
                  {opt.description}
                </p>
              </button>
            );
          })}
        </div>
      </div>

      {/* Simulation Controls & Parameters */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-3 mb-5 p-3.5 bg-gray-900/60 rounded-lg border border-gray-700/70">
        <div>
          <label htmlFor="diff-spot-price" className="block text-xs text-gray-400 mb-1">
            Spot Price ($)
          </label>
          <input
            id="diff-spot-price"
            type="number"
            step="0.01"
            className="w-full bg-gray-800 border border-gray-600 p-1.5 rounded text-sm text-white focus:border-accent focus:outline-none"
            value={spotPriceInput}
            onChange={(e) => setSpotPriceInput(parseFloat(e.target.value) || 0)}
          />
        </div>

        <div>
          <label htmlFor="diff-volatility" className="block text-xs text-gray-400 mb-1">
            Volatility (Ann.)
          </label>
          <input
            id="diff-volatility"
            type="number"
            step="0.01"
            className="w-full bg-gray-800 border border-gray-600 p-1.5 rounded text-sm text-white focus:border-accent focus:outline-none"
            value={volatility}
            onChange={(e) => setVolatility(parseFloat(e.target.value) || 0)}
          />
        </div>

        <div>
          <label htmlFor="diff-drift" className="block text-xs text-gray-400 mb-1">
            Drift (Ann.)
          </label>
          <input
            id="diff-drift"
            type="number"
            step="0.01"
            className="w-full bg-gray-800 border border-gray-600 p-1.5 rounded text-sm text-white focus:border-accent focus:outline-none"
            value={drift}
            onChange={(e) => setDrift(parseFloat(e.target.value) || 0)}
          />
        </div>

        <div>
          <label htmlFor="diff-horizon" className="block text-xs text-gray-400 mb-1">
            Horizon (Days)
          </label>
          <input
            id="diff-horizon"
            type="number"
            step="1"
            min="5"
            max="35"
            title="The calibration fix is only verified well-calibrated up to a 30-35 day horizon; the backend now rejects requests outside this range."
            className="w-full bg-gray-800 border border-gray-600 p-1.5 rounded text-sm text-white focus:border-accent focus:outline-none"
            value={horizonDays}
            onChange={(e) => setHorizonDays(parseInt(e.target.value, 10) || 30)}
          />
        </div>

        <div className="lg:col-span-1">
          <div className="flex items-center justify-between mb-1">
            <label htmlFor="diff-guidance-scale" className="text-xs text-gray-400">
              Guidance (s)
            </label>
            <span className="text-xs font-mono font-semibold text-accent">
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
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-sky-400 mt-2"
            value={guidanceScale}
            onChange={(e) => setGuidanceScale(parseFloat(e.target.value))}
          />
        </div>

        <div className="flex items-end">
          <button
            onClick={runSimulation}
            disabled={loading}
            className="w-full bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white font-medium py-1.5 px-3 rounded shadow transition-colors flex items-center justify-center gap-1 text-sm"
          >
            {loading ? (
              <>
                <svg className="animate-spin h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
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
        <div className="p-3 bg-red-950/60 border border-red-800 text-red-300 rounded-lg text-sm mb-4 flex items-center justify-between">
          <span>{error}</span>
          <button onClick={runSimulation} className="text-xs underline text-red-200 hover:text-white">
            Retry
          </button>
        </div>
      )}

      {data && (
        <div className="space-y-4">
          {/* Dynamic Risk Gauge KPI Cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="bg-gray-900/80 p-3.5 rounded-lg border border-red-900/40">
              <div className="text-[11px] uppercase tracking-wider text-gray-400 mb-0.5">Value at Risk (95%)</div>
              <div className="text-lg font-bold text-red-400">
                ${data.VaR_95.toFixed(2)}
              </div>
              <div className="text-[11px] text-gray-500 mt-0.5">
                -{((data.VaR_95 / spotPriceInput) * 100).toFixed(1)}% Max Loss @ 95%
              </div>
            </div>

            <div className="bg-gray-900/80 p-3.5 rounded-lg border border-red-900/40">
              <div className="text-[11px] uppercase tracking-wider text-gray-400 mb-0.5">Conditional VaR (95%)</div>
              <div className="text-lg font-bold text-red-400">
                ${data.CVaR_95.toFixed(2)}
              </div>
              <div className="text-[11px] text-gray-500 mt-0.5">
                -{((data.CVaR_95 / spotPriceInput) * 100).toFixed(1)}% Expected Shortfall
              </div>
            </div>

            <div className="bg-gray-900/80 p-3.5 rounded-lg border border-red-800/60 bg-red-950/20">
              <div className="text-[11px] uppercase tracking-wider text-gray-400 mb-0.5">Value at Risk (99%)</div>
              <div className="text-lg font-bold text-red-300">
                ${(data.VaR_99 ?? data.VaR_95 * 1.3).toFixed(2)}
              </div>
              <div className="text-[11px] text-gray-500 mt-0.5">
                Extreme Tail Cutoff
              </div>
            </div>

            <div className="bg-gray-900/80 p-3.5 rounded-lg border border-red-800/60 bg-red-950/20">
              <div className="text-[11px] uppercase tracking-wider text-gray-400 mb-0.5">Conditional VaR (99%)</div>
              <div className="text-lg font-bold text-red-300">
                ${(data.CVaR_99 ?? data.CVaR_95 * 1.4).toFixed(2)}
              </div>
              <div className="text-[11px] text-gray-500 mt-0.5">
                Severe Crisis Shortfall
              </div>
            </div>
          </div>

          {/* SVG Multi-path Simulation Fan Chart */}
          <div className="bg-gray-900/90 p-4 rounded-lg border border-gray-700/80">
            <div className="flex items-center justify-between mb-2">
              <div>
                <h4 className="text-sm font-semibold text-white">
                  Guided SDE Simulation Cloud ({numPaths} Paths)
                </h4>
                <p className="text-xs text-gray-400">
                  Reverse Ornstein-Uhlenbeck trajectory with {guidanceScale.toFixed(1)}x {currentRegimeMeta.name} steering
                </p>
              </div>
              <div className="flex items-center gap-3 text-xs text-gray-400">
                <span className="flex items-center gap-1">
                  <span className="w-2.5 h-2.5 rounded-sm bg-red-500/20 border border-red-500/60" />
                  10th-90th Cone
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-3 h-0.5 bg-sky-400" />
                  Median Path
                </span>
              </div>
            </div>

            <div className="w-full overflow-x-auto">
              <svg
                viewBox={`0 0 ${svgWidth} ${svgHeight}`}
                className="w-full h-auto max-h-64 select-none"
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
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Crash Probabilities */}
            <div className="bg-gray-900/80 p-4 rounded-lg border border-gray-700/80">
              <h4 className="text-sm font-semibold text-gray-200 mb-3 flex items-center justify-between">
                <span>Crash Probabilities</span>
                <span className="text-xs text-gray-400 font-normal">Empirical Path Frequencies</span>
              </h4>
              <div className="space-y-2.5">
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
                      <div className="flex justify-between text-xs text-gray-300 mb-1">
                        <span className="font-medium">≥ {pctLabel}% Drawdown</span>
                        <span className="font-mono font-semibold" style={{ color: barColor }}>
                          {probPct}%
                        </span>
                      </div>
                      <div className="w-full h-2 bg-gray-800 rounded-full overflow-hidden border border-gray-700">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{
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
            <div className="bg-gray-900/80 p-4 rounded-lg border border-gray-700/80">
              <h4 className="text-sm font-semibold text-gray-200 mb-3 flex items-center justify-between">
                <span>Terminal Price Distribution</span>
                <span className="text-xs text-gray-400 font-normal">Monte Carlo Density</span>
              </h4>
              <div className="h-32 flex flex-col justify-end">
                <div className="flex items-end justify-between px-1 h-full gap-1">
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
                        className="w-full rounded-t transition-all duration-300"
                        style={{
                          height: `${Math.max(4, h)}%`,
                          backgroundColor: isLossBin ? "rgba(239, 68, 68, 0.6)" : "rgba(56, 189, 248, 0.6)",
                          borderTop: `1px solid ${isLossBin ? theme.decline : theme.accent}`,
                        }}
                        title={`Bin ${i + 1} ($${binStart.toFixed(1)} - $${(binStart + binWidth).toFixed(1)}): ${count} paths`}
                      />
                    );
                  })}
                </div>
                <div className="flex justify-between text-[10px] text-gray-500 font-mono mt-1.5 pt-1 border-t border-gray-800">
                  <span>${(Math.min(...terminalPrices, spotPriceInput * 0.5) || 0).toFixed(0)}</span>
                  <span className="text-gray-400">Spot ${spotPriceInput.toFixed(0)}</span>
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
