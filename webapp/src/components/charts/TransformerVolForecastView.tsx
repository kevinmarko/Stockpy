import React from "react";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { TransformerForecastResponse } from "../../api/types";
import { theme } from "../../theme";

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
      <div className="p-4 text-gray-400 animate-pulse bg-gray-800 rounded-lg border border-gray-700">
        Loading AI Vol Forecast...
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 text-red-400 bg-red-950/40 rounded-lg border border-red-800/60 text-sm">
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
    <div className="bg-gray-800 p-5 rounded-lg shadow-lg border border-gray-700">
      {/* Header with Title and Conditioning Badges */}
      <div className="flex flex-wrap items-center justify-between gap-2 mb-4 pb-3 border-b border-gray-700/80">
        <div className="flex items-center gap-2">
          <h3 className="text-lg font-bold text-white tracking-wide">
            🤖 Transformer Volatility Forecast: {data.symbol}
          </h3>
          {data.macro_conditioned && (
            <span
              className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-950 text-emerald-400 border border-emerald-700/60 shadow-sm"
              data-testid="macro-conditioned-badge"
              title="Conditioned on FRED macro series (VIXCLS, T10Y2Y, BAMLC0A0CM, FEDFUNDS)"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Macro-Conditioned
            </span>
          )}
        </div>
        {data.trained_samples !== undefined && data.trained_samples > 0 && (
          <span className="text-xs text-gray-400 bg-gray-900/60 px-2.5 py-1 rounded border border-gray-700/50">
            Trained on <span className="text-gray-200 font-medium">{data.trained_samples}</span> causal windows
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Multi-Horizon Volatility Cone / Breakdown */}
        <div className="bg-gray-900/90 p-4 rounded-lg border border-gray-700 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-sm font-semibold text-gray-200">
                Multi-Horizon Volatility Forecast
              </h4>
              {hasQuantiles && (
                <span className="text-xs text-sky-400 font-medium bg-sky-950/60 px-2 py-0.5 rounded border border-sky-800/40">
                  Probabilistic Cone (q₁₀ - q₉₀)
                </span>
              )}
            </div>

            {/* SVG Cone Visualization when Quantiles Available */}
            {hasQuantiles ? (
              <div className="my-2 bg-gray-950/80 p-2 rounded border border-gray-800 overflow-x-auto">
                <svg
                  viewBox={`0 0 ${svgWidth} ${svgHeight}`}
                  className="w-full h-auto max-h-[160px] select-none"
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
            <div className="mt-3 flex flex-col gap-2">
              {horizons.map((h) => {
                const v = data.forecast[h];
                const q = quantileForecast?.[h];
                const widthPct = Math.min(100, (v / maxVol) * 100);

                return (
                  <div
                    key={h}
                    className="p-2 rounded bg-gray-800/60 border border-gray-700/50 flex flex-col gap-1.5"
                  >
                    <div className="flex items-center justify-between text-xs">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-gray-200 w-8">{h}</span>
                        {q && (
                          <span className="text-[11px] text-gray-400">
                            q₁₀: <span className="text-gray-300">{(q.q10 * 100).toFixed(1)}%</span> — q₉₀:{" "}
                            <span className="text-gray-300">{(q.q90 * 100).toFixed(1)}%</span>
                          </span>
                        )}
                      </div>
                      <span className="text-sm font-bold text-sky-400">
                        {(v * 100).toFixed(1)}%
                      </span>
                    </div>

                    {/* Visual Bar Track */}
                    <div className="h-2 bg-gray-700/70 rounded-full overflow-hidden relative">
                      {q ? (
                        <>
                          {/* Shaded interval [q10, q90] */}
                          <div
                            className="absolute h-full bg-sky-500/30 rounded-full"
                            style={{
                              left: `${Math.max(0, (q.q10 / yUpper) * 100)}%`,
                              width: `${Math.min(100, ((q.q90 - q.q10) / yUpper) * 100)}%`,
                            }}
                          />
                          {/* Median point indicator */}
                          <div
                            className="absolute h-full w-1.5 bg-sky-400 rounded-full shadow"
                            style={{
                              left: `${Math.max(0, Math.min(98, (q.q50 / yUpper) * 100))}%`,
                            }}
                            title={`Median: ${(q.q50 * 100).toFixed(1)}%`}
                          />
                        </>
                      ) : (
                        <div
                          className="h-full bg-blue-500 rounded-full"
                          style={{ width: `${widthPct}%` }}
                          title={`${h}: ${(v * 100).toFixed(1)}%`}
                        />
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="mt-3 pt-2 border-t border-gray-800 text-[11px] text-gray-400 flex items-center justify-between">
            <span>Probabilistic Cone: 10th – 90th percentile bounds</span>
            <span className="text-gray-300">Annualized Volatility (σ)</span>
          </div>
        </div>

        {/* Attention Heatmap Card */}
        <div className="bg-gray-900/90 p-4 rounded-lg border border-gray-700 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-1">
              <h4 className="text-sm font-semibold text-gray-200">Attention Heatmap</h4>
              <span className="text-[11px] text-gray-400">Self-Attention Matrix</span>
            </div>
            <p className="text-[11px] text-gray-400 mb-3">
              Captures temporal dependency across sequence steps in the TFT causal attention head.
            </p>

            {heatmap.length === 0 ? (
              <div className="text-xs text-gray-400 py-8 text-center bg-gray-950/50 rounded border border-gray-800">
                No attention data available.
              </div>
            ) : (
              <div className="p-2.5 bg-gray-950/90 rounded border border-gray-800 flex justify-center">
                <div
                  className="flex flex-col gap-[1.5px]"
                  data-testid="attention-heatmap"
                  role="img"
                  aria-label="Temporal Self-Attention Matrix Heatmap"
                >
                  {heatmap.map((row, i) => (
                    <div key={i} className="flex gap-[1.5px]">
                      {row.map((v, j) => {
                        const intensity = Math.min(1, Math.abs(v) / heatmapMax);
                        return (
                          <div
                            key={j}
                            title={`Step (${i}, ${j}): ${v.toFixed(3)}`}
                            className="rounded-[1px] transition-opacity hover:opacity-80"
                            style={{
                              width: heatmap.length > 20 ? 4 : 14,
                              height: heatmap.length > 20 ? 4 : 14,
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

          <div className="mt-3 pt-2 border-t border-gray-800 flex items-center justify-between text-[11px] text-gray-400">
            <span>Lookback Attention Scale</span>
            <div className="flex items-center gap-1.5">
              <span>Low</span>
              <div className="w-16 h-2 rounded-full bg-gradient-to-r from-sky-950 via-sky-700 to-sky-400 border border-gray-700/50" />
              <span>High</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

