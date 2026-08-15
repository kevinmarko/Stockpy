import React, { useState } from "react";
import { api } from "../../api/client";
import { DiffusionStressRequest, DiffusionStressResponse } from "../../api/types";

interface Props {
  symbol: string;
}

export const GenerativeDiffusionStressView: React.FC<Props> = ({ symbol }) => {
  const [data, setData] = useState<DiffusionStressResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [drift, setDrift] = useState(0);
  const [volatility, setVolatility] = useState(0.2);
  const [jumpIntensity, setJumpIntensity] = useState(0.01);
  const [horizonDays, setHorizonDays] = useState(30);

  const runSimulation = async () => {
    setLoading(true);
    setError(null);
    try {
      const req: DiffusionStressRequest = {
        symbol,
        drift,
        volatility,
        jump_intensity: jumpIntensity,
        jump_mean: -0.05,
        jump_std: 0.1,
        paths: 1000,
        horizon_days: horizonDays,
      };
      const res = await api.runDiffusionStressTest(req);
      setData(res);
    } catch (e: any) {
      setError(e.message || "Failed to run simulation");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-gray-800 p-4 rounded-lg shadow-lg border border-gray-700">
      <h3 className="text-lg font-bold text-white mb-2">
        🌪️ Generative Diffusion Stress Test: {symbol}
      </h3>
      <div className="flex gap-4 mb-4 text-sm text-gray-300">
        <div>
          <label className="block text-xs">Drift (Ann.)</label>
          <input type="number" step="0.01" className="bg-gray-700 p-1 w-20 rounded" value={drift} onChange={(e) => setDrift(parseFloat(e.target.value))} />
        </div>
        <div>
          <label className="block text-xs">Volatility</label>
          <input type="number" step="0.01" className="bg-gray-700 p-1 w-20 rounded" value={volatility} onChange={(e) => setVolatility(parseFloat(e.target.value))} />
        </div>
        <div>
          <label className="block text-xs">Jump Intensity</label>
          <input type="number" step="0.01" className="bg-gray-700 p-1 w-20 rounded" value={jumpIntensity} onChange={(e) => setJumpIntensity(parseFloat(e.target.value))} />
        </div>
        <div>
          <label className="block text-xs">Horizon (Days)</label>
          <input type="number" step="1" className="bg-gray-700 p-1 w-20 rounded" value={horizonDays} onChange={(e) => setHorizonDays(parseInt(e.target.value, 10))} />
        </div>
        <div className="flex items-end">
          <button onClick={runSimulation} disabled={loading} className="bg-red-600 hover:bg-red-500 text-white px-3 py-1 rounded disabled:opacity-50">
            {loading ? "Running..." : "Run Stress Test"}
          </button>
        </div>
      </div>
      
      {error && <div className="text-red-500 mb-2">{error}</div>}

      {data && (
        <div className="grid grid-cols-2 gap-4">
          <div className="bg-gray-900 p-4 rounded border border-gray-600">
            <h4 className="text-sm font-semibold text-gray-300 mb-2">Risk Metrics (Horizon: {data.horizon_days}d)</h4>
            <div className="text-sm text-gray-400 space-y-1">
              <div className="flex justify-between"><span>Value at Risk (95%)</span> <span className="text-red-400">{(data.var_95 * 100).toFixed(2)}%</span></div>
              <div className="flex justify-between"><span>Conditional VaR (95%)</span> <span className="text-red-400">{(data.cvar_95 * 100).toFixed(2)}%</span></div>
              <div className="flex justify-between"><span>Expected Shortfall</span> <span className="text-red-400">{(data.expected_shortfall * 100).toFixed(2)}%</span></div>
            </div>
            <h4 className="text-sm font-semibold text-gray-300 mt-4 mb-2">Crash Probabilities</h4>
            <div className="text-sm text-gray-400 space-y-1">
              {Object.entries(data.crash_probabilities).map(([k, v]) => (
                <div key={k} className="flex justify-between"><span>{k} drop</span> <span className="text-yellow-400">{(v * 100).toFixed(1)}%</span></div>
              ))}
            </div>
          </div>
          <div className="bg-gray-900 p-4 rounded border border-gray-600">
            <h4 className="text-sm font-semibold text-gray-300 mb-2">Monte Carlo Crash Cloud</h4>
            <div className="h-32 flex flex-col justify-end">
              {/* Simplistic representation of terminal distribution histogram */}
              <div className="flex items-end justify-between px-2 h-full gap-1">
                {Array.from({ length: 20 }).map((_, i) => {
                  const binStart = Math.min(...data.terminal_price_distribution) + i * ((Math.max(...data.terminal_price_distribution) - Math.min(...data.terminal_price_distribution)) / 20);
                  const count = data.terminal_price_distribution.filter(v => v >= binStart && v < binStart + ((Math.max(...data.terminal_price_distribution) - Math.min(...data.terminal_price_distribution)) / 20)).length;
                  const h = Math.min(100, Math.max(0, (count / data.paths_simulated) * 500));
                  return (
                    <div
                      key={i}
                      className="w-full bg-red-900 rounded-t border-t border-red-500"
                      style={{ height: `${h}%` }}
                      title={`Bin ${i}: ${count} paths`}
                    />
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
