import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { Activity, Loader2 } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { api } from '../api/client';
import { theme } from '../theme';

export interface SHAPFeature {
  name: string;
  value: number;
  // Optional SignalImportanceRow.normalized_contribution (score*weight
  // normalized so every non-null row in a batch sums to ~1.0) -- undefined
  // for a caller-supplied `data` override that doesn't carry it, null when
  // the backend/mock computed it as null (nothing to normalize).
  normalizedContribution?: number | null;
  // Optional SignalImportanceRow.config_weight -- the static
  // settings.SIGNAL_WEIGHTS entry for this module, a DIFFERENT number from
  // `value` (a measured, symbol-averaged score*weight).
  configWeight?: number | null;
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
      .map((r) => ({
        name: r.name,
        value: r.mean_abs_contribution as number,
        normalizedContribution: r.normalized_contribution ?? null,
        configWeight: r.config_weight ?? null,
      }));
  }, []);
  return { data, loading, error };
}

/**
 * The bar chart's hover tooltip -- hoisted to module scope (rather than
 * redefined as a closure on every render) and exported so it's directly
 * unit-testable: Recharts only ever mounts a `<Tooltip>`'s content on a real
 * mouse hover over the chart, which jsdom's zero-layout
 * `<ResponsiveContainer>` never produces, so this is otherwise unreachable
 * from a component-level render test.
 */
export function SignalDriverTooltip({ active, payload }: any) {
  if (active && payload && payload.length) {
    const point = payload[0].payload as SHAPFeature;
    const isPositive = point.value >= 0;
    const normalizedPct =
      point.normalizedContribution != null ? (point.normalizedContribution * 100).toFixed(1) : null;
    return (
      <div
        style={{
          background: theme.surface3,
          padding: "var(--s-3)",
          border: `1px solid ${theme.borderStrong}`,
          borderRadius: "var(--r-sm)",
          boxShadow: "var(--shadow-card)",
        }}
      >
        <p style={{ margin: 0, fontWeight: 600, color: theme.textPrimary }}>{point.name}</p>
        <p style={{ margin: "var(--s-1) 0 0", fontSize: "var(--t-body)", color: isPositive ? theme.growth : theme.decline }}>
          Mean |Contribution|: {(point.value * 100).toFixed(1)}%
        </p>
        {normalizedPct != null && (
          <p style={{ margin: "var(--s-1) 0 0", fontSize: "var(--t-caption)", color: theme.textSecondary }}>
            Normalized Contribution: {normalizedPct}%
          </p>
        )}
        {point.configWeight != null && (
          <p style={{ margin: "var(--s-1) 0 0", fontSize: "var(--t-caption)", color: theme.textSecondary }}>
            Absolute Config Weight: {point.configWeight.toFixed(3)}
          </p>
        )}
      </div>
    );
  }
  return null;
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

  return (
    <div className="card card-pad" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", marginBottom: "var(--s-4)" }}>
        <Activity style={{ width: 20, height: 20, color: theme.accent }} />
        <h3 style={{ margin: 0, fontWeight: 600, color: theme.textPrimary }}>Signal Driver Weights</h3>
      </div>
      <p style={{ fontSize: "var(--t-body)", color: theme.textSecondary, marginBottom: "var(--s-6)" }}>
        Mean absolute contribution (score × weight) per signal module across the universe.
        Linear weight decomposition — not Shapley marginal contributions.
      </p>

      <div
        style={{ flex: 1, minHeight: 300, display: "flex", alignItems: "center", justifyContent: "center" }}
        data-testid="signal-driver-weights-chart"
      >
        {loading ? (
          <Loader2 className="icon-spin" style={{ width: 24, height: 24, color: theme.textMuted }} />
        ) : error ? (
          <div style={{ color: theme.decline, fontSize: "var(--t-body)" }}>Failed to load signal driver weights</div>
        ) : sortedData.length === 0 ? (
          <div style={{ fontSize: "var(--t-body)", color: theme.textSecondary }}>No tracked symbols yet — run the pipeline, then reload.</div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              layout="vertical"
              data={sortedData}
              margin={{ top: 5, right: 30, left: 100, bottom: 5 }}
            >
              <CartesianGrid strokeDasharray="3 3" horizontal={true} vertical={true} stroke={theme.chartGrid} />
              <XAxis type="number" tickFormatter={(val) => `${(val * 100).toFixed(0)}%`} tick={{ fill: theme.textMuted, fontSize: 12 }} />
              <YAxis dataKey="name" type="category" width={120} tick={{ fill: theme.textMuted, fontSize: 12 }} />
              <Tooltip content={<SignalDriverTooltip />} cursor={{ fill: theme.border }} />
              <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                {sortedData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.value >= 0 ? theme.growth : theme.decline} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
