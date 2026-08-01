import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { Activity, Loader2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api } from '../api/client';

interface SHAPFeature {
  name: string;
  value: number;
}

/**
 * Universe-wide mean(|score * weight|) per signal module — the SAME real
 * endpoint (GET /metrics/signals/importance) and fetch pattern
 * screens/SignalBreakdown.tsx's GlobalImportancePanel already uses. Rows
 * with n_symbols_scored === 0 (mean_abs_contribution: null) are dropped
 * rather than plotted as a fake 0 bar -- a 0 bar would misread as "measured
 * and found unimportant" instead of "no data this batch" (CONSTRAINT #4).
 */
function useUniverseWideImportance(): { data: SHAPFeature[] | null; loading: boolean; error: string | null } {
  const { data, loading, error } = useApi(async () => {
    const universe = await api.getUniverse();
    const symbols = universe.symbols.map((s) => s.symbol);
    if (symbols.length === 0) return [];
    const importance = await api.getSignalImportance(symbols);
    return importance.rows
      .filter((r) => r.mean_abs_contribution !== null)
      .map((r) => ({ name: r.name, value: r.mean_abs_contribution as number }));
  }, []);
  return { data, loading, error };
}

export default function SignalDriverWeights({ data: overrideData }: { data?: SHAPFeature[] }) {
  const fetched = useUniverseWideImportance();
  // An explicit `data` prop overrides the live fetch entirely (e.g. a caller
  // showing one symbol's own signed per-module breakdown rather than the
  // universe-wide unsigned importance this component fetches by default).
  const usingLiveFetch = overrideData === undefined;
  const plotData = overrideData ?? fetched.data ?? [];
  const loading = usingLiveFetch && fetched.loading;
  const error = usingLiveFetch ? fetched.error : null;

  // Sort by absolute value for standard SHAP-style display.
  const sortedData = [...plotData].sort((a, b) => Math.abs(b.value) - Math.abs(a.value));

  const CustomTooltip = ({ active, payload }: any) => {
    if (active && payload && payload.length) {
      const point = payload[0].payload;
      const isPositive = point.value >= 0;
      return (
        <div
          style={{
            background: "var(--surface-3)",
            padding: "var(--s-3)",
            border: "1px solid var(--border-strong)",
            borderRadius: "var(--r-sm)",
            boxShadow: "var(--shadow-card)",
          }}
        >
          <p style={{ margin: 0, fontWeight: 600, color: "var(--text-primary)" }}>{point.name}</p>
          <p style={{ margin: "var(--s-1) 0 0", fontSize: "var(--t-body)", color: isPositive ? "var(--growth)" : "var(--decline)" }}>
            Mean |Contribution|: {(point.value * 100).toFixed(1)}%
          </p>
        </div>
      );
    }
    return null;
  };

  return (
    <div className="card card-pad" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", marginBottom: "var(--s-4)" }}>
        <Activity style={{ width: 20, height: 20, color: "#a855f7" }} />
        <h3 style={{ margin: 0, fontWeight: 600, color: "var(--text-primary)" }}>Signal Driver Weights</h3>
      </div>
      <p style={{ fontSize: "var(--t-body)", color: "var(--text-secondary)", marginBottom: "var(--s-6)" }}>
        Mean absolute contribution (score × weight) per signal module across the universe.
        Linear weight decomposition — not Shapley marginal contributions.
      </p>

      <div style={{ flex: 1, minHeight: 300, display: "flex", alignItems: "center", justifyContent: "center" }}>
        {loading ? (
          <Loader2 className="icon-spin" style={{ width: 24, height: 24, color: "var(--text-muted)" }} />
        ) : error ? (
          <div style={{ color: "var(--decline)", fontSize: "var(--t-body)" }}>Failed to load signal driver weights</div>
        ) : sortedData.length === 0 ? (
          <div style={{ fontSize: "var(--t-body)", color: "var(--text-secondary)" }}>No tracked symbols yet — run the pipeline, then reload.</div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              layout="vertical"
              data={sortedData}
              margin={{ top: 5, right: 30, left: 100, bottom: 5 }}
            >
              <CartesianGrid strokeDasharray="3 3" horizontal={true} vertical={true} stroke="#334155" opacity={0.2} />
              <XAxis type="number" tickFormatter={(val) => `${(val * 100).toFixed(0)}%`} tick={{ fill: '#64748b', fontSize: 12 }} />
              <YAxis dataKey="name" type="category" width={120} tick={{ fill: '#64748b', fontSize: 12 }} />
              <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(148, 163, 184, 0.1)' }} />
              <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                {sortedData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.value >= 0 ? '#10b981' : '#ef4444'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
