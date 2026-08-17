import React from "react";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { TransformerForecastResponse } from "../../api/types";

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

  if (loading) return <div>Loading AI Vol Forecast...</div>;
  if (error) return <div className="text-red-500">{String(error)}</div>;
  if (!data) return null;

  const horizons = Object.keys(data.forecast).sort(
    (a, b) => (HORIZON_ORDER[a] ?? 99) - (HORIZON_ORDER[b] ?? 99)
  );
  const maxVol = Math.max(0.0001, ...horizons.map((h) => data.forecast[h]));

  const heatmap = data.attention_heatmap ?? [];
  const heatmapMax = heatmap.reduce(
    (acc, row) => Math.max(acc, ...row.map((v) => Math.abs(v))),
    0.0001
  );

  return (
    <div className="bg-gray-800 p-4 rounded-lg shadow-lg border border-gray-700">
      <h3 className="text-lg font-bold text-white mb-2">
        🤖 Transformer Volatility Forecast: {data.symbol}
      </h3>
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-gray-900 p-4 rounded border border-gray-600">
          <h4 className="text-sm font-semibold text-gray-300 mb-2">Multi-Horizon Volatility Forecast</h4>
          <div className="mt-2 flex flex-col gap-2">
            {horizons.map((h) => {
              const v = data.forecast[h];
              const widthPct = Math.min(100, (v / maxVol) * 100);
              return (
                <div key={h} className="flex items-center gap-2">
                  <span className="text-xs text-gray-400 w-10">{h}</span>
                  <div className="flex-1 h-3 bg-gray-700 rounded overflow-hidden">
                    <div
                      className="h-full bg-blue-500 rounded"
                      style={{ width: `${widthPct}%` }}
                      title={`${h}: ${(v * 100).toFixed(1)}%`}
                    />
                  </div>
                  <span className="text-xs text-blue-400 w-14 text-right">{(v * 100).toFixed(1)}%</span>
                </div>
              );
            })}
          </div>
        </div>
        <div className="bg-gray-900 p-4 rounded border border-gray-600">
          <h4 className="text-sm font-semibold text-gray-300 mb-2">Attention Heatmap</h4>
          {heatmap.length === 0 ? (
            <div className="text-xs text-gray-400">No attention data available.</div>
          ) : (
            <div className="flex flex-col gap-[1px]" data-testid="attention-heatmap">
              {heatmap.map((row, i) => (
                <div key={i} className="flex gap-[1px]">
                  {row.map((v, j) => {
                    const intensity = Math.min(1, Math.abs(v) / heatmapMax);
                    return (
                      <div
                        key={j}
                        title={`(${i}, ${j}): ${v.toFixed(3)}`}
                        style={{
                          width: 10,
                          height: 10,
                          background: `rgba(59, 130, 246, ${intensity.toFixed(2)})`,
                        }}
                      />
                    );
                  })}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
