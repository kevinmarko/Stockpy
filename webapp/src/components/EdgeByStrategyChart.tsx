import {
  ResponsiveContainer,
  BarChart,
  Bar as RBar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import { api } from "../api/client";
import type { EdgeByStrategy } from "../api/types";
import { useApi } from "../hooks/useApi";
import { EmptyState, ErrorState, Loading } from "./ui";
import { chartAxisLine, chartAxisTick, chartGridProps, chartTooltipStyle } from "./charts";
import { fmtNum } from "../format";
import { seriesColor, theme } from "../theme";

/**
 * Edge-by-strategy widget -- TWO stacked single-axis bar charts (never one
 * dual-y-axis chart; this codebase's documented anti-dual-axis principle:
 * different scales overlaid on one plot is "the most common charting
 * mistake"). Self-contained (fetches its own data, no required props) so it
 * can be dropped into any screen -- currently `StrategyInsights.tsx` and the
 * Create Data App `/app/:slug` renderer, kept as the ONE implementation
 * rather than two independently-drifting copies.
 */
export function EdgeByStrategyChart() {
  const { data, loading, error, status, reload } = useApi<EdgeByStrategy>(
    () => api.getEdgeByStrategy(),
    []
  );

  if (loading) return <Loading lines={3} />;
  if (error) return <ErrorState message={error} status={status} onRetry={reload} />;
  if (!data || data.rows.length === 0) {
    return (
      <EmptyState
        title="No closed trades to score yet"
        hint={data?.reason ?? "Edge ratio by strategy populates once trades close."}
      />
    );
  }

  return (
    <div data-testid="edge-by-strategy-chart">
      <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginBottom: "var(--s-1-5)" }}>
        Mean edge ratio by strategy
      </div>
      <div style={{ height: 200 }}>
        <ResponsiveContainer>
          <BarChart data={data.rows} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
            <CartesianGrid {...chartGridProps} />
            <XAxis dataKey="strategy" tick={{ ...chartAxisTick, fontSize: "var(--t-micro)" }} {...chartAxisLine} />
            <YAxis tick={chartAxisTick} {...chartAxisLine} />
            <Tooltip
              contentStyle={chartTooltipStyle}
              formatter={(v) => [typeof v === "number" ? fmtNum(v, 2) : "—", "Mean edge ratio"]}
            />
            <RBar dataKey="mean_edge_ratio" name="Mean edge ratio" fill={seriesColor(0)} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", margin: "var(--s-3) 0 var(--s-1-5)" }}>
        Trade count by strategy
      </div>
      <div style={{ height: 200 }}>
        <ResponsiveContainer>
          <BarChart data={data.rows} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
            <CartesianGrid {...chartGridProps} />
            <XAxis dataKey="strategy" tick={{ ...chartAxisTick, fontSize: "var(--t-micro)" }} {...chartAxisLine} />
            <YAxis tick={chartAxisTick} {...chartAxisLine} allowDecimals={false} />
            <Tooltip
              contentStyle={chartTooltipStyle}
              formatter={(v) => [typeof v === "number" ? v.toFixed(0) : "—", "Trades"]}
            />
            <RBar dataKey="n_trades" name="Trades" fill={seriesColor(1)} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
