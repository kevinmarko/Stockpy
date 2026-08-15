import React from "react";
import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { TransformerForecastResponse } from "../../api/types";

interface Props {
  symbol: string;
}

export const TransformerVolForecastView: React.FC<Props> = ({ symbol }) => {
  const { data, loading, error } = useApi<TransformerForecastResponse>(
    () => api.getTransformerForecast(symbol),
    [symbol]
  );

  if (loading) return <div>Loading AI Vol Forecast...</div>;
  if (error) return <div className="text-red-500">{String(error)}</div>;
  if (!data) return null;

  return (
    <div className="bg-gray-800 p-4 rounded-lg shadow-lg border border-gray-700">
      <h3 className="text-lg font-bold text-white mb-2">
        🤖 Transformer Volatility Forecast: {data.symbol}
      </h3>
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-gray-900 p-4 rounded border border-gray-600">
          <h4 className="text-sm font-semibold text-gray-300 mb-2">Cone Forecast (Horizon: {data.forecast_horizon}d)</h4>
          <div className="text-xs text-gray-400">Current Vol: {(data.current_vol * 100).toFixed(1)}%</div>
          <div className="mt-4 h-32 flex items-end justify-between px-2">
            {/* Simple sparkline / cone representation */}
            {data.forecast_trajectory.map((v, i) => {
              if (i % Math.ceil(data.forecast_trajectory.length / 20) !== 0) return null;
              const h = Math.min(100, Math.max(0, (v / (data.current_vol * 2)) * 100));
              return (
                <div
                  key={i}
                  className="w-2 bg-blue-500 rounded-t"
                  style={{ height: `${h}%` }}
                  title={`Day ${i + 1}: ${(v * 100).toFixed(1)}%`}
                />
              );
            })}
          </div>
        </div>
        <div className="bg-gray-900 p-4 rounded border border-gray-600">
          <h4 className="text-sm font-semibold text-gray-300 mb-2">Attention Heatmap & Importance</h4>
          <div className="text-xs text-gray-400">
            {Object.entries(data.feature_importance).map(([k, v]) => (
              <div key={k} className="flex justify-between mb-1">
                <span>{k}</span>
                <span className="text-blue-400">{(v * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
