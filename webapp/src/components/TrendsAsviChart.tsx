import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TrendsAsvi } from "../api/types";
import { theme } from "../theme";
import { chartAxisLine, chartAxisTick, chartCursorProps, chartGridProps, chartTooltipStyle } from "./charts";
import { ChartEmpty, formatUtcDate, lastNonNullPoint } from "./TrendsRawWindowsChart";

export function TrendsAsviChart({ asvi }: { asvi: TrendsAsvi | null }) {
  const points = asvi?.points ?? [];
  if (!asvi || !points.some(([, v]) => v != null)) {
    return <ChartEmpty testId="trends-asvi-empty">No ASVI series to show</ChartEmpty>;
  }
  // Null values are left as undefined so the warm-up gap stays a gap.
  const rows = points.map(([t, v]) => ({ t, v: v != null && Number.isFinite(v) ? v : undefined }));
  const last = lastNonNullPoint(points);
  const latestText = asvi.latest != null && Number.isFinite(asvi.latest) ? asvi.latest.toFixed(2) : "—";

  return (
    <div data-testid="trends-asvi-chart">
      <div style={{ width: "100%", height: 220 }}>
        <ResponsiveContainer>
          <LineChart data={rows} margin={{ top: 8, right: 6, left: 6, bottom: 0 }}>
            <CartesianGrid {...chartGridProps} />
            <XAxis
              dataKey="t"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              tickFormatter={formatUtcDate}
              tick={chartAxisTick}
              {...chartAxisLine}
              minTickGap={44}
            />
            <YAxis
              // Always include 0 so the zero reference line (ASVI = 0 means
              // "search volume at its usual level") stays on screen even when
              // every value is above or below it; Recharts hides an
              // out-of-domain ReferenceLine.
              domain={[(min: number) => Math.min(min, 0), (max: number) => Math.max(max, 0)]}
              tick={chartAxisTick}
              {...chartAxisLine}
              width={38}
              tickFormatter={(v: number) => v.toFixed(1)}
            />
            <Tooltip
              contentStyle={chartTooltipStyle}
              labelFormatter={formatUtcDate}
              formatter={(val) => [Number(val).toFixed(2), "ASVI"]}
              cursor={chartCursorProps}
            />
            <ReferenceLine y={0} stroke={theme.textMuted} strokeDasharray="3 3" />
            <Line
              dataKey="v"
              name="ASVI"
              type="monotone"
              stroke={theme.accent}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, fill: theme.surface, stroke: theme.accent, strokeWidth: 2 }}
              connectNulls={false}
              isAnimationActive={false}
            />
            {last && (
              <ReferenceDot
                x={last[0]}
                y={last[1]}
                r={4}
                fill={theme.accent}
                stroke={theme.surface}
                strokeWidth={2}
                ifOverflow="extendDomain"
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div
        data-testid="trends-asvi-caption"
        style={{ color: theme.textSecondary, fontSize: "var(--t-caption)", marginTop: "var(--s-1)" }}
      >
        Latest ASVI{asvi.latest_date ? ` (${asvi.latest_date})` : ""}:{" "}
        <span className="num" style={{ color: theme.textPrimary, fontWeight: 600 }}>
          {latestText}
        </span>
      </div>
    </div>
  );
}

export default TrendsAsviChart;
