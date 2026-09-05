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
import { theme, alpha } from "../../theme";
import { Tile } from "../ui";

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
        <div style={{ marginBottom: "var(--s-4)", display: "flex", alignItems: "center", gap: "var(--s-4)", flexWrap: "wrap" }}>
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
          {error && <p style={{ color: theme.decline, margin: 0 }}>{error}</p>}
          {(!symbol || quantity <= 0) && (
            <p style={{ color: theme.caution, fontSize: "var(--t-body)", margin: 0 }}>
              Please provide a valid symbol and quantity.
            </p>
          )}
        </div>

        {data && (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
            {data.variance === 0 && (
              <div
                style={{
                  padding: "10px 14px",
                  background: alpha(theme.caution, "20"),
                  color: theme.caution,
                  border: `1px solid ${theme.caution}`,
                  borderRadius: "var(--r-sm)",
                  fontSize: "var(--t-body)",
                }}
              >
                Warning: Zero variance detected. Execution trajectory may be degenerate.
              </div>
            )}
            <div className="tiles">
              <Tile label="Expected Shortfall" value={`$${data.expected_shortfall.toFixed(2)}`} />
              <Tile label="Variance" value={data.variance.toFixed(4)} />
              <Tile label="Half-Life (Steps)" value={data.half_life.toFixed(1)} />
            </div>

            {/* Fixed pixel height, not a Tailwind h-72 (which produced no
                real CSS in this Tailwind-free webapp and left the chart
                rendering at 0 height) -- matching AccountPerformanceChart's
                convention: ResponsiveContainer needs a definite ancestor
                height to resolve its own 100% against. */}
            <div style={{ width: "100%", height: 288 }}>
              <ResponsiveContainer>
                <LineChart data={data.trajectory} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                  <XAxis dataKey="step" tick={{ fontSize: 12 }} />
                  <YAxis yAxisId="left" tick={{ fontSize: 12 }} width={50} />
                  <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 12 }} width={50} />
                  <Tooltip />
                  <Legend />
                  <Line yAxisId="left" type="monotone" dataKey="shares_remaining" stroke={theme.accent} name="Shares Remaining" dot={false} strokeWidth={2} />
                  <Line yAxisId="right" type="monotone" dataKey="trade_size" stroke={theme.growth} name="Trade Size" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
