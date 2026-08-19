import { useState, useEffect, useRef } from "react";
import { api } from "../../api/client";
import {
  HrpCvarOptimizeResponse,
  HrpCvarClusterNode,
} from "../../api/types";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { theme, sectorColor } from "../../theme";

export interface HrpPortfolioOptimizerViewProps {
  symbols?: string[];
  initialCurrentWeights?: Record<string, number>;
}

const DEFAULT_SYMBOLS = ["AAPL", "MSFT", "NVDA", "JPM", "V"];

const DEFAULT_SECTOR_MAP: Record<string, string> = {
  AAPL: "Tech",
  MSFT: "Tech",
  NVDA: "Tech",
  JPM: "Financials",
  V: "Financials",
  UNH: "Healthcare",
  JNJ: "Healthcare",
  AMZN: "Consumer",
  PG: "Consumer",
  XOM: "Energy",
  CVX: "Energy",
};

const DEFAULT_ASSET_BETAS: Record<string, number> = {
  AAPL: 1.15,
  MSFT: 1.05,
  NVDA: 1.65,
  JPM: 0.95,
  V: 0.85,
  UNH: 0.70,
  JNJ: 0.60,
  AMZN: 1.25,
  PG: 0.55,
  XOM: 0.80,
  CVX: 0.75,
};

const DEFAULT_SECTOR_CAPS: Record<string, number> = {
  Tech: 0.35,
  Financials: 0.30,
  Healthcare: 0.25,
  Consumer: 0.25,
  Energy: 0.20,
};

export function HrpPortfolioOptimizerView({
  symbols = DEFAULT_SYMBOLS,
  initialCurrentWeights,
}: HrpPortfolioOptimizerViewProps) {
  const activeSymbols = symbols && symbols.length > 0 ? symbols : DEFAULT_SYMBOLS;

  // Optimization Parameters
  const [lambdaTurnover, setLambdaTurnover] = useState<number>(0.05);
  const [maxAssetWeight, setMaxAssetWeight] = useState<number>(0.35);
  const [betaMin, setBetaMin] = useState<number>(0.70);
  const [betaMax, setBetaMax] = useState<number>(1.30);
  const [sectorCaps, setSectorCaps] = useState<Record<string, number>>(DEFAULT_SECTOR_CAPS);

  // Incumbent weights w0
  const [currentWeights] = useState<Record<string, number>>(() => {
    if (initialCurrentWeights) return initialCurrentWeights;
    const n = activeSymbols.length;
    const eqW = 1 / Math.max(1, n);
    const initial: Record<string, number> = {};
    activeSymbols.forEach((s) => {
      initial[s] = Number(eqW.toFixed(3));
    });
    return initial;
  });

  const [data, setData] = useState<HrpCvarOptimizeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const handleOptimize = async () => {
    if (activeSymbols.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.optimizeHrpCvar({
        symbols: activeSymbols,
        current_weights: currentWeights,
        lambda_turnover: lambdaTurnover,
        max_asset_weight: maxAssetWeight,
        sector_caps: sectorCaps,
        target_beta_range: [betaMin, betaMax],
        sector_map: DEFAULT_SECTOR_MAP,
        asset_betas: DEFAULT_ASSET_BETAS,
      });
      if (isMountedRef.current) {
        setData(res);
      }
    } catch (e: any) {
      if (isMountedRef.current) {
        setError(e.message || "Optimization failed");
      }
    } finally {
      if (isMountedRef.current) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    handleOptimize();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSectorCapChange = (sector: string, val: number) => {
    setSectorCaps((prev) => ({
      ...prev,
      [sector]: val,
    }));
  };

  // Prepare chart comparison data: Symbol, Incumbent w0, Proposed w*, Delta
  const chartData = activeSymbols.map((sym) => {
    const inc = currentWeights[sym] ?? 0;
    const propAlloc = data?.allocations.find((a) => a.symbol === sym);
    const prop = propAlloc ? propAlloc.weight : 0;
    return {
      symbol: sym,
      incumbent: Number((inc * 100).toFixed(1)),
      proposed: Number((prop * 100).toFixed(1)),
      delta: Number(((prop - inc) * 100).toFixed(1)),
      sector: DEFAULT_SECTOR_MAP[sym] || "Other",
    };
  });

  return (
    <div
      role="region"
      aria-label="HRP Portfolio Optimizer Desk"
      style={{
        background: theme.surface,
        border: `1px solid ${theme.border}`,
        borderRadius: 10,
        padding: 20,
        display: "flex",
        flexDirection: "column",
        gap: 20,
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0, color: theme.textPrimary }}>
              Turnover-Regularized HRP-CVaR Optimizer
            </h2>
            <span
              style={{
                fontSize: 11,
                padding: "2px 8px",
                borderRadius: 12,
                background: "rgba(56, 189, 248, 0.15)",
                color: theme.accent,
                fontWeight: 600,
                border: `1px solid rgba(56, 189, 248, 0.3)`,
              }}
            >
              Phase 35 Engine
            </span>
          </div>
          <p style={{ fontSize: 13, color: theme.textSecondary, margin: "4px 0 0 0" }}>
            Factor-neutral hierarchical risk parity with L1 turnover regularizer and sector concentration bounds
          </p>
        </div>

        <button
          onClick={handleOptimize}
          disabled={loading || activeSymbols.length === 0}
          aria-label="Run HRP CVaR Optimization"
          data-testid="run-optimize-btn"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "8px 18px",
            background: loading ? theme.surface3 : theme.accent,
            color: loading ? theme.textMuted : "#000",
            border: "none",
            borderRadius: 6,
            cursor: loading || activeSymbols.length === 0 ? "not-allowed" : "pointer",
            fontWeight: 700,
            fontSize: 13,
            transition: "all 0.2s ease",
          }}
        >
          {loading ? (
            <>
              <span style={{ display: "inline-block", animation: "spin 1s linear infinite" }}>⚙️</span>
              <span>Rebalancing...</span>
            </>
          ) : (
            <>
              <span>⚡</span>
              <span>Run Optimization</span>
            </>
          )}
        </button>
      </div>

      {error && (
        <div
          role="alert"
          style={{
            background: "rgba(239, 68, 68, 0.12)",
            border: `1px solid rgba(239, 68, 68, 0.3)`,
            borderRadius: 6,
            padding: "10px 14px",
            color: theme.decline,
            fontSize: 13,
          }}
        >
          <strong>Optimization Error:</strong> {error}
        </div>
      )}

      {/* Control Console */}
      <div
        style={{
          background: theme.surface2,
          border: `1px solid ${theme.border}`,
          borderRadius: 8,
          padding: 16,
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 600, color: theme.textSecondary, textTransform: "uppercase", letterSpacing: 0.5 }}>
          Rebalancing Controls & Constraints
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 16 }}>
          {/* Turnover Penalty Slider */}
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <label htmlFor="turnover-slider" style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>
                Turnover Penalty (λ<sub>turnover</sub>)
              </label>
              <span data-testid="turnover-lambda-val" style={{ fontSize: 13, fontFamily: "monospace", color: theme.accent, fontWeight: 700 }}>
                {lambdaTurnover.toFixed(2)}
              </span>
            </div>
            <input
              id="turnover-slider"
              type="range"
              min="0.00"
              max="0.50"
              step="0.01"
              value={lambdaTurnover}
              onChange={(e) => setLambdaTurnover(parseFloat(e.target.value))}
              aria-label="Turnover Penalty Slider"
              aria-valuenow={lambdaTurnover}
              aria-valuemin={0}
              aria-valuemax={0.5}
              style={{ width: "100%", accentColor: theme.accent, cursor: "pointer" }}
            />
            <span style={{ fontSize: 11, color: theme.textMuted }}>
              Higher λ restricts deviation from incumbent weights w₀ to reduce transaction costs.
            </span>
          </div>

          {/* Max Asset Weight Slider */}
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <label htmlFor="max-weight-slider" style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>
                Max Asset Weight (w<sub>max</sub>)
              </label>
              <span data-testid="max-weight-val" style={{ fontSize: 13, fontFamily: "monospace", color: theme.growth, fontWeight: 700 }}>
                {(maxAssetWeight * 100).toFixed(0)}%
              </span>
            </div>
            <input
              id="max-weight-slider"
              type="range"
              min="0.10"
              max="0.50"
              step="0.05"
              value={maxAssetWeight}
              onChange={(e) => setMaxAssetWeight(parseFloat(e.target.value))}
              aria-label="Max Asset Weight Slider"
              aria-valuenow={maxAssetWeight}
              aria-valuemin={0.1}
              aria-valuemax={0.5}
              style={{ width: "100%", accentColor: theme.growth, cursor: "pointer" }}
            />
            <span style={{ fontSize: 11, color: theme.textMuted }}>
              Single-name risk cap to prevent idiosyncratic concentration.
            </span>
          </div>

          {/* Target Beta Bounds */}
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <label style={{ fontSize: 13, fontWeight: 600, color: theme.textPrimary }}>
                Target Beta Range [β<sub>min</sub>, β<sub>max</sub>]
              </label>
              <span style={{ fontSize: 13, fontFamily: "monospace", color: theme.caution, fontWeight: 700 }}>
                [{betaMin.toFixed(2)}, {betaMax.toFixed(2)}]
              </span>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input
                type="number"
                step="0.05"
                min="0.0"
                max={betaMax}
                value={betaMin}
                onChange={(e) => setBetaMin(parseFloat(e.target.value) || 0.0)}
                aria-label="Target Beta Minimum"
                style={{
                  width: "100%",
                  padding: "6px 8px",
                  background: theme.surface3,
                  border: `1px solid ${theme.border}`,
                  borderRadius: 4,
                  color: theme.textPrimary,
                  fontSize: 12,
                }}
              />
              <span style={{ color: theme.textMuted }}>to</span>
              <input
                type="number"
                step="0.05"
                min={betaMin}
                max="2.5"
                value={betaMax}
                onChange={(e) => setBetaMax(parseFloat(e.target.value) || 1.5)}
                aria-label="Target Beta Maximum"
                style={{
                  width: "100%",
                  padding: "6px 8px",
                  background: theme.surface3,
                  border: `1px solid ${theme.border}`,
                  borderRadius: 4,
                  color: theme.textPrimary,
                  fontSize: 12,
                }}
              />
            </div>
            <span style={{ fontSize: 11, color: theme.textMuted }}>
              Enforces market-neutral or factor-controlled portfolio exposure.
            </span>
          </div>
        </div>

        {/* Sector Caps Widgets */}
        <div style={{ borderTop: `1px solid ${theme.border}`, paddingTop: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: theme.textSecondary, marginBottom: 8 }}>
            Sector Concentration Caps
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
            {Object.entries(sectorCaps).map(([sector, cap], idx) => (
              <div
                key={sector}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  background: theme.surface3,
                  padding: "6px 12px",
                  borderRadius: 6,
                  border: `1px solid ${theme.border}`,
                }}
              >
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    backgroundColor: sectorColor(idx),
                  }}
                />
                <span style={{ fontSize: 12, color: theme.textPrimary, fontWeight: 600 }}>{sector}:</span>
                <input
                  type="number"
                  min="0.05"
                  max="1.0"
                  step="0.05"
                  value={cap}
                  onChange={(e) => handleSectorCapChange(sector, parseFloat(e.target.value) || 0.25)}
                  aria-label={`${sector} Sector Cap`}
                  style={{
                    width: 54,
                    padding: "2px 4px",
                    background: theme.surface2,
                    border: `1px solid ${theme.border}`,
                    borderRadius: 4,
                    color: theme.textPrimary,
                    fontSize: 12,
                    textAlign: "right",
                  }}
                />
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* KPI Cards */}
      {data && (
        <div
          data-testid="hrp-kpis"
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            gap: 12,
          }}
        >
          {/* Turnover */}
          <div
            style={{
              background: theme.surface2,
              border: `1px solid ${theme.border}`,
              borderRadius: 8,
              padding: "12px 16px",
            }}
          >
            <div style={{ fontSize: 12, color: theme.textSecondary }}>Projected Turnover</div>
            <div
              data-testid="kpi-turnover"
              style={{
                fontSize: 20,
                fontWeight: 700,
                color: data.turnover < 0.15 ? theme.growth : data.turnover < 0.3 ? theme.caution : theme.decline,
                margin: "4px 0",
              }}
            >
              {(data.turnover * 100).toFixed(1)}%
            </div>
            <div style={{ fontSize: 11, color: theme.textMuted }}>
              {data.turnover < 0.15 ? "Low Friction" : data.turnover < 0.3 ? "Moderate Turnover" : "High Rebalance"}
            </div>
          </div>

          {/* CVaR 95% */}
          <div
            style={{
              background: theme.surface2,
              border: `1px solid ${theme.border}`,
              borderRadius: 8,
              padding: "12px 16px",
            }}
          >
            <div style={{ fontSize: 12, color: theme.textSecondary }}>Post-Rebalance CVaR (95%)</div>
            <div
              data-testid="kpi-cvar"
              style={{ fontSize: 20, fontWeight: 700, color: theme.decline, margin: "4px 0" }}
            >
              {(data.cvar_95 * 100).toFixed(2)}%
            </div>
            <div style={{ fontSize: 11, color: theme.textMuted }}>Tail Expected Shortfall</div>
          </div>

          {/* Portfolio Beta */}
          <div
            style={{
              background: theme.surface2,
              border: `1px solid ${theme.border}`,
              borderRadius: 8,
              padding: "12px 16px",
            }}
          >
            <div style={{ fontSize: 12, color: theme.textSecondary }}>Portfolio Beta (β)</div>
            <div
              data-testid="kpi-beta"
              style={{
                fontSize: 20,
                fontWeight: 700,
                color: data.portfolio_beta >= betaMin && data.portfolio_beta <= betaMax ? theme.growth : theme.caution,
                margin: "4px 0",
              }}
            >
              {data.portfolio_beta.toFixed(2)}
            </div>
            <div style={{ fontSize: 11, color: theme.textMuted }}>Target: [{betaMin.toFixed(2)}, {betaMax.toFixed(2)}]</div>
          </div>

          {/* Diversification Ratio */}
          <div
            style={{
              background: theme.surface2,
              border: `1px solid ${theme.border}`,
              borderRadius: 8,
              padding: "12px 16px",
            }}
          >
            <div style={{ fontSize: 12, color: theme.textSecondary }}>Diversification Ratio</div>
            <div
              data-testid="kpi-div-ratio"
              style={{ fontSize: 20, fontWeight: 700, color: theme.accent, margin: "4px 0" }}
            >
              {data.diversification_ratio.toFixed(2)}x
            </div>
            <div style={{ fontSize: 11, color: theme.textMuted }}>Choueifaty Benefit</div>
          </div>

          {/* Expected Return / Sharpe */}
          <div
            style={{
              background: theme.surface2,
              border: `1px solid ${theme.border}`,
              borderRadius: 8,
              padding: "12px 16px",
            }}
          >
            <div style={{ fontSize: 12, color: theme.textSecondary }}>Expected Return / Sharpe</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: theme.textPrimary, margin: "4px 0" }}>
              {(data.expected_return * 100).toFixed(1)}% <span style={{ fontSize: 12, color: theme.textSecondary }}>/ {data.sharpe_ratio.toFixed(2)} SR</span>
            </div>
            <div style={{ fontSize: 11, color: theme.textMuted }}>Annualized Projection</div>
          </div>
        </div>
      )}

      {/* Visualizations Grid */}
      {data && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 16 }}>
          {/* Allocation Delta Bar Chart */}
          <div
            style={{
              background: theme.surface2,
              border: `1px solid ${theme.border}`,
              borderRadius: 8,
              padding: 16,
            }}
          >
            <h3 style={{ fontSize: 14, fontWeight: 600, color: theme.textPrimary, margin: "0 0 12px 0" }}>
              Allocation Comparison (Incumbent w₀ vs Proposed w*)
            </h3>
            <div style={{ height: 260, width: "100%" }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.chartGrid} />
                  <XAxis dataKey="symbol" stroke={theme.textSecondary} fontSize={12} />
                  <YAxis stroke={theme.textSecondary} fontSize={12} tickFormatter={(val) => `${val}%`} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: theme.surface,
                      borderColor: theme.borderStrong,
                      borderRadius: 6,
                      fontSize: 12,
                    }}
                    formatter={(val: any) => [`${Number(val).toFixed(1)}%`, ""]}
                  />
                  <Legend wrapperStyle={{ fontSize: 12, paddingTop: 6 }} />
                  <Bar dataKey="incumbent" name="Incumbent w₀" fill="#64748b" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="proposed" name="Proposed w*" fill={theme.accent} radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Sector Exposures & Caps */}
          <div
            style={{
              background: theme.surface2,
              border: `1px solid ${theme.border}`,
              borderRadius: 8,
              padding: 16,
              display: "flex",
              flexDirection: "column",
              gap: 12,
            }}
          >
            <h3 style={{ fontSize: 14, fontWeight: 600, color: theme.textPrimary, margin: 0 }}>
              Sector Exposures vs Configured Caps
            </h3>

            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 4 }}>
              {Object.entries(data.sector_exposures).map(([sec, exp], idx) => {
                const cap = sectorCaps[sec] ?? 1.0;
                const isExceeded = exp > cap + 0.001;
                const pct = Math.min(100, Math.round(exp * 100));
                const capPct = Math.round(cap * 100);

                return (
                  <div key={sec} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                      <span style={{ fontWeight: 600, color: theme.textPrimary }}>{sec}</span>
                      <span style={{ color: isExceeded ? theme.decline : theme.textSecondary }}>
                        {(exp * 100).toFixed(1)}% / Cap: {capPct}%
                      </span>
                    </div>

                    {/* Progress Bar Container */}
                    <div
                      style={{
                        position: "relative",
                        height: 10,
                        background: theme.surface3,
                        borderRadius: 5,
                        overflow: "hidden",
                      }}
                    >
                      <div
                        style={{
                          height: "100%",
                          width: `${pct}%`,
                          background: isExceeded ? theme.decline : sectorColor(idx),
                          borderRadius: 5,
                          transition: "width 0.4s ease",
                        }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Hierarchical Dendrogram Tree */}
            <div style={{ marginTop: 8, borderTop: `1px solid ${theme.border}`, paddingTop: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: theme.textSecondary, marginBottom: 6 }}>
                HRP Hierarchical Tree Structure
              </div>
              <div
                style={{
                  background: theme.surface,
                  border: `1px solid ${theme.border}`,
                  borderRadius: 6,
                  padding: 10,
                  maxHeight: 120,
                  overflowY: "auto",
                }}
              >
                <DendrogramNode node={data.dendrogram} />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function DendrogramNode({ node, depth = 0 }: { node: HrpCvarClusterNode; depth?: number }) {
  const padding = depth * 12;
  return (
    <div style={{ paddingLeft: `${padding}px` }}>
      <div style={{ display: "flex", alignItems: "center", fontSize: 11, padding: "2px 0" }}>
        <span style={{ color: theme.accent, fontWeight: 600 }}>{node.name}</span>
        {node.distance !== undefined && (
          <span style={{ marginLeft: 6, color: theme.textMuted, fontSize: 10 }}>
            (d={node.distance.toFixed(3)})
          </span>
        )}
      </div>
      {node.children && node.children.length > 0 && (
        <div style={{ borderLeft: `1px solid ${theme.border}`, marginLeft: 4 }}>
          {node.children.map((child, idx) => (
            <DendrogramNode key={idx} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

export { HrpPortfolioOptimizerView as HrpCvarOptimizerView };
