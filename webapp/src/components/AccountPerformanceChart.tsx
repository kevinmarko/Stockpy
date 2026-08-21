import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer
} from 'recharts';
import { CurvePoint } from '../api/types';
import { theme } from '../theme';
import { fmtUsd, fmtDate } from '../format';
import { CustomTooltip, chartAxisTick, chartAxisLine, chartGridProps, chartCursorProps } from './charts';

interface Props {
  data: CurvePoint[];
}

export default function AccountPerformanceChart({ data }: Props) {
  if (!data || data.length === 0) {
    return (
      <div
        className="empty"
        data-testid="equity-empty"
        style={{ padding: "var(--s-8) var(--s-2)", background: "var(--surface-2)", borderRadius: "var(--r-md)", height: '100%' }}
      >
        <div style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>
          No account performance data yet
        </div>
        <div style={{ marginTop: "var(--s-1-5)", fontSize: "var(--t-body)" }}>
          No curve data available. Run the Stockpy pipeline to accumulate an
          account equity history.
        </div>
      </div>
    );
  }

  return (
    // Fixed pixel height (matching PerfLine's own convention in charts.tsx),
    // not height:'100%' -- Recharts' ResponsiveContainer measures its own
    // container via ResizeObserver and needs a DEFINITE (non-percentage)
    // height somewhere in its immediate ancestry to resolve against. A
    // percentage height here silently measures 0x0 and never renders
    // (verified live: this is what PR #846 originally shipped) once the
    // parent chain includes a flex container, since a flex item's
    // percentage height doesn't resolve without an explicit flex-basis.
    <div style={{ width: '100%', height: 200 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
          <CartesianGrid {...chartGridProps} />
          <XAxis
            dataKey="date"
            {...chartAxisLine}
            tick={chartAxisTick}
            // fmtDate renders the calendar date embedded in the ISO string
            // (via timeZone: "UTC") rather than the browser's local
            // calendar date -- a bare `new Date("2026-08-20")` parses as
            // UTC midnight, so re-rendering it in a US-negative timezone
            // without this would silently display the day before.
            tickFormatter={fmtDate}
            minTickGap={30}
          />
          <YAxis
            domain={['auto', 'auto']}
            {...chartAxisLine}
            tick={chartAxisTick}
            width={56}
            tickFormatter={(val: number) => fmtUsd(val, { compact: true })}
          />
          <Tooltip
            content={<CustomTooltip valueFormat="currency" valueLabel="Equity" />}
            cursor={chartCursorProps}
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke={theme.growth}
            strokeWidth={2.5}
            dot={false}
            activeDot={{ r: 5, fill: theme.growth, stroke: theme.surface, strokeWidth: 2 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
