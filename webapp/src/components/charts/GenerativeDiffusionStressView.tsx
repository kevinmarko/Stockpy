import React, { useState } from "react";
import { api } from "../../api/client";
import { DiffusionStressRequest, DiffusionStressResponse } from "../../api/types";

interface Props {
  symbol: string;
  spotPrice?: number;
}

export const GenerativeDiffusionStressView: React.FC<Props> = ({ symbol, spotPrice }) => {
  const [data, setData] = useState<DiffusionStressResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [spotPriceInput, setSpotPriceInput] = useState(spotPrice ?? 100);
  const [drift, setDrift] = useState(0);
  const [volatility, setVolatility] = useState(0.2);
  const [horizonDays, setHorizonDays] = useState(30);

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
      };
      const res = await api.runDiffusionStressTest(req);
      setData(res);
    } catch (e: any) {
      setError(e.message || "Failed to run simulation");
    } finally {
      setLoading(false);
    }
  };

  // Derived honestly from data.paths client-side -- the real response has
  // no expected_shortfall/crash_probabilities/terminal_price_distribution
  // fields, only paths/VaR_95/CVaR_95.
  const terminalPrices = data ? data.paths.map((p) => p[p.length - 1]) : [];
  const crashThresholds = [-0.05, -0.1, -0.2];
  const crashProbabilities = data
    ? crashThresholds.map((pct) => {
        const threshold = data.paths[0]?.[0] ?? spotPriceInput;
        const count = terminalPrices.filter((v) => v <= threshold * (1 + pct)).length;
        return { pct, prob: terminalPrices.length > 0 ? count / terminalPrices.length : 0 };
      })
    : [];

  return (
    <div className="bg-gray-800 p-4 rounded-lg shadow-lg border border-gray-700">
      <h3 className="text-lg font-bold text-white mb-2">
        🌪️ Generative Diffusion Stress Test: {symbol}
      </h3>
      <div className="flex gap-4 mb-4 text-sm text-gray-300">
        <div>
          <label className="block text-xs">Spot Price ($)</label>
          <input type="number" step="0.01" className="bg-gray-700 p-1 w-20 rounded" value={spotPriceInput} onChange={(e) => setSpotPriceInput(parseFloat(e.target.value))} />
        </div>
        <div>
          <label className="block text-xs">Drift (Ann.)</label>
          <input type="number" step="0.01" className="bg-gray-700 p-1 w-20 rounded" value={drift} onChange={(e) => setDrift(parseFloat(e.target.value))} />
        </div>
        <div>
          <label className="block text-xs">Volatility</label>
          <input type="number" step="0.01" className="bg-gray-700 p-1 w-20 rounded" value={volatility} onChange={(e) => setVolatility(parseFloat(e.target.value))} />
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
            <h4 className="text-sm font-semibold text-gray-300 mb-2">Risk Metrics ({data.paths.length} paths)</h4>
            <div className="text-sm text-gray-400 space-y-1">
              <div className="flex justify-between"><span>Value at Risk (95%)</span> <span className="text-red-400">${data.VaR_95.toFixed(2)}</span></div>
              <div className="flex justify-between"><span>Conditional VaR (95%)</span> <span className="text-red-400">${data.CVaR_95.toFixed(2)}</span></div>
            </div>
            <h4 className="text-sm font-semibold text-gray-300 mt-4 mb-2">Crash Probabilities (derived from simulated paths)</h4>
            <div className="text-sm text-gray-400 space-y-1">
              {crashProbabilities.map(({ pct, prob }) => (
                <div key={pct} className="flex justify-between"><span>{(pct * 100).toFixed(0)}% drop</span> <span className="text-yellow-400">{(prob * 100).toFixed(1)}%</span></div>
              ))}
            </div>
          </div>
          <div className="bg-gray-900 p-4 rounded border border-gray-600">
            <h4 className="text-sm font-semibold text-gray-300 mb-2">Monte Carlo Terminal Price Distribution</h4>
            <div className="h-32 flex flex-col justify-end">
              {/* Histogram of terminal prices derived from data.paths */}
              <div className="flex items-end justify-between px-2 h-full gap-1">
                {Array.from({ length: 20 }).map((_, i) => {
                  const min = Math.min(...terminalPrices);
                  const max = Math.max(...terminalPrices);
                  const binWidth = (max - min) / 20 || 1;
                  const binStart = min + i * binWidth;
                  const count = terminalPrices.filter((v) => v >= binStart && v < binStart + binWidth).length;
                  const h = Math.min(100, Math.max(0, (count / Math.max(1, terminalPrices.length)) * 500));
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
