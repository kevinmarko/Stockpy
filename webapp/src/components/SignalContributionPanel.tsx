import { useMemo } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { useApi } from "../hooks/useApi";
import { api } from "../api/client";
import { theme } from "../theme";
import { chartAxisTick, chartGridProps, chartAxisLine } from "./charts";
import { EmptyState, ErrorState, Loading } from "./ui";
import type { SignalBreakdown, SignalModuleScore } from "../api/types";

// Helper to format module names
function formatModuleName(name: string): string {
  return name
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: any[];
}

const CustomTooltip = ({ active, payload }: CustomTooltipProps) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload as SignalModuleScore;
    return (
      <div
        style={{
          background: theme.surface3,
          border: `1px solid ${theme.borderStrong}`,
          borderRadius: 10,
          padding: "var(--s-2) var(--s-3)",
          color: theme.textPrimary,
          fontSize: "var(--t-caption)",
          boxShadow: "var(--shadow-card)",
          minWidth: 160,
        }}
      >
        <div
          style={{
            marginBottom: "var(--s-1-5)",
            fontWeight: 600,
            color: theme.textSecondary,
            borderBottom: `1px solid ${theme.border}`,
            paddingBottom: 4,
          }}
        >
          {formatModuleName(data.name)}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-1-5)" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              gap: "var(--s-4)",
              alignItems: "center",
            }}
          >
            <span style={{ color: theme.textSecondary }}>Score</span>
            <span className="num" style={{ fontWeight: 700, color: theme.textPrimary }}>
              {data.score !== null ? data.score.toFixed(4) : "N/A"}
            </span>
          </div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              gap: "var(--s-4)",
              alignItems: "center",
            }}
          >
            <span style={{ color: theme.textSecondary }}>Weight</span>
            <span className="num" style={{ fontWeight: 700, color: theme.textPrimary }}>
              {data.weight.toFixed(4)}
            </span>
          </div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              gap: "var(--s-4)",
              alignItems: "center",
            }}
          >
            <span style={{ color: theme.textSecondary }}>Contribution</span>
            <span
              className="num"
              style={{
                fontWeight: 700,
                color:
                  data.contribution !== null
                    ? data.contribution >= 0
                      ? theme.growth
                      : theme.decline
                    : theme.textPrimary,
              }}
            >
              {data.contribution !== null ? data.contribution.toFixed(4) : "N/A"}
            </span>
          </div>
        </div>
      </div>
    );
  }
  return null;
};

export function SignalContributionPanel({
  symbol,
  compact,
}: {
  symbol: string;
  compact?: boolean;
}) {
  const { data, loading, error, status, reload } = useApi<SignalBreakdown>(
    () => api.getSignalBreakdown(symbol),
    [symbol]
  );

  const chartData = useMemo(() => {
    if (!data || !data.modules) return [];
    // Filter out null contributions and sort by absolute contribution descending
    return data.modules
      .filter((m) => m.contribution !== null)
      .sort((a, b) => Math.abs(b.contribution!) - Math.abs(a.contribution!));
  }, [data]);

  if (loading) return <Loading />;
  if (error)
    return (
      <ErrorState
        message={error}
        status={status}
        onRetry={reload}
      />
    );

  if (!data || chartData.length === 0) {
    return (
      <EmptyState
        title="No Signal Data"
        hint="No signal contribution data available."
      />
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100%" }}>
      {!compact && (
        <div style={{ marginBottom: "var(--s-4)" }}>
          <h3
            style={{
              margin: "0 0 var(--s-1) 0",
              fontSize: "var(--t-subhead)",
              fontWeight: 600,
            }}
          >
            Signal Contribution Breakdown
          </h3>
          <div style={{ fontSize: "var(--t-body)", color: "var(--text-muted)" }}>
            Weighted linear decomposition (score × weight), not Shapley values
          </div>
        </div>
      )}
      <div style={{ width: "100%", height: compact ? 200 : 300 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={chartData}
            layout="vertical"
            margin={{ top: 5, right: 30, left: 10, bottom: 5 }}
          >
            <CartesianGrid {...chartGridProps} />
            <XAxis type="number" tick={chartAxisTick} {...chartAxisLine} />
            <YAxis
              dataKey="name"
              type="category"
              tickFormatter={formatModuleName}
              tick={chartAxisTick}
              width={120}
              {...chartAxisLine}
            />
            <Tooltip content={<CustomTooltip />} cursor={{ fill: theme.surface2 }} />
            <Bar dataKey="contribution" isAnimationActive={false}>
              {chartData.map((entry, index) => (
                <Cell
                  key={`cell-${index}`}
                  fill={entry.contribution! >= 0 ? theme.growth : theme.decline}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
