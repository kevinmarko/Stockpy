import React from "react";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { TransformerForecastResponse } from "../../api/types";
import { theme, alpha } from "../../theme";

interface Props {
  symbol: string;
}

// Sort order for the known horizon-day labels the backend emits
// ("1d", "5d", "21d", "60d"). Any unexpected label sorts after these.
const HORIZON_ORDER: Record<string, number> = { "1d": 0, "5d": 1, "21d": 2, "60d": 3 };

export const TransformerVolForecastView: React.FC<Props> = ({ symbol }) => {
  const { data, loading, error } = useApi<TransformerForecastResponse>(
    () => api.getTransformerForecast(symbol),
    [symbol]
  );

  if (loading) {
    return (
      <div
        className="card card-pad"
        style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", color: theme.textSecondary, fontSize: "var(--t-body)" }}
      >
        {/* .pulse-dot (index.css) replaces the dead Tailwind `animate-pulse` this
            used to carry -- this webapp has no Tailwind build, so that class
            produced no CSS at all and the loading state never pulsed. */}
        <span className="pulse-dot" aria-hidden="true" />
        Loading AI Vol Forecast...
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="card card-pad"
        style={{
          color: theme.decline,
          background: alpha(theme.decline, "15"),
          border: `1px solid ${alpha(theme.decline, "40")}`,
          fontSize: "var(--t-body)",
        }}
      >
        {String(error)}
      </div>
    );
  }

  if (!data) return null;

  const horizons = Object.keys(data.forecast).sort(
    (a, b) => (HORIZON_ORDER[a] ?? 99) - (HORIZON_ORDER[b] ?? 99)
  );

  const quantileForecast = data.quantile_forecast;
  const hasQuantiles = quantileForecast && Object.keys(quantileForecast).length > 0;

  // Compute maximum vol for scale bounds
  let maxVol = 0.0001;
  horizons.forEach((h) => {
    maxVol = Math.max(maxVol, data.forecast[h] ?? 0);
    if (quantileForecast?.[h]) {
      maxVol = Math.max(maxVol, quantileForecast[h].q90);
    }
  });
  // Pad upper bound for chart aesthetics
  const yUpper = Math.max(0.2, Math.ceil(maxVol * 1.25 * 20) / 20);

  const heatmap = data.attention_heatmap ?? [];
  const heatmapMax = heatmap.reduce(
    (acc, row) => Math.max(acc, ...row.map((v) => Math.abs(v))),
    0.0001
  );

  // SVG dimensions for Volatility Cone Chart
  const svgWidth = 460;
  const svgHeight = 160;
  const padLeft = 45;
  const padRight = 25;
  const padTop = 15;
  const padBottom = 28;
  const chartW = svgWidth - padLeft - padRight;
  const chartH = svgHeight - padTop - padBottom;

  const getX = (idx: number) => padLeft + (idx / Math.max(1, horizons.length - 1)) * chartW;
  const getY = (val: number) => padTop + chartH - (Math.min(val, yUpper) / yUpper) * chartH;

  // Build polygon path for shaded quantile interval (q10 -> q90)
  let conePath = "";
  if (hasQuantiles && horizons.length > 0) {
    const topPoints = horizons.map((h, i) => `${getX(i)},${getY(quantileForecast[h]?.q90 ?? data.forecast[h])}`);
    const bottomPoints = horizons
      .slice()
      .reverse()
      .map((h, i) => {
        const origIdx = horizons.length - 1 - i;
        return `${getX(origIdx)},${getY(quantileForecast[h]?.q10 ?? data.forecast[h])}`;
      });
    conePath = `M ${topPoints.join(" L ")} L ${bottomPoints.join(" L ")} Z`;
  }

  // Median path (q50 or point forecast)
  const medianPoints = horizons.map((h, i) => `${getX(i)},${getY(quantileForecast?.[h]?.q50 ?? data.forecast[h])}`);
  const medianPath = `M ${medianPoints.join(" L ")}`;

  return (
    <div className="card card-pad">
      {/* Header with Title and Conditioning Badges */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "var(--s-2)",
          marginBottom: "var(--s-4)",
          paddingBottom: "var(--s-3)",
          borderBottom: `1px solid ${theme.border}`,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <h3 style={{ fontSize: "1.05rem", fontWeight: 700, color: theme.textPrimary, letterSpacing: "0.01em", margin: 0 }}>
            🤖 Transformer Volatility Forecast: {data.symbol}
          </h3>
          {data.macro_conditioned && (
            <span
              className="badge badge-good"
              data-testid="macro-conditioned-badge"
              title="Conditioned on FRED macro series (VIXCLS, T10Y2Y, BAMLC0A0CM, FEDFUNDS)"
            >
              <span className="pulse-dot" aria-hidden="true" />
              Macro-Conditioned
            </span>
          )}
        </div>
        {data.trained_samples !== undefined && data.trained_samples > 0 && (
          <span className="chip">
            Trained on <span style={{ color: theme.textPrimary, fontWeight: 600, margin: "0 4px" }}>{data.trained_samples}</span> causal windows
          </span>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: "var(--s-5)" }}>
        {/* Multi-Horizon Volatility Cone / Breakdown */}
        <div
          style={{
            background: theme.base,
            padding: "var(--s-4)",
            borderRadius: "var(--r-md)",
            border: `1px solid ${theme.border}`,
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
          }}
        >
          <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--s-2)" }}>
              <h4 style={{ fontSize: "0.85rem", fontWeight: 600, color: theme.textSecondary, margin: 0 }}>
                Multi-Horizon Volatility Forecast
              </h4>
              {hasQuantiles && (
                <span
                  style={{
                    fontSize: "0.7rem",
                    fontWeight: 600,
                    color: theme.accent,
                    background: alpha(theme.accent, "15"),
                    border: `1px solid ${alpha(theme.accent, "35")}`,
                    padding: "2px 8px",
                    borderRadius: "var(--r-xs)",
                  }}
                >
                  Probabilistic Cone (q₁₀ - q₉₀)
                </span>
              )}
            </div>

            {/* SVG Cone Visualization when Quantiles Available */}
            {hasQuantiles ? (
              <div
                style={{
                  margin: "var(--s-2) 0",
                  background: theme.base,
                  padding: "var(--s-2)",
                  borderRadius: "var(--r-xs)",
                  border: `1px solid ${theme.border}`,
                  overflowX: "auto",
                }}
              >
                <svg
                  viewBox={`0 0 ${svgWidth} ${svgHeight}`}
                  style={{ width: "100%", height: "auto", maxHeight: 160, userSelect: "none" }}
                  aria-label="Multi-horizon volatility probabilistic cone"
                >
                  {/* Grid Lines */}
                  {[0.25, 0.5, 0.75, 1.0].map((frac) => {
                    const yVal = frac * yUpper;
                    const yCoord = getY(yVal);
                    return (
                      <g key={frac}>
                        <line
                          x1={padLeft}
                          y1={yCoord}
                          x2={svgWidth - padRight}
                          y2={yCoord}
                          stroke="rgba(255,255,255,0.07)"
                          strokeDasharray="2 2"
                        />
                        <text
                          x={padLeft - 6}
                          y={yCoord + 3}
                          textAnchor="end"
                          fontSize="9"
                          fill="#9ca3af"
                        >
                          {(yVal * 100).toFixed(0)}%
                        </text>
                      </g>
                    );
                  })}

                  {/* Shaded Quantile Cone (q10 to q90) */}
                  {conePath && (
                    <path
                      d={conePath}
                      fill="rgba(56, 189, 248, 0.16)"
                      stroke="rgba(56, 189, 248, 0.45)"
                      strokeWidth="1.2"
                      strokeDasharray="3 3"
                    />
                  )}

                  {/* Median Line (q50) */}
                  {medianPath && (
                    <path
                      d={medianPath}
                      fill="none"
                      stroke={theme.accent}
                      strokeWidth="2.2"
                    />
                  )}

                  {/* Dots & Labels for Each Horizon */}
                  {horizons.map((h, i) => {
                    const cx = getX(i);
                    const q = quantileForecast[h];
                    const medVal = q?.q50 ?? data.forecast[h];
                    const q10Val = q?.q10 ?? medVal;
                    const q90Val = q?.q90 ?? medVal;
                    const cyMed = getY(medVal);
                    const cy10 = getY(q10Val);
                    const cy90 = getY(q90Val);

                    return (
                      <g key={h}>
                        {/* Upper Whisker / Dot (q90) */}
                        <circle cx={cx} cy={cy90} r="2.5" fill="#38bdf8" fillOpacity="0.8" />
                        {/* Lower Whisker / Dot (q10) */}
                        <circle cx={cx} cy={cy10} r="2.5" fill="#38bdf8" fillOpacity="0.8" />
                        {/* Median Circle (q50) */}
                        <circle
                          cx={cx}
                          cy={cyMed}
                          r="4.5"
                          fill="#0f172a"
                          stroke={theme.accent}
                          strokeWidth="2"
                        />
                        {/* Horizon Label on X Axis */}
                        <text
                          x={cx}
                          y={svgHeight - 8}
                          textAnchor="middle"
                          fontSize="10"
                          fontWeight="600"
                          fill="#d1d5db"
                        >
                          {h}
                        </text>
                      </g>
                    );
                  })}
                </svg>
              </div>
            ) : null}

            {/* Horizon Metric Detail Rows */}
            <div style={{ marginTop: "var(--s-3)", display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
              {horizons.map((h) => {
                const v = data.forecast[h];
                const q = quantileForecast?.[h];
                const widthPct = Math.min(100, (v / maxVol) * 100);

                return (
                  <div
                    key={h}
                    style={{
                      padding: "var(--s-2)",
                      borderRadius: "var(--r-xs)",
                      background: theme.surface2,
                      border: `1px solid ${theme.border}`,
                      display: "flex",
                      flexDirection: "column",
                      gap: "var(--s-1-5)",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: "0.75rem" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
                        <span style={{ fontWeight: 600, color: theme.textSecondary, width: 32, display: "inline-block" }}>{h}</span>
                        {q && (
                          <span style={{ fontSize: "0.68rem", color: theme.textMuted }}>
                            q₁₀: <span style={{ color: theme.textSecondary }}>{(q.q10 * 100).toFixed(1)}%</span> — q₉₀:{" "}
                            <span style={{ color: theme.textSecondary }}>{(q.q90 * 100).toFixed(1)}%</span>
                          </span>
                        )}
                      </div>
                      <span style={{ fontSize: "0.8rem", fontWeight: 700, color: theme.accent }}>
                        {(v * 100).toFixed(1)}%
                      </span>
                    </div>

                    {/* Visual Bar Track */}
                    <div style={{ height: 8, background: theme.surface3, borderRadius: "var(--r-pill)", overflow: "hidden", position: "relative" }}>
                      {q ? (
                        <>
                          {/* Shaded interval [q10, q90] */}
                          <div
                            style={{
                              position: "absolute",
                              height: "100%",
                              background: alpha(theme.accent, "30"),
                              borderRadius: "var(--r-pill)",
                              left: `${Math.max(0, (q.q10 / yUpper) * 100)}%`,
                              width: `${Math.min(100, ((q.q90 - q.q10) / yUpper) * 100)}%`,
                            }}
                          />
                          {/* Median point indicator */}
                          <div
                            style={{
                              position: "absolute",
                              height: "100%",
                              width: 6,
                              background: theme.accent,
                              borderRadius: "var(--r-pill)",
                              left: `${Math.max(0, Math.min(98, (q.q50 / yUpper) * 100))}%`,
                            }}
                            title={`Median: ${(q.q50 * 100).toFixed(1)}%`}
                          />
                        </>
                      ) : (
                        <div
                          style={{ height: "100%", background: theme.accent, borderRadius: "var(--r-pill)", width: `${widthPct}%` }}
                          title={`${h}: ${(v * 100).toFixed(1)}%`}
                        />
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div
            style={{
              marginTop: "var(--s-3)",
              paddingTop: "var(--s-2)",
              borderTop: `1px solid ${theme.border}`,
              fontSize: "0.68rem",
              color: theme.textMuted,
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <span>Probabilistic Cone: 10th – 90th percentile bounds</span>
            <span style={{ color: theme.textSecondary }}>Annualized Volatility (σ)</span>
          </div>
        </div>

        {/* Attention Heatmap Card */}
        <div
          style={{
            background: theme.base,
            padding: "var(--s-4)",
            borderRadius: "var(--r-md)",
            border: `1px solid ${theme.border}`,
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
          }}
        >
          <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--s-1)" }}>
              <h4 style={{ fontSize: "0.85rem", fontWeight: 600, color: theme.textSecondary, margin: 0 }}>Attention Heatmap</h4>
              <span style={{ fontSize: "0.68rem", color: theme.textMuted }}>Self-Attention Matrix</span>
            </div>
            <p style={{ fontSize: "0.68rem", color: theme.textMuted, marginBottom: "var(--s-3)" }}>
              Captures temporal dependency across sequence steps in the TFT causal attention head.
            </p>

            {heatmap.length === 0 ? (
              <div
                style={{
                  fontSize: "0.75rem",
                  color: theme.textMuted,
                  padding: "var(--s-6) 0",
                  textAlign: "center",
                  background: theme.base,
                  borderRadius: "var(--r-xs)",
                  border: `1px solid ${theme.border}`,
                }}
              >
                No attention data available.
              </div>
            ) : (
              <div
                style={{
                  padding: "var(--s-2-5)",
                  background: theme.base,
                  borderRadius: "var(--r-xs)",
                  border: `1px solid ${theme.border}`,
                  display: "flex",
                  justifyContent: "center",
                }}
              >
                <div
                  style={{ display: "flex", flexDirection: "column", gap: 1.5 }}
                  data-testid="attention-heatmap"
                  role="img"
                  aria-label="Temporal Self-Attention Matrix Heatmap"
                >
                  {heatmap.map((row, i) => (
                    <div key={i} style={{ display: "flex", gap: 1.5 }}>
                      {row.map((v, j) => {
                        const intensity = Math.min(1, Math.abs(v) / heatmapMax);
                        return (
                          <div
                            key={j}
                            title={`Step (${i}, ${j}): ${v.toFixed(3)}`}
                            style={{
                              width: heatmap.length > 20 ? 4 : 14,
                              height: heatmap.length > 20 ? 4 : 14,
                              borderRadius: 1,
                              background: `rgba(56, 189, 248, ${Math.max(0.06, intensity).toFixed(2)})`,
                            }}
                          />
                        );
                      })}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div
            style={{
              marginTop: "var(--s-3)",
              paddingTop: "var(--s-2)",
              borderTop: `1px solid ${theme.border}`,
              fontSize: "0.68rem",
              color: theme.textMuted,
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <span>Lookback Attention Scale</span>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)" }}>
              <span>Low</span>
              <div
                style={{
                  width: 64,
                  height: 8,
                  borderRadius: "var(--r-pill)",
                  background: `linear-gradient(to right, ${alpha(theme.accent, "10")}, ${alpha(theme.accent, "60")}, ${theme.accent})`,
                  border: `1px solid ${theme.border}`,
                }}
              />
              <span>High</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
