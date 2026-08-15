import { useState } from "react";
import { api } from "../../api/client";
import { AlmgrenChrissOptimizeResponse } from "../../api/types";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { theme } from "../../theme";

export function AlmgrenChrissRouterView({ symbol, quantity }: { symbol: string; quantity: number }) {
  const [data, setData] = useState<AlmgrenChrissOptimizeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleOptimize = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.optimizeAlmgrenChriss({ symbol, quantity });
      setData(res);
    } catch (e: any) {
      setError(e.message || "Optimization failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 8, padding: 16 }}>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>Almgren-Chriss Execution Router</h2>
      </div>
      <div>
        <div className="mb-4 flex items-center gap-4">
          <button 
            onClick={handleOptimize} 
            disabled={loading || !symbol || quantity <= 0}
            style={{
              padding: "8px 16px",
              background: theme.accent,
              color: "#000",
              border: "none",
              borderRadius: 4,
              cursor: (loading || !symbol || quantity <= 0) ? "not-allowed" : "pointer",
              fontWeight: 600
            }}
          >
            {loading ? "Calculating..." : "Calculate Execution Trajectory"}
          </button>
          {error && <p className="text-red-500 m-0">{error}</p>}
          {(!symbol || quantity <= 0) && <p className="text-yellow-500 text-sm m-0">Please provide a valid symbol and quantity.</p>}
        </div>

        {data && (
          <div className="space-y-4">
            {data.variance === 0 && (
              <div className="bg-yellow-900 border border-yellow-700 text-yellow-200 p-3 rounded text-sm">
                Warning: Zero variance detected. Execution trajectory may be degenerate.
              </div>
            )}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4 text-sm">
              <div className="bg-gray-800 p-3 rounded">
                <span className="block text-gray-400">Expected Shortfall</span>
                <span className="text-lg font-semibold">${data.expected_shortfall.toFixed(2)}</span>
              </div>
              <div className="bg-gray-800 p-3 rounded">
                <span className="block text-gray-400">Variance</span>
                <span className="text-lg font-semibold">{data.variance.toFixed(4)}</span>
              </div>
              <div className="bg-gray-800 p-3 rounded">
                <span className="block text-gray-400">Half-Life (Steps)</span>
                <span className="text-lg font-semibold">{data.half_life.toFixed(1)}</span>
              </div>
            </div>

            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={data.trajectory} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                  <XAxis dataKey="step" tick={{ fontSize: 12 }} />
                  <YAxis yAxisId="left" tick={{ fontSize: 12 }} width={50} />
                  <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 12 }} width={50} />
                  <Tooltip />
                  <Legend />
                  <Line yAxisId="left" type="monotone" dataKey="shares_remaining" stroke="#3b82f6" name="Shares Remaining" dot={false} strokeWidth={2} />
                  <Line yAxisId="right" type="monotone" dataKey="trade_size" stroke="#10b981" name="Trade Size" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
