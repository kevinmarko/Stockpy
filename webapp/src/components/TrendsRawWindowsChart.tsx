import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TrendsPoint, TrendsRawWindow } from "../api/types";
import { seriesColor, theme } from "../theme";
import { chartAxisLine, chartAxisTick, chartCursorProps, chartGridProps, chartTooltipStyle } from "./charts";

/**
 * Dates from the Trends API are UTC midnight epoch-ms. Formatting in the
 * viewer's local zone shows every date one day early for US viewers, so pin
 * the zone and locale (deterministic in tests too).
 */
export const formatUtcDate = (ms: unknown): string => {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "";
  return new Date(ms).toLocaleDateString("en-US", {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
};

/** "YYYY-MM-DD" -> UTC-midnight epoch ms (NaN when unparseable). */
export function isoToUtcMs(iso: string | null | undefined): number {
  if (!iso) return NaN;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return NaN;
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

/** Last point whose value is a finite number (null gaps are skipped, never zero). */
export function lastNonNullPoint(points: TrendsPoint[]): [number, number] | null {
  for (let i = points.length - 1; i >= 0; i--) {
    const [t, v] = points[i];
    if (v != null && Number.isFinite(v)) return [t, v];
  }
  return null;
}

/**
 * Merge several point series onto one row-per-timestamp table. A null value
 * leaves the key absent so Recharts shows a gap instead of a zero.
 */
export function mergeSeries(series: { key: string; points: TrendsPoint[] }[]): Record<string, number>[] {
  const rows = new Map<number, Record<string, number>>();
  for (const s of series) {
    for (const [t, v] of s.points ?? []) {
      if (!Number.isFinite(t)) continue;
      let row = rows.get(t);
      if (!row) {
        row = { t };
        rows.set(t, row);
      }
      if (v != null && Number.isFinite(v)) row[s.key] = v;
    }
  }
  return [...rows.values()].sort((a, b) => a.t - b.t);
}

export function ChartEmpty({ children, testId }: { children: string; testId: string }) {
  return (
    <div
      className="empty"
      data-testid={testId}
      style={{ padding: "var(--s-6) var(--s-2)", background: "var(--surface-2)", borderRadius: "var(--r-md)" }}
    >
      {children}
    </div>
  );
}

export function windowLabel(w: TrendsRawWindow): string {
  return `Window ${w.index + 1} (${w.start} → ${w.end})`;
}

export function TrendsRawWindowsChart({ windows }: { windows: TrendsRawWindow[] }) {
  const usable = (windows ?? []).filter((w) => w && (w.points ?? []).some(([, v]) => v != null));
  if (usable.length === 0) {
    return <ChartEmpty testId="trends-raw-empty">No raw windows to show</ChartEmpty>;
  }
  const series = usable.map((w, i) => ({ key: `w${i}`, name: windowLabel(w), points: w.points }));
  const rows = mergeSeries(series);

  return (
    <div data-testid="trends-raw-chart">
      <div style={{ width: "100%", height: 260 }}>
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
            <YAxis domain={[0, 100]} tick={chartAxisTick} {...chartAxisLine} width={34} />
            <Tooltip
              contentStyle={chartTooltipStyle}
              labelFormatter={formatUtcDate}
              cursor={chartCursorProps}
            />
            <Legend wrapperStyle={{ fontSize: "var(--t-caption)", color: theme.textSecondary }} />
            {series.map((s, i) => (
              <Line
                key={s.key}
                dataKey={s.key}
                name={s.name}
                type="monotone"
                stroke={seriesColor(i)}
                strokeWidth={1.75}
                strokeDasharray={i >= 3 ? "4 3" : undefined}
                dot={false}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div style={{ color: theme.textMuted, fontSize: "var(--t-micro)", marginTop: "var(--s-1)" }}>
        Each window is normalized by Google to its own peak of 100, so the same day can have a different value in
        every window.
      </div>
    </div>
  );
}

export default TrendsRawWindowsChart;
