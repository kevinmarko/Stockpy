import { useState } from "react";
import { api } from "../../api/client";
import { HrpCvarOptimizeResponse, HrpCvarClusterNode } from "../../api/types";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  Legend,
} from "recharts";
import { theme } from "../../theme";

export function HrpCvarOptimizerView({ symbols }: { symbols: string[] }) {
  const [data, setData] = useState<HrpCvarOptimizeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleOptimize = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.optimizeHrpCvar({ symbols });
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
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>HRP CVaR Optimizer</h2>
      </div>
      <div>
        <div className="mb-4" style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <button 
            onClick={handleOptimize} 
            disabled={loading || symbols.length === 0}
            style={{
              padding: "8px 16px",
              background: theme.accent,
              color: "#000",
              border: "none",
              borderRadius: 4,
              cursor: (loading || symbols.length === 0) ? "not-allowed" : "pointer",
              fontWeight: 600
            }}
          >
            {loading ? "Optimizing..." : "Run Optimization"}
          </button>
          {error && <p className="text-red-500 m-0">{error}</p>}
        </div>

        {data && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <h3 className="font-semibold mb-2">Asset Allocations</h3>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.allocations} layout="vertical" margin={{ left: 20 }}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis type="number" tickFormatter={(val) => `${(val * 100).toFixed(1)}%`} />
                    <YAxis dataKey="symbol" type="category" width={80} />
                    <Tooltip formatter={(val: any) => `${(Number(val) * 100).toFixed(1)}%`} />
                  <Legend />
                  <Bar dataKey="weight" fill="#3b82f6" name="Weight">
                      {data.allocations.map((_entry, index) => (
                        <Cell key={`cell-${index}`} fill={`hsl(${(index * 360) / data.allocations.length}, 70%, 50%)`} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div className="mt-4 text-sm text-gray-400">
                <p>Expected Return: {(data.expected_return * 100).toFixed(2)}%</p>
                <p>CVaR (95%): {(data.cvar_95 * 100).toFixed(2)}%</p>
                <p>Sharpe Ratio: {data.sharpe_ratio.toFixed(2)}</p>
              </div>
            </div>
            <div>
              <h3 className="font-semibold mb-2">Dendrogram Clustering</h3>
              <div className="bg-gray-800 p-4 rounded overflow-auto max-h-80">
                <DendrogramNode node={data.dendrogram} />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function DendrogramNode({ node, depth = 0 }: { node: HrpCvarClusterNode; depth?: number }) {
  const padding = depth * 16;
  return (
    <div style={{ paddingLeft: `${padding}px` }}>
      <div className="flex items-center text-sm py-1 border-l-2 border-gray-600 pl-2 my-1">
        <span className="font-medium text-blue-400">{node.name}</span>
        {node.distance !== undefined && (
          <span className="ml-2 text-xs text-gray-500">d={node.distance.toFixed(3)}</span>
        )}
      </div>
      {node.children && node.children.length > 0 && (
        <div className="border-l border-gray-700 ml-2">
          {node.children.map((child, idx) => (
            <DendrogramNode key={idx} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}
