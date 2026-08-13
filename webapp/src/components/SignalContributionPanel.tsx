import { useApi } from "../hooks/useApi";
import { api } from "../api/client";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import type { SymbolSignalBreakdown } from "../api/types";

export function SignalContributionPanel({ symbol }: { symbol: string }) {
  const { data, loading, error } = useApi<SymbolSignalBreakdown>(
    () => api.getSignalBreakdown(symbol),
    [symbol]
  );

  if (loading) {
    return (
      <div style={{ padding: "var(--s-4)", textAlign: "center", color: "var(--text-muted)" }}>
        Loading signal breakdown for {symbol}...
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: "var(--s-4)", color: "var(--decline)" }}>
        Failed to load signal breakdown: {error}
      </div>
    );
  }

  if (!data || !data.modules || data.modules.length === 0) {
    return (
      <div style={{ padding: "var(--s-4)", color: "var(--text-muted)" }}>
        No signal contribution data available.
      </div>
    );
  }

  const chartData = data.modules
    .filter((m) => m.contribution != null)
    .map((m) => ({
      name: m.name,
      contribution: m.contribution as number,
    }))
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution)); // Sort by magnitude

  return (
    <div style={{ padding: "var(--s-3)", background: "var(--surface-2)", borderRadius: "var(--r-md)", marginTop: "var(--s-2)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", marginBottom: "var(--s-3)" }}>
        <h4 style={{ margin: 0, fontSize: "var(--t-body)", color: "var(--text-primary)" }}>Signal Contribution Breakdown</h4>
        <span
          title="Weighted linear decomposition (score × weight), not Shapley values"
          style={{ cursor: "help", color: "var(--text-muted)", fontSize: "var(--t-caption)" }}
        >
          ⓘ
        </span>
      </div>
      <div style={{ width: "100%", height: 200 }}>
        <ResponsiveContainer>
          <BarChart
            data={chartData}
            layout="vertical"
            margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
          >
            <XAxis type="number" />
            <YAxis 
              type="category" 
              dataKey="name" 
              width={120} 
              tick={{ fontSize: 12, fill: "var(--text-secondary)" }} 
            />
            <Tooltip
              formatter={(value: any) => [Number(value).toFixed(4), "Contribution"]}
              contentStyle={{ backgroundColor: "var(--surface)", border: "1px solid var(--border)", borderRadius: "var(--r-sm)" }}
            />
            <Bar dataKey="contribution">
              {chartData.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={entry.contribution >= 0 ? "var(--growth)" : "var(--decline)"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
